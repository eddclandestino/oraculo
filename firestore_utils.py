"""
ORÁCULO Firestore Session Logger — Production Implementation
=============================================================
Logs session metadata to Cloud Firestore for the hackathon requirement.

Collection structure:
  oraculo_sessions/{session_id}
    ├── session_id: str
    ├── started_at: str (ISO 8601)
    ├── ended_at: str (ISO 8601) — set on session end
    ├── duration_seconds: float — calculated on session end
    ├── status: "active" | "completed" | "error"
    ├── tools_called: list[dict]  — [{name, timestamp, duration_ms}]
    ├── tool_summary: dict — {get_stock_quote: 3, get_market_news: 1, ...}
    ├── error_count: int
    ├── last_error: str | null
    ├── client_info: dict — {user_agent, ip} if available
    └── environment: str — "production" | "development"

Design decisions:
- Lazy-initialize Firestore client (works locally without credentials)
- All operations are fire-and-forget (log warnings, never crash the app)
- Uses async Firestore client for non-blocking writes
- Batches tool_call updates using array_union for atomic appends
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Lazy-initialized Firestore client ──
_firestore_client = None
_init_attempted = False


def _get_client():
    """
    Lazy-initialize the async Firestore client.
    Returns None if Firestore is unavailable (local dev without credentials).
    """
    global _firestore_client, _init_attempted

    if _init_attempted:
        return _firestore_client

    _init_attempted = True

    try:
        from google.cloud import firestore as firestore_module
        from config import GOOGLE_CLOUD_PROJECT
        _firestore_client = firestore_module.AsyncClient(project=GOOGLE_CLOUD_PROJECT)
        logger.info(f"Firestore client initialized (project={GOOGLE_CLOUD_PROJECT})")
    except ImportError:
        logger.warning(
            "google-cloud-firestore not installed. Session logging disabled. "
            "Install with: pip install google-cloud-firestore"
        )
    except Exception as e:
        logger.warning(
            f"Firestore unavailable: {e}. "
            "This is normal in local development without GCP credentials. "
            "Session logging will be disabled."
        )

    return _firestore_client


def _collection_ref():
    """Get the sessions collection reference, or None."""
    client = _get_client()
    if client is None:
        return None
    from config import FIRESTORE_COLLECTION
    return client.collection(FIRESTORE_COLLECTION)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════
# SESSION LIFECYCLE
# ═══════════════════════════════════════════════════════════════

class SessionLogger:
    """
    Tracks and logs session data to Firestore.

    Usage:
        logger = SessionLogger(session_id)
        await logger.start(client_info={"user_agent": "...", "ip": "..."})
        await logger.log_tool_call("get_stock_quote", duration_ms=234)
        await logger.log_error("Connection timeout")
        await logger.end()
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._start_time = time.time()
        self._tools_called: list[dict] = []
        self._tool_counts: dict[str, int] = {}
        self._error_count = 0
        self._last_error: Optional[str] = None

    async def start(self, client_info: Optional[dict] = None):
        """Log session start to Firestore."""
        col = _collection_ref()
        if col is None:
            return

        try:
            from config import ENVIRONMENT
            doc_ref = col.document(self.session_id)
            await doc_ref.set({
                "session_id": self.session_id,
                "started_at": _now_iso(),
                "status": "active",
                "tools_called": [],
                "tool_summary": {},
                "error_count": 0,
                "last_error": None,
                "client_info": client_info or {},
                "environment": ENVIRONMENT,
            })
            logger.debug(f"Firestore: session {self.session_id} started")
        except Exception as e:
            logger.warning(f"Firestore start failed: {e}")

    async def log_tool_call(self, tool_name: str, duration_ms: float = 0):
        """Log a tool call. Updates both the local state and Firestore."""
        tool_entry = {
            "name": tool_name,
            "timestamp": _now_iso(),
            "duration_ms": round(duration_ms),
        }
        self._tools_called.append(tool_entry)
        self._tool_counts[tool_name] = self._tool_counts.get(tool_name, 0) + 1

        col = _collection_ref()
        if col is None:
            return

        try:
            from google.cloud.firestore_v1 import ArrayUnion
            doc_ref = col.document(self.session_id)
            await doc_ref.update({
                "tools_called": ArrayUnion([tool_entry]),
                "tool_summary": self._tool_counts,
            })
        except Exception as e:
            logger.debug(f"Firestore tool log failed: {e}")

    async def log_error(self, error_msg: str):
        """Log an error that occurred during the session."""
        self._error_count += 1
        self._last_error = error_msg

        col = _collection_ref()
        if col is None:
            return

        try:
            doc_ref = col.document(self.session_id)
            await doc_ref.update({
                "error_count": self._error_count,
                "last_error": error_msg,
            })
        except Exception as e:
            logger.debug(f"Firestore error log failed: {e}")

    async def end(self):
        """Log session end with final stats."""
        duration = time.time() - self._start_time

        col = _collection_ref()
        if col is None:
            return

        try:
            doc_ref = col.document(self.session_id)
            await doc_ref.update({
                "ended_at": _now_iso(),
                "duration_seconds": round(duration, 1),
                "status": "completed",
                "tool_summary": self._tool_counts,
                "error_count": self._error_count,
                "last_error": self._last_error,
            })
            logger.info(
                f"Firestore: session {self.session_id} ended "
                f"(duration={duration:.1f}s, tools={sum(self._tool_counts.values())}, "
                f"errors={self._error_count})"
            )
        except Exception as e:
            logger.warning(f"Firestore end failed: {e}")

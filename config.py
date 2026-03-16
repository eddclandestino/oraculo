"""
ORÁCULO Configuration — Validated & Typed
==========================================
All configuration is loaded from environment variables with validation.
Fails fast on startup if critical config is missing, rather than
crashing mid-session when a tool tries to use a missing API key.
"""
import os
import sys
import logging
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    """Immutable, validated configuration. Created once at startup."""

    # ── Google Cloud ──
    GOOGLE_CLOUD_PROJECT: str = ""
    GOOGLE_CLOUD_LOCATION: str = "us-central1"
    GOOGLE_API_KEY: str = ""

    # ── Gemini Live API ──
    GEMINI_MODEL: str = "gemini-2.5-flash-native-audio-preview-12-2025"
    GEMINI_VOICE: str = "Orus"
    GEMINI_ENABLE_COMPRESSION: bool = True   # Context window compression
    GEMINI_ENABLE_RESUMPTION: bool = True    # Session resumption

    # ── Market Data ──
    ALPHA_VANTAGE_API_KEY: str = ""

    # ── App ──
    PORT: int = 8080
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    MAX_CONCURRENT_SESSIONS: int = 10
    WS_RATE_LIMIT_PER_SECOND: int = 60   # Max WebSocket messages per second per client

    # ── Firestore ──
    FIRESTORE_COLLECTION: str = "oraculo_sessions"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    def validate(self) -> list[str]:
        """Return list of validation warnings/errors."""
        issues = []
        if not self.GOOGLE_API_KEY:
            issues.append("CRITICAL: GOOGLE_API_KEY not set — Gemini Live API will not work")
        if not self.ALPHA_VANTAGE_API_KEY:
            issues.append("WARNING: ALPHA_VANTAGE_API_KEY not set — tools will use yfinance fallback only")
        if self.ENVIRONMENT == "production" and not self.GOOGLE_CLOUD_PROJECT:
            issues.append("WARNING: GOOGLE_CLOUD_PROJECT not set — Firestore logging will be disabled")
        return issues


def load_config() -> Config:
    """Load config from environment, validate, and return."""
    config = Config(
        GOOGLE_CLOUD_PROJECT=os.getenv("GOOGLE_CLOUD_PROJECT", ""),
        GOOGLE_CLOUD_LOCATION=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        GOOGLE_API_KEY=os.getenv("GOOGLE_API_KEY", ""),
        GEMINI_MODEL=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025"),
        GEMINI_VOICE=os.getenv("GEMINI_VOICE", "Orus"),
        GEMINI_ENABLE_COMPRESSION=os.getenv("GEMINI_ENABLE_COMPRESSION", "true").lower() == "true",
        GEMINI_ENABLE_RESUMPTION=os.getenv("GEMINI_ENABLE_RESUMPTION", "true").lower() == "true",
        ALPHA_VANTAGE_API_KEY=os.getenv("ALPHA_VANTAGE_API_KEY", ""),
        PORT=int(os.getenv("PORT", "8080")),
        ENVIRONMENT=os.getenv("ENVIRONMENT", "development"),
        LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
        MAX_CONCURRENT_SESSIONS=int(os.getenv("MAX_CONCURRENT_SESSIONS", "10")),
        WS_RATE_LIMIT_PER_SECOND=int(os.getenv("WS_RATE_LIMIT_PER_SECOND", "60")),
        FIRESTORE_COLLECTION=os.getenv("FIRESTORE_COLLECTION", "oraculo_sessions"),
    )

    issues = config.validate()
    for issue in issues:
        if issue.startswith("CRITICAL"):
            logger.error(issue)
        else:
            logger.warning(issue)

    if any(i.startswith("CRITICAL") for i in issues) and config.is_production:
        logger.error("Cannot start in production with critical config errors")
        sys.exit(1)

    return config


# ── Singleton config instance ──
# Import this from other modules: `from config import cfg`
cfg = load_config()

# ── Backward compatibility ──
# Old code imports individual names from config. These aliases keep it working.
GOOGLE_CLOUD_PROJECT = cfg.GOOGLE_CLOUD_PROJECT
GOOGLE_CLOUD_LOCATION = cfg.GOOGLE_CLOUD_LOCATION
GOOGLE_API_KEY = cfg.GOOGLE_API_KEY
GEMINI_MODEL = cfg.GEMINI_MODEL
GEMINI_VOICE = cfg.GEMINI_VOICE
ALPHA_VANTAGE_API_KEY = cfg.ALPHA_VANTAGE_API_KEY
PORT = cfg.PORT
ENVIRONMENT = cfg.ENVIRONMENT
LOG_LEVEL = cfg.LOG_LEVEL
FIRESTORE_COLLECTION = cfg.FIRESTORE_COLLECTION

"""
ORÁCULO — FastAPI Server v2.0 (Hardened)
WebSocket endpoint bridges browser client <-> Gemini Live API session.
"""
import asyncio
import base64
import json
import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from config import cfg, PORT, ENVIRONMENT, LOG_LEVEL
from gemini_live import GeminiLiveSession
from firestore_utils import SessionLogger
from middleware import SecurityHeadersMiddleware, WebSocketRateLimiter

# Structured JSON logging in production for Cloud Run log parsing
if cfg.is_production:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format='{"time":"%(asctime)s","logger":"%(name)s","level":"%(levelname)s","msg":"%(message)s"}'
    )
else:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )
logger = logging.getLogger(__name__)

app = FastAPI(
    title="ORÁCULO",
    version="1.0.0",
    description="Real-Time Voice + Vision Market Intelligence Agent"
)

# Security headers on all responses
app.add_middleware(SecurityHeadersMiddleware)

# CORS: restrictive in production, permissive in development
if cfg.is_production:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://oraculo-*.run.app",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Serve frontend
FRONTEND_DIR = Path(__file__).parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# ── Rate limiter and session tracking ──
_rate_limiter = WebSocketRateLimiter(max_per_second=cfg.WS_RATE_LIMIT_PER_SECOND)
_active_sessions: set[str] = set()


@app.on_event("startup")
async def startup():
    """Validate critical configuration on startup."""
    issues = cfg.validate()
    for issue in issues:
        if issue.startswith("CRITICAL"):
            logger.error(f"Config: {issue}")
        else:
            logger.warning(f"Config: {issue}")
    logger.info(
        f"ORÁCULO starting (env={cfg.ENVIRONMENT}, model={cfg.GEMINI_MODEL}, "
        f"compression={'ON' if cfg.GEMINI_ENABLE_COMPRESSION else 'OFF'}, "
        f"resumption={'ON' if cfg.GEMINI_ENABLE_RESUMPTION else 'OFF'})"
    )


@app.on_event("shutdown")
async def shutdown():
    """Clean up shared resources."""
    from tools import _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        logger.info("HTTP client closed")


@app.get("/")
async def root():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "oraculo",
        "version": "1.0.0",
        "environment": cfg.ENVIRONMENT,
        "gemini_model": cfg.GEMINI_MODEL,
        "active_sessions": len(_active_sessions),
        "max_sessions": cfg.MAX_CONCURRENT_SESSIONS,
        "features": {
            "context_compression": cfg.GEMINI_ENABLE_COMPRESSION,
            "session_resumption": cfg.GEMINI_ENABLE_RESUMPTION,
            "alpha_vantage": bool(cfg.ALPHA_VANTAGE_API_KEY),
            "firestore": bool(cfg.GOOGLE_CLOUD_PROJECT),
        },
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    Main WebSocket endpoint.

    Protocol (JSON messages):
    Client -> Server:
      {"type": "audio", "data": "<base64 PCM 16kHz>"}
      {"type": "video", "data": "<base64 JPEG>"}
      {"type": "text", "text": "user message"}

    Server -> Client:
      {"type": "audio", "data": "<base64 PCM 24kHz>"}
      {"type": "text", "text": "transcription or info"}
      {"type": "transcript", "text": "...", "role": "user"|"model"}
      {"type": "interrupted"}
      {"type": "tool_call", "name": "tool_name"}
      {"type": "turn_complete"}
      {"type": "reconnecting", "message": "..."}
      {"type": "session_resumed"}
      {"type": "error", "message": "error description"}
    """
    # ── Session limit check ──
    if len(_active_sessions) >= cfg.MAX_CONCURRENT_SESSIONS:
        await ws.close(code=1013, reason="Server at capacity. Try again later.")
        return

    await ws.accept()
    session_id = str(uuid.uuid4())
    _active_sessions.add(session_id)
    logger.info(f"WebSocket connected: {session_id} (active={len(_active_sessions)})")

    session_logger = SessionLogger(session_id)
    client_info = {}
    try:
        client_info = {
            "user_agent": ws.headers.get("user-agent", "unknown"),
            "origin": ws.headers.get("origin", "unknown"),
        }
    except Exception:
        pass

    await session_logger.start(client_info=client_info)

    gemini_session = None

    try:
        async def on_audio(audio_bytes: bytes):
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
            await ws.send_json({"type": "audio", "data": audio_b64})

        async def on_text(text: str):
            await ws.send_json({"type": "text", "text": text})

        async def on_transcript(text: str, role: str):
            # Sanitize before sending to activity log (XSS prevention)
            safe_text = (
                text.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
            )
            await ws.send_json({"type": "transcript", "text": safe_text, "role": role})

        async def on_interrupted():
            await ws.send_json({"type": "interrupted"})

        async def on_tool_call(name: str):
            await ws.send_json({"type": "tool_call", "name": name})
            await session_logger.log_tool_call(name)

        async def on_turn_complete():
            await ws.send_json({"type": "turn_complete"})

        async def on_go_away(time_left: float):
            logger.warning(
                f"GoAway for session {session_id}: {time_left:.0f}s until disconnect"
            )
            try:
                await ws.send_json(
                    {"type": "reconnecting", "message": "Refreshing AI connection..."}
                )
            except Exception:
                pass

        async def on_session_resumed():
            logger.info(f"Session resumed: {session_id}")
            try:
                await ws.send_json({"type": "session_resumed"})
            except Exception:
                pass

        async def on_error(message: str):
            logger.error(f"Gemini error for {session_id}: {message}")
            try:
                await ws.send_json({"type": "error", "message": message})
            except Exception:
                pass

        gemini_session = GeminiLiveSession(callbacks={
            "on_audio": on_audio,
            "on_text": on_text,
            "on_transcript": on_transcript,
            "on_interrupted": on_interrupted,
            "on_tool_call": on_tool_call,
            "on_turn_complete": on_turn_complete,
            "on_go_away": on_go_away,
            "on_session_resumed": on_session_resumed,
            "on_error": on_error,
        })
        await gemini_session.connect()

        # NOTE: Initial greeting is intentionally disabled.
        # Sending text immediately after connect causes the session stream to
        # close ~4 seconds later — the Live API appears to require active audio
        # input to keep the session alive. The greeting now fires from the
        # frontend after the user enables their microphone (via a text message
        # sent once mic is active). Re-enable this line only if you confirm
        # the session stays alive without a mic stream.
        #
        # await gemini_session.send_text(
        #     "Greet the trader briefly. Let them know you're ready to analyze "
        #     "their screens and answer questions. Keep it to one sentence."
        # )

        # Main message loop: browser -> Gemini
        _audio_log_count = 0  # throttle audio log to every 50th message
        while True:
            # ── Rate limiting ──
            if not _rate_limiter.allow(session_id):
                logger.warning(f"Rate limited: {session_id}")
                await asyncio.sleep(0.1)
                continue

            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.warning(f"WebSocket receive error ({session_id}): {e}")
                continue

            # ── JSON validation ──
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from {session_id}: {raw[:100]}")
                continue

            msg_type = msg.get("type")
            if msg_type not in ("audio", "video", "text"):
                logger.debug(f"Unknown message type from {session_id}: {msg_type}")
                continue

            # ── Size validation ──
            data = msg.get("data", "")
            if msg_type == "audio" and len(data) > 200_000:  # ~150KB base64
                logger.warning(f"Audio chunk too large from {session_id}: {len(data)}")
                continue
            if msg_type == "video" and len(data) > 500_000:  # ~375KB base64
                logger.warning(f"Video frame too large from {session_id}: {len(data)}")
                continue
            if msg_type == "text":
                text_content = msg.get("text", "")
                if len(text_content) > 2000:
                    logger.warning(
                        f"Text too long from {session_id}: {len(text_content)}"
                    )
                    continue

            try:
                if msg_type == "audio":
                    audio_bytes = base64.b64decode(data)
                    _audio_log_count += 1
                    if _audio_log_count % 50 == 1:
                        logger.info(
                            f"Forwarding audio to Gemini: {len(audio_bytes)} bytes "
                            f"(msg #{_audio_log_count})"
                        )
                    await gemini_session.send_audio(audio_bytes)
                elif msg_type == "video":
                    jpeg_bytes = base64.b64decode(data)
                    await gemini_session.send_video_frame(jpeg_bytes)
                elif msg_type == "text":
                    text_payload = msg.get("text", "")
                    logger.info(f"Forwarding text to Gemini: {text_payload!r}")
                    await gemini_session.send_text(text_payload)
            except Exception as e:
                logger.error(f"Error processing {msg_type} from {session_id}: {e}")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {session_id}")
    except Exception as e:
        logger.error(f"WebSocket error ({session_id}): {e}")
        await session_logger.log_error(str(e))
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        _active_sessions.discard(session_id)
        _rate_limiter.cleanup(session_id)
        if gemini_session:
            await gemini_session.close()
        await session_logger.end()
        logger.info(f"Session cleaned up: {session_id}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=(ENVIRONMENT == "development"))

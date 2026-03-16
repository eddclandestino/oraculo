"""
ORÁCULO Middleware — Security Headers, Rate Limiting, CORS
==========================================================
"""
import time
import logging
from collections import defaultdict
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from config import cfg

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        # Prevent MIME sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # XSS protection
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Permissions policy — disable unnecessary browser features
        response.headers["Permissions-Policy"] = "geolocation=(), payment=()"
        return response


class WebSocketRateLimiter:
    """
    Token bucket rate limiter for WebSocket messages.
    Prevents a single client from flooding the server.
    """
    def __init__(self, max_per_second: int = 60):
        self._max_per_second = max_per_second
        self._buckets: dict[str, dict] = defaultdict(
            lambda: {"tokens": max_per_second, "last_refill": time.monotonic()}
        )

    def allow(self, client_id: str) -> bool:
        bucket = self._buckets[client_id]
        now = time.monotonic()
        # Refill tokens based on elapsed time
        elapsed = now - bucket["last_refill"]
        bucket["tokens"] = min(
            self._max_per_second,
            bucket["tokens"] + elapsed * self._max_per_second
        )
        bucket["last_refill"] = now

        if bucket["tokens"] >= 1:
            bucket["tokens"] -= 1
            return True
        return False

    def cleanup(self, client_id: str):
        self._buckets.pop(client_id, None)

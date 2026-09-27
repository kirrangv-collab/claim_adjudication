"""Simple in-process rate limiting middleware.

Uses a fixed-window counter keyed by client IP. This is intentionally
lightweight and dependency-free, but it is per-process state: running
multiple API replicas behind a load balancer means each replica enforces
its own independent limit. A real multi-instance deployment should replace
this with a shared store (e.g. Redis) — noted here rather than silently
pretended away.
"""
import os
import time
from collections import defaultdict

from backend.config import get_settings

_DEFAULT_ORIGINS = (
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:8080,http://127.0.0.1:8080"
)


class RateLimitMiddleware:
    def __init__(self, app):
        self.app = app
        self._hits: dict[str, list[float]] = defaultdict(list)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        client = scope.get("client")
        key = client[0] if client else "unknown"
        now = time.monotonic()
        window = settings.rate_limit_window_seconds
        limit = settings.rate_limit_requests

        hits = self._hits[key]
        cutoff = now - window
        while hits and hits[0] < cutoff:
            hits.pop(0)

        if len(hits) >= limit:
            headers = self._error_headers(scope)
            body = b'{"detail":"Rate limit exceeded. Please slow down and try again shortly."}'
            headers.extend([
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"retry-after", str(window).encode("ascii")),
            ])
            await send({"type": "http.response.start", "status": 429, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return

        hits.append(now)
        await self.app(scope, receive, send)

    @staticmethod
    def _error_headers(scope) -> list[tuple[bytes, bytes]]:
        allowed = {
            origin.strip()
            for origin in os.getenv("CLAIMLAB_ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",")
            if origin.strip()
        }
        request_headers = dict(scope.get("headers", []))
        origin = request_headers.get(b"origin", b"").decode("ascii", errors="ignore")
        if origin in allowed:
            return [(b"access-control-allow-origin", origin.encode("ascii"))]
        return []


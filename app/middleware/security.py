"""Transport-independent request hardening and distributed abuse controls."""

import asyncio
import time
import hashlib
import logging

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.config import settings

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > settings.MAX_REQUEST_BYTES:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={"detail": "Request body exceeds the configured limit"},
                    )
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cache-Control", "no-store")
        if settings.ENVIRONMENT.lower() == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis-backed fixed-window limits for authentication and expensive writes."""

    _redis: Redis | None = None
    _retry_after: float = 0
    _redis_timeout = 0.75
    _retry_delay = 30.0
    _script = """
    local current = redis.call('INCR', KEYS[1])
    if current == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
    return current
    """

    @staticmethod
    def _limit(request: Request) -> tuple[int, int] | None:
        path = request.url.path
        if request.method == "POST" and path.endswith("/auth/clinical/hospital-code"):
            return 10, 60
        if request.method == "POST" and path.endswith("/auth/signup"):
            return 5, 3600
        if request.method == "POST" and "/auth/" in path:
            return 30, 60
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and (
            "/voice-jobs" in path or "/records/upload" in path or "/voice-intake" in path
        ):
            return 60, 60
        return None

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rule = self._limit(request) if settings.RATE_LIMIT_ENABLED else None
        if not rule:
            return await call_next(request)

        if time.monotonic() < self._retry_after:
            return await call_next(request)

        limit, window = rule
        client_ip = request.client.host if request.client else "unknown"
        identity = hashlib.sha256(client_ip.encode()).hexdigest()[:24]
        route = hashlib.sha256(
            f"{request.method}:{request.url.path}".encode()
        ).hexdigest()[:20]
        key = f"rate:{route}:{identity}"
        try:
            if self._redis is None:
                self._redis = Redis.from_url(
                    settings.REDIS_URL, decode_responses=True,
                    socket_connect_timeout=self._redis_timeout,
                    socket_timeout=self._redis_timeout,
                )
            count = int(await asyncio.wait_for(
                self._redis.eval(self._script, 1, key, window),
                timeout=self._redis_timeout,
            ))
        except Exception:  # fail open so a Redis outage does not take down clinical access
            self._retry_after = time.monotonic() + self._retry_delay
            logger.warning("Rate limiter unavailable; retrying in 30 seconds")
            return await call_next(request)

        if count > limit:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Too many requests; try again later"},
                headers={"Retry-After": str(window)},
            )
        return await call_next(request)

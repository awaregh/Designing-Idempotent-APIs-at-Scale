"""Idempotency middleware that caches responses by X-Idempotency-Key header."""
from __future__ import annotations

import json
from typing import Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from services.shared.redis_client import get_redis

logger = structlog.get_logger(__name__)


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """
    HTTP middleware that transparently handles idempotency keys.

    Workflow:
    1. Read X-Idempotency-Key header.
    2. If absent: pass through unchanged.
    3. Build cache key "idem:{key}:{path}".
    4. On cache hit: return stored response with X-Idempotency-Replay: true.
    5. On cache miss: call next handler, cache 2xx responses for 24 h.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        idem_key = request.headers.get("X-Idempotency-Key")

        if not idem_key:
            return await call_next(request)

        redis = get_redis()
        cache_key = f"idem:{idem_key}:{request.url.path}"

        # --- Cache hit ---
        cached = await redis.get(cache_key)
        if cached:
            data = json.loads(cached)
            logger.info(
                "idempotency_cache_hit",
                key=idem_key,
                path=request.url.path,
            )
            response = JSONResponse(
                content=data["body"],
                status_code=data["status_code"],
            )
            response.headers["X-Idempotency-Replay"] = "true"
            return response

        # --- Cache miss: execute request ---
        response: Response = await call_next(request)

        if 200 <= response.status_code < 300:
            # Consume the body bytes so we can re-wrap them
            body_bytes = b""
            async for chunk in response.body_iterator:  # type: ignore[attr-defined]
                body_bytes += chunk

            try:
                body_json = json.loads(body_bytes)
                payload = json.dumps(
                    {"body": body_json, "status_code": response.status_code}
                )
                await redis.setex(cache_key, 86400, payload)
                logger.info(
                    "idempotency_cache_set",
                    key=idem_key,
                    path=request.url.path,
                    status_code=response.status_code,
                )
            except (json.JSONDecodeError, Exception) as exc:
                logger.warning("idempotency_cache_set_failed", error=str(exc))
                body_bytes = body_bytes  # keep original

            # Rebuild response with same headers
            new_response = Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )
            return new_response

        return response

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("interview")


def setup_logging() -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def log_event(event: str, **fields: Any) -> None:
    payload = {"event": event, **{key: value for key, value in fields.items() if value is not None}}
    logger.info(json.dumps(payload, ensure_ascii=False, default=str))


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = (request.headers.get("x-request-id") or "").strip() or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-Id"] = request_id
        log_event(
            "http",
            requestId=request_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=elapsed_ms,
        )
        return response

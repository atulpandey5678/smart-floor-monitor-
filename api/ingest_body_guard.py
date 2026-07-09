"""Oversize request-body guard for the Ingest_API (`/api/ingest/*`).

The Edge_Agent pushes durable events — including base64-encoded Event_Images
on ``/api/ingest/alert`` — to the Cloud_Server. To protect the server from
oversized payloads, this middleware rejects any ingest request whose body
exceeds the configured maximum with HTTP 413 **before** the body is parsed or
any persistence occurs (Requirement 2.9).

Two complementary checks are applied, in order of cheapness:

1. **Content-Length check.** When the client declares a ``Content-Length`` that
   already exceeds the cap, the request is rejected immediately without reading
   the body at all.
2. **Streaming byte cap.** For chunked or otherwise unknown-length bodies (no
   trustworthy ``Content-Length``), the body is read incrementally and the
   request is rejected as soon as the running byte total crosses the cap, so an
   oversized body is never fully buffered or parsed.

Only ``/api/ingest/*`` paths are guarded; every other request passes straight
through untouched.

Implementation note: this is a Starlette ``BaseHTTPMiddleware``. In the
installed Starlette version the middleware request is a ``_CachedRequest`` that
replays a body assigned to ``_body`` to downstream handlers. After the streaming
check succeeds we assign the accumulated bytes to ``request._body`` so the
Ingest_API route handlers can still parse the JSON payload normally.
"""

from __future__ import annotations

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import ClientDisconnect

from config import INGEST_MAX_BODY_BYTES
from api.ingest_auth import INGEST_PATH_PREFIX

logger = structlog.get_logger(__name__)


class IngestBodySizeGuardMiddleware(BaseHTTPMiddleware):
    """Reject oversized ``/api/ingest/*`` request bodies with HTTP 413.

    The maximum body size defaults to :data:`config.INGEST_MAX_BODY_BYTES`
    (10 MB, env-overridable) and is inclusive of the base64-encoded
    Event_Image carried by ``/api/ingest/alert``.
    """

    def __init__(self, app, max_body_bytes: int = INGEST_MAX_BODY_BYTES) -> None:
        super().__init__(app)
        self.max_body_bytes = max_body_bytes

    def _too_large(self) -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={
                "detail": (
                    "Request body exceeds the maximum allowed size of "
                    f"{self.max_body_bytes} bytes"
                )
            },
        )

    async def dispatch(self, request: Request, call_next):
        # Only guard ingest endpoints; everything else is untouched.
        if not request.url.path.startswith(INGEST_PATH_PREFIX):
            return await call_next(request)

        max_bytes = self.max_body_bytes

        # 1) Content-Length fast path — reject before reading any body bytes.
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except (TypeError, ValueError):
                declared = None
            if declared is not None and declared > max_bytes:
                logger.info(
                    "Ingest request rejected (413): declared Content-Length "
                    "%s exceeds max %s",
                    declared,
                    max_bytes,
                )
                return self._too_large()

        # 2) Streaming byte cap — for chunked / unknown-length bodies, read
        #    incrementally and reject as soon as the cap is crossed, before
        #    the body is parsed or persisted.
        chunks: list[bytes] = []
        received = 0
        try:
            async for chunk in request.stream():
                received += len(chunk)
                if received > max_bytes:
                    logger.info(
                        "Ingest request rejected (413): streamed body exceeded "
                        "max %s bytes",
                        max_bytes,
                    )
                    return self._too_large()
                chunks.append(chunk)
        except ClientDisconnect:
            # Client hung up mid-upload; nothing to persist. Report a 400 so
            # the middleware chain does not surface an unhandled error.
            return JSONResponse(
                status_code=400,
                content={"detail": "Client disconnected before the body was received"},
            )

        # Cache the fully-read body so downstream handlers can parse it. The
        # _CachedRequest replays `_body` to the rest of the app.
        request._body = b"".join(chunks)

        return await call_next(request)

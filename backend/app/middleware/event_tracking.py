"""Automatic API event tracking middleware."""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware

from app.auth import decode_token
from app.services.event_collector import event_collector

logger = logging.getLogger(__name__)


class EventTrackingMiddleware(BaseHTTPMiddleware):
    TRACKED_PATHS = {
        "/api/data/search": "search.query",
        "/api/integration/adql/query": "search.adql",
        "/api/chat/message": "ai.message_sent",
        "/api/chat/message/stream": "ai.message_sent",
        "/api/pipeline/run": "analysis.pipeline_run",
        "/api/alerts": "alert.viewed",
        "/api/paper": "export.paper_draft",
    }

    async def dispatch(self, request, call_next):
        started = time.perf_counter()

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                request.state.user_id = str(decode_token(auth_header.split(" ", 1)[1]))
            except Exception:
                request.state.user_id = None
        else:
            request.state.user_id = None

        request.state.tracking_session_id = request.headers.get("X-Tracking-Session")

        response = await call_next(request)
        duration_ms = int((time.perf_counter() - started) * 1000)

        path_prefix = next(
            (prefix for prefix in self.TRACKED_PATHS if request.url.path.startswith(prefix)),
            None,
        )
        if path_prefix:
            await event_collector.track(
                event_type=self.TRACKED_PATHS[path_prefix],
                event_data={
                    "path": request.url.path,
                    "method": request.method,
                    "status": response.status_code,
                },
                user_id=getattr(request.state, "user_id", None),
                session_id=getattr(request.state, "tracking_session_id", None),
                duration_ms=duration_ms,
                page=request.headers.get("X-Page-Name"),
            )

        # Track security-relevant HTTP status codes
        _SECURITY_EVENTS = {
            401: "security.auth_failure",
            403: "security.forbidden",
            429: "security.rate_limited",
        }
        security_event = _SECURITY_EVENTS.get(response.status_code)
        if security_event:
            try:
                await event_collector.track(
                    event_type=security_event,
                    event_data={
                        "path": request.url.path,
                        "method": request.method,
                    },
                    user_id=getattr(request.state, "user_id", None),
                    session_id=getattr(request.state, "tracking_session_id", None),
                    duration_ms=duration_ms,
                    page=request.headers.get("X-Page-Name"),
                )
            except Exception:
                logger.warning(
                    "Failed to track security event %s for %s %s",
                    security_event,
                    request.method,
                    request.url.path,
                    exc_info=True,
                )

        return response

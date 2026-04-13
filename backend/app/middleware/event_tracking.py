"""Automatic API event tracking middleware."""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware

from app.auth import decode_token
from app.services.event_collector import event_collector


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
        return response

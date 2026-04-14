"""Structured logging configuration with correlation ID support."""

import contextvars
import logging
import json
import sys
import uuid
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Correlation ID — set per-request by CorrelationIdMiddleware
# ---------------------------------------------------------------------------
correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default="-",
)


class CorrelationIdFilter(logging.Filter):
    """Inject the current correlation ID into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get("-")  # type: ignore[attr-defined]
        return True


class JSONFormatter(logging.Formatter):
    """JSON log formatter for production."""

    def format(self, record):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "correlation_id": getattr(record, "correlation_id", "-"),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


# ---------------------------------------------------------------------------
# ASGI middleware — sets correlation_id_var and echoes it in the response
# ---------------------------------------------------------------------------
class CorrelationIdMiddleware:
    """Add a correlation ID to each HTTP request for log tracing."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract or generate correlation ID
        headers = dict(scope.get("headers", []))
        req_id = (
            headers.get(b"x-request-id", b"").decode()
            or str(uuid.uuid4())[:8]
        )
        correlation_id_var.set(req_id)

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                resp_headers = list(message.get("headers", []))
                resp_headers.append((b"x-request-id", req_id.encode()))
                message["headers"] = resp_headers
            await send(message)

        await self.app(scope, receive, send_with_id)


def setup_logging(json_format: bool = False, level: str = "INFO"):
    """Configure application logging."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)

    # Add correlation ID filter to the root handler
    handler.addFilter(CorrelationIdFilter())

    if json_format:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(correlation_id)s | %(name)s:%(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))

    root.handlers = [handler]

    # Quiet noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

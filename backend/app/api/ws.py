"""WebSocket endpoint for pipeline progress updates."""

import json
import logging
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter()

# Active WebSocket connections per run_id
_connections: dict[str, list[WebSocket]] = defaultdict(list)


async def broadcast_progress(run_id: str, data: dict):
    """Send progress update to all WebSocket clients watching a run."""
    message = json.dumps({"run_id": run_id, **data})
    clients = _connections.get(run_id, [])
    disconnected = []
    for ws in clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        clients.remove(ws)


def notify_progress_sync(run_id: str, data: dict):
    """Synchronous helper to queue a progress broadcast (for use from Celery tasks).

    Publishes to Redis pub/sub so the FastAPI process can relay to WebSockets.
    """
    try:
        import redis as redis_lib
        from app.config import settings
        kwargs = {}
        if settings.redis_ssl:
            kwargs["ssl_cert_reqs"] = "none"
        r = redis_lib.Redis.from_url(settings.redis_url, **kwargs)
        message = json.dumps({"run_id": run_id, **data})
        r.publish("pipeline_progress", message)
        r.close()
    except Exception as e:
        logger.warning(f"Failed to publish progress: {e}")


@router.websocket("/ws/pipeline/{run_id}")
async def pipeline_ws(websocket: WebSocket, run_id: str):
    """WebSocket endpoint for real-time pipeline progress."""
    await websocket.accept()
    _connections[run_id].append(websocket)
    logger.info(f"WebSocket connected for run {run_id}")

    try:
        while True:
            # Keep connection alive; client can send pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        _connections[run_id].remove(websocket)
        if not _connections[run_id]:
            del _connections[run_id]
        logger.info(f"WebSocket disconnected for run {run_id}")


async def redis_subscriber():
    """Background task that listens to Redis pub/sub and relays to WebSockets."""
    try:
        import redis.asyncio as aioredis
        from app.config import settings

        kwargs = {}
        if settings.redis_ssl:
            import ssl as _ssl
            kwargs["ssl_cert_reqs"] = _ssl.CERT_NONE
        r = aioredis.from_url(settings.redis_url, **kwargs)
        pubsub = r.pubsub()
        await pubsub.subscribe("pipeline_progress")

        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    run_id = data.pop("run_id", None)
                    if run_id:
                        await broadcast_progress(run_id, data)
                except (json.JSONDecodeError, KeyError):
                    pass
    except Exception as e:
        logger.warning(f"Redis subscriber failed (WebSocket progress will be unavailable): {e}")

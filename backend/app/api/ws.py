"""WebSocket endpoint for pipeline progress updates and collaboration."""

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

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


# ══════════════════════════════════════
# Collaboration WebSocket
# ══════════════════════════════════════

# Collaboration state
_collab_connections: dict[str, list[tuple]] = defaultdict(list)  # team_id -> [(ws, user_info)]
_team_presence: dict[str, dict[str, dict]] = defaultdict(dict)   # team_id -> {user_id: info}


@router.websocket("/ws/collab/{team_id}")
async def collab_websocket(websocket: WebSocket, team_id: str):
    """Real-time collaboration channel for team pipeline editing."""
    await websocket.accept()

    user_info = {"user_id": "anonymous", "name": "User"}
    _collab_connections[team_id].append((websocket, user_info))

    try:
        # Wait for initial identify message
        init_msg = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        if init_msg.get("type") == "identify":
            user_info["user_id"] = init_msg.get("user_id", "anonymous")
            user_info["name"] = init_msg.get("name", "User")

        # Update presence
        _team_presence[team_id][user_info["user_id"]] = {
            "name": user_info["name"],
            "status": "online",
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }

        # Broadcast presence update
        await _broadcast_collab(team_id, {
            "type": "presence",
            "users": _team_presence[team_id],
        })

        # Message loop
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "cursor":
                await _broadcast_collab(team_id, {
                    "type": "cursor",
                    "user_id": user_info["user_id"],
                    "position": data.get("position"),
                }, exclude=websocket)

            elif msg_type == "comment":
                await _broadcast_collab(team_id, {
                    "type": "comment",
                    "user_id": user_info["user_id"],
                    "user_name": user_info["name"],
                    "content": data.get("content", ""),
                    "node_id": data.get("node_id"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            elif msg_type == "edit":
                await _broadcast_collab(team_id, {
                    "type": "edit",
                    "user_id": user_info["user_id"],
                    "node_id": data.get("node_id"),
                    "params": data.get("params"),
                }, exclude=websocket)

    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _collab_connections[team_id] = [
            (ws, ui) for ws, ui in _collab_connections[team_id] if ws != websocket
        ]
        _team_presence[team_id].pop(user_info["user_id"], None)
        try:
            await _broadcast_collab(team_id, {
                "type": "presence",
                "users": _team_presence[team_id],
            })
        except Exception:
            pass


async def _broadcast_collab(team_id: str, message: dict, exclude=None):
    """Broadcast message to all connections in a team channel."""
    dead = []
    for ws, _ in _collab_connections[team_id]:
        if ws == exclude:
            continue
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _collab_connections[team_id] = [
            (w, u) for w, u in _collab_connections[team_id] if w != ws
        ]

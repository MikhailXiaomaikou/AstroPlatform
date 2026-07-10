"""Best-effort updater for ``ChatSession.agent_status`` / ``current_run_id``.

P1.3.b added these columns. P1.3.a wires them up: the SSE endpoint flips
``agent_status`` to ``running`` when an agent loop starts and back to
``idle`` (or ``suspended`` on hard exit) when it ends, so

- The frontend can ask "is my session mid-run?" before reconnecting and
  decide whether to send ``resume_from_session=true``.
- A second concurrent SSE request can see the in-flight run instead of
  blindly starting a new one (this is the only handle for that — we
  don't try to *join* the in-flight loop in P1.3, just report it).

All updates are best-effort: a DB failure must not break the chat
stream, since the agent_status field is advisory rather than load-bearing.
"""

from __future__ import annotations

import logging
import uuid
from typing import Iterable

from sqlalchemy import select

from app.models.database import async_session as AsyncSessionLocal
from app.models.schemas import ChatSession

logger = logging.getLogger(__name__)

VALID_STATUSES: frozenset[str] = frozenset({"idle", "running", "suspended", "completed"})


def _coerce_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


async def update_session_status(
    session_id: str | uuid.UUID | None,
    *,
    owner_id: str | uuid.UUID | None = None,
    status: str | None = None,
    current_run_id: str | None = None,
) -> None:
    """Patch ``agent_status`` / ``current_run_id`` on a ChatSession row.

    Both ``status`` and ``current_run_id`` are optional — only non-None
    fields are written. If ``status`` is given it must be one of
    ``VALID_STATUSES``.
    """
    sid = _coerce_uuid(session_id)
    owner = _coerce_uuid(owner_id)
    # A session id without an authenticated owner is never sufficient to
    # mutate durable state. Anonymous chats intentionally have neither.
    if sid is None or owner is None:
        return
    if status is not None and status not in VALID_STATUSES:
        logger.debug("ignoring unknown agent_status %r", status)
        return
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ChatSession).where(
                    ChatSession.id == sid,
                    ChatSession.user_id == owner,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return
            if status is not None:
                row.agent_status = status
            if current_run_id is not None:
                row.current_run_id = current_run_id
            await db.commit()
    except Exception as exc:
        logger.debug("agent_status update failed for %s: %s", sid, exc)


async def get_session_status(
    session_id: str | uuid.UUID | None,
    *,
    owner_id: str | uuid.UUID | None = None,
) -> dict[str, str | None]:
    """Return ``{agent_status, current_run_id}`` or ``{}`` on lookup failure."""
    sid = _coerce_uuid(session_id)
    owner = _coerce_uuid(owner_id)
    if sid is None or owner is None:
        return {}
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ChatSession.agent_status, ChatSession.current_run_id).where(
                    ChatSession.id == sid,
                    ChatSession.user_id == owner,
                )
            )
            row = result.first()
            if row is None:
                return {}
            return {"agent_status": row[0], "current_run_id": row[1]}
    except Exception as exc:
        logger.debug("agent_status read failed for %s: %s", sid, exc)
        return {}


def valid_statuses() -> Iterable[str]:
    return VALID_STATUSES

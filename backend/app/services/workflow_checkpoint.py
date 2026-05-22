"""Cross-process checkpoint store for multi-step AI workflows.

A tool call chain may span 5-10 steps (query → filter → fit → crossmatch
→ plot). Before this module existed, a mid-chain failure forced the AI
to restart from scratch even though the first N successful steps were
already in the session cache. WorkflowCheckpoint records (step_idx,
tool_name, inputs_hash, status, cache_refs) so the AI can explicitly
resume from the last good step.

Storage is the shared JsonKvStore (Redis-first, SQLite fallback), so
checkpoints survive process restart and are visible across multi-worker
deployments. TTL is 2 h, enforced by the backend.

Public API (record_step / get_checkpoint / get_last_successful_step /
summarize / clear_session / reset) is unchanged from the previous
in-memory implementation; callers do not need to migrate.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from threading import RLock
from typing import Any

from app.services._kv_store import JsonKvStore

TTL_SECONDS = 2 * 3600
MAX_STEPS_PER_SESSION = 32


@dataclass
class CheckpointStep:
    step_idx: int
    tool_name: str
    inputs_hash: str
    status: str  # "completed" | "failed" | "in_progress"
    cache_refs: list[str] = field(default_factory=list)
    error: str | None = None
    tool_call_id: str | None = None
    summary: str | None = None
    created_at: float = field(default_factory=time.time)


@dataclass
class WorkflowCheckpoint:
    session_id: str
    steps: list[CheckpointStep] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)


_store = JsonKvStore("wf_ckpt")
# Process-local lock guards the read-modify-write window within a single
# worker. Cross-worker concurrency on the same session is accepted to drop
# at most a step or two; chat sessions are pinned to one worker in practice
# (one user, one SSE stream, one agent loop).
_lock = RLock()


def _deserialize(data: dict | None) -> WorkflowCheckpoint | None:
    if not isinstance(data, dict):
        return None
    try:
        steps = [CheckpointStep(**s) for s in data.get("steps", [])]
        return WorkflowCheckpoint(
            session_id=data["session_id"],
            steps=steps,
            updated_at=float(data.get("updated_at", time.time())),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _serialize(cp: WorkflowCheckpoint) -> dict:
    return asdict(cp)


def _persist(cp: WorkflowCheckpoint) -> None:
    _store.set(cp.session_id, _serialize(cp), TTL_SECONDS)


def record_step(
    session_id: str,
    tool_name: str,
    inputs_hash: str,
    status: str,
    cache_refs: list[str] | None = None,
    error: str | None = None,
    tool_call_id: str | None = None,
    summary: str | None = None,
) -> CheckpointStep:
    """Append a step to the session's checkpoint chain."""
    with _lock:
        cp = _deserialize(_store.get(session_id))
        if cp is None:
            cp = WorkflowCheckpoint(session_id=session_id)
        step = CheckpointStep(
            step_idx=len(cp.steps),
            tool_name=tool_name,
            inputs_hash=inputs_hash,
            status=status,
            cache_refs=list(cache_refs or []),
            error=error,
            tool_call_id=tool_call_id,
            summary=summary,
        )
        cp.steps.append(step)
        if len(cp.steps) > MAX_STEPS_PER_SESSION:
            cp.steps = cp.steps[-MAX_STEPS_PER_SESSION:]
            for i, s in enumerate(cp.steps):
                s.step_idx = i
        cp.updated_at = time.time()
        _persist(cp)
        return step


def get_checkpoint(session_id: str) -> WorkflowCheckpoint | None:
    return _deserialize(_store.get(session_id))


def get_last_successful_step(session_id: str) -> CheckpointStep | None:
    cp = get_checkpoint(session_id)
    if cp is None:
        return None
    for step in reversed(cp.steps):
        if step.status == "completed":
            return step
    return None


def summarize(session_id: str) -> dict[str, Any]:
    """JSON-friendly summary for the resume_workflow tool."""
    cp = get_checkpoint(session_id)
    if cp is None or not cp.steps:
        return {"session_id": session_id, "has_checkpoint": False, "steps": []}
    last_successful = next(
        (s for s in reversed(cp.steps) if s.status == "completed"), None
    )
    return {
        "session_id": session_id,
        "has_checkpoint": True,
        "n_steps": len(cp.steps),
        "steps": [
            {
                "step_idx": s.step_idx,
                "tool_name": s.tool_name,
                "status": s.status,
                "cache_refs": s.cache_refs,
                "error": s.error,
                "tool_call_id": s.tool_call_id,
                "summary": s.summary,
                "age_seconds": round(time.time() - s.created_at, 1),
            }
            for s in cp.steps
        ],
        "last_successful_step_idx": (
            last_successful.step_idx if last_successful is not None else None
        ),
    }


def clear_session(session_id: str) -> None:
    with _lock:
        _store.delete(session_id)


def reset() -> None:
    """Test helper — clears every checkpoint in the ``wf_ckpt`` namespace."""
    with _lock:
        _store.clear_namespace()

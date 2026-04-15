"""Lightweight in-memory checkpoint store for multi-step AI workflows.

A tool call chain may span 5-10 steps (query → filter → fit → crossmatch
→ plot). Before this module existed, a mid-chain failure forced the AI
to restart from scratch even though the first N successful steps were
already in the session cache. WorkflowCheckpoint records (step_idx,
tool_name, inputs_hash, status, cache_refs) so the AI can explicitly
resume from the last good step.

Storage is process-local dict keyed by session_id. Entries expire
after TTL (default 2 hours) to prevent unbounded growth. Intentionally
not backed by the database — checkpoints are ephemeral session state,
not persistent research artifacts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

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
    created_at: float = field(default_factory=time.time)


@dataclass
class WorkflowCheckpoint:
    session_id: str
    steps: list[CheckpointStep] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)


_store: dict[str, WorkflowCheckpoint] = {}
_lock = RLock()


def _sweep_expired(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    stale = [sid for sid, cp in _store.items() if now - cp.updated_at > TTL_SECONDS]
    for sid in stale:
        _store.pop(sid, None)


def record_step(
    session_id: str,
    tool_name: str,
    inputs_hash: str,
    status: str,
    cache_refs: list[str] | None = None,
    error: str | None = None,
) -> CheckpointStep:
    """Append a step to the session's checkpoint chain."""
    with _lock:
        _sweep_expired()
        cp = _store.get(session_id)
        if cp is None:
            cp = WorkflowCheckpoint(session_id=session_id)
            _store[session_id] = cp
        step = CheckpointStep(
            step_idx=len(cp.steps),
            tool_name=tool_name,
            inputs_hash=inputs_hash,
            status=status,
            cache_refs=list(cache_refs or []),
            error=error,
        )
        cp.steps.append(step)
        # Bound memory per session
        if len(cp.steps) > MAX_STEPS_PER_SESSION:
            cp.steps = cp.steps[-MAX_STEPS_PER_SESSION:]
            for i, s in enumerate(cp.steps):
                s.step_idx = i
        cp.updated_at = time.time()
        return step


def get_checkpoint(session_id: str) -> WorkflowCheckpoint | None:
    with _lock:
        _sweep_expired()
        return _store.get(session_id)


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
                "age_seconds": round(time.time() - s.created_at, 1),
            }
            for s in cp.steps
        ],
        "last_successful_step_idx": (
            get_last_successful_step(session_id).step_idx
            if get_last_successful_step(session_id) is not None
            else None
        ),
    }


def clear_session(session_id: str) -> None:
    with _lock:
        _store.pop(session_id, None)


def reset() -> None:
    """Test helper."""
    with _lock:
        _store.clear()

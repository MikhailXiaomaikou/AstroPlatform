"""Unit tests for the Python sandbox session-registry TTL sweep.

Verifies the lazy eviction in app.services.code_executor that prevents
_session_vars from growing unbounded on long-running Render instances.
"""

from __future__ import annotations

import time

import pytest

from app.services import code_executor as ce


@pytest.fixture(autouse=True)
def _reset_registry():
    """Isolate each test from the module-global session registry."""
    ce._session_vars.clear()
    ce._session_replay_signatures.clear()
    ce._session_last_access.clear()
    yield
    ce._session_vars.clear()
    ce._session_replay_signatures.clear()
    ce._session_last_access.clear()


def test_get_session_vars_touches_timestamp():
    ce.get_session_vars("alpha")
    ts_first = ce._session_last_access["alpha"]
    # Ensure monotonic clock moves — sleep briefly.
    time.sleep(0.01)
    ce.get_session_vars("alpha")
    ts_second = ce._session_last_access["alpha"]
    assert ts_second >= ts_first


def test_sweep_evicts_idle_sessions(monkeypatch):
    """Sessions older than MAX_SESSION_IDLE_SECONDS are dropped by the sweep."""
    monkeypatch.setattr(ce, "MAX_SESSION_IDLE_SECONDS", 1.0)
    monkeypatch.setattr(ce, "MAX_TRACKED_SESSIONS", 10)

    ce.get_session_vars("active")
    ce.get_session_vars("idle")
    # Backdate the idle session so the TTL branch fires.
    ce._session_last_access["idle"] -= 2.0

    evicted = ce._sweep_idle_sessions()

    assert evicted == 1
    assert "idle" not in ce._session_vars
    assert "active" in ce._session_vars


def _seed_direct(ids_with_ts):
    """Bypass get_session_vars so the built-in sweep does not run during setup."""
    for sid, ts in ids_with_ts:
        ce._session_vars[sid] = {}
        ce._session_last_access[sid] = ts


def test_sweep_caps_registry_size(monkeypatch):
    """Once the cap is crossed, oldest sessions are evicted first."""
    monkeypatch.setattr(ce, "MAX_SESSION_IDLE_SECONDS", 10_000_000.0)  # disable TTL path
    monkeypatch.setattr(ce, "MAX_TRACKED_SESSIONS", 3)

    base = time.monotonic()
    _seed_direct([("s0", base + 0), ("s1", base + 1), ("s2", base + 2),
                  ("s3", base + 3), ("s4", base + 4)])

    evicted = ce._sweep_idle_sessions()

    assert evicted == 2
    # Oldest two (s0, s1) should be gone; most recent three stay.
    assert set(ce._session_vars.keys()) == {"s2", "s3", "s4"}


def test_get_session_vars_triggers_sweep_when_over_cap(monkeypatch):
    """A fresh get_session_vars call runs the sweep if we're already over cap."""
    monkeypatch.setattr(ce, "MAX_SESSION_IDLE_SECONDS", 10_000_000.0)
    monkeypatch.setattr(ce, "MAX_TRACKED_SESSIONS", 3)

    base = time.monotonic()
    # Seed 4 existing sessions at known timestamps without going through
    # get_session_vars (which would trigger the sweep during setup).
    _seed_direct([("s0", base + 0), ("s1", base + 1), ("s2", base + 2), ("s3", base + 3)])

    # Creating a new session tips the registry to 5 > cap; the inline sweep
    # must fire and evict the oldest.
    ce.get_session_vars("s4")

    assert "s0" not in ce._session_vars  # oldest evicted
    assert "s4" in ce._session_vars       # newest kept
    assert len(ce._session_vars) <= ce.MAX_TRACKED_SESSIONS


def test_clear_session_vars_also_clears_meta():
    ce.get_session_vars("beta")
    assert "beta" in ce._session_last_access
    ce.clear_session_vars("beta")
    assert "beta" not in ce._session_vars
    assert "beta" not in ce._session_last_access

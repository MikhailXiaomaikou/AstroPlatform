"""Tests for the Celery task wrapper that runs long ai_tools jobs.

Tests call the task body directly (``.run(...)``) instead of dispatching
through a broker, so they don't need a live Redis. The actual ai_tools
``execute_tool`` is monkeypatched to a fast async stub.
"""

from __future__ import annotations

import pytest

from app.services import _kv_store, async_tool_runtime as atr


@pytest.fixture(autouse=True)
def _isolate():
    _kv_store.use_memory_backend_for_testing()
    # No-op dispatcher so submit_async_job doesn't try to send to Celery during setup.
    atr.set_dispatcher(lambda *a, **kw: None)
    yield
    atr.reset_dispatcher()


def _prime_job(tool_name: str, args: dict) -> str:
    banner = atr.submit_async_job(tool_name, args)
    return banner["job_id"]


def test_run_long_tool_success(monkeypatch):
    from app.tasks import ai_tools_tasks as att

    async def fake_execute(tool_name, tool_input, **kwargs):
        assert tool_name == "fake_tool"
        assert tool_input == {"foo": "bar"}
        return {"answer": 42, "__tool_status__": "OK"}

    monkeypatch.setattr("app.services.ai_tools.execute_tool", fake_execute)

    job_id = _prime_job("fake_tool", {"foo": "bar"})
    out = att.run_long_tool.run("fake_tool", {"foo": "bar"}, job_id)

    assert out["status"] == "completed"
    job = atr.get_async_job(job_id)
    assert job["status"] == "completed"
    assert job["result"]["answer"] == 42
    assert "completed_at" in job


def test_run_long_tool_records_error(monkeypatch):
    from app.tasks import ai_tools_tasks as att

    async def boom(tool_name, tool_input, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("app.services.ai_tools.execute_tool", boom)

    job_id = _prime_job("flaky_tool", {})
    out = att.run_long_tool.run("flaky_tool", {}, job_id)

    assert out["status"] == "failed"
    assert "kaboom" in out["error"]
    job = atr.get_async_job(job_id)
    assert job["status"] == "failed"
    assert job["error_class"] == "RuntimeError"


def test_run_long_tool_respects_pre_run_cancel(monkeypatch):
    from app.tasks import ai_tools_tasks as att

    async def should_not_run(*args, **kwargs):
        raise AssertionError("execute_tool should not have been called after cancel")

    monkeypatch.setattr("app.services.ai_tools.execute_tool", should_not_run)

    job_id = _prime_job("slow_tool", {})
    atr.cancel_async_job(job_id)

    out = att.run_long_tool.run("slow_tool", {}, job_id)
    assert out["status"] == "cancelled"


def test_run_long_tool_late_cancel_keeps_result(monkeypatch):
    from app.tasks import ai_tools_tasks as att

    async def fake_execute(tool_name, tool_input, **kwargs):
        # Simulate the cancel arriving while we're computing.
        atr.cancel_async_job(att._current_job_id_for_test)  # type: ignore[attr-defined]
        return {"partial": True, "__tool_status__": "OK"}

    monkeypatch.setattr("app.services.ai_tools.execute_tool", fake_execute)

    job_id = _prime_job("slow_tool", {})
    att._current_job_id_for_test = job_id  # type: ignore[attr-defined]
    try:
        out = att.run_long_tool.run("slow_tool", {}, job_id)
    finally:
        del att._current_job_id_for_test  # type: ignore[attr-defined]

    assert out["status"] == "cancelled_with_result"
    job = atr.get_async_job(job_id)
    assert job["result"]["partial"] is True

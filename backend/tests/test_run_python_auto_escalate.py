"""U2b (PART U): run_python auto-escalates mode='normal' → 'slow' on timeout.

Round 10 observation: AI calling download_and_clean_lightcurve with default
mode='normal' (75s budget) consistently timed out on TESS downloads. The
helper's error_class='timeout' message correctly asked the AI to retry with
mode='slow', but that wasted a round trip. Fix: on the first timeout auto-
retry once with mode='slow' (300s) and surface `auto_escalated_mode=True`
in the response.
"""

import asyncio
import pytest


@pytest.mark.asyncio
async def test_normal_mode_timeout_auto_escalates(monkeypatch):
    """normal-mode timeout → retry mode='slow' → success."""
    from app.services import ai_tools

    calls = []

    def fake_execute_python(code, context, session_id, timeout_s):
        calls.append(timeout_s)
        if timeout_s <= 75.0:
            # First call (mode='normal', 75s) simulates a timeout.
            raise asyncio.TimeoutError("simulated slow task")
        # Second call (mode='slow', 300s) succeeds.
        class _R:
            success = True
            stdout = "ok\n"
            stderr = ""
            error = None
            figures = []
            variables = {}
            variable_types = {}
            backend = "inprocess"
            duration_ms = 100
            exit_code = 0
        return _R()

    from app.services import code_executor
    monkeypatch.setattr(code_executor, "execute_python", fake_execute_python)

    # wait_for sees TimeoutError from inside the executor; that path
    # means our auto-escalate code retries with 300s.
    original_wait_for = asyncio.wait_for

    async def fake_wait_for(awaitable, timeout):
        # Surface a TimeoutError on the first call, complete on subsequent.
        if timeout <= 75.0:
            # cancel the inner task to avoid resource warnings
            if hasattr(awaitable, "cancel"):
                awaitable.cancel()
            raise asyncio.TimeoutError
        return await original_wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    response = await ai_tools._exec_run_python(
        {"code": "print('hi')", "data_source": "none_not_analyzing_real_data"},
        python_session_id="test_escalate",
    )

    assert response["success"] is True, response
    assert response.get("auto_escalated_mode") is True
    assert response.get("mode") == "slow"
    assert "note" in response
    assert "auto-escalated" in response["note"].lower()


@pytest.mark.asyncio
async def test_explicit_slow_mode_no_escalate_needed(monkeypatch):
    """mode='slow' 直接跑 300s, 不触发 escalate 逻辑."""
    from app.services import ai_tools

    def fake_execute_python(code, context, session_id, timeout_s):
        assert timeout_s == 300.0
        class _R:
            success = True
            stdout = ""
            stderr = ""
            error = None
            figures = []
            variables = {}
            variable_types = {}
            backend = "inprocess"
            duration_ms = 100
            exit_code = 0
        return _R()

    from app.services import code_executor
    monkeypatch.setattr(code_executor, "execute_python", fake_execute_python)

    response = await ai_tools._exec_run_python(
        {"code": "print('hi')", "mode": "slow",
         "data_source": "none_not_analyzing_real_data"},
        python_session_id="test_slow",
    )
    assert response["success"] is True
    assert response.get("auto_escalated_mode") is not True
    assert response.get("mode") == "slow"


@pytest.mark.asyncio
async def test_slow_mode_timeout_final_error(monkeypatch):
    """mode='slow' 超时 → 直接报错, 不再 escalate (避免死循环)."""
    from app.services import ai_tools

    async def fake_wait_for(awaitable, timeout):
        if hasattr(awaitable, "cancel"):
            awaitable.cancel()
        raise asyncio.TimeoutError

    def fake_execute_python(*a, **k):
        return None  # never called because wait_for always times out

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    from app.services import code_executor
    monkeypatch.setattr(code_executor, "execute_python", fake_execute_python)

    response = await ai_tools._exec_run_python(
        {"code": "while True: pass", "mode": "slow",
         "data_source": "none_not_analyzing_real_data"},
        python_session_id="test_slow_timeout",
    )
    assert response["success"] is False
    assert response.get("error_class") == "timeout"
    assert "300" in response.get("error", "") or "slow" in response.get("error", "")

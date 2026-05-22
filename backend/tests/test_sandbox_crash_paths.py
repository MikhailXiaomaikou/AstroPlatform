"""F0.5: Regression tests for the sandbox failure-surfacing paths.

Covers the concrete bug surfaced by the Pleiades reviewer: when the
subprocess sandbox crashed mid-run (OOM / SIGKILL / broken pipe), the
backend returned a SandboxResult with success=False but error=None, and
the _exec_run_python tool wrapper then dropped the error field, so the
frontend rendered the generic "Python sandbox returned no message" string
with no actionable information.

F0.1 hardens the subprocess backend's payload-completeness check.
F0.2 guarantees _exec_run_python always populates an error field on
failure plus an error_class chip label.

These tests exercise both ends.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="subprocess sandbox uses POSIX rlimit / setsid",
)

from app.services.sandbox.subprocess_backend import SubprocessBackend  # noqa: E402


SMALL_TIMEOUT = 5
SMALL_MEMORY = 512 * 1024 * 1024


class TestSubprocessPayloadGuard:
    """F0.1 — SubprocessBackend must surface a concrete error for every
    failure mode, including the ones where the child dies before sending
    back a complete payload dict."""

    def test_sigkill_mid_execution_surfaces_error(self):
        """Child kills itself with SIGKILL (uncatchable) before send()."""
        import os as _os

        r = SubprocessBackend().execute(
            "import os, signal; os.kill(os.getpid(), signal.SIGKILL)",
            timeout=SMALL_TIMEOUT,
            memory_bytes=SMALL_MEMORY,
        )
        assert r.success is False
        assert r.error is not None and r.error.strip() != ""
        low = r.error.lower()
        # Must match one of the F0.1 error phrases, not be empty.
        assert any(
            tok in low
            for tok in (
                "terminated without result",
                "incomplete payload",
                "without an error message",
            )
        ), f"error message should describe the crash, got: {r.error!r}"
        assert "os" != None  # keep linter happy about _os import ordering
        del _os

    def test_empty_dict_payload_surfaces_error(self):
        """Simulate the child returning {} (observed when serialization
        breaks mid-send). The old code returned success=False, error=None;
        F0.1 must catch this and return a concrete error message."""

        class _FakeConn:
            def __init__(self, value):
                self._value = value

            def poll(self, timeout=None):
                return True

            def recv(self):
                return self._value

            def close(self):
                return None

        class _FakeProc:
            exitcode = 0
            pid = 12345

            def start(self):
                return None

            def join(self, timeout=None):
                return None

            def is_alive(self):
                return False

            def kill(self):
                return None

        def _fake_pipe(duplex=False):
            parent = _FakeConn({})  # empty dict payload
            child = _FakeConn({})
            return parent, child

        def _fake_process(*a, **kw):
            return _FakeProc()

        fake_ctx = SimpleNamespace(Pipe=_fake_pipe, Process=_fake_process)

        with patch(
            "app.services.sandbox.subprocess_backend._ctx",
            fake_ctx,
        ):
            r = SubprocessBackend().execute(
                "x = 1",
                timeout=SMALL_TIMEOUT,
                memory_bytes=SMALL_MEMORY,
            )

        assert r.success is False
        assert r.error is not None and r.error.strip() != ""
        assert "incomplete payload" in r.error.lower()

    def test_non_dict_payload_surfaces_error(self):
        """Payload is non-None but the wrong type (bytes / str / None-like)."""

        class _FakeConn:
            def __init__(self, value):
                self._value = value

            def poll(self, timeout=None):
                return True

            def recv(self):
                return self._value

            def close(self):
                return None

        class _FakeProc:
            exitcode = 0
            pid = 12345

            def start(self):
                return None

            def join(self, timeout=None):
                return None

            def is_alive(self):
                return False

            def kill(self):
                return None

        def _fake_pipe(duplex=False):
            return _FakeConn("not-a-dict"), _FakeConn("not-a-dict")

        def _fake_process(*a, **kw):
            return _FakeProc()

        fake_ctx = SimpleNamespace(Pipe=_fake_pipe, Process=_fake_process)

        with patch(
            "app.services.sandbox.subprocess_backend._ctx",
            fake_ctx,
        ):
            r = SubprocessBackend().execute(
                "x = 1",
                timeout=SMALL_TIMEOUT,
                memory_bytes=SMALL_MEMORY,
            )

        assert r.success is False
        assert r.error is not None and r.error.strip() != ""
        assert "incomplete payload" in r.error.lower()

    def test_success_false_without_error_key_surfaces_error(self):
        """Payload has success=False but no error field (historical bug
        pattern — child crashed mid-exception-handling)."""

        class _FakeConn:
            def __init__(self, value):
                self._value = value

            def poll(self, timeout=None):
                return True

            def recv(self):
                return self._value

            def close(self):
                return None

        class _FakeProc:
            exitcode = -9  # SIGKILL
            pid = 12345

            def start(self):
                return None

            def join(self, timeout=None):
                return None

            def is_alive(self):
                return False

            def kill(self):
                return None

        def _fake_pipe(duplex=False):
            payload = {"success": False, "stdout": "", "stderr": ""}
            return _FakeConn(payload), _FakeConn(payload)

        def _fake_process(*a, **kw):
            return _FakeProc()

        fake_ctx = SimpleNamespace(Pipe=_fake_pipe, Process=_fake_process)

        with patch(
            "app.services.sandbox.subprocess_backend._ctx",
            fake_ctx,
        ):
            r = SubprocessBackend().execute(
                "x = 1",
                timeout=SMALL_TIMEOUT,
                memory_bytes=SMALL_MEMORY,
            )

        assert r.success is False
        assert r.error is not None and r.error.strip() != ""
        # F0.1 synthesizes a message when success=False but error missing
        assert "without an error message" in r.error.lower()


class TestExecRunPythonErrorFloor:
    """F0.2 — _exec_run_python must always populate `error` + `error_class`
    when success is False, so the frontend never has to fall back to the
    generic "sandbox returned no message" string."""

    def test_failure_with_error_passes_through(self):
        """Normal NameError path: error string comes from the sandbox."""
        from app.services.ai_tools import _exec_run_python

        response = asyncio.run(
            _exec_run_python({"code": "print(no_such_variable)"})
        )
        assert response["success"] is False
        assert "error" in response
        assert response["error"].strip() != ""
        assert "error_class" in response
        assert response["error_class"] == "name_error"

    def test_failure_without_error_gets_synthesized_message(self):
        """Simulate CodeExecutionResult with success=False, error=None
        reaching _exec_run_python — the tool wrapper must still emit a
        usable error field and error_class="sandbox_crash"."""
        from app.services.ai_tools import _exec_run_python
        from app.services.code_executor import CodeExecutionResult

        empty_failure = CodeExecutionResult()
        empty_failure.success = False
        empty_failure.error = None
        empty_failure.stdout = ""
        empty_failure.stderr = ""

        async def _fake_loop_run(*_args, **_kwargs):
            return empty_failure

        async def _run():
            loop = asyncio.get_running_loop()
            with patch.object(
                loop,
                "run_in_executor",
                side_effect=lambda *a, **kw: asyncio.sleep(0, result=empty_failure),
            ):
                return await _exec_run_python({"code": "print(1)"})

        # asyncio.sleep(0, result=...) is 3.7+; easier: patch execute_python
        from unittest.mock import MagicMock

        with patch(
            "app.services.code_executor.execute_python",
            MagicMock(return_value=empty_failure),
        ):
            response = asyncio.run(_exec_run_python({"code": "print(1)"}))

        assert response["success"] is False
        assert "error" in response
        msg = response["error"].lower()
        assert "sandbox reported failure" in msg or "without a specific error" in msg
        assert response.get("error_class") == "sandbox_crash"
        assert response.get("__tool_status__") == "FAILED"
        assert response.get("analysis_status") == "failed"

    def test_empty_code_early_return(self):
        """Empty code should still hit the pre-existing empty_input path,
        not go anywhere near the sandbox."""
        from app.services.ai_tools import _exec_run_python

        response = asyncio.run(_exec_run_python({"code": ""}))
        assert response["success"] is False
        assert response.get("error_class") == "empty_input"
        assert "empty" in response["error"].lower()

    def test_synthetic_declared_crash_keeps_failed_status(self):
        """R22: data_source=none must not overwrite a sandbox failure as SYNTHETIC."""
        from app.services.ai_tools import _exec_run_python

        response = asyncio.run(_exec_run_python({
            "code": "import numpy as np\nx = np.random.normal(size=2)\nraise MemoryError('forced test failure')",
            "data_source": "none_not_analyzing_real_data",
        }))
        assert response["success"] is False
        assert response.get("data_origin") == "synthetic"
        assert response.get("__tool_status__") == "FAILED"
        assert response.get("analysis_status") == "failed"

    def test_timeout_reaches_timeout_error_class(self):
        """F0.2 classifier — a "timed out" message surfaces as error_class=timeout."""
        from app.services.ai_tools import _classify_sandbox_error

        assert _classify_sandbox_error("Code execution timed out after 90 seconds") == "timeout"
        assert _classify_sandbox_error("MemoryError: process hit limit") == "oom"
        assert _classify_sandbox_error("SIGSEGV signal 11") == "sandbox_crash"
        assert _classify_sandbox_error("process killed by SIGKILL signal 9") == "oom"
        assert _classify_sandbox_error("sandbox subprocess terminated without result") == "sandbox_crash"
        assert _classify_sandbox_error("NameError: name 'x' is not defined") == "name_error"
        assert _classify_sandbox_error("ImportError: no module named astropy") == "import_error"
        assert _classify_sandbox_error("SyntaxError: invalid syntax") == "syntax_error"
        assert _classify_sandbox_error("") == "unknown"


class TestEndToEndNoMessageBugIsGone:
    """Integration test for the exact bug the reviewer hit: a run_python
    call whose sandbox subprocess was killed returns a complete, usable
    error payload — never the "Python sandbox returned no message" path."""

    def test_failed_call_always_has_error_field_and_class(self):
        from app.services.ai_tools import _exec_run_python

        # Any failing code MUST produce a response with both error and
        # error_class populated.  Pre-F0, a backend crash (SIGKILL, pipe
        # break) silently returned success=False with no error field,
        # triggering the "Python sandbox returned no message" fallback.
        # Post-F0, every failure path carries a user-facing message.
        response = asyncio.run(
            _exec_run_python({
                "code": "raise RuntimeError('pleiades test regression')"
            })
        )
        assert response["success"] is False
        assert "error" in response, (
            "F0 regression: tool response missing error field — the "
            "frontend would fall back to 'Python sandbox returned no "
            "message'"
        )
        assert response["error"].strip() != ""
        assert "error_class" in response
        assert response["error_class"] != "unknown"

    def test_import_blocked_path_produces_complete_response(self):
        """The in-process sandbox blocks os/sys/subprocess imports.  This
        previously surfaced as 'Python sandbox returned no message' in
        some configurations; now it must carry a concrete ImportError."""
        from app.services.ai_tools import _exec_run_python

        response = asyncio.run(
            _exec_run_python({
                "code": "import os\nos.kill(os.getpid(), 9)\n"
            })
        )
        assert response["success"] is False
        assert response.get("error_class") == "import_error"
        assert "error" in response and response["error"].strip() != ""

    def test_x5_session_counter_bumps_across_calls(self):
        """X5 (PART X): _session_run_python_count accumulates across every run_python call
        (both successful and failed). This is the foundation for B4/B5/B6 crash-data
        collection on the 3rd+ attempt."""
        from app.services.ai_tools import (
            _bump_run_python_attempt_idx,
            _session_run_python_count,
        )
        # Use an isolated session id to avoid interference with other tests
        sid = f"pytest-x5-counter-{id(self)}"
        _session_run_python_count.pop(sid, None)

        assert _bump_run_python_attempt_idx(sid) == 1
        assert _bump_run_python_attempt_idx(sid) == 2
        assert _bump_run_python_attempt_idx(sid) == 3
        assert _session_run_python_count[sid] == 3

        _session_run_python_count.pop(sid, None)

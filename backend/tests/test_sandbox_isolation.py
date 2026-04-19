"""Stability tests for the subprocess sandbox backend.

These tests verify that user code which would crash, hang, or exhaust
memory is contained in the child process and cannot affect the parent
pytest process. They exercise the backend directly; the in-process
backend has its own coverage in test_code_executor.py.
"""

import os
import signal
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="subprocess sandbox uses POSIX rlimit / setsid",
)

from app.services.sandbox.subprocess_backend import SubprocessBackend  # noqa: E402


SMALL_TIMEOUT = 5  # seconds — keep the suite fast
SMALL_MEMORY = 256 * 1024 * 1024  # 256 MB


def _run(code: str, *, timeout: int = SMALL_TIMEOUT, memory: int = SMALL_MEMORY):
    return SubprocessBackend().execute(code, timeout=timeout, memory_bytes=memory)


class TestSubprocessSandboxIsolation:
    def test_simple_success_returns_stdout(self):
        r = _run("print('hello from sandbox')\nresult = 1 + 1")
        assert r.success is True
        assert "hello from sandbox" in r.stdout
        assert r.backend == "subprocess"
        assert r.variables.get("result") == "2"

    def test_infinite_loop_is_killed(self):
        r = _run("while True:\n    pass", timeout=2)
        assert r.success is False
        assert r.error is not None

    def test_memory_bomb_is_contained(self):
        r = _run(
            "x = bytearray(4 * 1024 * 1024 * 1024)",
            memory=128 * 1024 * 1024,
        )
        assert r.success is False
        assert r.error is not None

    def test_sys_exit_does_not_crash_parent(self):
        r = _run("import sys; sys.exit(42)")
        assert r.success is False
        assert "SystemExit" in (r.error or "")

    @pytest.mark.skipif(
        sys.platform == "darwin" and not os.environ.get("RUN_CRASH_TESTS"),
        reason=(
            "On macOS, an in-process SIGSEGV is logged by ReportCrash as a "
            ".ips file under ~/Library/Logs/DiagnosticReports/. Set "
            "RUN_CRASH_TESTS=1 to opt in; otherwise rely on "
            "test_abrupt_child_death_is_contained for the same invariant."
        ),
    )
    def test_segfault_is_contained(self):
        # ctypes is a builtin module so it IS importable in subprocess mode;
        # dereferencing NULL should abort the child, not the parent.
        r = _run("import ctypes; ctypes.string_at(0)")
        assert r.success is False
        # Either the child reported an error or died without sending one —
        # both outcomes are acceptable; the key property is the parent is
        # still alive to make this assertion.

    def test_abrupt_child_death_is_contained(self):
        # SIGKILL is delivered by the kernel without a Mach exception, so
        # macOS ReportCrash does NOT write a .ips file — unlike SIGSEGV.
        # Exercises the same parent-survival invariant as the segfault test.
        r = _run("import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
        assert r.success is False
        assert r.error is not None

    def test_exception_traceback_captured(self):
        r = _run("raise ValueError('boom')")
        assert r.success is False
        assert "ValueError" in (r.error or "")
        assert "boom" in (r.error or "")

    def test_matplotlib_figure_captured_as_base64(self):
        r = _run(
            "import matplotlib.pyplot as plt\n"
            "plt.figure(); plt.plot([1,2,3],[1,4,9]); plt.title('t')"
        )
        assert r.success is True, r.error
        assert len(r.figures) == 1
        # PNG base64 starts with iVBOR
        assert r.figures[0].startswith("iVBOR")

    def test_parent_survives_repeated_crashes(self):
        for _ in range(3):
            _run("raise SystemError('bang')")
            _run("while True: pass", timeout=1)
        r = _run("print('still alive')")
        assert r.success is True
        assert "still alive" in r.stdout

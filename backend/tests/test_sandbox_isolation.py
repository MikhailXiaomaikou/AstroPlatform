"""Stability tests for the subprocess sandbox backend.

These tests verify that user code which would crash, hang, or exhaust
memory is contained in the child process and cannot affect the parent
pytest process. They exercise the backend directly; the in-process
backend has its own coverage in test_code_executor.py.
"""

import os
import signal
import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="subprocess sandbox uses POSIX rlimit / setsid",
)

from app.services.sandbox.subprocess_backend import SubprocessBackend  # noqa: E402
from app.services.sandbox import subprocess_backend as sb  # noqa: E402


SMALL_TIMEOUT = 5  # seconds — keep the suite fast
SMALL_MEMORY = 512 * 1024 * 1024  # 512 MB，覆盖率注入下启动 matplotlib/numpy 需要更高预算


def _run(code: str, *, timeout: int = SMALL_TIMEOUT, memory: int = SMALL_MEMORY):
    return SubprocessBackend().execute(code, timeout=timeout, memory_bytes=memory)


class TestSubprocessSandboxIsolation:
    def test_simple_success_returns_stdout(self):
        r = _run("print('hello from sandbox')\nresult = 1 + 1")
        assert r.success is True
        assert "hello from sandbox" in r.stdout
        assert r.backend == "subprocess"
        assert r.variables.get("result") == "2"

    def test_cache_accessors_are_available(self):
        r = SubprocessBackend().execute(
            "rows = get_cached_results('latest_adql')\n"
            "print(rows[0]['value'])\n"
            "import astro\n"
            "print(astro.get_adql_results()[0]['value'])",
            timeout=SMALL_TIMEOUT,
            memory_bytes=SMALL_MEMORY,
            cache_context={"latest_adql": [{"value": "cached"}]},
        )
        assert r.success is True
        assert "cached" in r.stdout

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


class TestSubprocessSandboxHelpers:
    def test_child_limits_are_best_effort(self, monkeypatch):
        calls = []

        def _record_limit(kind, limits):
            calls.append((kind, limits))

        monkeypatch.setattr(sb.resource, "setrlimit", _record_limit)
        monkeypatch.setattr(sb.os, "setsid", lambda: calls.append(("setsid", None)))

        sb._child_set_limits(1234, 5)

        assert (sb.resource.RLIMIT_AS, (1234, 1234)) in calls
        assert (sb.resource.RLIMIT_CPU, (5, 5)) in calls
        assert "setsid" in [kind for kind, _limits in calls]

        def _raise_oserror(*_args):
            raise OSError("unavailable")

        monkeypatch.setattr(sb.resource, "setrlimit", _raise_oserror)
        monkeypatch.setattr(sb.os, "setsid", _raise_oserror)

        sb._child_set_limits(1234, 5)

    def test_child_breadcrumb_is_best_effort(self, monkeypatch):
        writes = []

        def _write(fd, data):
            writes.append((fd, data))
            return len(data)

        monkeypatch.setattr(sb.os, "write", _write)
        sb._child_breadcrumb("ready")
        assert writes and b"ready" in writes[0][1]

        def _raise_oserror(*_args):
            raise OSError("closed")

        monkeypatch.setattr(sb.os, "write", _raise_oserror)
        sb._child_breadcrumb("ignored")

    def test_safe_var_repr_handles_common_large_values(self):
        import numpy as np
        import pandas as pd

        array_repr = sb._safe_var_repr(np.array([1.0, 2.0, 3.0]))
        assert "ndarray" in array_repr
        assert "shape=(3,)" in array_repr

        bad_mean_repr = sb._safe_var_repr(np.array(["not-a-number"], dtype=object))
        assert "mean=None" in bad_mean_repr

        frame_repr = sb._safe_var_repr(pd.DataFrame({"a": [1], "b": [2]}))
        assert "DataFrame" in frame_repr
        assert "columns=['a', 'b']" in frame_repr

        class _BadRepr:
            def __repr__(self):
                raise RuntimeError("bad repr")

        assert "repr failed" in sb._safe_var_repr(_BadRepr())

        class _LongRepr:
            def __repr__(self):
                return "x" * (sb._VAR_REPR_MAX + 5)

        assert "TRUNCATED" in sb._safe_var_repr(_LongRepr())

    def test_cache_accessors_and_astro_module(self):
        accessors = sb._make_cache_accessors({
            "latest": [{"name": "M31"}],
            "latest_adql": [{"value": 1}],
            "latest_adql_set": {"rows": [{"value": 2}]},
            "latest_adql_sets": [{"rows": [{"value": 3}]}],
        })

        assert accessors["get_search_results"]() == [{"name": "M31"}]
        assert accessors["get_adql_results"]() == [{"value": 1}]
        assert accessors["get_latest_adql_result"]() == {"rows": [{"value": 2}]}
        assert accessors["get_adql_result_sets"]() == [{"rows": [{"value": 3}]}]
        assert accessors["get_cached_results"]("missing") is None
        assert sb._normalize_adql_rows({"rows": "not-rows"}) == []
        assert sb._normalize_adql_rows([{"columns": ["a"], "rows": [{"a": 1}]}]) == [{"a": 1}]
        assert sb._normalize_adql_rows([{"plain": "row"}]) == [{"plain": "row"}]
        assert sb._normalize_adql_rows(object()) == []

        astro_module = sb._make_astro_module({"get_cached_results": lambda key: f"hit:{key}"})
        assert astro_module is not None
        assert astro_module.get_cached_results("latest") == "hit:latest"
        assert sys.modules["astro"] is astro_module

    def test_execute_kills_alive_child_without_payload(self, monkeypatch):
        class _FakeConn:
            def poll(self, timeout=None):
                return False

            def recv(self):
                raise AssertionError("recv should not be called")

            def close(self):
                return None

        class _FakeProc:
            exitcode = -9
            pid = 12345

            def __init__(self):
                self.killed = False
                self.alive = True

            def start(self):
                return None

            def join(self, timeout=None):
                return None

            def is_alive(self):
                return self.alive

            def kill(self):
                self.killed = True
                self.alive = False

        fake_proc = _FakeProc()

        fake_ctx = SimpleNamespace(
            Pipe=lambda duplex=False: (_FakeConn(), _FakeConn()),
            Process=lambda *args, **kwargs: fake_proc,
        )

        monkeypatch.setattr(sb, "_ctx", fake_ctx)
        monkeypatch.setattr(sb.os, "killpg", lambda *_args: (_ for _ in ()).throw(PermissionError()))

        result = SubprocessBackend().execute("x = 1", timeout=1, memory_bytes=128 * 1024 * 1024)

        assert result.success is False
        assert "terminated without result" in result.error
        assert fake_proc.killed is True

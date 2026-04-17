"""Subprocess sandbox backend.

Runs user code in a fresh `multiprocessing.Process` per call with enforced
wall-clock and address-space limits. Guarantees that runaway code
(infinite loops, OOM, SIGSEGV via ctypes, `sys.exit`, fork bombs) cannot
crash or leak into the parent FastAPI worker.

Trade-offs vs the in-process backend:
- Each call pays ~1-2 s to re-import numpy/scipy/astropy in the child
- Session variables do NOT persist across calls in this backend (fresh
  globals every time). Callers needing Jupyter-like state should use
  the in-process backend.
- Only base64-PNG figures, stdout/stderr, and stringified variable
  reprs cross the process boundary — complex objects stay in the child.
"""

from __future__ import annotations

import base64
import logging
import multiprocessing as mp
import os
import resource
import signal
import sys
import time
import traceback
from io import BytesIO, StringIO

from app.services.sandbox.base import SandboxResult

logger = logging.getLogger(__name__)

_ctx = mp.get_context("spawn")  # spawn for consistent behavior across platforms


def _child_set_limits(memory_bytes: int, cpu_seconds: int) -> None:
    """Apply rlimit caps inside the child process."""
    try:
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    except (ValueError, OSError, AttributeError):
        pass
    # Isolate the process group so the parent can signal the whole tree
    try:
        os.setsid()
    except OSError:
        pass


def _child_main(code: str, conn, memory_bytes: int, cpu_seconds: int) -> None:
    """Entry point for the sandbox subprocess."""
    _child_set_limits(memory_bytes, cpu_seconds)

    result: dict = {
        "stdout": "",
        "stderr": "",
        "error": None,
        "figures": [],
        "variables": {},
        "variable_types": {},
        "success": True,
    }

    stdout_buf = StringIO()
    stderr_buf = StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr

    try:
        sys.stdout, sys.stderr = stdout_buf, stderr_buf

        # Matplotlib must be Agg before the first import of pyplot
        import matplotlib
        matplotlib.use("Agg")
        matplotlib.rcParams["font.sans-serif"] = [
            "Noto Sans CJK SC", "Noto Sans CJK", "WenQuanYi Micro Hei",
            "PingFang SC", "Microsoft YaHei", "SimHei", "DejaVu Sans",
        ]
        matplotlib.rcParams["axes.unicode_minus"] = False
        import matplotlib.pyplot as plt
        import numpy as np

        # H4: Filter the truly dangerous builtins (open/exec/eval/compile) from
        # user code.  Previously the subprocess backend exposed the full
        # builtins module — a weaker posture than the in-proc sandbox it was
        # meant to harden.  Note: subprocess mode is still "crash isolation,
        # not full security isolation", so we preserve native __import__ (the
        # child has its own OS process; module-level restrictions that the
        # in-proc sandbox relies on are not needed here and would break
        # legitimate `import sys` / `import time` usage).
        import builtins as _builtins
        _BLOCKED = {"exec", "eval", "compile", "open"}
        _safe_builtins = {k: v for k, v in vars(_builtins).items() if k not in _BLOCKED}

        exec_globals: dict = {
            "__builtins__": _safe_builtins,
            "np": np,
            "numpy": np,
            "plt": plt,
            "matplotlib": matplotlib,
        }
        # Best-effort pre-imports (child-only, skipped if missing)
        for mod_name, alias in (
            ("pandas", "pd"),
            ("scipy", "scipy"),
            ("astropy", "astropy"),
        ):
            try:
                mod = __import__(mod_name)
                exec_globals[alias] = mod
                if mod_name != alias:
                    exec_globals[mod_name] = mod
            except ImportError:
                pass

        pre_keys = set(exec_globals.keys())

        exec(code, exec_globals)  # noqa: S102

        # Capture figures
        for fig_num in plt.get_fignums():
            fig = plt.figure(fig_num)
            buf = BytesIO()
            try:
                fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
                result["figures"].append(base64.b64encode(buf.getvalue()).decode("utf-8"))
            except Exception as fig_err:  # pragma: no cover
                stderr_buf.write(f"[figure capture failed: {fig_err}]\n")
        plt.close("all")

        # Stringify new user variables (skip privates, modules, pre-existing)
        import inspect as _inspect
        for name, val in exec_globals.items():
            if name.startswith("_") or name in pre_keys:
                continue
            try:
                if _inspect.ismodule(val):
                    continue
                r = repr(val)
                if len(r) < 5000:
                    result["variables"][name] = r
                    result["variable_types"][name] = type(val).__name__
            except Exception:
                continue

    except SystemExit as e:
        result["success"] = False
        result["error"] = f"SystemExit: {e.code}"
    except MemoryError:
        result["success"] = False
        result["error"] = "MemoryError: process hit its address-space limit"
    except Exception as e:
        result["success"] = False
        result["error"] = f"{type(e).__name__}: {e}"
        result["stderr"] = traceback.format_exc()
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        result["stdout"] = stdout_buf.getvalue()[:500_000]
        extra_stderr = stderr_buf.getvalue()
        if extra_stderr and not result["stderr"]:
            result["stderr"] = extra_stderr[:500_000]

        try:
            conn.send(result)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass


class SubprocessBackend:
    """Fresh subprocess per call with rlimit + wall-clock timeout."""

    name = "subprocess"

    def execute(self, code: str, *, timeout: int, memory_bytes: int) -> SandboxResult:
        parent_conn, child_conn = _ctx.Pipe(duplex=False)
        proc = _ctx.Process(
            target=_child_main,
            args=(code, child_conn, memory_bytes, timeout + 5),
        )
        t0 = time.monotonic()
        proc.start()
        child_conn.close()  # parent keeps only the read end

        payload: dict | None = None
        try:
            if parent_conn.poll(timeout=timeout):
                try:
                    payload = parent_conn.recv()
                except (EOFError, ConnectionResetError):
                    payload = None
            proc.join(timeout=1.0)
        finally:
            if proc.is_alive():
                # Kill the whole process group (setsid was called in child)
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, AttributeError):
                    try:
                        proc.kill()
                    except Exception:
                        pass
                proc.join(timeout=2.0)
            try:
                parent_conn.close()
            except Exception:
                pass

        duration_ms = int((time.monotonic() - t0) * 1000)

        if payload is None:
            exit_code = proc.exitcode if hasattr(proc, "exitcode") else None
            return SandboxResult(
                success=False,
                error=(
                    f"sandbox subprocess terminated without result "
                    f"(exit code {exit_code})"
                ),
                backend=self.name,
                duration_ms=duration_ms,
                exit_code=exit_code,
            )

        return SandboxResult(
            stdout=payload.get("stdout", ""),
            stderr=payload.get("stderr", ""),
            error=payload.get("error"),
            figures=payload.get("figures", []),
            variables=payload.get("variables", {}),
            variable_types=payload.get("variable_types", {}),
            success=payload.get("success", False),
            backend=self.name,
            duration_ms=duration_ms,
            exit_code=proc.exitcode,
        )

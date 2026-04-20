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


def _child_breadcrumb(msg: str) -> None:
    """G0.3: emit breadcrumb to child stderr (Render logs) for diagnosing
    the 'empty response / tool response malformed' nine-consecutive-failure
    pattern.  No-op on failure so the child never crashes on its own
    diagnostic path.
    """
    try:
        os.write(2, f"[sandbox-child] {msg}\n".encode("utf-8"))
    except Exception:
        pass


# G0.3: hard payload ceiling.  Python multiprocessing.Pipe serialises the
# payload with pickle and queues it through an OS pipe.  Linux pipe buffers
# are small (64 KiB default), but larger payloads are transmitted via the
# send file descriptor — this works for several MB but can deadlock for
# tens of MB when the receive side isn't drained fast enough.  If the
# payload is obviously too big, fail fast with a clear message.
_SANDBOX_MAX_PAYLOAD_BYTES = 32 * 1024 * 1024  # 32 MB

# G0.3: per-variable repr cap — numpy arrays / pandas DataFrames can have
# unbounded repr().  5000 was the old cap but we still blew up when the
# whole `variables` dict was huge.  Keep 5000 per variable and also cap
# the whole variables dict at ~512 KB.
_VAR_REPR_MAX = 5000
_VARS_TOTAL_MAX = 512 * 1024


def _safe_var_repr(val) -> str | None:
    """Return a short, safe string representation of a variable value.

    For numpy ndarrays and pandas DataFrames (common large objects that
    crashed the sandbox on repr()), return a shape/dtype/summary string
    instead of the full content.
    """
    try:
        # numpy ndarray
        cls = type(val).__name__
        if cls == "ndarray":
            shape = getattr(val, "shape", None)
            dtype = getattr(val, "dtype", None)
            try:
                mean = float(val.mean()) if val.size else None
            except Exception:
                mean = None
            return f"<ndarray shape={shape} dtype={dtype} mean={mean}>"
        if cls in ("DataFrame", "Series"):
            shape = getattr(val, "shape", None)
            cols = getattr(val, "columns", None)
            col_list = list(cols)[:10] if cols is not None else None
            return f"<{cls} shape={shape} columns={col_list}>"
        r = repr(val)
    except Exception as e:
        return f"<repr failed: {type(e).__name__}>"
    if len(r) > _VAR_REPR_MAX:
        return r[:_VAR_REPR_MAX] + f"...[TRUNCATED: {len(r) - _VAR_REPR_MAX} chars dropped]"
    return r


def _child_main(code: str, conn, memory_bytes: int, cpu_seconds: int) -> None:
    """Entry point for the sandbox subprocess."""
    _child_set_limits(memory_bytes, cpu_seconds)
    _child_breadcrumb("start exec")

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
        _child_breadcrumb(f"exec done; fignums={plt.get_fignums()}")

        # Capture figures — G0.3: each figure guarded independently so one
        # OOM/corrupt figure doesn't wipe the whole result.
        for fig_num in plt.get_fignums():
            try:
                fig = plt.figure(fig_num)
                buf = BytesIO()
                fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
                png_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                # G0.3: per-figure size cap (8 MB raw PNG is absurd).
                if len(png_b64) > 8 * 1024 * 1024:
                    stderr_buf.write(
                        f"[figure {fig_num}: {len(png_b64)} b64 chars, dropping — too large for pipe]\n"
                    )
                    _child_breadcrumb(f"figure {fig_num} too large, skipped")
                    continue
                result["figures"].append(png_b64)
                _child_breadcrumb(f"figure {fig_num} serialized OK ({len(png_b64)} b64 chars)")
            except Exception as fig_err:  # pragma: no cover
                stderr_buf.write(f"[figure {fig_num} capture failed: {type(fig_err).__name__}: {fig_err}]\n")
                _child_breadcrumb(f"figure {fig_num} failed: {type(fig_err).__name__}")
        try:
            plt.close("all")
        except Exception:
            pass

        # Stringify new user variables (skip privates, modules, pre-existing)
        import inspect as _inspect
        vars_total_bytes = 0
        for name, val in exec_globals.items():
            if name.startswith("_") or name in pre_keys:
                continue
            try:
                if _inspect.ismodule(val):
                    continue
                r = _safe_var_repr(val)
                if r is None:
                    continue
                # G0.3: total-vars cap.  Stops when we hit 512 KB cumulative.
                if vars_total_bytes + len(r) > _VARS_TOTAL_MAX:
                    stderr_buf.write(
                        f"[variables truncated at {vars_total_bytes} bytes; "
                        f"stopped including from '{name}' onwards]\n"
                    )
                    _child_breadcrumb(f"variables truncated at {name}")
                    break
                result["variables"][name] = r
                result["variable_types"][name] = type(val).__name__
                vars_total_bytes += len(r)
            except Exception as e:
                stderr_buf.write(f"[variable '{name}' capture failed: {type(e).__name__}: {e}]\n")
                continue

    except SystemExit as e:
        result["success"] = False
        result["error"] = f"SystemExit: {e.code}"
        _child_breadcrumb(f"SystemExit: {e.code}")
    except MemoryError:
        result["success"] = False
        result["error"] = "MemoryError: process hit its address-space limit"
        _child_breadcrumb("MemoryError hit")
    except Exception as e:
        result["success"] = False
        result["error"] = f"{type(e).__name__}: {e}"
        result["stderr"] = traceback.format_exc()
        _child_breadcrumb(f"unhandled: {type(e).__name__}: {str(e)[:200]}")
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr

        # G0.3: stdout truncation with explicit marker (was silent before).
        raw_stdout = stdout_buf.getvalue()
        if len(raw_stdout) > 500_000:
            result["stdout"] = (
                raw_stdout[:500_000]
                + f"\n[TRUNCATED: {len(raw_stdout) - 500_000} bytes dropped]"
            )
            _child_breadcrumb(f"stdout truncated from {len(raw_stdout)} to 500000")
        else:
            result["stdout"] = raw_stdout

        extra_stderr = stderr_buf.getvalue()
        if extra_stderr and not result["stderr"]:
            if len(extra_stderr) > 500_000:
                result["stderr"] = extra_stderr[:500_000] + "\n[TRUNCATED]"
            else:
                result["stderr"] = extra_stderr

        # G0.3: pre-send size check.  If the pickled payload would exceed
        # _SANDBOX_MAX_PAYLOAD_BYTES, replace it with a concrete error
        # rather than risking a pipe deadlock / broken pipe on send.
        sent_ok = False
        try:
            import pickle
            pickled_size = len(pickle.dumps(result))
            _child_breadcrumb(
                f"about to send; pickled size={pickled_size} "
                f"stdout_len={len(result['stdout'])} "
                f"figures={len(result['figures'])} "
                f"vars={len(result['variables'])}"
            )
            if pickled_size > _SANDBOX_MAX_PAYLOAD_BYTES:
                # Replace with a minimal error payload
                result = {
                    "success": False,
                    "error": (
                        f"sandbox payload too large ({pickled_size} bytes, "
                        f"max {_SANDBOX_MAX_PAYLOAD_BYTES}). Truncate stdout / "
                        f"avoid storing large numpy arrays in module-level "
                        f"variables / reduce the number of figures."
                    ),
                    "stdout": result["stdout"][:100_000],
                    "stderr": result.get("stderr", "")[:10_000],
                    "figures": [],
                    "variables": {},
                    "variable_types": {},
                }
                _child_breadcrumb("payload too large, replaced with error")
        except Exception as size_err:
            _child_breadcrumb(f"pickle size check failed: {size_err}")
            # proceed to send anyway — if it fails conn.send will catch it

        try:
            conn.send(result)
            sent_ok = True
        except Exception as send_err:
            # F0.3 / G0.3: if conn.send fails (broken pipe, parent gone,
            # serialization error), we need the stderr of the child to carry
            # a breadcrumb.  Log via the child's own stderr (which Render
            # captures) since logger might not be configured in the spawn
            # context.
            _child_breadcrumb(
                f"conn.send failed: {type(send_err).__name__}: {send_err}"
            )
            # Try one more time with a minimal payload that must serialise
            try:
                conn.send({
                    "success": False,
                    "error": (
                        f"sandbox send failed: {type(send_err).__name__}: {send_err}. "
                        f"Original result had stdout_len={len(result.get('stdout', ''))} "
                        f"figures={len(result.get('figures', []))}."
                    ),
                    "stdout": "",
                    "stderr": "",
                    "figures": [],
                    "variables": {},
                    "variable_types": {},
                })
                sent_ok = True
                _child_breadcrumb("fallback minimal payload sent OK")
            except Exception as fallback_err:
                _child_breadcrumb(f"fallback send also failed: {fallback_err}")
        finally:
            _child_breadcrumb(
                f"exit; sent_ok={sent_ok} "
                f"success={result.get('success')} "
                f"error={str(result.get('error'))[:120]!r}"
            )
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
        exit_code = proc.exitcode if hasattr(proc, "exitcode") else None

        # G0.3: emit sandbox duration + outcome histogram for monitoring.
        # When the user reports "nine consecutive empty responses" we want
        # to see the actual distribution from Render.
        try:
            from app.observability.metrics import record_histogram
            record_histogram(
                "sandbox_duration_seconds",
                duration_ms / 1000.0,
                backend=self.name,
                exit_code=str(exit_code),
            )
        except Exception:
            pass

        # F0.1: payload-completeness guard.  `parent_conn.recv()` can deliver
        # a non-None but empty / non-dict / missing-keys payload when the
        # child died mid-serialization (OOM, SIGKILL, pipe truncation).
        # Previously we fell through to `payload.get(...)` which yielded
        # success=False, error=None and the downstream tool wrapper then
        # dropped the error field entirely, surfacing as "sandbox returned
        # no message" to the user.  Fail loudly instead.
        if payload is None:
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

        if not isinstance(payload, dict) or not payload:
            return SandboxResult(
                success=False,
                error=(
                    f"sandbox subprocess returned incomplete payload "
                    f"(type={type(payload).__name__}, "
                    f"keys={list(payload) if isinstance(payload, dict) else 'n/a'}, "
                    f"exit code {exit_code}).  The child likely crashed during "
                    f"result serialization (OOM / SIGKILL / broken pipe)."
                ),
                backend=self.name,
                duration_ms=duration_ms,
                exit_code=exit_code,
            )

        reported_success = bool(payload.get("success"))
        reported_error = payload.get("error")
        if not reported_success and not reported_error:
            reported_error = (
                f"sandbox reported failure without an error message "
                f"(payload keys={list(payload)}, exit code {exit_code}).  "
                f"This usually means the child died before populating the "
                f"error field (SIGKILL / OOM / fast exit)."
            )

        return SandboxResult(
            stdout=payload.get("stdout", "") or "",
            stderr=payload.get("stderr", "") or "",
            error=reported_error,
            figures=payload.get("figures", []) or [],
            variables=payload.get("variables", {}) or {},
            variable_types=payload.get("variable_types", {}) or {},
            success=reported_success,
            backend=self.name,
            duration_ms=duration_ms,
            exit_code=exit_code,
        )

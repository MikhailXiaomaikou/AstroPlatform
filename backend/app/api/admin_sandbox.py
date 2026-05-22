"""Phase P: Sandbox diagnostic endpoints — bypass the multiprocessing pipe
to capture Python interpreter crashes directly from stderr.

Round 6 confirmed: SandboxResult.stderr is always empty because the child
process crashes during Python startup / module import before `_child_main`
ever runs; the multiprocessing child inherits the parent uvicorn fd=2 →
goes into Render container logs, unreachable from the client layer.

`subprocess.Popen` is different: `stderr=PIPE` captures at the fork/exec fd
level, catching crashes whether Python hasn't finished initializing or an
import fails mid-way. This endpoint exists to diagnose R5-OPEN-A — click
once, read stderr, and immediately know if it's ModuleNotFound / libstdc++
mismatch / permission issue or something else.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from typing import Any

from fastapi import APIRouter, Depends

from app.api.auth import require_admin_any

router = APIRouter(prefix="/api/admin/sandbox", tags=["admin-sandbox"])
logger = logging.getLogger(__name__)


@router.get("/health")
async def sandbox_health(
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    """Run the simplest possible Python program via subprocess.Popen (not
    multiprocessing) and capture the full fd=2 stderr output.
    Success → ok=True + stdout "ok\\n"; failure → ok=False + real Python
    error info in stderr.
    """
    code = (
        "import sys\n"
        'print("ok")\n'
        'sys.stderr.write("stderr-baseline\\n")\n'
        "# 额外打印几个关键环境变量供诊断\n"
        'print("PYTHON_VERSION=" + sys.version.replace("\\n", " "))\n'
        'print("EXECUTABLE=" + sys.executable)\n'
    )
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=15,
        )
        duration_ms = int((time.time() - t0) * 1000)
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "duration_ms": duration_ms,
            "python_executable": sys.executable,
            "python_version": sys.version,
            "cwd": os.getcwd(),
            "note": (
                "This bypasses the multiprocessing-based sandbox. "
                "If this health check succeeds but run_python still crashes, "
                "the issue is inside the SubprocessBackend pipe setup, not "
                "the Python environment itself."
            ),
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "exit_code": None,
            "stdout": e.stdout or "",
            "stderr": (e.stderr or "") + "\n\n[TimeoutExpired after 15s]",
            "duration_ms": int((time.time() - t0) * 1000),
            "error": "timeout after 15s",
        }
    except Exception as e:
        return {
            "ok": False,
            "exit_code": None,
            "stdout": "",
            "stderr": f"{type(e).__name__}: {e}",
            "duration_ms": int((time.time() - t0) * 1000),
            "error": f"failed to spawn subprocess: {type(e).__name__}: {e}",
        }


@router.get("/repro-imports")
async def sandbox_repro_imports(
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    """Use Popen to run a child-equivalent Python that attempts to import
    app.services.sandbox.subprocess_backend + app.main.

    When a multiprocessing spawn child starts, it re-imports the main program
    and target module. If any module's top-level code crashes (missing env
    variable / DB connection init / import cycle), the child Python-exit(1)s
    **before** `_child_main` ever runs and its stderr goes to the parent's fd=2.
    This endpoint uses Popen to reproduce the same import chain with
    stderr=PIPE captured, pinpointing the exact crash location for R5-OPEN-A.
    """
    probe = (
        "import sys\n"
        "import traceback\n"
        "try:\n"
        "    import app.services.sandbox.subprocess_backend  # noqa\n"
        '    print("sandbox_backend_import_ok")\n'
        "except Exception:\n"
        '    sys.stderr.write("=== FAILED importing app.services.sandbox.subprocess_backend ===\\n")\n'
        "    traceback.print_exc(file=sys.stderr)\n"
        "    sys.exit(10)\n"
        "try:\n"
        "    from app.main import app  # noqa\n"
        '    print("app_main_import_ok")\n'
        "except Exception:\n"
        '    sys.stderr.write("=== FAILED importing app.main ===\\n")\n'
        "    traceback.print_exc(file=sys.stderr)\n"
        "    sys.exit(11)\n"
        "try:\n"
        "    import pickle, multiprocessing as mp\n"
        "    ctx = mp.get_context('spawn')\n"
        "    # 试 pickle _child_main (spawn 必须能 pickle 它)\n"
        "    from app.services.sandbox.subprocess_backend import _child_main\n"
        "    pickle.dumps(_child_main)\n"
        '    print("pickle_child_main_ok")\n'
        "except Exception:\n"
        '    sys.stderr.write("=== FAILED picklability check ===\\n")\n'
        "    traceback.print_exc(file=sys.stderr)\n"
        "    sys.exit(12)\n"
    )
    t0 = time.time()
    # Important: cwd must be backend/ so that `import app.*` resolves correctly.
    import pathlib as _pl
    backend_dir = _pl.Path(__file__).resolve().parent.parent.parent
    try:
        proc = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True, text=True, timeout=30,
            cwd=str(backend_dir),
            env={**os.environ},  # inherit the current environment
        )
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "duration_ms": int((time.time() - t0) * 1000),
            "cwd": str(backend_dir),
            "note": (
                "exit_code 10 = app.services.sandbox.subprocess_backend "
                "import 挂. exit 11 = app.main import 挂. exit 12 = "
                "spawn picklability 挂. 0 = 三步都 OK, 问题在 multiprocessing "
                "本身 (pipe / fd 继承 / signal handling)."
            ),
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "exit_code": None,
            "stdout": e.stdout or "",
            "stderr": (e.stderr or "") + "\n\n[TimeoutExpired after 30s — import hung]",
            "duration_ms": int((time.time() - t0) * 1000),
            "error": "import timeout",
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"failed to spawn probe: {type(e).__name__}: {e}",
        }


def _mp_staged_probe(conn, memory_bytes: int = 1024 * 1024 * 1024, cpu_seconds: int = 15) -> None:
    """Execute steps in the same order as _child_main, sending a checkpoint
    after each success. The parent loop recvs each step result; the first
    FAIL is the smoking gun. Must be a module-level function to be picklable
    by spawn.

    R7 fix synced: set BLAS to single thread + RLIMIT_NPROC 256, so the
    probe reflects behavior after the production fix.
    """
    import os as _os0
    _os0.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    _os0.environ.setdefault("OMP_NUM_THREADS", "1")
    _os0.environ.setdefault("MKL_NUM_THREADS", "1")
    _os0.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
    import sys
    import traceback as _tb

    def _send(step: int, name: str, ok: bool, err: str = "", tb: str = ""):
        try:
            conn.send({"step": step, "name": name, "ok": ok, "err": err, "tb": tb[-2000:]})
        except Exception:
            # Inside multiprocessing spawn child — parent logger handlers not
            # inherited, so log calls here would no-op. Pipe write failure is
            # itself the signal; parent observes EOF / no checkpoint arriving.
            pass

    def _run(step: int, name: str, fn):
        try:
            fn()
            _send(step, name, True)
            return True
        except BaseException as e:
            _send(step, name, False, f"{type(e).__name__}: {e}", _tb.format_exc())
            return False

    try:
        if not _run(1, "send-hello", lambda: None):
            return

        import os as _os
        import resource
        if not _run(2, "setrlimit_RLIMIT_AS", lambda: resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))):
            return
        if not _run(3, "setrlimit_RLIMIT_CPU", lambda: resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))):
            return
        if not _run(4, "setrlimit_RLIMIT_NPROC", lambda: resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))):
            return
        if not _run(5, "os.setsid", lambda: _os.setsid()):
            return

        from io import StringIO
        if not _run(6, "StringIO", lambda: StringIO()):
            return

        def _redir():
            sys.stdout = StringIO()
            sys.stderr = StringIO()
        if not _run(7, "redirect-stdio", _redir):
            return

        def _mpl_use():
            import matplotlib
            matplotlib.use("Agg")
        if not _run(8, "matplotlib.use(Agg)", _mpl_use):
            return

        def _mpl_fonts():
            import matplotlib
            matplotlib.rcParams["font.sans-serif"] = [
                "Noto Sans CJK SC", "Noto Sans CJK", "WenQuanYi Micro Hei",
                "PingFang SC", "Microsoft YaHei", "SimHei", "DejaVu Sans",
            ]
            matplotlib.rcParams["axes.unicode_minus"] = False
        if not _run(9, "mpl-fonts", _mpl_fonts):
            return

        if not _run(10, "import-pyplot", lambda: __import__("matplotlib.pyplot")):
            return
        if not _run(11, "import-numpy", lambda: __import__("numpy")):
            return

        _send(99, "all-steps-done", True)
    finally:
        try:
            conn.close()
        except Exception:
            # Same multiprocessing-child reason as _send above; parent observes
            # EOF on its end of the pipe regardless.
            pass


@router.get("/probe-steps")
async def sandbox_probe_steps(
    memory_mb: int = 1024,
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    """Execute the first 11 steps of _child_main inside a multiprocessing
    spawn child, one at a time. The first step that fails is the smoking gun.

    Query params:
      memory_mb=1024 (default 1 GB; try larger values like 4096 to check
      whether RLIMIT_AS is too small)
    """
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_mp_staged_probe,
        args=(child_conn,),
        kwargs={"memory_bytes": memory_mb * 1024 * 1024, "cpu_seconds": 30},
    )
    t0 = time.time()
    try:
        proc.start()
    except Exception as e:
        return {"ok": False, "stage": "proc.start", "error": f"{type(e).__name__}: {e}"}
    child_conn.close()

    checkpoints: list[dict] = []
    try:
        # Loop recv until the child closes the pipe or we hit the deadline.
        deadline = time.time() + 60
        while time.time() < deadline:
            if parent_conn.poll(timeout=2):
                try:
                    msg = parent_conn.recv()
                    checkpoints.append(msg)
                    if msg.get("step") == 99 or msg.get("ok") is False:
                        break
                except EOFError:
                    break
            else:
                if not proc.is_alive():
                    break
    finally:
        try:
            proc.join(timeout=3)
        except Exception as e:
            logger.debug("sandbox probe proc.join failed: %s", e)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)
        try:
            parent_conn.close()
        except Exception as e:
            logger.debug("sandbox probe parent_conn.close failed: %s", e)

    exit_code = proc.exitcode if hasattr(proc, "exitcode") else None
    duration_ms = int((time.time() - t0) * 1000)

    last_ok = next((c for c in reversed(checkpoints) if c.get("ok")), None)
    first_fail = next((c for c in checkpoints if c.get("ok") is False), None)

    return {
        "ok": first_fail is None and any(c.get("step") == 99 for c in checkpoints),
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "memory_mb": memory_mb,
        "checkpoints": checkpoints,
        "last_ok_step": last_ok.get("step") if last_ok else None,
        "first_fail_step": first_fail.get("step") if first_fail else None,
        "first_fail_name": first_fail.get("name") if first_fail else None,
        "first_fail_err": first_fail.get("err") if first_fail else None,
        "first_fail_tb": first_fail.get("tb") if first_fail else None,
        "note": (
            "checkpoints 逐个看. 第一个 ok=false 的 step 就是问题所在. "
            "如果没有 first_fail 但也没 step=99, 说明 child 崩在某个步骤"
            "但没来得及 send err (SIGKILL / SIGSEGV). 尝试调整 memory_mb "
            "query param 比如 ?memory_mb=4096 看 RLIMIT_AS 是否太小."
        ),
    }


def _mp_simple_probe(conn, probe_id: str) -> None:
    """Target for /repro-multiprocessing. Module-level to be picklable by
    spawn mode.  No rlimit, no setsid, no heavy imports — just send a
    dict back.  If this still fails, the issue is multiprocessing itself,
    not _child_main content."""
    import os as _os
    conn.send({
        "probe_id": probe_id,
        "ok": True,
        "child_pid": _os.getpid(),
    })
    conn.close()


@router.get("/repro-multiprocessing")
async def sandbox_repro_multiprocessing(
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    """Launch the simplest possible multiprocessing spawn child — no rlimit,
    no setsid, no heavy imports; just conn.send({'ok': True}).

    Compare with /exec-test (which goes through the production
    SubprocessBackend including rlimit + setsid + matplotlib + numpy +
    signal handlers):
    - This endpoint passes + /exec-test hangs → the problem is inside
      _child_main (RLIMIT_AS / setsid / matplotlib.use("Agg") / numpy
      init / ...)
    - This endpoint also hangs → multiprocessing spawn itself does not work
      in the Render container (insufficient pipe fds / clone() syscall blocked
      / unpickle bootstrap failure); must refactor to subprocess.Popen
    """
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_mp_simple_probe,
        args=(child_conn, "admin-probe"),
    )
    t0 = time.time()
    try:
        proc.start()
    except Exception as e:
        return {
            "ok": False,
            "stage": "proc.start",
            "error": f"{type(e).__name__}: {e}",
            "duration_ms": int((time.time() - t0) * 1000),
        }
    child_conn.close()  # parent holds read end

    payload: Any = None
    poll_ok = False
    recv_err: str | None = None
    try:
        if parent_conn.poll(timeout=15):
            poll_ok = True
            try:
                payload = parent_conn.recv()
            except (EOFError, ConnectionResetError) as e:
                recv_err = f"{type(e).__name__}: {e}"
    finally:
        try:
            proc.join(timeout=3)
        except Exception as e:
            logger.debug("sandbox probe proc.join failed: %s", e)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)
        try:
            parent_conn.close()
        except Exception as e:
            logger.debug("sandbox probe parent_conn.close failed: %s", e)

    exit_code = proc.exitcode if hasattr(proc, "exitcode") else None
    duration_ms = int((time.time() - t0) * 1000)

    return {
        "ok": bool(payload) and isinstance(payload, dict) and payload.get("ok") is True,
        "exit_code": exit_code,
        "poll_ok": poll_ok,
        "recv_err": recv_err,
        "payload_received": payload if isinstance(payload, (dict, str, type(None))) else str(payload),
        "duration_ms": duration_ms,
        "note": (
            "最简 multiprocessing spawn probe.  无 rlimit / setsid / "
            "heavy import.  本端点返 ok=true → spawn 本身可用, 问题在 "
            "SubprocessBackend._child_main 的某一步 (多半是 "
            "_child_set_limits 的 RLIMIT_AS / os.setsid, 或 matplotlib "
            "import).  返 ok=false + exit 非零 / poll_ok=false → spawn "
            "本身在 Render 容器上不通, 要换 subprocess.Popen 架构."
        ),
    }


@router.get("/exec-test")
async def sandbox_exec_test(
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    """Run `print("hello")` through the real SubprocessBackend.execute(),
    as a comparison against the /health endpoint (direct Popen).

    If /health passes but /exec-test fails → the problem is in the
    multiprocessing pipe / _child_main setup layer.
    If both fail and /health's stderr contains content → Python environment /
    dependency / permission issue; fix according to stderr.
    """
    from app.services.sandbox.subprocess_backend import SubprocessBackend
    from app.config import settings
    try:
        backend = SubprocessBackend()
        t0 = time.time()
        # Production run_python defaults to 1 GB. Give at least 512 MB here
        # to avoid false-positive numpy/matplotlib init crashes in the diagnostic endpoint
        # caused by a hardcoded 256 MB limit.
        memory_bytes = max(settings.sandbox_memory_bytes, 512 * 1024 * 1024)
        result = backend.execute(
            'print("hello from sandbox exec-test")',
            timeout=10,
            memory_bytes=memory_bytes,
        )
        return {
            "ok": result.success,
            "success": result.success,
            "exit_code": getattr(result, "exit_code", None),
            "stdout": getattr(result, "stdout", ""),
            "stderr": getattr(result, "stderr", ""),
            "error": getattr(result, "error", None),
            "duration_ms": int((time.time() - t0) * 1000),
            "backend": getattr(result, "backend", "unknown"),
            "memory_mb": memory_bytes // (1024 * 1024),
            "note": (
                "This uses the production multiprocessing-based "
                "SubprocessBackend. Compare against /sandbox/health "
                "(direct subprocess.Popen) to isolate where the crash is."
            ),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"Failed to invoke SubprocessBackend: {type(e).__name__}: {e}",
        }

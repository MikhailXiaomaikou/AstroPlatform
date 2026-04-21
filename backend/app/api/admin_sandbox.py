"""Phase P: Sandbox 诊断端点 — 绕开 multiprocessing pipe 直接抓 Python
解释器崩溃的 stderr.

Round 6 确认: SandboxResult.stderr 总是空, 因为子进程在 Python 启动 /
module import 阶段就挂了, child 的 `_child_main` 根本没跑到; multiprocessing
child 的 stderr 默认继承 parent uvicorn 的 fd=2 → 进 Render 容器日志,
client 层拿不到.

`subprocess.Popen` 不一样: `stderr=PIPE` 在 fork/exec 层 fd 级别捕获,
无论 Python 本身还没初始化完还是 import 阶段崩都能抓到.  这个端点就是
拿来诊断 R5-OPEN-A 的 — 点一次按钮看 stderr, 立刻知道是 ModuleNotFound /
libstdc++ mismatch / 权限问题还是别的.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any

from fastapi import APIRouter, Depends

from app.api.auth import require_admin_any

router = APIRouter(prefix="/api/admin/sandbox", tags=["admin-sandbox"])


@router.get("/health")
async def sandbox_health(
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    """用 subprocess.Popen (不是 multiprocessing) 跑最简 Python 程序,
    抓 fd=2 stderr 全部内容.  成功 → ok=True + stdout "ok\\n";
    失败 → ok=False + stderr 里有真实 Python 错误信息.
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
    """Popen 跑一个 child-equivalent Python: 尝试 import
    app.services.sandbox.subprocess_backend + app.main.

    multiprocessing spawn 的 child 启动时会 re-import 主 program + target
    module.  如果哪个 module 的 top-level code 崩 (environment variable
    缺失 / DB 连接初始化 / import cycle), child 在 `_child_main` 执行
    **之前** 就 Python-exit(1), 它的 stderr 去 parent 的 fd=2.  这个
    endpoint 用 Popen 复现同样的 import 链路 + stderr=PIPE 捕获, 能定
    位 R5-OPEN-A 的具体 crash 位置.
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
    # 重要: cwd 必须是 backend/ 以便 import app.*
    import pathlib as _pl
    backend_dir = _pl.Path(__file__).resolve().parent.parent.parent
    try:
        proc = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True, text=True, timeout=30,
            cwd=str(backend_dir),
            env={**os.environ},  # 继承当前环境
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
    """启动一个**最简** multiprocessing spawn child, 不 rlimit / 不
    setsid / 不 heavy import, 只是 conn.send({'ok': True}).

    对比 /exec-test (走生产 SubprocessBackend, 含 rlimit + setsid +
    import matplotlib + numpy + signals):
    - 本端点成 + /exec-test 挂 → 问题在 _child_main 内部一步
      (RLIMIT_AS / setsid / matplotlib.use("Agg") / numpy init / ...)
    - 本端点也挂 → multiprocessing spawn 机制本身在 Render 容器上不能
      用 (pipe fd 不足 / clone() syscall block / unpickle bootstrap
      fail), 必须 refactor 成 subprocess.Popen
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
        except Exception:
            pass
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)
        try:
            parent_conn.close()
        except Exception:
            pass

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
    """走真实 SubprocessBackend.execute() 跑 `print("hello")`, 对比上面
    /health 端点 (Popen direct).

    如果 /health 成功但这个 /exec-test 失败 → multiprocessing pipe /
    _child_main setup 层面的问题.
    如果两个都失败但 /health 的 stderr 有内容 → Python 环境 /
    依赖 / 权限问题, 按 stderr 指示修.
    """
    from app.services.sandbox.subprocess_backend import SubprocessBackend
    try:
        backend = SubprocessBackend()
        t0 = time.time()
        result = backend.execute(
            'print("hello from sandbox exec-test")',
            timeout=10,
            memory_bytes=256 * 1024 * 1024,  # 256 MB
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

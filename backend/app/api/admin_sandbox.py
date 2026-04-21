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

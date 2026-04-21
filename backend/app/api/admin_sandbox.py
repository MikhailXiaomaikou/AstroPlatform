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

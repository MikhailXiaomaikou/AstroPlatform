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


def _mp_staged_probe(conn, memory_bytes: int = 1024 * 1024 * 1024, cpu_seconds: int = 15) -> None:
    """按 _child_main 的实际顺序分步执行, 每步成功 send checkpoint.
    父进程循环 recv 拿到每步结果, 第一个 FAIL 就是 smoking gun.
    必须 module-level 才能被 spawn pickle.

    R7 fix 已同步: 设 BLAS 单 threads + RLIMIT_NPROC 256, 让 probe
    反映 production 修好后的行为.
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
            pass


@router.get("/probe-steps")
async def sandbox_probe_steps(
    memory_mb: int = 1024,
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    """分步在 multiprocessing spawn child 里执行 _child_main 前 11 步.
    第一个失败的 step 就是 smoking gun.

    Query params:
      memory_mb=1024 (默认 1GB, 可试更大比如 4096 看 RLIMIT_AS 是不是太小)
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
        # 循环 recv 直到 child 关闭 pipe 或超时
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

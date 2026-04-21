"""R2 回归测试: subprocess exit_code 与 success 字段语义一致.

Round 8 观察到 NameError → subprocess 正常 return → exit_code=0 + success=False
的 Unix 语义矛盾. 本测试锁定: 异常路径 exit_code 必须非 0.
"""

import os
import sys
import pytest

# 这些测试依赖真实 subprocess, CI 里如果没装子进程 sandbox 依赖就跳过
pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="subprocess sandbox 仅在 POSIX 系统测试"
)


@pytest.fixture
def backend():
    from app.services.sandbox.subprocess_backend import SubprocessBackend
    return SubprocessBackend()


def test_name_error_produces_nonzero_exit_code(backend):
    """Round 8 原 bug: NameError → exit_code=0. R2 修好后应为非 0."""
    result = backend.execute(
        "print(undefined_variable)",
        timeout=10,
        memory_bytes=256 * 1024 * 1024,
    )
    assert result.success is False
    assert result.exit_code not in (None, 0), (
        f"expected non-zero exit_code on NameError, got {result.exit_code}"
    )
    # error 或 stderr 至少有一个含有 NameError 线索
    combined = (result.error or "") + (result.stderr or "")
    assert "NameError" in combined or "undefined_variable" in combined


def test_successful_run_still_exits_zero(backend):
    """R2 不应误伤正常成功路径."""
    result = backend.execute(
        "print('hello world')",
        timeout=10,
        memory_bytes=256 * 1024 * 1024,
    )
    assert result.success is True
    assert result.exit_code == 0, (
        f"successful run should exit 0, got {result.exit_code}"
    )
    assert "hello world" in (result.stdout or "")


def test_explicit_sys_exit_triggers_nonzero(backend):
    """用户代码显式 sys.exit(N) 在当前 sandbox 走失败路径.

    Sandbox 内 `_child_main` 的 catch 覆盖了 SystemExit, 把它归为失败类,
    走 R2 的 exit 1. 这是可接受的 — 用户在 sandbox 里本来就不该调 sys.exit,
    该路径命中说明用户代码异常退出, 不应当视为 success.
    """
    result = backend.execute(
        "import sys; sys.exit(7)",
        timeout=10,
        memory_bytes=256 * 1024 * 1024,
    )
    assert result.success is False
    assert result.exit_code not in (None, 0)


def test_zero_division_also_triggers_nonzero_exit(backend):
    """另一类 runtime error: ZeroDivisionError."""
    result = backend.execute(
        "x = 1 / 0",
        timeout=10,
        memory_bytes=256 * 1024 * 1024,
    )
    assert result.success is False
    assert result.exit_code not in (None, 0)

"""R2 regression test: subprocess exit_code must be consistent with the success field semantics.

Round 8 observed that NameError → subprocess returning normally → exit_code=0 + success=False
contradicts Unix semantics. This test locks the invariant: error paths must produce a non-zero exit_code.
"""

import os
import pytest

# These tests depend on a real subprocess; skip in CI if the subprocess sandbox dependencies are not installed.
pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="subprocess sandbox is only tested on POSIX systems"
)

# Coverage injection + default science imports on GitHub Ubuntu/Python 3.11:
# 256 MB can sporadically trigger numpy/matplotlib C-init SIGSEGV. Production uses 1 GB;
# using 512 MB here (consistent with sandbox_isolation) to test exit_code semantics only.
SANDBOX_TEST_MEMORY = 512 * 1024 * 1024


@pytest.fixture
def backend():
    from app.services.sandbox.subprocess_backend import SubprocessBackend
    return SubprocessBackend()


def test_name_error_produces_nonzero_exit_code(backend):
    """Round 8 original bug: NameError → exit_code=0. After R2 fix it should be non-zero."""
    result = backend.execute(
        "print(undefined_variable)",
        timeout=10,
        memory_bytes=SANDBOX_TEST_MEMORY,
    )
    assert result.success is False
    assert result.exit_code not in (None, 0), (
        f"expected non-zero exit_code on NameError, got {result.exit_code}"
    )
    # at least one of error or stderr must contain a NameError hint
    combined = (result.error or "") + (result.stderr or "")
    assert "NameError" in combined or "undefined_variable" in combined


def test_successful_run_still_exits_zero(backend):
    """R2 must not break the normal success path."""
    result = backend.execute(
        "print('hello world')",
        timeout=10,
        memory_bytes=SANDBOX_TEST_MEMORY,
    )
    assert result.success is True
    assert result.exit_code == 0, (
        f"successful run should exit 0, got {result.exit_code}"
    )
    assert "hello world" in (result.stdout or "")


def test_explicit_sys_exit_triggers_nonzero(backend):
    """User code calling sys.exit(N) explicitly follows the failure path in the current sandbox.

    The `_child_main` catch inside the sandbox covers SystemExit and classifies it as a
    failure, taking R2's exit 1. This is acceptable — user code should not call sys.exit
    inside the sandbox; hitting this path means abnormal exit and must not be treated as success.
    """
    result = backend.execute(
        "import sys; sys.exit(7)",
        timeout=10,
        memory_bytes=SANDBOX_TEST_MEMORY,
    )
    assert result.success is False
    assert result.exit_code not in (None, 0)


def test_zero_division_also_triggers_nonzero_exit(backend):
    """Another category of runtime error: ZeroDivisionError."""
    result = backend.execute(
        "x = 1 / 0",
        timeout=10,
        memory_bytes=SANDBOX_TEST_MEMORY,
    )
    assert result.success is False
    assert result.exit_code not in (None, 0)

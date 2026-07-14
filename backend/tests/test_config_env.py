"""Regression tests for local .env compatibility."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent.parent


def _run_in_fresh_interpreter(
    workdir: Path,
    code: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(BACKEND_DIR),
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_full_env_example_copy_does_not_crash_settings(tmp_path: Path) -> None:
    """Runtime-only keys in the documented example are accepted."""

    example = (BACKEND_DIR / ".env.example").read_text(encoding="utf-8")
    (tmp_path / ".env").write_text(example, encoding="utf-8")

    result = _run_in_fresh_interpreter(
        tmp_path,
        (
            "import app.config\n"
            "assert app.config.settings.shared_deepseek_api_key_enabled is False\n"
            "print('BOOT_OK')\n"
        ),
    )

    assert result.returncode == 0, result.stderr
    assert "BOOT_OK" in result.stdout
    assert "extra_forbidden" not in result.stderr


def test_real_environment_wins_over_declared_dotenv_field(tmp_path: Path) -> None:
    """Hosted and launch-command variables retain precedence over .env."""

    (tmp_path / ".env").write_text(
        "SHARED_DEEPSEEK_API_KEY_ENABLED=0\n",
        encoding="utf-8",
    )

    result = _run_in_fresh_interpreter(
        tmp_path,
        (
            "import app.config\n"
            "assert app.config.settings.shared_deepseek_api_key_enabled is True\n"
            "print('REAL_ENV_WINS')\n"
        ),
        extra_env={"SHARED_DEEPSEEK_API_KEY_ENABLED": "1"},
    )

    assert result.returncode == 0, result.stderr
    assert "REAL_ENV_WINS" in result.stdout

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_WRAPPER = _REPO / "docs/demo/foundry-candidate/run-candidate-demo.sh"


def _wrapper_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    wrapper = repo / "docs/demo/foundry-candidate/run-candidate-demo.sh"
    wrapper.parent.mkdir(parents=True)
    (repo / "backend").mkdir()
    shutil.copy2(_WRAPPER, wrapper)
    wrapper.chmod(0o755)
    return repo, wrapper


def _environment(repo: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["STANDARD_ASTRO_REPO"] = str(repo)
    for key in ("PYTHON", "PYTHONHOME", "PYTHONPATH"):
        environment.pop(key, None)
    return environment


def _run(
    wrapper: Path,
    output: Path,
    *,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(wrapper), str(output)],
        cwd=wrapper.parents[3],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _write_probe(path: Path, marker: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\nprintf invoked > " + str(marker) + "\nexit 99\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_wrapper_never_falls_back_to_retired_venv_or_ambient_python(
    tmp_path: Path,
) -> None:
    repo, wrapper = _wrapper_fixture(tmp_path)
    output = tmp_path / "receipt-must-not-exist"
    retired_marker = tmp_path / "retired-python-invoked"
    ambient_marker = tmp_path / "ambient-python-invoked"
    _write_probe(repo / "backend/.venv/bin/python", retired_marker)
    fake_bin = tmp_path / "fake-bin"
    _write_probe(fake_bin / "python3", ambient_marker)
    environment = _environment(repo)
    environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]

    completed = _run(wrapper, output, environment=environment)

    assert completed.returncode == 78
    assert "replay_python_required" in completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr
    assert not retired_marker.exists()
    assert not ambient_marker.exists()
    assert not output.exists()


def test_wrapper_requires_python_to_be_an_explicit_executable_path(
    tmp_path: Path,
) -> None:
    repo, wrapper = _wrapper_fixture(tmp_path)
    output = tmp_path / "receipt-must-not-exist"
    environment = _environment(repo)
    environment["PYTHON"] = "python3"

    completed = _run(wrapper, output, environment=environment)

    assert completed.returncode == 78
    assert "replay_python_not_executable" in completed.stderr
    assert not output.exists()


def test_wrapper_rejects_an_existing_relative_python_path(tmp_path: Path) -> None:
    repo, wrapper = _wrapper_fixture(tmp_path)
    output = tmp_path / "receipt-must-not-exist"
    relative_python = repo / "relative-python"
    relative_python.symlink_to(sys.executable)
    environment = _environment(repo)
    environment["PYTHON"] = "./relative-python"

    completed = _run(wrapper, output, environment=environment)

    assert completed.returncode == 78
    assert "replay_python_not_executable" in completed.stderr
    assert not output.exists()


def test_wrapper_fails_before_receipt_when_explicit_environment_lacks_deps(
    tmp_path: Path,
) -> None:
    repo, wrapper = _wrapper_fixture(tmp_path)
    output = tmp_path / "receipt-must-not-exist"
    empty_venv = tmp_path / "empty-venv"
    created = subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(empty_venv)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert created.returncode == 0, created.stderr
    environment = _environment(repo)
    environment["PYTHON"] = str(empty_venv / "bin/python")

    completed = _run(wrapper, output, environment=environment)

    assert completed.returncode == 78
    assert "replay_dependency_missing:numpy" in completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr
    assert "Traceback" not in completed.stderr
    assert not output.exists()


def test_wrapper_binds_project_imports_to_selected_checkout(tmp_path: Path) -> None:
    repo, wrapper = _wrapper_fixture(tmp_path)
    output = tmp_path / "receipt-must-not-exist"
    environment = _environment(repo)
    environment["PYTHON"] = sys.executable

    completed = _run(wrapper, output, environment=environment)

    assert completed.returncode == 78
    assert (
        "replay_project_module_missing:app.services.foundry_source_tree"
        in completed.stderr
    )
    assert "ModuleNotFoundError" not in completed.stderr
    assert "Traceback" not in completed.stderr
    assert not output.exists()

"""Trust-boundary tests for the one-candidate Validation container."""

from __future__ import annotations

import runpy
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import foundry_demo_runner
from app.services.foundry_demo_runner import load_candidate_bundle, run_candidate_demo


_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _ROOT / ".github/workflows/foundry-candidate-demo.yml"
_DOCKERFILE = _ROOT / "backend/Dockerfile.foundry-demo"
_CLI = _ROOT / "backend/scripts/run_foundry_candidate_demo.py"
_CANDIDATE = (
    _ROOT
    / "backend/foundry_candidates/desi_dr2_official_chain_summary_v1.json"
)


def test_workflow_copies_artifacts_only_after_container_exit() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")

    assert 'test ! -e foundry-output' in text
    assert "foundry-output:/out:rw" not in text
    assert "--output /out/demo-report.json" not in text
    assert "--mount type=volume,destination=/trusted-output" in text
    assert "--network none" in text
    assert "--read-only" in text
    assert "--cap-drop ALL" in text
    assert "--cap-add SETUID" in text
    assert "--cap-add SETGID" in text
    assert "--cap-add KILL" in text
    assert "--security-opt no-new-privileges" in text

    create = text.index("docker create")
    start = text.index('docker start --attach "$container"')
    exited = text.index('test "$state" = "exited|false|0|0"')
    make_final = text.index("install -d -m 0700 foundry-output")
    copy = text.index('docker cp "$container:/trusted-output/." foundry-output/')
    remove = text.index('docker rm --volumes "$container"')
    assert create < start < exited < make_final < copy < remove


def test_image_uses_root_supervisor_and_explicit_candidate_identity() -> None:
    text = _DOCKERFILE.read_text(encoding="utf-8")

    assert "FOUNDRY_TRUSTED_SUPERVISOR=1" in text
    assert "FOUNDRY_CANDIDATE_UID=10002" in text
    assert "FOUNDRY_CANDIDATE_GID=10002" in text
    assert "--owner=root --group=root --mode=0700 /trusted-output" in text
    assert "USER root" in text
    assert "USER foundry" not in text


def test_runner_configures_only_the_candidate_subprocess_uid_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOUNDRY_CANDIDATE_UID", "10002")
    monkeypatch.setenv("FOUNDRY_CANDIDATE_GID", "10002")
    monkeypatch.setattr(foundry_demo_runner.os, "geteuid", lambda: 0)

    assert foundry_demo_runner._candidate_subprocess_identity() == {  # noqa: SLF001
        "user": 10002,
        "group": 10002,
        "extra_groups": (),
    }


def test_candidate_identity_check_requires_zero_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOUNDRY_CANDIDATE_UID", "10002")
    monkeypatch.setenv("FOUNDRY_CANDIDATE_GID", "10002")
    for name, value in (
        ("getuid", 10002),
        ("geteuid", 10002),
        ("getgid", 10002),
        ("getegid", 10002),
    ):
        monkeypatch.setattr(foundry_demo_runner.os, name, lambda value=value: value)
    monkeypatch.setattr(foundry_demo_runner.os, "getgroups", lambda: [])
    real_read_text = Path.read_text

    def zero_caps(path: Path, *args: object, **kwargs: object) -> str:
        if path == Path("/proc/self/status"):
            return "Name:\tpython\nCapEff:\t0000000000000000\n"
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", zero_caps)
    foundry_demo_runner._assert_candidate_subprocess_identity()  # noqa: SLF001

    def retained_caps(path: Path, *args: object, **kwargs: object) -> str:
        if path == Path("/proc/self/status"):
            return "Name:\tpython\nCapEff:\t0000000000000020\n"
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", retained_caps)
    with pytest.raises(
        foundry_demo_runner.FoundryDemoContractError,
        match="candidate_capability_drop_failed",
    ):
        foundry_demo_runner._assert_candidate_subprocess_identity()  # noqa: SLF001


def test_root_publisher_rechecks_actual_stream_bytes() -> None:
    namespace = runpy.run_path(str(_CLI))
    validate = namespace["_validate_trusted_result"]
    bundle = load_candidate_bundle(_CANDIDATE)
    streams: dict[str, bytes] = {}
    report = run_candidate_demo(bundle, captured_streams=streams)

    validate(report, streams)
    streams["stdout.log"] = b"Result is SUPPORTED\n"
    with pytest.raises(RuntimeError, match="stream_formal_escape"):
        validate(report, streams)


def test_trusted_output_root_must_be_empty_root_owned_0700_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    validate = runpy.run_path(str(_CLI))["_validate_trusted_output_root"]
    output = tmp_path / "trusted-output"
    output.mkdir(mode=0o700)
    real_lstat = Path.lstat

    def root_owned_lstat(path: Path) -> object:
        metadata = real_lstat(path)
        if path == output:
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_uid=0,
                st_gid=0,
            )
        return metadata

    monkeypatch.setattr(Path, "lstat", root_owned_lstat)

    assert validate(output) == output
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    (output / "preexisting").write_text("forbidden", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not_empty"):
        validate(output)

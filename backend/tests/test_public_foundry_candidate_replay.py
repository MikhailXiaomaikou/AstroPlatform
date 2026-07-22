from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.foundry_candidate_identity import (
    candidate_version_sha256,
    canonical_json,
)
from app.services.foundry_source_tree import (
    FoundrySourceTreeError,
    assert_clean_checkout,
    tracked_source_tree_hash,
)


_REPO = Path(__file__).resolve().parents[2]
_WRAPPER_RELATIVE = Path("docs/demo/foundry-candidate/run-candidate-demo.sh")
_HISTORICAL_VERSION = (
    "f4e8fa65deeb0b8662770fe436035596a89085ac33ef53cfb8e974d191268868"
)


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.fixture(scope="module")
def clean_replay_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    destination = tmp_path_factory.mktemp("public-foundry-replay") / "repo"
    destination.mkdir()
    listed = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=_REPO,
        check=True,
        capture_output=True,
    ).stdout
    for raw_relative in (item for item in listed.split(b"\0") if item):
        relative = Path(os.fsdecode(raw_relative))
        source = _REPO / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            target.symlink_to(os.readlink(source))
        else:
            shutil.copy2(source, target)

    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "public-replay-test@invalid.example"],
        ["git", "config", "user.name", "Public Replay Test"],
        ["git", "add", "."],
        ["git", "commit", "-qm", "public replay fixture"],
    ):
        subprocess.run(command, cwd=destination, check=True)
    return destination


def _replay_environment(repo: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHON"] = sys.executable
    environment["STANDARD_ASTRO_REPO"] = str(repo)
    environment.pop("DESI_DR2_OFFICIAL_CHAIN_ROOT", None)
    return environment


def _tiny_source_repo(path: Path) -> tuple[Path, Path]:
    path.mkdir()
    runtime = path / "runtime.py"
    runtime.write_text("VALUE = 'committed'\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "source-gate-test@invalid.example"],
        ["git", "config", "user.name", "Source Gate Test"],
        ["git", "add", "runtime.py"],
        ["git", "commit", "-qm", "source gate fixture"],
    ):
        subprocess.run(command, cwd=path, check=True)
    return path, runtime


def test_public_wrapper_replays_current_checkout_with_new_identity(
    clean_replay_repo: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "replay"
    completed = _run(
        [str(clean_replay_repo / _WRAPPER_RELATIVE), str(output)],
        cwd=clean_replay_repo,
        env=_replay_environment(clean_replay_repo),
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads((output / "demo-report.json").read_text(encoding="utf-8"))
    identity = json.loads(
        (output / "replay-identity.json").read_text(encoding="utf-8")
    )
    envelope = identity["candidate_version_envelope"]
    envelope_kwargs = dict(envelope)
    envelope_kwargs.pop("schema_version")
    source_tree_sha256, _manifest = tracked_source_tree_hash(clean_replay_repo)

    assert report["status"] == "PARTIAL"
    assert report["failure_class"] == "official_chain_mirror_unavailable"
    assert report["candidate_version_sha256"] != _HISTORICAL_VERSION
    assert report["candidate_version_sha256"] == candidate_version_sha256(
        **envelope_kwargs
    )
    expected_runner_digest = "sha256:" + hashlib.sha256(
        canonical_json(identity["runner_descriptor"])
    ).hexdigest()
    assert identity["runner_image_digest"] == expected_runner_digest
    assert report["runner_image_digest"] == expected_runner_digest
    assert report["candidate_bundle_sha256"] == envelope["candidate_bundle_sha256"]
    assert report["workflow_spec_sha256"] == envelope["workflow_spec_sha256"]
    assert envelope["code_tree_sha256"] == source_tree_sha256
    assert envelope["patch_sha256"] == hashlib.sha256(b"").hexdigest()
    policy_path = Path("backend/app/services/foundry_evidence_policy.py")
    assert identity["runner_descriptor"]["runtime_files"][policy_path.as_posix()] == (
        hashlib.sha256((clean_replay_repo / policy_path).read_bytes()).hexdigest()
    )
    assert identity["historical_demo_version_reused"] is False
    assert identity["ledger_recorded"] is False
    assert identity["runner_digest_kind"] == "LOCAL_DESCRIPTOR_SHA256"
    assert identity["environment_closure"] == "DESCRIPTOR_ONLY"
    assert identity["formal_registry_eligible"] is False
    assert (
        hashlib.sha256(
            json.dumps(
                report["environment"],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        == report["environment_sha256"]
    )


def test_public_wrapper_rejects_dirty_checkout_when_git_config_hides_untracked(
    clean_replay_repo: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "dirty-replay"
    marker = clean_replay_repo / "backend" / "untracked_runtime.py"
    subprocess.run(
        ["git", "config", "status.showUntrackedFiles", "no"],
        cwd=clean_replay_repo,
        check=True,
    )
    marker.write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
    try:
        completed = _run(
            [str(clean_replay_repo / _WRAPPER_RELATIVE), str(output)],
            cwd=clean_replay_repo,
            env=_replay_environment(clean_replay_repo),
        )
    finally:
        marker.unlink()
        subprocess.run(
            ["git", "config", "--unset", "status.showUntrackedFiles"],
            cwd=clean_replay_repo,
            check=True,
        )

    assert completed.returncode != 0
    assert "source_checkout_not_clean" in completed.stderr
    assert not output.exists()


def test_source_gate_ignores_external_git_worktree_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual, runtime = _tiny_source_repo(tmp_path / "actual")
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    shutil.copy2(runtime, decoy / runtime.name)
    runtime.write_text("VALUE = 'unreceipted runtime edit'\n", encoding="utf-8")

    monkeypatch.setenv("GIT_DIR", str(actual / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))

    with pytest.raises(
        FoundrySourceTreeError,
        match="source_worktree_mismatch",
    ):
        assert_clean_checkout(actual)


@pytest.mark.parametrize(
    "index_flag",
    ["--assume-unchanged", "--skip-worktree"],
)
def test_source_gate_compares_flagged_worktree_bytes_to_index(
    tmp_path: Path,
    index_flag: str,
) -> None:
    repo, runtime = _tiny_source_repo(tmp_path / "flagged")
    subprocess.run(
        ["git", "update-index", index_flag, runtime.name],
        cwd=repo,
        check=True,
    )
    runtime.write_text("VALUE = 'hidden runtime edit'\n", encoding="utf-8")

    with pytest.raises(
        FoundrySourceTreeError,
        match="source_worktree_mismatch",
    ):
        assert_clean_checkout(repo)


@pytest.mark.parametrize(
    "index_flag",
    ["--assume-unchanged", "--skip-worktree"],
)
def test_source_gate_rejects_unsafe_index_flags_without_content_change(
    tmp_path: Path,
    index_flag: str,
) -> None:
    repo, runtime = _tiny_source_repo(tmp_path / "flagged-clean")
    subprocess.run(
        ["git", "update-index", index_flag, runtime.name],
        cwd=repo,
        check=True,
    )

    with pytest.raises(
        FoundrySourceTreeError,
        match="source_index_flags_unsafe",
    ):
        assert_clean_checkout(repo)


@pytest.mark.skipif(os.name == "nt", reason="Git executable bits are POSIX-only")
def test_source_gate_compares_worktree_executable_mode(tmp_path: Path) -> None:
    repo, runtime = _tiny_source_repo(tmp_path / "mode-change")
    runtime.chmod(runtime.stat().st_mode | 0o111)

    with pytest.raises(
        FoundrySourceTreeError,
        match="source_worktree_mismatch",
    ):
        assert_clean_checkout(repo)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs extra privileges")
def test_source_gate_compares_symlink_target_bytes(tmp_path: Path) -> None:
    repo = tmp_path / "symlink-change"
    repo.mkdir()
    link = repo / "runtime-link"
    link.symlink_to("committed-target")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "source-gate-test@invalid.example"],
        ["git", "config", "user.name", "Source Gate Test"],
        ["git", "add", link.name],
        ["git", "commit", "-qm", "symlink fixture"],
    ):
        subprocess.run(command, cwd=repo, check=True)
    link.unlink()
    link.symlink_to("unreceipted-target")

    with pytest.raises(
        FoundrySourceTreeError,
        match="source_worktree_mismatch",
    ):
        assert_clean_checkout(repo)


def test_public_wrapper_fails_for_configured_invalid_mirror(
    clean_replay_repo: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "invalid-mirror-replay"
    mirror = tmp_path / "invalid-mirror"
    mirror.mkdir()
    environment = _replay_environment(clean_replay_repo)
    environment["DESI_DR2_OFFICIAL_CHAIN_ROOT"] = str(mirror)

    completed = _run(
        [str(clean_replay_repo / _WRAPPER_RELATIVE), str(output)],
        cwd=clean_replay_repo,
        env=environment,
    )

    assert completed.returncode != 0
    report = json.loads((output / "demo-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "FAILED"
    assert report["failure_class"] == "official_chain_mirror_integrity_failed"
    assert report["validation_summary"]["official_mirror_configured"] is True
    assert report["validation_summary"]["official_mirror_verified"] is False


def test_public_wrapper_propagates_python_startup_failure(
    clean_replay_repo: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "startup-failure"
    environment = _replay_environment(clean_replay_repo)
    environment["PYTHON"] = "/bin/false"

    completed = _run(
        [str(clean_replay_repo / _WRAPPER_RELATIVE), str(output)],
        cwd=clean_replay_repo,
        env=environment,
    )

    assert completed.returncode != 0
    assert not output.exists()


def test_public_wrapper_verifies_historical_receipt(
    clean_replay_repo: Path,
) -> None:
    completed = _run(
        [str(clean_replay_repo / _WRAPPER_RELATIVE), "--verify-recorded"],
        cwd=clean_replay_repo,
        env=_replay_environment(clean_replay_repo),
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["verified"] is True
    assert result["historical_provenance_complete"] is False
    assert result["candidate_version_sha256"] == _HISTORICAL_VERSION
    assert result["event_chain_head"] == (
        "46e13270462347d11414bcdf9e19c5be0fef1e465c30d47f47589fe2d7a751fe"
    )


def _standalone_verifier_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    kit = repo / "docs/demo/foundry-candidate"
    services = repo / "backend/app/services"
    kit.parent.mkdir(parents=True)
    services.mkdir(parents=True)
    shutil.copytree(_REPO / "docs/demo/foundry-candidate", kit)
    for relative in (
        Path("backend/app/__init__.py"),
        Path("backend/app/services/__init__.py"),
        Path("backend/app/services/foundry_candidate_identity.py"),
        Path("backend/app/services/foundry_evidence_policy.py"),
    ):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_REPO / relative, target)
    return repo, kit


def _rewrite_manifest_digest(kit: Path, relative: str) -> None:
    digest = hashlib.sha256((kit / relative).read_bytes()).hexdigest()
    lines = (kit / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    rewritten = [
        f"{digest}  {relative}" if line.endswith(f"  {relative}") else line
        for line in lines
    ]
    (kit / "SHA256SUMS").write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def _rewrite_report_receipt_links(
    kit: Path,
    report: dict[str, object],
) -> None:
    report.pop("demo_report_sha256", None)
    report_hash = hashlib.sha256(canonical_json(report)).hexdigest()
    report["demo_report_sha256"] = report_hash
    (kit / "demo-report.sanitized.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    events = json.loads((kit / "ledger-events.json").read_text(encoding="utf-8"))
    final_event = events["events"][-1]
    final_event["envelope"]["payload"]["demo_report_sha256"] = report_hash
    final_event_hash = hashlib.sha256(
        canonical_json(final_event["envelope"])
    ).hexdigest()
    final_event["event_hash"] = final_event_hash
    (kit / "ledger-events.json").write_text(
        json.dumps(events, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    ledger = json.loads(
        (kit / "ledger-summary.sanitized.json").read_text(encoding="utf-8")
    )
    ledger["demo"]["demo_report_sha256"] = report_hash
    ledger["event_chain"][-1]["event_hash"] = final_event_hash
    (kit / "ledger-summary.sanitized.json").write_text(
        json.dumps(ledger, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    for relative in (
        "demo-report.sanitized.json",
        "ledger-events.json",
        "ledger-summary.sanitized.json",
    ):
        _rewrite_manifest_digest(kit, relative)


def test_recorded_verifier_rejects_rehashed_event_tampering(tmp_path: Path) -> None:
    repo, kit = _standalone_verifier_fixture(tmp_path)
    events = json.loads((kit / "ledger-events.json").read_text(encoding="utf-8"))
    events["events"][-1]["envelope"]["payload"]["claim_eligible"] = True
    events["events"][-1]["event_hash"] = hashlib.sha256(
        canonical_json(events["events"][-1]["envelope"])
    ).hexdigest()
    (kit / "ledger-events.json").write_text(
        json.dumps(events, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _rewrite_manifest_digest(kit, "ledger-events.json")

    completed = _run(
        [
            sys.executable,
            str(_REPO / "backend/scripts/run_public_foundry_candidate_replay.py"),
            "verify-recorded",
            "--kit-dir",
            str(kit),
        ],
        cwd=repo,
    )

    assert completed.returncode != 0
    assert "recorded_final_event_report_mismatch" in completed.stderr


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [
        ("evidence_class", "FORMAL_EVIDENCE"),
        ("publication_ready", True),
        ("claim_eligible", True),
        ("evidence_pack_allowed", True),
    ],
)
def test_recorded_verifier_rejects_rehashed_report_policy_tampering(
    tmp_path: Path,
    field: str,
    tampered_value: object,
) -> None:
    repo, kit = _standalone_verifier_fixture(tmp_path)
    report = json.loads(
        (kit / "demo-report.sanitized.json").read_text(encoding="utf-8")
    )
    report[field] = tampered_value
    report.pop("demo_report_sha256")
    report_hash = hashlib.sha256(canonical_json(report)).hexdigest()
    report["demo_report_sha256"] = report_hash
    (kit / "demo-report.sanitized.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    events = json.loads((kit / "ledger-events.json").read_text(encoding="utf-8"))
    final_event = events["events"][-1]
    final_event["envelope"]["payload"]["demo_report_sha256"] = report_hash
    final_event["event_hash"] = hashlib.sha256(
        canonical_json(final_event["envelope"])
    ).hexdigest()
    (kit / "ledger-events.json").write_text(
        json.dumps(events, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    ledger = json.loads(
        (kit / "ledger-summary.sanitized.json").read_text(encoding="utf-8")
    )
    ledger["demo"]["demo_report_sha256"] = report_hash
    ledger["event_chain"][-1]["event_hash"] = final_event["event_hash"]
    (kit / "ledger-summary.sanitized.json").write_text(
        json.dumps(ledger, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    for relative in (
        "demo-report.sanitized.json",
        "ledger-events.json",
        "ledger-summary.sanitized.json",
    ):
        _rewrite_manifest_digest(kit, relative)

    completed = _run(
        [
            sys.executable,
            str(_REPO / "backend/scripts/run_public_foundry_candidate_replay.py"),
            "verify-recorded",
            "--kit-dir",
            str(kit),
        ],
        cwd=repo,
    )

    assert completed.returncode != 0
    assert "recorded_demo_report_scope_invalid" in completed.stderr


def test_recorded_verifier_rejects_rehashed_ledger_policy_mismatch(
    tmp_path: Path,
) -> None:
    repo, kit = _standalone_verifier_fixture(tmp_path)
    ledger = json.loads(
        (kit / "ledger-summary.sanitized.json").read_text(encoding="utf-8")
    )
    ledger["demo"]["claim_eligible"] = True
    (kit / "ledger-summary.sanitized.json").write_text(
        json.dumps(ledger, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _rewrite_manifest_digest(kit, "ledger-summary.sanitized.json")

    completed = _run(
        [
            sys.executable,
            str(_REPO / "backend/scripts/run_public_foundry_candidate_replay.py"),
            "verify-recorded",
            "--kit-dir",
            str(kit),
        ],
        cwd=repo,
    )

    assert completed.returncode != 0
    assert "recorded_demo_ledger_link_mismatch" in completed.stderr


@pytest.mark.parametrize(
    "formal_signal",
    [
        {"publication_ready": True},
        {"claim_eligible": True},
        {"scientific_verdict": "SUPPORTED"},
        {"evidence_pack_allowed": True},
        {"evidence_pack_id": "forged-pack"},
        {"evidence_class": "FORMAL_EVIDENCE"},
        {"evidence_class": "model_adequacy"},
        {"evidence_class": "A_READY"},
    ],
)
def test_recorded_verifier_rejects_rehashed_nested_formal_signal(
    tmp_path: Path,
    formal_signal: dict[str, object],
) -> None:
    repo, kit = _standalone_verifier_fixture(tmp_path)
    report = json.loads(
        (kit / "demo-report.sanitized.json").read_text(encoding="utf-8")
    )
    report["result"]["forged_nested_signal"] = formal_signal
    _rewrite_report_receipt_links(kit, report)

    completed = _run(
        [
            sys.executable,
            str(_REPO / "backend/scripts/run_public_foundry_candidate_replay.py"),
            "verify-recorded",
            "--kit-dir",
            str(kit),
        ],
        cwd=repo,
    )

    assert completed.returncode != 0
    assert "recorded_demo_report_scope_invalid" in completed.stderr


def test_recorded_verifier_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    repo, kit = _standalone_verifier_fixture(tmp_path)
    report_path = kit / "demo-report.sanitized.json"
    raw = report_path.read_text(encoding="utf-8")
    marker = '  "publication_ready": false,\n'
    assert marker in raw
    report_path.write_text(
        raw.replace(
            marker,
            '  "publication_ready": true,\n' + marker,
            1,
        ),
        encoding="utf-8",
    )
    _rewrite_manifest_digest(kit, "demo-report.sanitized.json")

    completed = _run(
        [
            sys.executable,
            str(_REPO / "backend/scripts/run_public_foundry_candidate_replay.py"),
            "verify-recorded",
            "--kit-dir",
            str(kit),
        ],
        cwd=repo,
    )

    assert completed.returncode != 0
    assert "recorded_json_duplicate_key:publication_ready" in completed.stderr


def test_recorded_verifier_rejects_manifest_with_missing_entry(
    tmp_path: Path,
) -> None:
    repo, kit = _standalone_verifier_fixture(tmp_path)
    lines = (kit / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    (kit / "SHA256SUMS").write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")

    completed = _run(
        [
            sys.executable,
            str(_REPO / "backend/scripts/run_public_foundry_candidate_replay.py"),
            "verify-recorded",
            "--kit-dir",
            str(kit),
        ],
        cwd=repo,
    )

    assert completed.returncode != 0
    assert "recorded_sha256sums_coverage_mismatch" in completed.stderr


def test_recorded_verifier_rejects_file_missing_from_manifest(
    tmp_path: Path,
) -> None:
    repo, kit = _standalone_verifier_fixture(tmp_path)
    (kit / "unmanifested.txt").write_text("not covered\n", encoding="utf-8")

    completed = _run(
        [
            sys.executable,
            str(_REPO / "backend/scripts/run_public_foundry_candidate_replay.py"),
            "verify-recorded",
            "--kit-dir",
            str(kit),
        ],
        cwd=repo,
    )

    assert completed.returncode != 0
    assert "recorded_sha256sums_coverage_mismatch" in completed.stderr


def test_recorded_verifier_rejects_rehashed_candidate_bundle_tampering(
    tmp_path: Path,
) -> None:
    repo, kit = _standalone_verifier_fixture(tmp_path)
    bundle = json.loads((kit / "candidate-bundle.json").read_text(encoding="utf-8"))
    bundle["workflow_spec"]["claim_scope"] = "tampered_scope"
    (kit / "candidate-bundle.json").write_text(
        json.dumps(bundle, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _rewrite_manifest_digest(kit, "candidate-bundle.json")

    completed = _run(
        [
            sys.executable,
            str(_REPO / "backend/scripts/run_public_foundry_candidate_replay.py"),
            "verify-recorded",
            "--kit-dir",
            str(kit),
        ],
        cwd=repo,
    )

    assert completed.returncode != 0
    assert "recorded_candidate_bundle_hash_mismatch" in completed.stderr


def test_recorded_verifier_rejects_rehashed_runner_descriptor_tampering(
    tmp_path: Path,
) -> None:
    repo, kit = _standalone_verifier_fixture(tmp_path)
    receipt = json.loads((kit / "runner-descriptor.json").read_text(encoding="utf-8"))
    receipt["descriptor"]["python"] = "0.0-tampered"
    (kit / "runner-descriptor.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _rewrite_manifest_digest(kit, "runner-descriptor.json")

    completed = _run(
        [
            sys.executable,
            str(_REPO / "backend/scripts/run_public_foundry_candidate_replay.py"),
            "verify-recorded",
            "--kit-dir",
            str(kit),
        ],
        cwd=repo,
    )

    assert completed.returncode != 0
    assert "recorded_runner_descriptor_hash_mismatch" in completed.stderr


def test_recorded_verifier_rejects_manifested_symlink(tmp_path: Path) -> None:
    repo, kit = _standalone_verifier_fixture(tmp_path)
    receipt = kit / "runner-descriptor.json"
    outside = repo / "runner-descriptor-copy.json"
    shutil.copy2(receipt, outside)
    receipt.unlink()
    receipt.symlink_to(outside)

    completed = _run(
        [
            sys.executable,
            str(_REPO / "backend/scripts/run_public_foundry_candidate_replay.py"),
            "verify-recorded",
            "--kit-dir",
            str(kit),
        ],
        cwd=repo,
    )

    assert completed.returncode != 0
    assert "recorded_sha256sums_symlink_forbidden" in completed.stderr

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
from app.services.foundry_source_tree import tracked_source_tree_hash


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

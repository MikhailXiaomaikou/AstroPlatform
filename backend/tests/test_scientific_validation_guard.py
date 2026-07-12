from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts import scientific_validation_guard as guard


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_junit_guard_accepts_complete_zero_skip_report(tmp_path):
    report = tmp_path / "pass.xml"
    report.write_text(
        '<testsuites><testsuite><testcase classname="science" name="passes" />'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )

    assert guard._assert_junit(report, minimum_tests=1) == 0


def test_junit_guard_rejects_silent_skip(tmp_path):
    report = tmp_path / "skip.xml"
    report.write_text(
        '<testsuites><testsuite><testcase classname="science" name="silently_skipped">'
        '<skipped message="optional dependency missing" />'
        "</testcase></testsuite></testsuites>",
        encoding="utf-8",
    )

    assert guard._assert_junit(report, minimum_tests=1) == 1


def test_manifest_parser_rejects_parent_traversal(tmp_path):
    manifest = tmp_path / "bad.sha256"
    manifest.write_text(f"{'0' * 64}  ../outside.dat\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe manifest path"):
        guard._read_manifest(manifest)


def test_sha256_streaming_matches_reference(tmp_path):
    data = b"released scientific data\n" * 100
    product = tmp_path / "product.dat"
    product.write_bytes(data)

    assert guard._sha256(product) == hashlib.sha256(data).hexdigest()


def test_scheduled_workflow_overrides_default_pytest_deselection() -> None:
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "scientific-validation.yml"
    ).read_text(encoding="utf-8")

    # backend/pytest.ini excludes slow/integration tests by default.  Both
    # scheduled suites must collect their explicit node list without inheriting
    # that marker filter; the JUnit guard then enforces zero skips/failures.
    assert workflow.count("-o addopts='' ") == 2

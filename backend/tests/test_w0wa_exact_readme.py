"""Regression checks for the revision-2 exact-environment operator contract."""

from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_README = (
    _REPO_ROOT / "backend" / "scripts" / "cobaya" / "README_full_cmb_reproduction.md"
)


def test_revision_2_readme_uses_fresh_fail_closed_environment_paths() -> None:
    text = _README.read_text(encoding="utf-8")

    for path in (
        "exact-venv-r2",
        "wheelhouse-r2",
        "primary-r2",
        "isolated-venv-r2",
        "isolated-r2",
    ):
        assert path in text

    for revision_1_path in (
        "w0wa-strict-a-readiness/exact-venv\n",
        "w0wa-strict-a-readiness/wheels\n",
        "w0wa-strict-a-readiness/isolated-venv\n",
        "cobaya_runs/w0wa_exact_formal --",
        "cobaya_runs/w0wa_exact_isolated --",
    ):
        assert revision_1_path not in text

    assert text.count('if [ -e "$path" ] || [ -L "$path" ]; then') == 2
    assert "never create, update, remove, or install into the\nrevision-1" in text


def test_revision_2_readme_verifies_then_installs_the_frozen_wheelhouse() -> None:
    text = _README.read_text(encoding="utf-8")
    verification = text.index("expected_manifest_hash")
    offline_install = text.index('"$EXACT_VENV/bin/pip" install --no-index')

    assert verification < offline_install
    assert (
        "e45ea8e098a3470622cd26cd7ed5061262859a09a6f84f93d97eaf49e56541bc"
        in text
    )
    assert "dependency lock hash does not match revision 2" in text
    assert "wheelhouse filenames do not exactly match the manifest" in text
    assert text.count('"$EXACT_VENV/bin/pip" check') == 1
    assert text.count('"$EXACT_ISOLATED_VENV/bin/pip" check') == 1
    assert text.count('--find-links "$EXACT_WHEELHOUSE"') == 2


def test_revision_2_readme_never_relies_on_legacy_receipt_defaults() -> None:
    text = _README.read_text(encoding="utf-8")

    assert text.count('--preflight-report "$EXACT_PRIMARY_PREFLIGHT"') == 4
    assert text.count('--generation-report "$EXACT_PRIMARY_GENERATION"') == 3
    assert '--output "$EXACT_PRIMARY_ANALYSIS"' in text
    assert '--output "$EXACT_PRIMARY_GRADE"' in text
    assert '--wheels-path "$EXACT_WHEELHOUSE"' in text
    assert "They do **not** produce\nthe hidden-answer or combined model-adequacy manifests" in text
    assert '--hidden-answer "$EXACT_HIDDEN_ANSWER"' in text

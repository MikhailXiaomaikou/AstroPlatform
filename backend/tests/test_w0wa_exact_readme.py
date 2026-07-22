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
        "exact-venv-r2-a003",
        "wheelhouse-r2-a003",
        "packages-r2-a003",
        "primary-r2-a003",
        "isolated-venv-r2-a003",
        "isolated-r2-a003",
    ):
        assert path in text

    for revision_1_path in (
        "w0wa-strict-a-readiness/exact-venv\n",
        "w0wa-strict-a-readiness/wheels\n",
        "w0wa-strict-a-readiness/isolated-venv\n",
        'cobaya-install" scripts/cobaya/w0wa_exact_install.yaml -p packages',
        "--packages-path packages",
        "cobaya_runs/w0wa_exact_formal --",
        "cobaya_runs/w0wa_exact_isolated --",
    ):
        assert revision_1_path not in text

    assert text.count('if [ -e "$path" ] || [ -L "$path" ]; then') == 2
    assert "Never create, update, remove,\nor install into revision-1 state" in text
    assert "initial Amendment-002 `r2` state" in text


def test_revision_2_readme_isolates_the_likelihood_data_closure() -> None:
    text = _README.read_text(encoding="utf-8")

    assert 'export EXACT_PACKAGES="$EXACT_R2_ROOT/packages-r2-a003"' in text
    assert (
        'for path in "$EXACT_VENV" "$EXACT_WHEELHOUSE" '
        '"$EXACT_PACKAGES" "$EXACT_PRIMARY"; do'
    ) in text
    assert (
        'cobaya-install" scripts/cobaya/w0wa_exact_install.yaml \\\n'
        '  -p "$EXACT_PACKAGES"'
    ) in text
    assert text.count('--packages-path "$EXACT_PACKAGES"') == 8
    assert "read-only revision-2 likelihood/data closure" in text
    assert "without trusting or modifying revision-1\n`backend/packages`" in text
    assert "Each environment independently re-hashes the tree" in text
    assert "--packages-path packages" not in text
    assert "w0wa_exact_formal_r2_a003" in text
    assert "w0wa_exact_isolated_r2_a003" in text
    assert "w0wa_exact_smoke_r2_a003" in text
    assert "--prefix cobaya_runs/w0wa_exact_formal_r2 \\\n" not in text
    assert "--chain-prefix cobaya_runs/w0wa_exact_isolated_r2 \\\n" not in text


def test_revision_2_readme_verifies_then_installs_the_frozen_wheelhouse() -> None:
    text = _README.read_text(encoding="utf-8")
    verification = text.index("expected_manifest_hash")
    offline_install = text.index('"$EXACT_VENV/bin/pip" install --no-index')

    assert verification < offline_install
    assert (
        "37c9926fae0ebb49e833f6ecfd51001a11a96470a5631dfae8d58fb09d3bcb36"
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
    assert "ebb2f8d8eef202dbe8a8a85b0cb753829f3899a2" in text
    assert "f9efb4ac6f7850d4c7739ac038d08beb37ea785e" not in text

"""The platform-chain CLI must reuse the production YAML path verbatim."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND_ROOT / "scripts" / "cobaya" / "produce_platform_chain.py"
PREREG = BACKEND_ROOT / "scripts" / "cobaya" / "platform_h0_prereg.json"


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=True,
    )


def test_generate_matches_the_production_yaml_builder(tmp_path):
    prereg = json.loads(PREREG.read_text())
    _run_cli("generate", "--output-dir", str(tmp_path), cwd=BACKEND_ROOT)
    run_dir = tmp_path / prereg["profile_id"]
    yaml_text = (run_dir / "platform_h0.input.yaml").read_text()

    # Rebuild through the production code path directly; any divergence means
    # the CLI stopped being "platform-exact".
    from app.services.cobaya_runner import _build_cobaya_yaml
    from app.services.cosmology_likelihoods.runners import (
        _cobaya_parameter_order,
        _sanitize_runner_priors,
        _validate_dataset_selection,
        _validate_model,
    )

    model_key = _validate_model(prereg["model"])
    entries = _validate_dataset_selection(model_key, list(prereg["dataset_keys"]))
    parameter_order = _cobaya_parameter_order(model_key, entries)
    expected = _build_cobaya_yaml(
        model_key=model_key,
        entries=entries,
        prior_bounds=_sanitize_runner_priors(parameter_order, None),
        parameter_order=parameter_order,
        sampler=prereg["sampler_commitments"]["sampler"],
        output_prefix=run_dir / "chain",
        seed=int(prereg["sampler_commitments"]["seed"]),
    )
    assert yaml_text == expected

    # Preregistered commitments must be present in the rendered config.
    commitments = prereg["sampler_commitments"]
    assert f"seed: {commitments['seed']}" in yaml_text
    assert f"Rminus1_stop: {commitments['Rminus1_stop']}" in yaml_text
    assert f"max_samples: {commitments['max_samples']}" in yaml_text
    for dataset_key in prereg["dataset_keys"]:
        assert dataset_key in json.dumps(prereg["dataset_keys"])
    for likelihood in (
        "planck_2018_highl_plik.TTTEEE_lite_native",
        "planck_2018_lowl.TT",
        "planck_2018_lowl.EE",
        "planck_2018_lensing.native",
    ):
        assert likelihood in yaml_text
    # lowl.EE is in the run, so tau must be sampled flat (no Gaussian pin).
    tau_block = yaml_text.split("  tau:\n", 1)[1].split("  A_planck:", 1)[0]
    assert "dist: norm" not in tau_block


def test_manifest_hashes_artifacts_and_pins_prereg(tmp_path):
    prereg = json.loads(PREREG.read_text())
    _run_cli("generate", "--output-dir", str(tmp_path), cwd=BACKEND_ROOT)
    run_dir = tmp_path / prereg["profile_id"]
    (run_dir / "chain.1.txt").write_text("# weight -logpost H0\n1 10.0 67.4\n")

    _run_cli("manifest", "--output-dir", str(tmp_path), cwd=BACKEND_ROOT)
    manifest = json.loads((run_dir / "production_manifest.json").read_text())

    assert manifest["profile_id"] == prereg["profile_id"]
    assert len(manifest["prereg_sha256"]) == 64
    names = {item["relative_path"] for item in manifest["artifacts"]}
    assert "platform_h0.input.yaml" in names
    assert "chain.1.txt" in names
    assert "production_manifest.json" not in names
    for item in manifest["artifacts"]:
        assert len(item["sha256"]) == 64
        assert item["size_bytes"] > 0
    packages = manifest["environment"]["packages"]
    assert "cobaya" in packages and "camb" in packages

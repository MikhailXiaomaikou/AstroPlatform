"""Fast synthetic tests for the offline canonical full-likelihood evidence path."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_DIR = _REPO_ROOT / "backend" / "scripts" / "cobaya"
_MODULE_PATH = _SCRIPT_DIR / "canonical_full_likelihood_evidence.py"
_SPEC = importlib.util.spec_from_file_location(
    "canonical_full_likelihood_evidence", _MODULE_PATH
)
assert _SPEC and _SPEC.loader
evidence = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = evidence
_SPEC.loader.exec_module(evidence)

CANONICAL = _SCRIPT_DIR / "w0wa_desi_sn_planck.yaml"
FREE_MAP = _SCRIPT_DIR / "w0wa_desi_sn_planck_map.yaml"
FIXED_MAP = _SCRIPT_DIR / "lcdm_desi_sn_planck_map.yaml"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write_yaml(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _prefix_file(prefix: Path, suffix: str) -> Path:
    return Path(f"{prefix}{suffix}")


def _write_table(path: Path, names: list[str], rows: np.ndarray) -> None:
    np.savetxt(path, rows, header=" ".join(names), comments="# ", fmt="%.12g")


def _synthetic_inventory(label: str = "canonical-test-v1") -> dict:
    groups = {
        "synthetic_canonical_stack": {
            "versions": [label],
            "files": [
                {
                    "path": "data/synthetic.dat",
                    "size_bytes": 12,
                    "sha256": evidence._hash_object(label),
                }
            ],
        }
    }
    return {
        "packages_path": "/synthetic/packages",
        "complete": True,
        "missing": [],
        "groups": groups,
        "fingerprint": evidence._hash_object(groups),
    }


def _chain_rows(
    rng: np.random.Generator, n: int, w_shift: float = 0.0
) -> tuple[list[str], np.ndarray]:
    names = [
        "weight",
        "minuslogpost",
        "ombh2",
        "omch2",
        "H0",
        "tau",
        "ns",
        "logA",
        "w",
        "wa",
        "omegam",
    ]
    rows = np.column_stack(
        [
            np.ones(n),
            rng.normal(6000.0, 3.0, n),
            rng.normal(0.0224, 0.0002, n),
            rng.normal(0.120, 0.002, n),
            rng.normal(68.0, 0.7, n),
            rng.normal(0.055, 0.006, n),
            rng.normal(0.965, 0.004, n),
            rng.normal(3.045, 0.01, n),
            rng.normal(-0.83 + w_shift, 0.06, n),
            rng.normal(-0.75, 0.27, n),
            rng.normal(0.308, 0.007, n),
        ]
    )
    return names, rows


def _minimum_table(chi2_total: float, *, fixed: bool) -> tuple[list[str], np.ndarray]:
    likelihoods = list(evidence.REQUIRED_LIKELIHOODS)
    components = [20.0, 20.0, 20.0, 15.0, 10.0, 15.0]
    components[-1] += chi2_total - sum(components)
    names = [
        "weight",
        "minuslogpost",
        "w",
        "wa",
        "chi2",
        *[f"chi2__{name}" for name in likelihoods],
    ]
    row = np.array(
        [
            1.0,
            chi2_total / 2.0,
            -1.0 if fixed else -0.83,
            0.0 if fixed else -0.75,
            chi2_total,
            *components,
        ]
    )
    return names, row.reshape(1, -1)


def _build_synthetic_run(
    tmp_path: Path,
    *,
    shifted_chain: bool = False,
    likelihood_mismatch: bool = False,
    data_mismatch: bool = False,
    failed_map: str | None = None,
) -> tuple[dict, dict]:
    canonical = _load_yaml(CANONICAL)
    free_config = _load_yaml(FREE_MAP)
    fixed_config = _load_yaml(FIXED_MAP)
    inventory = _synthetic_inventory()

    chain_prefix = tmp_path / "chain"
    _write_yaml(_prefix_file(chain_prefix, ".input.yaml"), canonical)
    _write_yaml(_prefix_file(chain_prefix, ".updated.yaml"), canonical)
    for index in range(1, 5):
        names, rows = _chain_rows(
            np.random.default_rng(1000 + index),
            1600,
            w_shift=1.2 if shifted_chain and index == 4 else 0.0,
        )
        _write_table(_prefix_file(chain_prefix, f".{index}.txt"), names, rows)
    evidence.write_completed_attestation(
        kind="chain",
        config_path=CANONICAL,
        prefix=chain_prefix,
        data_inventory=inventory,
    )

    free_prefix = tmp_path / "free_map"
    fixed_prefix = tmp_path / "fixed_map"
    _write_yaml(_prefix_file(free_prefix, ".minimize.input.yaml"), free_config)
    _write_yaml(_prefix_file(free_prefix, ".minimize.updated.yaml"), free_config)
    _write_yaml(_prefix_file(fixed_prefix, ".minimize.input.yaml"), fixed_config)
    fixed_updated = copy.deepcopy(fixed_config)
    if likelihood_mismatch:
        fixed_updated["likelihood"]["sn.pantheonplus"] = {
            "dataset_file": "different.dataset"
        }
    _write_yaml(_prefix_file(fixed_prefix, ".minimize.updated.yaml"), fixed_updated)
    free_names, free_rows = _minimum_table(100.0, fixed=False)
    fixed_names, fixed_rows = _minimum_table(106.0, fixed=True)
    _write_table(_prefix_file(free_prefix, ".minimum.txt"), free_names, free_rows)
    _write_table(_prefix_file(fixed_prefix, ".minimum.txt"), fixed_names, fixed_rows)

    free_inventory = (
        _synthetic_inventory("mismatched-data") if data_mismatch else inventory
    )
    evidence.write_completed_attestation(
        kind="map",
        config_path=FREE_MAP,
        prefix=free_prefix,
        data_inventory=free_inventory,
        returncode=1 if failed_map == "free" else 0,
    )
    evidence.write_completed_attestation(
        kind="map",
        config_path=FIXED_MAP,
        prefix=fixed_prefix,
        data_inventory=inventory,
        returncode=1 if failed_map == "fixed" else 0,
    )

    kwargs = {
        "canonical_config_path": CANONICAL,
        "chain_prefix": chain_prefix,
        "free_map_config_path": FREE_MAP,
        "fixed_map_config_path": FIXED_MAP,
        "free_map_prefix": free_prefix,
        "fixed_map_prefix": fixed_prefix,
        "packages_path": tmp_path / "unused-packages",
        "data_inventory": inventory,
    }
    return evidence.build_evidence_manifest(**kwargs), kwargs


def test_formal_config_is_strict_and_map_pair_is_generated_from_one_source():
    canonical = _load_yaml(CANONICAL)
    assert canonical["sampler"]["mcmc"]["Rminus1_stop"] == 0.01
    assert canonical["sampler"]["mcmc"]["Rminus1_cl_stop"] == 0.10
    assert evidence.validate_canonical_config(canonical) == []

    generated_free, generated_fixed = evidence.build_map_configs(canonical)
    committed_free = _load_yaml(FREE_MAP)
    committed_fixed = _load_yaml(FIXED_MAP)
    assert committed_free == generated_free
    assert committed_fixed == generated_fixed
    pair = evidence.validate_map_config_pair(committed_free, committed_fixed)
    assert pair["passed"] is True
    assert committed_fixed["params"]["w"] == -1.0
    assert committed_fixed["params"]["wa"] == 0.0
    assert committed_free["likelihood"] == committed_fixed["likelihood"]
    assert committed_free["sampler"]["minimize"]["ignore_prior"] is False


def test_complete_synthetic_evidence_emits_intervals_and_verified_map_delta(tmp_path):
    manifest, _ = _build_synthetic_run(tmp_path)
    assert manifest["publication_ready"] is True, manifest["failures"]
    diagnostics = manifest["posterior"]["diagnostics"]
    assert diagnostics["n_chains"] == 4
    assert all(
        item["rank_normalized_rhat"] < 1.01 and item["bulk_ess"] >= 400
        for item in diagnostics["parameters"].values()
    )
    assert set(manifest["posterior"]["intervals_68"]) == set(evidence.REPORT_PARAMETERS)
    assert manifest["map_comparison"]["delta_chi2"] == pytest.approx(6.0)
    assert manifest["map_comparison"][
        "delta_likelihood_chi2_at_optimized_points"
    ] == pytest.approx(6.0)
    assert manifest["map_comparison"][
        "delta_objective_at_optimized_points"
    ] == pytest.approx(6.0)
    assert manifest["map_comparison"]["likelihood_only_mle_proven"] is False
    assert manifest["map_comparison"]["significance_ready"] is False
    assert manifest["significance_ready"] is False
    assert manifest["claim_scope"] == (
        "posterior_intervals_and_descriptive_paired_optimizer_differences"
    )
    assert "p_value" not in manifest["map_comparison"]
    assert "equivalent_sigma" not in manifest["map_comparison"]
    assert manifest["map_comparison"]["significance_withheld_reason"] == (
        "paired_optimizers_target_posterior_not_likelihood_only_mle"
    )
    assert manifest["data"]["fingerprint"]
    assert manifest["environment"]["packages"]["cobaya"]
    assert manifest["manifest_sha256"].startswith("sha256:")


def test_shifted_chain_fails_rank_gate_and_suppresses_intervals(tmp_path):
    manifest, _ = _build_synthetic_run(tmp_path, shifted_chain=True)
    assert manifest["publication_ready"] is False
    assert manifest["posterior"]["passed"] is False
    assert "intervals_68" not in manifest["posterior"]
    w_diagnostic = manifest["posterior"]["diagnostics"]["parameters"]["w"]
    assert w_diagnostic["passed"] is False
    assert "rank_normalized_rhat_at_or_above_1.01" in w_diagnostic["failures"]


@pytest.mark.parametrize(
    ("kwargs", "expected_reason"),
    [
        (
            {"likelihood_mismatch": True},
            "map_execution_likelihood_fingerprint_mismatch",
        ),
        ({"data_mismatch": True}, "free_w0wa_optimization_not_verified"),
        ({"failed_map": "fixed"}, "fixed_lcdm_optimization_not_verified"),
    ],
)
def test_map_comparison_fails_closed_on_unmatched_or_unsuccessful_runs(
    tmp_path,
    kwargs,
    expected_reason,
):
    manifest, _ = _build_synthetic_run(tmp_path, **kwargs)
    comparison = manifest["map_comparison"]
    assert comparison["passed"] is False
    assert expected_reason in comparison["reasons"]
    assert "delta_chi2" not in comparison
    assert "equivalent_sigma" not in comparison
    # A valid posterior may still be reported, but the combined publication
    # certificate remains closed without a valid paired MAP comparison.
    assert manifest["posterior"]["passed"] is True
    assert manifest["publication_ready"] is False


def test_data_inventory_hashes_exact_files_and_marks_missing_patterns(tmp_path):
    packages = tmp_path / "packages"
    (packages / "data" / "toy").mkdir(parents=True)
    (packages / "data" / "toy" / "version.dat").write_text("v1\n", encoding="utf-8")
    (packages / "data" / "toy" / "vector.dat").write_bytes(b"1 2 3\n")
    complete = evidence.build_data_inventory(
        packages,
        asset_spec={"toy.like": ("data/toy/version.dat", "data/toy/vector.dat")},
    )
    assert complete["complete"] is True
    assert complete["groups"]["toy.like"]["versions"] == ["v1"]
    assert all(
        item["sha256"].startswith("sha256:")
        for item in complete["groups"]["toy.like"]["files"]
    )

    missing = evidence.build_data_inventory(
        packages,
        asset_spec={"toy.like": ("data/toy/missing.dat",)},
    )
    assert missing["complete"] is False
    assert missing["missing"] == ["toy.like:data/toy/missing.dat"]

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import numpy as np
import pytest

from app.services.agent_runtime.prompt_routing import (
    _cosmology_direct_route_from_prompt,
)
from app.services.ai_tools_cosmology import (
    COSMOLOGY_TOOL_NAMES,
    COSMOLOGY_TOOL_SCHEMAS,
    dispatch_cosmology,
)
from app.services.cosmology_likelihoods import (
    CosmologyAnalysisEntry,
    OfficialChainArtifact,
    audit_cosmology_analysis_registry,
    build_robustness_matrix,
    list_cosmology_analyses,
    run_dark_energy_evidence_matrix,
    run_robustness_matrix,
    summarize_official_analysis,
)
from app.services.cosmology_likelihoods import dark_energy_matrix
from app.services.cosmology_likelihoods import runners as likelihood_runners


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _valid_chain_text(*, rows: int = 300, header: str | None = None) -> str:
    columns = header or "# weight minuslogpost w wa H0 omegam"
    body = []
    for index in range(rows):
        phase = (index % 41) - 20
        body.append(
            f"1 {100 + index / 1000:.6f} "
            f"{-0.8 + phase / 1000:.6f} "
            f"{-0.6 - phase / 500:.6f} "
            f"{68.0 + phase / 100:.6f} "
            f"{0.30 + phase / 10000:.6f}"
        )
    return columns + "\n" + "\n".join(body) + "\n"


def _reference_summary_for_fixture(chains: list[str]) -> str:
    values = {name: [] for name in ("w", "wa", "H0", "omegam")}
    weights: list[float] = []
    try:
        for payload in chains:
            lines = payload.splitlines()
            columns = lines[0].lstrip("#").split()
            indices = {name: columns.index(name) for name in ("weight", *values)}
            for raw in lines[1:]:
                fields = raw.split()
                if not fields:
                    continue
                weights.append(float(fields[indices["weight"]]))
                for name in values:
                    values[name].append(float(fields[indices[name]]))
    except (IndexError, ValueError):
        # Malformed-chain tests must fail in the production parser before the
        # reference gate. A valid fallback keeps fixture construction separate.
        return _reference_summary_for_fixture([_valid_chain_text() for _ in range(4)])

    weight_array = np.asarray(weights, dtype=float)
    positions_order: dict[str, tuple[float, float, float]] = {}
    for name, raw_values in values.items():
        value_array = np.asarray(raw_values, dtype=float)
        order = np.argsort(value_array, kind="mergesort")
        ordered_values = value_array[order]
        ordered_weights = weight_array[order]
        positions = np.cumsum(ordered_weights) - 0.5 * ordered_weights
        positions /= float(np.sum(ordered_weights))
        mean = float(np.average(value_array, weights=weight_array))
        q16 = float(np.interp(0.16, positions, ordered_values))
        q84 = float(np.interp(0.84, positions, ordered_values))
        positions_order[name] = (mean, q16, q84)
    lines = ["parameter 68.0% 95.0%"]
    for name, (mean, q16, q84) in positions_order.items():
        lines.append(
            f"{name} {mean:.12g}^{{+{q84 - mean:.12g}}}_{{-{mean - q16:.12g}}} --"
        )
    return "\n".join(lines) + "\n"


def _fixture_entry(
    root: Path,
    *,
    chain_payloads: list[str] | None = None,
    checkpoint: str | None = None,
    reference_summary: str | None = None,
) -> CosmologyAnalysisEntry:
    prefix = "cobaya/base_w_wa/test-analysis"
    chains = chain_payloads or [_valid_chain_text() for _ in range(4)]
    payloads = {
        "chain.updated.yaml": "sampler: {mcmc: {}}\n",
        "chain.checkpoint": checkpoint
        or (
            "sampler:\n"
            "  mcmc:\n"
            "    converged: true\n"
            "    Rminus1_last: 0.008\n"
            "    burn_in: 0\n"
            "    mpi_size: 4\n"
        ),
        "chain.margestats": reference_summary
        or _reference_summary_for_fixture(chains),
        **{f"chain.{index}.txt": value for index, value in enumerate(chains, 1)},
    }
    artifacts = []
    for filename, payload in payloads.items():
        relative_path = f"{prefix}/{filename}"
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        role = (
            "chain"
            if filename.endswith(".txt")
            else "config"
            if filename.endswith(".yaml")
            else "reference_summary"
            if filename.endswith(".margestats")
            else "checkpoint"
        )
        artifacts.append(
            OfficialChainArtifact(
                role=role,
                relative_path=relative_path,
                sha256=_sha256(payload),
            )
        )
    return CosmologyAnalysisEntry(
        key="test_desi_dr2_w0wa",
        display_name="Test DESI DR2 w0wa",
        release="test",
        model="w0wa_cdm",
        supernova_selection="union3",
        official_model_folder="base_w_wa",
        official_sn_component="union3",
        data_components=("desi-bao-all", "union3", "test-cmb"),
        parameter_map=(
            ("w0", "w"),
            ("wa", "wa"),
            ("H0", "H0"),
            ("omegam", "omegam"),
        ),
        weight_column="weight",
        burn_in_rule="checkpoint burn_in=0",
        chain_format="Cobaya ASCII",
        evidence_tier="published_external",
        source_url="https://data.desi.lbl.gov/public/papers/y3/bao-cosmo-params/",
        paper_arxiv="2503.14738",
        paper_doi="10.1103/tr6y-kpc6",
        license_name="CC-BY-4.0",
        license_url="https://data.desi.lbl.gov/doc/acknowledgments/",
        overlap_groups=("desi_dr2_bao:all", "cmb:test"),
        artifacts=tuple(artifacts),
        notes="Test fixture only.",
    )


def test_official_analysis_registry_is_frozen_and_auditable() -> None:
    entries = list_cosmology_analyses()
    assert len(entries) == 3
    assert {entry.model for entry in entries} == {"w0wa_cdm"}
    assert {entry.supernova_selection for entry in entries} == {
        "pantheon_plus",
        "union3",
        "des_sn5yr",
    }
    assert audit_cosmology_analysis_registry() == []
    for entry in entries:
        assert entry.paper_arxiv == "2503.14738"
        assert entry.paper_doi == "10.1103/tr6y-kpc6"
        assert len([item for item in entry.artifacts if item.role == "chain"]) == 4
        assert len([item for item in entry.artifacts if item.role == "config"]) == 1
        assert len([item for item in entry.artifacts if item.role == "checkpoint"]) == 1
        assert len([item for item in entry.artifacts if item.role == "reference_summary"]) == 1
        assert all(len(item.sha256) == 64 for item in entry.artifacts)
    pantheon = next(
        entry for entry in entries if entry.supernova_selection == "pantheon_plus"
    )
    assert "uncalibrated" in pantheon.notes
    assert "SH0ES-calibrated" in pantheon.notes


def test_registry_audit_rejects_swapped_official_parameter_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = list_cosmology_analyses()[0]
    swapped = CosmologyAnalysisEntry(
        **{
            **entry.__dict__,
            "parameter_map": (
                ("w0", "wa"),
                ("wa", "w"),
                ("H0", "H0"),
                ("omegam", "omegam"),
            ),
        }
    )
    from app.services.cosmology_likelihoods import analysis_registry

    monkeypatch.setitem(analysis_registry._ANALYSIS_REGISTRY, entry.key, swapped)  # noqa: SLF001
    assert any(
        "invalid parameter map" in issue
        for issue in audit_cosmology_analysis_registry()
    )


def test_missing_official_mirror_withholds_all_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DESI_DR2_OFFICIAL_CHAIN_ROOT", raising=False)
    entry = list_cosmology_analyses()[0]
    result = summarize_official_analysis(entry)
    assert result["status"] == "WITHHELD"
    assert result["parameter_intervals"] == {}
    assert result["withheld_reasons"] == ["official_chain_cache_not_configured"]


def test_verified_local_mirror_produces_weighted_intervals(tmp_path: Path) -> None:
    entry = _fixture_entry(tmp_path)
    result = summarize_official_analysis(entry, cache_root=tmp_path)
    assert result["status"] == "READY"
    assert result["publication_ready"] is False
    assert set(result["parameter_intervals"]) == {"w0", "wa", "H0", "omegam"}
    assert result["diagnostics"]["rows_total"] == 1200
    assert result["diagnostics"]["kish_weight_ess"] == pytest.approx(1200.0)
    assert result["diagnostics"]["checkpoint"]["rminus1_last"] == 0.008
    acceptance = result["diagnostics"]["official_reference_acceptance"]
    assert acceptance["status"] == "PASSED"
    assert acceptance["center_max_sigma"] == 0.1
    assert acceptance["interval_width_max_fraction"] == 0.05
    assert result["diagnostics"]["column_order"] == [
        "weight",
        "minuslogpost",
        "w",
        "wa",
        "H0",
        "omegam",
    ]
    contours = result["two_dimensional_contours"]
    assert set(contours) == {"w0__wa", "omegam__H0"}
    assert contours["w0__wa"]["grid_bins_per_axis"] == 32
    assert contours["w0__wa"]["captured_weight_fraction"] > 0.95
    assert len(contours["w0__wa"]["probability_grid"]) == 32


def test_reference_center_or_width_mismatch_fails_closed(tmp_path: Path) -> None:
    wrong = "\n".join(
        [
            "parameter 68.0% 95.0%",
            "w -0.1\\pm 0.01 --",
            "wa -0.6\\pm 0.02 --",
            "H0 68.0\\pm 0.02 --",
            "omegam 0.3\\pm 0.0002 --",
            "",
        ]
    )
    entry = _fixture_entry(tmp_path, reference_summary=wrong)
    result = summarize_official_analysis(entry, cache_root=tmp_path)
    assert result["status"] == "WITHHELD"
    assert result["parameter_intervals"] == {}
    assert result["withheld_reasons"][0].startswith(
        "official_reference_center_mismatch:"
    )


def test_modified_official_chain_fails_closed(tmp_path: Path) -> None:
    entry = _fixture_entry(tmp_path)
    chain = next(item for item in entry.artifacts if item.role == "chain")
    (tmp_path / chain.relative_path).write_text("modified\n", encoding="utf-8")
    result = summarize_official_analysis(entry, cache_root=tmp_path)
    assert result["status"] == "WITHHELD"
    assert result["parameter_intervals"] == {}
    assert "checksum_mismatch" in result["withheld_reasons"][0]


@pytest.mark.parametrize("failure", ["negative_weight", "missing_parameter", "header_mismatch"])
def test_malformed_official_chains_fail_closed(tmp_path: Path, failure: str) -> None:
    chains = [_valid_chain_text() for _ in range(4)]
    if failure == "negative_weight":
        chains[0] = chains[0].replace("\n1 ", "\n-1 ", 1)
    elif failure == "missing_parameter":
        chains[0] = chains[0].replace(" wa", "", 1)
    else:
        chains[3] = chains[3].replace(
            "# weight minuslogpost w wa H0 omegam",
            "# minuslogpost weight w wa H0 omegam",
        )
    entry = _fixture_entry(tmp_path, chain_payloads=chains)
    result = summarize_official_analysis(entry, cache_root=tmp_path)
    assert result["status"] == "WITHHELD"
    assert result["parameter_intervals"] == {}
    assert result["withheld_reasons"]


def test_low_weight_ess_fails_closed(tmp_path: Path) -> None:
    chains = [_valid_chain_text() for _ in range(4)]
    chains[0] = chains[0].replace("\n1 ", "\n1000000000 ", 1)
    entry = _fixture_entry(tmp_path, chain_payloads=chains)
    result = summarize_official_analysis(entry, cache_root=tmp_path)
    assert result["status"] == "WITHHELD"
    assert result["parameter_intervals"] == {}
    assert result["withheld_reasons"] == ["official_chain_weight_ess_below_1000"]


def test_matrix_without_mirror_is_explicitly_withheld(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DESI_DR2_OFFICIAL_CHAIN_ROOT", raising=False)
    result = run_dark_energy_evidence_matrix(model="w0wa_cdm")
    assert result["official_ready_cells"] == 0
    assert result["official_withheld_cells"] == 3
    assert result["publication_ready"] is False
    assert result["__do_not_claim__"] is True
    assert all(cell["parameter_intervals"] == {} for cell in result["matrix"])


def test_tension_lab_never_assumes_shared_cells_are_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_summary(entry: CosmologyAnalysisEntry) -> dict:
        offset = {"pantheon_plus": 0.0, "union3": 0.05, "des_sn5yr": -0.03}[
            entry.supernova_selection
        ]
        return {
            "success": True,
            "status": "READY",
            "parameter_intervals": {
                "w0": {"mean": -0.8 + offset, "q16": -0.9, "q84": -0.7},
                "wa": {"mean": -0.6 + offset, "q16": -0.9, "q84": -0.3},
                "H0": {"mean": 68.0 + offset, "q16": 67.0, "q84": 69.0},
                "omegam": {"mean": 0.30 + offset / 10, "q16": 0.28, "q84": 0.32},
            },
            "diagnostics": {"checkpoint": {"converged": True}},
            "two_dimensional_contours": {
                "w0__wa": {"representation": "weighted_histogram2d_hpd_display"}
            },
            "withheld_reasons": [],
            "support_artifacts": [],
        }

    monkeypatch.setattr(dark_energy_matrix, "summarize_official_analysis", fake_summary)
    result = run_dark_energy_evidence_matrix(model="w0wa_cdm")
    comparisons = result["tension_lab"]["comparisons"]
    assert len(comparisons) == 12
    assert result["tension_lab"]["naive_independent_sigma_allowed"] is False
    assert all(row["status"] == "correlated_tension_withheld" for row in comparisons)
    assert all(row["tension_sigma"] is None for row in comparisons)
    assert all("desi_dr2_bao:all" in row["overlap_groups"] for row in comparisons)
    contour_comparisons = result["tension_lab"]["contour_comparisons"]
    assert len(contour_comparisons) == 3
    assert all(row["tension_sigma"] is None for row in contour_comparisons)


def test_dr1_references_are_separate_config_only_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DESI_DR2_OFFICIAL_CHAIN_ROOT", raising=False)
    result = run_dark_energy_evidence_matrix(
        model="w0wa_cdm",
        include_desi_dr1_reference=True,
    )
    assert result["matrix_size"] == 6
    for cell in result["matrix"]:
        keys = set(cell.get("dataset_keys") or [])
        assert not {"desi_dr1_bao", "desi_dr2_bao"}.issubset(keys)
    references = [
        cell for cell in result["matrix"] if cell["bao_dataset_key"] == "desi_dr1_bao"
    ]
    assert len(references) == 3
    assert all(cell["status"] == "CONFIG_ONLY" for cell in references)


@pytest.mark.parametrize("model", ["lcdm", "wcdm"])
def test_unregistered_official_model_is_not_substituted(model: str) -> None:
    result = run_dark_energy_evidence_matrix(model=model, supernova_sets=["union3"])
    cell = result["matrix"][0]
    assert cell["status"] == "WITHHELD"
    assert cell["parameter_intervals"] == {}
    assert cell["withheld_reasons"] == [
        "official_joint_chain_not_registered_for_model"
    ]


def test_existing_robustness_matrix_keeps_dr1_default_and_accepts_dr2() -> None:
    default = build_robustness_matrix(
        model="lcdm",
        supernova_sets=["union3"],
        include_h0_prior=False,
    )
    assert default["bao_dataset_key"] == "desi_dr1_bao"
    assert all(
        "desi_dr2_bao" not in cell["dataset_keys"] for cell in default["matrix"]
    )

    dr2 = build_robustness_matrix(
        model="lcdm",
        bao_dataset_key="desi_dr2_bao",
        supernova_sets=["union3"],
        include_h0_prior=False,
    )
    assert dr2["bao_dataset_key"] == "desi_dr2_bao"
    assert all(
        "desi_dr1_bao" not in cell["dataset_keys"] for cell in dr2["matrix"]
    )
    with pytest.raises(ValueError, match="bao_dataset_key"):
        build_robustness_matrix(model="lcdm", bao_dataset_key="desi_dr3_bao")


def test_robustness_runner_uses_only_selected_bao_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    def fake_run_likelihood_chain(**kwargs: object) -> dict:
        keys = [str(key) for key in kwargs["dataset_keys"]]  # type: ignore[index]
        seen.append(keys)
        return {
            "success": True,
            "publication_ready": False,
            "analysis_status": "CONFIG_ONLY",
            "execution_status": "not_run",
            "datasets_used": [],
            "datasets_not_run": [],
            "warnings": [],
        }

    monkeypatch.setattr(
        likelihood_runners,
        "run_likelihood_chain",
        fake_run_likelihood_chain,
    )
    result = run_robustness_matrix(
        model="lcdm",
        bao_dataset_key="desi_dr2_bao",
        supernova_sets=["union3"],
        include_h0_prior=False,
    )
    assert result["bao_dataset_key"] == "desi_dr2_bao"
    assert any("desi_dr2_bao" in keys for keys in seen)
    assert all("desi_dr1_bao" not in keys for keys in seen)


def test_tool_schema_dispatch_and_direct_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DESI_DR2_OFFICIAL_CHAIN_ROOT", raising=False)
    schema_names = {schema["name"] for schema in COSMOLOGY_TOOL_SCHEMAS}
    assert "run_dark_energy_evidence_matrix" in COSMOLOGY_TOOL_NAMES
    assert "run_dark_energy_evidence_matrix" in schema_names
    result = asyncio.run(
        dispatch_cosmology(
            "run_dark_energy_evidence_matrix",
            {"model": "w0wa_cdm", "supernova_sets": ["union3"]},
        )
    )
    assert result is not None
    assert result["official_withheld_cells"] == 1

    calls = _cosmology_direct_route_from_prompt(
        "Run a DESI DR2 evidence matrix for w0wa with Pantheon+, Union3, "
        "and DES-SN5YR; compare against a DR1 reference."
    )
    assert calls is not None and len(calls) == 1
    assert calls[0]["name"] == "run_dark_energy_evidence_matrix"
    assert calls[0]["input"] == {
        "model": "w0wa_cdm",
        "supernova_sets": ["pantheon_plus", "des_sn5yr", "union3"],
        "include_desi_dr1_reference": True,
    }

    default_model_calls = _cosmology_direct_route_from_prompt(
        "Run the DESI DR2 evidence matrix with Pantheon+, Union3, and DES-SN5YR."
    )
    assert default_model_calls is not None
    assert default_model_calls[0]["input"]["model"] == "w0wa_cdm"

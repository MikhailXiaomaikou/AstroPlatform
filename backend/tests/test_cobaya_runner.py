"""PART AI Phase 5 #2 Track 2 step 2 — cobaya_runner unit tests.

Locks:
1. is_external_enabled gate: env-flag truthy AND cobaya importable.
2. dispatch returns the step-3-pending NOT_PUB_READY shape until the
   adapter resolver lands.
3. YAML generation contains the registered cobaya_likelihood, prior
   bounds, sampler block, and (when set) packages_path.
4. subprocess success path produces a publication-ready envelope; chain
   parsing tolerates a positional column layout.
5. subprocess failure modes (timeout, non-zero exit, missing executable,
   parse failure) round-trip into a publication-blocking envelope with a
   stable error_class.
6. run_likelihood_chain default off path is byte-identical to legacy.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from app.services import cobaya_runner
from app.services.cobaya_runner import (
    CobayaConfigError,
    CobayaLikelihoodTranslationPending,
    CobayaParseError,
    CobayaRunError,
    CobayaSubprocessFailure,
    CobayaSubprocessTimeout,
    _build_cobaya_yaml,
    _parse_chain_files,
    _runner_success,
    _summarise_samples,
    dispatch_external_cobaya,
    is_external_enabled,
)
from app.services.cosmology_likelihoods import (
    _validate_dataset_selection,
    get_cosmology_dataset,
    run_likelihood_chain,
)


# ---------------------------------------------------------------------------
# 1. is_external_enabled gate
# ---------------------------------------------------------------------------


def test_is_external_enabled_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXTERNAL_COBAYA_ENABLED", raising=False)
    assert is_external_enabled() is False


def test_is_external_enabled_falsy_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    for value in ["false", "0", "no", "", "anything-else"]:
        monkeypatch.setenv("EXTERNAL_COBAYA_ENABLED", value)
        assert is_external_enabled() is False, value


def test_is_external_enabled_truthy_but_cobaya_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXTERNAL_COBAYA_ENABLED", "true")
    with patch("app.services.cobaya_runner._cobaya_import_ok", return_value=False):
        assert is_external_enabled() is False


def test_is_external_enabled_truthy_with_cobaya_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXTERNAL_COBAYA_ENABLED", "true")
    with patch("app.services.cobaya_runner._cobaya_import_ok", return_value=True):
        assert is_external_enabled() is True
    monkeypatch.setenv("EXTERNAL_COBAYA_ENABLED", "1")
    with patch("app.services.cobaya_runner._cobaya_import_ok", return_value=True):
        assert is_external_enabled() is True


# ---------------------------------------------------------------------------
# 2. dispatch returns step-3-pending NOT_PUB_READY
# ---------------------------------------------------------------------------


def test_dispatch_returns_translation_pending_until_step3() -> None:
    entries = _validate_dataset_selection("lcdm", ["spt3g_cmb"])
    out = dispatch_external_cobaya(
        model_key="lcdm",
        entries=entries,
        prior_bounds={"H0": (50.0, 90.0), "omegam": (0.05, 0.6), "rd": (130.0, 170.0)},
        parameter_order=["H0", "omegam", "rd"],
        seed=20260502,
        sample_count=4000,
    )
    assert out["publication_ready"] is False
    assert out["__do_not_claim__"] is True
    assert out["error_class"] == CobayaLikelihoodTranslationPending.error_class
    assert "translation" in out["__message_to_model__"].lower()
    assert out["dataset_keys"] == ["spt3g_cmb"]
    assert out["datasets_used"] == []
    runner_meta = out["provenance"]["cosmology_likelihood"]
    assert runner_meta["runner"] == "cobaya:not_run"
    assert runner_meta["error_class"] == CobayaLikelihoodTranslationPending.error_class


def test_dispatch_with_no_entries_returns_failure() -> None:
    out = dispatch_external_cobaya(
        model_key="lcdm",
        entries=[],
        prior_bounds={},
        parameter_order=[],
        seed=1,
        sample_count=10,
    )
    assert out["publication_ready"] is False
    assert out["error_class"] == "no_dataset_entries"


# ---------------------------------------------------------------------------
# 3. YAML generation
# ---------------------------------------------------------------------------


def test_build_cobaya_yaml_has_required_sections(tmp_path: Path) -> None:
    entries = _validate_dataset_selection("lcdm", ["spt3g_cmb"])
    yaml = _build_cobaya_yaml(
        model_key="lcdm",
        entries=entries,
        prior_bounds={"H0": (60.0, 80.0), "omegam": (0.1, 0.5)},
        parameter_order=["H0", "omegam"],
        sampler="evaluate",
        output_prefix=tmp_path / "chain",
        seed=12345,
    )
    # Must contain registered cobaya_likelihood adapter name (step 2 emits
    # the original string; step 3 will translate it).
    assert "external:cmb.spt3g_2018" in yaml
    assert "theory:" in yaml and "camb:" in yaml
    assert "params:" in yaml
    assert "H0:" in yaml and "omegam:" in yaml
    assert "min: 60.0" in yaml and "max: 80.0" in yaml
    assert "sampler:" in yaml and "evaluate:" in yaml
    assert "output:" in yaml


def test_build_cobaya_yaml_w0_is_dropped_and_aliased_to_w(tmp_path: Path) -> None:
    # cobaya/CAMB name the CPL pair (w, wa) — sampling "w0" directly dies at
    # model build ("Could not find anything to use input parameter(s) {'w0'}").
    # The builder must emit drop: true on w0 plus a dynamic `w` alias, keeping
    # the platform's "w0" name in the chain columns.
    entries = _validate_dataset_selection("w0wa_cdm", ["spt3g_cmb"])
    yaml = _build_cobaya_yaml(
        model_key="w0wa_cdm",
        entries=entries,
        prior_bounds={"H0": (60.0, 80.0), "w0": (-2.0, -0.3), "wa": (-3.0, 2.0)},
        parameter_order=["H0", "w0", "wa"],
        sampler="evaluate",
        output_prefix=tmp_path / "chain",
        seed=12345,
    )
    w0_block = yaml.split("  w0:\n", 1)[1].split("\n  wa:", 1)[0]
    assert "drop: true" in w0_block
    assert 'value: "lambda w0: w0"' in yaml
    assert "derived: false" in yaml
    # wcdm samples plain "w" — no drop/alias machinery there.
    yaml_wcdm = _build_cobaya_yaml(
        model_key="wcdm",
        entries=entries,
        prior_bounds={"H0": (60.0, 80.0), "w": (-2.0, -0.3)},
        parameter_order=["H0", "w"],
        sampler="evaluate",
        output_prefix=tmp_path / "chain",
        seed=12345,
    )
    assert "drop: true" not in yaml_wcdm
    assert "lambda" not in yaml_wcdm


def test_build_cobaya_yaml_packages_path_when_env_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COBAYA_PACKAGES_PATH", "/app/cobaya_packages")
    entries = _validate_dataset_selection("lcdm", ["spt3g_cmb"])
    yaml = _build_cobaya_yaml(
        model_key="lcdm",
        entries=entries,
        prior_bounds={"H0": (60.0, 80.0)},
        parameter_order=["H0"],
        sampler="evaluate",
        output_prefix=tmp_path / "chain",
        seed=12345,
    )
    assert "packages_path: /app/cobaya_packages" in yaml


def test_build_cobaya_yaml_unsupported_sampler_raises(tmp_path: Path) -> None:
    entries = _validate_dataset_selection("lcdm", ["spt3g_cmb"])
    with pytest.raises(CobayaConfigError):
        _build_cobaya_yaml(
            model_key="lcdm",
            entries=entries,
            prior_bounds={"H0": (60.0, 80.0)},
            parameter_order=["H0"],
            sampler="nuts",  # unsupported
            output_prefix=tmp_path / "chain",
            seed=12345,
        )


def test_build_cobaya_yaml_mcmc_sampler_includes_rminus1(tmp_path: Path) -> None:
    entries = _validate_dataset_selection("lcdm", ["spt3g_cmb"])
    yaml = _build_cobaya_yaml(
        model_key="lcdm",
        entries=entries,
        prior_bounds={"H0": (60.0, 80.0)},
        parameter_order=["H0"],
        sampler="mcmc",
        output_prefix=tmp_path / "chain",
        seed=12345,
    )
    assert "mcmc:" in yaml and "Rminus1_stop" in yaml
    # M12: the sampler seed must be written into the YAML so the run is
    # reproducible (the provenance envelope stamps this exact value).
    assert "seed: 12345" in yaml


# ---------------------------------------------------------------------------
# 4. Chain parsing
# ---------------------------------------------------------------------------


def test_parse_chain_files_positional_layout(tmp_path: Path) -> None:
    """cobaya chain row layout: weight, -logpost, -logprior, params..."""
    output_prefix = tmp_path / "chain"
    rng = np.random.default_rng(20260502)
    n_draws = 800
    samples_h0 = rng.normal(67.4, 1.0, n_draws)
    samples_om = rng.normal(0.315, 0.01, n_draws)

    for chain_id in (1, 2):
        rows = np.column_stack(
            [
                np.ones(n_draws),  # weight
                np.zeros(n_draws),  # -logpost
                np.zeros(n_draws),  # -logprior
                samples_h0 + chain_id * 0.01,
                samples_om + chain_id * 1e-4,
            ]
        )
        np.savetxt(output_prefix.parent / f"chain.{chain_id}.txt", rows)

    samples_per_chain, meta = _parse_chain_files(
        output_prefix=output_prefix,
        parameter_order=["H0", "omegam"],
    )
    assert meta["n_chains"] == 2
    assert meta["n_draws_total"] == 1600
    assert all(s.shape == (n_draws, 2) for s in samples_per_chain)


def test_parse_chain_files_no_files_raises(tmp_path: Path) -> None:
    with pytest.raises(CobayaParseError):
        _parse_chain_files(
            output_prefix=tmp_path / "missing",
            parameter_order=["H0"],
        )


def test_parse_chain_files_truncated_columns_raises(tmp_path: Path) -> None:
    output_prefix = tmp_path / "chain"
    np.savetxt(
        output_prefix.parent / "chain.1.txt",
        np.array([[1.0, 0.0, 0.0]]),  # missing param columns
    )
    with pytest.raises(CobayaParseError):
        _parse_chain_files(
            output_prefix=output_prefix,
            parameter_order=["H0", "omegam"],
        )


# ---------------------------------------------------------------------------
# 5. Summaries / diagnostics
# ---------------------------------------------------------------------------


def test_summarise_samples_returns_q16_q84() -> None:
    rng = np.random.default_rng(42)
    samples = [rng.normal(67.4, 1.0, (1000, 2)) for _ in range(2)]
    summaries, diag = _summarise_samples(
        samples_per_chain=samples,
        parameter_order=["H0", "omegam"],
    )
    assert "H0" in summaries and "omegam" in summaries
    h0 = summaries["H0"]
    assert h0["q16"] < h0["median"] < h0["q84"]
    # arviz may or may not be available; either way diagnostics dict is populated
    assert diag.get("n_chains") == 2


def test_low_e_tau_gaussian_standin_is_preliminary_even_with_good_chains() -> None:
    names = ("ombh2", "omch2", "H0", "ns", "As", "tau", "A_planck")
    diagnostics = {
        "overall_status": "ok",
        "n_chains": 4,
        "n_independent_chains": 4,
        "per_parameter": {
            name: {"rhat": 1.001, "ess_bulk": 1200.0} for name in names
        },
    }
    verified = {"hash_verified": True, "files_sha256": {}, "mismatches": []}
    summaries = {name: {"median": 1.0} for name in names}

    result = _runner_success(
        model_key="lcdm",
        entries=[get_cosmology_dataset("planck_2018_highl_TTTEEE_lite")],
        seed=7,
        sampler="mcmc",
        summaries=summaries,
        diagnostics=diagnostics,
        chain_meta={},
        stdout_tail="",
        data_verification=verified,
    )

    assert result["publication_ready"] is False
    assert result["preliminary_ready"] is True
    assert result["chain_tier"] == "exploratory"
    assert "compressed_or_approximate_likelihood" in result[
        "preliminary_reasons"
    ]
    assert result["approximate_likelihood_components"] == [
        "planck_lowE_tau_gaussian_standin"
    ]
    assert result["provenance"]["cosmology_likelihood"]["tau_constraint"] == {
        "mode": "compressed_lowE_gaussian_standin",
        "publication_eligible": False,
    }


def test_real_verified_low_e_likelihood_removes_tau_approximation_block() -> None:
    names = ("ombh2", "omch2", "H0", "ns", "As", "tau", "A_planck")
    diagnostics = {
        "overall_status": "ok",
        "n_chains": 4,
        "n_independent_chains": 4,
        "per_parameter": {
            name: {"rhat": 1.001, "ess_bulk": 1200.0} for name in names
        },
    }
    selected_but_unverified = {
        "hash_verified": True,
        "files_sha256": {},
        "mismatches": [],
    }

    unverified_result = _runner_success(
        model_key="lcdm",
        entries=[
            get_cosmology_dataset("planck_2018_highl_TTTEEE_lite"),
            get_cosmology_dataset("planck_2018_lowl_EE"),
        ],
        seed=7,
        sampler="mcmc",
        summaries={name: {"median": 1.0} for name in names},
        diagnostics=diagnostics,
        chain_meta={},
        stdout_tail="",
        data_verification=selected_but_unverified,
    )

    assert unverified_result["publication_ready"] is False
    assert unverified_result["approximate_likelihood_components"] == []
    assert "tau_constraining_likelihood_unverified" in unverified_result[
        "preliminary_reasons"
    ]
    assert unverified_result["provenance"]["cosmology_likelihood"][
        "tau_constraint"
    ] == {
        "mode": "selected_lowE_likelihood_unverified",
        "publication_eligible": False,
    }

    verified = {
        "hash_verified": True,
        "verified_dataset_keys": [
            "planck_2018_highl_TTTEEE_lite",
            "planck_2018_lowl_EE",
        ],
        "files_sha256": {},
        "mismatches": [],
    }

    result = _runner_success(
        model_key="lcdm",
        entries=[
            get_cosmology_dataset("planck_2018_highl_TTTEEE_lite"),
            get_cosmology_dataset("planck_2018_lowl_EE"),
        ],
        seed=7,
        sampler="mcmc",
        summaries={name: {"median": 1.0} for name in names},
        diagnostics=diagnostics,
        chain_meta={},
        stdout_tail="",
        data_verification=verified,
    )

    # Verified lowE removes the data-substitution block and clears the
    # numerical-reproducibility stage.  It still cannot become publication-
    # ready until predictive checks, prior/systematics sensitivity, simulation
    # recovery, and independent reproduction are attested separately.
    assert result["publication_gate"]["numerical_eligible"] is True
    assert result["publication_ready"] is False
    assert "model_adequacy_attestation_missing" in result["preliminary_reasons"]
    assert result["approximate_likelihood_components"] == []
    assert "compressed_or_approximate_likelihood" not in result[
        "preliminary_reasons"
    ]
    assert result["provenance"]["cosmology_likelihood"]["tau_constraint"] == {
        "mode": "verified_lowE_likelihood_or_tau_not_sampled",
        "publication_eligible": True,
    }


# ---------------------------------------------------------------------------
# 6. run_likelihood_chain dispatch — default off, on path, mocked subprocess
# ---------------------------------------------------------------------------


def test_run_likelihood_chain_default_off_does_not_invoke_cobaya(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EXTERNAL_COBAYA_ENABLED", raising=False)
    out = run_likelihood_chain(model="lcdm", dataset_keys=["spt3g_cmb"])
    # Legacy path → compressed_gaussian_analytic runner
    assert (
        out["provenance"]["cosmology_likelihood"]["runner"]
        == "compressed_gaussian_analytic"
    )


def test_run_likelihood_chain_dispatches_to_cobaya_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXTERNAL_COBAYA_ENABLED", "true")
    with patch(
        "app.services.cobaya_runner._cobaya_import_ok", return_value=True
    ), patch(
        "app.services.cobaya_runner.dispatch_external_cobaya",
        return_value={
            "success": True,
            "publication_ready": False,
            "error_class": "test_sentinel",
            "provenance": {"cosmology_likelihood": {"runner": "cobaya:not_run"}},
        },
    ) as mock_dispatch:
        out = run_likelihood_chain(model="lcdm", dataset_keys=["spt3g_cmb"])
    assert mock_dispatch.called
    assert out["error_class"] == "test_sentinel"
    assert out["provenance"]["cosmology_likelihood"]["runner"] == "cobaya:not_run"


def test_run_likelihood_chain_mixed_selection_keeps_legacy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed external_cobaya + compressed entries should NOT cobaya-dispatch.

    Otherwise the compressed entries would silently lose their summary;
    legacy path handles them and lists external_cobaya entries as
    datasets_not_run.
    """
    monkeypatch.setenv("EXTERNAL_COBAYA_ENABLED", "true")
    with patch("app.services.cobaya_runner._cobaya_import_ok", return_value=True):
        # Pure external_cobaya selection → cobaya dispatch
        with patch(
            "app.services.cobaya_runner.dispatch_external_cobaya",
            return_value={"runner_called": True},
        ) as cobaya_dispatch:
            run_likelihood_chain(model="lcdm", dataset_keys=["spt3g_cmb"])
        assert cobaya_dispatch.called
        # Mixed selection — desi_dr1_bao is BAO-direct executable, must take
        # precedence over the cobaya path (legacy bao sampling path runs).
        with patch(
            "app.services.cobaya_runner.dispatch_external_cobaya"
        ) as cobaya_dispatch:
            run_likelihood_chain(
                model="lcdm", dataset_keys=["desi_dr1_bao", "spt3g_cmb"]
            )
        assert not cobaya_dispatch.called


# ---------------------------------------------------------------------------
# 7. Subprocess error mapping (uses CobayaRunError chain)
# ---------------------------------------------------------------------------


def test_runner_failure_envelope_carries_error_class() -> None:
    entries = _validate_dataset_selection("lcdm", ["spt3g_cmb"])
    failure = cobaya_runner._runner_failure(  # noqa: SLF001 — internal access for test coverage
        model_key="lcdm",
        entries=entries,
        seed=1,
        error_class="cobaya_subprocess_timeout",
        message="cobaya-run timed out after 180 s",
    )
    assert failure["error_class"] == "cobaya_subprocess_timeout"
    assert failure["publication_ready"] is False
    assert failure["__tool_status__"] == "PARTIAL"
    assert failure["analysis_status"] == "EXTERNAL_COBAYA_NOT_RUN"
    assert "180 s" in failure["__message_to_model__"]


def test_cobaya_run_error_subclasses_carry_error_class() -> None:
    assert CobayaSubprocessTimeout("x").error_class == "cobaya_subprocess_timeout"
    assert (
        CobayaSubprocessFailure("x").error_class == "cobaya_subprocess_nonzero_exit"
    )
    assert CobayaParseError("x").error_class == "cobaya_chain_parse_failed"
    assert CobayaConfigError("x").error_class == "cobaya_config_invalid"
    assert (
        CobayaLikelihoodTranslationPending("x").error_class
        == "cobaya_likelihood_id_translation_pending"
    )


# ---------------------------------------------------------------------------
# 8. fit_statistics on the external envelope (backlog P3b, 2026-07-07)
#
# Gap: _runner_success returned chain_tier but NO fit_statistics, so an
# external-cobaya chain could never enter model-comparison pairing —
# research_program.py gates on isinstance(result.get("fit_statistics"), dict)
# and compute_model_comparison differences fit_statistics["chi2"/"aic"].
# Models reachable only via the external CMB path (ok_lcdm, lcdm_mnu, ...)
# therefore had NO model comparison at all.
#
# The statistics must come from the run's real products: cobaya 3.6.2 writes
# a "#"-prefixed header naming every column in each chain .txt, including the
# total "chi2" column (verified == -2 ln L against a live toy-likelihood
# cobaya run on 2026-07-07). chi2 here is the minimum of that column over
# posterior draws (weight > 0), mirroring the in-process runner's
# min-over-samples semantics. BIC / n_constraints are honestly absent: the
# chain products do not record the likelihood data-vector length N.
# ---------------------------------------------------------------------------


_FIXTURE_PARAM_STATS = {
    # center, scale for the deterministic fixture chains
    "H0": (67.4, 0.5),
    "omegam": (0.315, 0.008),
    "omegak": (0.001, 0.002),
}


def _write_cobaya_format_chain(
    path: Path,
    param_names: list[str],
    rows: np.ndarray,
    *,
    with_header: bool = True,
) -> None:
    """Write a chain .txt in the exact cobaya 3.6.2 layout (verified against a
    live `python -m cobaya run`): '#'-prefixed header naming every column,
    then whitespace-separated rows: weight, minuslogpost, params...,
    minuslogprior, minuslogprior__0, chi2, chi2__<like>."""
    columns = [
        "weight",
        "minuslogpost",
        *param_names,
        "minuslogprior",
        "minuslogprior__0",
        "chi2",
        "chi2__spt3g",
    ]
    assert rows.shape[1] == len(columns)
    with open(path, "w", encoding="utf-8") as fh:
        if with_header:
            fh.write("#" + " ".join(f"{c:>17s}" for c in columns)[1:] + "\n")
        np.savetxt(fh, rows)


def _fixture_rows(
    param_names: list[str],
    planted_min_chi2: float | None,
    *,
    n_draws: int,
    seed: int,
) -> np.ndarray:
    """Deterministic draws; chi2 column uniform in [20, 40] with an optional
    planted exact minimum at row 0, so the expected best-fit chi2 is known by
    construction (hand-computable, not re-derived from the code under test)."""
    rng = np.random.default_rng(seed)
    params = np.column_stack(
        [
            rng.normal(*_FIXTURE_PARAM_STATS[name], size=n_draws)
            for name in param_names
        ]
    )
    chi2 = rng.uniform(20.0, 40.0, n_draws)
    if planted_min_chi2 is not None:
        chi2[0] = planted_min_chi2
    weight = np.ones(n_draws)
    minuslogpost = 0.5 * chi2 + 2.0
    minuslogprior = np.full(n_draws, 2.0)
    return np.column_stack(
        [weight, minuslogpost, params, minuslogprior, minuslogprior, chi2, chi2]
    )


def test_parse_chain_files_best_chi2_hand_computed(tmp_path: Path) -> None:
    """best_chi2 = min of the real chi2 column over weight>0 rows, across all
    chains; a weight-0 row (cobaya's discarded burn-in seed point) must NOT
    win the minimum even when its chi2 is smaller."""
    params = ["H0", "omegam"]
    chain1 = np.array(
        [
            # weight, -logpost, H0, omegam, -logprior, -logprior__0, chi2, chi2__spt3g
            [0.0, 3.5, 67.0, 0.310, 2.0, 2.0, 3.0, 3.0],  # weight-0 seed point
            [2.0, 8.25, 67.4, 0.315, 2.0, 2.0, 12.5, 12.5],
            [1.0, 9.0, 68.0, 0.320, 2.0, 2.0, 14.0, 14.0],
        ]
    )
    chain2 = np.array(
        [
            [1.0, 7.875, 67.2, 0.313, 2.0, 2.0, 11.75, 11.75],
            [3.0, 8.625, 67.9, 0.318, 2.0, 2.0, 13.25, 13.25],
        ]
    )
    _write_cobaya_format_chain(tmp_path / "chain.1.txt", params, chain1)
    _write_cobaya_format_chain(tmp_path / "chain.2.txt", params, chain2)

    _, meta = _parse_chain_files(
        output_prefix=tmp_path / "chain", parameter_order=params
    )
    # Hand computation: weight>0 chi2 values are {12.5, 14.0} ∪ {11.75, 13.25};
    # the weight-0 chi2=3.0 seed point is excluded. min = 11.75.
    assert meta["best_chi2"] == 11.75
    assert "weight > 0" in meta["best_chi2_note"]


def test_parse_chain_files_without_chi2_column_reports_absent(tmp_path: Path) -> None:
    """Headerless chain files (no way to locate the chi2 column) must yield an
    honest best_chi2=None with a reason — never a guessed column."""
    params = ["H0", "omegam"]
    rows = _fixture_rows(params, 10.0, n_draws=50, seed=3)
    _write_cobaya_format_chain(
        tmp_path / "chain.1.txt", params, rows, with_header=False
    )
    _, meta = _parse_chain_files(
        output_prefix=tmp_path / "chain", parameter_order=params
    )
    assert meta["best_chi2"] is None
    assert "chain.1.txt" in meta["best_chi2_note"]

    # Mixed availability (one chain with a header, one without) is also not a
    # full-run minimum — must stay honestly absent.
    rows2 = _fixture_rows(params, 9.0, n_draws=50, seed=4)
    _write_cobaya_format_chain(tmp_path / "chain.2.txt", params, rows2)
    _, meta = _parse_chain_files(
        output_prefix=tmp_path / "chain", parameter_order=params
    )
    assert meta["best_chi2"] is None


def _dispatch_with_fixture_chains(
    *,
    model_key: str,
    param_names: list[str],
    planted_min_chi2: float,
    n_chains: int = 4,
    seed: int = 11,
) -> dict:
    """Run the REAL dispatch_external_cobaya path with the subprocess mocked to
    write deterministic cobaya-format chain files (the exact channel the bug
    lived on: subprocess -> _parse_chain_files -> _runner_success)."""
    import subprocess as sp

    entries = _validate_dataset_selection("lcdm", ["spt3g_cmb"])
    prior_bounds = {
        "H0": (55.0, 80.0),
        "omegam": (0.1, 0.5),
        "omegak": (-0.3, 0.3),
    }

    def fake_run(yaml_path: Path, *, timeout_s: float) -> sp.CompletedProcess:
        for chain_id in range(1, n_chains + 1):
            rows = _fixture_rows(
                param_names,
                planted_min_chi2 if chain_id == 1 else None,
                n_draws=800,
                seed=seed + chain_id,
            )
            _write_cobaya_format_chain(
                yaml_path.parent / f"chain.{chain_id}.txt", param_names, rows
            )
        return sp.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")

    with patch(
        "app.services.cobaya_runner._cobaya_likelihood_translatable",
        return_value=True,
    ), patch(
        "app.services.cobaya_runner._run_cobaya_subprocess", side_effect=fake_run
    ), patch(
        "app.services.cobaya_runner._verify_pinned_cmb_data",
        return_value={"hash_verified": True, "files_sha256": {}, "mismatches": []},
    ):
        return dispatch_external_cobaya(
            model_key=model_key,
            entries=entries,
            prior_bounds={k: prior_bounds[k] for k in param_names},
            parameter_order=param_names,
            seed=seed,
            sample_count=800,
            sampler="mcmc",
        )


def test_external_envelope_enters_model_comparison_pairing() -> None:
    """fail-before / pass-after for the P3b gap.

    Before the fix: the external envelope had NO fit_statistics, so
    (a) research_program's pairing gate isinstance(result.get("fit_statistics"),
        dict) excluded every external cell, and
    (b) compute_model_comparison produced all-None deltas even for two
        converged external chains.
    After: fit_statistics comes from the chain's real chi2 column and the pair
    yields hand-checkable deltas."""
    from app.services.cosmology_likelihoods import compute_model_comparison

    lcdm = _dispatch_with_fixture_chains(
        model_key="lcdm", param_names=["H0", "omegam"], planted_min_chi2=10.25
    )
    ok_lcdm = _dispatch_with_fixture_chains(
        model_key="ok_lcdm",
        param_names=["H0", "omegam", "omegak"],
        planted_min_chi2=8.0,
    )

    # (a) the exact research_program.py pairing gate condition
    assert isinstance(lcdm.get("fit_statistics"), dict)
    assert isinstance(ok_lcdm.get("fit_statistics"), dict)

    # Hand computation: chi2 = planted minimum of the chain chi2 column;
    # aic = chi2 + 2k (k = sampled parameters).
    fs = lcdm["fit_statistics"]
    assert fs["chi2"] == 10.25
    assert fs["aic"] == 10.25 + 2.0 * 2  # 14.25
    assert fs["n_parameters"] == 2
    fs_ext = ok_lcdm["fit_statistics"]
    assert fs_ext["chi2"] == 8.0
    assert fs_ext["aic"] == 8.0 + 2.0 * 3  # 14.0
    assert fs_ext["n_parameters"] == 3
    # BIC / n_constraints are honestly ABSENT (chain products do not record
    # the data-vector length N) — with the reason on the envelope, and no
    # resurrected delta_chi2 placeholder (2026-06-12 regression).
    for stats in (fs, fs_ext):
        assert "bic" not in stats
        assert "n_constraints" not in stats
        assert "delta_chi2" not in stats
        assert "data-vector length" in stats["note"]

    # (b) the pair yields hand-checkable diagnostic deltas, but the chain-file
    # minimum is not a separately optimised likelihood-only MLE, so no model
    # preference is claimable:
    # delta_chi2 = 8.0 - 10.25 = -2.25; delta_aic = 14.0 - 14.25 = -0.25.
    cmp = compute_model_comparison(lcdm, ok_lcdm)
    assert cmp["delta_chi2"] == -2.25
    assert cmp["delta_aic"] == -0.25
    assert cmp["delta_bic"] is None  # no N -> no BIC, honestly None
    assert cmp["n_extra_params"] == 1
    assert cmp["comparison_valid"] is False
    assert cmp["preferred"] == "undetermined"
    assert cmp["__do_not_claim__"] is True
    assert "likelihood-only MLE" in cmp["comparison_warning"]
    assert cmp["baseline_chi2_kind"] == "posterior_draw_minimum"
    assert cmp["extended_chi2_kind"] == "posterior_draw_minimum"

    # Tier semantics preserved (do NOT relax): fit_statistics must not upgrade
    # the chain.  The baseline is numerically converged but has no separately
    # attested model-adequacy manifest, while ok_lcdm is additionally off-anchor;
    # both therefore remain exploratory and non-claimable.
    assert lcdm["publication_gate"]["numerical_eligible"] is True
    assert lcdm["chain_tier"] == "exploratory"
    assert lcdm["publication_ready"] is False
    assert ok_lcdm["chain_tier"] == "exploratory"
    assert ok_lcdm["publication_ready"] is False


def test_single_external_chain_is_preliminary_and_has_no_verdict() -> None:
    """A single chain keeps factual fit statistics but is preliminary only.

    Model comparison still fails closed because rank-Rhat and per-parameter ESS
    are unavailable; fit_statistics must never promote it into a verdict.
    """
    from app.services.cosmology_likelihoods import compute_model_comparison

    lcdm = _dispatch_with_fixture_chains(
        model_key="lcdm", param_names=["H0", "omegam"], planted_min_chi2=10.25
    )
    preliminary = _dispatch_with_fixture_chains(
        model_key="ok_lcdm",
        param_names=["H0", "omegam", "omegak"],
        planted_min_chi2=8.0,
        n_chains=1,
    )
    assert preliminary["chain_tier"] == "exploratory"
    assert preliminary["publication_ready"] is False
    assert preliminary["preliminary_ready"] is True
    assert "fewer_than_four_independent_chains" in preliminary["preliminary_reasons"]
    assert preliminary["fit_statistics"]["chi2"] == 8.0  # factual, still reported

    cmp = compute_model_comparison(lcdm, preliminary)
    assert cmp["comparison_valid"] is False
    assert cmp["preferred"] == "undetermined"
    assert cmp["__do_not_claim__"] is True
    assert CobayaRunError("x", error_class="custom").error_class == "custom"


# ---------------------------------------------------------------------------
# 8. __message_to_model__ follows chain_tier (Codex review 2026-09-03,
#    thread PRRT_kwDORoeoE86eta7A)
# ---------------------------------------------------------------------------


def _external_payload(
    *,
    diagnostics: dict,
    data_verification: dict | None,
    with_adequacy: bool,
) -> dict:
    """Build a _runner_success envelope on a hash-bound LCDM/DESI selection.

    Mirrors test_off_anchor_abstain.test_external_cobaya_runner_applies_off_anchor_gate:
    lcdm on desi_dr1_bao with an omegam summary, four converged chains, a
    verified hash and an attested model-adequacy manifest is the one recipe
    that reaches chain_tier="publication" on this path.
    """
    from app.services.cosmology_likelihoods.verification import (
        PUBLICATION_REQUIRED_ADEQUACY_CHECKS,
        build_model_adequacy_attestation,
        build_model_adequacy_subject,
    )

    entries = [get_cosmology_dataset("desi_dr1_bao")]
    summaries = {"omegam": {"median": 0.30}}
    adequacy = None
    if with_adequacy:
        subject = build_model_adequacy_subject(
            model="lcdm",
            dataset_keys=[entry.key for entry in entries],
            random_seed=42,
            summaries=summaries,
            diagnostics=diagnostics,
            data_verification=data_verification,
        )
        adequacy = build_model_adequacy_attestation(
            subject=subject,
            evidence_by_check={
                name: {"artifact_id": f"test:{name}"}
                for name in PUBLICATION_REQUIRED_ADEQUACY_CHECKS
            },
        )
    return _runner_success(
        model_key="lcdm",
        entries=entries,
        seed=42,
        sampler="mcmc",
        summaries=summaries,
        diagnostics=diagnostics,
        chain_meta={},
        stdout_tail="",
        data_verification=data_verification,
        model_adequacy=adequacy,
    )


def test_external_message_to_model_follows_chain_tier() -> None:
    """The tool-card message must follow the tier computed a few lines above it.

    ``_runner_success`` computes ``chain_tier`` as publication / exploratory /
    blocked, but its ``__message_to_model__`` told the model to "say the run is
    exploratory" for every tier -- so a chain blocked by unverified inputs or
    missing diagnostics reached the user as merely exploratory (Codex review
    2026-09-03, thread PRRT_kwDORoeoE86eta7A).  Measured on the pre-fix code:
    all three blocked constructions below returned chain_tier="blocked" with a
    message containing "say the run is exploratory".
    """
    good_diag = {
        "overall_status": "ok",
        "n_chains": 4,
        "n_independent_chains": 4,
        "per_parameter": {"omegam": {"rhat": 1.001, "ess_bulk": 1200.0}},
    }
    verified = {"hash_verified": True, "files_sha256": {}, "mismatches": []}

    publication = _external_payload(
        diagnostics=good_diag, data_verification=verified, with_adequacy=True
    )
    assert publication["chain_tier"] == "publication"
    pub_msg = publication["__message_to_model__"]
    assert "publication" in pub_msg
    assert "exploratory" not in pub_msg.lower()
    assert "blocked" not in pub_msg.lower()

    exploratory = _external_payload(
        diagnostics=good_diag, data_verification=verified, with_adequacy=False
    )
    assert exploratory["chain_tier"] == "exploratory"
    exp_msg = exploratory["__message_to_model__"]
    assert "say the run is exploratory" in exp_msg
    assert "stay in this tool card" in exp_msg
    assert "blocked" not in exp_msg.lower()

    blocked_cases = {
        "unverified_hash": dict(diagnostics=good_diag, data_verification=None),
        "diagnostics_unavailable": dict(
            diagnostics={"overall_status": "diagnostics_unavailable", "n_chains": 4},
            data_verification=verified,
        ),
        "no_chains": dict(
            diagnostics={"overall_status": "no_chains", "n_chains": 0},
            data_verification=verified,
        ),
    }
    expected_cause = {
        "unverified_hash": "not hash-verified",
        "diagnostics_unavailable": "diagnostics are unavailable",
        "no_chains": "produced no chains",
    }
    for label, kwargs in blocked_cases.items():
        result = _external_payload(with_adequacy=False, **kwargs)
        assert result["chain_tier"] == "blocked", label
        msg = result["__message_to_model__"]
        assert "blocked" in msg.lower(), (label, msg)
        assert "exploratory" not in msg.lower(), (label, msg)
        # The stated cause is read off the run, not a generic diagnosis.
        assert expected_cause[label] in msg, (label, msg)
        # Nothing from a blocked chain may be quoted or described as a result,
        # and the unblocking conditions are named (prompt.md blocked bullet).
        assert "Do NOT report" in msg, (label, msg)
        assert "hash-verified" in msg and "diagnostics" in msg, (label, msg)
        assert "four independent chains" in msg, (label, msg)

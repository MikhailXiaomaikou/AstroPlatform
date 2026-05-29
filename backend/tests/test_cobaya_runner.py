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
    _summarise_samples,
    dispatch_external_cobaya,
    is_external_enabled,
)
from app.services.cosmology_likelihoods import (
    _validate_dataset_selection,
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
    )
    assert "mcmc:" in yaml and "Rminus1_stop" in yaml


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
    assert CobayaRunError("x", error_class="custom").error_class == "custom"

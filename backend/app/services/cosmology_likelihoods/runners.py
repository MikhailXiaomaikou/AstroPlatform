"""Public runners: likelihood chains, robustness matrix, model comparison, AP test.

Split verbatim out of the pre-2026-07-03 single-file
app/services/cosmology_likelihoods.py (7,757 lines). Import the package
``app.services.cosmology_likelihoods`` — it re-exports every pre-split name
and keeps the original one-namespace monkeypatch semantics.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.services.cosmology_likelihoods.core import (
    MODEL_LABELS,
    _derived_s8_from_samples,
    _s8_gaussian_constraints,
    _s8_is_derived,
)

from app.services.cosmology_likelihoods.registry import (
    get_cosmology_dataset,
)

from app.services.cosmology_likelihoods.config_builder import (
    _collect_citations,
    _combination_warnings,
    _config_hash,
    _validate_dataset_selection,
    _validate_model,
)

from app.services.cosmology_likelihoods.distances import (
    _flat_lcdm_distances_at_z,
)

from app.services.cosmology_likelihoods.bao import (
    load_verified_bao_data,
)

from app.services.cosmology_likelihoods.verification import (
    _finalize_cov_fidelity,
    _is_executable_bao_entry,
    _is_executable_cc_entry,
    _is_executable_des_sn_entry,
    _is_executable_dr12_entry,
    _is_executable_fsbao_entry,
    _is_executable_grid_bao_entry,
    _is_executable_rsd_entry,
    _is_executable_sn_entry,
)

from app.services.cosmology_likelihoods.sampling import (
    _all_external_cobaya,
    _cobaya_parameter_order,
    _combined_chi2,
    _compressed_parameter_order,
    _compressed_runner_unavailable,
    _pairwise_tensions,
    _posterior_summary,
    _run_sampling_likelihood_chain,
    _sanitize_runner_priors,
)



def compute_model_comparison(
    baseline_result: dict[str, Any], extended_result: dict[str, Any]
) -> dict[str, Any]:
    """Δχ² / ΔAIC / ΔBIC between a baseline (ΛCDM) and an extended-model fit
    on the SAME datasets — the real model-comparison the per-run delta_chi²=0.0
    placeholder never provided.

    Sign convention: Δ = extended − baseline. Δχ² < 0 means the extra freedom
    fits the data better; ΔAIC/ΔBIC already penalise the extra parameters, so
    they answer 'is that improvement worth the added parameters'. Preference is
    read off ΔAIC on a Jeffreys-like scale (|ΔAIC|<2 inconclusive).

    Validity guards (all fail closed to preferred='undetermined' while still
    reporting the factual deltas, and stamp __do_not_claim__ on the output so
    the deltas cannot support reply claims): a chain_tier='blocked' input —
    its chi2 is not evidence (ESS collapse, no prior support, unverifiable
    rows); an input whose chain tier or ESS cannot be established
    (convergence unverified); and a representation mismatch — sampled axes
    differing beyond the extended model's own parameters mean the two chi2
    came from different likelihoods.

    Valid verdicts additionally carry baseline/extended chain-tier fields and,
    when either input is exploratory-tier (today every in-process extended
    model is off-anchor, so this is the NORMAL case), a verdict_caveat that
    consumers must render next to the verdict."""
    bf = baseline_result.get("fit_statistics") or {}
    ef = extended_result.get("fit_statistics") or {}

    def _num(d: dict[str, Any], key: str) -> float | None:
        v = d.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v) if math.isfinite(float(v)) else None

    def _delta(key: str) -> float | None:
        b, e = _num(bf, key), _num(ef, key)
        return round(e - b, 4) if (b is not None and e is not None) else None

    base_model = str(baseline_result.get("model") or "lcdm")
    ext_model = str(extended_result.get("model") or "")
    delta_aic = _delta("aic")
    k_b, k_e = _num(bf, "n_parameters"), _num(ef, "n_parameters")

    # Δχ²/ΔAIC only mean anything when both fits used the SAME likelihood. A
    # compressed dataset with a model-DEPENDENT representation would compute the
    # two chi2 against different data vectors, invalidating the comparison
    # (planck2018_compressed used to be one: until 2026-07-07 its ΛCDM chains ran
    # a diagonal parameter summary while extended flat-DE chains ran the
    # Chen-Huang-Wang distance prior with an extra ombh2 axis; it now executes
    # the correlated distance prior on every flat model, so its lcdm-vs-wcdm
    # pairs compare against one likelihood). The guard stays: detect any future
    # swap from the sampled axes — any difference beyond the extended model's
    # own DE/extension parameters means the representation changed underneath.
    model_extension_params = {"w", "w0", "wa", "omegak", "mnu"}
    base_axes = baseline_result.get("parameters")
    ext_axes = extended_result.get("parameters")
    comparison_warning = None

    def _input_quality(result: dict[str, Any]) -> dict[str, Any]:
        tier = result.get("chain_tier")
        diags = result.get("chain_diagnostics")
        diags = diags if isinstance(diags, dict) else {}

        def _finite(value: Any) -> float | None:
            # bool is an int subclass and NaN passes isinstance — both must
            # not count as a measured ESS.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            return float(value) if math.isfinite(float(value)) else None

        ess = _finite(diags.get("proposal_ess"))
        if ess is None:
            ess = _finite(diags.get("ess_bulk"))
        return {
            "tier": tier if isinstance(tier, str) else None,
            "ess": ess,
        }

    quality = {
        "baseline": _input_quality(baseline_result),
        "extended": _input_quality(extended_result),
    }
    blocked_inputs = [label for label, q in quality.items() if q["tier"] == "blocked"]
    unvetted_inputs = [
        label for label, q in quality.items()
        if q["tier"] not in {"publication", "exploratory", "blocked"}
    ]
    ess_unknown_inputs = [
        label for label, q in quality.items()
        if q["tier"] in {"publication", "exploratory"} and q["ess"] is None
    ]
    if blocked_inputs:
        plural = len(blocked_inputs) > 1
        comparison_warning = (
            "the " + " and ".join(blocked_inputs)
            + (" fits carry" if plural else " fit carries")
            + " chain_tier='blocked' (ESS collapse, no prior support, or "
            "unverifiable rows): "
            + ("their" if plural else "its")
            + " chi2/AIC values are not evidence, so no model-preference "
            "verdict can be read from this pair."
        )
    elif unvetted_inputs:
        comparison_warning = (
            "no chain_tier could be established for the "
            + " and ".join(unvetted_inputs)
            + " fit(s); refusing a preference verdict from unvetted fits."
        )
    elif ess_unknown_inputs:
        comparison_warning = (
            "convergence is unverified (no measurable ESS) for the "
            + " and ".join(ess_unknown_inputs)
            + " fit(s): the best-fit chi2 has no numerical guarantee, so no "
            "model-preference verdict can be read from this pair."
        )
    elif not (isinstance(base_axes, dict) and isinstance(ext_axes, dict)):
        # Fail closed: without both sampled-axis summaries the representation
        # check below cannot run, and that check is exactly what catches
        # model-dependent compressed swaps. Every legitimate comparable result
        # today carries the parameters dict.
        comparison_warning = (
            "sampled-axis summaries are missing on at least one side, so "
            "representation comparability could not be established; no "
            "model-preference verdict can be read from this pair."
        )
    if comparison_warning is None and isinstance(base_axes, dict) and isinstance(ext_axes, dict):
        extra_beyond_model = (set(ext_axes) - set(base_axes)) - model_extension_params
        missing_from_ext = set(base_axes) - set(ext_axes)
        if extra_beyond_model or missing_from_ext:
            comparison_warning = (
                "sampled axes differ beyond the extended model's own parameters "
                f"(extra: {sorted(extra_beyond_model)}, missing: {sorted(missing_from_ext)}): "
                "a selected dataset uses a model-dependent compressed representation, "
                "so the two chi2 are computed against different likelihoods and "
                "delta_chi2/delta_aic are not a valid model comparison."
            )

    if comparison_warning is not None or delta_aic is None:
        preferred = "undetermined"
    elif delta_aic < -2.0:
        preferred = ext_model
    elif delta_aic > 2.0:
        preferred = base_model
    else:
        preferred = "inconclusive"
    out = {
        "baseline_model": base_model,
        "extended_model": ext_model,
        "delta_chi2": _delta("chi2"),
        "delta_aic": delta_aic,
        "delta_bic": _delta("bic"),
        "n_extra_params": int(k_e - k_b) if (k_b is not None and k_e is not None) else None,
        "preferred": preferred,
        "comparison_valid": comparison_warning is None,
        "baseline_chain_tier": quality["baseline"]["tier"],
        "extended_chain_tier": quality["extended"]["tier"],
        "baseline_ess": quality["baseline"]["ess"],
        "extended_ess": quality["extended"]["ess"],
        "convention": "delta = extended - baseline; negative favors the extended model; |delta_aic|<2 is inconclusive",
    }
    if comparison_warning is not None:
        out["comparison_warning"] = comparison_warning
        # The deltas stay visible for diagnostics, but they were computed from
        # at least one fit whose chi2 is not evidence (or from two different
        # likelihoods) — they must never support a reply claim.
        out["__do_not_claim__"] = True
    else:
        exploratory_inputs = [
            label for label, q in quality.items() if q["tier"] == "exploratory"
        ]
        if exploratory_inputs:
            out["verdict_caveat"] = (
                "the " + " and ".join(exploratory_inputs) + " fit is "
                "exploratory-tier (off-anchor frontier model and/or ESS below "
                "the publication floor): present the preference as "
                "compressed-preliminary evidence with this caveat, never as a "
                "published-anchor result."
            )
    return out


def run_likelihood_chain(
    *,
    model: str,
    dataset_keys: list[str],
    priors: dict[str, Any] | None = None,
    random_seed: int | None = None,
    n_samples: int = 4000,
    allow_emcee_fallback: bool = False,
) -> dict[str, Any]:
    """Run the phase-1 compressed Gaussian cosmology likelihood.

    This combines registry entries with explicit ``CompressedLikelihoodSpec``
    summaries and the DESI DR1 public Gaussian BAO mean/covariance products.
    Full external likelihood packages are reported as not-run unless a dataset
    has an explicit registered compression.  Pantheon+ defaults to such an
    SN compressed-preliminary path; the full covariance χ² runner is opt-in
    via PANTHEON_PLUS_FULL_CHI2_ENABLED.
    """
    model_key = _validate_model(model)
    entries = _validate_dataset_selection(model_key, dataset_keys)
    seed = int(random_seed if random_seed is not None else 20260502)
    sample_count = max(256, min(int(n_samples or 4000), 20000))
    if any(
        _is_executable_bao_entry(entry)
        or _is_executable_cc_entry(entry)
        or _is_executable_rsd_entry(entry)
        or _is_executable_fsbao_entry(entry)
        or _is_executable_dr12_entry(entry)
        or _is_executable_grid_bao_entry(entry)
        or _is_executable_sn_entry(entry)
        or _is_executable_des_sn_entry(entry)
        for entry in entries
    ):
        return _run_sampling_likelihood_chain(
            model_key=model_key,
            entries=entries,
            priors=priors,
            seed=seed,
            sample_count=sample_count,
            allow_emcee_fallback=allow_emcee_fallback,
        )

    # PART AI Phase 5 #2 Track 2 step 2: external_cobaya dispatch.
    # When EXTERNAL_COBAYA_ENABLED env is truthy AND every selected entry
    # has execution_mode="external_cobaya" AND cobaya is importable, hand
    # the run to the subprocess runner. Default off — the legacy
    # compressed-Gaussian path below remains the production behaviour, and
    # `cobaya_runner.dispatch_external_cobaya` itself returns a structured
    # NOT_PUB_READY envelope until step 3 ships the adapter resolver.
    from app.services import cobaya_runner

    if cobaya_runner.is_external_enabled() and _all_external_cobaya(entries):
        cobaya_param_order = _cobaya_parameter_order(model_key, entries)
        cobaya_priors = _sanitize_runner_priors(cobaya_param_order, priors)
        return cobaya_runner.dispatch_external_cobaya(
            model_key=model_key,
            entries=entries,
            prior_bounds=cobaya_priors,
            parameter_order=cobaya_param_order,
            seed=seed,
            sample_count=sample_count,
            # A real posterior, not a single evaluate-at-reference point. This is
            # the minutes-long heavy fit the EXTERNAL_COBAYA_ENABLED gate exists
            # for (it never runs on the interactive deadline path by default).
            sampler="mcmc",
        )

    compressed_entries = [entry for entry in entries if entry.compressed_likelihood is not None]
    skipped_entries = [entry for entry in entries if entry.compressed_likelihood is None]

    if not compressed_entries:
        return _compressed_runner_unavailable(
            model_key=model_key,
            entries=entries,
            seed=seed,
            reason="No selected dataset has a registered compressed Gaussian likelihood.",
        )

    if model_key != "lcdm":
        return _compressed_runner_unavailable(
            model_key=model_key,
            entries=entries,
            seed=seed,
            reason=(
                "Phase-1 compressed likelihoods are calibrated as ΛCDM summary "
                "constraints; extended-model parameters are genuinely sampled "
                "only on the external Cobaya CMB path: select the Planck 2018 "
                "likelihood datasets (planck_2018_highl_TTTEEE_lite + "
                "planck_2018_lowl_TT / planck_2018_lowl_EE, optionally "
                "planck_2018_lensing) with this same tool (requires "
                "EXTERNAL_COBAYA_ENABLED=true; off by default — a minutes-long "
                "fit), or run external Cobaya/CosmoSIS packages."
            ),
        )

    parameter_order = _compressed_parameter_order(compressed_entries)
    if not parameter_order:
        return _compressed_runner_unavailable(
            model_key=model_key,
            entries=entries,
            seed=seed,
            reason="Selected compressed likelihoods contain no supported phase-1 parameters.",
        )
    prior_bounds = _sanitize_runner_priors(parameter_order, priors)
    precision = np.zeros((len(parameter_order), len(parameter_order)), dtype=float)
    information = np.zeros(len(parameter_order), dtype=float)
    invalid_specs: list[str] = []

    for entry in compressed_entries:
        spec = entry.compressed_likelihood
        if spec is None:
            continue
        try:
            params = list(spec.parameters)
            mean = np.asarray(spec.mean, dtype=float)
            cov = np.asarray(spec.covariance, dtype=float)
            if mean.shape != (len(params),) or cov.shape != (len(params), len(params)):
                raise ValueError("mean/covariance dimensions do not match parameters")
            cov_inv = np.linalg.inv(cov)
            idx = [parameter_order.index(param) for param in params if param in parameter_order]
            local_idx = [params.index(param) for param in params if param in parameter_order]
            if not idx:
                # B2: this dataset has no LINEAR (sampled) parameter, so the
                # precision matrix can't take it. A pure derived-S8 dataset
                # (e.g. WL S8) is EXPECTED here — it is folded in below via the
                # S8 importance-reweighting (_s8_gaussian_constraints), so it
                # must NOT be flagged. Only a dataset that NO path applies — a
                # BBN ombh2 prior, whose parameter is neither sampled nor
                # reweighted — would otherwise contribute χ²=0 silently while
                # still appearing in datasets_used as publication-ready; flag
                # that one so the publication gate demotes the run.
                applied_via_s8 = "S8" in params and _s8_is_derived(parameter_order)
                if not applied_via_s8:
                    invalid_specs.append(
                        f"{entry.key}: none of its parameters {params} are in the "
                        f"sampled parameter set {list(parameter_order)} and it has "
                        f"no S8 row to reweight, so it contributed no constraint — "
                        f"not applied as run."
                    )
                continue
            sub_inv = cov_inv[np.ix_(local_idx, local_idx)]
            sub_mean = mean[local_idx]
            precision[np.ix_(idx, idx)] += sub_inv
            information[idx] += sub_inv @ sub_mean
        except Exception as exc:
            invalid_specs.append(f"{entry.key}: {exc}")

    try:
        posterior_cov = np.linalg.inv(precision)
        posterior_mean = posterior_cov @ information
    except Exception as exc:
        return _compressed_runner_unavailable(
            model_key=model_key,
            entries=entries,
            seed=seed,
            reason=f"Compressed likelihood precision matrix is singular ({exc}).",
        )

    prior_violations = [
        name
        for name, value in zip(parameter_order, posterior_mean, strict=True)
        if not (prior_bounds[name][0] <= float(value) <= prior_bounds[name][1])
    ]
    rng = np.random.default_rng(seed)
    # Apply any derived-S8 (σ8·√(Ωm/0.3)) constraints by importance-reweighting
    # the narrow closed-form proposal.  The linear precision matrix only saw the
    # σ8/Ωm rows; the WL/Planck S8 rows are folded in here on the *derived* value
    # so the sampler can never explore an S8 inconsistent with σ8/Ωm.
    s8_constraints = _s8_gaussian_constraints(compressed_entries)
    s8_ess: float | None = None
    if s8_constraints and _s8_is_derived(parameter_order):
        # The proposal is closed-form (cheap), so oversample a fixed pool: the
        # importance ESS then clears the 400 publication floor even for small
        # requested clouds and several S8 datasets in tension (planck+5WL ESS≈1.8k
        # at pool 8000 vs 233 at the bare sample_count of 1000).
        pool = max(sample_count, 8000)
        proposal = rng.multivariate_normal(posterior_mean, posterior_cov, size=pool)
        derived = _derived_s8_from_samples(proposal, parameter_order)
        log_w = np.zeros(pool, dtype=float)
        for mean_s8, sigma_s8 in s8_constraints:
            log_w += -0.5 * ((derived - mean_s8) / sigma_s8) ** 2
        log_w -= log_w.max()
        weights = np.exp(log_w)
        weights /= weights.sum()
        s8_ess = float(1.0 / np.sum(weights ** 2))
        samples = proposal[rng.choice(pool, size=sample_count, p=weights)]
    else:
        samples = rng.multivariate_normal(posterior_mean, posterior_cov, size=sample_count)
    summaries = {
        name: _posterior_summary(samples[:, index])
        for index, name in enumerate(parameter_order)
    }
    if _s8_is_derived(parameter_order):
        summaries["S8"] = _posterior_summary(
            _derived_s8_from_samples(samples, parameter_order)
        )
    chi2_point = samples.mean(axis=0) if s8_ess is not None else posterior_mean
    chi2 = _combined_chi2(compressed_entries, parameter_order, chi2_point)
    k = len(parameter_order)
    n_constraints = sum(
        len(entry.compressed_likelihood.parameters)
        for entry in compressed_entries
        if entry.compressed_likelihood is not None
    )
    aic = chi2 + 2.0 * k
    bic = chi2 + math.log(max(n_constraints, 1)) * k
    tensions = _pairwise_tensions(compressed_entries)
    result_hash = _config_hash(
        model_key,
        [entry.key for entry in compressed_entries],
        {name: prior_bounds[name] for name in parameter_order},
        f"compressed_gaussian:{seed}:{sample_count}",
    )
    warnings = [
        "Compressed Gaussian summary likelihood; use as preliminary consistency check, not full external likelihood.",
    ]
    warnings.extend(_combination_warnings(entries))
    if skipped_entries:
        warnings.append(
            "Datasets not run in compressed phase: "
            + ", ".join(entry.key for entry in skipped_entries)
            + ". Generate external Cobaya/CosmoSIS configs for those datasets."
        )
    if invalid_specs:
        warnings.extend(invalid_specs)
    if prior_violations:
        warnings.append("Posterior mean outside configured prior bounds for: " + ", ".join(prior_violations))
    s8_underpowered = s8_ess is not None and s8_ess < 400.0
    if s8_underpowered:
        warnings.append(
            f"Derived-S8 reweighting ESS={s8_ess:.0f} below the 400 publication "
            "floor; S8-combined posterior is exploratory only."
        )

    # Every executed compressed summary is a hand-typed Gaussian -> 'literature_typed'
    # (no released file to checksum); stamp it so a compressed-only chain is never
    # left with an unstamped (None) fidelity the publication gate would ignore.
    cov_fidelity, artifact_sha256, fidelity_ok = _finalize_cov_fidelity(
        compressed_entries, warnings
    )
    # Off-anchor guard, mirrored from the sampling path: an extended-model chain
    # whose frontier parameters (w/w0/wa) have no reproduced anchor is never
    # publication-ready.  This analytic branch is lcdm-only today (guarded
    # upstream), so chain_is_off_anchor short-circuits to False — a no-op now
    # that removes the latent asymmetry if that upstream guard is ever relaxed.
    from app.services.cosmology_oracle import chain_is_off_anchor
    off_anchor = chain_is_off_anchor(model_key, [entry.key for entry in compressed_entries])
    # do_not_combine_with violation double-counts shared measurements -> never
    # publication-ready (mirrors the sampling path).
    combination_conflict = bool(_combination_warnings(entries))
    publication_ready = (
        not invalid_specs
        and not prior_violations
        and not skipped_entries
        and not s8_underpowered
        and fidelity_ok
        and not off_anchor
        and not combination_conflict
    )
    # Compressed-Gaussian analytic path is otherwise binary by construction: the
    # posterior is closed-form so there is no "exploratory" intermediate (the
    # only soft gate is the derived-S8 reweighting ESS above).
    chain_tier = "publication" if publication_ready else "blocked"
    result: dict[str, Any] = {
        "success": True,
        "__tool_status__": "COMPLETED" if publication_ready else "PARTIAL",
        "analysis_status": "COMPRESSED_CHAIN_READY" if publication_ready else "PARTIAL",
        "publication_ready": publication_ready,
        "chain_tier": chain_tier,
        "off_anchor_review_required": off_anchor,
        "claim_scope": "compressed_likelihood_preliminary",
        "compressed_likelihood_preliminary": True,
        "model": model_key,
        "model_label": MODEL_LABELS[model_key],
        "sampler": "compressed_gaussian_analytic",
        "parameters": summaries,
        "posterior_summary": summaries,
        "derived_params": {
            name: summaries[name]
            for name in ("S8", "sigma8", "omegam", "H0")
            if name in summaries
        },
        "pairwise_tensions": tensions,
        "fit_statistics": {
            "chi2": round(float(chi2), 6),
            # delta_chi2 placeholder removed (2026-06-12) — see the sampling
            # runner's fit_statistics note; use compute_model_comparison.
            "aic": round(float(aic), 6),
            "bic": round(float(bic), 6),
            "n_constraints": int(n_constraints),
            "n_parameters": int(k),
        },
        "chain_diagnostics": {
            "overall_status": (
                "analytic_gaussian"
                if s8_ess is None
                else "analytic_gaussian_s8_reweighted"
            ),
            "publication_ready": publication_ready,
            # No sampling chains exist on the analytic path — R-hat is
            # undefined, not 1.0 (2026-06-12 review: the hard-coded value was
            # a never-computed statistic in the provenance envelope). The
            # exact-Gaussian draws ARE independent, so ess = n_draws is real.
            "rhat": None,
            "rhat_note": "not applicable (analytic Gaussian, no sampling chains)",
            "ess_bulk": int(round(s8_ess)) if s8_ess is not None else sample_count,
            "ess_source": (
                "importance_weights" if s8_ess is not None else "exact_gaussian_draws"
            ),
            "n_draws": sample_count,
            "n_chains": 1,
            "thresholds": {"ess_min": 400},
        },
        "datasets_used": [entry.to_dict() for entry in compressed_entries],
        "datasets_not_run": [entry.to_dict() for entry in skipped_entries],
        "dataset_keys": [entry.key for entry in entries],
        "priors": {name: list(bounds) for name, bounds in prior_bounds.items()},
        "random_seed": seed,
        "n_samples": sample_count,
        "runner_hash": result_hash,
        "warnings": warnings,
        "__message_to_model__": (
            "This is a publication-ready compressed-Gaussian preliminary result, "
            "not a full external likelihood run. You may quote posterior/tension "
            "numbers only with that caveat and only for datasets_used; do not "
            "claim that datasets_not_run were included in the numerical posterior."
        ),
        "provenance": {
            "cosmology_likelihood": {
                "registry_version": "2026-04-30",
                "runner": "compressed_gaussian_analytic",
                "runner_hash": result_hash,
                "dataset_keys": [entry.key for entry in entries],
                "datasets_used": [entry.key for entry in compressed_entries],
                "datasets_not_run": [entry.key for entry in skipped_entries],
                "citations": _collect_citations(entries),
                "compressed_sources": [
                    {
                        "dataset_key": entry.key,
                        "source_locator": entry.compressed_likelihood.source_locator
                        if entry.compressed_likelihood else None,
                        "approximation": entry.compressed_likelihood.approximation
                        if entry.compressed_likelihood else None,
                    }
                    for entry in compressed_entries
                ],
                "cov_fidelity": cov_fidelity,
                "artifact_sha256": artifact_sha256,
                "publication_ready": publication_ready,
            },
        },
    }
    if not publication_ready:
        result["__do_not_claim__"] = True
        # bug_011 fix: per-tier rewrite of __message_to_model__ — the
        # publication-tier text ("you may quote posterior/tension numbers")
        # contradicts the chain_tier=blocked / __do_not_claim__ guardrail.
        # Mirrors the per-tier message handling in _run_sampling_likelihood_chain.
        if invalid_specs:
            blocked_reason = "Invalid compressed-likelihood specs: " + "; ".join(invalid_specs)
        elif skipped_entries:
            blocked_reason = (
                "Requested datasets were not numerically included: "
                + ", ".join(entry.key for entry in skipped_entries)
            )
        elif prior_violations:
            blocked_reason = (
                "Posterior mean outside configured prior bounds for: "
                + ", ".join(prior_violations)
            )
        else:
            blocked_reason = "Compressed-Gaussian analytic chain blocked"
        result["__message_to_model__"] = (
            blocked_reason
            + ". Do not cite H0, Om0, sigma8, S8, HDI, or posterior "
            "constraints from this result."
        )
    return result


def run_robustness_matrix(
    *,
    model: str,
    supernova_sets: list[str] | None = None,
    include_h0_prior: bool = True,
    include_weak_lensing: bool = False,
    random_seed: int | None = None,
    n_samples: int = 4000,
) -> dict[str, Any]:
    model_key = _validate_model(model)
    sn_keys = list(supernova_sets) if supernova_sets is not None else ["pantheon_plus"]
    if not sn_keys:
        sn_keys = ["pantheon_plus"]
    combos: list[tuple[str, list[str]]] = [
        ("BAO only", ["desi_dr1_bao"]),
        ("SN only", [sn_keys[0]]),
        ("CMB only", ["planck2018_compressed"]),
        ("BAO + CMB", ["desi_dr1_bao", "planck2018_compressed"]),
    ]
    if include_weak_lensing:
        combos.append((
            "BAO + CMB + weak lensing",
            [
                "desi_dr1_bao",
                "planck2018_compressed",
                "kids1000_wl",
                "des_y3_3x2pt",
                "hsc_y1_cosmic_shear",
            ],
        ))
    for sn_key in sn_keys:
        label = get_cosmology_dataset(sn_key).display_name
        if sn_key != sn_keys[0]:
            combos.append((f"{label} only", [sn_key]))
        combos.append((f"BAO + {label}", ["desi_dr1_bao", sn_key]))
        combos.append((f"{label} + CMB", [sn_key, "planck2018_compressed"]))
        combos.append((f"BAO + {label} + CMB", ["desi_dr1_bao", sn_key, "planck2018_compressed"]))
    if include_h0_prior:
        combos.extend([
            (label + " + SH0ES H0", keys + ["shoes_h0_riess22"])
            for label, keys in list(combos)
        ])

    matrix: list[dict[str, Any]] = []
    base_seed = int(random_seed if random_seed is not None else 20260502)
    for index, (label, keys) in enumerate(combos):
        try:
            run = run_likelihood_chain(
                model=model_key,
                dataset_keys=keys,
                random_seed=base_seed + index,
                n_samples=n_samples,
            )
            # PART AD: explicit per-cell status so the UI can tell an empty
            # cell (config-only / missing likelihood) apart from a negative
            # scientific result. `runnable` is kept for back-compat.
            if run.get("publication_ready"):
                cell_status = "runnable"
            else:
                _as = str(run.get("analysis_status") or "").upper()
                if "NO_COMPRESSED" in _as or "MISSING" in _as:
                    cell_status = "missing_likelihood"
                elif run.get("execution_status") == "not_run" or "CONFIG" in _as:
                    cell_status = "config_only"
                elif isinstance(run.get("chain_diagnostics"), dict):
                    cell_status = "executed_not_ready"
                else:
                    cell_status = "blocked"
            execution_level = (
                "compressed_preliminary"
                if cell_status == "runnable"
                else "executed_not_ready"
                if cell_status == "executed_not_ready"
                else "config_only"
                if cell_status == "config_only"
                else "not_available"
            )
            matrix.append({
                "label": label,
                "dataset_keys": keys,
                "runnable": bool(run.get("publication_ready")),
                "publication_ready": bool(run.get("publication_ready")),
                "status": cell_status,
                "execution_level": execution_level,
                "result": run,
                "warnings": run.get("warnings", []),
            })
        except Exception as exc:
            matrix.append({
                "label": label,
                "dataset_keys": keys,
                "runnable": False,
                "publication_ready": False,
                "status": "failed",
                "error": str(exc),
                "error_class": exc.__class__.__name__,
            })

    ready_cells = [row for row in matrix if row.get("publication_ready")]
    not_ready_labels = [
        row["label"] for row in matrix if row.get("status") == "executed_not_ready"
    ]
    matrix_message = (
        "Summarize only cells with publication_ready=true as compressed preliminary "
        "results. For non-runnable cells, say the external likelihood/config is still needed."
    )
    if not_ready_labels:
        matrix_message += (
            " Cells marked executed_not_ready ran but their importance-sampling ESS fell "
            "below the publication floor (typically 3+ probe products). Do not quote those "
            "cells directly; to obtain a quotable posterior for one, call "
            "run_cosmology_likelihood_chain on that single dataset combination — it "
            "auto-upgrades to compressed-emcee (~11 s) and can reach publication-ready ESS."
        )
    return {
        "success": True,
        "__tool_status__": "COMPLETED" if ready_cells else "PARTIAL",
        "analysis_status": "COMPRESSED_ROBUSTNESS_READY" if ready_cells else "PARTIAL",
        "publication_ready": bool(ready_cells),
        "claim_scope": "compressed_likelihood_preliminary",
        "model": model_key,
        "matrix_size": len(matrix),
        "ready_cells": len(ready_cells),
        "include_weak_lensing": bool(include_weak_lensing),
        "matrix": matrix,
        "warnings": [
            "Robustness matrix uses compressed Gaussian summaries where available; config-only cells are not numerical evidence.",
        ],
        "__message_to_model__": matrix_message,
    }


def run_alcock_paczynski_test(
    *,
    omega_m_grid: tuple[float, float, int] = (0.10, 0.50, 401),
) -> dict[str, Any]:
    """Alcock-Paczynski geometric test on DESI DR1 BAO DM/DH ratios.

    The (DM/rs) / (DH/rs) ratio at each redshift is independent of H0
    and rs (both cancel), so it provides a pure-geometry Ωm constraint
    that does NOT use the BAO peak amplitude — only the ratio of
    transverse to line-of-sight distances. Inconsistency between the
    AP-only Ωm and the BAO-amplitude Ωm signals either non-flat
    geometry or evolving dark energy.

    DESI DR1 has 5 redshift bins with both DM/rs and DH/rs measured
    (z = 0.510 / 0.706 / 0.930 / 1.317 / 2.330). DV/rs-only bins
    (z = 0.295, 1.491) cannot enter the AP test.

    Reference: Alcock & Paczynski 1979 Nature 281 358.

    Returns a dict with Ωm best-fit, 1σ band, per-z observed ratios,
    chi² at minimum, and degrees of freedom.
    """
    # Provenance binding (2026-06-12 review): fit the sha256-VERIFIED vendored
    # arrays, not the legacy hand-typed constants — the constants are
    # byte-identical to the vendored file (documented at _HARDCODED_BAO), so
    # this shifts no number, but it puts the array this tool actually fits
    # under the registry pin audit and makes publication_ready conditional on
    # verification instead of unconditional ("decorative provenance" class).
    verified = load_verified_bao_data("desi_dr1_bao")
    if not verified.get("hash_verified") or verified.get("mean_vector") is None:
        return {
            "tool": "alcock_paczynski_test",
            "success": False,
            "error": (
                "DESI DR1 BAO vendored data failed sha256 verification (or is "
                "missing); refusing to run the AP test on unverified data."
            ),
            "error_class": "unverified_data",
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "publication_ready": False,
            "__do_not_claim__": True,
        }
    mean_vector = verified["mean_vector"]
    pairs: list[tuple[float, float, float]] = []
    cov = verified["covariance"]
    for i, (z_dm, val_dm, qty_dm) in enumerate(mean_vector):
        if qty_dm != "DM_over_rs":
            continue
        for j, (z_dh, val_dh, qty_dh) in enumerate(mean_vector):
            if qty_dh != "DH_over_rs" or abs(z_dh - z_dm) > 1e-6:
                continue
            sigma_dm_sq = cov[i][i]
            sigma_dh_sq = cov[j][j]
            cov_dm_dh = cov[i][j]
            ratio = val_dm / val_dh
            sigma_ratio_sq = (
                sigma_dm_sq / val_dh ** 2
                + (val_dm / val_dh ** 2) ** 2 * sigma_dh_sq
                - 2 * (val_dm / val_dh ** 3) * cov_dm_dh
            )
            sigma_ratio = math.sqrt(max(sigma_ratio_sq, 1e-30))
            pairs.append((z_dm, ratio, sigma_ratio))
            break  # don't double-count if duplicate DH at same z

    if not pairs:
        return {
            "tool": "alcock_paczynski_test",
            "success": False,
            "error": "DESI DR1 BAO MEAN_VECTOR has no (DM, DH) pairs at the same redshift.",
            "error_class": "no_dm_dh_pairs",
            "__tool_status__": "FAILED",
            "__do_not_claim__": True,
        }

    omega_m_lo, omega_m_hi, omega_m_n = omega_m_grid
    omega_m = np.linspace(omega_m_lo, omega_m_hi, int(omega_m_n))
    h0 = np.full_like(omega_m, 70.0)  # H0 cancels in DM/DH ratio

    chi2 = np.zeros_like(omega_m)
    for z, ratio_obs, sigma_ratio in pairs:
        dm_pred, dh_pred, _ = _flat_lcdm_distances_at_z(z, h0, omega_m)
        ratio_pred = dm_pred / dh_pred
        chi2 += ((ratio_obs - ratio_pred) / sigma_ratio) ** 2

    best_idx = int(np.argmin(chi2))
    omega_m_best = float(omega_m[best_idx])
    chi2_min = float(chi2[best_idx])

    # 1σ band from Δχ² < 1
    in_1sigma_mask = (chi2 - chi2_min) < 1.0
    in_1sigma = omega_m[in_1sigma_mask]
    omega_m_lo_1sigma = float(in_1sigma.min()) if in_1sigma.size else omega_m_best
    omega_m_hi_1sigma = float(in_1sigma.max()) if in_1sigma.size else omega_m_best

    n_dof = max(len(pairs) - 1, 1)
    z_pairs_payload = [
        {
            "z": round(z, 4),
            "DM_DH_ratio": round(ratio, 6),
            "sigma_ratio": round(sigma, 6),
        }
        for z, ratio, sigma in pairs
    ]

    return {
        "tool": "alcock_paczynski_test",
        "success": True,
        "method": (
            "DESI DR1 BAO DM/DH ratio chi-square fit. H0 and r_d cancel "
            "in the geometric ratio, leaving Ωm as the only free "
            "parameter under flat LCDM."
        ),
        "n_redshift_pairs": len(pairs),
        "n_dof": n_dof,
        "omega_m_best": round(omega_m_best, 6),
        "omega_m_1sigma_low": round(omega_m_lo_1sigma, 6),
        "omega_m_1sigma_high": round(omega_m_hi_1sigma, 6),
        "omega_m_1sigma_half_width": round(
            (omega_m_hi_1sigma - omega_m_lo_1sigma) / 2.0, 6
        ),
        "chi2_min": round(chi2_min, 4),
        "chi2_per_dof": round(chi2_min / n_dof, 4),
        "z_pairs": z_pairs_payload,
        # Conditional on the sha256 verification above — an unverified file
        # already returned a loud refusal before reaching this point.
        "publication_ready": bool(verified.get("hash_verified")),
        "claim_scope": "alcock_paczynski_geometric_omega_m",
        "data_origin": "real_archive",
        "analysis_status": "ALCOCK_PACZYNSKI_READY",
        "citations": [
            {
                "label": "Alcock & Paczynski geometric test",
                "year": 1979,
                "doi": "10.1038/281358a0",
                "bibcode": "1979Natur.281..358A",
            },
            {
                "label": "DESI Collaboration DR1 BAO",
                "year": 2024,
                "arxiv": "2404.03002",
            },
        ],
        "provenance": {
            "alcock_paczynski": {
                "input_dataset": "desi_dr1_bao",
                "artifact_sha256": verified.get("sha256"),
                "cov_fidelity": verified.get("cov_fidelity"),
                "n_pairs_used": len(pairs),
                "omega_m_grid_min_max": [float(omega_m.min()), float(omega_m.max())],
                "omega_m_grid_resolution": int(omega_m_n),
                "H0_cancellation": "DM/DH ratio is independent of H0 and r_d",
                "model_assumed": "flat LCDM",
                "free_parameters": ["omegam"],
                "notes": (
                    "AP-only Ωm vs BAO-amplitude Ωm comparison reveals "
                    "non-flat geometry or evolving dark energy when "
                    "discrepant > 2σ."
                ),
            },
        },
    }

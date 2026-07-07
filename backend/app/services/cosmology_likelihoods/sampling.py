"""In-process samplers (importance/emcee/fast paths) and runner support.

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
    CMB_PARAMETER_PRIORS,
    CompressedLikelihoodSpec,
    CosmologyDatasetEntry,
    MODEL_LABELS,
    RUNNER_PARAMETER_PRIORS,
    S8_PIVOT_OMEGAM,
    SUPPORTED_MODELS,
    _derived_s8_from_samples,
    _drop_derived_s8,
    _s8_is_derived,
    logger,
)

from app.services.cosmology_likelihoods.config_builder import (
    _collect_citations,
    _combination_warnings,
    _config_hash,
)

from app.services.cosmology_likelihoods.bao import (
    EBOSS_DR16_FSBAO_EXECUTABLE_KEYS,
    EBOSS_DR16_GRID_BAO_EXECUTABLE_KEYS,
    MGS_OUT_OF_BOUNDS_CHI2,
    SDSS_DR12_CONSENSUS_EXECUTABLE_KEYS,
    SDSS_DR12_RS_FID_MPC,
    _BAO_DATA,
    _BAO_FAST_PATH_KEYS,
    _FSBAO_DATA,
    _bao_chi2_samples,
    _dr12_chi2_samples,
    _fsbao_chi2_samples,
    _grid_bao_chi2_samples,
    _grid_support_warnings,
    load_verified_dr12_consensus_data,
)

from app.services.cosmology_likelihoods.cc import (
    _cc_entry_point_count,
    _cosmic_chronometer_chi2_samples,
    _cosmic_chronometer_moresco20_chi2_samples,
)

from app.services.cosmology_likelihoods.rsd import (
    EBOSS_DR16_FSIGMA8,
    _eboss_fsigma8_chi2_samples,
)

from app.services.cosmology_likelihoods.sn import (
    PANTHEON18_EXECUTABLE_KEYS,
    _load_pantheon_plus_data,
    _offset_sn_chi2_samples,
    _offset_sn_n_points,
    _pantheon_plus_chi2_samples,
)

from app.services.cosmology_likelihoods.cmb import (
    CMB_APLANCK_KEYS,
    CMB_COBAYA_EXECUTABLE_KEYS,
    PLANCK18_DP_PROPOSAL_MOMENTS,
    _compressed_chi2_samples,
    _planck_dp_lcdm_proposal_moments,
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


# ESS floor below which a single-cell chain auto-upgrades from importance
# sampling to compressed-emcee.  Multi-probe products (3+ likelihoods) push the
# joint posterior far narrower than any proposal Gaussian, so importance ESS
# collapses (measured: BAO+SN+CMB ESS≈39).  emcee ensemble sampling recovers it
# (≈780) in ~11 s, well inside the 45 s tool deadline.  Robustness/research
# matrices keep the fast importance path so they stay inside their own deadline.
_EMCEE_FALLBACK_ESS_FLOOR = 400.0


def _sn_emcee_bypass_active(
    sn_entries: list[CosmologyDatasetEntry] | None,
    des_sn_entries: list[CosmologyDatasetEntry] | None,
    allow_emcee_fallback: bool,
) -> bool:
    """Whether SN entries replace importance sampling with the emcee bypass.

    Single source for the runner's sampler label AND the routing inside
    _draw_importance_posterior, so the two cannot drift apart. Expensive
    full-vector SN sets (Pantheon+ 1701, DES-SN5YR 1829 — both env-gated
    opt-ins) ALWAYS take emcee: no proposal Gaussian covers their posteriors
    and the per-draw cost is prohibitive. The cheap offset-marginalized set
    (Union3, 22x22) takes emcee only when the caller allows it — robustness
    matrices pass allow_emcee_fallback=False precisely so every cell stays
    inside the chat tool deadline, and a 22-bin chi2 importance-samples fine
    (2026-06-12 review: union3 cells were forcing 30-cell matrices to ~64 s,
    past the 45 s default deadline)."""
    if sn_entries and any(e.key == "pantheon_plus" for e in sn_entries):
        return True
    if any(e.key in ("des_sn5yr", "pantheon18") for e in (des_sn_entries or [])):
        return True
    return bool(des_sn_entries) and allow_emcee_fallback


def _run_sampling_likelihood_chain(
    *,
    model_key: str,
    entries: list[CosmologyDatasetEntry],
    priors: dict[str, Any] | None,
    seed: int,
    sample_count: int,
    allow_emcee_fallback: bool = False,
) -> dict[str, Any]:
    """Importance-sample executable low-dimensional likelihood products.

    DESI DR1 publishes a Gaussian BAO measurement vector and covariance.  We
    evaluate those raw data products directly against flat ΛCDM distance-ratio
    predictions, while still treating full desilike/Cobaya as the higher-fidelity
    second-stage likelihood.
    """
    # Curvature + neutrino-mass extensions still require external CAMB; the
    # phase-1 distance integral is hard-coded flat with massless neutrinos.
    if model_key.startswith("ok_") or model_key.endswith("_mnu"):
        return _compressed_runner_unavailable(
            model_key=model_key,
            entries=entries,
            seed=seed,
            reason=(
                "The phase-1 in-process runner (BAO distance ratios, cosmic-"
                "chronometer H(z), RSD fσ8 growth) is flat-geometry, massless-"
                f"neutrino only — it never samples omegak or mnu, so running "
                f"'{model_key}' here would only relabel a ΛCDM-shaped chain. "
                "Curvature (ok_*) and neutrino-mass (*_mnu) extensions are "
                "genuinely sampled only on the external Cobaya CMB path: select "
                "the Planck 2018 likelihood datasets "
                "(planck_2018_highl_TTTEEE_lite + planck_2018_lowl_TT / "
                "planck_2018_lowl_EE, optionally planck_2018_lensing) with this "
                "same tool (requires EXTERNAL_COBAYA_ENABLED=true; off by "
                "default — a minutes-long fit). Caveat for ok_* models: primary "
                "CMB alone carries a geometric degeneracy in omegak, so treat "
                "CMB-only curvature posteriors accordingly."
            ),
        )

    bao_entries = [entry for entry in entries if _is_executable_bao_entry(entry)]
    cc_entries = [entry for entry in entries if _is_executable_cc_entry(entry)]
    rsd_entries = [entry for entry in entries if _is_executable_rsd_entry(entry)]
    fsbao_entries = [entry for entry in entries if _is_executable_fsbao_entry(entry)]
    dr12_entries = [entry for entry in entries if _is_executable_dr12_entry(entry)]
    grid_bao_entries = [entry for entry in entries if _is_executable_grid_bao_entry(entry)]
    sn_entries = [entry for entry in entries if _is_executable_sn_entry(entry)]
    des_sn_entries = [entry for entry in entries if _is_executable_des_sn_entry(entry)]
    # des_sn5yr, when its full χ² is enabled, runs the executable path — exclude it
    # from the compressed-summary set just like the other executable SN entries.
    executable_sn_keys = {entry.key for entry in sn_entries} | {entry.key for entry in des_sn_entries}
    compressed_entries = [
        entry
        for entry in entries
        if entry.compressed_likelihood is not None and entry.key not in executable_sn_keys
    ]
    executable_keys = (
        {e.key for e in bao_entries}
        | {e.key for e in sn_entries}
        | {e.key for e in cc_entries}
        | {e.key for e in rsd_entries}
        | {e.key for e in fsbao_entries}
        | {e.key for e in dr12_entries}
        | {e.key for e in grid_bao_entries}
        | {e.key for e in des_sn_entries}
    )
    skipped_entries = [
        entry
        for entry in entries
        if entry.key not in executable_keys
        and entry.compressed_likelihood is None
    ]
    parameter_order = _sampling_parameter_order(
        bao_entries, compressed_entries, sn_entries, model_key=model_key,
        cc_entries=cc_entries, rsd_entries=rsd_entries, fsbao_entries=fsbao_entries,
        dr12_entries=dr12_entries, grid_bao_entries=grid_bao_entries,
        des_sn_entries=des_sn_entries,
    )
    if not parameter_order:
        return _compressed_runner_unavailable(
            model_key=model_key,
            entries=entries,
            seed=seed,
            reason="Selected datasets contain no phase-1 executable likelihood parameters.",
        )

    prior_bounds = _sanitize_runner_priors(parameter_order, priors)
    rng = np.random.default_rng(seed)
    invalid_specs: list[str] = []
    # SN entries may take the emcee bypass inside _draw_importance_posterior
    # rather than importance sampling; the shared helper decides for BOTH the
    # label here and the routing there.
    _sn_emcee_bypass = _sn_emcee_bypass_active(
        sn_entries, des_sn_entries, allow_emcee_fallback
    )
    sampler_used = "sn_emcee" if _sn_emcee_bypass else "bao_gaussian_importance"

    try:
        # Fast analytic-grid BAO-only path is calibrated for flat ΛCDM in the
        # natural (H0, omegam, rd) plane against a single DESI BAO vector (DR1 or
        # DR2); wCDM / w0waCDM add extra dimensions and other BAO datasets have
        # their own vectors, so anything else falls through to the importance
        # sampler. Requires EXACTLY ONE bao entry so a DR1+DR2 (overlapping) mix
        # never fires it (which would silently fit only one of the two).
        if (
            len(bao_entries) == 1
            and bao_entries[0].key in _BAO_FAST_PATH_KEYS
            and not compressed_entries
            and not sn_entries
            and not cc_entries
            and not rsd_entries
            and not fsbao_entries
            and not dr12_entries
            and not grid_bao_entries
            and not des_sn_entries
            and parameter_order == ["H0", "omegam", "rd"]
        ):
            (
                posterior_samples,
                best_chi2,
                proposal_ess,
                proposal_draws,
            ) = _draw_desi_bao_only_posterior(
                rng,
                parameter_order,
                prior_bounds,
                sample_count,
                key=bao_entries[0].key,
            )
        else:
            (
                posterior_samples,
                best_chi2,
                proposal_ess,
                proposal_draws,
                compressed_errors,
            ) = _draw_importance_posterior(
                rng,
                parameter_order,
                prior_bounds,
                bao_entries,
                compressed_entries,
                sample_count,
                sn_entries=sn_entries,
                cc_entries=cc_entries,
                rsd_entries=rsd_entries,
                fsbao_entries=fsbao_entries,
                dr12_entries=dr12_entries,
                grid_bao_entries=grid_bao_entries,
                des_sn_entries=des_sn_entries,
                allow_emcee_fallback=allow_emcee_fallback,
            )
            invalid_specs.extend(compressed_errors)
            # ESS-floor emcee upgrade.  Importance sampling collapses on 3+
            # probe products and on extended-DE axes; emcee recovers a
            # quotable posterior in ~11 s.  Matrices pass
            # allow_emcee_fallback=False for ΛCDM baseline subset cells (they
            # stay fast) and True for the extended-model branch cells AND the
            # full-union ΛCDM comparison anchor (emcee-eligible cells are
            # budget-capped at 3 per matrix), which is why run_research_matrix
            # has its own 120 s tool deadline.
            # Requires every entry to have an executable chi²
            # (no skipped_entries) and ≥2 likelihood components.
            n_components = (
                len(bao_entries) + len(compressed_entries) + len(sn_entries)
                + len(cc_entries) + len(rsd_entries) + len(fsbao_entries)
                + len(dr12_entries) + len(grid_bao_entries)
                + len(des_sn_entries)
            )
            if (
                allow_emcee_fallback
                and proposal_ess < _EMCEE_FALLBACK_ESS_FLOOR
                and not skipped_entries
                and n_components >= 2
            ):
                try:
                    (
                        _em_samples,
                        _em_best_chi2,
                        _em_ess,
                        _em_draws,
                        emcee_errors,
                    ) = _run_emcee_chain(
                        seed,
                        parameter_order,
                        prior_bounds,
                        bao_entries,
                        compressed_entries,
                        sn_entries,
                        sample_count,
                        cc_entries=cc_entries,
                        rsd_entries=rsd_entries,
                        fsbao_entries=fsbao_entries,
                        dr12_entries=dr12_entries,
                        grid_bao_entries=grid_bao_entries,
                        des_sn_entries=des_sn_entries,
                    )
                    # 2026-06-12 review: only adopt the emcee upgrade when its
                    # ESS estimate is finite AND an actual improvement. If the
                    # emcee autocorrelation estimate failed (NaN), a measured
                    # importance ESS — even a low one — is strictly more honest
                    # than an unverifiable chain.
                    if math.isfinite(_em_ess) and _em_ess > proposal_ess:
                        posterior_samples = _em_samples
                        best_chi2 = _em_best_chi2
                        proposal_ess = _em_ess
                        proposal_draws = _em_draws
                        invalid_specs.extend(emcee_errors)
                        sampler_used = "compressed_emcee"
                    else:
                        logger.warning(
                            "compressed-emcee fallback did not improve diagnostics "
                            "(ess=%s); keeping importance result", _em_ess,
                        )
                except Exception as exc:
                    logger.warning(
                        "compressed-emcee fallback failed (%s); keeping importance result",
                        exc,
                    )
    except Exception as exc:
        return _compressed_runner_unavailable(
            model_key=model_key,
            entries=entries,
            seed=seed,
            reason=f"Executable compressed likelihood failed ({exc}).",
        )
    summaries = {
        name: _posterior_summary(posterior_samples[:, index])
        for index, name in enumerate(parameter_order)
    }
    derived_samples: dict[str, np.ndarray] = {}
    # S8 is reported as a derived quantity (σ8·√(Ωm/0.3)) rather than a sampled
    # column, so its posterior is exactly consistent with the σ8/Ωm posterior.
    if _s8_is_derived(parameter_order):
        derived_samples["S8"] = _derived_s8_from_samples(posterior_samples, parameter_order)
        summaries["S8"] = _posterior_summary(derived_samples["S8"])
    if "H0" in parameter_order and "rd" in parameter_order:
        h0 = posterior_samples[:, parameter_order.index("H0")]
        rd = posterior_samples[:, parameter_order.index("rd")]
        derived_samples["H0_rd"] = h0 * rd
    derived_summaries = {
        name: _posterior_summary(values)
        for name, values in derived_samples.items()
    }
    for name in ("H0", "omegam", "rd", "sigma8", "S8"):
        if name in summaries:
            derived_summaries[name] = summaries[name]

    used_keys = {
        entry.key
        for entry in bao_entries + compressed_entries + cc_entries + rsd_entries + fsbao_entries + dr12_entries + grid_bao_entries + sn_entries + des_sn_entries
    }
    used_entries = [entry for entry in entries if entry.key in used_keys]
    # Each executable BAO entry contributes its own measurement vector (DESI DR1
    # = 12 points, 6dFGS+MGS = 2 points); cosmic chronometers contribute 31 H(z)
    # points; eBOSS RSD contributes 6 fσ8 points.  Derive from the vectors so a
    # dataset with a different length doesn't silently feed a wrong BIC penalty.
    n_constraints = (
        sum(len(_BAO_DATA[entry.key][0]) for entry in bao_entries)
        + sum(_cc_entry_point_count(entry) for entry in cc_entries)
        + len(EBOSS_DR16_FSIGMA8) * len(rsd_entries)
        + sum(len(_FSBAO_DATA[entry.key][0]) for entry in fsbao_entries)
        + sum(len(load_verified_dr12_consensus_data(entry.key)["mean_vector"] or ()) for entry in dr12_entries)
        + sum(1 if entry.key == "eboss_dr16_elg_bao" else 2 for entry in grid_bao_entries)
        + sum(len(_load_pantheon_plus_data()["mu"]) for entry in sn_entries if entry.key == "pantheon_plus")
        + sum(_offset_sn_n_points(entry.key) for entry in des_sn_entries)
        + sum(
            len(entry.compressed_likelihood.parameters)
            for entry in compressed_entries
            if entry.compressed_likelihood is not None
        )
    )
    k = len(parameter_order)
    aic = best_chi2 + 2.0 * k
    bic = best_chi2 + math.log(max(n_constraints, 1)) * k
    result_hash = _config_hash(
        model_key,
        [entry.key for entry in used_entries],
        {name: prior_bounds[name] for name in parameter_order},
        f"importance_bao:{seed}:{sample_count}:{proposal_draws}",
    )
    warnings = [
        (
            "In-process Gaussian mean/covariance runner (compressed-likelihood "
            "preliminary), not a full external desilike/Cobaya likelihood. See "
            "datasets_used for the exact release(s) fit."
        ),
    ]
    warnings.extend(_combination_warnings(entries))
    warnings.extend(_grid_support_warnings(grid_bao_entries, parameter_order, prior_bounds, seed))
    if (bao_entries or fsbao_entries or dr12_entries or grid_bao_entries) and not any(entry.probe == "cmb" for entry in used_entries):
        warnings.append(
            "BAO-only H0 and rd constraints are prior/calibration dependent; "
            "quote Omega_m or H0*rd more strongly than H0 alone."
        )
    if skipped_entries:
        warnings.append(
            "Datasets not run in compressed phase: "
            + ", ".join(entry.key for entry in skipped_entries)
            + ". Generate external Cobaya/CosmoSIS configs for those datasets."
        )
    if invalid_specs:
        warnings.extend(invalid_specs)
    # ess_unknown: the emcee autocorrelation estimate failed (NaN channel from
    # _run_emcee_chain) — convergence is UNVERIFIED, which is different from
    # "low": it blocks publication and caps the tier at exploratory below.
    ess_unknown = not math.isfinite(proposal_ess)
    if ess_unknown:
        warnings.append(
            f"{sampler_used} effective-sample-size estimate unavailable "
            "(autocorrelation time could not be estimated — pathological or "
            "too-short chain). Convergence is UNVERIFIED; result capped at "
            "exploratory."
        )
    elif proposal_ess < 400.0:
        warnings.append(
            f"{sampler_used} ESS={proposal_ess:.1f} below publication threshold 400."
        )

    cov_fidelity, artifact_sha256, fidelity_ok = _finalize_cov_fidelity(
        bao_entries + cc_entries + rsd_entries + fsbao_entries + dr12_entries + grid_bao_entries + sn_entries + des_sn_entries + compressed_entries, warnings
    )
    _executable_full_fidelity = not compressed_entries and cov_fidelity == "full"
    if _executable_full_fidelity:
        # Keep the headline warning consistent with the executable scope —
        # the generic text calls the run "compressed-likelihood preliminary",
        # which is false for a chain that fit only released, sha256-verified
        # full-fidelity products (2026-06-12 gate check).
        warnings[0] = (
            "In-process runner over released, sha256-verified full-fidelity "
            "likelihood products (no compressed Gaussian participated); still "
            "not an external desilike/Cobaya run. See datasets_used for the "
            "exact release(s) fit."
        )
    # Off-anchor safety guard: a converged extended-model chain whose novel frontier
    # parameters (w/w0/wa) have no reproduced published anchor is NOT publication-
    # ready — at most exploratory + routed to human review — so claim_validator
    # (posterior claims only when publication_ready) cannot let its w0/wa be quoted
    # as a published conclusion.  The chat tool runs these with emcee, so this guard
    # is what actually holds the "no off-anchor conclusions" line in the live path.
    from app.services.cosmology_oracle import chain_is_off_anchor
    off_anchor = chain_is_off_anchor(model_key, [entry.key for entry in used_entries])
    if off_anchor:
        warnings.append(
            "Off-anchor frontier parameters (w/w0/wa) have no reproduced published "
            "anchor; result is exploratory and routed to human review, not "
            "publication-ready."
        )
    # do_not_combine_with violation (e.g. the GA2018 31-pt CC compilation co-added
    # with its Moresco-2020 15-pt subset): the joint likelihood double-counts shared
    # measurements, so the posterior is methodologically invalid — block it outright
    # (not even exploratory), beyond the advisory _combination_warnings already added.
    combination_conflict = bool(_combination_warnings(entries))
    publication_ready = (
        not invalid_specs
        and not skipped_entries
        and proposal_ess >= 400.0
        and fidelity_ok
        and not off_anchor
        and not combination_conflict
    )
    # Importance-sampler three-tier (mirrors fit_cosmology_emcee, 2026-05-21):
    #   publication: ESS ≥ 400 and no invalid specs
    #   exploratory: 100 ≤ ESS < 400 — posterior discussable but not citeable
    #   blocked:     ESS < 100, invalid specs, a double-count conflict, OR data that
    #                failed sha256 verification (unverified/unstamped fidelity must
    #                never be discussable, not even as exploratory — anti-fabrication).
    if publication_ready:
        chain_tier = "publication"
    elif (
        fidelity_ok
        and not invalid_specs
        and not skipped_entries
        and not combination_conflict
        # ess_unknown (diagnostics failed) lands here, NOT in blocked: the
        # chain may be fine — only its convergence certificate is missing —
        # so the numbers stay discussable with the exploratory caveat.
        and (proposal_ess >= 100.0 or ess_unknown)
    ):
        chain_tier = "exploratory"
    else:
        chain_tier = "blocked"
    # Reason-aware exploratory warning: a chain lands in the exploratory tier
    # either because the importance-sampler ESS is below 400 OR because the
    # off-anchor frontier guard fired (w/w0/wa with no reproduced published
    # anchor) — and these are independent. The old string hard-coded
    # "ESS below 400" for every exploratory chain, which is literally false
    # when ESS >= 400 and the off-anchor guard is what demoted it.
    exploratory_reasons: list[str] = []
    if ess_unknown:
        exploratory_reasons.append(
            "the effective-sample-size estimate failed (autocorrelation time "
            "not estimable), so convergence is unverified"
        )
    elif proposal_ess < 400.0:
        exploratory_reasons.append(
            f"{sampler_used} ESS={proposal_ess:.0f} is below the publication threshold of 400"
        )
    if off_anchor:
        exploratory_reasons.append(
            "off-anchor frontier parameters (w/w0/wa) have no reproduced published anchor"
        )
    exploratory_warning = (
        "Exploratory chain (" + "; ".join(exploratory_reasons) + "). "
        "Posterior median and 1-sigma range may be discussed as exploratory, but MUST "
        "NOT be cited as a published constraint and MUST NOT be added to the bibcode pool."
        if chain_tier == "exploratory" else None
    )
    # Honest claim scope (2026-06-12): a chain where NO compressed Gaussian
    # participated and the aggregate fidelity is sha256-verified 'full' ran the
    # released likelihood products themselves — labeling it
    # 'compressed_likelihood_preliminary' was a lie that made the
    # full_likelihood_overclaim gate hard-block factually true replies (the
    # union3 22-bin upgrade exposed it; same F1-specificity class as 9f2667e).
    # (_executable_full_fidelity computed above, next to the headline warning.)
    # Tension diagnostics compare each dataset's PUBLISHED 1D anchor; executed
    # SN entries keep that anchor in compressed_likelihood even though it no
    # longer runs, so include them — otherwise union3 silently vanishes from
    # pairwise_tensions the moment it becomes executable.
    _tension_entries = compressed_entries + [
        e for e in (*sn_entries, *des_sn_entries)
        if e.compressed_likelihood is not None
    ]
    result: dict[str, Any] = {
        "success": True,
        "__tool_status__": (
            "COMPLETED" if chain_tier == "publication"
            else "EXPLORATORY" if chain_tier == "exploratory"
            else "PARTIAL"
        ),
        "analysis_status": (
            "COMPRESSED_CHAIN_READY" if chain_tier == "publication"
            else "EXPLORATORY" if chain_tier == "exploratory"
            else "PARTIAL"
        ),
        "publication_ready": publication_ready,
        "chain_tier": chain_tier,
        "off_anchor_review_required": off_anchor,
        "claim_scope": (
            "executable_full_fidelity_likelihoods"
            if _executable_full_fidelity
            else "compressed_likelihood_preliminary"
        ),
        "compressed_likelihood_preliminary": not _executable_full_fidelity,
        "model": model_key,
        "model_label": MODEL_LABELS[model_key],
        "sampler": sampler_used,
        "parameters": summaries,
        "posterior_summary": summaries,
        "derived_params": derived_summaries,
        "pairwise_tensions": _pairwise_tensions(_tension_entries),
        "fit_statistics": {
            "chi2": round(best_chi2, 6),
            # delta_chi2 was a hard-coded 0.0 placeholder here for years — a
            # meaningless number masquerading as a computed statistic ("wCDM
            # gives delta_chi2=0, no improvement"). Model comparison lives in
            # compute_model_comparison, which fits BOTH models on the SAME
            # datasets; a single run has no baseline to difference against.
            "aic": round(float(aic), 6),
            "bic": round(float(bic), 6),
            "n_constraints": int(n_constraints),
            "n_parameters": int(k),
        },
        "chain_diagnostics": {
            "overall_status": (
                "emcee_sampled" if sampler_used in ("compressed_emcee", "sn_emcee")
                else "importance_resampled"
            ),
            "publication_ready": publication_ready,
            # R-hat is NOT computed on this runner (importance resampling has
            # no MCMC chains; the emcee bypass returns one flattened ensemble).
            # It was hard-coded 1.0 for years — a never-computed convergence
            # statistic inside the provenance envelope (2026-06-12 review).
            # Per-parameter R-hat lives on the external cobaya path.
            "rhat": None,
            "rhat_note": "not computed on the in-process runner",
            "ess_bulk": (
                None if ess_unknown else int(min(round(proposal_ess), sample_count))
            ),
            "ess_source": (
                "autocorr_failed" if ess_unknown
                else (
                    "emcee_autocorr"
                    if sampler_used in ("compressed_emcee", "sn_emcee")
                    else "importance_weights"
                )
            ),
            "proposal_ess": None if ess_unknown else round(proposal_ess, 3),
            "proposal_draws": int(proposal_draws),
            "n_draws": sample_count,
            "n_chains": 1,
            "thresholds": {"ess_min": 400},
        },
        "datasets_used": [entry.to_dict() for entry in used_entries],
        "datasets_not_run": [entry.to_dict() for entry in skipped_entries],
        "dataset_keys": [entry.key for entry in entries],
        "priors": {name: list(bounds) for name, bounds in prior_bounds.items()},
        "random_seed": seed,
        "n_samples": sample_count,
        "runner_hash": result_hash,
        "warnings": warnings,
        "__message_to_model__": (
            "This is a publication-ready compressed-likelihood preliminary result "
            "when publication_ready=true. Quote posterior numbers only with that "
            "caveat and only for datasets_used; do not claim datasets_not_run were "
            "included in the numerical posterior."
        ),
        "provenance": {
            "cosmology_likelihood": {
                "registry_version": "2026-04-30",
                "runner": sampler_used,
                "runner_hash": result_hash,
                "dataset_keys": [entry.key for entry in entries],
                "datasets_used": [entry.key for entry in used_entries],
                "datasets_not_run": [entry.key for entry in skipped_entries],
                "citations": _collect_citations(entries),
                "compressed_sources": _sampling_source_records(used_entries),
                "cov_fidelity": cov_fidelity,
                "artifact_sha256": artifact_sha256,
                "publication_ready": publication_ready,
            },
        },
    }
    # Per-tier rewrite of __message_to_model__ — keep the publication text
    # only for the publication tier; for exploratory and blocked, replace
    # with tier-specific guidance (mirrors fit_cosmology_emcee per-tier
    # message handling; bug_013 review fix: otherwise the result dict's
    # publication-tier instructions contradict the chain_tier badge).
    if chain_tier == "exploratory" and exploratory_warning is not None:
        result["__exploratory_warning__"] = exploratory_warning
        result["warnings"] = list(result.get("warnings") or []) + [exploratory_warning]
        result["__message_to_model__"] = (
            exploratory_warning
            + " When reporting numbers, prefix with 'exploratory' and refuse "
            "phrasings like 'we find H0 =' or 'our constraint is'."
        )
    elif chain_tier == "blocked":
        result["__do_not_claim__"] = True
        blocked_reason = (
            f"Importance sampler ESS={proposal_ess:.0f} below exploratory floor 100"
            if not invalid_specs and proposal_ess < 100.0
            else "Requested datasets were not numerically included: "
            + ", ".join(entry.key for entry in skipped_entries)
            if skipped_entries
            else "Invalid compressed-likelihood specs: " + "; ".join(invalid_specs)
            if invalid_specs
            else "Chain did not reach a quotable posterior"
        )
        result["__message_to_model__"] = (
            blocked_reason
            + ". Do not cite H0, Om0, w0, wa, sigma8, S8, HDI, or posterior "
            "constraints from this result."
        )
    return result


def _sampling_parameter_order(
    bao_entries: list[CosmologyDatasetEntry],
    compressed_entries: list[CosmologyDatasetEntry],
    sn_entries: list[CosmologyDatasetEntry] | None = None,
    *,
    model_key: str = "lcdm",
    cc_entries: list[CosmologyDatasetEntry] | None = None,
    rsd_entries: list[CosmologyDatasetEntry] | None = None,
    fsbao_entries: list[CosmologyDatasetEntry] | None = None,
    dr12_entries: list[CosmologyDatasetEntry] | None = None,
    grid_bao_entries: list[CosmologyDatasetEntry] | None = None,
    des_sn_entries: list[CosmologyDatasetEntry] | None = None,
) -> list[str]:
    order: list[str] = []
    if bao_entries:
        order.extend(["H0", "omegam", "rd"])
    if des_sn_entries:
        # Offset-marginalized SN family (DES-SN5YR, Union3): the χ² analytically
        # marginalizes the SN offset (and H0), so it constrains Ωm (+ the w0/wa
        # DE shape) only — no H0, no M_B.
        if "omegam" not in order:
            order.append("omegam")
    if fsbao_entries:
        # FSBAO measures joint (D_M/r_s, D_H/r_s, fσ8): distance ratios need
        # (H0, Ωm, r_d), the fσ8 growth term adds σ8.
        for param in ("H0", "omegam", "rd", "sigma8"):
            if param not in order:
                order.append(param)
    if dr12_entries:
        # DR12 consensus BAO measures (D_M·rs_fid/r_d, H·r_d/rs_fid) — pure
        # distance/expansion, same parameter set as BAO, no growth term.
        for param in ("H0", "omegam", "rd"):
            if param not in order:
                order.append(param)
    if grid_bao_entries:
        # eBOSS DR16 grid surfaces measure dimensionless distance ratios
        # (D_V/r_d or D_M/r_d + D_H/r_d) — same parameter set as BAO.
        for param in ("H0", "omegam", "rd"):
            if param not in order:
                order.append(param)
    if cc_entries:
        # Cosmic chronometers measure H(z) = H0·E(z), constraining (H0, Ωm).
        for param in ("H0", "omegam"):
            if param not in order:
                order.append(param)
    if rsd_entries:
        # RSD fσ8(z) = f(z)·σ8·D(z)/D(0) is H0-independent; it constrains the
        # growth combination of (Ωm, σ8) [+ the DE EoS via γ(w)].
        for param in ("omegam", "sigma8"):
            if param not in order:
                order.append(param)
    if sn_entries:
        # Pantheon+ chi² needs (H0, Ωm, M_B). H0 / Ωm overlap with BAO/CMB;
        # M_B (SN Ia absolute-magnitude nuisance) is unique to SN.
        for param in ("H0", "omegam", "M_B"):
            if param not in order:
                order.append(param)
    # Dark-energy parameters per model. SUPPORTED_MODELS[model_key] lists the
    # canonical parameter set ("w" for wCDM, "w0"/"wa" for CPL).
    for param in SUPPORTED_MODELS.get(model_key, ()):
        if param in {"w", "w0", "wa"} and param not in order:
            order.append(param)
    for param in _compressed_parameter_order(compressed_entries):
        if param not in order:
            order.append(param)
    # The planck2018_compressed dp_flat branch in _compressed_chi2_samples executes
    # the correlated CHW2019 (R, l_A, ombh2, ns) distance priors on EVERY flat model
    # (2026-07-07; previously extended-DE only), and that prior reads sampled ombh2
    # and ns columns. Both are deliberately kept out of RUNNER_PARAMETER_PRIORS /
    # _compressed_parameter_order, so add them here exactly when that branch will
    # fire — otherwise the prior is unreachable and Planck silently contributes zero
    # chi2. Keep this predicate in lockstep with the dp_flat gate in
    # _compressed_chi2_samples.
    planck_dp_flat = (
        any(e.key == "planck2018_compressed" for e in compressed_entries)
        and "omegak" not in order
    )
    if planck_dp_flat:
        for param in ("ombh2", "ns"):
            if param not in order:
                order.append(param)
    preferred = ["H0", "omegam", "rd", "w", "w0", "wa", "sigma8", "S8", "M_B"]
    ordered = [param for param in preferred if param in order] + [
        param for param in order if param not in preferred
    ]
    return _drop_derived_s8(ordered)


def _draw_uniform_prior_samples(
    rng: np.random.Generator,
    parameter_order: list[str],
    prior_bounds: dict[str, tuple[float, float]],
    count: int,
) -> np.ndarray:
    samples = np.empty((count, len(parameter_order)), dtype=float)
    for index, name in enumerate(parameter_order):
        low, high = prior_bounds[name]
        samples[:, index] = rng.uniform(low, high, size=count)
    return samples


def _draw_desi_bao_only_posterior(
    rng: np.random.Generator,
    parameter_order: list[str],
    prior_bounds: dict[str, tuple[float, float]],
    sample_count: int,
    key: str = "desi_dr1_bao",
) -> tuple[np.ndarray, float, float, int]:
    """Sample a single DESI BAO-only posterior (DR1 or DR2) in the natural H0*rd
    degeneracy plane."""
    if parameter_order != ["H0", "omegam", "rd"]:
        raise ValueError("DESI BAO-only runner expects H0, omegam, rd parameters")
    h0_low, h0_high = prior_bounds["H0"]
    om_low, om_high = prior_bounds["omegam"]
    rd_low, rd_high = prior_bounds["rd"]
    q_low = h0_low * rd_low
    q_high = h0_high * rd_high
    # Grid resolution sets how faithfully the (Ωm, H0·rd) posterior is represented;
    # DESI DR2's tighter 13-point likelihood needs a denser grid than DR1's to keep
    # the effective sample size above the publication floor (the superseding, more
    # constraining dataset must not land in a weaker chain tier than DR1).
    om_values = np.linspace(om_low, om_high, 600)
    q_values = np.linspace(q_low, q_high, 1200)
    om_grid, q_grid = np.meshgrid(om_values, q_values, indexing="ij")
    flat_om = om_grid.ravel()
    flat_q = q_grid.ravel()

    h0_cond_low = np.maximum(h0_low, flat_q / rd_high)
    h0_cond_high = np.minimum(h0_high, flat_q / rd_low)
    valid = h0_cond_high > h0_cond_low
    if not np.any(valid):
        raise ValueError("H0*rd grid has no support inside the configured H0/rd priors")

    grid_samples = np.column_stack([
        np.ones(np.count_nonzero(valid), dtype=float),
        flat_om[valid],
        flat_q[valid],
    ])
    chi2 = _bao_chi2_samples(grid_samples, parameter_order, key)
    best_chi2 = float(np.min(chi2))
    marginal_h0_prior = np.log(h0_cond_high[valid] / h0_cond_low[valid])
    log_weights = -0.5 * (chi2 - best_chi2) + np.log(marginal_h0_prior)
    log_weights -= float(np.max(log_weights))
    weights = np.exp(np.clip(log_weights, -745.0, 0.0))
    weight_sum = float(np.sum(weights))
    if not math.isfinite(weight_sum) or weight_sum <= 0.0:
        raise ValueError("DESI BAO-only posterior weights underflowed")

    probabilities = weights / weight_sum
    proposal_ess = float(weight_sum * weight_sum / np.sum(weights * weights))
    chosen = rng.choice(grid_samples.shape[0], size=sample_count, replace=True, p=probabilities)
    chosen_q = grid_samples[chosen, 2]
    chosen_om = grid_samples[chosen, 1]
    low = np.maximum(h0_low, chosen_q / rd_high)
    high = np.minimum(h0_high, chosen_q / rd_low)
    chosen_h0 = np.exp(rng.uniform(np.log(low), np.log(high)))
    chosen_rd = chosen_q / chosen_h0
    posterior = np.column_stack([chosen_h0, chosen_om, chosen_rd])
    return posterior, best_chi2, proposal_ess, int(grid_samples.shape[0])


def _draw_gaussian_centered_proposal(
    rng: np.random.Generator,
    parameter_order: list[str],
    prior_bounds: dict[str, tuple[float, float]],
    compressed_entries: list[CosmologyDatasetEntry],
    proposal_count: int,
    *,
    inflation: float = 2.5,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Build a multivariate-Gaussian importance proposal centered on the
    tightest compressed-likelihood entry overlapping ``parameter_order``.

    Returns ``(samples, log_proposal_pdf)`` so the caller can apply the
    importance correction ``log_w = -0.5 chi² - log q(theta)``.

    Returns ``None`` when there is no compressed Gaussian to anchor on, or
    when the chosen Gaussian's prior overlap is too small after rejection
    (caller should fall back to uniform proposal).

    Picks the tightest entry by sum-of-normalized-variances (per-parameter
    σ relative to its prior box width) so we don't compare H0 σ in km/s/Mpc
    to Ωm σ in dimensionless.  Covariance is scaled by ``inflation² = 6.25``
    so each Gaussian dim has σ_proposal = 2.5·σ_target.  Per-dim importance
    efficiency ≈ σ_t/σ_p = 0.4; on prod's 4-Gaussian-dim path
    (Planck H0/Ωm/σ8/S8) this gives 0.4⁴ ≈ 2.5%, ESS ≈ 2500 from a 100k
    proposal.  Inflation=5 collapsed to 0.04% / ESS≈30 because
    (1/5)⁴ ≈ 1.6e-3.  BAO+Planck combined σ_H0 ≈ 0.5 (Planck-dominated),
    so 2.5σ_Planck = 1.35 still covers the joint posterior comfortably.

    planck2018_compressed (2026-07-07, correlated distance-prior upgrade):
    the executed CMB term is the nonlinear CHW2019 (R, l_A, ombh2, ns) prior,
    whose ΛCDM parameter-space image is a strongly correlated ridge an
    axis-independent proposal cannot cover (measured ESS 21 vs the 400
    publication floor).  For flat ΛCDM the (H0, omegam, ombh2, ns) block is
    therefore proposed jointly from the linearized prior
    (``_planck_dp_lcdm_proposal_moments``); for flat extended-DE models the
    ombh2/ns axes get independent Table-I-anchored Gaussians (the DE target
    is degeneracy-widened anyway and relies on the emcee upgrade).  Proposal
    densities are subtracted exactly, so these anchors can never bias the
    posterior — a bad anchor only costs ESS.
    """
    best_entry: CosmologyDatasetEntry | None = None
    best_trace = math.inf
    best_local_idx: list[int] = []
    best_sample_idx: list[int] = []

    for entry in compressed_entries:
        spec = entry.compressed_likelihood
        if spec is None:
            continue
        params = list(spec.parameters)
        names = [n for n in params if n in parameter_order]
        if not names:
            continue
        local_idx = [params.index(n) for n in names]
        cov = np.asarray(spec.covariance, dtype=float)[np.ix_(local_idx, local_idx)]
        scales = np.array(
            [prior_bounds[n][1] - prior_bounds[n][0] for n in names], dtype=float
        )
        if not np.all(scales > 0):
            continue
        normalized_diag = np.diagonal(cov) / (scales ** 2)
        trace_norm = float(np.sum(normalized_diag))
        if trace_norm < best_trace:
            best_entry = entry
            best_trace = trace_norm
            best_local_idx = local_idx
            best_sample_idx = [parameter_order.index(n) for n in names]

    # Assemble independent proposal blocks; every axis not covered by a block
    # draws uniform from the prior box.  A block is (indices, mean,
    # [(weight, covariance), ...]) — a Gaussian mixture sharing one mean.
    # Each covariance below is ALREADY inflation-scaled.
    planck_selected = any(e.key == "planck2018_compressed" for e in compressed_entries)
    blocks: list[tuple[list[int], np.ndarray, list[tuple[float, np.ndarray]]]] = []

    dp_moments = _planck_dp_lcdm_proposal_moments() if planck_selected else None
    dp_lcdm = (
        dp_moments is not None
        and all(name in parameter_order for name in dp_moments[0])
        and not any(p in parameter_order for p in ("w", "w0", "wa", "omegak"))
    )
    if dp_lcdm and dp_moments is not None:
        # Flat ΛCDM: propose (H0, omegam, ombh2, ns) jointly from the
        # linearized distance prior (see docstring).  Two extra tight axes
        # (ombh2, ns) mean the flat 2.5x inflation cannot reach the 400 ESS
        # publication floor even with perfect shape matching (0.54^6), so the
        # DP block runs a tighter 1.8x core carrying 90% of the mass plus a
        # 4x defensive tail (10%) that keeps the weights bounded when a
        # co-selected probe (BAO) shifts the joint posterior off the
        # DP-only center.  Mixture density is exact below — never a bias.
        dp_names, dp_mean, dp_cov = dp_moments
        blocks.append((
            [parameter_order.index(n) for n in dp_names],
            dp_mean,
            [(0.9, dp_cov * (1.8 ** 2)), (0.1, dp_cov * (4.0 ** 2))],
        ))
        planck_spec = next(
            e.compressed_likelihood
            for e in compressed_entries
            if e.key == "planck2018_compressed" and e.compressed_likelihood is not None
        )
        spec_params = list(planck_spec.parameters)
        if "sigma8" in parameter_order and "sigma8" in spec_params:
            # sigma8 is constrained only through the derived-S8 growth row
            # (S8 = sigma8*sqrt(omegam/0.3)), so its posterior width is the
            # S8 row width propagated back — wider than the spec's direct
            # sigma8 row.  Anchor the mean on the sigma8 row but take the
            # LARGER of the two widths so the proposal never under-covers.
            spec_mean = np.asarray(planck_spec.mean, dtype=float)
            spec_cov = np.asarray(planck_spec.covariance, dtype=float)
            k = spec_params.index("sigma8")
            s8_sigma = math.sqrt(float(spec_cov[k][k]))
            if "S8" in spec_params and "omegam" in spec_params:
                ks8 = spec_params.index("S8")
                kom = spec_params.index("omegam")
                propagated = math.sqrt(float(spec_cov[ks8][ks8])) / math.sqrt(
                    float(spec_mean[kom]) / S8_PIVOT_OMEGAM
                )
                s8_sigma = max(s8_sigma, propagated)
            blocks.append((
                [parameter_order.index("sigma8")],
                np.array([float(spec_mean[k])]),
                [(1.0, np.array([[(inflation * s8_sigma) ** 2]]))],
            ))
    else:
        if best_entry is None:
            return None
        spec = best_entry.compressed_likelihood
        assert spec is not None  # narrowed by the search above
        blocks.append((
            best_sample_idx,
            np.asarray(spec.mean, dtype=float)[best_local_idx],
            [(1.0, np.asarray(spec.covariance, dtype=float)[
                np.ix_(best_local_idx, best_local_idx)
            ] * (inflation ** 2))],
        ))
        # Flat extended-DE models: the distance-prior axes (ombh2, ns) carry
        # no Gaussian row in any spec — their constraint lives only in the
        # nonlinear CHW2019 prior — and a uniform proposal over the full
        # prior box collapses the importance ESS (sigma_ombh2 = 1.5e-4
        # against a 6e-3-wide box).  Anchor them independently on the same
        # paper-verified Table-I moments the target chi2 uses.
        if planck_selected:
            for name, (dp_mean, dp_sigma) in PLANCK18_DP_PROPOSAL_MOMENTS.items():
                if name in parameter_order:
                    idx = parameter_order.index(name)
                    if not any(idx in blk_idx for blk_idx, _, _ in blocks):
                        blocks.append((
                            [idx],
                            np.array([dp_mean]),
                            [(1.0, np.array([[(inflation * dp_sigma) ** 2]]))],
                        ))

    prepared: list[tuple[list[int], np.ndarray, list[tuple[float, np.ndarray, np.ndarray, float]]]] = []
    for blk_idx, blk_mean, components in blocks:
        comps: list[tuple[float, np.ndarray, np.ndarray, float]] = []
        for weight, comp_cov in components:
            # Numerical jitter so multivariate_normal doesn't refuse
            # near-singular covariance for one-parameter (1×1) Gaussians.
            comp_cov = comp_cov + np.eye(len(blk_idx)) * 1e-12
            sign, logdet = np.linalg.slogdet(comp_cov)
            if sign <= 0 or not math.isfinite(logdet):
                return None
            comps.append((weight, comp_cov, np.linalg.inv(comp_cov), logdet))
        prepared.append((blk_idx, blk_mean, comps))

    # Reject anything outside the prior box.  An inflated Gaussian centered
    # well inside the box should keep ≥80% of draws; if it does not (e.g.
    # Gaussian mean near the edge), bail and let the caller use uniform.
    over = max(int(proposal_count * 1.5), proposal_count + 1)
    samples = np.empty((over, len(parameter_order)), dtype=float)
    covered = {idx for blk_idx, *_ in prepared for idx in blk_idx}
    for i, name in enumerate(parameter_order):
        if i in covered:
            continue
        low, high = prior_bounds[name]
        samples[:, i] = rng.uniform(low, high, size=over)
    for blk_idx, blk_mean, comps in prepared:
        if len(comps) == 1:
            draws = rng.multivariate_normal(blk_mean, comps[0][1], size=over)
        else:
            weights = np.array([w for w, *_ in comps], dtype=float)
            choice = rng.choice(len(comps), size=over, p=weights / weights.sum())
            draws = np.empty((over, len(blk_idx)), dtype=float)
            for c, (_, comp_cov, _, _) in enumerate(comps):
                mask = choice == c
                if np.any(mask):
                    draws[mask] = rng.multivariate_normal(
                        blk_mean, comp_cov, size=int(mask.sum())
                    )
        for k, idx in enumerate(blk_idx):
            samples[:, idx] = draws[:, k]

    in_box = np.ones(over, dtype=bool)
    for i, name in enumerate(parameter_order):
        low, high = prior_bounds[name]
        in_box &= (samples[:, i] >= low) & (samples[:, i] <= high)
    samples = samples[in_box]
    if samples.shape[0] < proposal_count:
        return None
    samples = samples[:proposal_count]

    log_q = np.zeros(samples.shape[0], dtype=float)
    for blk_idx, blk_mean, comps in prepared:
        diffs = samples[:, blk_idx] - blk_mean
        comp_logpdfs = np.stack([
            -0.5 * np.einsum("ni,ij,nj->n", diffs, comp_inv, diffs)
            - 0.5 * comp_logdet
            - 0.5 * len(blk_idx) * np.log(2.0 * np.pi)
            + math.log(weight)
            for weight, _, comp_inv, comp_logdet in comps
        ])
        peak = np.max(comp_logpdfs, axis=0)
        log_q += peak + np.log(np.sum(np.exp(comp_logpdfs - peak), axis=0))
    # Uniform-prior dimensions contribute a constant log(1/(high-low)) which
    # cancels out of the importance ratio; intentionally omitted.
    return samples, log_q


def _run_emcee_chain(
    seed: int,
    parameter_order: list[str],
    prior_bounds: dict[str, tuple[float, float]],
    bao_entries: list[CosmologyDatasetEntry],
    compressed_entries: list[CosmologyDatasetEntry],
    sn_entries: list[CosmologyDatasetEntry],
    target_sample_count: int,
    cc_entries: list[CosmologyDatasetEntry] | None = None,
    rsd_entries: list[CosmologyDatasetEntry] | None = None,
    fsbao_entries: list[CosmologyDatasetEntry] | None = None,
    dr12_entries: list[CosmologyDatasetEntry] | None = None,
    grid_bao_entries: list[CosmologyDatasetEntry] | None = None,
    des_sn_entries: list[CosmologyDatasetEntry] | None = None,
) -> tuple[np.ndarray, float, float, int, list[str]]:
    """emcee MCMC over any BAO + compressed + SN likelihood product.

    Importance sampling collapses on tight posteriors — both Pantheon+'s
    1701-SN χ² ridge and 3+ probe products whose joint posterior is far
    narrower than any proposal Gaussian.  emcee ensemble sampling handles
    these naturally; at 32 walkers × ~1500 post-burn steps the posterior ESS
    is in the hundreds-to-thousands.  Used both for the full-χ² SN path and as
    the single-cell ESS-floor fallback for compressed multi-probe chains.

    Returns the same tuple as ``_draw_importance_posterior`` for drop-in
    substitution: ``(samples, best_chi2, effective_sample_size, n_draws,
    compressed_errors)``.
    """
    cc_entries = cc_entries or []
    rsd_entries = rsd_entries or []
    fsbao_entries = fsbao_entries or []
    des_sn_entries = des_sn_entries or []
    import emcee

    rng = np.random.default_rng(seed)
    ndim = len(parameter_order)
    n_walkers = max(2 * ndim + 2, 32)
    # Burn-in + post-burn budget.  Pantheon+'s χ² has integrated
    # autocorrelation time τ ≈ 25-30 steps, and emcee's documentation
    # recommends ≥ 50τ for trustworthy posterior — i.e. ≥ 1500 post-burn
    # steps per walker.  With 32 walkers that produces ~48k posterior
    # samples (ESS in the thousands after thinning).  Now that
    # _flat_lcdm_dm_grid_vectorized eliminated the 1701-call Python loop,
    # this completes in ~60-120s instead of 11 minutes.
    n_burn = 400
    n_steps = max(n_burn + 1500, n_burn + max(target_sample_count // n_walkers + 100, 1500))

    # Init walkers in a small ball around a sensible center.  Different
    # parameter names get different default centers because Pantheon+ /
    # Planck disagree on H0 — we sit between them.
    init_centers = {
        "H0": 70.0,
        "omegam": 0.31,
        "rd": 147.0,
        "M_B": -19.4,
        "sigma8": 0.81,
        "S8": 0.83,
        # Distance-prior axes (CHW2019 Table-I means): the targets are far
        # tighter than the prior box, so starting walkers at the box middle
        # wastes most of the burn-in walking to the mode.
        "ombh2": PLANCK18_DP_PROPOSAL_MOMENTS["ombh2"][0],
        "ns": PLANCK18_DP_PROPOSAL_MOMENTS["ns"][0],
    }
    center = np.empty(ndim, dtype=float)
    for i, name in enumerate(parameter_order):
        low, high = prior_bounds[name]
        candidate = init_centers.get(name, 0.5 * (low + high))
        center[i] = float(np.clip(candidate, low + 0.01 * (high - low), high - 0.01 * (high - low)))
    scale = np.array(
        [(prior_bounds[name][1] - prior_bounds[name][0]) * 0.02 for name in parameter_order],
        dtype=float,
    )
    p0 = center + rng.normal(size=(n_walkers, ndim)) * scale
    for i, name in enumerate(parameter_order):
        low, high = prior_bounds[name]
        p0[:, i] = np.clip(p0[:, i], low + 1e-4 * (high - low), high - 1e-4 * (high - low))

    compressed_errors: list[str] = []

    def log_prob_batch(theta_batch: np.ndarray) -> np.ndarray:
        if theta_batch.ndim == 1:
            theta_batch = theta_batch[None, :]
        n_w = theta_batch.shape[0]
        in_box = np.ones(n_w, dtype=bool)
        for i, name in enumerate(parameter_order):
            low, high = prior_bounds[name]
            in_box &= (theta_batch[:, i] >= low) & (theta_batch[:, i] <= high)
        result = np.full(n_w, -np.inf, dtype=float)
        if not np.any(in_box):
            return result
        valid = theta_batch[in_box]
        chi2 = np.zeros(valid.shape[0], dtype=float)
        for entry in bao_entries:
            chi2 += _bao_chi2_samples(valid, parameter_order, entry.key)
        for entry in cc_entries:
            if entry.key == "cosmic_chronometers":
                chi2 += _cosmic_chronometer_chi2_samples(valid, parameter_order)
            elif entry.key == "cosmic_chronometers_moresco20":
                chi2 += _cosmic_chronometer_moresco20_chi2_samples(valid, parameter_order)
            else:
                raise ValueError(f"executable CC entry {entry.key!r} has no chi2 dispatch")
        for entry in rsd_entries:
            if entry.key == "eboss_dr16_rsd":
                chi2 += _eboss_fsigma8_chi2_samples(valid, parameter_order)
            else:
                raise ValueError(f"executable RSD entry {entry.key!r} has no chi2 dispatch")
        for entry in fsbao_entries:
            chi2 += _fsbao_chi2_samples(valid, parameter_order, entry.key)
        for entry in dr12_entries:
            chi2 += _dr12_chi2_samples(valid, parameter_order, entry.key)
        for entry in grid_bao_entries:
            chi2 += _grid_bao_chi2_samples(valid, parameter_order, entry.key)
        for entry in sn_entries:
            if entry.key == "pantheon_plus":
                chi2 += _pantheon_plus_chi2_samples(valid, parameter_order)
            else:
                raise ValueError(f"executable SN entry {entry.key!r} has no chi2 dispatch")
        for entry in des_sn_entries:
            chi2 += _offset_sn_chi2_samples(valid, parameter_order, entry.key)
        extra_chi2, errs = _compressed_chi2_samples(
            valid, parameter_order, compressed_entries
        )
        chi2 += extra_chi2
        if errs:
            compressed_errors.extend(errs)
        result[in_box] = -0.5 * chi2
        return result

    sampler = emcee.EnsembleSampler(n_walkers, ndim, log_prob_batch, vectorize=True)
    # Seed the chain evolution deterministically (emcee otherwise draws stretch
    # moves from numpy's global RNG, making random_seed / runner_hash a false
    # reproducibility claim). Mirrors cosmology_mcmc.fit_cosmology_emcee.
    try:
        sampler.random_state = np.random.RandomState(seed).get_state()
    except Exception:
        logger.debug("emcee sampler random_state could not be set explicitly", exc_info=True)
    sampler.run_mcmc(p0, n_steps, progress=False)

    chain = sampler.get_chain(discard=n_burn, flat=True)  # (n_walkers*(n_steps-n_burn), ndim)
    log_probs = sampler.get_log_prob(discard=n_burn, flat=True)
    finite = np.isfinite(log_probs)
    chain = chain[finite]
    log_probs = log_probs[finite]
    if chain.shape[0] == 0:
        raise ValueError("emcee chain produced no finite log-probability draws")

    best_chi2 = float(-2.0 * np.max(log_probs))

    # Sub-sample if we have more than target.
    if chain.shape[0] > target_sample_count:
        idx = rng.choice(chain.shape[0], size=target_sample_count, replace=False)
        chain_out = chain[idx]
    else:
        chain_out = chain

    # Effective sample size — median across parameters using autocorrelation
    # length when available, else conservative fallback.
    try:
        tau = sampler.get_autocorr_time(quiet=True, discard=n_burn)
        n_draws_total = (n_steps - n_burn) * n_walkers
        # get_autocorr_time returns NaN for (near-)zero-variance parameters
        # (e.g. a walker pinned to a prior edge). np.max would propagate the
        # NaN into ess_estimate and later crash int(round(nan)) at the caller.
        max_tau = float(np.nanmax(tau))
        if not math.isfinite(max_tau) or max_tau <= 0:
            raise ValueError("non-finite autocorrelation time")
        ess_estimate = float(n_draws_total / max(max_tau, 1.0))
    except Exception:
        # 2026-06-12 review: the old n/10 fallback (~4800 for a default chain)
        # sailed OVER the 400 publication floor at the exact moment the ESS
        # measurement FAILED — promoting an unverifiable chain to the highest
        # confidence tier. The honest value is "unknown": NaN propagates to the
        # runner, which reports ess=None, warns, and caps the tier at
        # exploratory (never publication).
        ess_estimate = float("nan")
    return chain_out, best_chi2, ess_estimate, int(chain.shape[0]), list(set(compressed_errors))


def _draw_importance_posterior(
    rng: np.random.Generator,
    parameter_order: list[str],
    prior_bounds: dict[str, tuple[float, float]],
    bao_entries: list[CosmologyDatasetEntry],
    compressed_entries: list[CosmologyDatasetEntry],
    sample_count: int,
    *,
    sn_entries: list[CosmologyDatasetEntry] | None = None,
    cc_entries: list[CosmologyDatasetEntry] | None = None,
    rsd_entries: list[CosmologyDatasetEntry] | None = None,
    fsbao_entries: list[CosmologyDatasetEntry] | None = None,
    dr12_entries: list[CosmologyDatasetEntry] | None = None,
    grid_bao_entries: list[CosmologyDatasetEntry] | None = None,
    des_sn_entries: list[CosmologyDatasetEntry] | None = None,
    allow_emcee_fallback: bool = True,
) -> tuple[np.ndarray, float, float, int, list[str]]:
    sn_entries = sn_entries or []
    cc_entries = cc_entries or []
    rsd_entries = rsd_entries or []
    fsbao_entries = fsbao_entries or []
    dr12_entries = dr12_entries or []
    grid_bao_entries = grid_bao_entries or []
    des_sn_entries = des_sn_entries or []
    # SN paths may bypass importance sampling with emcee — see
    # _sn_emcee_bypass_active for the policy (expensive full vectors always;
    # the cheap union3 22-bin set only when the caller allows emcee).
    if _sn_emcee_bypass_active(sn_entries, des_sn_entries, allow_emcee_fallback):
        # Use the rng's bit-state to seed emcee deterministically.
        emcee_seed = int(rng.integers(0, 2**31 - 1))
        return _run_emcee_chain(
            emcee_seed,
            parameter_order,
            prior_bounds,
            bao_entries,
            compressed_entries,
            sn_entries,
            sample_count,
            cc_entries=cc_entries,
            rsd_entries=rsd_entries,
            fsbao_entries=fsbao_entries,
            dr12_entries=dr12_entries,
            grid_bao_entries=grid_bao_entries,
            des_sn_entries=des_sn_entries,
        )

    proposal_count = min(max(sample_count * 25, 80_000), 300_000)

    # No SN entries: legacy importance-sampling path (delivers ESS > 400 for
    # BAO+CMB workflows since the W2 fix in commit 6c829df).
    # Gaussian-centered single-component proposal when any compressed entry
    # has a Gaussian likelihood overlapping the sampling parameters;
    # otherwise fall back to uniform-prior proposal.
    gaussian_proposal = _draw_gaussian_centered_proposal(
        rng, parameter_order, prior_bounds, compressed_entries, proposal_count,
    )
    if gaussian_proposal is not None:
        samples, log_proposal_pdf = gaussian_proposal
    else:
        samples = _draw_uniform_prior_samples(
            rng, parameter_order, prior_bounds, proposal_count
        )
        log_proposal_pdf = np.zeros(samples.shape[0], dtype=float)

    chi2 = np.zeros(samples.shape[0], dtype=float)
    for entry in bao_entries:
        chi2 += _bao_chi2_samples(samples, parameter_order, entry.key)
    for entry in cc_entries:
        if entry.key == "cosmic_chronometers":
            chi2 += _cosmic_chronometer_chi2_samples(samples, parameter_order)
        elif entry.key == "cosmic_chronometers_moresco20":
            chi2 += _cosmic_chronometer_moresco20_chi2_samples(samples, parameter_order)
        else:
            raise ValueError(f"executable CC entry {entry.key!r} has no chi2 dispatch")
    for entry in rsd_entries:
        if entry.key == "eboss_dr16_rsd":
            chi2 += _eboss_fsigma8_chi2_samples(samples, parameter_order)
        else:
            raise ValueError(f"executable RSD entry {entry.key!r} has no chi2 dispatch")
    for entry in fsbao_entries:
        chi2 += _fsbao_chi2_samples(samples, parameter_order, entry.key)
    for entry in (dr12_entries or []):
        chi2 += _dr12_chi2_samples(samples, parameter_order, entry.key)
    for entry in (grid_bao_entries or []):
        chi2 += _grid_bao_chi2_samples(samples, parameter_order, entry.key)
    for entry in (sn_entries or []):
        if entry.key == "pantheon_plus":
            chi2 += _pantheon_plus_chi2_samples(samples, parameter_order)
        else:
            raise ValueError(f"executable SN entry {entry.key!r} has no chi2 dispatch")
    for entry in (des_sn_entries or []):
        chi2 += _offset_sn_chi2_samples(samples, parameter_order, entry.key)
    extra_chi2, compressed_errors = _compressed_chi2_samples(
        samples,
        parameter_order,
        compressed_entries,
    )
    chi2 += extra_chi2
    finite = np.isfinite(chi2) & np.isfinite(log_proposal_pdf)
    if not np.any(finite):
        raise ValueError("importance sampler produced no finite likelihood values")
    samples = samples[finite]
    chi2 = chi2[finite]
    log_proposal_pdf = log_proposal_pdf[finite]
    best_chi2 = float(np.min(chi2))
    if best_chi2 >= 0.5 * MGS_OUT_OF_BOUNDS_CHI2:
        # Every proposal sample carries an out-of-bounds likelihood penalty
        # (e.g. the whole prior volume maps outside the MGS chi2(alpha) table
        # range).  A CONSTANT penalty cancels in the normalized weights below,
        # so continuing would silently drop that constraint and report a
        # posterior shaped only by the remaining data — refuse loudly instead
        # (cobaya's equivalent is logp = -inf everywhere).
        raise ValueError(
            "no importance-sampling proposal has likelihood support: every "
            "sample hit an out-of-bounds penalty (best chi2 = "
            f"{best_chi2:.3g}); refusing to report a posterior that would "
            "silently drop that dataset's constraint. Check the prior ranges "
            "against the dataset's tabulated support."
        )
    # Importance weight: w = p(theta|data) / q(theta).  The numerator is
    # exp(-0.5 chi²); the denominator is the proposal pdf q.  Working in log-
    # space and subtracting the max keeps the exp() inside float range.
    log_weights = -0.5 * (chi2 - best_chi2) - log_proposal_pdf
    log_weights -= float(np.max(log_weights))
    weights = np.exp(np.clip(log_weights, -745.0, 0.0))
    weight_sum = float(np.sum(weights))
    if not math.isfinite(weight_sum) or weight_sum <= 0.0:
        raise ValueError("importance sampler weights underflowed")
    probabilities = weights / weight_sum
    proposal_ess = float(weight_sum * weight_sum / np.sum(weights * weights))
    posterior_indices = rng.choice(samples.shape[0], size=sample_count, replace=True, p=probabilities)
    posterior_samples = samples[posterior_indices]
    return posterior_samples, best_chi2, proposal_ess, int(samples.shape[0]), compressed_errors


def _sampling_source_records(entries: list[CosmologyDatasetEntry]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for entry in entries:
        if entry.key == "desi_dr1_bao":
            records.append({
                "dataset_key": entry.key,
                "source_locator": "DESI DR1 BAO ALL_GCcomb mean/covariance files",
                "approximation": "Gaussian BAO mean/covariance evaluated against flat LCDM distances",
                "data_products": [product.to_dict() for product in entry.data_products],
            })
        elif entry.key == "sdss_6df_bao":
            records.append({
                "dataset_key": entry.key,
                "source_locator": "Aubourg+2015 Table II (6dFGS Gaussian) + CobayaSampler/bao_data sdss_MGS_prob.txt (Ross+2015 MGS chi2(alpha) table)",
                "approximation": "Mixed: 6dFGS z=0.106 hand-typed Gaussian (D_V/r_d = 3.047 ± 0.137) + SDSS MGS z=0.15 full non-Gaussian chi2(alpha) lookup (cobaya's spline convention, alpha = (D_V/r_d)/4.29720761315)",
                "data_products": [product.to_dict() for product in entry.data_products],
            })
        elif entry.key == "cosmic_chronometers":
            records.append({
                "dataset_key": entry.key,
                "source_locator": "Gómez-Valent & Amendola 2018 (arXiv:1802.01505) Table 1 — 31 cosmic-chronometer H(z)",
                "approximation": "Diagonal-covariance H(z)=H0·E(z) χ² (flat w0waCDM); full Moresco+2020 systematic covariance not applied",
            })
        elif entry.key == "cosmic_chronometers_moresco20":
            records.append({
                "dataset_key": entry.key,
                "source_locator": "Moresco et al. 2020 (arXiv:2003.07362) CCcovariance — 15 BC03 H(z) + full systematic covariance",
                "approximation": "Full-covariance H(z)=H0·E(z) χ² = rᵀC⁻¹r (flat w0waCDM); covariance reproduced from sha256-pinned raw files via scripts/gen_moresco20_cc_covariance.py",
                "data_products": [product.to_dict() for product in entry.data_products],
            })
        elif entry.key == "eboss_dr16_rsd":
            records.append({
                "dataset_key": entry.key,
                "source_locator": "Alam et al. 2021 (arXiv:2007.08991) Table III RSD-only column — 6 eBOSS DR16 fσ8(z)",
                "approximation": "Diagonal-covariance fσ8=f(z)·σ8·D(z)/D(0) χ² with Linder γ growth index; per-tracer Gaussian (correlations ignored, per Table III note a)",
            })
        elif entry.key in EBOSS_DR16_FSBAO_EXECUTABLE_KEYS:
            records.append({
                "dataset_key": entry.key,
                "source_locator": f"SDSS DR16 BAO+RSD consensus (CobayaSampler/bao_data, {entry.key}) — joint (D_M/r_s, D_H/r_s, fσ8) + full covariance",
                "approximation": "Full-covariance rᵀC⁻¹r χ² (flat w0waCDM): D_M/r_s, D_H/r_s distance ratios + Linder-γ fσ8 growth, with the released joint distance+growth covariance",
                "data_products": [product.to_dict() for product in entry.data_products],
            })
        elif entry.key in EBOSS_DR16_GRID_BAO_EXECUTABLE_KEYS:
            records.append({
                "dataset_key": entry.key,
                "source_locator": (
                    "eBOSS DR16 released non-Gaussian BAO surface (Alam et al. 2021; "
                    "CobayaSampler/bao_data, " + entry.key + ") — "
                    + ("D_V/r_d probability table at z=0.845" if entry.key == "eboss_dr16_elg_bao"
                       else "(D_M/r_d, D_H/r_d) likelihood grid at z=2.334")
                ),
                "approximation": (
                    "chi2 = -2·ln L from a cubic spline of the released log-surface "
                    "(cobaya bao.sdss_dr16_* parity); out-of-grid samples refused "
                    "(fail-safe), never extrapolated"
                ),
                "data_products": [product.to_dict() for product in entry.data_products],
            })
        elif entry.key in SDSS_DR12_CONSENSUS_EXECUTABLE_KEYS:
            records.append({
                "dataset_key": entry.key,
                "source_locator": "BOSS DR12 consensus BAO (Alam et al. 2017, arXiv:1607.03155; CobayaSampler/bao_data) — (D_M·rs_fid/r_d, H·r_d/rs_fid) at z=0.38/0.51/0.61 + full 6×6 covtot",
                "approximation": "Full-covariance rᵀC⁻¹r χ² (flat w0waCDM) in the release's rs_fid=147.78 Mpc storage convention, mirroring cobaya bao.sdss_dr12_consensus_bao",
                # Structured numeric field: rs_fid is a legitimate published
                # constant of the release and must stay claimable now that the
                # prose strings above are free-text-skipped by claim_validator.
                "rs_fid_mpc": SDSS_DR12_RS_FID_MPC,
                "data_products": [product.to_dict() for product in entry.data_products],
            })
        elif entry.key in PANTHEON18_EXECUTABLE_KEYS:
            # Key-in-set, NOT key==: with the env flag OFF this entry runs the
            # compressed Ωm Gaussian and must fall through to the honest
            # compressed record below — a full-covariance attestation for a
            # computation that never ran was a 2026-06-13 review MAJOR.
            records.append({
                "dataset_key": entry.key,
                "source_locator": "Pantheon 2018 Scolnic+2018 (arXiv:1710.00845; CobayaSampler/sn_data) — 1048-SN apparent magnitudes + stat+sys covariance",
                "approximation": "Full-covariance SN χ² = δᵀC⁻¹δ − (ΣC⁻¹δ)²/(ΣC⁻¹) (flat w0waCDM), C = C_sys + diag(dmb²), analytically marginalizing the constant magnitude offset (no M_B/H0); constrains Ωm (+w0/wa)",
                "data_products": [product.to_dict() for product in entry.data_products],
            })
        elif entry.key == "union3":
            records.append({
                "dataset_key": entry.key,
                "source_locator": "Union3/UNITY1.5 Rubin+2023 (arXiv:2311.12098; CobayaSampler/sn_data) — 22-bin binned distance moduli + full 22x22 mag covariance",
                "approximation": "Full-covariance SN χ² = δᵀC⁻¹δ − (ΣC⁻¹δ)²/(ΣC⁻¹) (flat w0waCDM), analytically marginalizing the constant magnitude offset (no M_B/H0); constrains Ωm (+w0/wa)",
                "data_products": [product.to_dict() for product in entry.data_products],
            })
        elif _is_executable_des_sn_entry(entry):
            records.append({
                "dataset_key": entry.key,
                "source_locator": "DES-SN5YR Vincenzi+2024 Legacy (github tag 1.3) — 1829-SN distance-modulus vector + full stat+sys covariance",
                "approximation": "Full-covariance SN χ² = δᵀC⁻¹δ − (ΣC⁻¹δ)²/(ΣC⁻¹) (flat w0waCDM), analytically marginalizing the SN absolute-magnitude offset (no M_B/H0); constrains Ωm (+w0/wa)",
                "data_products": [product.to_dict() for product in entry.data_products],
            })
        elif entry.compressed_likelihood is not None:
            records.append({
                "dataset_key": entry.key,
                "source_locator": entry.compressed_likelihood.source_locator,
                "approximation": entry.compressed_likelihood.approximation,
            })
    return records


def _compressed_runner_unavailable(
    *,
    model_key: str,
    entries: list[CosmologyDatasetEntry],
    seed: int,
    reason: str,
) -> dict[str, Any]:
    return {
        "success": True,
        "__tool_status__": "PARTIAL",
        "analysis_status": "NO_COMPRESSED_LIKELIHOOD",
        "publication_ready": False,
        "chain_tier": "blocked",
        "__do_not_claim__": True,
        "model": model_key,
        "model_label": MODEL_LABELS.get(model_key, model_key),
        "sampler": "compressed_gaussian_analytic",
        "dataset_keys": [entry.key for entry in entries],
        "datasets": [entry.to_dict() for entry in entries],
        "datasets_used": [],
        "datasets_not_run": [entry.to_dict() for entry in entries],
        "random_seed": seed,
        "warnings": [reason],
        "__message_to_model__": (
            reason
            + " Do not quote posterior constraints, S8/H0/Omega_m tensions, "
            "AIC/BIC, or significance from this result."
        ),
        "provenance": {
            "cosmology_likelihood": {
                "registry_version": "2026-04-30",
                "runner": "compressed_gaussian_analytic",
                "dataset_keys": [entry.key for entry in entries],
                "datasets_used": [],
                "datasets_not_run": [entry.key for entry in entries],
                "citations": _collect_citations(entries),
                "publication_ready": False,
            }
        },
    }


def _compressed_parameter_order(entries: list[CosmologyDatasetEntry]) -> list[str]:
    order: list[str] = []
    for entry in entries:
        spec = entry.compressed_likelihood
        if spec is None:
            continue
        for param in spec.parameters:
            if param in RUNNER_PARAMETER_PRIORS and param not in order:
                order.append(param)
    # Keep familiar cosmology params first for stable UI/test output.
    preferred = ["H0", "omegam", "sigma8", "S8"]
    ordered = [param for param in preferred if param in order] + [
        param for param in order if param not in preferred
    ]
    return _drop_derived_s8(ordered)


def _all_external_cobaya(entries: list[CosmologyDatasetEntry]) -> bool:
    """True iff every entry is registered with execution_mode='external_cobaya'.

    Used as the gate for delegating run_likelihood_chain to cobaya_runner.
    Mixed selections (some entries compressed, some external_cobaya) keep
    the legacy compressed-Gaussian path so we never silently drop the
    compressed datasets — those still produce a publication_ready summary
    in the existing branch.
    """
    return bool(entries) and all(
        entry.execution_mode == "external_cobaya" for entry in entries
    )


def _cobaya_parameter_order(
    model_key: str,
    entries: list[CosmologyDatasetEntry],
) -> list[str]:
    """Pick the parameter ordering passed to cobaya_runner.

    A primary-CMB entry (plik_lite / low-l TT / low-l EE) samples the full CMB
    set (ombh2, omch2, H0, ns, As, tau), plus A_planck when plik_lite is among
    the entries (see CMB_APLANCK_KEYS).  Otherwise: prefers any
    compressed-likelihood parameter spec the registered entries expose; falls
    back to the intersection of SUPPORTED_MODELS[model_key] with
    RUNNER_PARAMETER_PRIORS so that the YAML cobaya_runner emits always declares
    params it has prior bounds for.
    """
    if any(entry.key in CMB_COBAYA_EXECUTABLE_KEYS for entry in entries):
        order = ["ombh2", "omch2", "H0", "ns", "As", "tau"]
        if any(entry.key in CMB_APLANCK_KEYS for entry in entries):
            order.append("A_planck")
        for param in SUPPORTED_MODELS.get(model_key, ()):
            # mnu joined 2026-06-12: without it a *_mnu chain silently ran
            # CAMB's fixed default neutrino mass while the result carried the
            # mnu model name (same class as the w0 orphan, but silent).
            # cobaya/CAMB consume a sampled "mnu" directly; live-verified the
            # plik_lite likelihood responds (-2lnL 584->2044 at mnu 0.06->0.5).
            # omegak joined the same day: ok_* chains died at CAMB setup on
            # the bogus "curved" extra_arg and never sampled curvature at all;
            # CAMB consumes it as "omk" via the YAML builder's alias table.
            if param in {"w", "w0", "wa", "mnu", "omegak"} and param not in order:
                order.append(param)
        return order
    compressed_order = _compressed_parameter_order(entries)
    if compressed_order:
        return compressed_order
    fallback = [p for p in SUPPORTED_MODELS[model_key] if p in RUNNER_PARAMETER_PRIORS]
    return fallback or ["H0", "omegam"]


def _sanitize_runner_priors(
    parameters: list[str],
    priors: dict[str, Any] | None,
) -> dict[str, tuple[float, float]]:
    user = priors or {}
    if not isinstance(user, dict):
        raise ValueError("priors must be an object")
    unknown = set(user) - set(parameters)
    if unknown:
        raise ValueError(f"priors include unsupported compressed-runner parameters: {sorted(unknown)}")
    # Geometric + CMB-cobaya params share this validator; the CMB-only params live
    # in a separate dict so they don't leak into _compressed_parameter_order.
    bounds = {**RUNNER_PARAMETER_PRIORS, **CMB_PARAMETER_PRIORS}
    out: dict[str, tuple[float, float]] = {}
    for name in parameters:
        default_low, default_high = bounds[name]
        raw = user.get(name, (default_low, default_high))
        if isinstance(raw, dict):
            low_raw, high_raw = raw.get("min"), raw.get("max")
        elif isinstance(raw, (list, tuple)) and len(raw) == 2:
            low_raw, high_raw = raw
        else:
            raise ValueError(f"prior for {name} must be [min, max]")
        low, high = float(low_raw), float(high_raw)
        if not (math.isfinite(low) and math.isfinite(high)) or low >= high:
            raise ValueError(f"prior for {name} must have finite min < max")
        if low < default_low or high > default_high:
            raise ValueError(f"prior for {name} must stay within [{default_low}, {default_high}]")
        out[name] = (low, high)
    return out


def _posterior_summary(values: np.ndarray) -> dict[str, Any]:
    hdi_low = round(float(np.percentile(values, 3.0)), 6)
    hdi_high = round(float(np.percentile(values, 97.0)), 6)
    return {
        "mean": round(float(np.mean(values)), 6),
        "std": round(float(np.std(values)), 6),
        "median": round(float(np.median(values)), 6),
        "hdi_low_94": hdi_low,
        "hdi_high_94": hdi_high,
        "hdi_94": [hdi_low, hdi_high],
        # These are posterior summaries from an importance-resampled cloud,
        # not independent MCMC chains.  The citeability diagnostic lives in
        # chain_diagnostics.proposal_ess; do not imply per-parameter ESS=N.
        "rhat": None,
        "ess_bulk": None,
        "ess_tail": None,
        "status": "importance_resampled_summary",
        "diagnostic_note": "Use chain_diagnostics.proposal_ess for the publication gate.",
    }


def _combined_chi2(
    entries: list[CosmologyDatasetEntry],
    parameter_order: list[str],
    posterior_mean: np.ndarray,
) -> float:
    params = {name: float(posterior_mean[index]) for index, name in enumerate(parameter_order)}
    # Derived S8 at this parameter point (σ8·√(Ωm/0.3)) when both are present —
    # so the WL/Planck S8 rows still enter χ² even though S8 is not sampled.
    derived_s8 = (
        params["sigma8"] * math.sqrt(params["omegam"] / S8_PIVOT_OMEGAM)
        if "sigma8" in params and "omegam" in params
        else None
    )
    total = 0.0
    for entry in entries:
        spec = entry.compressed_likelihood
        if spec is None:
            continue
        names = [
            name
            for name in spec.parameters
            if name in params or (name == "S8" and derived_s8 is not None)
        ]
        if not names:
            continue
        local_idx = [list(spec.parameters).index(name) for name in names]
        mean = np.asarray(spec.mean, dtype=float)[local_idx]
        cov = np.asarray(spec.covariance, dtype=float)[np.ix_(local_idx, local_idx)]
        vec = np.asarray(
            [
                derived_s8 if (name == "S8" and name not in params) else params[name]
                for name in names
            ],
            dtype=float,
        )
        residual = vec - mean
        total += float(residual.T @ np.linalg.inv(cov) @ residual)
    return total


def _compressed_s8_mean_var(
    spec: CompressedLikelihoodSpec,
) -> tuple[float, float, str] | None:
    """Return (S8 mean, variance, source) for direct or derived S8 specs."""

    params = list(spec.parameters)
    mean = np.asarray(spec.mean, dtype=float)
    cov = np.asarray(spec.covariance, dtype=float)
    if "S8" in params:
        idx = params.index("S8")
        return float(mean[idx]), float(cov[idx][idx]), "direct"
    if "sigma8" not in params or "omegam" not in params:
        return None
    sigma_idx = params.index("sigma8")
    omegam_idx = params.index("omegam")
    sigma8 = float(mean[sigma_idx])
    omegam = float(mean[omegam_idx])
    if sigma8 <= 0 or omegam <= 0:
        return None
    s8 = sigma8 * math.sqrt(omegam / S8_PIVOT_OMEGAM)
    # First-order error propagation from (sigma8, Omega_m).  This is sufficient
    # for the compressed tension table; the full posterior still comes from the
    # sampler and is reported separately.
    grad = np.asarray([
        s8 / sigma8,
        0.5 * s8 / omegam,
    ])
    subcov = cov[np.ix_([sigma_idx, omegam_idx], [sigma_idx, omegam_idx])]
    variance = float(grad.T @ subcov @ grad)
    if not math.isfinite(variance) or variance <= 0:
        return None
    return float(s8), variance, "derived_from_sigma8_omegam"


def _pairwise_tensions(entries: list[CosmologyDatasetEntry]) -> list[dict[str, Any]]:
    tensions: list[dict[str, Any]] = []
    specs = [
        (entry, entry.compressed_likelihood)
        for entry in entries
        if entry.compressed_likelihood is not None
    ]
    for i, (left_entry, left_spec) in enumerate(specs):
        if left_spec is None:
            continue
        for right_entry, right_spec in specs[i + 1:]:
            if right_spec is None:
                continue
            common = [
                name for name in left_spec.parameters
                if name in right_spec.parameters and name in RUNNER_PARAMETER_PRIORS
            ]
            for name in common:
                li = list(left_spec.parameters).index(name)
                ri = list(right_spec.parameters).index(name)
                left_var = float(left_spec.covariance[li][li])
                right_var = float(right_spec.covariance[ri][ri])
                denom = math.sqrt(max(left_var + right_var, 0.0))
                if denom <= 0:
                    continue
                delta = float(left_spec.mean[li] - right_spec.mean[ri])
                tensions.append({
                    "parameter": name,
                    "dataset_a": left_entry.key,
                    "dataset_b": right_entry.key,
                    "delta": round(delta, 6),
                    "sigma": round(abs(delta) / denom, 3),
                    "value_a": float(left_spec.mean[li]),
                    "value_b": float(right_spec.mean[ri]),
                })
            if "S8" not in common:
                left_s8 = _compressed_s8_mean_var(left_spec)
                right_s8 = _compressed_s8_mean_var(right_spec)
                if left_s8 is not None and right_s8 is not None:
                    left_value, left_var, left_source = left_s8
                    right_value, right_var, right_source = right_s8
                    denom = math.sqrt(max(left_var + right_var, 0.0))
                    if denom > 0:
                        delta = left_value - right_value
                        tensions.append({
                            "parameter": "S8",
                            "dataset_a": left_entry.key,
                            "dataset_b": right_entry.key,
                            "delta": round(delta, 6),
                            "sigma": round(abs(delta) / denom, 3),
                            "value_a": round(left_value, 6),
                            "value_b": round(right_value, 6),
                            "comparison": "derived_pairwise",
                            "value_a_source": left_source,
                            "value_b_source": right_source,
                            "note": (
                                "S8 compared after deriving it from sigma8 and "
                                "Omega_m where a compressed summary did not carry "
                                "S8 directly."
                            ),
                        })
                elif (
                    ("S8" in left_spec.parameters or {"sigma8", "omegam"} <= set(left_spec.parameters))
                    and ("S8" in right_spec.parameters or {"sigma8", "omegam"} <= set(right_spec.parameters))
                ):
                    tensions.append({
                        "parameter": "S8",
                        "dataset_a": left_entry.key,
                        "dataset_b": right_entry.key,
                        "sigma": None,
                        "status": "not_comparable",
                        "reason": "S8 uncertainty could not be propagated from the registered compressed covariance.",
                    })
    tensions.sort(
        key=lambda item: float(item["sigma"]) if isinstance(item.get("sigma"), (int, float)) else -1.0,
        reverse=True,
    )
    return tensions

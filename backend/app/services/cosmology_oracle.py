"""T1-U9: a single, sourced table of published cosmology anchors.

The platform's "right answers" — published parameter constraints it can reproduce
in-process — were scattered across benchmark bands and module constants.  This
centralizes them as one frozen, citable table so the reproduce-anchor harness
(T1-U10) and the oracle-coverage measurement (T1-U11) share a single basis.

Each anchor records the published central value, an absolute tolerance, the
datasets + model that reproduce it, and the arXiv identifier of the source.  We
store arXiv ids (which match the registry DatasetCitation entries) rather than
hand-typed bibcodes to avoid introducing an unverifiable citation string.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OracleAnchor:
    goal_key: str            # stable id, e.g. "desi_dr1_bao_omegam"
    parameter: str           # the posterior parameter the harness reads
    value: float             # published central value
    tol: float               # absolute tolerance for "reproduced"
    datasets: tuple[str, ...]
    model: str
    # "independent" = the fit recovers the published number from RAW data +
    # physics (a genuine reproduction).  "consistency" = a compressed Gaussian
    # summary recovers its own input mean (a self-consistency check, not
    # independent validation).  Only honest if set per anchor by inspection.
    independence: str
    source_arxiv: str        # arXiv id of the source paper
    source_label: str


# Anchors seeded ONLY from constraints the platform already reproduces in-process
# (each has a backing benchmark or provenance test).  SN/CMB values are kept in
# sync with the live registry compressed means by test_oracle_values_match_live_
# registry_constants, so a registry typo breaks the oracle test instead of
# silently shifting the "right answer".
PUBLISHED_ANCHORS: tuple[OracleAnchor, ...] = (
    OracleAnchor(
        "desi_dr1_bao_omegam", "omegam", 0.295, 0.02, ("desi_dr1_bao",), "lcdm",
        "independent",
        "2404.03002", "DESI DR1 BAO flat-ΛCDM Ωm (Adame et al. 2024)",
    ),
    OracleAnchor(
        # Genuine joint reproduction: fitting DESI BAO distance ratios + the
        # Planck CMB compressed summary recovers Ωm BETWEEN the two inputs
        # (DESI-only 0.295, Planck-only 0.3153), landing near the published
        # DESI 2024 VI BAO+CMB value 0.307 ± 0.005.  tol 0.015 is honest about
        # our compressed-Planck approximation vs the full Planck likelihood.
        "desi_cmb_omegam", "omegam", 0.307, 0.015,
        ("desi_dr1_bao", "planck2018_compressed"), "lcdm",
        "independent",
        "2404.03002", "DESI DR1 BAO + CMB flat-ΛCDM Ωm (DESI 2024 VI)",
    ),
    OracleAnchor(
        # Genuine fit-quality reproduction (not a parameter recovery — CC-only
        # H0/Ωm are degenerate): flat-ΛCDM fits the 31 published cosmic-
        # chronometer H(z) points with reduced χ² ≈ 0.5 (conservative CC errors).
        # Band [0.3, 1.2] mirrors the cosmic_chronometer_hz benchmark.
        "cc_fit_quality", "chi2_dof", 0.75, 0.45, ("cosmic_chronometers",), "lcdm",
        "fit_quality",
        "1802.01505", "Cosmic-chronometer H(z) ΛCDM fit quality (Gómez-Valent & Amendola 2018 compilation)",
    ),
    OracleAnchor(
        # Genuine fit-quality reproduction: flat-ΛCDM growth fits the 6 published
        # eBOSS DR16 RSD fσ8 points with reduced χ² ≈ 1.3.  Band [0.3, 2.0]
        # mirrors the eboss_fsigma8_growth benchmark.
        "eboss_fit_quality", "chi2_dof", 1.15, 0.85, ("eboss_dr16_rsd",), "lcdm",
        "fit_quality",
        "2007.08991", "eBOSS DR16 RSD fσ8 ΛCDM growth fit quality (Alam et al. 2021)",
    ),
    OracleAnchor(
        "pantheon_plus_omegam", "omegam", 0.334, 0.05, ("pantheon_plus",), "lcdm",
        "consistency",
        "2202.04077", "Pantheon+SH0ES SN Ωm (Brout et al. 2022)",
    ),
    OracleAnchor(
        "pantheon_plus_h0", "H0", 73.04, 3.0, ("pantheon_plus",), "lcdm",
        "consistency",
        "2112.04510", "SH0ES SN-calibrated H0 (Riess et al. 2022)",
    ),
    OracleAnchor(
        "des_sn5yr_omegam", "omegam", 0.352, 0.03, ("des_sn5yr",), "lcdm",
        "consistency",
        "2401.02929", "DES-SN5YR SN-only Ωm (Abbott et al. 2024)",
    ),
    OracleAnchor(
        "union3_omegam", "omegam", 0.356, 0.05, ("union3",), "lcdm",
        "consistency",
        "2311.12098", "Union3 SN-only Ωm (Rubin et al. 2023)",
    ),
    OracleAnchor(
        "planck2018_h0", "H0", 67.36, 1.0, ("planck2018_compressed",), "lcdm",
        "consistency",
        "1807.06209", "Planck 2018 CMB-only H0 (Planck Collaboration 2020)",
    ),
    OracleAnchor(
        "planck2018_omegam", "omegam", 0.3153, 0.02, ("planck2018_compressed",), "lcdm",
        "consistency",
        "1807.06209", "Planck 2018 CMB-only Ωm (Planck Collaboration 2020)",
    ),
)


# Goals the platform may be ASKED to constrain but for which it has NO reproduced
# published anchor: extended-model parameters the phase-1 in-process runner cannot
# fit (curvature/neutrino-mass need external Cobaya; the w0/wa dark-energy EOS is a
# research target, not an anchored constraint).  Listed explicitly so coverage
# reports the gap honestly instead of hiding it.
OFF_ANCHOR_GOALS: tuple[str, ...] = (
    "w0_dark_energy_eos",
    "wa_dark_energy_evolution",
    "omega_k_curvature",
    "neutrino_mass_sum",
)


def get_anchor(goal_key: str) -> OracleAnchor | None:
    """Return the anchor for a goal key, or None if it is off-anchor."""
    return next((a for a in PUBLISHED_ANCHORS if a.goal_key == goal_key), None)


def is_covered(goal_key: str) -> bool:
    """True iff the goal is backed by a reproduced published anchor."""
    return get_anchor(goal_key) is not None


def route_goal(goal_key: str) -> dict:
    """Decide whether a goal may be answered autonomously.  Covered (anchored)
    goals route to 'answer'; everything else routes to 'human_review'.  This is
    the rail that enforces 'no off-anchor autonomous conclusions' — it does NOT
    authorize off-anchor autonomy (flipping an uncovered goal to 'answer' is a
    policy decision, not a code change here)."""
    if is_covered(goal_key):
        return {"route": "answer", "goal_key": goal_key}
    return {
        "route": "human_review",
        "goal_key": goal_key,
        "reason": "off_anchor_not_in_oracle_coverage",
        "suggested_next_step": (
            "This goal has no reproduced published anchor. Add and verify an "
            "OracleAnchor (T1-U9/U10) and re-measure coverage before answering it "
            "autonomously, or route it to a human reviewer."
        ),
    }


def off_anchor_abstention(goal_key: str) -> dict:
    """For an off-anchor goal, return a structured-abstention envelope that
    renders via the existing HonestAbstentionCard banner vocabulary
    (__tool_status__/__do_not_claim__/__message_to_model__/__suggested_next_step__)
    and carries NO numeric conclusion.  A covered goal passes straight through."""
    routing = route_goal(goal_key)
    if routing["route"] == "answer":
        return routing
    return {
        "__tool_status__": "UNAVAILABLE",
        "__do_not_claim__": True,
        "off_anchor_abstained": True,
        "route": "human_review",
        "goal_key": goal_key,
        "publication_ready": False,
        "__message_to_model__": (
            f"The goal '{goal_key}' is off-anchor: it has no reproduced published "
            "anchor in the oracle-coverage table, so v1 must not emit an autonomous "
            "numeric conclusion. Report that it was routed to human review; do not "
            "fabricate or estimate a value."
        ),
        "__suggested_next_step__": routing["suggested_next_step"],
    }


def oracle_coverage() -> dict:
    """Measure what fraction of the goal universe is backed by a reproduced
    published anchor.  Measurement ONLY — it authorizes nothing; it is the number
    that must be reviewed before any off-anchor autonomy is enabled (T1-U12 routes
    uncovered goals to human review)."""
    covered = tuple(a.goal_key for a in PUBLISHED_ANCHORS)
    independent = tuple(a.goal_key for a in PUBLISHED_ANCHORS if a.independence == "independent")
    fit_quality = tuple(a.goal_key for a in PUBLISHED_ANCHORS if a.independence == "fit_quality")
    genuine = independent + fit_quality
    uncovered = OFF_ANCHOR_GOALS
    n_goals = len(covered) + len(uncovered)
    return {
        "n_goals": n_goals,
        "n_covered": len(covered),
        "coverage_fraction": round(len(covered) / n_goals, 4),
        # The numbers that actually gate autonomy: GENUINE reproductions —
        # independent (parameter recovery from raw data) + fit_quality (χ²/dof
        # against published data) — as opposed to compressed self-consistency.
        "n_independent": len(independent),
        "independent_fraction": round(len(independent) / n_goals, 4),
        "n_fit_quality": len(fit_quality),
        "n_genuine": len(genuine),
        "genuine_fraction": round(len(genuine) / n_goals, 4),
        "independent_goals": list(independent),
        "fit_quality_goals": list(fit_quality),
        "genuine_goals": list(genuine),
        "covered_goals": list(covered),
        "uncovered_goals": list(uncovered),
    }


def reproduce_anchor(anchor: OracleAnchor) -> dict:
    """Run the real in-process chain for an anchor and check it reproduces the
    published value within tolerance.  This is the correctness axis (does the fit
    land on the right number), distinct from provenance (did the number come from
    a verified tool).  run_likelihood_chain is imported lazily to keep this module
    a dependency-free data table."""
    from app.services.cosmology_likelihoods import run_likelihood_chain

    r = run_likelihood_chain(
        model=anchor.model, dataset_keys=list(anchor.datasets), n_samples=4000, random_seed=42,
    )
    if anchor.parameter == "chi2_dof":
        # Fit-quality anchor: genuinely compute reduced χ² = χ² / (N_data - N_params).
        fs = r.get("fit_statistics", {})
        chi2, n_data, n_par = fs.get("chi2"), fs.get("n_constraints"), fs.get("n_parameters")
        dof = (n_data - n_par) if isinstance(n_data, int) and isinstance(n_par, int) else None
        med = (chi2 / dof) if isinstance(chi2, (int, float)) and dof and dof > 0 else None
    else:
        med = (r.get("parameters", {}).get(anchor.parameter, {}) or {}).get("median")
    has_value = isinstance(med, (int, float))
    within = has_value and abs(float(med) - anchor.value) <= anchor.tol
    return {
        "goal_key": anchor.goal_key,
        "parameter": anchor.parameter,
        "published_value": anchor.value,
        "tol": anchor.tol,
        "reproduced_value": float(med) if has_value else None,
        "within_tol": bool(within),
        "publication_ready": bool(r.get("publication_ready")),
        "datasets": list(anchor.datasets),
        "source_arxiv": anchor.source_arxiv,
    }

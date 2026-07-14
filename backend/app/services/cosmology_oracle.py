"""T1-U9: a single, sourced table of published cosmology anchors.

Published reference values and genuinely reproducible targets were scattered
across benchmark bands and module constants.  This centralizes them as one
frozen, citable table while keeping literature-only posterior summaries
separate from targets the in-process runner may actually reproduce.

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
    # "independent" = the fit recovers the published number from raw/released
    # data + physics (a genuine reproduction). "fit_quality" = a data-backed
    # goodness-of-fit reproduction. "consistency" is retained as the stable
    # compatibility label for a literature-context posterior reference; it is
    # never executed as a likelihood or counted as a reproduction.
    independence: str
    source_arxiv: str        # arXiv id of the source paper
    source_label: str


# Genuine anchors have a backing data/fit benchmark. SN/CMB posterior summaries
# remain in the table only as literature context and drift guards; they are kept
# in sync with registry constants but are not sent through the runner as if they
# were likelihood factors.
PUBLISHED_ANCHORS: tuple[OracleAnchor, ...] = (
    OracleAnchor(
        "desi_dr1_bao_omegam", "omegam", 0.295, 0.02, ("desi_dr1_bao",), "lcdm",
        "independent",
        "2404.03002", "DESI DR1 BAO flat-ΛCDM Ωm (Adame et al. 2024)",
    ),
    OracleAnchor(
        # Joint BAO+CMB fit (sums both χ²); recovers Ωm=0.312, consistent with the
        # published DESI 2024 VI BAO+CMB value 0.307 ± 0.005 within tol.  HONEST
        # CAVEAT: the compressed-Planck Ωm (σ≈0.007) is much tighter than the BAO
        # Ωm constraint, so this joint is CMB-dominated — the recovered 0.312 sits
        # near Planck-only (0.3153), not midway, and the BAO leg moves it only
        # weakly.  It demonstrates the platform can combine probes, but is a weak
        # test of the BAO contribution.  tol 0.012 (≈2.4σ) over the published error.
        "desi_cmb_omegam", "omegam", 0.307, 0.012,
        ("desi_dr1_bao", "planck2018_compressed"), "lcdm",
        "independent",
        "2404.03002", "DESI DR1 BAO + CMB flat-ΛCDM Ωm (DESI 2024 VI)",
    ),
    OracleAnchor(
        # Genuine fit-quality reproduction (not a parameter recovery — CC-only
        # H0/Ωm are degenerate): flat-ΛCDM fits the 31 published cosmic-chronometer
        # H(z) points (data source: Gómez-Valent & Amendola 2018) with reduced χ²
        # in the good-fit range.  value=1.0 is the EXPECTED reduced χ² of a correct
        # model (a statistical expectation, NOT a number this paper reports);
        # tol=0.6 is the acceptable-fit window.  Actual recovery ≈ 0.50.
        "cc_fit_quality", "chi2_dof", 1.0, 0.6, ("cosmic_chronometers",), "lcdm",
        "fit_quality",
        "1802.01505", "Cosmic-chronometer H(z) ΛCDM fit quality (data: Gómez-Valent & Amendola 2018)",
    ),
    OracleAnchor(
        # Genuine fit-quality reproduction: flat-ΛCDM growth fits the 6 published
        # eBOSS DR16 RSD fσ8 points (data source: Alam et al. 2021).  value=1.0 is
        # the expected reduced χ² of a correct model (not a paper-reported number);
        # tol=0.7 is the acceptable-fit window.  Actual recovery ≈ 1.32.
        "eboss_fit_quality", "chi2_dof", 1.0, 0.7, ("eboss_dr16_rsd",), "lcdm",
        "fit_quality",
        "2007.08991", "eBOSS DR16 RSD fσ8 ΛCDM growth fit quality (data: Alam et al. 2021)",
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
    """True iff the goal has any published anchor (genuine OR consistency)."""
    return get_anchor(goal_key) is not None


def is_genuine(goal_key: str) -> bool:
    """True iff the goal is backed by a GENUINE reproduction — an independent
    parameter recovery or a fit-quality validation — NOT merely a compressed
    self-consistency anchor that recovers its own input mean."""
    a = get_anchor(goal_key)
    return a is not None and a.independence in ("independent", "fit_quality")


def chain_is_off_anchor(model: str, dataset_keys) -> bool:
    """True if a fit introduces frontier parameters (w/w0/wa/Ωk/Mν) with NO genuine
    reproduced anchor for this exact (model, datasets) goal — so its novel
    parameters must not be quoted as a published conclusion (exploratory + human
    review only).  LCDM goals are anchored (Ωm/H0) and never off-anchor.  Any
    extended model is off-anchor unless an 'independent' anchor reproduces that same
    model+datasets — none today; the Phase-2 full-CMB dark-energy reproduction will
    register the first one, at which point THAT goal may publish."""
    if model == "lcdm":
        return False
    keys = set(dataset_keys or ())
    for a in PUBLISHED_ANCHORS:
        if a.independence == "independent" and a.model == model and set(a.datasets) == keys:
            return False
    return True


def route_goal(goal_key: str) -> dict:
    """Decide whether a goal may be answered autonomously.  Only GENUINELY
    reproduced goals route to 'answer'; a literature-context posterior anchor
    is not validation, so it routes to human_review like any off-anchor goal. This
    enforces 'no off-anchor autonomous conclusions' and does NOT authorize
    off-anchor autonomy (a policy decision, not a code change here)."""
    if is_genuine(goal_key):
        return {"route": "answer", "goal_key": goal_key}
    consistency_only = is_covered(goal_key)
    return {
        "route": "human_review",
        "goal_key": goal_key,
        "reason": (
            "consistency_only_not_independently_reproduced"
            if consistency_only
            else "off_anchor_not_in_oracle_coverage"
        ),
        "suggested_next_step": (
            "This goal has only a published posterior summary retained as "
            "literature context, not an executable likelihood or genuine "
            "reproduction. Add and "
            "verify an independent or fit-quality anchor, or route to a human reviewer."
            if consistency_only
            else "This goal has no reproduced published anchor. Add and verify an "
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
            f"The goal '{goal_key}' is not genuinely reproduced (reason: "
            f"{routing['reason']}), so v1 must not emit an autonomous numeric "
            "conclusion. Report that it was routed to human review; do not "
            "fabricate or estimate a value."
        ),
        "__suggested_next_step__": routing["suggested_next_step"],
    }


def oracle_coverage() -> dict:
    """Measure reference coverage separately from genuine reproduction.

    ``n_covered`` / ``coverage_fraction`` are retained compatibility names for
    goals with any curated published reference, including literature-only
    posterior summaries.  They must not be interpreted as reproduction metrics.
    ``n_reproduced`` / ``reproduced_fraction`` are the fail-closed scientific
    coverage values and include only independent or fit-quality reproductions.
    """
    covered = tuple(a.goal_key for a in PUBLISHED_ANCHORS)
    independent = tuple(a.goal_key for a in PUBLISHED_ANCHORS if a.independence == "independent")
    fit_quality = tuple(a.goal_key for a in PUBLISHED_ANCHORS if a.independence == "fit_quality")
    literature_context = tuple(
        a.goal_key for a in PUBLISHED_ANCHORS if a.independence == "consistency"
    )
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
        "n_reproduced": len(genuine),
        "reproduced_fraction": round(len(genuine) / n_goals, 4),
        "n_literature_context": len(literature_context),
        "literature_context_goals": list(literature_context),
        "coverage_semantics": (
            "coverage_fraction is curated-reference coverage; use "
            "reproduced_fraction for scientific reproduction coverage"
        ),
        # Denominator-stable: fraction of ANCHORS that are genuine — does not move
        # when the off-anchor list is edited (genuine_fraction's denominator does).
        "genuine_of_anchored": round(len(genuine) / len(covered), 4) if covered else 0.0,
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
    if anchor.independence == "consistency":
        return {
            "goal_key": anchor.goal_key,
            "parameter": anchor.parameter,
            "published_value": anchor.value,
            "tol": anchor.tol,
            "reproduced_value": None,
            "within_tol": None,
            "reproduction_attempted": False,
            "reproduction_status": "literature_context_not_executed",
            "anchor_scope": "literature_context",
            "publication_ready": False,
            "preliminary_ready": False,
            "chain_tier": "not_run",
            "publication_gate": None,
            "preliminary_reasons": [
                "published_posterior_summary_is_not_a_likelihood"
            ],
            "datasets": list(anchor.datasets),
            "source_arxiv": anchor.source_arxiv,
        }

    from app.services.cosmology_likelihoods import run_likelihood_chain

    # 2000 importance samples is ample for the point-median tolerances here (the
    # tightest is desi_cmb at 0.012); halves the compute the harness + benchmark
    # re-run across every anchor.
    r = run_likelihood_chain(
        model=anchor.model, dataset_keys=list(anchor.datasets), n_samples=2000, random_seed=42,
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
        "reproduction_attempted": True,
        "reproduction_status": "completed",
        "anchor_scope": "genuine_reproduction",
        "publication_ready": bool(r.get("publication_ready")),
        "preliminary_ready": bool(r.get("preliminary_ready")),
        "chain_tier": r.get("chain_tier"),
        "publication_gate": r.get("publication_gate"),
        "preliminary_reasons": list(r.get("preliminary_reasons") or []),
        "datasets": list(anchor.datasets),
        "source_arxiv": anchor.source_arxiv,
    }

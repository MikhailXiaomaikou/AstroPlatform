"""Cosmology audit — compare a published constraint against an in-platform reproduction.

Borrows the Feynman (MIT, companion-inc/feynman) ``/audit`` idea: reproduce a
paper's parameter constraint with the platform's real compressed-likelihood
runner, then report the tension between the published value and the reproduced
value.

HARD CONTRACT (learned from the 2026-05-20 removal of ``spot_check_literature_value``,
which flagged real H0/sigma8/S8 tensions as paper "fabrication" at +/-3 sigma):
the output is a TENSION measurement with physical attribution, NOT a
fabrication / "the paper is wrong" verdict. A real tension means the
measurements disagree — a scientific signal, never a fabrication flag.
``NOT_REPRODUCED`` means the platform lacks the data/model, not that the
published value is unsupported.

Scope (lightweight comparator): the caller supplies the published value(s) and
the datasets/model to reproduce with. This module does NOT extract claims from
paper text — that noisy step is left to the caller.
"""
from __future__ import annotations

import math
from typing import Any

# Tension classification thresholds (n-sigma).
_CONSISTENT_MAX = 1.0
_STRONG_MIN = 3.0

# claimed / reproduced parameter-name aliases -> canonical registry key.
# Registry canonical names come from cosmology_likelihoods MODEL parameter
# orders: H0, omegam, ombh2, rd, w (wcdm) / w0 + wa (w0wa), omegak, mnu;
# plus derived S8 / sigma8 / M_B.
_PARAM_ALIASES = {
    "h0": "H0", "hubble": "H0", "hubbleconstant": "H0",
    "om0": "omegam", "omegam": "omegam", "omegamatter": "omegam",
    "om": "omegam", "omm": "omegam", "omegam0": "omegam", "omegamzero": "omegam",
    "w": "w0", "w0": "w0", "weos": "w0",
    "wa": "wa",
    "sigma8": "sigma8", "sig8": "sigma8",
    "s8": "S8",
    "omegak": "omegak", "ok": "omegak",
    "mnu": "mnu", "summnu": "mnu",
    "ombh2": "ombh2", "rd": "rd", "mb": "M_B",
}


def _canonical_param(name: str) -> str:
    """Normalise a parameter name so claimed and reproduced keys can be matched."""
    s = str(name).strip().lower()
    s = s.replace("σ", "sigma").replace("ω", "omega").replace("ₘ", "m")
    s = s.replace(" ", "").replace("_", "").replace("-", "").replace("\\", "")
    return _PARAM_ALIASES.get(s, s)


def tension_sigma(
    claimed_value: float,
    claimed_sigma: float,
    reproduced_median: float,
    reproduced_std: float,
) -> float:
    """n-sigma tension between two Gaussian measurements.

    ``nsigma = |a - b| / sqrt(sigma_a^2 + sigma_b^2)``. Assumes the two
    measurements are INDEPENDENT; overlapping data violates this and is flagged
    separately by the caller.
    """
    denom = math.sqrt(float(claimed_sigma) ** 2 + float(reproduced_std) ** 2)
    diff = abs(float(claimed_value) - float(reproduced_median))
    if denom == 0.0:
        return 0.0 if diff == 0.0 else float("inf")
    return diff / denom


def classify_tension(nsigma: float) -> str:
    if not math.isfinite(nsigma):
        return "STRONG_TENSION"
    if nsigma < _CONSISTENT_MAX:
        return "CONSISTENT"
    if nsigma < _STRONG_MIN:
        return "MILD_TENSION"
    return "STRONG_TENSION"


def _parse_claimed_pair(raw: Any) -> tuple[float, float] | None:
    """Accept ``[value, sigma]`` or ``[value]`` (sigma=0). None if unparseable."""
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    try:
        value = float(raw[0])
        sigma = float(raw[1]) if len(raw) > 1 and raw[1] is not None else 0.0
    except (TypeError, ValueError, IndexError):
        return None
    if not math.isfinite(value) or not math.isfinite(sigma):
        return None
    return value, max(sigma, 0.0)


def audit_published_constraint(
    *,
    model: str,
    dataset_keys: list[str],
    claimed: dict[str, Any],
    paper_ref: dict[str, Any] | None = None,
    claimed_datasets: list[str] | None = None,
    random_seed: int | None = None,
) -> dict[str, Any]:
    """Reproduce a published cosmology constraint and report per-parameter tension."""
    from app.services.cosmology_likelihoods import _REGISTRY, run_likelihood_chain

    paper_ref = paper_ref or {}
    claimed = claimed or {}
    requested = [str(k) for k in (dataset_keys or [])]
    claimed_ds = {str(k) for k in (claimed_datasets or [])}

    base_warnings = [
        "Tension is a physical signal, not a fabrication verdict: a real H0/sigma8/S8 "
        "tension means the measurements disagree, NOT that the paper is wrong.",
        "Reproduction uses the compressed Gaussian summary likelihood, not the full "
        "external likelihood — treat it as a preliminary consistency check.",
    ]
    model_msg = (
        "Report each tension by its n-sigma as a physical comparison and attribute it to "
        "known tensions where relevant. NEVER describe a tension as the paper being wrong "
        "or fabricated. NOT_REPRODUCED means the platform lacks the data/model, not that "
        "the published value is unsupported."
    )

    # 1. Partition requested datasets by actual execution mode.  Registry
    # ``status`` describes product maturity, not whether the in-process runner
    # can execute it (DESI is intentionally status=external_likelihood while its
    # verified compressed path is runnable).
    runnable: list[str] = []
    unavailable: list[dict[str, str]] = []
    for key in requested:
        entry = _REGISTRY.get(key)
        if entry is None:
            unavailable.append({"key": key, "reason": "not in cosmology dataset registry"})
        elif entry.execution_mode == "config_only":
            unavailable.append({"key": key, "reason": "execution_mode=config_only"})
        else:
            runnable.append(key)

    # 2. Nothing runnable -> BLOCKED (no reproduction, no fabrication).
    if not runnable:
        return {
            "success": True,
            "__tool_status__": "BLOCKED",
            "analysis_status": "BLOCKED",
            "data_origin": "unavailable",
            "publication_ready": False,
            "__do_not_claim__": True,
            "audit_report": {
                "model": str(model or ""),
                "datasets_run": [],
                "datasets_unavailable": unavailable,
                "comparisons": [],
                "paper_ref": paper_ref,
            },
            "warnings": base_warnings + [
                "No requested dataset is directly runnable in the platform; cannot reproduce. "
                "This is a coverage gap, not a judgement on the paper."
            ],
            "__message_to_model__": (
                "Reproduction is BLOCKED: none of the requested datasets are runnable. State "
                "that the platform cannot reproduce this constraint and DO NOT quote any "
                "tension. " + model_msg
            ),
        }

    # 3. Reproduce deterministically (no emcee fallback -> closed-form, reproducible).
    reproduced = run_likelihood_chain(
        model=str(model or ""),
        dataset_keys=runnable,
        random_seed=random_seed,
        allow_emcee_fallback=False,
    )
    repro_params = reproduced.get("parameters") if isinstance(reproduced, dict) else None
    repro_params = repro_params if isinstance(repro_params, dict) else {}
    repro_pub_ready = bool(reproduced.get("publication_ready")) if isinstance(reproduced, dict) else False

    # canonical -> (actual_key, summary) from the reproduction, only where median exists.
    repro_canon: dict[str, tuple[str, dict]] = {}
    for actual_key, summary in repro_params.items():
        if isinstance(summary, dict) and summary.get("median") is not None:
            repro_canon[_canonical_param(actual_key)] = (actual_key, summary)

    overlap_pairs: list[str] = []
    unknown_claimed_datasets = sorted(key for key in claimed_ds if key not in _REGISTRY)
    for claimed_key in sorted(claimed_ds):
        claimed_entry = _REGISTRY.get(claimed_key)
        for runnable_key in runnable:
            runnable_entry = _REGISTRY.get(runnable_key)
            if claimed_entry is None or runnable_entry is None:
                continue
            same_group = bool(
                claimed_entry.independence_group
                and claimed_entry.independence_group == runnable_entry.independence_group
            )
            declared_overlap = (
                claimed_key == runnable_key
                or claimed_key in runnable_entry.do_not_combine_with
                or runnable_key in claimed_entry.do_not_combine_with
                or claimed_key in runnable_entry.known_overlap
                or runnable_key in claimed_entry.known_overlap
            )
            if same_group or declared_overlap:
                overlap_pairs.append(f"{claimed_key}<->{runnable_key}")
    overlap = sorted(set(overlap_pairs))
    independence = (
        "overlapping_data" if overlap
        else "independence_unverified" if unknown_claimed_datasets
        else "assumed_independent" if claimed_ds
        else "independence_unverified"
    )

    # 4. Compare each claimed parameter against the reproduction.
    comparisons: list[dict[str, Any]] = []
    any_reproduced = False
    for raw_name, raw_pair in claimed.items():
        canon = _canonical_param(raw_name)
        pair = _parse_claimed_pair(raw_pair)
        if pair is None:
            comparisons.append({
                "param": raw_name,
                "verdict": "INVALID_INPUT",
                "note": "claimed value must be [value] or [value, sigma].",
            })
            continue
        claimed_value, claimed_sig = pair
        if canon not in repro_canon:
            comparisons.append({
                "param": raw_name,
                "canonical": canon,
                "claimed": [claimed_value, claimed_sig],
                "verdict": "NOT_REPRODUCED",
                "note": "platform reproduction did not constrain this parameter with the "
                        "runnable datasets/model.",
            })
            continue
        any_reproduced = True
        actual_key, summary = repro_canon[canon]
        repro_median = float(summary.get("median"))
        repro_std = float(summary.get("std") or 0.0)
        if independence == "overlapping_data":
            comparisons.append({
                "param": raw_name,
                "canonical": canon,
                "reproduced_key": actual_key,
                "claimed": [claimed_value, claimed_sig],
                "reproduced": [repro_median, repro_std],
                "tension_sigma": None,
                "verdict": "OVERLAPPING_DATA_NOT_COMPARABLE",
                "independence": independence,
                "exploratory": True,
                "note": (
                    "The constraints share data/calibration. Independent-error "
                    "quadrature is invalid without their cross-covariance."
                ),
            })
            continue
        nsigma = tension_sigma(claimed_value, claimed_sig, repro_median, repro_std)
        comparisons.append({
            "param": raw_name,
            "canonical": canon,
            "reproduced_key": actual_key,
            "claimed": [claimed_value, claimed_sig],
            "reproduced": [repro_median, repro_std],
            "tension_sigma": round(nsigma, 3) if math.isfinite(nsigma) else None,
            "verdict": classify_tension(nsigma),
            "independence": independence,
            "exploratory": not repro_pub_ready,
            "note": (
                "Reproduced constraint is not publication-ready; treat this tension as "
                "exploratory only." if not repro_pub_ready else ""
            ),
        })

    # 5. Overall status.
    has_blocked_items = bool(unavailable) or any(
        c.get("verdict") in {
            "NOT_REPRODUCED",
            "INVALID_INPUT",
            "OVERLAPPING_DATA_NOT_COMPARABLE",
        }
        for c in comparisons
    )
    if not any_reproduced:
        tool_status, analysis_status = "BLOCKED", "BLOCKED"
    elif has_blocked_items:
        tool_status, analysis_status = "PARTIAL", "AUDIT_READY"
    else:
        tool_status, analysis_status = "COMPLETED", "AUDIT_READY"

    warnings = list(base_warnings)
    if independence == "overlapping_data":
        warnings.append(
            "Claimed datasets overlap the reproduction datasets (" + ", ".join(overlap) +
            "); n-sigma is withheld because no cross-covariance was supplied."
        )
    elif independence == "independence_unverified":
        warnings.append(
            "claimed_datasets not provided; n-sigma assumes the published and reproduced "
            "constraints are independent. If they share data, the tension is overstated."
        )
    if not repro_pub_ready:
        warnings.append(
            "Reproduced chain is not publication-ready (extended model or skipped datasets); "
            "all tensions are exploratory."
        )

    citations = list(reproduced.get("citations") or []) if isinstance(reproduced, dict) else []
    ref_id = paper_ref.get("bibcode") or paper_ref.get("arxiv") or paper_ref.get("doi")
    if ref_id:
        citations = [{
            "source": paper_ref.get("source", "published constraint"),
            "ref": ref_id,
            "role": "audited_paper",
        }] + citations

    return {
        "success": True,
        "__tool_status__": tool_status,
        "analysis_status": analysis_status,
        "data_origin": "cached_real",
        # Audit output is a comparison, never itself a citeable posterior.
        "publication_ready": False,
        "audit_report": {
            "model": str(model or ""),
            "datasets_run": runnable,
            "datasets_unavailable": unavailable,
            "reproduction_publication_ready": repro_pub_ready,
            "independence": independence,
            "comparisons": comparisons,
            "paper_ref": paper_ref,
        },
        "reproduced_constraint": reproduced,
        "citations": citations,
        "warnings": warnings,
        "__message_to_model__": model_msg,
    }

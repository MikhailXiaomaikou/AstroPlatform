"""Controlled posterior-chain diagnostics.

The paper-mining loop repeatedly identifies R-hat, ESS, trace/corner
diagnostics, and publication-readiness checks as a recurring paper tool.  This
module provides a typed numerical kernel for existing chains; it never runs a
likelihood or samples a model by itself.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.services.cosmology_likelihoods.verification import (
    PUBLICATION_ESS_MIN,
    PUBLICATION_MIN_INDEPENDENT_CHAINS,
    PUBLICATION_RHAT_MAX,
    _assess_publication_gate,
)


# Vehtari et al. (2021) recommend multiple independent chains and the modern
# rank-normalized split-R-hat.  Two chains are enough to *compute* a diagnostic,
# but are too fragile for this service's publication gate; require four.
MIN_CHAINS = PUBLICATION_MIN_INDEPENDENT_CHAINS
MIN_DRAWS_PER_CHAIN = 20
R_HAT_MAX = PUBLICATION_RHAT_MAX
ESS_MIN = PUBLICATION_ESS_MIN


def evaluate_chain_diagnostics(
    *,
    chains: dict[str, Any] | list[Any],
    parameters: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate R-hat/ESS-style diagnostics for explicit posterior chains."""
    try:
        parsed = _parse_chains(chains, parameters=parameters)
    except ValueError as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "publication_ready": False,
            "error": str(exc),
            "error_class": "invalid_chain_input",
            "__do_not_claim__": True,
        }

    diagnostics: dict[str, dict[str, Any]] = {}
    gate_diagnostics: dict[str, dict[str, float | None]] = {}
    warnings: list[str] = []
    for name, chain_arrays in parsed.items():
        stacked = np.vstack(chain_arrays)
        rhat = _rhat(chain_arrays)
        ess = _ess(chain_arrays)
        values = stacked.reshape(-1)
        status = "ok"
        if rhat is None:
            status = "rhat_unavailable"
            warnings.append(
                f"{name}: publication-grade rank-normalized R-hat requires at least "
                f"{MIN_CHAINS} non-degenerate chains."
            )
        elif not math.isfinite(rhat):
            status = "rhat_nonfinite"
            warnings.append(f"{name}: R-hat is non-finite; the chains are degenerate or not comparable.")
        elif rhat >= R_HAT_MAX:
            status = "rhat_high"
            warnings.append(f"{name}: R-hat={rhat:.3f} exceeds {R_HAT_MAX}.")
        if ess < ESS_MIN:
            status = "ess_low" if status == "ok" else f"{status}+ess_low"
            warnings.append(f"{name}: ESS={ess:.1f} is below {ESS_MIN}.")
        diagnostics[name] = {
            "mean": round(float(np.mean(values)), 6),
            "std": round(float(np.std(values, ddof=1)), 6) if values.size > 1 else 0.0,
            "median": round(float(np.median(values)), 6),
            "hdi_low_94": round(float(np.percentile(values, 3.0)), 6),
            "hdi_high_94": round(float(np.percentile(values, 97.0)), 6),
            "rhat": round(float(rhat), 6) if rhat is not None else None,
            "ess_bulk": round(float(ess), 3),
            "mcse_mean": round(float(np.std(values, ddof=1) / math.sqrt(max(ess, 1.0))), 6)
            if values.size > 1
            else 0.0,
            "n_chains": len(chain_arrays),
            "draws_per_chain": int(min(len(arr) for arr in chain_arrays)),
            "status": status,
        }
        gate_diagnostics[name] = {
            "rhat": float(rhat) if rhat is not None else None,
            "ess_bulk": float(ess),
        }

    n_independent_chains = min(item["n_chains"] for item in diagnostics.values())
    convergence_gate = _assess_publication_gate(
        cov_fidelity=None,
        likelihood_is_compressed_or_approximate=False,
        n_independent_chains=n_independent_chains,
        per_parameter=gate_diagnostics,
        critical_parameters=list(diagnostics),
        # This standalone tool has no likelihood/data receipt.  Reuse only the
        # shared convergence half of the gate, then fail scientific publication
        # closed below with an explicit provenance reason.
        assess_data_likelihood=False,
    )
    convergence_ready = bool(convergence_gate["eligible"])
    publication_gate = {
        **convergence_gate,
        "eligible": False,
        "reasons": [*convergence_gate["reasons"], "missing_likelihood_provenance"],
        "observed": {
            **convergence_gate["observed"],
            "likelihood_provenance_available": False,
        },
    }
    publication_ready = False
    warnings.append(
        "Standalone chain diagnostics do not include the fitted likelihood, data "
        "checksums, covariance fidelity, or runner provenance; scientific "
        "publication readiness cannot be certified here."
    )
    return {
        "success": True,
        "__tool_status__": "COMPLETED" if convergence_ready else "PARTIAL",
        "analysis_status": "CHAIN_DIAGNOSTICS_READY" if convergence_ready else "PARTIAL",
        "publication_ready": publication_ready,
        "convergence_ready": convergence_ready,
        "diagnostics_ready": convergence_ready,
        "publication_gate": publication_gate,
        "publication_reasons": list(publication_gate["reasons"]),
        "claim_scope": "posterior_chain_diagnostics",
        "scientific_claim_scope": "diagnostics_only",
        "parameters": diagnostics,
        "chain_diagnostics": {
            "overall_status": "ok" if convergence_ready else "not_converged",
            "convergence_ready": convergence_ready,
            "diagnostics_ready": convergence_ready,
            "publication_ready": publication_ready,
            "publication_gate": publication_gate,
            "n_independent_chains": n_independent_chains,
            "parameter_count": len(diagnostics),
            "thresholds": {
                "min_chains": MIN_CHAINS,
                "min_draws_per_chain": MIN_DRAWS_PER_CHAIN,
                "rhat_method": "rank",
                "rhat_max": R_HAT_MAX,
                "rhat_max_exclusive": R_HAT_MAX,
                "ess_method": "bulk",
                "ess_min": ESS_MIN,
            },
        },
        "warnings": warnings,
        "__message_to_model__": (
            "This tool diagnoses supplied posterior chains only. It can support "
            "claims about rank-normalized R-hat and bulk ESS when convergence_ready=true, "
            "but it cannot certify scientific publication readiness without the fitted "
            "likelihood, verified data products, and runner provenance."
        ),
        "__do_not_claim__": [
            *(
                []
                if convergence_ready
                else ["Do not call these chains converged; inspect the diagnostics first."]
            ),
            "Do not use standalone chain diagnostics as a scientific publication gate; likelihood/data provenance is missing.",
        ],
    }


def _parse_chains(
    chains: dict[str, Any] | list[Any],
    *,
    parameters: list[str] | None,
) -> dict[str, list[np.ndarray]]:
    if isinstance(chains, dict):
        parsed: dict[str, list[np.ndarray]] = {}
        selected = [str(p) for p in parameters] if parameters else list(chains)
        for name in selected:
            raw = chains.get(name)
            arrays = _arrays_from_raw(raw)
            if arrays:
                parsed[name] = arrays
        if not parsed:
            raise ValueError("no parseable parameter chains found")
        return parsed
    if isinstance(chains, list):
        return _parse_list_of_chain_rows(chains, parameters=parameters)
    raise ValueError("chains must be an object or a list")


def _arrays_from_raw(raw: Any) -> list[np.ndarray]:
    if not isinstance(raw, list) or not raw:
        return []
    if all(isinstance(item, (int, float)) for item in raw):
        arr = _valid_array(raw)
        return [arr] if arr is not None else []
    arrays: list[np.ndarray] = []
    for item in raw:
        if isinstance(item, list):
            arr = _valid_array(item)
            if arr is not None:
                arrays.append(arr)
    return arrays


def _parse_list_of_chain_rows(chains: list[Any], *, parameters: list[str] | None) -> dict[str, list[np.ndarray]]:
    if not chains:
        raise ValueError("chains list is empty")
    if all(isinstance(row, dict) for row in chains):
        names = [str(p) for p in parameters] if parameters else sorted(
            {
                str(key)
                for row in chains
                if isinstance(row, dict)
                for key, value in row.items()
                if isinstance(value, (int, float))
            }
        )
        result: dict[str, list[np.ndarray]] = {}
        for name in names:
            arr = _valid_array([row.get(name) for row in chains if isinstance(row, dict)])
            if arr is not None:
                result[name] = [arr]
        if not result:
            raise ValueError("no numeric columns found in chain rows")
        return result
    raise ValueError("list chains must be numeric arrays or rows of objects")


def _valid_array(values: list[Any]) -> np.ndarray | None:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < MIN_DRAWS_PER_CHAIN:
        return None
    return arr


def _rhat(chains: list[np.ndarray]) -> float | None:
    """Return rank-normalized split-R-hat, or ``None`` when unavailable.

    The previous hand-written estimator returned 1.0 whenever within-chain
    variance was zero.  That certified chains stuck at different constants as
    perfectly converged.  Publication readiness now depends on ArviZ's
    rank-normalized split diagnostic and fails closed if it cannot be computed.
    """
    if len(chains) < MIN_CHAINS:
        return None
    n = min(len(chain) for chain in chains)
    if n < MIN_DRAWS_PER_CHAIN:
        return None
    trimmed = np.asarray([chain[:n] for chain in chains], dtype=float)
    if np.any(np.var(trimmed, axis=1, ddof=1) <= 0.0):
        return None
    try:
        import arviz as az

        idata = az.from_dict(posterior={"value": trimmed})
        value = az.rhat(idata, var_names=["value"], method="rank")["value"]
        result = float(value.values) if hasattr(value, "values") else float(value)
        return result if math.isfinite(result) else None
    except Exception:
        # A missing/failed diagnostic is not evidence of convergence.
        return None


def _ess(chains: list[np.ndarray]) -> float:
    """Return chain-aware bulk ESS; never concatenate chain boundaries."""
    if len(chains) < MIN_CHAINS:
        return 0.0
    n = min(len(chain) for chain in chains)
    if n < MIN_DRAWS_PER_CHAIN:
        return 0.0
    trimmed = np.asarray([chain[:n] for chain in chains], dtype=float)
    if not np.all(np.isfinite(trimmed)) or float(np.var(trimmed)) <= 0.0:
        return 0.0
    try:
        import arviz as az

        idata = az.from_dict(posterior={"value": trimmed})
        value = az.ess(idata, var_names=["value"], method="bulk")["value"]
        result = float(value.values) if hasattr(value, "values") else float(value)
        return result if math.isfinite(result) and result > 0.0 else 0.0
    except Exception:
        return 0.0

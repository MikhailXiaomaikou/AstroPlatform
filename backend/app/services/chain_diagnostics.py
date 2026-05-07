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


MIN_CHAINS = 2
MIN_DRAWS_PER_CHAIN = 20
R_HAT_MAX = 1.05
ESS_MIN = 400.0


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
    warnings: list[str] = []
    for name, chain_arrays in parsed.items():
        stacked = np.vstack(chain_arrays)
        rhat = _rhat(chain_arrays)
        ess = _ess(chain_arrays)
        values = stacked.reshape(-1)
        status = "ok"
        if rhat is None:
            status = "single_chain_no_rhat"
            warnings.append(f"{name}: at least two chains are required for R-hat.")
        elif rhat > R_HAT_MAX:
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

    publication_ready = all(item["status"] == "ok" for item in diagnostics.values())
    return {
        "success": True,
        "__tool_status__": "COMPLETED" if publication_ready else "PARTIAL",
        "analysis_status": "CHAIN_DIAGNOSTICS_READY" if publication_ready else "PARTIAL",
        "publication_ready": publication_ready,
        "claim_scope": "posterior_chain_diagnostics",
        "parameters": diagnostics,
        "chain_diagnostics": {
            "overall_status": "ok" if publication_ready else "not_publication_ready",
            "publication_ready": publication_ready,
            "parameter_count": len(diagnostics),
            "thresholds": {
                "min_chains": MIN_CHAINS,
                "min_draws_per_chain": MIN_DRAWS_PER_CHAIN,
                "rhat_max": R_HAT_MAX,
                "ess_min": ESS_MIN,
            },
        },
        "warnings": warnings,
        "__message_to_model__": (
            "This tool diagnoses supplied posterior chains only. It can support "
            "claims about convergence diagnostics, not cosmological parameter "
            "constraints unless those chains came from a publication-ready runner."
        ),
        "__do_not_claim__": (
            [] if publication_ready else [
                "Do not call these chains converged or publication-ready; inspect the diagnostics first."
            ]
        ),
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
    if len(chains) < MIN_CHAINS:
        return None
    n = min(len(chain) for chain in chains)
    if n < MIN_DRAWS_PER_CHAIN:
        return None
    trimmed = np.asarray([chain[:n] for chain in chains], dtype=float)
    chain_means = np.mean(trimmed, axis=1)
    chain_vars = np.var(trimmed, axis=1, ddof=1)
    between = n * float(np.var(chain_means, ddof=1))
    within = float(np.mean(chain_vars))
    if within <= 0:
        return 1.0
    var_hat = ((n - 1) / n) * within + between / n
    return math.sqrt(max(var_hat / within, 0.0))


def _ess(chains: list[np.ndarray]) -> float:
    values = np.concatenate(chains)
    n_total = values.size
    if n_total < MIN_DRAWS_PER_CHAIN:
        return 0.0
    centered = values - np.mean(values)
    variance = float(np.var(centered))
    if variance <= 0:
        return float(n_total)
    max_lag = min(1000, n_total // 2)
    positive_sum = 0.0
    for lag in range(1, max_lag):
        rho = float(np.dot(centered[:-lag], centered[lag:]) / ((n_total - lag) * variance))
        if rho <= 0:
            break
        positive_sum += rho
    tau = 1.0 + 2.0 * positive_sum
    return float(n_total / max(tau, 1.0))

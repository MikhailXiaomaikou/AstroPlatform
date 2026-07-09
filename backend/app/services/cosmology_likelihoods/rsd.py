"""eBOSS fsigma8 RSD family: loader, predictions and chi^2.

Split verbatim out of the pre-2026-07-03 single-file
app/services/cosmology_likelihoods.py (7,757 lines). Import the package
``app.services.cosmology_likelihoods`` — it re-exports every pre-split name
and keeps the original one-namespace monkeypatch semantics.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np

from app.services.cosmology_likelihoods.core import (
    logger,
)

from app.services.cosmology_likelihoods.data_io import (
    _load_verified_diagonal_vector,
)

from app.services.cosmology_likelihoods.growth import (
    _growth_factor_ratio,
    _growth_rate_f,
)



# ── eBOSS DR16 RSD fσ8 executable likelihood (2026-05-29) ───────────────────
# 6 RSD-only growth-rate measurements fσ8(z_eff) [dimensionless] from the SDSS
# lineage, read directly from Alam et al. 2021 (eBOSS DR16 cosmological
# implications, arXiv:2007.08991) Table III, "RSD-Only Measurements" column —
# the values marginalised over the BAO distances D_M/r_d, D_H/r_d, so they are
# the clean standalone growth probe to combine with a SEPARATE BAO dataset
# (e.g. DESI) without double-counting distances.  Table III footnote (a) states
# the per-tracer uncertainties are Gaussian approximations "ignoring the
# correlations between measurements", so a DIAGONAL covariance is exactly the
# published Gaussian approximation, not a shortcut.  SDSS-only: 6dFGS is
# excluded (the paper does not include it) and Lyα (z=2.33) reports no fσ8,
# so the executable vector is 6 points (not the 7 the registry notes implied).
EBOSS_DR16_FSIGMA8_EXECUTABLE_KEYS = {"eboss_dr16_rsd"}
# Legacy hand-typed eBOSS fσ8 — kept only as the loader fallback. Byte-derived
# into data/cosmology/eboss_dr16_rsd/fsigma8.txt, which is what the fit reads
# (provenance-binding, T1-U3/U4).
_HARDCODED_EBOSS_FSIGMA8: tuple[tuple[float, float, float], ...] = (
    (0.15, 0.53, 0.16),    # SDSS MGS
    (0.38, 0.500, 0.047),  # BOSS Galaxy
    (0.51, 0.455, 0.039),  # BOSS Galaxy
    (0.70, 0.448, 0.043),  # eBOSS LRG
    (0.85, 0.315, 0.095),  # eBOSS ELG
    (1.48, 0.462, 0.045),  # eBOSS QSO
)


@lru_cache(maxsize=None)
def _load_rsd_raw(dataset_key: str) -> dict[str, Any]:
    """Cached verified (or honest literature_typed) eBOSS fσ8 record; raises
    ValueError (message contains 'unverified') on any failed verification —
    missing-but-pinned, tampered or malformed vendored file. lru_cache never
    caches exceptions, so one transient failure cannot poison the process
    until restart (the union3 pattern)."""
    raw = _load_verified_diagonal_vector(dataset_key, "fsigma8.txt", "rsd_measurement_vector")
    if raw["cov_fidelity"] == "unverified":
        raise ValueError(
            f"eBOSS RSD fsigma8 data product {dataset_key} is unverified "
            "(missing, tampered or malformed vendored file)."
        )
    return {
        "fsigma8_vector": raw["vector"] if raw["vector"] is not None else tuple(_HARDCODED_EBOSS_FSIGMA8),
        "sha256": raw["sha256"],
        "hash_verified": raw["hash_verified"],
        "cov_fidelity": raw["cov_fidelity"],
    }


def load_verified_rsd_data(dataset_key: str) -> dict[str, Any]:
    """Load the eBOSS RSD fσ8 vector from the vendored, sha256-pinned file so the
    fitted vector IS the checksummed array (object identity).  cov_fidelity is
    'diagonal' on success (only per-tracer diagonal errors are published,
    Alam+2021 Table III note a; the full 6×6 inter-bin covariance is a separate
    offline reconstruction), 'unverified' on a missing-but-pinned or corrupt
    file.  The fitted vector falls back to the hand-typed values to keep the fit
    running; the 'unverified' fidelity then blocks publication. NEVER raises;
    the refusal record is rebuilt fresh per call — the cached inner loader
    raises instead of caching a failure record, so a transient read failure
    self-heals without a process restart (union3 raise-inside-cache pattern)."""
    try:
        return _load_rsd_raw(dataset_key)
    except ValueError as exc:
        logger.warning("eBOSS RSD data product %s failed verification: %s", dataset_key, exc)
        return {
            "fsigma8_vector": tuple(_HARDCODED_EBOSS_FSIGMA8),
            "sha256": None, "hash_verified": False, "cov_fidelity": "unverified",
        }


# Historical cache-management API (tests clear the loader by its public name);
# the real cache lives on the raising inner loader.
load_verified_rsd_data.cache_clear = _load_rsd_raw.cache_clear  # type: ignore[attr-defined]


# The fσ8 vector the chi² fits — sourced from the verified loader so the fit
# reads the sha256-pinned committed artifact, not a hand-typed copy.
EBOSS_DR16_FSIGMA8: tuple[tuple[float, float, float], ...] = load_verified_rsd_data(
    "eboss_dr16_rsd"
)["fsigma8_vector"]


def _eboss_fsigma8_predictions(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """Predicted fσ8(z) = f(z)·σ8·D(z)/D(0) at the 6 eBOSS RSD effective
    redshifts for each posterior sample.  Needs omegam + sigma8 in parameter
    order (added for RSD selections); fσ8 is H0-independent."""
    omegam = samples[:, parameter_order.index("omegam")]
    sigma8 = samples[:, parameter_order.index("sigma8")]
    n_samples = samples.shape[0]
    if "w0" in parameter_order:
        w0 = samples[:, parameter_order.index("w0")]
    elif "w" in parameter_order:
        w0 = samples[:, parameter_order.index("w")]
    else:
        w0 = np.full(n_samples, -1.0, dtype=float)
    wa = samples[:, parameter_order.index("wa")] if "wa" in parameter_order else np.zeros(n_samples)
    fsigma8_vector = load_verified_rsd_data("eboss_dr16_rsd")["fsigma8_vector"]
    predictions = np.empty((n_samples, len(fsigma8_vector)), dtype=float)
    for col, (z, _v, _s) in enumerate(fsigma8_vector):
        f_z = _growth_rate_f(z, omegam, w0, wa)
        d_ratio = _growth_factor_ratio(z, omegam, w0, wa)
        predictions[:, col] = f_z * sigma8 * d_ratio
    return predictions


def _eboss_fsigma8_chi2_samples(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """Diagonal-covariance χ² of the 6-point eBOSS DR16 RSD fσ8 vector
    (Alam+2021 Table III footnote a: per-tracer Gaussian, correlations ignored).

    Reads the fresh verified record (not the import-time EBOSS_DR16_FSIGMA8
    snapshot) so a self-healed loader also heals the fitted bytes."""
    fsigma8_vector = load_verified_rsd_data("eboss_dr16_rsd")["fsigma8_vector"]
    observed = np.asarray([row[1] for row in fsigma8_vector], dtype=float)
    sigma = np.asarray([row[2] for row in fsigma8_vector], dtype=float)
    predictions = _eboss_fsigma8_predictions(samples, parameter_order)
    residual = predictions - observed
    return np.sum((residual / sigma) ** 2, axis=1)

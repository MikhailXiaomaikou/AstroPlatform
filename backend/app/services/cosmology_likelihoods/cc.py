"""Cosmic-chronometer H(z) family: loaders, predictions and chi^2.

Split verbatim out of the pre-2026-07-03 single-file
app/services/cosmology_likelihoods.py (7,757 lines). Import the package
``app.services.cosmology_likelihoods`` — it re-exports every pre-split name
and keeps the original one-namespace monkeypatch semantics.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any

import numpy as np

from app.services.cosmology_likelihoods.core import (
    CosmologyDatasetEntry,
    logger,
)

from app.services.cosmology_likelihoods.data_io import (
    _VENDORED_COSMO_DATA_DIR,
    _load_verified_diagonal_vector,
    _registry_product_sha256,
)

from app.services.cosmology_likelihoods.distances import (
    _de_energy_density,
)



# ── Cosmic-chronometer H(z) executable likelihood (2026-05-29) ──────────────
# 31 differential-age H(z) measurements [km/s/Mpc] compiled by Gómez-Valent &
# Amendola 2018 (JCAP 04, 051; arXiv:1802.01505) Table 1, which collects the
# cosmic-chronometer points of Zhang+2014, Jimenez+2003, Simon+2005, Stern+2010,
# Moresco+2012/2015, Moresco+2016 and Ratsimbazafy+2017.  That paper uses a
# DIAGONAL covariance (D_ij = σ_i² δ_ij) and we follow it.  The fuller Moresco
# et al. 2020 (arXiv:2003.07362) systematic covariance is a documented refinement
# that is NOT applied here — keeping this a preliminary-grade, growth-independent
# expansion-rate probe, consistent with the registry entry's standing warning.
COSMIC_CHRONOMETER_EXECUTABLE_KEYS = {"cosmic_chronometers"}
# Legacy hand-typed CC H(z) — kept only as the loader fallback for environments
# missing the vendored file. Byte-derived into data/cosmology/cosmic_chronometers/
# hz.txt, which is what the fit actually reads (provenance-binding, T1-U1/U2).
_HARDCODED_CC_HZ: tuple[tuple[float, float, float], ...] = (
    (0.07, 69.0, 19.6), (0.09, 69.0, 12.0), (0.12, 68.6, 26.2), (0.17, 83.0, 8.0),
    (0.1791, 75.0, 4.0), (0.1993, 75.0, 5.0), (0.2, 72.9, 29.6), (0.27, 77.0, 14.0),
    (0.28, 88.8, 36.6), (0.3519, 83.0, 14.0), (0.3802, 83.0, 13.5), (0.4, 95.0, 17.0),
    (0.4004, 77.0, 10.2), (0.4247, 87.1, 11.2), (0.4497, 92.8, 12.9), (0.47, 89.0, 49.6),
    (0.4783, 80.9, 9.0), (0.48, 97.0, 62.0), (0.5929, 104.0, 13.0), (0.6797, 92.0, 8.0),
    (0.7812, 105.0, 12.0), (0.8754, 125.0, 17.0), (0.88, 90.0, 40.0), (0.9, 117.0, 23.0),
    (1.037, 154.0, 20.0), (1.3, 168.0, 17.0), (1.363, 160.0, 33.6), (1.43, 177.0, 18.0),
    (1.53, 140.0, 14.0), (1.75, 202.0, 40.0), (1.965, 186.5, 50.4),
)


@lru_cache(maxsize=None)
def _load_cc_raw(dataset_key: str) -> dict[str, Any]:
    """Cached verified (or honest literature_typed) CC H(z) record; raises
    ValueError (message contains 'unverified') on any failed verification —
    missing-but-pinned, tampered or malformed vendored file. lru_cache never
    caches exceptions, so one transient failure cannot poison the process
    until restart (the union3 pattern)."""
    raw = _load_verified_diagonal_vector(dataset_key, "hz.txt", "hz_measurement_vector")
    if raw["cov_fidelity"] == "unverified":
        raise ValueError(
            f"cosmic-chronometer H(z) data product {dataset_key} is unverified "
            "(missing, tampered or malformed vendored file)."
        )
    return {
        "hz_vector": raw["vector"] if raw["vector"] is not None else tuple(_HARDCODED_CC_HZ),
        "sha256": raw["sha256"],
        "hash_verified": raw["hash_verified"],
        "cov_fidelity": raw["cov_fidelity"],
    }


def load_verified_cc_data(dataset_key: str) -> dict[str, Any]:
    """Load the cosmic-chronometer H(z) vector from the vendored, sha256-pinned
    file so the fitted vector IS the checksummed array (object identity).
    cov_fidelity is 'diagonal' on success (diagonal covariance; the Moresco+2020
    systematic covariance is a separate offline upgrade, distinct from a released
    full covariance), 'unverified' on a missing-but-pinned or corrupt file.  The
    fitted vector falls back to the hand-typed values only to keep the fit
    running; the 'unverified' fidelity then blocks publication. NEVER raises;
    the refusal record is rebuilt fresh per call — the cached inner loader
    raises instead of caching a failure record, so a transient read failure
    self-heals without a process restart (union3 raise-inside-cache pattern)."""
    try:
        return _load_cc_raw(dataset_key)
    except ValueError as exc:
        logger.warning("CC H(z) data product %s failed verification: %s", dataset_key, exc)
        return {
            "hz_vector": tuple(_HARDCODED_CC_HZ),
            "sha256": None, "hash_verified": False, "cov_fidelity": "unverified",
        }


# Historical cache-management API (tests clear the loader by its public name);
# the real cache lives on the raising inner loader.
load_verified_cc_data.cache_clear = _load_cc_raw.cache_clear  # type: ignore[attr-defined]


# The H(z) vector the chi² fits — sourced from the verified loader so the fit
# reads the sha256-pinned committed artifact, not a hand-typed copy.
COSMIC_CHRONOMETER_HZ: tuple[tuple[float, float, float], ...] = load_verified_cc_data(
    "cosmic_chronometers"
)["hz_vector"]


# ── Moresco 2020 cosmic-chronometer FULL systematic covariance (2026-06-05) ──
# 15-point BC03 H(z) sample (Moresco 2012/2015/2016) with the full Moresco et al.
# 2020 (arXiv:2003.07362) systematic covariance, reproduced from the vendored,
# sha256-pinned raw source files by scripts/gen_moresco20_cc_covariance.py
# (faithful port of gitlab.com/mmoresco/CCcovariance). Distinct from the GA2018
# 31-point diagonal compilation (key "cosmic_chronometers"), which is unchanged.
COSMIC_CHRONOMETER_FULL_COV_KEYS = {"cosmic_chronometers_moresco20"}


@lru_cache(maxsize=None)
def _load_cc_full_cov_raw(dataset_key: str) -> dict[str, Any]:
    """Load + sha256-verify a vendored CC mean.txt + full-cov cov.txt; raises
    ValueError (message always contains 'unverified') on ANY failure —
    missing, unreadable, digest mismatch, malformed. lru_cache never caches
    exceptions, so one transient failure cannot poison the process until
    restart (the union3 pattern)."""
    mean_path = _VENDORED_COSMO_DATA_DIR / dataset_key / "mean.txt"
    cov_path = _VENDORED_COSMO_DATA_DIR / dataset_key / "cov.txt"
    pinned_cov = _registry_product_sha256(dataset_key, "covariance")
    pinned_mean = _registry_product_sha256(dataset_key, "hz_measurement_vector")
    if not (mean_path.exists() and cov_path.exists()):
        raise ValueError(
            f"CC full-cov data product {dataset_key} is unverified: vendored "
            f"mean.txt/cov.txt missing under {_VENDORED_COSMO_DATA_DIR / dataset_key}."
        )
    try:
        mean_digest = hashlib.sha256(mean_path.read_bytes()).hexdigest()
        cov_digest = hashlib.sha256(cov_path.read_bytes()).hexdigest()
        rows: list[tuple[float, float, float]] = []
        for line in mean_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            z_str, value_str, _quantity = stripped.split()
            rows.append((float(z_str), float(value_str), 0.0))
        covariance = np.loadtxt(cov_path)
        n = len(rows)
        if n == 0 or covariance.shape != (n, n):
            raise ValueError(f"mean/cov shape mismatch: {n} points vs cov {covariance.shape}")
    except Exception as exc:  # malformed/truncated/unreadable file
        raise ValueError(
            f"CC full-cov data product {dataset_key} is unverified: failed to load ({exc})."
        ) from exc
    if not ((mean_digest == pinned_mean) and (cov_digest == pinned_cov)):
        raise ValueError(
            f"CC full-cov data product {dataset_key} is unverified: vendored file "
            "bytes do not match the registry sha256 pins; refusing to fit tampered "
            "or stale data."
        )
    return {
        "hz_vector": tuple(rows),
        "covariance": covariance,
        "sha256": cov_digest,
        "mean_sha256": mean_digest,
        "hash_verified": True,
        "cov_fidelity": "full",
    }


def load_verified_cc_full_cov_data(dataset_key: str) -> dict[str, Any]:
    """Load a CC (z, H(z)) vector + FULL covariance from the vendored, sha256-pinned
    mean.txt / cov.txt so the fitted covariance IS the checksum-verified array.
    cov_fidelity is 'full' on success, 'unverified' on a missing-but-pinned or
    corrupt file (which blocks publication).  A full covariance has no honest
    hand-typed substitute, so there is no literature_typed fallback. NEVER
    raises; the 'unverified' refusal is rebuilt fresh per call — the cached
    inner loader raises instead of caching a failure record, so a transient
    read failure self-heals without a process restart (union3
    raise-inside-cache pattern)."""
    try:
        return _load_cc_full_cov_raw(dataset_key)
    except ValueError as exc:
        logger.warning("CC full-cov data product %s failed verification: %s", dataset_key, exc)
        return {
            "hz_vector": None, "covariance": None, "sha256": None,
            "hash_verified": False, "cov_fidelity": "unverified",
        }


# Historical cache-management API (tests clear the loader by its public name);
# the real cache lives on the raising inner loader.
load_verified_cc_full_cov_data.cache_clear = _load_cc_full_cov_raw.cache_clear  # type: ignore[attr-defined]


# (z, H(z)) the moresco20 χ² fits + its inverse covariance — sourced from the
# verified loader so the fit reads the sha256-pinned committed artifacts.
_MORESCO20_RAW = load_verified_cc_full_cov_data("cosmic_chronometers_moresco20")
COSMIC_CHRONOMETER_MORESCO20_HZ: tuple[tuple[float, float, float], ...] = (
    _MORESCO20_RAW["hz_vector"] if _MORESCO20_RAW["hz_vector"] is not None else ()
)
_MORESCO20_COV_INV: np.ndarray | None = (
    np.linalg.inv(_MORESCO20_RAW["covariance"])
    if _MORESCO20_RAW["covariance"] is not None
    else None
)


def _cc_entry_point_count(entry: CosmologyDatasetEntry) -> int:
    """Number of H(z) data points an executable CC entry contributes — the
    GA2018 31-point vector vs the Moresco-2020 15-point vector — so the BIC
    sample size is the real per-entry length, not a single hard-coded constant."""
    if entry.key in COSMIC_CHRONOMETER_FULL_COV_KEYS:
        return len(COSMIC_CHRONOMETER_MORESCO20_HZ)
    return len(COSMIC_CHRONOMETER_HZ)


def _hz_predictions_for(
    samples: np.ndarray, parameter_order: list[str],
    vector: tuple[tuple[float, float, float], ...],
) -> np.ndarray:
    """Predicted H(z) [km/s/Mpc] for each posterior sample at the redshifts of
    ``vector`` (rows ``(z, H_obs, σ)``).  H(z) = H0·E(z) is closed-form for the
    flat w0waCDM kernel — no comoving-distance integral needed."""
    h0 = samples[:, parameter_order.index("H0")]
    omegam = samples[:, parameter_order.index("omegam")]
    n_samples = samples.shape[0]
    if "w0" in parameter_order:
        w0 = samples[:, parameter_order.index("w0")]
    elif "w" in parameter_order:
        w0 = samples[:, parameter_order.index("w")]
    else:
        w0 = np.full(n_samples, -1.0, dtype=float)
    wa = samples[:, parameter_order.index("wa")] if "wa" in parameter_order else np.zeros(n_samples)
    predictions = np.empty((n_samples, len(vector)), dtype=float)
    for col, (z, _h_obs, _sigma) in enumerate(vector):
        rho_de = _de_energy_density(1.0 / (1.0 + z), w0, wa)
        ez = np.sqrt(omegam * (1.0 + z) ** 3 + (1.0 - omegam) * rho_de)
        predictions[:, col] = h0 * ez
    return predictions


def _cosmic_chronometer_hz_predictions(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """Predicted H(z) at the 31 GA2018 cosmic-chronometer redshifts."""
    hz_vector = load_verified_cc_data("cosmic_chronometers")["hz_vector"]
    return _hz_predictions_for(samples, parameter_order, hz_vector)


def _cosmic_chronometer_chi2_samples(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """Diagonal-covariance χ² of the 31-point cosmic-chronometer H(z) vector.

    Gómez-Valent & Amendola 2018 use a diagonal covariance for this compilation,
    so χ² = Σ_i ((H_pred(z_i) − H_obs_i) / σ_i)².

    Reads the fresh verified record (not the import-time COSMIC_CHRONOMETER_HZ
    snapshot) so a self-healed loader also heals the fitted bytes."""
    hz_vector = load_verified_cc_data("cosmic_chronometers")["hz_vector"]
    observed = np.asarray([row[1] for row in hz_vector], dtype=float)
    sigma = np.asarray([row[2] for row in hz_vector], dtype=float)
    predictions = _hz_predictions_for(samples, parameter_order, hz_vector)
    residual = predictions - observed
    return np.sum((residual / sigma) ** 2, axis=1)


def _cosmic_chronometer_moresco20_chi2_samples(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """Full-covariance χ² of the 15-point Moresco 2020 cosmic-chronometer H(z)
    vector: χ² = rᵀ C⁻¹ r with the reproduced systematic covariance C."""
    if (
        load_verified_cc_full_cov_data("cosmic_chronometers_moresco20")["cov_fidelity"] == "unverified"
        or _MORESCO20_COV_INV is None
        or not COSMIC_CHRONOMETER_MORESCO20_HZ
    ):
        raise ValueError(
            "Moresco-2020 CC covariance failed sha256 verification (or its vendored "
            "file is missing); refusing to compute chi2 from unverified data."
        )
    observed = np.asarray([row[1] for row in COSMIC_CHRONOMETER_MORESCO20_HZ], dtype=float)
    predictions = _hz_predictions_for(samples, parameter_order, COSMIC_CHRONOMETER_MORESCO20_HZ)
    residual = predictions - observed
    return np.einsum("ni,ij,nj->n", residual, _MORESCO20_COV_INV, residual)

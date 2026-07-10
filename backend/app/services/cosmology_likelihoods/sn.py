"""Supernova family: Pantheon+/DES-SN5YR/Union3/Pantheon18 loaders and chi^2.

Split verbatim out of the pre-2026-07-03 single-file
app/services/cosmology_likelihoods.py (7,757 lines). Import the package
``app.services.cosmology_likelihoods`` — it re-exports every pre-split name
and keeps the original one-namespace monkeypatch semantics.
"""

from __future__ import annotations

import hashlib
import io
import os
import pathlib
from functools import lru_cache
from typing import Any

import numpy as np

from app.services.cosmology_likelihoods.core import (
    logger,
)

from app.services.cosmology_likelihoods.data_io import (
    _registry_product_sha256,
)

from app.services.cosmology_likelihoods.distances import (
    _flat_de_dm_grid_vectorized,
)



# M6 (2026-05-18): Pantheon+SH0ES Python chi² runner — bypasses external
# Cobaya for the SN-distance-modulus likelihood.  1701 SNe + full
# stat+sys covariance from the 2022 data release, loaded lazily from
# backend/data/pantheon_plus_2022/data.npz.  It is intentionally opt-in:
# default chat/research matrices use the fast registered compressed summary
# above so a multi-cell workflow does not hang on the full covariance χ².
PANTHEON_PLUS_FULL_CHI2_ENABLED = os.getenv("PANTHEON_PLUS_FULL_CHI2_ENABLED", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
PANTHEON_PLUS_EXECUTABLE_KEYS = {"pantheon_plus"} if PANTHEON_PLUS_FULL_CHI2_ENABLED else set()

# DES-SN5YR full distance-modulus χ² is opt-in for the same reason as Pantheon+:
# the 1829×1829 covariance is slow per-sample, and the default research path uses
# the fast compressed Ωm summary. Off -> des_sn5yr stays the compressed Ωm entry.
DES_SN5YR_FULL_CHI2_ENABLED = os.getenv("DES_SN5YR_FULL_CHI2_ENABLED", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DES_SN5YR_EXECUTABLE_KEYS = {"des_sn5yr"} if DES_SN5YR_FULL_CHI2_ENABLED else set()

# Pantheon (2018, Scolnic et al.) 1048-SN full vector — env-gated like DES
# (the 1048x1048 per-sample cost is prohibitive for importance sampling; the
# enabled path always takes emcee). Default fast path is the compressed
# SN-only Omega_m Gaussian on the registry entry.
PANTHEON18_FULL_CHI2_ENABLED = os.getenv("PANTHEON18_FULL_CHI2_ENABLED", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
PANTHEON18_EXECUTABLE_KEYS = {"pantheon18"} if PANTHEON18_FULL_CHI2_ENABLED else set()

# Union3's full 22-bin binned-distance likelihood is ALWAYS on — no env flag.
# The DES flag above exists purely for the 1829x1829 per-sample cost; a 22x22
# covariance has no cost worth gating, and the default path SHOULD be the
# released likelihood, not the 1D compressed approximation (2026-06-12).
UNION3_EXECUTABLE_KEYS = frozenset({"union3"})


# M6 (2026-05-18): Pantheon+SH0ES 2022 data loader.  Lazy-loaded from a
# ~20 MB npz committed alongside the source code (see
# scripts/fetch_pantheon_plus.py for the regeneration script).  A scientifically
# usable SH0ES likelihood needs more than MU_SH0ES: it needs the released
# corrected apparent magnitude, calibrator flag and Cepheid-host distance so
# calibrators can be compared to CEPH_DIST instead of a cosmological distance.
# Older bundles missing those columns remain hash-verifiable artifacts, but are
# explicitly *not* executable SH0ES/H0 likelihoods.
_PANTHEON_PLUS_DATA_DIR = (
    pathlib.Path(__file__).resolve().parent.parent.parent.parent / "data" / "pantheon_plus_2022"
)
_PANTHEON_PLUS_SHOES_REQUIRED_FIELDS = (
    "m_b_corr",
    "is_calibrator",
    "cepheid_distance",
)


def _pantheon_plus_blocker(verified: dict[str, Any]) -> str:
    """Return a stable, user-facing reason the SH0ES likelihood is unavailable."""
    issues = verified.get("scientific_issues") or ()
    details = "; ".join(str(issue) for issue in issues)
    base = (
        "Pantheon+SH0ES full likelihood is unavailable: the official likelihood "
        "requires m_b_corr, IS_CALIBRATOR, and CEPH_DIST, applies "
        "(zHD > 0.01) | IS_CALIBRATOR, and uses CEPH_DIST as the calibrator "
        "theory distance. The current bundle cannot support a full SH0ES/H0 claim"
    )
    return f"{base} ({details})." if details else f"{base}."


@lru_cache(maxsize=None)
def load_verified_pantheon_plus_data(dataset_key: str = "pantheon_plus") -> dict[str, Any]:
    """Load the Pantheon+SH0ES 1701-SN bundle from the vendored, sha256-pinned
    ``data.npz`` and verify its digest against the registry, so the covariance the
    χ² inverts IS the checksummed array (object identity).  cov_fidelity is "full"
    on a digest match (the stat+sys matrix is a released FULL covariance),
    "unverified" on a present-but-mismatched/corrupt file (blocks publication).  A
    missing-but-pinned file degrades to "unverified" with no arrays (so
    _entry_verification can stamp it without an import-time crash).  The expensive
    1701x1701 inverse is NOT computed here — verify-only callers
    (_entry_verification, audit_executable_pins) need only the digest + fidelity;
    the fit path derives cov_inv lazily via _pantheon_plus_cov_inv().
    """
    pinned = _registry_product_sha256(dataset_key, "sn_full_data_npz")
    npz_path = _PANTHEON_PLUS_DATA_DIR / "data.npz"

    def _fallback(fidelity: str, issue: str) -> dict[str, Any]:
        return {
            "z_hd": None, "z_hel": None, "mu": None, "mu_err_diag": None, "cov": None,
            "m_b_corr": None, "is_calibrator": None, "cepheid_distance": None,
            "selection_mask": None, "n_selected": 0, "n_calibrators": 0,
            "sha256": None, "hash_verified": False, "cov_fidelity": fidelity,
            "shoes_calibration_ready": False, "likelihood_ready": False,
            "scientific_issues": (issue,),
        }

    if not npz_path.exists():
        return _fallback(
            "unverified" if pinned else "literature_typed",
            f"vendored bundle is missing at {npz_path}",
        )
    try:
        raw = npz_path.read_bytes()  # read once: the hash and np.load share these bytes
        digest = hashlib.sha256(raw).hexdigest()
        npz = np.load(io.BytesIO(raw))
        verified = digest == pinned
        z_hd = np.asarray(npz["z_hd"], dtype=np.float64)
        z_hel = np.asarray(npz["z_hel"], dtype=np.float64)
        mu = np.asarray(npz["mu"], dtype=np.float64)
        mu_err_diag = np.asarray(npz["mu_err_diag"], dtype=np.float64)
        cov = np.asarray(npz["cov"], dtype=np.float64)
        missing = [name for name in _PANTHEON_PLUS_SHOES_REQUIRED_FIELDS if name not in npz]
        m_b_corr = (
            np.asarray(npz["m_b_corr"], dtype=np.float64) if "m_b_corr" in npz else None
        )
        raw_is_calibrator = (
            np.asarray(npz["is_calibrator"])
            if "is_calibrator" in npz
            else None
        )
        is_calibrator = (
            np.asarray(raw_is_calibrator, dtype=bool)
            if raw_is_calibrator is not None
            else None
        )
        cepheid_distance = (
            np.asarray(npz["cepheid_distance"], dtype=np.float64)
            if "cepheid_distance" in npz
            else None
        )

        issues: list[str] = []
        if not verified:
            issues.append("sha256 does not match the registry pin")
        if missing:
            issues.append("bundle is missing official fields: " + ", ".join(missing))

        n = z_hd.size
        if z_hd.ndim != 1:
            issues.append(f"z_hd must be one-dimensional, got shape {z_hd.shape}")
        base_vectors = {
            "z_hel": z_hel,
            "mu": mu,
            "mu_err_diag": mu_err_diag,
        }
        for name, values in base_vectors.items():
            if values.shape != (n,):
                issues.append(f"{name} shape {values.shape} does not match z_hd {(n,)}")
        covariance_asymmetry = float("nan")
        if cov.shape != (n, n):
            issues.append(f"covariance shape {cov.shape} does not match {(n, n)}")
        elif not np.all(np.isfinite(cov)):
            issues.append("covariance contains non-finite values")
        else:
            covariance_asymmetry = float(np.max(np.abs(cov - cov.T)))
            # The released ASCII matrix is rounded independently above/below
            # the diagonal and differs by at most ~3e-8.  Accept only this tiny
            # release-level roundoff, then symmetrize explicitly before any
            # Cholesky/inversion. Larger asymmetry is a corrupt product.
            if covariance_asymmetry > 5e-8:
                issues.append(
                    f"covariance asymmetry {covariance_asymmetry:.3g} exceeds 5e-8"
                )
            else:
                cov = 0.5 * (cov + cov.T)
            if np.any(np.diag(cov) <= 0.0):
                issues.append("covariance has non-positive diagonal elements")
        if not np.all(np.isfinite(z_hd)) or not np.all(np.isfinite(z_hel)):
            issues.append("redshift vectors contain non-finite values")

        for name, values in (
            ("m_b_corr", m_b_corr),
            ("is_calibrator", is_calibrator),
            ("cepheid_distance", cepheid_distance),
        ):
            if values is not None and values.shape != (n,):
                issues.append(f"{name} shape {values.shape} does not match z_hd {(n,)}")
        if raw_is_calibrator is not None and raw_is_calibrator.shape == (n,):
            if not np.all(np.isin(raw_is_calibrator, (0, 1, False, True))):
                issues.append("is_calibrator contains values other than 0/1")

        selection_mask: np.ndarray | None = None
        if not missing and not issues:
            assert m_b_corr is not None
            assert is_calibrator is not None
            assert cepheid_distance is not None
            selection_mask = (z_hd > 0.01) | is_calibrator
            if not np.any(selection_mask):
                issues.append("official selection contains no supernovae")
            if not np.any(is_calibrator & selection_mask):
                issues.append("official selection contains no Cepheid calibrators")
            if not np.all(np.isfinite(m_b_corr[selection_mask])):
                issues.append("selected m_b_corr contains non-finite values")
            calibrator_distances = cepheid_distance[is_calibrator & selection_mask]
            if not np.all(np.isfinite(calibrator_distances)) or np.any(
                calibrator_distances <= 0.0
            ):
                issues.append("selected calibrators have invalid CEPH_DIST values")

        ready = bool(verified and selection_mask is not None and not issues)
        return {
            "z_hd": z_hd,
            "z_hel": z_hel,
            "mu": mu,
            "mu_err_diag": mu_err_diag,
            "cov": cov,
            "covariance_symmetrized": bool(covariance_asymmetry > 0.0),
            "covariance_max_asymmetry_raw": covariance_asymmetry,
            "m_b_corr": m_b_corr,
            "is_calibrator": is_calibrator,
            "cepheid_distance": cepheid_distance,
            "selection_mask": selection_mask,
            "n_selected": int(np.count_nonzero(selection_mask)) if selection_mask is not None else 0,
            "n_calibrators": (
                int(np.count_nonzero(is_calibrator & selection_mask))
                if is_calibrator is not None and selection_mask is not None
                else 0
            ),
            "sha256": digest,
            "hash_verified": bool(verified),
            "cov_fidelity": "full" if verified else "unverified",
            "shoes_calibration_ready": ready,
            "likelihood_ready": ready,
            "scientific_issues": tuple(issues),
        }
    except Exception as exc:  # malformed/truncated npz — degrade, never crash import
        logger.warning("Pantheon+ data product failed to load (%s); marking unverified", exc)
        return _fallback("unverified", f"bundle failed to load: {exc}")


@lru_cache(maxsize=None)
def _pantheon_plus_cov_inv() -> np.ndarray:
    """Inverse of the officially selected, verified SN covariance.

    The released covariance is 1701x1701, but the Pantheon+SH0ES likelihood uses
    only rows satisfying ``(zHD > 0.01) | IS_CALIBRATOR``.  Subsetting before
    inversion follows the release likelihood exactly and prevents excluded
    low-redshift, non-calibrator rows from entering the fit.
    """
    verified = load_verified_pantheon_plus_data("pantheon_plus")
    if not verified.get("likelihood_ready"):
        raise ValueError(_pantheon_plus_blocker(verified))
    selection = verified["selection_mask"]
    covariance = verified["cov"][np.ix_(selection, selection)]
    return np.linalg.inv(covariance)


# ── DES-SN5YR full distance-modulus likelihood (2026-06-05) ─────────────────
# 1829-SN Vincenzi+2024 Legacy Hubble diagram + full stat+sys covariance,
# vendored as a sha256-pinned npz (built by scripts/fetch_des_sn5yr.py from the
# github tag-1.3 release: C_total = STAT+SYS systematic cov + diag(MUERR_FINAL²)).
# Mirrors the Pantheon+ full-cov machinery, but the χ² analytically marginalizes
# the SN absolute-magnitude offset (no M_B nuisance, and H0 drops out too), so it
# constrains Ωm (+ the w0/wa DE shape) only.
_DES_SN5YR_DATA_DIR = (
    pathlib.Path(__file__).resolve().parent.parent.parent.parent / "data" / "des_sn5yr"
)


@lru_cache(maxsize=None)
def load_verified_des_sn5yr_data(dataset_key: str = "des_sn5yr") -> dict[str, Any]:
    """Load the DES-SN5YR 1829-SN bundle from the vendored, sha256-pinned data.npz
    and verify its digest against the registry (so the covariance the χ² inverts IS
    the checksummed array). cov_fidelity is 'full' on a digest match, 'unverified'
    on a present-but-mismatched/corrupt or missing-but-pinned file (blocks
    publication). The 1829×1829 inverse is derived lazily on the fit path."""
    pinned = _registry_product_sha256(dataset_key, "sn_full_data_npz")
    npz_path = _DES_SN5YR_DATA_DIR / "data.npz"

    def _fallback(fidelity: str) -> dict[str, Any]:
        return {
            "z_hd": None, "z_hel": None, "mu": None, "mu_err_diag": None, "cov": None,
            "sha256": None, "hash_verified": False, "cov_fidelity": fidelity,
        }

    if not npz_path.exists():
        return _fallback("unverified" if pinned else "literature_typed")
    try:
        raw = npz_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        npz = np.load(io.BytesIO(raw))
        verified = digest == pinned
        return {
            "z_hd": np.asarray(npz["z_hd"], dtype=np.float64),
            "z_hel": np.asarray(npz["z_hel"], dtype=np.float64),
            "mu": np.asarray(npz["mu"], dtype=np.float64),
            "mu_err_diag": np.asarray(npz["mu_err_diag"], dtype=np.float64),
            "cov": np.asarray(npz["cov"], dtype=np.float64),
            "sha256": digest,
            "hash_verified": bool(verified),
            "cov_fidelity": "full" if verified else "unverified",
        }
    except Exception as exc:  # malformed/truncated npz — degrade, never crash import
        logger.warning("DES-SN5YR data product failed to load (%s); marking unverified", exc)
        return _fallback("unverified")


@lru_cache(maxsize=None)
def _des_sn5yr_cov_inv() -> np.ndarray:
    """Inverse of the verified 1829×1829 DES-SN5YR covariance — computed once, only
    on the fit path."""
    return np.linalg.inv(load_verified_des_sn5yr_data("des_sn5yr")["cov"])


@lru_cache(maxsize=None)
def _load_des_sn5yr_data() -> dict[str, np.ndarray]:
    """Arrays the DES-SN5YR χ² fits, sourced from the sha256-verified loader (cov IS
    the checksummed object) with cov_inv derived lazily. Refuses unverified data."""
    verified = load_verified_des_sn5yr_data("des_sn5yr")
    if verified["cov"] is None:
        raise FileNotFoundError(
            f"DES-SN5YR data file missing: {_DES_SN5YR_DATA_DIR / 'data.npz'}. "
            "Run `python scripts/fetch_des_sn5yr.py` to build it (~26 MB)."
        )
    if verified.get("cov_fidelity") == "unverified":
        raise ValueError(
            "DES-SN5YR covariance failed sha256 verification (digest mismatch); "
            "refusing to compute chi2 from unverified data — re-fetch the release."
        )
    return {
        "z_hd": verified["z_hd"], "z_hel": verified["z_hel"], "mu": verified["mu"],
        "cov_inv": _des_sn5yr_cov_inv(),
    }


# ── Union3 / UNITY1.5 full binned-distance likelihood (2026-06-12) ──────────
# The same 22-bin lcparam_full.txt + mag_covmat.txt cobaya's sn.union3 reads
# (CobayaSampler/sn_data), vendored + sha256-pinned. cobaya marginalizes the
# constant magnitude offset by projecting it out of invcov
# (_marginalize_abs_mag); our chi2 = δᵀC⁻¹δ − (ΣC⁻¹δ)²/(ΣC⁻¹) is the same
# projection applied per-sample — algebraically identical, locked by test.
_UNION3_DATA_DIR = (
    pathlib.Path(__file__).resolve().parent.parent.parent.parent / "data" / "union3"
)


@lru_cache(maxsize=1)
def _load_union3_raw() -> dict[str, Any]:
    """Load + sha256-verify the vendored Union3 files; raises ValueError on ANY
    failure (missing/unreadable/digest mismatch/malformed). lru_cache never
    caches exceptions, so one transient failure cannot poison the process
    until restart (the MGS-iteration lesson — a cached unverified record
    would)."""
    vec_path = _UNION3_DATA_DIR / "lcparam_full.txt"
    cov_path = _UNION3_DATA_DIR / "mag_covmat.txt"
    if not (vec_path.exists() and cov_path.exists()):
        raise ValueError(
            f"Union3 data unverified: vendored files missing under {_UNION3_DATA_DIR} "
            "(lcparam_full.txt + mag_covmat.txt, from CobayaSampler/sn_data)."
        )
    try:
        vec_raw = vec_path.read_bytes()
        cov_raw = cov_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Union3 data unverified: vendored file unreadable ({exc}).") from exc
    vec_ok = hashlib.sha256(vec_raw).hexdigest() == _registry_product_sha256(
        "union3", "measurement_vector"
    )
    cov_digest = hashlib.sha256(cov_raw).hexdigest()
    cov_ok = cov_digest == _registry_product_sha256("union3", "covariance")
    if not (vec_ok and cov_ok):
        raise ValueError(
            "Union3 data unverified: vendored file bytes do not match the registry "
            "sha256 pins; refusing to compute chi2 from tampered or stale data."
        )
    # Parse the SAME bytes the digests certified.
    z_cmb_list: list[float] = []
    z_hel_list: list[float] = []
    mb_list: list[float] = []
    for line in vec_raw.decode("utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        z_cmb_list.append(float(parts[1]))
        z_hel_list.append(float(parts[2]))
        mb_list.append(float(parts[4]))
    cov_tokens = cov_raw.decode("utf-8").split()
    n = int(cov_tokens[0])
    cov = np.asarray(cov_tokens[1:], dtype=float).reshape(n, n)
    if n != len(mb_list):
        raise ValueError(
            f"Union3 data unverified: vector/cov shape mismatch "
            f"({len(mb_list)} bins vs cov {n}x{n})."
        )
    return {
        "z_cmb": np.asarray(z_cmb_list, dtype=float),
        "z_hel": np.asarray(z_hel_list, dtype=float),
        "mb": np.asarray(mb_list, dtype=float),
        "cov": cov,
        "sha256": cov_digest,
    }


def load_verified_union3_data(dataset_key: str = "union3") -> dict[str, Any]:
    """Verification record for audit/provenance — NEVER raises (audit and
    import paths need a record, not an exception). Built fresh per call from
    the cached raw load: success → 'full' + digest; any failure → 'unverified'
    (blocks publication), recomputed next call so a transient failure heals
    without a process restart. Never a silent fallback to the compressed
    Gaussian."""
    try:
        raw = _load_union3_raw()
    except ValueError as exc:
        logger.warning("Union3 data product failed verification: %s", exc)
        return {
            "z_cmb": None, "z_hel": None, "mb": None, "cov": None,
            "sha256": None, "hash_verified": False, "cov_fidelity": "unverified",
        }
    return {**raw, "hash_verified": True, "cov_fidelity": "full"}


@lru_cache(maxsize=1)
def _union3_cov_inv() -> np.ndarray:
    """Inverse of the verified 22x22 Union3 covariance — computed on the fit path."""
    return np.linalg.inv(_load_union3_raw()["cov"])


# ── Pantheon (2018) full 1048-SN likelihood (2026-06-13, env-gated) ─────────
# The same lcparam_full_long_zhel.txt + sys_full_long.txt cobaya's sn.pantheon
# reads (CobayaSampler/sn_data), vendored + sha256-pinned. Convention
# (use_abs_mag=False, pecz=0, intrinsicdisp=0): C_total = C_sys + diag(dmb²),
# absolute magnitude analytically marginalized.
_PANTHEON18_DATA_DIR = (
    pathlib.Path(__file__).resolve().parent.parent.parent.parent
    / "data"
    / "cosmology"
    / "pantheon18"
)


@lru_cache(maxsize=1)
def _load_pantheon18_raw() -> dict[str, Any]:
    """Load + sha256-verify the vendored Pantheon 2018 files; raises ValueError
    on ANY failure (missing/unreadable/digest mismatch/malformed). lru_cache
    never caches exceptions, so one transient failure cannot poison the
    process until restart (the union3 pattern)."""
    vec_path = _PANTHEON18_DATA_DIR / "lcparam_full_long_zhel.txt"
    cov_path = _PANTHEON18_DATA_DIR / "sys_full_long.txt"
    if not (vec_path.exists() and cov_path.exists()):
        raise ValueError(
            f"Pantheon18 data unverified: vendored files missing under "
            f"{_PANTHEON18_DATA_DIR} (lcparam_full_long_zhel.txt + sys_full_long.txt, "
            "from CobayaSampler/sn_data). Run scripts/fetch_pantheon18.py."
        )
    vec_raw = vec_path.read_bytes()
    cov_raw = cov_path.read_bytes()
    vec_ok = (
        hashlib.sha256(vec_raw).hexdigest()
        == _registry_product_sha256("pantheon18", "measurement_vector")
    )
    cov_digest = hashlib.sha256(cov_raw).hexdigest()
    cov_ok = cov_digest == _registry_product_sha256("pantheon18", "covariance")
    if not (vec_ok and cov_ok):
        raise ValueError(
            "Pantheon18 data unverified: vendored file bytes do not match the "
            "registry sha256 pins; refusing to compute chi2 from tampered or stale data."
        )
    z_cmb_list: list[float] = []
    z_hel_list: list[float] = []
    mb_list: list[float] = []
    dmb_list: list[float] = []
    for line in vec_raw.decode("utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        z_cmb_list.append(float(parts[1]))
        z_hel_list.append(float(parts[2]))
        mb_list.append(float(parts[4]))
        dmb_list.append(float(parts[5]))
    cov_tokens = cov_raw.decode("utf-8").split()
    n = int(float(cov_tokens[0]))
    cov_sys = np.asarray(cov_tokens[1:], dtype=float).reshape(n, n)
    if n != len(mb_list):
        raise ValueError(
            f"Pantheon18 data unverified: vector/cov shape mismatch "
            f"({len(mb_list)} SNe vs cov {n}x{n})."
        )
    # cobaya sn.pantheon convention (pecz=0, intrinsicdisp=0):
    # C_total = C_sys + diag(dmb²).
    cov = cov_sys + np.diag(np.asarray(dmb_list, dtype=float) ** 2)
    return {
        "z_cmb": np.asarray(z_cmb_list, dtype=float),
        "z_hel": np.asarray(z_hel_list, dtype=float),
        "mb": np.asarray(mb_list, dtype=float),
        "cov": cov,
        "sha256": cov_digest,
    }


def load_verified_pantheon18_data(dataset_key: str = "pantheon18") -> dict[str, Any]:
    """Verification record for audit/provenance — NEVER raises (audit and
    import paths need a record, not an exception). Success → 'full' + digest;
    any failure → 'unverified' (blocks publication), recomputed next call so
    a transient failure heals without a restart."""
    try:
        raw = _load_pantheon18_raw()
    except ValueError as exc:
        logger.warning("Pantheon18 data product failed verification: %s", exc)
        return {
            "z_cmb": None, "z_hel": None, "mb": None, "cov": None,
            "sha256": None, "hash_verified": False, "cov_fidelity": "unverified",
        }
    return {**raw, "hash_verified": True, "cov_fidelity": "full"}


@lru_cache(maxsize=1)
def _pantheon18_cov_inv() -> np.ndarray:
    """Inverse of the verified 1048x1048 Pantheon18 covariance (fit path only)."""
    return np.linalg.inv(_load_pantheon18_raw()["cov"])


def _load_pantheon18_data() -> dict[str, np.ndarray]:
    """Arrays the Pantheon18 χ² fits — raises ValueError ('unverified') on
    missing/tampered data via the raw loader."""
    raw = _load_pantheon18_raw()
    return {
        "z_hd": raw["z_cmb"], "z_hel": raw["z_hel"], "mu": raw["mb"],
        "cov_inv": _pantheon18_cov_inv(),
    }


def _pantheon18_chi2_samples(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """χ² from the full Pantheon 2018 1048-SN apparent-magnitude vector
    (offset-marginalized — cobaya sn.pantheon's use_abs_mag=False convention,
    whose analytic M-marginalization lives in alpha_beta_logp's amarg terms;
    mb = μ + M, and the constant M is projected out, so mb works directly as
    an offset-normalized μ_obs)."""
    data = _load_pantheon18_data()
    return _offset_marginalized_sn_chi2(
        samples, parameter_order,
        z_hd=data["z_hd"], z_hel=data["z_hel"],
        mu_obs=data["mu"], cov_inv=data["cov_inv"],
    )


def _load_union3_data() -> dict[str, np.ndarray]:
    """Arrays the Union3 χ² fits — raises ValueError ('unverified') on
    missing/tampered data via the raw loader."""
    raw = _load_union3_raw()
    return {
        "z_hd": raw["z_cmb"], "z_hel": raw["z_hel"], "mu": raw["mb"],
        "cov_inv": _union3_cov_inv(),
    }


@lru_cache(maxsize=None)
def _load_pantheon_plus_data() -> dict[str, np.ndarray]:
    """Return the officially selected Pantheon+SH0ES likelihood arrays.

    Refuses old ``MU_SH0ES``-only bundles even when their bytes and covariance
    are sha256-verified: those rows cannot reproduce the official calibrator
    likelihood or identify H0.  The selected covariance is cached with the
    inverse so repeated sampler calls do not copy a 1657x1657 matrix.
    """
    verified = load_verified_pantheon_plus_data("pantheon_plus")
    issues = tuple(str(issue) for issue in verified.get("scientific_issues") or ())
    if verified.get("cov") is None and any(
        "vendored bundle is missing at" in issue for issue in issues
    ):
        raise FileNotFoundError(
            f"Pantheon+SH0ES data file missing: {_PANTHEON_PLUS_DATA_DIR / 'data.npz'}. "
            "Run `python scripts/fetch_pantheon_plus.py` to download "
            "the 2022 release (~20 MB)."
        )
    if not verified.get("likelihood_ready"):
        raise ValueError(_pantheon_plus_blocker(verified))
    selection = verified["selection_mask"]
    selected_covariance = verified["cov"][np.ix_(selection, selection)]
    return {
        "z_hd": verified["z_hd"][selection],
        "z_hel": verified["z_hel"][selection],
        # Keep the selected legacy MU_SH0ES vector only for compatibility with
        # row-count/provenance code. It is never the observed vector in χ².
        "mu": verified["mu"][selection],
        "mu_err_diag": verified["mu_err_diag"][selection],
        "m_b_corr": verified["m_b_corr"][selection],
        "is_calibrator": verified["is_calibrator"][selection],
        "cepheid_distance": verified["cepheid_distance"][selection],
        "cov": selected_covariance,
        "cov_inv": _pantheon_plus_cov_inv(),
    }


# Kept as a compatibility export for callers that display the historical SH0ES
# reference value.  The official likelihood below does not subtract a fixed
# M_B reference: it fits corrected apparent magnitudes as m_b_corr = theory_mu
# + M_B and uses CEPH_DIST + M_B for the calibrator rows.
PANTHEON_PLUS_M_B_REF = -19.253


def _pantheon_plus_chi2_samples(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """Official-form Pantheon+SH0ES χ² under flat w0waCDM.

    The released likelihood first selects ``(zHD > 0.01) | IS_CALIBRATOR`` and
    fits ``m_b_corr``.  Hubble-flow rows use
    ``5 log10((1+zHEL) D_M(zHD)) + 25 + M_B``; calibrator rows instead use
    ``CEPH_DIST + M_B``.  The latter anchors M_B and thereby makes H0
    identifiable.  Applying cosmological distances to calibrators, or fitting
    every released row, is not the Pantheon+SH0ES likelihood.

    χ² = (m_b_corr - m_model)ᵀ · C_selected⁻¹ · (m_b_corr - m_model)

    parameter_order must contain "H0", "omegam", "M_B".  Optionally also
    "w"/"w0"/"wa": when present, those columns flow through the DE-aware
    distance integrand so the joint posterior on the SN side is consistent
    with the cosmological model (review fix bug_001: previously SN χ² was
    hard-coded to ΛCDM regardless of model_key, silently biasing w/wa
    posteriors toward -1/0 in DESI+SN joint fits).
    """
    data = _load_pantheon_plus_data()
    z_hd = data["z_hd"]    # cosmological redshift — drives the comoving-distance integral
    z_hel = data["z_hel"]  # heliocentric redshift — the (1+z) luminosity-distance factor
    m_obs = data["m_b_corr"]
    is_calibrator = data["is_calibrator"].astype(bool, copy=False)
    cepheid_distance = data["cepheid_distance"]
    cov_inv = data["cov_inv"]
    n_samples = samples.shape[0]
    h0 = samples[:, parameter_order.index("H0")]
    omegam = samples[:, parameter_order.index("omegam")]
    m_b = samples[:, parameter_order.index("M_B")]
    # Read dark-energy params if present; default to ΛCDM (w0=-1, wa=0).
    if "w0" in parameter_order:
        w0 = samples[:, parameter_order.index("w0")]
    elif "w" in parameter_order:
        w0 = samples[:, parameter_order.index("w")]
    else:
        w0 = np.full(n_samples, -1.0, dtype=float)
    if "wa" in parameter_order:
        wa = samples[:, parameter_order.index("wa")]
    else:
        wa = np.zeros(n_samples, dtype=float)
    # Vectorized D_M over (z_hd, sample) under flat w0waCDM (CPL); the
    # luminosity-distance (1+z) factor uses z_hel per the Pantheon+ convention
    # D_L = (1 + z_hel) · D_M(z_hd).
    dm_grid = _flat_de_dm_grid_vectorized(z_hd, h0, omegam, w0, wa)  # (n_sn, n_samples)
    dl_grid = (1.0 + z_hel[:, None]) * dm_grid               # (n_sn, n_samples), Mpc
    theory_mu = 5.0 * np.log10(dl_grid) + 25.0
    theory_mu[is_calibrator, :] = cepheid_distance[is_calibrator, None]
    m_model = theory_mu + m_b[None, :]
    residual = m_obs[:, None] - m_model                      # (n_sn, n_samples)
    return np.einsum("in,ij,jn->n", residual, cov_inv, residual)


def _offset_marginalized_sn_chi2(
    samples: np.ndarray,
    parameter_order: list[str],
    *,
    z_hd: np.ndarray,
    z_hel: np.ndarray,
    mu_obs: np.ndarray,
    cov_inv: np.ndarray,
) -> np.ndarray:
    """Shared χ² core for binned/full SN distance-modulus likelihoods with the
    absolute-magnitude offset M analytically marginalized:

        δ = μ_model − μ_obs ,  χ² = δᵀC⁻¹δ − (Σ C⁻¹δ)² / (Σ C⁻¹)

    Algebraically identical to cobaya's _marginalize_abs_mag projection of the
    inverse covariance. The marginalized χ² is invariant to a constant shift of
    μ_model, so H0 (and M_B) drop out entirely — these likelihoods constrain
    Ωm (+ the w0/wa DE shape) only. parameter_order must contain "omegam";
    optionally "w"/"w0"/"wa". μ_obs may carry an arbitrary constant
    normalization (Union3's binned mb does)."""
    n_samples = samples.shape[0]
    omegam = samples[:, parameter_order.index("omegam")]
    h0_fid = np.full(n_samples, 70.0, dtype=float)  # absolute H0 is marginalized away
    if "w0" in parameter_order:
        w0 = samples[:, parameter_order.index("w0")]
    elif "w" in parameter_order:
        w0 = samples[:, parameter_order.index("w")]
    else:
        w0 = np.full(n_samples, -1.0, dtype=float)
    wa = samples[:, parameter_order.index("wa")] if "wa" in parameter_order else np.zeros(n_samples)
    dm_grid = _flat_de_dm_grid_vectorized(z_hd, h0_fid, omegam, w0, wa)  # (n_sn, n_samples)
    dl_grid = (1.0 + z_hel[:, None]) * dm_grid
    mu_model = 5.0 * np.log10(dl_grid) + 25.0
    delta = mu_model - mu_obs[:, None]                      # (n_sn, n_samples)
    cinv_delta = cov_inv @ delta                            # (n_sn, n_samples)
    chit2 = np.einsum("in,in->n", delta, cinv_delta)
    b = cinv_delta.sum(axis=0)                              # Σ_i (C⁻¹δ)_i per sample
    c_norm = float(cov_inv.sum())                           # Σ_ij C⁻¹_ij
    return chit2 - b ** 2 / c_norm


def _des_sn5yr_chi2_samples(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """χ² from the DES-SN5YR 1829 SNe Ia (offset-marginalized, per the official
    DES-SN5YR likelihood)."""
    data = _load_des_sn5yr_data()
    return _offset_marginalized_sn_chi2(
        samples, parameter_order,
        z_hd=data["z_hd"], z_hel=data["z_hel"],
        mu_obs=data["mu"], cov_inv=data["cov_inv"],
    )


def _union3_chi2_samples(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """χ² from the Union3/UNITY1.5 22-bin binned distance moduli
    (offset-marginalized — cobaya sn.union3's use_abs_mag=False convention)."""
    data = _load_union3_data()
    return _offset_marginalized_sn_chi2(
        samples, parameter_order,
        z_hd=data["z_hd"], z_hel=data["z_hel"],
        mu_obs=data["mu"], cov_inv=data["cov_inv"],
    )


def _offset_sn_chi2_samples(
    samples: np.ndarray, parameter_order: list[str], key: str
) -> np.ndarray:
    """Per-key dispatch for the offset-marginalized SN family — else-raise so a
    future key cannot silently run on another dataset's data."""
    if key == "des_sn5yr":
        return _des_sn5yr_chi2_samples(samples, parameter_order)
    if key == "union3":
        return _union3_chi2_samples(samples, parameter_order)
    if key == "pantheon18":
        return _pantheon18_chi2_samples(samples, parameter_order)
    raise ValueError(f"executable offset-marginalized SN entry {key!r} has no chi2 dispatch")


def _offset_sn_n_points(key: str) -> int:
    """Number of fitted data points for an offset-marginalized SN entry."""
    if key == "des_sn5yr":
        return int(len(_load_des_sn5yr_data()["mu"]))
    if key == "union3":
        return int(len(_load_union3_data()["mu"]))
    if key == "pantheon18":
        return int(len(_load_pantheon18_data()["mu"]))
    raise ValueError(f"executable offset-marginalized SN entry {key!r} has no data loader")

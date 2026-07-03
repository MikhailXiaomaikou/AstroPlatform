"""BAO family: vendored data loaders, predictions and chi^2 (DESI/SDSS/eBOSS).

Split verbatim out of the pre-2026-07-03 single-file
app/services/cosmology_likelihoods.py (7,757 lines). Import the package
``app.services.cosmology_likelihoods`` — it re-exports every pre-split name
and keeps the original one-namespace monkeypatch semantics.
"""

from __future__ import annotations

import hashlib
import io
from functools import lru_cache
from typing import Any

import numpy as np

from app.services.cosmology_likelihoods.core import (
    C_LIGHT_KM_S,
    CosmologyDatasetEntry,
    logger,
)

from app.services.cosmology_likelihoods.data_io import (
    _VENDORED_COSMO_DATA_DIR,
    _registry_product_sha256,
)

from app.services.cosmology_likelihoods.distances import (
    _flat_de_distances_at_z,
)

from app.services.cosmology_likelihoods.growth import (
    _growth_factor_ratio,
    _growth_rate_f,
)



DESI_DR1_BAO_MEAN_VECTOR: tuple[tuple[float, float, str], ...] = (
    (0.295, 7.92512927, "DV_over_rs"),
    (0.510, 13.62003080, "DM_over_rs"),
    (0.510, 20.98334647, "DH_over_rs"),
    (0.706, 16.84645313, "DM_over_rs"),
    (0.706, 20.07872919, "DH_over_rs"),
    (0.930, 21.70841761, "DM_over_rs"),
    (0.930, 17.87612922, "DH_over_rs"),
    (1.317, 27.78720817, "DM_over_rs"),
    (1.317, 13.82372285, "DH_over_rs"),
    (1.491, 26.07217182, "DV_over_rs"),
    (2.330, 39.70838281, "DM_over_rs"),
    (2.330, 8.52256583, "DH_over_rs"),
)
DESI_DR1_BAO_COVARIANCE: tuple[tuple[float, ...], ...] = (
    (2.27230845e-02, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (0.0, 6.34662240e-02, -6.85337250e-02, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (0.0, -6.85337250e-02, 3.72968756e-01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 1.01975713e-01, -7.99403059e-02, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, -7.99403059e-02, 3.54449156e-01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 0.0, 0.0, 7.95675235e-02, -3.80110101e-02, 0.0, 0.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 0.0, 0.0, -3.80110101e-02, 1.19935683e-01, 0.0, 0.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 4.76569857e-01, -1.29405759e-01, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -1.29405759e-01, 1.78270498e-01, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 4.47134991e-01, 0.0, 0.0),
    (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 8.89752928e-01, -7.69477120e-02),
    (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -7.69477120e-02, 2.91860447e-02),
)

# ── SDSS-MGS + 6dFGS low-z BAO executable likelihood (2026-05-29, Tier 2B) ──
# Two isotropic D_V/r_d anchors below the DESI redshift floor, compiled by
# Aubourg et al. 2015 (arXiv:1411.1074) Table II and reused by BOSS/eBOSS
# (Alam+2021 Table III): 6dFGS z=0.106 → D_V/r_d = 3.047 ± 0.137 (Beutler+2011,
# arXiv:1106.3366) and SDSS-MGS z=0.15 → D_V/r_d = 4.47 ± 0.17 (Ross+2015,
# arXiv:1409.3242). NOTE: the adversarial cross-check rejected a naive inversion
# of 6dFGS's published r_d/D_V=0.336 (→2.976); the compilation value 3.047 is the
# one the BAO distance-ratio convention here consumes. Two independent surveys →
# diagonal covariance.
SDSS_6DF_BAO_MEAN_VECTOR: tuple[tuple[float, float, str], ...] = (
    (0.106, 3.047, "DV_over_rd"),
    (0.150, 4.470, "DV_over_rd"),
)
SDSS_6DF_BAO_COVARIANCE: tuple[tuple[float, ...], ...] = (
    (0.137 ** 2, 0.0),
    (0.0, 0.17 ** 2),
)

# Legacy hand-typed BAO (mean, covariance) values, kept only as the fallback for
# datasets without a vendored sha256-pinned file. The DESI hand-typed constants
# are byte-identical to the vendored file (verified), so binding shifts no number.
_HARDCODED_BAO: dict[str, tuple[Any, Any]] = {
    "desi_dr1_bao": (DESI_DR1_BAO_MEAN_VECTOR, DESI_DR1_BAO_COVARIANCE),
    "sdss_6df_bao": (SDSS_6DF_BAO_MEAN_VECTOR, SDSS_6DF_BAO_COVARIANCE),
}


# ── SDSS MGS full non-Gaussian alpha likelihood (2026-06-12) ────────────────
# cobaya's bao.sdss_dr7_mgs convention: the released product is a 399-point
# chi2(alpha) table over alpha = (D_V(0.15)/r_d) / MGS_ALPHA_RESCALE, where
# MGS_ALPHA_RESCALE = D_V_fid/r_s_fid = 638.9518/148.69 (Ross+2015 fiducial).
# cobaya splines -chi2/2 (UnivariateSpline, s=0) and returns logp=-inf outside
# the tabulated range; we use the SAME spline construction (numerical parity)
# and a large finite chi2 outside bounds so importance weights vanish.
MGS_ALPHA_RESCALE = 4.29720761315
MGS_ALPHA_BOUNDS = (0.8005, 1.1985)
MGS_OUT_OF_BOUNDS_CHI2 = 1.0e10


@lru_cache(maxsize=1)
def load_verified_mgs_prob_table() -> dict[str, Any]:
    """Load + sha256-verify the vendored MGS chi2(alpha) table.

    Returns {alpha, chi2, sha256, hash_verified=True} or raises ValueError
    (message always contains 'unverified') on missing/unreadable/tampered/
    malformed — the chi2 path REFUSES to run on an unverified table, never a
    silent fallback to the retired Gaussian approximation.  Raising instead of
    returning an unverified record matters for the cache: lru_cache never
    caches exceptions, so one transient I/O failure cannot poison the process
    until restart the way a cached failure record would.
    """
    pinned = _registry_product_sha256("sdss_6df_bao", "mgs_alpha_chi2_table")
    path = _VENDORED_COSMO_DATA_DIR / "sdss_6df_bao" / "sdss_MGS_prob.txt"
    if not path.exists():
        raise ValueError(
            f"SDSS MGS chi2(alpha) table is unverified: vendored file missing at {path}."
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(
            f"SDSS MGS chi2(alpha) table is unverified: vendored file unreadable ({exc})."
        ) from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != pinned:
        raise ValueError(
            "SDSS MGS chi2(alpha) table is unverified: vendored file bytes do not "
            "match the registry sha256 pin; refusing to compute chi2 from tampered "
            "or stale data."
        )
    # Parse the SAME bytes the digest certified (no second read between hash
    # and parse).
    chi2 = np.loadtxt(io.StringIO(raw.decode("utf-8")), comments="#")
    if chi2.ndim != 1 or chi2.size < 10:
        raise ValueError(
            f"SDSS MGS chi2(alpha) table is unverified: expected a 1-column chi2 "
            f"table, got shape {chi2.shape}."
        )
    alpha = np.linspace(MGS_ALPHA_BOUNDS[0], MGS_ALPHA_BOUNDS[1], chi2.size)
    return {
        "alpha": alpha,
        "chi2": np.asarray(chi2, dtype=float),
        "sha256": digest,
        "hash_verified": True,
    }


@lru_cache(maxsize=1)
def _mgs_chi2_spline():
    """Interpolating spline of -chi2/2 over alpha — cobaya's exact construction.

    Raises ValueError (via the loader) on an unverified table; the exception is
    not cached, so a later call retries the load.  NOTE: like cobaya, the cubic
    interpolant can overshoot slightly below the table minimum (chi2 marginally
    negative near the best fit) — accepted, because numerical parity with
    cobaya's construction is the spec here.
    """
    from scipy.interpolate import UnivariateSpline

    table = load_verified_mgs_prob_table()
    return UnivariateSpline(table["alpha"], -table["chi2"] / 2.0, s=0, ext=2)


def _sdss_6df_mgs_chi2_samples(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """6dFGS Gaussian point + SDSS MGS full chi2(alpha) table.

    The 2-row mean vector still supplies the (z, quantity) prediction
    scaffold and the 6dFGS Gaussian; the MGS row's Gaussian sigma is retired
    in favour of the released non-Gaussian table.
    """
    spline = _mgs_chi2_spline()  # raises ValueError ('unverified') on a bad table
    mean_vector, cov = _BAO_DATA["sdss_6df_bao"]
    # The table lookup below is positional (row 1 = MGS); refuse loudly if the
    # mean vector's row order ever changes, instead of silently feeding the
    # 6dFGS prediction into the MGS table.
    if not (
        abs(mean_vector[0][0] - 0.106) < 1e-9 and abs(mean_vector[1][0] - 0.150) < 1e-9
    ):
        raise ValueError(
            "sdss_6df_bao mean-vector row order changed (expected row 0 = 6dFGS "
            f"z=0.106, row 1 = MGS z=0.15, got z={mean_vector[0][0]}, "
            f"{mean_vector[1][0]}); MGS chi2(alpha) table mapping is positional."
        )
    predictions = _bao_predictions(samples, parameter_order, mean_vector)
    # 6dFGS z=0.106 — Gaussian as before.
    chi2 = ((predictions[:, 0] - mean_vector[0][1]) ** 2) / float(cov[0][0])
    # SDSS MGS z=0.15 — alpha lookup in the released table.
    alpha = predictions[:, 1] / MGS_ALPHA_RESCALE
    mgs_chi2 = np.full(alpha.shape, MGS_OUT_OF_BOUNDS_CHI2, dtype=float)
    in_bounds = (alpha >= MGS_ALPHA_BOUNDS[0]) & (alpha <= MGS_ALPHA_BOUNDS[1])
    if np.any(in_bounds):
        mgs_chi2[in_bounds] = -2.0 * spline(alpha[in_bounds])
    return chi2 + mgs_chi2


@lru_cache(maxsize=None)
def load_verified_bao_data(dataset_key: str) -> dict[str, Any]:
    """Load a BAO (mean, covariance) from the vendored, sha256-pinned data-product
    files and verify the digests against the registry, so the fitted covariance IS
    the checksum-verified array (``cov_fidelity='full'``).  Falls back to the
    legacy hand-typed values with ``cov_fidelity='literature_typed'`` — an honest
    downgrade, never a silent wrong-shape covariance — only when no vendored file
    is present (e.g. the 6dFGS+MGS low-z compilation, which has no released file).
    """
    mean_path = _VENDORED_COSMO_DATA_DIR / dataset_key / "mean.txt"
    cov_path = _VENDORED_COSMO_DATA_DIR / dataset_key / "cov.txt"
    pinned = _registry_product_sha256(dataset_key, "covariance")

    def _fallback(fidelity: str) -> dict[str, Any]:
        if dataset_key not in _HARDCODED_BAO:
            # A released full-file BAO (e.g. DESI DR2) has no honest hand-typed
            # substitute; a missing/corrupt file is 'unverified', never faked.
            return {
                "mean_vector": None, "covariance": None, "sha256": None,
                "hash_verified": False, "cov_fidelity": "unverified",
            }
        mean_vector, cov = _HARDCODED_BAO[dataset_key]
        return {
            "mean_vector": tuple(mean_vector),
            "covariance": np.asarray(cov, dtype=float),
            "sha256": None, "hash_verified": False, "cov_fidelity": fidelity,
        }

    if dataset_key == "sdss_6df_bao":
        # Mixed probe (2026-06-12): the 6dFGS half is a hand-typed literature
        # Gaussian, but the MGS half reads the sha256-pinned released
        # chi2(alpha) table — so the stamp must carry the table's verification.
        # Verified -> 'literature_typed' (the weakest half — the hand-typed
        # 6dFGS Gaussian — sets the fidelity grade) + the table digest;
        # tampered/missing -> 'unverified' (audit-dirty, publication-blocked).
        base = _fallback("literature_typed")
        try:
            table = load_verified_mgs_prob_table()
        except ValueError as exc:
            logger.warning("sdss_6df_bao MGS table stamp: %s", exc)
            return {**base, "cov_fidelity": "unverified"}
        return {**base, "sha256": table["sha256"], "hash_verified": True}

    if not (mean_path.exists() and cov_path.exists()):
        # expected pinned product missing -> unverified (blocks publication);
        # no released product -> honest literature_typed.
        return _fallback("unverified" if pinned else "literature_typed")
    try:
        mean_digest = hashlib.sha256(mean_path.read_bytes()).hexdigest()
        cov_digest = hashlib.sha256(cov_path.read_bytes()).hexdigest()
        mean_ok = mean_digest == _registry_product_sha256(dataset_key, "measurement_vector")
        cov_ok = cov_digest == pinned
        mean_vector_list: list[tuple[float, float, str]] = []
        for line in mean_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            z_str, value_str, quantity = stripped.split()
            mean_vector_list.append((float(z_str), float(value_str), quantity))
        covariance = np.loadtxt(cov_path)
        n = len(mean_vector_list)
        if n == 0 or covariance.shape != (n, n):
            raise ValueError(f"mean/cov shape mismatch: {n} points vs cov {covariance.shape}")
        return {
            "mean_vector": tuple(mean_vector_list),
            "covariance": covariance,
            "sha256": cov_digest,
            "mean_sha256": mean_digest,
            "hash_verified": bool(mean_ok and cov_ok),
            "cov_fidelity": "full" if (mean_ok and cov_ok) else "unverified",
        }
    except Exception as exc:  # malformed/truncated file — degrade, never crash import
        logger.warning("BAO data product %s failed to load (%s); marking unverified", dataset_key, exc)
        return _fallback("unverified")


# Released full-file BAO likelihoods with NO hand-typed _HARDCODED_BAO fallback —
# the fitted data IS the sha256-pinned vendored file (load_verified_bao_data
# returns 'unverified' None if the file is missing/corrupt). DESI DR2 (2025)
# supersedes DR1 as the primary late-universe BAO distance anchor.
_RELEASED_ONLY_BAO_KEYS = ("desi_dr2_bao",)

# What the chi² fits — sourced from the verified loader so the fitted covariance
# and the registry checksum are the SAME array object.
_BAO_DATA: dict[str, tuple[Any, Any]] = {
    key: (
        load_verified_bao_data(key)["mean_vector"],
        load_verified_bao_data(key)["covariance"],
    )
    for key in (*_HARDCODED_BAO, *_RELEASED_ONLY_BAO_KEYS)
}

# Single-dataset BAO keys the analytic (Ωm, H0·rd)-plane fast path can sample
# directly — a clean publication-tier ΛCDM posterior with no importance-sampler
# collapse. Both are DESI combined distance-ratio vectors of the same form.
_BAO_FAST_PATH_KEYS = frozenset({"desi_dr1_bao", "desi_dr2_bao"})


# ── eBOSS DR16 FSBAO joint (D_M/r_s, D_H/r_s, fσ8) full-covariance (2026-06-05) ──
# Per-tracer joint distance+growth likelihoods from the SDSS DR16 release
# (CobayaSampler/bao_data, sdss_DR16_BAOplus_{LRG,QSO}_FSBAO_DMDHfs8.dat +
# _covtot.txt), the higher-fidelity full-covariance companion to the fσ8-only
# diagonal entry "eboss_dr16_rsd" (which is left untouched). Raw upstream files
# are vendored and sha256-pinned verbatim (no reproduction step needed).
EBOSS_DR16_FSBAO_EXECUTABLE_KEYS = {"eboss_dr16_lrg_fsbao", "eboss_dr16_qso_fsbao"}

# BOSS DR12 consensus BAO (Alam et al. 2017) — the BAO-only likelihood behind
# the Planck 2018 "+BAO" columns. Same vendored mean/cov file shape as the
# FSBAO products, but the stored values use the DIMENSIONAL rs_fid convention
# (cobaya bao.sdss_dr12_consensus_bao: rs_fid = 147.78 Mpc), NOT the
# dimensionless D/r_d ratios — which is why it has its own prediction kernel
# (_dr12_consensus_predictions) and never flows through _fsbao_predictions.
SDSS_DR12_CONSENSUS_EXECUTABLE_KEYS = {"sdss_dr12_consensus_bao"}
SDSS_DR12_RS_FID_MPC = 147.78

# eBOSS DR16 released non-Gaussian BAO likelihood surfaces (Alam et al. 2021):
# the ELG 1D D_V/r_d probability table and the two 50×50 Lyα (D_M/r_d, D_H/r_d)
# likelihood grids — all DIMENSIONLESS ratios, executed as chi2 = -2·ln L from
# splines of the released surfaces (cobaya bao.sdss_dr16_* parity).
EBOSS_DR16_GRID_BAO_EXECUTABLE_KEYS = {
    "eboss_dr16_elg_bao",
    "eboss_dr16_lyauto_bao",
    "eboss_dr16_lyxqso_bao",
}


@lru_cache(maxsize=None)
def load_verified_fsbao_data(dataset_key: str) -> dict[str, Any]:
    """Load an eBOSS DR16 FSBAO (z, value, quantity) measurement vector + FULL
    covariance from the vendored, sha256-pinned mean.txt / cov.txt so the fitted
    covariance IS the checksum-verified array. ``quantity`` is one of
    {DM_over_rs, DH_over_rs, f_sigma8}. cov_fidelity is 'full' on a digest match,
    'unverified' on a missing-but-pinned or corrupt file (blocks publication).
    A released full covariance has no honest hand-typed substitute, so there is
    no literature_typed fallback."""
    mean_path = _VENDORED_COSMO_DATA_DIR / dataset_key / "mean.txt"
    cov_path = _VENDORED_COSMO_DATA_DIR / dataset_key / "cov.txt"
    pinned_cov = _registry_product_sha256(dataset_key, "covariance")
    pinned_mean = _registry_product_sha256(dataset_key, "measurement_vector")
    unverified = {
        "mean_vector": None, "covariance": None, "sha256": None,
        "hash_verified": False, "cov_fidelity": "unverified",
    }
    if not (mean_path.exists() and cov_path.exists()):
        return unverified
    try:
        mean_digest = hashlib.sha256(mean_path.read_bytes()).hexdigest()
        cov_digest = hashlib.sha256(cov_path.read_bytes()).hexdigest()
        rows: list[tuple[float, float, str]] = []
        for line in mean_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            z_str, value_str, quantity = stripped.split()
            rows.append((float(z_str), float(value_str), quantity))
        covariance = np.loadtxt(cov_path)
        n = len(rows)
        if n == 0 or covariance.shape != (n, n):
            raise ValueError(f"mean/cov shape mismatch: {n} points vs cov {covariance.shape}")
        verified = (mean_digest == pinned_mean) and (cov_digest == pinned_cov)
        return {
            "mean_vector": tuple(rows),
            "covariance": covariance,
            "sha256": cov_digest,
            "mean_sha256": mean_digest,
            "hash_verified": bool(verified),
            "cov_fidelity": "full" if verified else "unverified",
        }
    except Exception as exc:  # malformed/truncated file — degrade, never crash import
        logger.warning("FSBAO data product %s failed to load (%s); marking unverified", dataset_key, exc)
        return unverified


# (mean_vector, covariance) the FSBAO χ² fits — sourced from the verified loader so
# the fit reads the sha256-pinned committed artifacts.
_FSBAO_DATA: dict[str, tuple[Any, Any]] = {
    key: (
        load_verified_fsbao_data(key)["mean_vector"],
        load_verified_fsbao_data(key)["covariance"],
    )
    for key in EBOSS_DR16_FSBAO_EXECUTABLE_KEYS
}


def load_verified_dr12_consensus_data(dataset_key: str = "sdss_dr12_consensus_bao") -> dict[str, Any]:
    """BOSS DR12 consensus BAO (z, value, quantity) vector + full 6×6 covtot.

    Same vendored mean.txt/cov.txt shape and registry-pinned sha256 discipline
    as the FSBAO products, so the parsing core is shared verbatim. The values
    are DIMENSIONAL (rs_fid = 147.78 Mpc storage convention) and are predicted
    only by _dr12_consensus_predictions — never by the dimensionless FSBAO
    kernel, whose identically-named 'DM_over_rs' rows mean D_M/r_d.

    Known residual (inherited from the shared fsbao loader, documented in the
    backlog): the lru_cache caches a returned unverified record, so one
    transient read failure at first touch blocks the dataset until restart —
    fail-closed (loud refusal, never wrong numbers), unlike the union3
    raise-inside-cache pattern that self-heals."""
    return load_verified_fsbao_data(dataset_key)


@lru_cache(maxsize=None)
def load_verified_grid_bao_data(dataset_key: str) -> dict[str, Any]:
    """Load a released eBOSS DR16 non-Gaussian BAO likelihood surface from the
    vendored, sha256-pinned grid.txt: the ELG (D_V/r_d, probability) table or
    a Lyα 50×50 (D_M/r_d, D_H/r_d, likelihood) grid. cov_fidelity is 'full'
    on a digest match — the surface IS the released full (non-Gaussian)
    likelihood — and 'unverified' on a missing/corrupt/tampered file (blocks
    publication; chi2 refuses loudly). Same cached-failure residual as the
    fsbao loader (fail-closed; documented in the backlog)."""
    grid_path = _VENDORED_COSMO_DATA_DIR / dataset_key / "grid.txt"
    pinned = _registry_product_sha256(dataset_key, "likelihood_grid")
    unverified = {
        "grid": None, "sha256": None,
        "hash_verified": False, "cov_fidelity": "unverified",
    }
    if not grid_path.exists():
        return unverified
    try:
        raw = grid_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        grid = np.loadtxt(io.BytesIO(raw))
        if dataset_key == "eboss_dr16_elg_bao":
            if grid.shape != (399, 2) or grid[:, 1].min() <= 0:
                raise ValueError(f"malformed ELG table: shape {grid.shape}")
        else:
            if grid.shape != (2500, 3) or grid[:, 2].min() <= 0:
                raise ValueError(f"malformed Lya grid: shape {grid.shape}")
        verified = digest == pinned
        return {
            "grid": grid,
            "sha256": digest,
            "hash_verified": bool(verified),
            "cov_fidelity": "full" if verified else "unverified",
        }
    except Exception as exc:  # malformed/truncated file — degrade, never crash import
        logger.warning("grid BAO data product %s failed to load (%s); marking unverified", dataset_key, exc)
        return unverified


@lru_cache(maxsize=None)
def _elg_logprob_spline():
    """Cubic spline of ln(probability) over the released ELG D_V/r_d table —
    exactly cobaya's construction (UnivariateSpline(x, log(p), s=0))."""
    from scipy.interpolate import UnivariateSpline

    verified = load_verified_grid_bao_data("eboss_dr16_elg_bao")
    if verified["cov_fidelity"] == "unverified" or verified["grid"] is None:
        raise ValueError(
            "eboss_dr16_elg_bao table failed sha256 verification (or its vendored "
            "file is missing); refusing to build the likelihood spline."
        )
    grid = verified["grid"]
    spline = UnivariateSpline(grid[:, 0], np.log(grid[:, 1]), s=0)
    return spline, float(grid[0, 0]), float(grid[-1, 0])


@lru_cache(maxsize=None)
def _lya_loglike_spline(dataset_key: str):
    """Bicubic spline of ln(likelihood) over a released Lyα 50×50 grid —
    exactly cobaya's construction (RectBivariateSpline(x, y, log(L), kx=3,
    ky=3); x = D_M/r_d is the slow file axis)."""
    from scipy.interpolate import RectBivariateSpline

    verified = load_verified_grid_bao_data(dataset_key)
    if verified["cov_fidelity"] == "unverified" or verified["grid"] is None:
        raise ValueError(
            f"{dataset_key} grid failed sha256 verification (or its vendored "
            "file is missing); refusing to build the likelihood spline."
        )
    grid = verified["grid"]
    x = np.unique(grid[:, 0])
    y = np.unique(grid[:, 1])
    loglike = np.log(grid[:, 2]).reshape(len(x), len(y))
    spline = RectBivariateSpline(x, y, loglike, kx=3, ky=3)
    return spline, (float(x[0]), float(x[-1])), (float(y[0]), float(y[-1]))


def _bao_chi2_samples(
    samples: np.ndarray, parameter_order: list[str], key: str = "desi_dr1_bao"
) -> np.ndarray:
    if key == "sdss_6df_bao":
        # MGS half runs on the released non-Gaussian chi2(alpha) table
        # (2026-06-12 fidelity upgrade); 6dFGS half stays Gaussian.
        return _sdss_6df_mgs_chi2_samples(samples, parameter_order)
    if load_verified_bao_data(key)["cov_fidelity"] == "unverified":
        raise ValueError(
            f"BAO {key} covariance failed sha256 verification (or its vendored "
            "file is missing); refusing to compute chi2 from unverified data."
        )
    mean_vector, cov = _BAO_DATA[key]
    observed = np.asarray([row[1] for row in mean_vector], dtype=float)
    covariance = np.asarray(cov, dtype=float)
    predictions = _bao_predictions(samples, parameter_order, mean_vector)
    residual = predictions - observed
    return np.einsum("ni,ij,nj->n", residual, np.linalg.inv(covariance), residual)


def _bao_predictions(
    samples: np.ndarray,
    parameter_order: list[str],
    mean_vector: tuple[tuple[float, float, str], ...],
) -> np.ndarray:
    h0 = samples[:, parameter_order.index("H0")]
    omegam = samples[:, parameter_order.index("omegam")]
    rd = samples[:, parameter_order.index("rd")]
    # wCDM / w0waCDM extensions — read w/w0/wa per sample when present in
    # parameter_order. "w" is the single-parameter wCDM equation of state; it
    # maps to (w0=w, wa=0). When neither is in the parameter_order this is the
    # flat-ΛCDM (-1, 0) limit and the predictions match the legacy path.
    n_samples = samples.shape[0]
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
    predictions = np.empty((n_samples, len(mean_vector)), dtype=float)
    distance_cache: dict[float, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for col, (z, _value, quantity) in enumerate(mean_vector):
        if z not in distance_cache:
            distance_cache[z] = _flat_de_distances_at_z(z, h0, omegam, w0=w0, wa=wa)
        dm, dh, dv = distance_cache[z]
        if quantity in {"DM_over_rs", "DM_over_rd"}:
            predictions[:, col] = dm / rd
        elif quantity in {"DH_over_rs", "DH_over_rd"}:
            predictions[:, col] = dh / rd
        elif quantity in {"DV_over_rs", "DV_over_rd"}:
            predictions[:, col] = dv / rd
        else:
            raise ValueError(f"unsupported BAO quantity {quantity!r}")
    return predictions


def _fsbao_predictions(
    samples: np.ndarray,
    parameter_order: list[str],
    mean_vector: tuple[tuple[float, float, str], ...],
) -> np.ndarray:
    """Predicted joint (D_M/r_s, D_H/r_s, fσ8) vector for the eBOSS DR16 FSBAO
    likelihoods.  Distance ratios reuse the flat-w0waCDM BAO kernel (/r_d, where
    r_s≡r_d); fσ8 reuses the RSD growth kernel (f(z)·σ8·D(z)/D(0))."""
    h0 = samples[:, parameter_order.index("H0")]
    omegam = samples[:, parameter_order.index("omegam")]
    rd = samples[:, parameter_order.index("rd")]
    sigma8 = samples[:, parameter_order.index("sigma8")]
    n_samples = samples.shape[0]
    if "w0" in parameter_order:
        w0 = samples[:, parameter_order.index("w0")]
    elif "w" in parameter_order:
        w0 = samples[:, parameter_order.index("w")]
    else:
        w0 = np.full(n_samples, -1.0, dtype=float)
    wa = samples[:, parameter_order.index("wa")] if "wa" in parameter_order else np.zeros(n_samples)
    predictions = np.empty((n_samples, len(mean_vector)), dtype=float)
    distance_cache: dict[float, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for col, (z, _value, quantity) in enumerate(mean_vector):
        if quantity in {"DM_over_rs", "DM_over_rd", "DH_over_rs", "DH_over_rd", "DV_over_rs", "DV_over_rd"}:
            if z not in distance_cache:
                distance_cache[z] = _flat_de_distances_at_z(z, h0, omegam, w0=w0, wa=wa)
            dm, dh, dv = distance_cache[z]
            if quantity.startswith("DM"):
                predictions[:, col] = dm / rd
            elif quantity.startswith("DH"):
                predictions[:, col] = dh / rd
            else:
                predictions[:, col] = dv / rd
        elif quantity in {"f_sigma8", "fsigma8"}:
            f_z = _growth_rate_f(z, omegam, w0, wa)
            d_ratio = _growth_factor_ratio(z, omegam, w0, wa)
            predictions[:, col] = f_z * sigma8 * d_ratio
        else:
            raise ValueError(f"unsupported FSBAO quantity {quantity!r}")
    return predictions


def _fsbao_chi2_samples(
    samples: np.ndarray, parameter_order: list[str], key: str
) -> np.ndarray:
    """Full-covariance χ² = rᵀ C⁻¹ r of an eBOSS DR16 FSBAO joint vector."""
    verified = load_verified_fsbao_data(key)
    if verified["cov_fidelity"] == "unverified" or verified["covariance"] is None:
        raise ValueError(
            f"FSBAO {key} covariance failed sha256 verification (or its vendored "
            "file is missing); refusing to compute chi2 from unverified data."
        )
    mean_vector, cov = verified["mean_vector"], verified["covariance"]
    observed = np.asarray([row[1] for row in mean_vector], dtype=float)
    predictions = _fsbao_predictions(samples, parameter_order, mean_vector)
    residual = predictions - observed
    return np.einsum("ni,ij,nj->n", residual, np.linalg.inv(np.asarray(cov, dtype=float)), residual)


def _dr12_consensus_predictions(
    samples: np.ndarray,
    parameter_order: list[str],
    mean_vector: tuple[tuple[float, float, str], ...],
) -> np.ndarray:
    """Predicted BOSS DR12 consensus vector in the release's DIMENSIONAL
    storage convention (cobaya bao.sdss_dr12_consensus_bao, rs_fid = 147.78):

      DM_over_rs row:  D_M(z) · (rs_fid / r_d)   [Mpc]
      bao_Hz_rs  row:  H(z)  · (r_d / rs_fid)    [km/s/Mpc]

    Mirrors cobaya's theory_fun with rs_rescale = 1/rs_fid exactly; H(z) is
    recovered from the flat-w0waCDM kernel's Hubble distance D_H = c/H."""
    h0 = samples[:, parameter_order.index("H0")]
    omegam = samples[:, parameter_order.index("omegam")]
    rd = samples[:, parameter_order.index("rd")]
    n_samples = samples.shape[0]
    if "w0" in parameter_order:
        w0 = samples[:, parameter_order.index("w0")]
    elif "w" in parameter_order:
        w0 = samples[:, parameter_order.index("w")]
    else:
        w0 = np.full(n_samples, -1.0, dtype=float)
    wa = samples[:, parameter_order.index("wa")] if "wa" in parameter_order else np.zeros(n_samples)
    predictions = np.empty((n_samples, len(mean_vector)), dtype=float)
    distance_cache: dict[float, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for col, (z, _value, quantity) in enumerate(mean_vector):
        if z not in distance_cache:
            distance_cache[z] = _flat_de_distances_at_z(z, h0, omegam, w0=w0, wa=wa)
        dm, dh, _dv = distance_cache[z]
        if quantity == "DM_over_rs":
            predictions[:, col] = dm * (SDSS_DR12_RS_FID_MPC / rd)
        elif quantity == "bao_Hz_rs":
            predictions[:, col] = (C_LIGHT_KM_S / dh) * (rd / SDSS_DR12_RS_FID_MPC)
        else:
            raise ValueError(f"unsupported DR12 consensus quantity {quantity!r}")
    return predictions


def _dr12_chi2_samples(
    samples: np.ndarray, parameter_order: list[str], key: str
) -> np.ndarray:
    """Full-covariance χ² = rᵀ C⁻¹ r of the BOSS DR12 consensus BAO vector."""
    verified = load_verified_dr12_consensus_data(key)
    if verified["cov_fidelity"] == "unverified" or verified["covariance"] is None:
        raise ValueError(
            f"DR12 consensus {key} covariance failed sha256 verification (or its "
            "vendored file is missing); refusing to compute chi2 from unverified data."
        )
    mean_vector, cov = verified["mean_vector"], verified["covariance"]
    observed = np.asarray([row[1] for row in mean_vector], dtype=float)
    predictions = _dr12_consensus_predictions(samples, parameter_order, mean_vector)
    residual = predictions - observed
    return np.einsum("ni,ij,nj->n", residual, np.linalg.inv(np.asarray(cov, dtype=float)), residual)


# Effective redshifts of the eBOSS DR16 released likelihood surfaces (from the
# cobaya bao.sdss_dr16_* yamls).
_EBOSS_ELG_GRID_Z = 0.845
_EBOSS_LYA_GRID_Z = 2.334


def _grid_bao_chi2_samples(
    samples: np.ndarray, parameter_order: list[str], key: str
) -> np.ndarray:
    """chi2 = -2·ln L from a released eBOSS DR16 non-Gaussian BAO surface.

    Out-of-grid samples get chi2 = +inf, zeroing their importance weights —
    NOTE this deliberately deviates from cobaya in BOTH directions, each time
    fail-safe: cobaya's Lyα RectBivariateSpline silently EXTRAPOLATES the
    log-likelihood out-of-grid (live-verified pathology: a far-outside point
    scores HIGHER than the in-grid minimum), and cobaya's ELG 1D spline
    (ext=2) CRASHES with a ValueError out-of-bounds. We refuse support
    instead; the refused prior-volume fraction is reported per dataset by
    _grid_support_warnings (parity tests stay in-grid)."""
    h0 = samples[:, parameter_order.index("H0")]
    omegam = samples[:, parameter_order.index("omegam")]
    rd = samples[:, parameter_order.index("rd")]
    n_samples = samples.shape[0]
    if "w0" in parameter_order:
        w0 = samples[:, parameter_order.index("w0")]
    elif "w" in parameter_order:
        w0 = samples[:, parameter_order.index("w")]
    else:
        w0 = np.full(n_samples, -1.0, dtype=float)
    wa = samples[:, parameter_order.index("wa")] if "wa" in parameter_order else np.zeros(n_samples)
    if key == "eboss_dr16_elg_bao":
        _dm, _dh, dv = _flat_de_distances_at_z(_EBOSS_ELG_GRID_Z, h0, omegam, w0=w0, wa=wa)
        x = dv / rd
        spline, lo, hi = _elg_logprob_spline()
        chi2 = np.full(x.shape, np.inf, dtype=float)
        in_bounds = (x >= lo) & (x <= hi)
        if np.any(in_bounds):
            chi2[in_bounds] = -2.0 * spline(x[in_bounds])
        return chi2
    if key in ("eboss_dr16_lyauto_bao", "eboss_dr16_lyxqso_bao"):
        dm, dh, _dv = _flat_de_distances_at_z(_EBOSS_LYA_GRID_Z, h0, omegam, w0=w0, wa=wa)
        xm = dm / rd
        yh = dh / rd
        spline, (xlo, xhi), (ylo, yhi) = _lya_loglike_spline(key)
        chi2 = np.full(xm.shape, np.inf, dtype=float)
        in_bounds = (xm >= xlo) & (xm <= xhi) & (yh >= ylo) & (yh <= yhi)
        if np.any(in_bounds):
            chi2[in_bounds] = -2.0 * spline.ev(xm[in_bounds], yh[in_bounds])
        return chi2
    raise ValueError(f"executable grid BAO entry {key!r} has no chi2 dispatch")


def _grid_support_warnings(
    grid_bao_entries: list[CosmologyDatasetEntry],
    parameter_order: list[str],
    prior_bounds: dict[str, tuple[float, float]],
    seed: int,
) -> list[str]:
    """Per-dataset accounting of how much PRIOR volume a released grid surface
    refuses (2026-06-12 review: an ELG-only chain silently hard-zeroed ~38% of
    the default prior at a <2σ-equivalent table edge while reporting
    publication tier). The truncation is faithful — it IS the release's own
    support — but it must be observable, not silent."""
    if not grid_bao_entries:
        return []
    rng = np.random.default_rng((int(seed) ^ 0x5EED) & 0x7FFFFFFF)
    n_probe = 2000
    samples = np.column_stack([
        rng.uniform(prior_bounds[name][0], prior_bounds[name][1], n_probe)
        for name in parameter_order
    ])
    notes: list[str] = []
    for entry in grid_bao_entries:
        try:
            chi2 = _grid_bao_chi2_samples(samples, parameter_order, entry.key)
        except ValueError:
            continue  # unverified data refuses elsewhere, loudly
        refused = float(np.mean(~np.isfinite(chi2)))
        if refused > 0.2:
            notes.append(
                f"{refused:.1%} of the prior volume falls outside the released "
                f"{entry.key} grid support and was refused (chi2=+inf, never "
                "extrapolated) — the posterior is truncated at the release's "
                "own support."
            )
    return notes

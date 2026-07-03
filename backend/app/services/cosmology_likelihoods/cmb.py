"""Compressed CMB distance priors and compressed-Gaussian chi^2.

Split verbatim out of the pre-2026-07-03 single-file
app/services/cosmology_likelihoods.py (7,757 lines). Import the package
``app.services.cosmology_likelihoods`` — it re-exports every pre-split name
and keeps the original one-namespace monkeypatch semantics.
"""

from __future__ import annotations


import numpy as np

from app.services.cosmology_likelihoods.core import (
    CosmologyDatasetEntry,
    _derived_s8_from_samples,
    _s8_is_derived,
)

from app.services.cosmology_likelihoods.distances import (
    _GL64_NODES,
    _GL64_WEIGHTS,
)



# ── CMB distance priors (Chen, Huang & Wang 2019, arXiv:1808.05724, Eqs 1-10) ──
# Compressed Planck 2018 TT,TE,EE+lowE geometry as (R, l_A, Omega_b h^2). The
# paper validates these base-LCDM priors against wCDM and CPL, so they remain
# valid in extended dark-energy models — unlike a hard H0/Omega_m Gaussian, which
# forbids the geometric slide along theta*=const that IS the dark-energy signal.
_T_CMB_K = 2.7255
_T_CMB_RATIO4 = (_T_CMB_K / 2.7) ** 4
# Eq (3): 3/(4 Omega_gamma h^2) = 31500 (T_CMB/2.7)^-4 ; baryon loading of `a`.
_RS_BARYON_COEF = 31500.0 / _T_CMB_RATIO4


def _cmb_distance_priors(omegam, h0, ombh2, w0=-1.0, wa=0.0):
    """(R, l_A, Omega_b h^2) CMB distance priors for flat (w0,wa)CDM, per
    Chen-Huang-Wang 2019.  Inputs scalar or broadcastable arrays.  R and l_A are
    H0-independent except through z*/radiation (the c/H0 cancels in both).
    Self-check: LCDM at Planck 2018 (Om=0.3153, H0=67.36, ombh2=0.02237) ->
    R~1.750, l_A~301.5."""
    scalar = np.ndim(omegam) == 0
    om = np.atleast_1d(np.asarray(omegam, float))
    obh2 = np.atleast_1d(np.asarray(ombh2, float))
    h2 = (np.atleast_1d(np.asarray(h0, float)) / 100.0) ** 2
    w0a = np.atleast_1d(np.asarray(w0, float))
    waa = np.atleast_1d(np.asarray(wa, float))
    om, obh2, h2, w0a, waa = np.broadcast_arrays(om, obh2, h2, w0a, waa)
    omh2 = om * h2
    # Recombination redshift z* (Eqs 8-10, Hu & Sugiyama 1996)
    g1 = 0.0738 * obh2 ** -0.238 / (1.0 + 39.5 * obh2 ** 0.763)
    g2 = 0.560 / (1.0 + 21.1 * obh2 ** 1.81)
    zstar = 1048.0 * (1.0 + 0.00124 * obh2 ** -0.738) * (1.0 + g1 * omh2 ** g2)
    # Radiation density incl. neutrinos (Eq 6); flat closes Omega_de.
    omr = om / (1.0 + 2.5e4 * omh2 / _T_CMB_RATIO4)
    omde = 1.0 - om - omr
    omc, omrc, omdec = om[:, None], omr[:, None], omde[:, None]
    w0c, wac, obc = w0a[:, None], waa[:, None], obh2[:, None]
    node = (_GL64_NODES + 1.0) * 0.5  # (64,) in [0,1]

    def inv_E(z):  # z shape (N, 64)
        x = 1.0 + z
        rho = x ** (3.0 * (1.0 + w0c + wac)) * np.exp(-3.0 * wac * z / x)
        return 1.0 / np.sqrt(omrc * x ** 4 + omc * x ** 3 + omdec * rho)

    # I = int_0^{z*} dz/E, in u=ln(1+z) so the low-z-peaked integrand is smooth.
    ustar = np.log(1.0 + zstar)[:, None]
    u = ustar * node
    i_dc = np.sum(_GL64_WEIGHTS * (ustar * 0.5) * np.exp(u) * inv_E(np.exp(u) - 1.0), axis=-1)
    # J = int_0^{a*} da / (a^2 E sqrt(3(1 + Rb a))).  c/H0 cancels in l_A = pi*I/J.
    astar = (1.0 / (1.0 + zstar))[:, None]
    a = astar * node
    rb = _RS_BARYON_COEF * obc * a
    integ = inv_E(1.0 / a - 1.0) / (a ** 2 * np.sqrt(3.0 * (1.0 + rb)))
    i_rs = np.sum(_GL64_WEIGHTS * (astar * 0.5) * integ, axis=-1)

    big_r = np.sqrt(om) * i_dc
    l_a = np.pi * i_dc / i_rs
    if scalar:
        return float(big_r[0]), float(l_a[0]), float(obh2[0])
    return big_r, l_a, obh2


# Planck 2018 TT,TE,EE+lowE distance priors, base-LCDM block (the paper validates
# this block for wCDM/CPL too).  Chen-Huang-Wang 2019, arXiv:1808.05724, Table I:
# R=1.7502+-0.0046, l_A=301.471+-0.090, ombh2=0.02236+-0.00015, with the listed
# correlation matrix.  Used as the CMB term for extended FLAT dark-energy fits.
_PLANCK18_DP_MEAN = np.array([1.7502, 301.471, 0.02236])
_PLANCK18_DP_SIGMA = np.array([0.0046, 0.090, 0.00015])
_PLANCK18_DP_CORR = np.array([
    [1.00, 0.46, -0.66],
    [0.46, 1.00, -0.33],
    [-0.66, -0.33, 1.00],
])
_PLANCK18_DP_INVCOV = np.linalg.inv(
    _PLANCK18_DP_SIGMA[:, None] * _PLANCK18_DP_SIGMA[None, :] * _PLANCK18_DP_CORR
)


def _planck_distance_prior_chi2(samples: np.ndarray, parameter_order: list[str]) -> np.ndarray:
    """Per-sample chi2 of the Planck 2018 CMB distance prior (R, l_A, ombh2) for an
    extended FLAT dark-energy chain (Chen-Huang-Wang 2019)."""
    om = samples[:, parameter_order.index("omegam")]
    h0 = samples[:, parameter_order.index("H0")]
    obh2 = samples[:, parameter_order.index("ombh2")]
    if "w0" in parameter_order:
        w0 = samples[:, parameter_order.index("w0")]
    elif "w" in parameter_order:
        w0 = samples[:, parameter_order.index("w")]
    else:
        w0 = -1.0
    wa = samples[:, parameter_order.index("wa")] if "wa" in parameter_order else 0.0
    big_r, l_a, _ = _cmb_distance_priors(om, h0, obh2, w0=w0, wa=wa)
    resid = np.column_stack([big_r, l_a, obh2]) - _PLANCK18_DP_MEAN
    return np.einsum("ni,ij,nj->n", resid, _PLANCK18_DP_INVCOV, resid)


def _compressed_chi2_samples(
    samples: np.ndarray,
    parameter_order: list[str],
    compressed_entries: list[CosmologyDatasetEntry],
) -> tuple[np.ndarray, list[str]]:
    total = np.zeros(samples.shape[0], dtype=float)
    invalid_specs: list[str] = []
    # S8 = σ8·√(Ωm/0.3) is derived (not a sampled column) whenever σ8 and Ωm are
    # both sampled; its Gaussian then applies on the derived per-sample value.
    derived_s8 = (
        _derived_s8_from_samples(samples, parameter_order)
        if _s8_is_derived(parameter_order)
        else None
    )
    for entry in compressed_entries:
        spec = entry.compressed_likelihood
        if spec is None:
            continue
        # Extended FLAT dark-energy models: the compressed Planck spec pins
        # H0/omegam at their LCDM projection, which forbids the geometric slide
        # along theta*=const that IS the w0/wa signal.  Use the model-valid
        # acoustic-scale distance prior (R, l_A, ombh2) instead (Chen-Huang-Wang
        # 2019), keeping any derived-S8 growth row.  Curved (ok_*) models keep the
        # old path (a FLAT distance prior would be wrong; the curved prior is
        # deferred).  LCDM has no DE param -> untouched, byte-for-byte unchanged.
        de_flat = (
            entry.key == "planck2018_compressed"
            and any(p in parameter_order for p in ("w", "w0", "wa"))
            and "omegak" not in parameter_order
        )
        if de_flat:
            try:
                total += _planck_distance_prior_chi2(samples, parameter_order)
                if derived_s8 is not None and "S8" in spec.parameters:
                    j = list(spec.parameters).index("S8")
                    s8_mean = float(np.asarray(spec.mean, dtype=float)[j])
                    s8_var = float(np.asarray(spec.covariance, dtype=float)[j, j])
                    total += (derived_s8 - s8_mean) ** 2 / s8_var
            except Exception as exc:
                invalid_specs.append(f"{entry.key}: {exc}")
            continue
        try:
            params = list(spec.parameters)
            names = [
                name
                for name in params
                if name in parameter_order
                or (name == "S8" and derived_s8 is not None)
            ]
            if not names:
                # B2: none of this dataset's parameters are in the sampled set,
                # so it can contribute no chi2. Record it as an invalid spec —
                # which flips publication_ready off and surfaces a blocked
                # reason — instead of silently dropping it to chi2=0 while it
                # still appears in datasets_used as if it had constrained the
                # fit (e.g. a BBN ombh2 prior selected alongside a chain that
                # samples only H0/omegam/rd, where ombh2 is never sampled).
                invalid_specs.append(
                    f"{entry.key}: none of its parameters {params} are in the "
                    f"sampled parameter set {list(parameter_order)}, so it "
                    f"contributed no constraint — not applied as run."
                )
                continue
            local_idx = [params.index(name) for name in names]
            mean = np.asarray(spec.mean, dtype=float)[local_idx]
            cov = np.asarray(spec.covariance, dtype=float)[np.ix_(local_idx, local_idx)]
            columns = [
                derived_s8
                if name == "S8" and name not in parameter_order
                else samples[:, parameter_order.index(name)]
                for name in names
            ]
            residual = np.column_stack(columns) - mean
            total += np.einsum("ni,ij,nj->n", residual, np.linalg.inv(cov), residual)
        except Exception as exc:
            invalid_specs.append(f"{entry.key}: {exc}")
    return total, invalid_specs


# Primary-CMB external-cobaya likelihoods that sample the full CMB parameter set
# (ombh2, omch2, H0, ns, As, tau), rather than the geometric (H0, Omega_m, rd)
# set the compressed/in-process probes use.
CMB_COBAYA_EXECUTABLE_KEYS = frozenset({
    "planck_2018_highl_TTTEEE_lite",
    "planck_2018_lowl_TT",
    "planck_2018_lowl_EE",
    "planck_2018_lensing",
})

# A_planck is sampled only when plik_lite or the 2018 lensing likelihood is
# selected (lensing's params include the planck_calib defaults, so it consumes
# the shared calibration). The native low-l likelihoods CAN consume it (cobaya
# get_can_support_params), but their default is calib=1 and a 0.25%
# calibration uncertainty is negligible against l<=29 cosmic variance — so a
# lowl-only run deliberately fixes it. In the full stack cobaya shares the one
# sampled A_planck across all likelihoods, matching official Planck practice.
CMB_APLANCK_KEYS = frozenset({
    "planck_2018_highl_TTTEEE_lite",
    "planck_2018_lensing",
})

"""Growth-of-structure math (growth index, growth factor, f(z)).

Split verbatim out of the pre-2026-07-03 single-file
app/services/cosmology_likelihoods.py (7,757 lines). Import the package
``app.services.cosmology_likelihoods`` — it re-exports every pre-split name
and keeps the original one-namespace monkeypatch semantics.
"""

from __future__ import annotations


import numpy as np

from app.services.cosmology_likelihoods.distances import (
    _de_energy_density,
)



# ── Structure-growth kernel for RSD fσ8 (Linder γ-parametrisation) ──────────
# 32-node Gauss-Legendre rule for the growth-factor integral, computed once.
_GROWTH_GL32_NODES, _GROWTH_GL32_WEIGHTS = np.polynomial.legendre.leggauss(32)


def _growth_index_gamma(w0: np.ndarray, wa: np.ndarray) -> np.ndarray:
    """Growth index γ (Linder & Cahn 2007).  ΛCDM → 0.55.  With CPL w(z=1) =
    w0 + wa·(1−a) at a=0.5: γ = 0.55 + 0.05[1+w(z=1)] for w(z=1) ≥ −1, else
    γ = 0.55 + 0.02[1+w(z=1)] (the phantom-side slope)."""
    w_z1 = w0 + 0.5 * wa
    return np.where(
        w_z1 >= -1.0,
        0.55 + 0.05 * (1.0 + w_z1),
        0.55 + 0.02 * (1.0 + w_z1),
    )


def _omega_m_of_z(
    z: float, omegam: np.ndarray, w0: np.ndarray, wa: np.ndarray
) -> np.ndarray:
    """Matter density fraction Ωm(z) = Ωm(1+z)³ / E²(z) for flat w0waCDM."""
    one_plus_z = 1.0 + z
    rho_de = _de_energy_density(1.0 / one_plus_z, w0, wa)
    ez2 = omegam * one_plus_z ** 3 + (1.0 - omegam) * rho_de
    return omegam * one_plus_z ** 3 / ez2


def _growth_rate_f(
    z: float, omegam: np.ndarray, w0: np.ndarray, wa: np.ndarray
) -> np.ndarray:
    """Linear growth rate f(z) = Ωm(z)^γ."""
    return _omega_m_of_z(z, omegam, w0, wa) ** _growth_index_gamma(w0, wa)


def _growth_factor_ratio(
    z: float, omegam: np.ndarray, w0: np.ndarray, wa: np.ndarray
) -> np.ndarray:
    """Normalised linear growth factor D(z)/D(0) = exp(−∫₀^z f(z')/(1+z') dz').

    f = dlnD/dlna ⇒ dlnD/dz = −f/(1+z); 32-point Gauss-Legendre quadrature.
    Sample arrays (omegam, w0, wa) are (N,); returns (N,)."""
    if z <= 0.0:
        return np.ones_like(omegam)
    nodes, weights = _GROWTH_GL32_NODES, _GROWTH_GL32_WEIGHTS
    zp = 0.5 * z * (nodes + 1.0)                                    # (K,)
    one_plus_zp = 1.0 + zp                                          # (K,)
    rho_de = _de_energy_density(
        1.0 / one_plus_zp[None, :], w0[:, None], wa[:, None]
    )                                                              # (N,K)
    ez2 = omegam[:, None] * one_plus_zp[None, :] ** 3 + (1.0 - omegam[:, None]) * rho_de
    omega_m_zp = omegam[:, None] * one_plus_zp[None, :] ** 3 / ez2  # (N,K)
    gamma = _growth_index_gamma(w0, wa)[:, None]                    # (N,1)
    integrand = omega_m_zp ** gamma / one_plus_zp[None, :]          # (N,K)
    integral = 0.5 * z * np.sum(weights[None, :] * integrand, axis=1)  # (N,)
    return np.exp(-integral)

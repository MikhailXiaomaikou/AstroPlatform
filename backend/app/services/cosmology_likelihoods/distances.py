"""Background expansion / comoving-distance / distance-modulus math.

Split verbatim out of the pre-2026-07-03 single-file
app/services/cosmology_likelihoods.py (7,757 lines). Import the package
``app.services.cosmology_likelihoods`` — it re-exports every pre-split name
and keeps the original one-namespace monkeypatch semantics.
"""

from __future__ import annotations


import numpy as np

from app.services.cosmology_likelihoods.core import (
    C_LIGHT_KM_S,
)



# Cached Gauss-Legendre quadrature nodes/weights (deg=64 trivially exact for
# the flat-ΛCDM E(z) integrand to << 0.1 mag accuracy over z ∈ [0, 3]).
_GL64_NODES, _GL64_WEIGHTS = np.polynomial.legendre.leggauss(64)


def _flat_de_dm_grid_vectorized(
    z: np.ndarray,
    h0: np.ndarray,
    omegam: np.ndarray,
    w0: np.ndarray,
    wa: np.ndarray,
) -> np.ndarray:
    """Vectorized comoving distance D_M(z; H0, Ωm, w0, wa) over (z, sample) pairs
    under flat w0waCDM (CPL).  ΛCDM is the (w0=-1, wa=0) limit.

    z      : (n_sn,)        — redshifts to evaluate at
    h0     : (n_samples,)   — Hubble constant per posterior sample
    omegam : (n_samples,)   — Ωm per posterior sample
    w0     : (n_samples,)   — dark-energy EOS at a=1 per sample
    wa     : (n_samples,)   — dark-energy EOS slope per sample
    Returns: (n_sn, n_samples)  — D_M in Mpc

    Replaces the previous Python `for j, z_j in z` loop inside Pantheon+
    chi² with one big NumPy einsum, and adds DE-aware E(z) integrand so
    wcdm/w0waCDM joint fits with SN are physically self-consistent
    (bug fix from review #2: SN χ² used to silently override w to -1).

    Memory: O(n_samples · n_sn · 64) float64; ~30 MB for 32 walkers × 1701
    SN × 64 nodes.  Tractable.
    """
    nodes = _GL64_NODES
    weights = _GL64_WEIGHTS
    # x[j, k] = 0.5 * z[j] * (nodes[k] + 1)  — quadrature variable
    x = 0.5 * z[:, None] * (nodes[None, :] + 1.0)            # (n_sn, 64)
    one_plus_x = 1.0 + x                                     # (n_sn, 64)
    one_plus_x_cubed = one_plus_x ** 3                       # (n_sn, 64)
    # Scale factor a = 1/(1+x); ρ_DE(a)/ρ_DE,0 = a^(-3(1+w0+wa)) · exp(-3 wa (1-a))
    a_int = 1.0 / one_plus_x                                 # (n_sn, 64)
    rho_de = (
        a_int[None, :, :] ** (-3.0 * (1.0 + w0[:, None, None] + wa[:, None, None]))
        * np.exp(-3.0 * wa[:, None, None] * (1.0 - a_int[None, :, :]))
    )                                                         # (n_samples, n_sn, 64)
    # ez[i, j, k] = sqrt(Ωm[i] * (1+x[j,k])^3 + (1 - Ωm[i]) * ρ_DE)
    ez = np.sqrt(
        omegam[:, None, None] * one_plus_x_cubed[None, :, :]
        + (1.0 - omegam[:, None, None]) * rho_de
    )                                                         # (n_samples, n_sn, 64)
    integral = 0.5 * z[None, :] * np.sum(weights[None, None, :] / ez, axis=2)
    # D_M = (c / H0) * integral
    dm = (C_LIGHT_KM_S / h0[:, None]) * integral             # (n_samples, n_sn)
    return dm.T  # (n_sn, n_samples)


def _flat_lcdm_dm_grid_vectorized(
    z: np.ndarray, h0: np.ndarray, omegam: np.ndarray
) -> np.ndarray:
    """ΛCDM-only convenience wrapper around :func:`_flat_de_dm_grid_vectorized`.

    Equivalent to (w0=-1, wa=0); kept as a thin shim for any caller that
    has no DE parameters in its parameter_order.
    """
    w0 = np.full_like(h0, -1.0, dtype=float)
    wa = np.zeros_like(h0, dtype=float)
    return _flat_de_dm_grid_vectorized(z, h0, omegam, w0, wa)


def _de_energy_density(a: np.ndarray, w0: np.ndarray, wa: np.ndarray) -> np.ndarray:
    """Flat-DE ρ_DE(a) / ρ_DE,0 for the CPL w(a) = w0 + wa(1-a) parameterization.

    Closed form: f(a) = a^(-3(1+w0+wa)) * exp(-3 wa (1-a)).
    Reduces to 1 for ΛCDM (w0=-1, wa=0). Vectorized over both axes.
    """
    return a ** (-3.0 * (1.0 + w0 + wa)) * np.exp(-3.0 * wa * (1.0 - a))


def _flat_de_distances_at_z(
    z: float,
    h0: np.ndarray,
    omegam: np.ndarray,
    *,
    w0: np.ndarray | float = -1.0,
    wa: np.ndarray | float = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Comoving D_M, Hubble D_H = c/H(z), volume D_V at redshift ``z`` under
    flat w0waCDM. ΛCDM is the (w0=-1, wa=0) limit.

    All sample arrays (h0, omegam, w0, wa) must be 1D of equal length; scalar
    w0/wa are broadcast. The Gauss-Legendre 64-point rule integrates 1/E(z')
    over z' ∈ [0, z] to < 1e-12 over z ≤ 3 for any sane (w0, wa) box.
    """
    nodes, weights = np.polynomial.legendre.leggauss(64)
    w0_arr = np.asarray(w0, dtype=float).reshape(-1) if np.ndim(w0) else np.full_like(omegam, float(w0))
    wa_arr = np.asarray(wa, dtype=float).reshape(-1) if np.ndim(wa) else np.full_like(omegam, float(wa))
    x = 0.5 * z * (nodes + 1.0)                                 # (64,)
    one_plus_x = 1.0 + x[None, :]                                # (1, 64)
    a_int = 1.0 / one_plus_x                                     # (1, 64) — scale factor
    rho_de_grid = _de_energy_density(a_int, w0_arr[:, None], wa_arr[:, None])
    ez_grid = np.sqrt(
        omegam[:, None] * one_plus_x ** 3
        + (1.0 - omegam[:, None]) * rho_de_grid
    )
    integral = 0.5 * z * np.sum(weights[None, :] / ez_grid, axis=1)
    dm = (C_LIGHT_KM_S / h0) * integral
    a_z = 1.0 / (1.0 + z)
    ez = np.sqrt(omegam * (1.0 + z) ** 3 + (1.0 - omegam) * _de_energy_density(a_z, w0_arr, wa_arr))
    dh = C_LIGHT_KM_S / (h0 * ez)
    dv = np.cbrt(z * dm * dm * dh)
    return dm, dh, dv


def _flat_lcdm_distances_at_z(
    z: float,
    h0: np.ndarray,
    omegam: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """ΛCDM-only convenience wrapper around :func:`_flat_de_distances_at_z`."""
    return _flat_de_distances_at_z(z, h0, omegam, w0=-1.0, wa=0.0)

"""Generate the Moresco 2020 cosmic-chronometer full covariance from the vendored
raw source files.

This is a faithful port of the official recipe published by Michele Moresco at
https://gitlab.com/mmoresco/CCcovariance (examples/CC_covariance.ipynb), following
Moresco et al. (2020), ApJ 898, 82 (arXiv:2003.07362).

The total covariance is

    Cov_ij = Cov_diag_ij + Cov_imf_ij + Cov_modooo_ij

where, exactly as in the upstream notebook's final `cov_mat`:
  * Cov_diag[i,i] = errHz[i]**2            (quoted total error; stat + metallicity,
                                            already diagonal in HzTable_MM_BC03.dat)
  * Cov_imf[i,j]    = Hz[i]*imf_i  * Hz[j]*imf_j      (IMF systematic, fully correlated)
  * Cov_modooo[i,j] = Hz[i]*ooo_i  * Hz[j]*ooo_j      (SPS-model "one-of-others"
                                                       systematic, fully correlated)
with imf_i, ooo_i the per-cent contributions from data_MM20.dat interpolated onto
the 15 BC03 redshifts and divided by 100.  data_MM20.dat tabulates the systematic
fractions only out to z=1.475, while the H(z) table reaches z=1.965, so np.interp
flat-clamps the IMF/OOO fractions of the highest-z point to the z=1.475 endpoint
values (imf=0.20%, ooo=2.34%) rather than extrapolating.  This matches the upstream
notebook, which also relies on np.interp's default constant extrapolation.  The
notebook deliberately combines only
diag + imf + mod_ooo (not the separate st.lib / sps columns) to avoid double-counting
the model systematic; we reproduce that choice verbatim.

This is NOT a partial covariance: the upstream notebook's section titled "Estimate
full CC covariance matrix" is exactly `cov_mat = cov_mat_spsooo + cov_mat_imf +
cov_mat_diag`.  The st.lib and `mod` columns are computed in the "components" section
only for decomposition/illustration (README: "how to decouple it into its various
components"); `mod_ooo` is the authors' chosen conservative model-systematic estimate,
so adding st.lib / `mod` on top would double-count the model term.

Inputs  (sha256-pinned in the cosmology registry, vendored next to this output):
  data/cosmology/cosmic_chronometers_moresco20/HzTable_MM_BC03.dat
  data/cosmology/cosmic_chronometers_moresco20/data_MM20.dat
Outputs (sha256-pinned in the registry; what the chi2 actually reads):
  data/cosmology/cosmic_chronometers_moresco20/mean.txt   (z  H(z)  Hz)
  data/cosmology/cosmic_chronometers_moresco20/cov.txt     (15x15)

Re-run:  python scripts/gen_moresco20_cc_covariance.py
"""
from __future__ import annotations

import pathlib

import numpy as np

DATA_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "cosmology" / "cosmic_chronometers_moresco20"


def build_covariance() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    hz_table = DATA_DIR / "HzTable_MM_BC03.dat"
    mm20 = DATA_DIR / "data_MM20.dat"

    z, hz, err_hz = np.genfromtxt(hz_table, comments="#", usecols=(0, 1, 2), unpack=True, delimiter=",")
    zmod, imf, _slib, _sps, spsooo = np.genfromtxt(mm20, comments="#", usecols=(0, 1, 2, 3, 4), unpack=True)

    cov_diag = np.diag(err_hz ** 2)
    imf_frac = np.interp(z, zmod, imf) / 100.0
    ooo_frac = np.interp(z, zmod, spsooo) / 100.0
    cov_imf = np.outer(hz * imf_frac, hz * imf_frac)
    cov_ooo = np.outer(hz * ooo_frac, hz * ooo_frac)
    cov = cov_diag + cov_imf + cov_ooo
    return z, hz, cov


def main() -> None:
    z, hz, cov = build_covariance()
    n = len(z)
    assert cov.shape == (n, n)
    assert np.allclose(cov, cov.T), "covariance must be symmetric"
    eig_min = np.linalg.eigvalsh(cov).min()
    assert eig_min > 0, f"covariance must be positive-definite (min eig {eig_min})"

    mean_path = DATA_DIR / "mean.txt"
    cov_path = DATA_DIR / "cov.txt"
    with mean_path.open("w") as fh:
        fh.write("# z  H(z)[km/s/Mpc]  quantity\n")
        for zi, hi in zip(z, hz):
            fh.write(f"{zi:.4f} {hi:.4f} Hz\n")
    # %.10e keeps full double precision so the committed sha256 is reproducible.
    np.savetxt(cov_path, cov, fmt="%.10e")
    print(f"wrote {mean_path} ({n} points) and {cov_path} ({n}x{n}); min eig {eig_min:.3e}")


if __name__ == "__main__":
    main()

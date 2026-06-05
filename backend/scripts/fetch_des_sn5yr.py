"""Fetch the DES-SN5YR (Vincenzi et al. 2024 Legacy) supernova Hubble diagram and
build the sha256-pinned data bundle the in-process χ² reads.

Source: github.com/des-science/DES-SN5YR, release tag `1.3`
("Vincenzi et al. 2024 Legacy Release"), folder 4_DISTANCES_COVMAT:
  * DES-SN5YR_HD.csv      — 1829 SNe: zHD, zHEL, MU, MUERR_FINAL
  * STAT+SYS.txt.gz       — first line N, then N×N SYSTEMATIC covariance (row-major)

Per the release README and the official 5_COSMOLOGY/SN_only_cosmosis_likelihood.py,
the TOTAL covariance is

    C_total = C_sys (STAT+SYS.txt.gz)  +  diag(MUERR_FINAL²)

because STAT+SYS.txt.gz holds only the systematic part — the statistical
uncertainty lives in MUERR_FINAL (STATONLY.txt.gz is all zeros). The likelihood
analytically marginalizes the SN absolute-magnitude offset M, so no nuisance
parameter is stored.

Output (sha256-pinned in the cosmology registry; what the χ² reads):
  data/cosmology/../../data/des_sn5yr/data.npz  with keys:
    z_hd, z_hel, mu, mu_err_diag, cov   (cov is the full C_total, 1829×1829)

Re-run:  python scripts/fetch_des_sn5yr.py
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import pathlib
import urllib.request

import numpy as np

TAG = "1.3"
BASE = f"https://raw.githubusercontent.com/des-science/DES-SN5YR/{TAG}/4_DISTANCES_COVMAT"
HD_URL = f"{BASE}/DES-SN5YR_HD.csv"
COV_URL = f"{BASE}/STAT%2BSYS.txt.gz"

OUT_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "des_sn5yr"


def _download(url: str) -> bytes:
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=300) as resp:
        return resp.read()


def main() -> None:
    hd_bytes = _download(HD_URL)
    cov_bytes = _download(COV_URL)

    rows = list(csv.DictReader(io.StringIO(hd_bytes.decode())))
    z_hd = np.array([float(r["zHD"]) for r in rows], dtype=np.float64)
    z_hel = np.array([float(r["zHEL"]) for r in rows], dtype=np.float64)
    mu = np.array([float(r["MU"]) for r in rows], dtype=np.float64)
    mu_err = np.array([float(r["MUERR_FINAL"]) for r in rows], dtype=np.float64)
    n = len(rows)

    with gzip.open(io.BytesIO(cov_bytes), "rt") as f:
        declared = int(f.readline())
        values = np.array([float(x) for x in f.read().split()], dtype=np.float64)
    if declared != n or values.size != n * n:
        raise ValueError(f"covariance/HD mismatch: HD n={n}, cov header={declared}, n_values={values.size}")
    c_sys = values.reshape(n, n)
    cov = c_sys + np.diag(mu_err ** 2)  # C_total per the official likelihood

    if not np.allclose(cov, cov.T):
        raise ValueError("total covariance is not symmetric")
    eig_min = float(np.linalg.eigvalsh(cov).min())
    if eig_min <= 0:
        raise ValueError(f"total covariance is not positive-definite (min eig {eig_min})")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "data.npz"
    np.savez_compressed(
        out, z_hd=z_hd, z_hel=z_hel, mu=mu, mu_err_diag=mu_err, cov=cov,
    )
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"wrote {out} ({n} SNe, cov {n}×{n}, min eig {eig_min:.3e})")
    print(f"sha256: {digest}")


if __name__ == "__main__":
    main()

"""Fetch the SDSS BOSS DR12 consensus BAO mean vector + covariance and vendor
them, byte-for-byte, into the sha256-pinned location the in-process χ² reads.

This is the BAO-only consensus likelihood of Alam et al. 2017 (arXiv:1607.03155)
— the "+BAO" column behind the Planck 2018 parameter tables.

Source: github.com/CobayaSampler/bao_data (repo root):
  * sdss_DR12Consensus_bao.dat — 6 rows: z, value, quantity for
    DM_over_rs / bao_Hz_rs at z = 0.38, 0.51, 0.61. NOTE the convention:
    unlike the dimensionless eBOSS DR16 FSBAO files, these values are
    DIMENSIONAL — D_M(z)·(rs_fid/r_d) in Mpc and H(z)·(r_d/rs_fid) in
    km/s/Mpc, with rs_fid = 147.78 Mpc (cobaya bao.sdss_dr12_consensus_bao).
  * BAO_consensus_covtot_dM_Hz.txt — 6×6 ASCII covariance matrix.

The registry (cosmology_likelihoods.py, entry "sdss_dr12_consensus_bao") pins
the sha256 of these UPSTREAM raw files, so the vendored copies must be
byte-identical to what is downloaded — we write the raw bytes verbatim (no
re-serialisation) and assert the digests match the pins. A mismatch means
upstream changed or the pin is wrong; the script aborts rather than vendoring
unverified data.

Output (what the χ² reads):
  data/cosmology/sdss_dr12_consensus_bao/mean.txt
  data/cosmology/sdss_dr12_consensus_bao/cov.txt

Re-run:  python scripts/fetch_sdss_dr12_consensus_bao.py
"""
from __future__ import annotations

import hashlib
import pathlib
import urllib.request

import numpy as np

BASE = "https://raw.githubusercontent.com/CobayaSampler/bao_data/master"
MEAN_URL = f"{BASE}/sdss_DR12Consensus_bao.dat"
COV_URL = f"{BASE}/BAO_consensus_covtot_dM_Hz.txt"

# Pins copied from the registry entry "sdss_dr12_consensus_bao" data_products.
MEAN_SHA256 = "fc43f1cd9c815bb58b09f4d8d1d272d2c4ec57e05e4893e2121c20dc08f4f862"
COV_SHA256 = "05c04829c8edc117870efe809494593a23de6c35547f8b66760a5250804b65cf"

OUT_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "data"
    / "cosmology"
    / "sdss_dr12_consensus_bao"
)


def _download(url: str) -> bytes:
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read()


def main() -> None:
    mean_bytes = _download(MEAN_URL)
    cov_bytes = _download(COV_URL)

    mean_digest = hashlib.sha256(mean_bytes).hexdigest()
    cov_digest = hashlib.sha256(cov_bytes).hexdigest()
    print(f"mean sha256: {mean_digest}  (pin {MEAN_SHA256})")
    print(f"cov  sha256: {cov_digest}  (pin {COV_SHA256})")
    if mean_digest != MEAN_SHA256 or cov_digest != COV_SHA256:
        raise SystemExit(
            "ABORT: downloaded bytes do not match the registry-pinned sha256 — "
            "upstream changed or the pin is wrong. Not vendoring unverified data."
        )

    # Sanity-parse (does NOT alter the bytes we vendor): 6 rows z/value/quantity,
    # 6×6 covariance, the expected redshifts and observable tags.
    rows = [
        ln.split()
        for ln in mean_bytes.decode().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    n = len(rows)
    cov = np.array(
        [
            [float(x) for x in ln.split()]
            for ln in cov_bytes.decode().splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
    )
    assert n == 6, f"expected 6 measurement rows, got {n}"
    assert cov.shape == (6, 6), f"expected 6x6 covariance, got {cov.shape}"
    assert sorted({r[0] for r in rows}) == ["0.38", "0.51", "0.61"], rows
    assert {r[2] for r in rows} == {"DM_over_rs", "bao_Hz_rs"}, rows
    assert np.allclose(cov, cov.T), "covariance must be symmetric"
    assert np.all(np.linalg.eigvalsh(cov) > 0), "covariance must be positive definite"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "mean.txt").write_bytes(mean_bytes)
    (OUT_DIR / "cov.txt").write_bytes(cov_bytes)
    print(f"vendored to {OUT_DIR}")


if __name__ == "__main__":
    main()

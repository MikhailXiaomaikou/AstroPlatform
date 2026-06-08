"""Fetch the DESI DR2 (2025) combined BAO Gaussian mean vector + covariance and
vendor them, byte-for-byte, into the sha256-pinned location the in-process χ² reads.

Source: github.com/CobayaSampler/bao_data, folder desi_bao_dr2:
  * desi_gaussian_bao_ALL_GCcomb_mean.txt — 13 rows: z, value, quantity
    (DM_over_rs / DH_over_rs / DV_over_rs for BGS/LRG/ELG/QSO/LyA bins)
  * desi_gaussian_bao_ALL_GCcomb_cov.txt  — 13×13 ASCII covariance matrix

The registry (cosmology_likelihoods.py, entry "desi_dr2_bao") already pins the
sha256 of these UPSTREAM raw files, so the vendored copies must be byte-identical
to what is downloaded — we write the raw bytes verbatim (no re-serialisation) and
assert the digests match the pins. A mismatch means upstream changed or the pin is
wrong; the script aborts rather than vendoring unverified data.

Output (what the χ² reads):
  data/cosmology/desi_dr2_bao/mean.txt
  data/cosmology/desi_dr2_bao/cov.txt

Re-run:  python scripts/fetch_desi_dr2_bao.py
"""
from __future__ import annotations

import hashlib
import pathlib
import urllib.request

import numpy as np

BASE = (
    "https://raw.githubusercontent.com/CobayaSampler/bao_data/master/desi_bao_dr2"
)
MEAN_URL = f"{BASE}/desi_gaussian_bao_ALL_GCcomb_mean.txt"
COV_URL = f"{BASE}/desi_gaussian_bao_ALL_GCcomb_cov.txt"

# Pins copied from the registry entry "desi_dr2_bao" data_products.
MEAN_SHA256 = "9ac154ab583ce759c0f7eef3c978c7c70a6ead2d18774caceadf1a350a640585"
COV_SHA256 = "252a143274c8a07c78694c119617d36594f6d7965d00319ca611c6ffb886e509"

OUT_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "cosmology" / "desi_dr2_bao"


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

    # Sanity-parse (does NOT alter the bytes we vendor): 13 rows z/value/quantity,
    # 13×13 covariance.
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
        ],
        dtype=np.float64,
    )
    if n != 13 or cov.shape != (13, 13):
        raise SystemExit(f"ABORT: unexpected shape — {n} mean rows, cov {cov.shape} (expected 13 / 13×13)")
    eig_min = float(np.linalg.eigvalsh(cov).min())
    if eig_min <= 0:
        raise SystemExit(f"ABORT: covariance not positive-definite (min eig {eig_min})")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "mean.txt").write_bytes(mean_bytes)  # verbatim → digest matches pin
    (OUT_DIR / "cov.txt").write_bytes(cov_bytes)
    quantities = sorted({r[2] for r in rows})
    print(f"vendored {OUT_DIR} ({n} rows, cov 13×13, min eig {eig_min:.3e}, quantities {quantities})")


if __name__ == "__main__":
    main()

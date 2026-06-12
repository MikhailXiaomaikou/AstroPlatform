"""Fetch the Pantheon (2018) 1048-SN sample and vendor it, byte-for-byte, into
the sha256-pinned location the in-process χ² reads.

Scolnic et al. 2018 (arXiv:1710.00845) — the SN anchor of the 2018-2022
literature era (the Planck 2018 / DES-Y1 / eBOSS companion analyses all quote
it). Source: github.com/CobayaSampler/sn_data, folder Pantheon (the same files
cobaya's sn.pantheon reads):

  * lcparam_full_long_zhel.txt — 1048 rows: name zcmb zhel dz mb dmb …
  * sys_full_long.txt — JLA-format systematic covariance (first token 1048,
    then 1048² values, ~12 MB)

The likelihood convention (cobaya sn.pantheon, use_abs_mag=False, pecz=0,
intrinsicdisp=0): C_total = C_sys + diag(dmb²), absolute magnitude analytically
marginalized — so it constrains Ωm (+w0/wa), never H0.

Output (what the χ² reads):
  data/cosmology/pantheon18/lcparam_full_long_zhel.txt
  data/cosmology/pantheon18/sys_full_long.txt

Re-run:  python scripts/fetch_pantheon18.py
"""
from __future__ import annotations

import hashlib
import pathlib
import urllib.request

import numpy as np

BASE = "https://raw.githubusercontent.com/CobayaSampler/sn_data/master/Pantheon"
LCPARAM_URL = f"{BASE}/lcparam_full_long_zhel.txt"
SYS_URL = f"{BASE}/sys_full_long.txt"

# Pins copied from the registry entry "pantheon18" data_products.
LCPARAM_SHA256 = "4e865e819eda499530b04da6965ab7aac0407878789b105732cb1f9b99a64323"
SYS_SHA256 = "0ec3388b984a708f27bcedf7171c8a3e74621aca73dabb41a21246e9ae3fb53d"

OUT_DIR = (
    pathlib.Path(__file__).resolve().parents[1] / "data" / "cosmology" / "pantheon18"
)


def _download(url: str) -> bytes:
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=300) as resp:
        return resp.read()


def main() -> None:
    lc_bytes = _download(LCPARAM_URL)
    sys_bytes = _download(SYS_URL)

    lc_digest = hashlib.sha256(lc_bytes).hexdigest()
    sys_digest = hashlib.sha256(sys_bytes).hexdigest()
    print(f"lcparam sha256: {lc_digest}  (pin {LCPARAM_SHA256})")
    print(f"sys     sha256: {sys_digest}  (pin {SYS_SHA256})")
    if lc_digest != LCPARAM_SHA256 or sys_digest != SYS_SHA256:
        raise SystemExit(
            "ABORT: downloaded bytes do not match the registry-pinned sha256 — "
            "upstream changed or the pin is wrong. Not vendoring unverified data."
        )

    # Sanity-parse (does NOT alter the vendored bytes).
    rows = [
        ln.split()
        for ln in lc_bytes.decode().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert len(rows) == 1048, f"expected 1048 SNe, got {len(rows)}"
    zcmb = np.array([float(r[1]) for r in rows])
    assert 0.005 < zcmb.min() < 0.02 and 2.0 < zcmb.max() < 2.5, (zcmb.min(), zcmb.max())
    sys_tokens = sys_bytes.decode().split()
    n = int(float(sys_tokens[0]))
    assert n == 1048 and len(sys_tokens) == 1 + n * n, (n, len(sys_tokens))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "lcparam_full_long_zhel.txt").write_bytes(lc_bytes)
    (OUT_DIR / "sys_full_long.txt").write_bytes(sys_bytes)
    print(f"vendored to {OUT_DIR}")


if __name__ == "__main__":
    main()

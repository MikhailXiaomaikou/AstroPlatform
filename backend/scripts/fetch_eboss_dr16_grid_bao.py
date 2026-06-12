"""Fetch the eBOSS DR16 non-Gaussian BAO likelihood grids and vendor them,
byte-for-byte, into the sha256-pinned locations the in-process χ² reads.

Three released likelihood surfaces from the SDSS DR16 official suite
(Alam et al. 2021, arXiv:2007.08991), github.com/CobayaSampler/bao_data:

  * sdss_DR16_ELG_BAO_DVtable.txt — 399×2: (D_V/r_d, probability density)
    at z=0.845 (de Mattia et al. 2021, arXiv:2007.09008). 1D non-Gaussian —
    the ELG posterior is visibly skewed, which is why the release ships a
    table instead of a Gaussian.
  * sdss_DR16_LYAUTO_BAO_DMDHgrid.txt — 50×50 grid: (D_M/r_d, D_H/r_d,
    likelihood ratio) at z=2.334, Lyα auto-correlation
    (du Mas des Bourboux et al. 2020, arXiv:2007.08995).
  * sdss_DR16_LYxQSO_BAO_DMDHgrid.txt — 50×50 grid, Lyα×QSO cross.

All values are DIMENSIONLESS distance ratios (no rs_fid convention — unlike
the DR12 consensus files). Grid maxima sit at the published best fits
(ELG D_V/r_d=18.33; LYAUTO 37.76/8.92; LYxQSO 37.44/9.06) — asserted below.

Output (what the χ² reads):
  data/cosmology/eboss_dr16_elg_bao/grid.txt
  data/cosmology/eboss_dr16_lyauto_bao/grid.txt
  data/cosmology/eboss_dr16_lyxqso_bao/grid.txt

Re-run:  python scripts/fetch_eboss_dr16_grid_bao.py
"""
from __future__ import annotations

import hashlib
import io
import pathlib
import urllib.request

import numpy as np

BASE = "https://raw.githubusercontent.com/CobayaSampler/bao_data/master"
DATA_ROOT = pathlib.Path(__file__).resolve().parents[1] / "data" / "cosmology"

# (upstream file, registry dataset key, pinned sha256)
TARGETS = (
    (
        "sdss_DR16_ELG_BAO_DVtable.txt",
        "eboss_dr16_elg_bao",
        "ebbd6b7a2946cf1903bac9e699702e6aa57a631799bb70421c8e7a55cb3d2c1f",
    ),
    (
        "sdss_DR16_LYAUTO_BAO_DMDHgrid.txt",
        "eboss_dr16_lyauto_bao",
        "40cee3a1c9dc58616ba7151ab9d020b0014238249409cd1ace71af14674e37e0",
    ),
    (
        "sdss_DR16_LYxQSO_BAO_DMDHgrid.txt",
        "eboss_dr16_lyxqso_bao",
        "653e2cea43a742d12090e9b7eacaf74dc7af7d7f6153a1a4c696d6303a7fb952",
    ),
)


def _download(url: str) -> bytes:
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read()


def main() -> None:
    for upstream, key, pin in TARGETS:
        raw = _download(f"{BASE}/{upstream}")
        digest = hashlib.sha256(raw).hexdigest()
        print(f"{upstream}: sha256 {digest}  (pin {pin})")
        if digest != pin:
            raise SystemExit(
                f"ABORT: {upstream} does not match the registry-pinned sha256 — "
                "upstream changed or the pin is wrong. Not vendoring unverified data."
            )
        # Sanity-parse (does NOT alter the vendored bytes).
        grid = np.loadtxt(io.BytesIO(raw))
        if key == "eboss_dr16_elg_bao":
            assert grid.shape == (399, 2), grid.shape
            assert grid[:, 1].min() > 0, "probability column must be positive (log taken)"
            assert abs(grid[np.argmax(grid[:, 1]), 0] - 18.33) < 0.05, "ELG peak off published D_V/r_d"
        else:
            assert grid.shape == (2500, 3), grid.shape
            assert len(np.unique(grid[:, 0])) == 50 and len(np.unique(grid[:, 1])) == 50
            assert grid[:, 2].min() > 0, "likelihood column must be positive (log taken)"
            peak = grid[np.argmax(grid[:, 2])]
            assert 36.0 < peak[0] < 39.0 and 8.0 < peak[1] < 10.0, peak
        out_dir = DATA_ROOT / key
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "grid.txt").write_bytes(raw)
        print(f"vendored to {out_dir / 'grid.txt'}")


if __name__ == "__main__":
    main()

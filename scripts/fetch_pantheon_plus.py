"""Fetch the Pantheon+SH0ES 2022 data release and save as a compact npz.

Pantheon+SH0ES (Brout+ 2022, ApJ 938, 110; Scolnic+ 2022, ApJ 938, 113)
provides:
  - 1701-row table including m_b_corr, IS_CALIBRATOR, and CEPH_DIST
  - 1701x1701 stat+sys covariance matrix (ASCII packed)

Source: https://github.com/PantheonPlusSH0ES/DataRelease
Both files are public, ~150 KB table + ~58 MB ASCII cov → ~23 MB npz.

This script runs once locally; the resulting backend/data/pantheon_plus_2022/
data.npz is checked into the repo (small enough) so prod Render deploys
don't depend on GitHub-raw fetch reliability.

Usage:
    python scripts/fetch_pantheon_plus.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "backend" / "data" / "pantheon_plus_2022"
OUT_DIR.mkdir(parents=True, exist_ok=True)

DATA_RELEASE_COMMIT = "c447f0fea703fcd0fff57de5000947b5ca81286b"

TABLE_URL = (
    f"https://raw.githubusercontent.com/PantheonPlusSH0ES/DataRelease/{DATA_RELEASE_COMMIT}/"
    "Pantheon%2B_Data/4_DISTANCES_AND_COVAR/Pantheon%2BSH0ES.dat"
)
COV_URL = (
    f"https://raw.githubusercontent.com/PantheonPlusSH0ES/DataRelease/{DATA_RELEASE_COMMIT}/"
    "Pantheon%2B_Data/4_DISTANCES_AND_COVAR/Pantheon%2BSH0ES_STAT%2BSYS.cov"
)


def fetch(url: str) -> str:
    print(f"  GET {url}")
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.text


def parse_table(
    text: str,
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
]:
    """Parse the Pantheon+SH0ES.dat table.

    First line is a space-separated header. Subsequent lines are SNe.
    Returns ``(cid, zHD, zHEL, mu, mu_err_diag, m_b_corr,
    is_calibrator, cepheid_distance)``.  The last three fields are required by
    the official joint Pantheon+SH0ES likelihood: the selection is
    ``(zHD > 0.01) | IS_CALIBRATOR`` and calibrators use ``CEPH_DIST`` as their
    theory distance.  A MU_SH0ES-only bundle cannot identify H0 correctly.

    Stage 4 (2026-05-19): added `cid` (SN name, e.g. "2011fe") for the
    literature_spot_check module. Same SN can appear in multiple rows
    (different IDSURVEY), so the spot-check loader handles inverse-variance
    weighted aggregation downstream.
    """
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    header = lines[0].split()
    rows = [ln.split() for ln in lines[1:]]
    idx = {col: header.index(col) for col in header}
    cid = np.array([r[idx["CID"]] for r in rows], dtype="U32")
    z_hd = np.array([float(r[idx["zHD"]]) for r in rows])
    z_hel = np.array([float(r[idx["zHEL"]]) for r in rows])
    mu = np.array([float(r[idx["MU_SH0ES"]]) for r in rows])
    mu_err = np.array([float(r[idx["MU_SH0ES_ERR_DIAG"]]) for r in rows])
    m_b_corr = np.array([float(r[idx["m_b_corr"]]) for r in rows])
    is_calibrator = np.array([int(r[idx["IS_CALIBRATOR"]]) for r in rows], dtype=bool)
    cepheid_distance = np.array([float(r[idx["CEPH_DIST"]]) for r in rows])
    return (
        cid,
        z_hd,
        z_hel,
        mu,
        mu_err,
        m_b_corr,
        is_calibrator,
        cepheid_distance,
    )


def parse_covariance(text: str, n: int) -> np.ndarray:
    """Parse the Pantheon+SH0ES_STAT+SYS.cov ASCII covariance.

    Format: first line is `n` (= 1701), followed by n*n floats, one per line,
    packed row-major.
    """
    tokens = text.split()
    declared_n = int(tokens[0])
    if declared_n != n:
        raise ValueError(f"covariance N mismatch: file says {declared_n}, table has {n}")
    flat = np.array([float(t) for t in tokens[1:]], dtype=np.float64)
    if flat.size != n * n:
        raise ValueError(f"covariance has {flat.size} entries, expected {n*n}")
    return flat.reshape(n, n)


def main() -> None:
    print("Fetching Pantheon+SH0ES (2022 data release)...")
    table_text = fetch(TABLE_URL)
    cov_text = fetch(COV_URL)

    (
        cid,
        z_hd,
        z_hel,
        mu,
        mu_err,
        m_b_corr,
        is_calibrator,
        cepheid_distance,
    ) = parse_table(table_text)
    n = z_hd.size
    print(f"  Parsed table: {n} SNe")
    if n != 1701:
        print(f"  WARNING: expected 1701 SNe, got {n}", file=sys.stderr)
    unique_cids = len(set(cid.tolist()))
    print(f"  Unique CIDs: {unique_cids} (some SNe appear in multiple surveys)")
    selection = (z_hd > 0.01) | is_calibrator
    print(
        "  Official SH0ES selection: "
        f"{int(np.count_nonzero(selection))} rows, "
        f"{int(np.count_nonzero(is_calibrator & selection))} calibrators"
    )
    if not np.all(np.isfinite(cepheid_distance[is_calibrator & selection])):
        raise ValueError("selected calibrator CEPH_DIST contains non-finite values")

    cov = parse_covariance(cov_text, n)
    print(f"  Parsed covariance: {cov.shape}")

    # Verify covariance is symmetric and PSD
    asym = np.max(np.abs(cov - cov.T))
    print(f"  Covariance asymmetry: {asym:.2e}")
    diag_min = np.min(np.diag(cov))
    diag_max = np.max(np.diag(cov))
    print(f"  Covariance diagonal: [{diag_min:.4f}, {diag_max:.4f}]")

    out_path = OUT_DIR / "data.npz"
    np.savez_compressed(
        out_path,
        cid=cid,
        z_hd=z_hd,
        z_hel=z_hel,
        mu=mu,
        mu_err_diag=mu_err,
        m_b_corr=m_b_corr,
        is_calibrator=is_calibrator,
        cepheid_distance=cepheid_distance,
        cov=cov,
    )
    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"\nWrote {out_path}  ({size_mb:.2f} MB)")
    print(f"Decision: {'commit to repo' if size_mb < 30 else 'gitignored, fetch on startup'}")


if __name__ == "__main__":
    main()

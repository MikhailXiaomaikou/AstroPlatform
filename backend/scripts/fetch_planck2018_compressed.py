#!/usr/bin/env python3
"""Compute a Planck 2018 posterior proposal/context covariance.

Source: Planck 2018 base ΛCDM, plikHM TT,TE,EE+lowl+lowE (no lensing), from the
IRSA mirror of the Planck Legacy Archive cosmoparams release R3.00. The +lensing
baseline is only published inside the ~9 GB full grid, so this script uses the
standalone TT,TE,EE+lowl+lowE chains (≈0.1σ from the +lensing column; relabel the
registry entry accordingly).

Why (H0, Ωm, σ8) and NOT S8: S8 ≡ σ8·(Ωm/0.3)^0.5 is exactly determined by σ8
and Ωm, so a joint Gaussian that also lists S8 is rank-deficient. More
importantly, these are posterior-chain summaries that inherit the source model
and priors: this artifact is for proposal design/literature context only and
must never be multiplied as a likelihood. The executable compressed CMB target
is the separately encoded CHW2019 distance prior.

Output (committed, small): backend/data/planck2018_compressed/posterior_proposal.json
    {
      "parameters": ["H0", "omegam", "sigma8"],
      "mean": [...],
      "covariance": [[...], [...], [...]],
      "correlation": [[...], ...],          # for human inspection
      "provenance": {chain_root, getdist_version, n_samples, source_url, generated}
    }

Usage:
    python scripts/fetch_planck2018_compressed.py                 # download from IRSA
    python scripts/fetch_planck2018_compressed.py --chains-zip /path/to.zip
    python scripts/fetch_planck2018_compressed.py --chains-root /path/to/base_plikHM_TTTEEE_lowl_lowE

Requires: getdist, numpy (+ network to IRSA unless --chains-zip / --chains-root given).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sys
import tempfile
import zipfile

import numpy as np

SOURCE_URL = (
    "https://irsa.ipac.caltech.edu/data/Planck/release_3/ancillary-data/"
    "cosmoparams/COM_CosmoParams_base-plikHM-TTTEEE-lowl-lowE_R3.00.zip"
)
PARAMS = ["H0", "omegam", "sigma8"]
OUT_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "data" / "planck2018_compressed" / "posterior_proposal.json"
)


def _download(dest: pathlib.Path) -> None:
    import urllib.request

    print(f"Downloading {SOURCE_URL}\n  -> {dest} (~62 MB)")
    urllib.request.urlretrieve(SOURCE_URL, dest)


def _find_chain_root(search_dir: pathlib.Path) -> pathlib.Path:
    """Locate the getdist chain root (the path passed to loadMCSamples).

    getdist expects ``<root>.paramnames`` + ``<root>_1.txt`` etc. We find the
    .paramnames for the TTTEEE+lowl+lowE base model and strip the suffix.
    """
    candidates = sorted(search_dir.rglob("*TTTEEE*lowl*lowE*.paramnames"))
    if not candidates:
        candidates = sorted(search_dir.rglob("*.paramnames"))
    if not candidates:
        raise FileNotFoundError(f"no .paramnames under {search_dir}")
    pn = candidates[0]
    return pn.with_suffix("")  # strip .paramnames -> chain root


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chains-zip", type=pathlib.Path, default=None,
                    help="Use an already-downloaded chains zip instead of fetching.")
    ap.add_argument("--chains-root", type=pathlib.Path, default=None,
                    help="Use an already-extracted getdist chain root directly.")
    args = ap.parse_args()

    try:
        from getdist import loadMCSamples
        import getdist
    except ImportError:
        print("ERROR: getdist is required (pip install getdist).", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = pathlib.Path(tmp)
        if args.chains_root is not None:
            root = args.chains_root
        else:
            zip_path = args.chains_zip
            if zip_path is None:
                zip_path = tmp_dir / "chains.zip"
                _download(zip_path)
            print(f"Extracting {zip_path} ...")
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmp_dir)
            root = _find_chain_root(tmp_dir)

        print(f"Loading getdist chains from root: {root}")
        samples = loadMCSamples(str(root))

        # Resolve the actual getdist parameter names (defensive: Planck uses
        # exactly these, but fail loudly if a release renames them).
        available = {p.name for p in samples.paramNames.names}
        missing = [p for p in PARAMS if p not in available]
        if missing:
            raise KeyError(
                f"params {missing} not in chain; available sample: "
                f"{sorted(available)[:20]}"
            )

        idx = [samples.paramNames.numberOfName(p) for p in PARAMS]
        mean = np.asarray(samples.mean(idx), dtype=float)
        cov = np.asarray(samples.cov(idx), dtype=float)
        std = np.sqrt(np.diag(cov))
        corr = cov / np.outer(std, std)

        # Sanity: covariance must be symmetric positive-definite.
        np.linalg.cholesky(cov)

        payload = {
            "parameters": PARAMS,
            "mean": [round(float(x), 6) for x in mean],
            "covariance": [[float(c) for c in row] for row in cov],
            "correlation": [[round(float(c), 4) for c in row] for row in corr],
            "provenance": {
                "description": (
                    "Planck 2018 base LCDM, plikHM TT,TE,EE+lowl+lowE (no lensing). "
                    "Mean + non-diagonal covariance of derived params (H0, Omega_m, "
                    "sigma8) computed from the public MCMC chains via getdist."
                ),
                "chain_root": pathlib.Path(root).name,
                "source_url": SOURCE_URL,
                "getdist_version": getdist.__version__,
                "n_samples": int(samples.numrows),
                "bibcode": "2020A&A...641A...6P",
                "generated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            },
        }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {OUT_PATH}")
    print(f"  mean (H0, Omega_m, sigma8) = {payload['mean']}")
    print(f"  1-sigma                    = {[round(float(s), 4) for s in std]}")
    print("  correlation matrix:")
    for row in payload["correlation"]:
        print("   ", row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

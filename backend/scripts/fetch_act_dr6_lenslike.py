#!/usr/bin/env python3
"""Fetch the official ACT DR6 lensing likelihood data tarball (NASA LAMBDA)
and vendor, byte-for-byte, the act_baseline lens_only file subset that
act_dr6_lenslike.load_data() reads — plus the package's fiducial test spectra
(the theory input of the chi2 = 14.06 reference anchor).

Source (v1.2, 2024-02-01, 361,306,879 bytes — NOT vendored whole):
  https://lambda.gsfc.nasa.gov/data/suborbital/ACT/ACT_dr6/likelihood/data/ACT_dr6_likelihood_v1.2.tgz

Verification chain (abort on ANY mismatch — never vendor unverified data):
  1. the downloaded tarball must match TARBALL_SHA256;
  2. every extracted member must match its per-file pin in FILE_SHA256
     (the same pins carried by the registry entry "act_dr6_lensing"
     data_products and enforced at load time by
     cosmology_likelihoods.cmb.load_verified_act_dr6_lenslike_data).

Output layout follows cobaya's InstallableLikelihood get_path convention so a
future cobaya_runner wiring resolves it through the existing packages_path:
  data/cobaya_packages/data/ACT_dr6_likelihood/v1.2/<files>

Re-run:  python scripts/fetch_act_dr6_lenslike.py
         python scripts/fetch_act_dr6_lenslike.py --tarball /path/to.tgz
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import shutil
import sys
import tarfile
import tempfile
import urllib.request

import numpy as np

URL = (
    "https://lambda.gsfc.nasa.gov/data/suborbital/ACT/ACT_dr6/likelihood/"
    "data/ACT_dr6_likelihood_v1.2.tgz"
)
TARBALL_SHA256 = "bbcde3bcacd7c9a97138c4873c8a1217635a18504d15c4f86b1fba39d3601085"
TARBALL_SIZE = 361306879

VERSION = "v1.2"
# tar member (relative to the archive root) -> pinned sha256.
FILE_SHA256: dict[str, str] = {
    f"{VERSION}/clkk_bandpowers_act.txt": "7660d216c48aa639dd374a6284b927a2821427fca8e98a3a7845da47c16806ae",
    f"{VERSION}/binning_matrix_act.txt": "a88fa3dc1ac5289e0580d8d0c1c3e9ee149f06cf62dad328cb5a248fd9985084",
    f"{VERSION}/covmat_act_cmbmarg.txt": "18ce4a7c542b7e23ecc17d492a8dbf748bf84a3bc30dc337a8999d9f4925c294",
    f"{VERSION}/covmat_act.txt": "e710817fc88e0321e6d2a8dc3805489ada0df3f3d930e6e341e4aa929a36f361",
    f"{VERSION}/like_corrs/cosmo2017_10K_acc3_lensedCls.dat": "8ca800b013145473f837a7e96d19a2c14972146c29e63fe7966a33f2bfeff47c",
    f"{VERSION}/like_corrs/cosmo2017_10K_acc3_lenspotentialCls.dat": "53d01931defba4cadda8781f9d6049cc56fea075295f58405812096abc2da9ae",
}

OUT_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "data" / "cobaya_packages" / "data" / "ACT_dr6_likelihood" / VERSION
)


def _download(dest: pathlib.Path) -> None:
    print(f"downloading {URL}\n  -> {dest} (~345 MB, may take a while)")
    with urllib.request.urlopen(URL, timeout=300) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out, length=1 << 20)


def _sha256_stream(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--tarball", type=pathlib.Path, default=None,
        help="Use an already-downloaded ACT_dr6_likelihood_v1.2.tgz instead of fetching.",
    )
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        tarball = args.tarball or (pathlib.Path(tmp) / "ACT_dr6_likelihood_v1.2.tgz")
        if args.tarball is None:
            _download(tarball)

        size = tarball.stat().st_size
        digest = _sha256_stream(tarball)
        print(f"tarball size {size} (expected {TARBALL_SIZE})")
        print(f"tarball sha256 {digest}\n        pinned {TARBALL_SHA256}")
        if size != TARBALL_SIZE or digest != TARBALL_SHA256:
            raise SystemExit(
                "ABORT: tarball does not match the pinned size/sha256 — upstream "
                "changed or the download is corrupt. Not vendoring unverified data."
            )

        extracted: dict[str, bytes] = {}
        with tarfile.open(tarball, "r:gz") as tf:
            for member, pin in FILE_SHA256.items():
                try:
                    fh = tf.extractfile(member)
                except KeyError:
                    fh = None
                if fh is None:
                    raise SystemExit(f"ABORT: member {member!r} missing from tarball")
                data = fh.read()
                got = hashlib.sha256(data).hexdigest()
                if got != pin:
                    raise SystemExit(
                        f"ABORT: {member} sha256 {got} != pinned {pin} — not vendoring."
                    )
                extracted[member] = data
                print(f"verified {member} ({len(data)} bytes)")

    # Sanity-parse (does NOT alter the vendored bytes): bandpowers vector,
    # square positive-definite covariances of matching size, binning matrix
    # row count matching the full bandpower count.
    band = np.loadtxt(
        [ln for ln in extracted[f"{VERSION}/clkk_bandpowers_act.txt"].decode().splitlines()]
    )
    nbins = band.size
    cov = np.loadtxt(extracted[f"{VERSION}/covmat_act.txt"].decode().splitlines())
    cov_marg = np.loadtxt(extracted[f"{VERSION}/covmat_act_cmbmarg.txt"].decode().splitlines())
    binmat = np.loadtxt(extracted[f"{VERSION}/binning_matrix_act.txt"].decode().splitlines())
    if cov.shape != (nbins, nbins) or cov_marg.shape != (nbins, nbins):
        raise SystemExit(
            f"ABORT: covariance shapes {cov.shape}/{cov_marg.shape} do not match "
            f"{nbins} bandpowers"
        )
    if binmat.shape[0] != nbins:
        raise SystemExit(
            f"ABORT: binning matrix rows {binmat.shape[0]} != {nbins} bandpowers"
        )
    for name, mat in (("covmat_act", cov), ("covmat_act_cmbmarg", cov_marg)):
        eig_min = float(np.linalg.eigvalsh(mat).min())
        if eig_min <= 0:
            raise SystemExit(f"ABORT: {name} not positive-definite (min eig {eig_min})")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for member, data in extracted.items():
        rel = pathlib.PurePosixPath(member).relative_to(VERSION)
        dest = OUT_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)  # verbatim -> digest matches pin
        print(f"vendored {dest}")
    print(
        f"done: {nbins} bandpowers, cov {cov.shape}, binmat {binmat.shape}, "
        f"under {OUT_DIR}"
    )


if __name__ == "__main__":
    sys.exit(main())

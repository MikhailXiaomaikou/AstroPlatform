"""Fetch & vendor the Planck 2018 plik_lite high-l TT/TE/EE *native* likelihood
data — the foreground-marginalized binned CMB bandpowers + covariance the
in-platform external-cobaya CMB likelihood reads.

Source: cobaya's bundled native plik_lite (CobayaSampler/planck_native_data,
plik_lite_2018_AL.zip), i.e. the Planck 2018 plik_lite release (Aghanim et al.
2020, "Planck 2018 results. V. CMB power spectra and likelihoods",
arXiv:1907.12875 / A&A 641 A5). The `_native` likelihood is PURE PYTHON — NO
clik / no Planck Likelihood Code needed — so the data is just a handful of ASCII
+ binary tables (~3 MB), small enough to vendor byte-for-byte and sha256-pin.

What it does:
  1. `cobaya-install planck_2018_highl_plik.TTTEEE_lite_native` into a temp
     packages dir (downloads + decompresses the official zip).
  2. Copies the data files into the committed cobaya-packages layout under
     data/cobaya_packages/data/planck_2018_pliklite_native/.
  3. Prints sha256 of every file so the registry entry can pin them.

The platform points COBAYA_PACKAGES_PATH at data/cobaya_packages at runtime, so
the cobaya subprocess reads THIS vendored, checksum-pinned copy (no runtime
network, fully reproducible).

Re-run:  python scripts/fetch_planck_pliklite.py
"""
from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile

LIKELIHOOD = "planck_2018_highl_plik.TTTEEE_lite_native"
DATA_SUBDIR = pathlib.Path("data") / "planck_2018_pliklite_native"

BACKEND = pathlib.Path(__file__).resolve().parents[1]
VENDOR_ROOT = BACKEND / "data" / "cobaya_packages"  # = COBAYA_PACKAGES_PATH
OUT_DIR = VENDOR_ROOT / DATA_SUBDIR


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="pliklite_install_") as tmp:
        print(f"cobaya-install {LIKELIHOOD} -> {tmp}")
        subprocess.run(
            [sys.executable, "-m", "cobaya", "install", LIKELIHOOD, "--packages-path", tmp],
            check=True,
        )
        src = pathlib.Path(tmp) / DATA_SUBDIR
        if not src.is_dir():
            raise SystemExit(f"ABORT: expected installed data dir not found: {src}")

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for f in OUT_DIR.iterdir():  # clean re-vendor
            if f.is_file():
                f.unlink()
        files = sorted(p for p in src.iterdir() if p.is_file())
        for p in files:
            shutil.copy2(p, OUT_DIR / p.name)

    print(f"\nvendored {len(files)} files -> {OUT_DIR}")
    print("sha256 (pin the science files in the registry):")
    for name in sorted(p.name for p in OUT_DIR.iterdir() if p.is_file()):
        digest = hashlib.sha256((OUT_DIR / name).read_bytes()).hexdigest()
        print(f"  {digest}  {name}")


if __name__ == "__main__":
    main()

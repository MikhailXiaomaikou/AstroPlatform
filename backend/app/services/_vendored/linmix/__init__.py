"""Vendored copy of jmeyers314/linmix.

Original repo: https://github.com/jmeyers314/linmix
License:       BSD 2-Clause (see LICENSE in this directory)
Vendored on:   2026-04-26
Maintenance:   Upstream has not seen substantive changes in years; we
               re-vendor only on bug reports.

Reason for vendoring:  linmix is not published on PyPI, so neither
`pip install linmix` nor a pinned `git+https://...` reference works
reliably during Render builds.  Vendoring fixes both the installation
path (no git required in the runtime image) and the reproducibility
risk (no upstream commit drift).

Do not modify linmix.py here.  Patches/adapters live in
app/services/bayesian_inference.py (see kelly07_linmix_fit).
"""

from .linmix import LinMix

__all__ = ["LinMix"]

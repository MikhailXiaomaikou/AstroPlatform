# Vendored: linmix

- **Upstream**: https://github.com/jmeyers314/linmix
- **Files vendored**: `linmix/__init__.py`, `linmix/linmix.py`, `LICENSE`
- **Vendored on**: 2026-04-26
- **Original author**: Josh Meyers
- **License**: BSD 2-Clause (see `LICENSE` next to this file)

## What this is

A Python port of B. Kelly's `LINMIX_ERR` IDL package
(Kelly 2007, ApJ 665, 1489 — arXiv:0705.2774).  Hierarchical Bayesian
linear regression with Gaussian-mixture latent-variable prior, supporting
errors in both the independent and dependent variables.

## Why vendored, not pip

`linmix` is **not** published on PyPI.  Neither `pip install linmix`
nor a pinned `git+https://...` reference is robust enough for
production deploys (Render's `python:3.11-slim` runtime has no `git`,
and a `git+` URL would also create implicit upstream-drift risk).

Vendoring resolves both:
- The Render image builds without extra system packages.
- The exact code we ship is in our repo and in our git history.

## Maintenance policy

- **Do not edit `linmix.py` or `__init__.py` in place.**  We treat the
  vendored copy as immutable.  Adapters and integration code live in
  `backend/app/services/bayesian_inference.py` (function
  `kelly07_linmix_fit`).
- If a bug is found upstream, re-vendor by replacing the files
  wholesale and updating "Vendored on" above.
- Upstream has not seen substantive commits in years; re-vendoring is
  expected to be infrequent.

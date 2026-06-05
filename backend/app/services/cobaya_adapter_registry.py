"""Step 3 framework (2026-05-20): adapter-name -> cobaya import-path resolver.

`CosmologyDatasetEntry.cobaya_likelihood` strings registered in
`cosmology_likelihoods` are **adapter names** (`"external:bao.sdss_6df_legacy"`,
`"external:sn.pantheon_plus"`, ...), not real Cobaya import paths. Until a
mapping exists, `cobaya_runner` returns the structured
`cobaya_likelihood_id_translation_pending` envelope rather than attempting to
import a non-existent module.

This module owns that mapping. The dict is **intentionally all-None today**:
filling a real Cobaya import path is per-dataset research (which Cobaya core
likelihood vs which third-party `cobaya-likelihood-*` package, exact class
name, packages_path requirement, etc.). Each entry has a TODO comment with
the candidate path so a future PR can fill them one at a time without
guessing.

When `resolve(adapter_name)` returns a non-None string, `cobaya_runner` will
forward that import path to the `cobaya-run` subprocess. Until then, the
result envelope stays publication-blocking, preserving the zero-fabrication
contract.

Two prefix conventions are recognised:

* ``external:<adapter>``  — the dataset uses a Cobaya likelihood. Mapping
  must point to a real import path (Cobaya internal or third-party module).
* ``gaussian:H0=...,sigma=...`` — the dataset is a compressed Gaussian prior
  on a single parameter. Cobaya supports these via the `H0` family of
  built-in likelihoods or an ad-hoc `gaussian` likelihood definition; the
  resolver handles them inline (no dict lookup).
"""
from __future__ import annotations

from typing import Final


# Adapter-name -> real Cobaya import path.
#
# All values are None today (step 3 placeholder). To unlock a dataset:
#   1. Look up the actual Cobaya likelihood class/module name (Cobaya docs
#      or the third-party `cobaya-likelihood-*` package README).
#   2. Replace the None with the import path string, e.g.
#      "external:bao.sdss_6df_legacy": "bao.sixdf_2011",
#   3. Add a unit test in test_cobaya_adapter_registry.py asserting the
#      adapter resolves and (if possible) that the underlying module
#      imports without error in CI.
#   4. Bump EXTERNAL_COBAYA_ENABLED to true in Render env and verify
#      `evaluate` sampler runs end-to-end.
#
# Sources to consult for each TODO:
#   * https://cobaya.readthedocs.io/en/latest/likelihoods.html
#   * https://github.com/CobayaSampler (third-party likelihood plugins)
#   * The dataset's own paper / release notes for the canonical cobaya
#     class name (when authors provide one).
ADAPTER_TO_COBAYA: Final[dict[str, str | None]] = {
    # ── BAO ─────────────────────────────────────────────────────────────
    # TODO(step3): DESI DR1 BAO — likely shipped via the `cobaya-likelihood-desi`
    # third-party package. Candidate: `bao.desi_dr1_bao` or class in
    # `cobaya_likelihood_desi.dr1`.
    "external:desilike.desi_dr1_bao": None,
    # TODO(step3): DESI DR2 BAO (2025, arXiv:2503.14738) — same desilike path as
    # DR1, DR2 data vector. Candidate: `bao.desi_dr2_bao` or `cobaya_likelihood_desi.dr2`.
    "external:desilike.desi_dr2_bao": None,
    # TODO(step3): 6dFGS BAO (Beutler+ 2011). Candidate cobaya core:
    # `bao.sixdf_2011`. Verify against current Cobaya release.
    "external:bao.sdss_6df_legacy": None,
    # TODO(step3): eBOSS DR16 RSD (Alam+ 2021). Likely in
    # `cobaya-likelihood-sdss` third-party package.
    "external:rsd.eboss_dr16_alam21": None,
    # ── Supernovae ───────────────────────────────────────────────────────
    # TODO(step3): Pantheon+ SN. Candidate: `sn.pantheonplus` (Cobaya 3.5+)
    # or third-party `cobaya-likelihood-pantheonplus`.
    "external:sn.pantheon_plus": None,
    # TODO(step3): DES Y5 SN. Third-party `cobaya-likelihood-des-sn5yr`.
    "external:sn.des_sn5yr": None,
    # TODO(step3): Union3 SN compilation. Likely needs custom Cobaya class
    # because Union3 is published as `Union3/UNITY1.5` chains; no first-party
    # Cobaya likelihood today.
    "external:sn.union3": None,
    # ── CMB ──────────────────────────────────────────────────────────────
    # TODO(step3): Planck 2018 compressed-Gaussian distance prior. There is
    # no first-party Cobaya likelihood class for this; option A is to use
    # the Cobaya `gaussian_distance_prior` helper, option B is to write a
    # thin class inline. Until then, keep None and let the runner fall back
    # to the platform's existing compressed-Gaussian path.
    "external:planck_2018_distance_prior": None,
    # TODO(step3): ACT DR6 lensing. Class `ACTDR6LensLike` lives in the
    # `act_dr6_lenslike` third-party package. Candidate import path:
    # `act_dr6_lenslike.ACTDR6LensLike`.
    "external:act_dr6_lenslike.ACTDR6LensLike": None,
    # TODO(step3): Planck PR4/NPIPE lensing (Carron+ 2022, arXiv:2206.07773).
    # Package: github.com/carronj/planck_PR4_lensing.
    "external:planck_PR4_lensing": None,
    # TODO(step3): SPT-3G 2018 CMB. Likely third-party
    # `cobaya-likelihood-spt3g` package. Candidate: `spt3g_2018`.
    "external:cmb.spt3g_2018": None,
    # ── Weak lensing / 3x2pt ─────────────────────────────────────────────
    # TODO(step3): KiDS-1000 cosmic shear. Third-party `cobaya-likelihood-kids`.
    "external:kids1000": None,
    # TODO(step3): DES Y3 3x2pt. Third-party `cobaya-likelihood-des-y3`.
    "external:des_y3_3x2pt": None,
    # TODO(step3): HSC Y1 cosmic shear. Third-party `cobaya-likelihood-hsc`.
    "external:hsc_y1_cosmic_shear": None,
    # ── H(z) / Other ─────────────────────────────────────────────────────
    # TODO(step3): cosmic chronometers (Moresco+). No first-party Cobaya
    # likelihood; typically users wrap as a Gaussian likelihood on the
    # H(z) datapoints. Until then, keep None.
    "external:cosmic_chronometers": None,
    # TODO(step3): cosmic chronometers with the full Moresco+2020 systematic
    # covariance (arXiv:2003.07362; gitlab.com/mmoresco/CCcovariance). Same
    # Gaussian-on-H(z) wrap, but with the full NxN covariance. Until then, None.
    "external:cosmic_chronometers_moresco20": None,
    # TODO(step3): SPT-SZ cluster counts (Bocquet+ 2019). No standard
    # Cobaya module; likely custom class.
    "external:cluster.spt_sz_bocquet19": None,
}


GAUSSIAN_PREFIX = "gaussian:"
EXTERNAL_PREFIX = "external:"


def resolve(adapter_name: str | None) -> str | None:
    """Return the real Cobaya import path for an adapter name, or None.

    Behavior:
    * ``None`` / empty / non-string input -> ``None`` (caller should raise
      ``CobayaLikelihoodTranslationPending``).
    * ``gaussian:H0=X,sigma=Y`` adapter -> returns the adapter as-is. The
      runner is expected to parse this inline and inject a Cobaya
      ``gaussian`` likelihood block without needing a third-party module.
    * ``external:<adapter>`` adapter -> dict lookup. Returns the mapped
      Cobaya import path, or ``None`` if the entry is a TODO placeholder
      (or the adapter is unknown).
    * Any other prefix -> ``None``.
    """
    if not isinstance(adapter_name, str) or not adapter_name:
        return None
    if adapter_name.startswith(GAUSSIAN_PREFIX):
        # Gaussian compressed-likelihood priors are handled inline by the
        # runner; no third-party module is needed. Return as-is so the
        # runner can detect the gaussian path.
        return adapter_name
    if not adapter_name.startswith(EXTERNAL_PREFIX):
        return None
    return ADAPTER_TO_COBAYA.get(adapter_name)


def is_translation_pending(adapter_name: str | None) -> bool:
    """Return True iff the adapter is known but its Cobaya path is a TODO.

    Useful for the runner's error message: distinguish "registered but
    cobaya path not yet filled in step 3" from "completely unknown adapter
    name (likely typo or new dataset without entry)".
    """
    if not isinstance(adapter_name, str) or not adapter_name:
        return False
    if not adapter_name.startswith(EXTERNAL_PREFIX):
        return False
    if adapter_name not in ADAPTER_TO_COBAYA:
        return False
    return ADAPTER_TO_COBAYA[adapter_name] is None


__all__ = [
    "ADAPTER_TO_COBAYA",
    "resolve",
    "is_translation_pending",
]

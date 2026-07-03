"""Executability gates, cov-fidelity stamps and the executable-pin audit.

Split verbatim out of the pre-2026-07-03 single-file
app/services/cosmology_likelihoods.py (7,757 lines). Import the package
``app.services.cosmology_likelihoods`` — it re-exports every pre-split name
and keeps the original one-namespace monkeypatch semantics.
"""

from __future__ import annotations


from app.services.cosmology_likelihoods.core import (
    CosmologyDatasetEntry,
)

from app.services.cosmology_likelihoods.bao import (
    EBOSS_DR16_FSBAO_EXECUTABLE_KEYS,
    EBOSS_DR16_GRID_BAO_EXECUTABLE_KEYS,
    SDSS_DR12_CONSENSUS_EXECUTABLE_KEYS,
    _BAO_DATA,
    load_verified_bao_data,
    load_verified_dr12_consensus_data,
    load_verified_fsbao_data,
    load_verified_grid_bao_data,
)

from app.services.cosmology_likelihoods.cc import (
    COSMIC_CHRONOMETER_EXECUTABLE_KEYS,
    COSMIC_CHRONOMETER_FULL_COV_KEYS,
    load_verified_cc_data,
    load_verified_cc_full_cov_data,
)

from app.services.cosmology_likelihoods.rsd import (
    EBOSS_DR16_FSIGMA8_EXECUTABLE_KEYS,
    load_verified_rsd_data,
)

from app.services.cosmology_likelihoods.sn import (
    DES_SN5YR_EXECUTABLE_KEYS,
    PANTHEON18_EXECUTABLE_KEYS,
    PANTHEON_PLUS_EXECUTABLE_KEYS,
    UNION3_EXECUTABLE_KEYS,
    load_verified_des_sn5yr_data,
    load_verified_pantheon18_data,
    load_verified_pantheon_plus_data,
    load_verified_union3_data,
)



# Weakest -> strongest covariance fidelity. 'unverified' = vendored file present
# but its digest mismatched the registry pin (tampering/corruption — must block
# publication); 'literature_typed' = honest hand-typed compilation (no released
# file); 'diagonal' = sha256-pinned vector with diagonal covariance; 'full' =
# sha256-verified released FULL covariance.
_COV_FIDELITY_ORDER = ("unverified", "literature_typed", "diagonal", "full")


def _entry_verification(entry: CosmologyDatasetEntry) -> tuple[str | None, str | None]:
    """(cov_fidelity, sha256) for one executed probe entry.  Branch precedence,
    strongest binding first: a released sha256-pinned covariance file
    (BAO/CC/RSD diagonal/full, or the Pantheon+ full-cov npz when the full path
    is enabled) -> a hand-typed published Gaussian summary ('literature_typed',
    no released file to checksum) -> unstamped (None).  An executed entry returns
    (None, None) only when it is neither a verified file nor a compressed
    summary, so no executed probe slips through the publication gate unstamped."""
    if entry.key in _BAO_DATA:
        verified = load_verified_bao_data(entry.key)
    elif _is_executable_dr12_entry(entry):
        verified = load_verified_dr12_consensus_data(entry.key)
    elif _is_executable_grid_bao_entry(entry):
        verified = load_verified_grid_bao_data(entry.key)
    elif _is_executable_fsbao_entry(entry):
        verified = load_verified_fsbao_data(entry.key)
    elif _is_executable_cc_full_cov_entry(entry):
        verified = load_verified_cc_full_cov_data(entry.key)
    elif _is_executable_cc_entry(entry):
        verified = load_verified_cc_data(entry.key)
    elif _is_executable_rsd_entry(entry):
        verified = load_verified_rsd_data(entry.key)
    elif _is_executable_sn_entry(entry):
        verified = load_verified_pantheon_plus_data(entry.key)
    elif _is_executable_des_sn_entry(entry):
        if entry.key == "des_sn5yr":
            verified = load_verified_des_sn5yr_data(entry.key)
        elif entry.key == "union3":
            verified = load_verified_union3_data(entry.key)
        elif entry.key == "pantheon18":
            verified = load_verified_pantheon18_data(entry.key)
        else:
            raise ValueError(
                f"executable offset-marginalized SN entry {entry.key!r} has no verifier"
            )
    elif entry.compressed_likelihood is not None:
        # Hand-typed published Gaussian summary — honest 'literature_typed'; there
        # is no released, vendored file to sha256-verify, so never 'full'/'diagonal'.
        return ("literature_typed", None)
    else:
        return (None, None)
    return (verified["cov_fidelity"], verified.get("sha256"))


def _aggregate_cov_fidelity(
    executed_entries: list[CosmologyDatasetEntry],
) -> tuple[str | None, dict[str, str | None]]:
    """Aggregate (cov_fidelity, artifact_sha256 map) across EVERY executed probe,
    not just BAO.  cov_fidelity is the WEAKEST across probes ('full' only when
    all are full), so a BAO(full)+CC(diagonal) chain reports 'diagonal', never
    'full'; artifact_sha256 pins every verified probe's file."""
    fidelities: list[str] = []
    artifact_sha256: dict[str, str | None] = {}
    seen: set[str] = set()
    for entry in executed_entries:
        if entry.key in seen:  # an entry can appear in two probe lists; verify once
            continue
        seen.add(entry.key)
        fidelity, sha = _entry_verification(entry)
        if fidelity is None:
            continue
        fidelities.append(fidelity)
        artifact_sha256[entry.key] = sha
    if not fidelities:
        return (None, artifact_sha256)
    weakest = min(
        fidelities,
        key=lambda f: _COV_FIDELITY_ORDER.index(f) if f in _COV_FIDELITY_ORDER else -1,
    )
    return (weakest, artifact_sha256)


def _finalize_cov_fidelity(
    executed_entries: list[CosmologyDatasetEntry], warnings: list[str]
) -> tuple[str | None, dict[str, str | None], bool]:
    """Aggregate cov_fidelity across executed probes, append the publication-block
    warning when it is unstamped (None) or unverified, and return whether it is
    publication-eligible.  Single source for BOTH runners (inline analytic +
    sampling) so the None/unverified gate and its warning cannot drift apart."""
    cov_fidelity, artifact_sha256 = _aggregate_cov_fidelity(executed_entries)
    fidelity_ok = cov_fidelity not in (None, "unverified")
    if not fidelity_ok:
        warnings.append(
            "A fitted data product failed sha256 verification (vendored file "
            "missing or bytes do not match the registry pin) or is an unstamped "
            f"probe (cov_fidelity={cov_fidelity!r}); not publication-ready."
        )
    return cov_fidelity, artifact_sha256, fidelity_ok


# ── T1-U7: self-policing pin enforcement ────────────────────────────────────
# Single source of truth: every in-process-executable probe must read a
# sha256-verified vendored file for the role its loader checks.  Honest
# exception: a MIXED probe whose Gaussian half is a hand-typed literature
# compilation with no released file (6dFGS) while its other half reads a
# sha256-pinned released file (the MGS chi2(alpha) table, 2026-06-12).  It is
# allowlisted to certify 'literature_typed' (the weakest half sets the grade)
# but its pinned half MUST still verify — tampering the table makes the audit
# dirty, not just the runtime loud.
_MIXED_LITERATURE_PLUS_PINNED_OK = frozenset({"sdss_6df_bao"})


def _executable_probe_keys() -> set[str]:
    """Every probe key the phase-1 runner can fit in-process.  Flag-independent:
    the Pantheon+ full-cov pin must exist whether or not the runtime flag is on."""
    return (
        set(_BAO_DATA)
        | set(COSMIC_CHRONOMETER_EXECUTABLE_KEYS)
        | set(COSMIC_CHRONOMETER_FULL_COV_KEYS)
        | set(EBOSS_DR16_FSIGMA8_EXECUTABLE_KEYS)
        | set(EBOSS_DR16_FSBAO_EXECUTABLE_KEYS)
        | set(SDSS_DR12_CONSENSUS_EXECUTABLE_KEYS)
        | set(EBOSS_DR16_GRID_BAO_EXECUTABLE_KEYS)
        | {"pantheon_plus"}
        | {"des_sn5yr"}
        | {"union3"}
        | {"pantheon18"}
    )


def audit_executable_pins() -> list[str]:
    """Issues (empty == clean): every in-process-executable probe must read a
    sha256-verified vendored file (hash_verified True, fidelity full/diagonal),
    EXCEPT allowlisted mixed probes (hand-typed Gaussian half + pinned-file
    half), which must certify 'literature_typed' AND verify their pinned half.
    Used by tests and scripts/audit_registry.py so a future
    executable probe cannot ship without a pinned, verified data product.

    The check relies on each loader's own hash_verified/cov_fidelity — which can
    only be True / 'full' / 'diagonal' when a sha256-pinned product matched the
    vendored file — so there is no parallel role map that could drift out of sync
    with what the loaders actually verify."""
    issues: list[str] = []
    for key in sorted(_executable_probe_keys()):
        if key in _BAO_DATA:
            verified = load_verified_bao_data(key)
        elif key in SDSS_DR12_CONSENSUS_EXECUTABLE_KEYS:
            verified = load_verified_dr12_consensus_data(key)
        elif key in EBOSS_DR16_GRID_BAO_EXECUTABLE_KEYS:
            verified = load_verified_grid_bao_data(key)
        elif key in EBOSS_DR16_FSBAO_EXECUTABLE_KEYS:
            verified = load_verified_fsbao_data(key)
        elif key in COSMIC_CHRONOMETER_FULL_COV_KEYS:
            verified = load_verified_cc_full_cov_data(key)
        elif key in COSMIC_CHRONOMETER_EXECUTABLE_KEYS:
            verified = load_verified_cc_data(key)
        elif key in EBOSS_DR16_FSIGMA8_EXECUTABLE_KEYS:
            verified = load_verified_rsd_data(key)
        elif key == "des_sn5yr":
            verified = load_verified_des_sn5yr_data(key)
        elif key == "union3":
            verified = load_verified_union3_data(key)
        elif key == "pantheon18":
            verified = load_verified_pantheon18_data(key)
        else:
            verified = load_verified_pantheon_plus_data(key)

        if key in _MIXED_LITERATURE_PLUS_PINNED_OK:
            if verified["cov_fidelity"] != "literature_typed" or not verified.get(
                "hash_verified"
            ):
                issues.append(
                    f"{key}: mixed literature+pinned probe must certify "
                    f"'literature_typed' with its pinned half sha256-verified, got "
                    f"cov_fidelity={verified['cov_fidelity']!r}, "
                    f"hash_verified={verified.get('hash_verified')!r}"
                )
            continue

        if not verified.get("hash_verified"):
            issues.append(
                f"{key}: vendored file not sha256-verified "
                f"(hash_verified=False, cov_fidelity={verified['cov_fidelity']!r})"
            )
        elif verified["cov_fidelity"] not in ("full", "diagonal"):
            issues.append(
                f"{key}: verified but fidelity {verified['cov_fidelity']!r} is not a "
                "file-backed grade (expected 'full' or 'diagonal')"
            )
    return issues


def _is_executable_bao_entry(entry: CosmologyDatasetEntry) -> bool:
    return entry.key in _BAO_DATA


def _is_executable_cc_full_cov_entry(entry: CosmologyDatasetEntry) -> bool:
    return entry.key in COSMIC_CHRONOMETER_FULL_COV_KEYS


def _is_executable_cc_entry(entry: CosmologyDatasetEntry) -> bool:
    return (
        entry.key in COSMIC_CHRONOMETER_EXECUTABLE_KEYS
        or entry.key in COSMIC_CHRONOMETER_FULL_COV_KEYS
    )


def _is_executable_rsd_entry(entry: CosmologyDatasetEntry) -> bool:
    return entry.key in EBOSS_DR16_FSIGMA8_EXECUTABLE_KEYS


def _is_executable_fsbao_entry(entry: CosmologyDatasetEntry) -> bool:
    return entry.key in EBOSS_DR16_FSBAO_EXECUTABLE_KEYS


def _is_executable_dr12_entry(entry: CosmologyDatasetEntry) -> bool:
    return entry.key in SDSS_DR12_CONSENSUS_EXECUTABLE_KEYS


def _is_executable_grid_bao_entry(entry: CosmologyDatasetEntry) -> bool:
    return entry.key in EBOSS_DR16_GRID_BAO_EXECUTABLE_KEYS


def _is_executable_sn_entry(entry: CosmologyDatasetEntry) -> bool:
    return entry.key in PANTHEON_PLUS_EXECUTABLE_KEYS


def _is_executable_des_sn_entry(entry: CosmologyDatasetEntry) -> bool:
    # The "des_sn" family/plumbing name now means "offset-marginalized binned
    # SN distance-modulus likelihood" — DES-SN5YR and Pantheon18 (both
    # env-gated) AND Union3 (always on). Same parameter footprint (omegam +
    # w0/wa; H0/M_B marginalized out), same chi2 form, per-key data dispatch.
    return (
        entry.key in DES_SN5YR_EXECUTABLE_KEYS
        or entry.key in UNION3_EXECUTABLE_KEYS
        or entry.key in PANTHEON18_EXECUTABLE_KEYS
    )

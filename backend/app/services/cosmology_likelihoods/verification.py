"""Executability gates, cov-fidelity stamps and the executable-pin audit.

Split verbatim out of the pre-2026-07-03 single-file
app/services/cosmology_likelihoods.py (7,757 lines). Import the package
``app.services.cosmology_likelihoods`` — it re-exports every pre-split name
and keeps the original one-namespace monkeypatch semantics.
"""

from __future__ import annotations

import math
from typing import Any


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

# One publication policy for every cosmology sampler.  These constants are
# intentionally owned next to the data-fidelity gate so in-process and external
# runners cannot quietly evolve different meanings of ``publication_ready``.
PUBLICATION_MIN_INDEPENDENT_CHAINS = 4
PUBLICATION_RHAT_MAX = 1.01
PUBLICATION_ESS_MIN = 400.0
_PUBLICATION_COV_FIDELITIES = frozenset({"diagonal", "full"})
PUBLICATION_REQUIRED_ADEQUACY_CHECKS = (
    "prior_predictive_check",
    "posterior_predictive_check",
    "prior_sensitivity",
    "systematics_robustness",
    "simulation_recovery",
    "independent_reproduction",
)


def build_model_adequacy_subject(
    *,
    model: str,
    dataset_keys: list[str] | tuple[str, ...],
    random_seed: int,
    summaries: dict[str, Any],
    diagnostics: dict[str, Any],
    data_verification: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the exact run subject to which adequacy evidence must bind."""

    from app.services.server_evidence import scientific_content_hash

    result_fingerprint = scientific_content_hash(
        {
            "summaries": summaries,
            "diagnostics": diagnostics,
            "data_verification": data_verification or {},
        }
    )
    return {
        "model": str(model),
        "dataset_keys": sorted(str(key) for key in dataset_keys),
        "random_seed": int(random_seed),
        "result_fingerprint": result_fingerprint,
    }


def build_model_adequacy_attestation(
    *,
    subject: dict[str, Any],
    evidence_by_check: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Create a signed, inline-evidence model-adequacy attestation.

    This server-side constructor is the only supported green path.  Every
    required check is bound to the exact run subject, hashed independently,
    and then covered by the manifest HMAC.
    """

    from app.services.server_evidence import (
        build_scientific_attestation,
        scientific_content_hash,
    )

    if not isinstance(subject, dict) or not subject:
        raise ValueError("model-adequacy subject is required")
    subject_hash = scientific_content_hash(subject)
    checks: dict[str, dict[str, Any]] = {}
    for name in PUBLICATION_REQUIRED_ADEQUACY_CHECKS:
        supplied = evidence_by_check.get(name)
        if not isinstance(supplied, dict) or not supplied:
            raise ValueError(f"missing adequacy evidence for {name}")
        evidence = {
            **supplied,
            "check": name,
            "status": "passed",
            "subject_hash": subject_hash,
        }
        evidence_hash = scientific_content_hash(evidence)
        checks[name] = {
            "status": "passed",
            "evidence_id": evidence_hash,
            "evidence_hash": evidence_hash,
            "evidence": evidence,
        }
    return build_scientific_attestation(
        attestation_type="model_adequacy",
        payload={
            "source": "server_attested",
            "subject": subject,
            "subject_hash": subject_hash,
            "checks": checks,
        },
    )


def _assess_model_adequacy(
    model_adequacy: dict[str, Any] | None,
    *,
    expected_subject: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a server-attested model-adequacy manifest.

    Convergence answers whether a sampler explored its target distribution; it
    does not show that the target model can reproduce the observations, is
    robust to reasonable priors/systematics, or has been independently
    reproduced.  Publication eligibility therefore needs a separate manifest
    whose checks point to durable evidence records.  Loose caller booleans are
    intentionally insufficient.
    """

    manifest = model_adequacy if isinstance(model_adequacy, dict) else {}
    checks = manifest.get("checks")
    checks = checks if isinstance(checks, dict) else {}
    reasons: list[str] = []
    from app.services.server_evidence import (
        scientific_content_hash,
        verify_scientific_attestation,
    )

    if manifest.get("source") not in {"server_attested", "human_attested"}:
        reasons.append("model_adequacy_attestation_missing")
    signature_verified = verify_scientific_attestation(
        manifest,
        expected_type="model_adequacy",
    )
    if not signature_verified:
        reasons.append("model_adequacy_signature_unverified")
    manifest_hash = manifest.get("manifest_hash")
    if (
        not isinstance(manifest_hash, str)
        or not manifest_hash.startswith("sha256:")
        or len(manifest_hash) != 71
    ):
        reasons.append("model_adequacy_manifest_hash_missing")

    subject = manifest.get("subject")
    subject_hash = manifest.get("subject_hash")
    expected_subject_hash = (
        scientific_content_hash(expected_subject)
        if isinstance(expected_subject, dict) and expected_subject
        else None
    )
    if not isinstance(subject, dict) or not subject:
        reasons.append("model_adequacy_subject_missing")
    elif subject_hash != scientific_content_hash(subject):
        reasons.append("model_adequacy_subject_hash_mismatch")
    if expected_subject_hash is None:
        reasons.append("model_adequacy_subject_unbound")
    elif subject_hash != expected_subject_hash:
        reasons.append("model_adequacy_subject_mismatch")

    observed: dict[str, str] = {}
    for name in PUBLICATION_REQUIRED_ADEQUACY_CHECKS:
        record = checks.get(name)
        record = record if isinstance(record, dict) else {}
        status = str(record.get("status") or "missing").lower()
        evidence_id = record.get("evidence_id")
        evidence_hash = record.get("evidence_hash")
        evidence = record.get("evidence")
        passed = bool(
            status in {"passed", "pass", "ok"}
            and isinstance(evidence_id, str)
            and evidence_id.strip()
            and isinstance(evidence_hash, str)
            and evidence_id == evidence_hash
            and isinstance(evidence, dict)
            and evidence_hash == scientific_content_hash(evidence)
            and evidence.get("check") == name
            and evidence.get("status") == "passed"
            and evidence.get("subject_hash") == subject_hash
        )
        observed[name] = "passed" if passed else status
        if not passed:
            reasons.append(f"{name}_missing_or_failed")

    return {
        "eligible": not reasons,
        "reasons": reasons,
        "required_checks": list(PUBLICATION_REQUIRED_ADEQUACY_CHECKS),
        "observed": observed,
        "manifest_hash": manifest_hash if isinstance(manifest_hash, str) else None,
        "source": manifest.get("source"),
        "signature_verified": signature_verified,
        "subject_hash": subject_hash if isinstance(subject_hash, str) else None,
        "subject_matches_run": bool(
            expected_subject_hash is not None and subject_hash == expected_subject_hash
        ),
    }


def _assess_publication_gate(
    *,
    cov_fidelity: str | None,
    likelihood_is_compressed_or_approximate: bool,
    n_independent_chains: int,
    per_parameter: dict[str, dict[str, Any]] | None,
    critical_parameters: list[str] | tuple[str, ...],
    assess_data_likelihood: bool = True,
    model_adequacy: dict[str, Any] | None = None,
    model_adequacy_subject: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the shared, machine-readable cosmology publication decision.

    ``publication_ready`` means substantially more than "a sampler returned
    numbers": inputs must be machine-bound rather than hand typed; the executed
    likelihood must not be a compressed/approximate substitute; and every
    critical sampled parameter must pass rank-normalized R-hat and bulk-ESS on
    at least four genuinely independent chains.  Those conditions establish
    numerical reproducibility only.  Final publication eligibility additionally
    requires a server/human-attested model-adequacy manifest covering predictive
    checks, prior/systematics sensitivity, simulation recovery, and independent
    reproduction.

    The returned reason codes are deliberately stable API fields.  Callers may
    still expose a scientifically useful preliminary posterior when this gate is
    false, but may not relabel it as publication-ready.
    """
    reasons: list[str] = []
    parameter_failures: dict[str, list[str]] = {}
    critical = [str(name) for name in critical_parameters]
    diagnostics = per_parameter if isinstance(per_parameter, dict) else {}

    if not critical:
        reasons.append("no_critical_parameters")

    if assess_data_likelihood:
        if cov_fidelity == "literature_typed":
            reasons.append("literature_typed_input")
        elif cov_fidelity not in _PUBLICATION_COV_FIDELITIES:
            reasons.append("unverified_or_unpinned_input")

        if likelihood_is_compressed_or_approximate:
            reasons.append("compressed_or_approximate_likelihood")

    if int(n_independent_chains) < PUBLICATION_MIN_INDEPENDENT_CHAINS:
        reasons.append("fewer_than_four_independent_chains")

    for name in critical:
        record = diagnostics.get(name)
        failures: list[str] = []
        if not isinstance(record, dict):
            failures.extend(("rank_normalized_rhat_unavailable", "bulk_ess_unavailable"))
        else:
            raw_rhat = record.get("rhat")
            raw_ess = record.get("ess_bulk")
            rhat = (
                float(raw_rhat)
                if isinstance(raw_rhat, (int, float))
                and not isinstance(raw_rhat, bool)
                and math.isfinite(float(raw_rhat))
                else None
            )
            ess = (
                float(raw_ess)
                if isinstance(raw_ess, (int, float))
                and not isinstance(raw_ess, bool)
                and math.isfinite(float(raw_ess))
                else None
            )
            if rhat is None:
                failures.append("rank_normalized_rhat_unavailable")
            elif rhat >= PUBLICATION_RHAT_MAX:
                failures.append("rank_normalized_rhat_at_or_above_1.01")
            if ess is None:
                failures.append("bulk_ess_unavailable")
            elif ess < PUBLICATION_ESS_MIN:
                failures.append("bulk_ess_below_400")
        if failures:
            parameter_failures[name] = failures

    if parameter_failures:
        for code in (
            "rank_normalized_rhat_unavailable",
            "rank_normalized_rhat_at_or_above_1.01",
            "bulk_ess_unavailable",
            "bulk_ess_below_400",
        ):
            if any(code in failures for failures in parameter_failures.values()):
                reasons.append(code)

    # Preserve order while protecting callers from duplicate reason codes.
    numerical_reasons = list(dict.fromkeys(reasons))
    numerical_eligible = not numerical_reasons
    adequacy = _assess_model_adequacy(
        model_adequacy,
        expected_subject=model_adequacy_subject,
    )
    # Gate sequentially: do not bury a numerical failure under six downstream
    # adequacy failures.  The adequacy object remains visible as the next stage.
    reasons = list(numerical_reasons)
    if numerical_eligible:
        reasons.extend(adequacy["reasons"])
    return {
        "eligible": not reasons,
        "numerical_eligible": numerical_eligible,
        "reasons": reasons,
        "numerical_reasons": numerical_reasons,
        "parameter_failures": parameter_failures,
        "critical_parameters": critical,
        "model_adequacy": adequacy,
        "thresholds": {
            "min_independent_chains": PUBLICATION_MIN_INDEPENDENT_CHAINS,
            "rhat_method": "rank",
            "rhat_max_exclusive": PUBLICATION_RHAT_MAX,
            "ess_method": "bulk",
            "ess_min": PUBLICATION_ESS_MIN,
        },
        "observed": {
            "cov_fidelity": cov_fidelity,
            "likelihood_is_compressed_or_approximate": bool(
                likelihood_is_compressed_or_approximate
            ),
            "data_likelihood_assessed": bool(assess_data_likelihood),
            "n_independent_chains": int(n_independent_chains),
        },
    }


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
    publication-eligible.  ``literature_typed`` is safe to expose as a labelled
    preliminary input, but is not machine-bound evidence and therefore cannot
    satisfy the publication gate.  Single source for BOTH runners (inline
    analytic + sampling) so this policy cannot drift apart."""
    cov_fidelity, artifact_sha256 = _aggregate_cov_fidelity(executed_entries)
    fidelity_ok = cov_fidelity in _PUBLICATION_COV_FIDELITIES
    if cov_fidelity == "literature_typed":
        warnings.append(
            "At least one executed constraint is a hand-typed literature summary "
            "with no hash-bound data vector/covariance; result is preliminary only "
            "and cannot be publication-ready."
        )
    elif not fidelity_ok:
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

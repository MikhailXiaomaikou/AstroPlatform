"""Subprocess runner for the Cobaya cosmology likelihood backend.

Implements the ``external_cobaya`` execution-mode path used by
``cosmology_likelihoods.run_likelihood_chain``. Encapsulates everything
that should not pollute the cosmology-likelihoods registry module:

* feature-gate detection (env flag + ``cobaya`` import probe)
* tempdir management for chain output
* YAML generation and ``cobaya-run`` subprocess dispatch
* chain-file parsing (Cobaya writes ``.{n}.txt`` per chain alongside an
  ``.input.yaml`` and a ``.checkpoint`` next to ``output_prefix``)
* posterior summary + ``rhat`` / ``ess`` diagnostics via ``arviz``
* mapping of every failure mode to a publication-blocking ``NOT_PUB_READY``
  result that round-trips through ``_compressed_runner_unavailable`` and the
  zero-fabrication banner machinery

PART AI Phase 5 #2 Track 2 step 2.

What step 2 ships
-----------------

* The complete subprocess + parse pipeline above.
* Default ``EXTERNAL_COBAYA_ENABLED=false`` — the dispatch is a strict no-op
  for every existing caller. ``cosmology_likelihoods.run_likelihood_chain``
  preserves the byte-identical compressed-Gaussian / unavailable behaviour
  shipped in ``02edf97``.
* When the env flag is enabled (Render env override), the runner is
  invoked but currently surfaces a deterministic
  ``error_class="cobaya_likelihood_id_translation_pending"`` because the
  ``CosmologyDatasetEntry.cobaya_likelihood`` strings registered in
  ``cosmology_likelihoods`` (e.g. ``"external:bao.sdss_6df_legacy"``) are
  *adapter* names, not real Cobaya likelihood identifiers. step 3 will own
  the ``adapter -> import_path`` resolver.

Until step 3 lands, the only behavioural change a maintainer can observe
with ``EXTERNAL_COBAYA_ENABLED=true`` is that
``run_likelihood_chain`` returns the ``cobaya_likelihood_id_translation_pending``
NOT_PUB_READY shape instead of the legacy ``compressed_gaussian_unavailable``
shape — both are publication-blocking, so the zero-fabrication contract
holds either way.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from app.services.cosmology_likelihoods import CosmologyDatasetEntry


logger = logging.getLogger(__name__)


EXTERNAL_COBAYA_ENV = "EXTERNAL_COBAYA_ENABLED"
PACKAGES_PATH_ENV = "COBAYA_PACKAGES_PATH"

# Committed, sha256-pinned cobaya packages dir (e.g. the Planck plik_lite native
# data). The subprocess reads THIS unless COBAYA_PACKAGES_PATH is set, so a CMB
# run is reproducible with no runtime network. (parents[2] = backend/.)
VENDORED_COBAYA_PACKAGES = Path(__file__).resolve().parents[2] / "data" / "cobaya_packages"

# Params sampled with a narrow GAUSSIAN prior (not flat min/max) in the cobaya YAML:
# the Planck plik_lite calibration nuisance A_planck, and the reionization optical
# depth tau (high-l TT/TE/EE alone cannot constrain it — pin it with the Planck
# 2018 lowE posterior). Mean/sigma per Planck 2018 V (arXiv:1907.12875).
COBAYA_GAUSSIAN_PRIORS: dict[str, tuple[float, float]] = {
    "A_planck": (1.0, 0.0025),
    "tau": (0.0544, 0.0073),
}

# The 2018 SimAll low-l EE likelihood IS the measurement behind the lowE tau
# posterior. When one of these entries is selected, tau keeps its FLAT prior
# (the likelihood constrains it) — applying the Gaussian pin on top would count
# the same data twice.
TAU_CONSTRAINING_KEYS = frozenset({"planck_2018_lowl_EE"})

# Platform parameter name -> the name cobaya/CAMB actually consume. Sampled
# under the platform name (chain columns keep it) with drop: true, plus a
# dynamically-computed alias param handed to the theory. Without this, the
# model build dies with "Could not find anything to use input parameter(s)".
COBAYA_PARAM_ALIASES: dict[str, str] = {
    "w0": "w",        # CPL pair is (w, wa) in cobaya/CAMB
    "omegak": "omk",  # curvature density is omk in CAMB
}

DEFAULT_EVALUATE_TIMEOUT_S = 180.0
DEFAULT_MCMC_TIMEOUT_S = 1800.0

# CMB external-cobaya likelihoods whose vendored data files MUST sha256-verify at
# runtime before the subprocess runs. The cobaya native likelihood reads these from
# the resolved COBAYA_PACKAGES_PATH, which an env override (or a post-deploy edit)
# could point at a different/tampered copy — every in-process probe carries
# hash_verified, so the CMB path must too. Maps entry.key -> {relpath under
# packages_path: registry data_product role whose sha256 pins it}.
_CMB_PINNED_DATA: dict[str, dict[str, str]] = {
    "planck_2018_highl_TTTEEE_lite": {
        "data/planck_2018_pliklite_native/cl_cmb_plik_v22.dat": "measurement_vector",
        "data/planck_2018_pliklite_native/c_matrix_plik_v22.dat": "covariance",
        "data/planck_2018_pliklite_native/blmin.dat": "binning_blmin",
        "data/planck_2018_pliklite_native/blmax.dat": "binning_blmax",
        "data/planck_2018_pliklite_native/bweight.dat": "binning_weights",
        "data/planck_2018_pliklite_native/plik_lite_v22.dataset": "dataset_ini",
    },
    "planck_2018_lowl_TT": {
        "data/planck_2018_lowT_native/mu.txt": "measurement_vector",
        "data/planck_2018_lowT_native/mu_sigma.txt": "sigma_vector",
        "data/planck_2018_lowT_native/cov.txt": "covariance",
        "data/planck_2018_lowT_native/cl2x_1.txt": "br_spline_table_1",
        "data/planck_2018_lowT_native/cl2x_2.txt": "br_spline_table_2",
    },
    "planck_2018_lowl_EE": {
        "data/planck_2018_lowE_native/prob_table.txt": "probability_table",
    },
    # Lensing: 5 flat files + the two per-bin window SETS. Window directories
    # (relpath ending "/") are pinned via an aggregate digest — sha256 over the
    # sorted (filename + bytes) of every file inside — because each window is
    # chi2-load-bearing (the plik_lite bweight lesson) and per-file pins for
    # 18 windows would bloat the registry without adding protection.
    "planck_2018_lensing": {
        "data/planck_supp_data_and_covmats/lensing/2018/smicadx12_Dec5_ftl_mv2_ndclpp_p_teb_consext8.dataset": "dataset_ini",
        "data/planck_supp_data_and_covmats/lensing/2018/smicadx12_Dec5_ftl_mv2_ndclpp_p_teb_consext8_bandpowers.dat": "measurement_vector",
        "data/planck_supp_data_and_covmats/lensing/2018/smicadx12_Dec5_ftl_mv2_ndclpp_p_teb_consext8_cov.dat": "covariance",
        "data/planck_supp_data_and_covmats/lensing/2018/smicadx12_Dec5_ftl_mv2_ndclpp_p_teb_consext8_lensing_fiducial_correction.dat": "fiducial_correction",
        "data/planck_supp_data_and_covmats/lensing/2018/planck_calib.paramnames": "calibration_paramnames",
        "data/planck_supp_data_and_covmats/lensing/2018/smicadx12_Dec5_ftl_mv2_ndclpp_p_teb_consext8_window/": "bin_windows_dir",
        "data/planck_supp_data_and_covmats/lensing/2018/smicadx12_Dec5_ftl_mv2_ndclpp_p_teb_consext8_lens_delta_window/": "lin_windows_dir",
    },
}


def _directory_aggregate_sha256(directory: Path) -> str:
    """Aggregate digest of a pinned data DIRECTORY: sha256 over the sorted
    (filename + bytes) of every regular file inside. Any added, removed,
    renamed, or edited window file changes the digest."""
    digest = hashlib.sha256()
    for child in sorted(p for p in directory.iterdir() if p.is_file()):
        digest.update(child.name.encode())
        digest.update(child.read_bytes())
    return digest.hexdigest()


def _resolve_packages_path() -> str | None:
    """The cobaya packages_path the subprocess will read — an explicit
    COBAYA_PACKAGES_PATH override, else the committed sha256-pinned vendored dir.
    Single source so the YAML and the runtime hash check never drift."""
    return os.environ.get(PACKAGES_PATH_ENV) or (
        str(VENDORED_COBAYA_PACKAGES) if VENDORED_COBAYA_PACKAGES.is_dir() else None
    )


def _verify_pinned_cmb_data(entries: list["CosmologyDatasetEntry"]) -> dict[str, Any] | None:
    """Recompute the on-disk sha256 of every pinned CMB data file at the RESOLVED
    packages_path and compare to the registry pins. Returns a verification record
    (None when no pinned CMB entry is selected). hash_verified is False on any
    missing file or digest mismatch, so a redirected/edited covariance cannot drive
    a result stamped with the official Planck pins."""
    pinned = [(e, _CMB_PINNED_DATA[e.key]) for e in entries if e.key in _CMB_PINNED_DATA]
    if not pinned:
        return None
    packages_path = _resolve_packages_path()
    files_sha256: dict[str, str] = {}
    mismatches: list[str] = []
    for entry, spec in pinned:
        pins = {p.role: p.sha256 for p in entry.data_products}
        for relpath, role in spec.items():
            f = Path(packages_path) / relpath if packages_path else None
            if relpath.endswith("/"):
                # Directory aggregate pin (e.g. lensing bin-window sets).
                if f is None or not f.is_dir():
                    mismatches.append(f"{relpath}: directory missing")
                    continue
                digest = _directory_aggregate_sha256(f)
                files_sha256[relpath] = digest
                if digest != pins.get(role):
                    mismatches.append(f"{relpath}: directory aggregate sha256 mismatch")
                continue
            if f is None or not f.is_file():
                mismatches.append(f"{relpath}: file missing")
                continue
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            files_sha256[relpath] = digest
            if digest != pins.get(role):
                mismatches.append(f"{relpath}: sha256 mismatch")
    selected_dataset_keys = list(dict.fromkeys(entry.key for entry, _ in pinned))
    return {
        "packages_path": packages_path,
        "hash_verified": not mismatches,
        "selected_dataset_keys": selected_dataset_keys,
        "verified_dataset_keys": selected_dataset_keys if not mismatches else [],
        "files_sha256": files_sha256,
        "mismatches": mismatches,
    }


class CobayaRunError(RuntimeError):
    """Base class for cobaya_runner failures.

    Carries an ``error_class`` string mirrored into the result envelope so
    the LLM (and humans reading Render logs) can match a behaviour to a
    specific failure mode.
    """

    error_class: str = "cobaya_run_failed"

    def __init__(self, message: str, *, error_class: str | None = None) -> None:
        super().__init__(message)
        if error_class is not None:
            self.error_class = error_class


class CobayaImportError(CobayaRunError):
    error_class = "cobaya_not_installed"


class CobayaConfigError(CobayaRunError):
    error_class = "cobaya_config_invalid"


class CobayaSubprocessTimeout(CobayaRunError):
    error_class = "cobaya_subprocess_timeout"


class CobayaSubprocessFailure(CobayaRunError):
    error_class = "cobaya_subprocess_nonzero_exit"


class CobayaParseError(CobayaRunError):
    error_class = "cobaya_chain_parse_failed"


class CobayaLikelihoodTranslationPending(CobayaRunError):
    """The CosmologyDatasetEntry.cobaya_likelihood string is an internal
    adapter name (e.g. ``external:bao.sdss_6df_legacy``) and step 3 still
    owns the resolver that maps it to a real Cobaya import path.
    """

    error_class = "cobaya_likelihood_id_translation_pending"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_external_enabled() -> bool:
    """Return True iff the Render env opted in AND ``cobaya`` is importable.

    Both checks must pass; otherwise the caller falls back to the
    compressed-Gaussian path. The env probe deliberately runs first so we
    never pay the cost of importing ``cobaya`` (slow on cold start) when
    the operator has not opted in.
    """
    if not _env_truthy(EXTERNAL_COBAYA_ENV):
        return False
    return _cobaya_import_ok()


def dispatch_external_cobaya(
    *,
    model_key: str,
    entries: list[CosmologyDatasetEntry],
    prior_bounds: dict[str, tuple[float, float]],
    parameter_order: list[str],
    seed: int,
    sample_count: int,
    sampler: str = "evaluate",
    timeout_s: float | None = None,
    include_chain_payload: bool = False,
) -> dict[str, Any]:
    """Run ``cobaya-run`` for the supplied entries and return the result envelope.

    The return shape is intentionally aligned with the dictionary
    ``run_likelihood_chain`` already produces (``parameters``,
    ``derived_params``, ``fit_statistics``, ``chain_diagnostics``,
    ``provenance``, ``warnings``...) so the caller can return our value
    directly without further massaging.

    On any failure mode this function still returns a publication-blocking
    NOT_PUB_READY envelope rather than raising — surfacing a structured
    failure to the chat layer is the contract.
    """
    if not entries:
        return _runner_failure(
            model_key=model_key,
            entries=entries,
            seed=seed,
            error_class="no_dataset_entries",
            message="dispatch_external_cobaya called without dataset entries.",
        )

    from app.services.cobaya_adapter_registry import is_translation_pending

    pending_translations: list[str] = []
    unknown_adapters: list[str] = []
    for entry in entries:
        if _cobaya_likelihood_translatable(entry.cobaya_likelihood):
            continue
        if is_translation_pending(entry.cobaya_likelihood):
            pending_translations.append(entry.key)
        else:
            unknown_adapters.append(
                f"{entry.key} (adapter={entry.cobaya_likelihood!r})"
            )
    if pending_translations or unknown_adapters:
        parts = [
            "External Cobaya backend reached, but the adapter-name to Cobaya "
            "import-path resolver has not been filled in for one or more "
            "selected datasets."
        ]
        if pending_translations:
            parts.append(
                "TODO in cobaya_adapter_registry: "
                + ", ".join(pending_translations)
            )
        if unknown_adapters:
            parts.append(
                "Unknown adapter (not in cobaya_adapter_registry): "
                + ", ".join(unknown_adapters)
            )
        return _runner_failure(
            model_key=model_key,
            entries=entries,
            seed=seed,
            error_class=CobayaLikelihoodTranslationPending.error_class,
            message=" ".join(parts),
        )

    # NOTE: control reaches here only when step 3 has filled in the
    # adapter resolver. Until then, the early return above prevents
    # _run_cobaya_subprocess from ever being called in production. The
    # remainder of this function is exercised by mocked tests so that the
    # subprocess + parse machinery is in place when step 3 lands.

    # Runtime data-fidelity gate: a pinned CMB likelihood must read sha256-verified
    # data at the resolved packages_path — otherwise BLOCK, so a redirected
    # COBAYA_PACKAGES_PATH or an edited vendored file can never drive a CMB fit that
    # is then stamped with the official Planck pins/citations.
    cmb_verification = _verify_pinned_cmb_data(entries)
    if cmb_verification is not None and not cmb_verification["hash_verified"]:
        return _runner_failure(
            model_key=model_key,
            entries=entries,
            seed=seed,
            error_class="cobaya_data_sha256_mismatch",
            message=(
                "CMB likelihood data failed sha256 verification at the resolved "
                f"COBAYA_PACKAGES_PATH={cmb_verification['packages_path']!r}: "
                + "; ".join(cmb_verification["mismatches"])
                + " — refusing to run a CMB fit on unverified data."
            ),
        )

    try:
        with tempfile.TemporaryDirectory(prefix="cobaya_run_") as tmpdir:
            tmp_path = Path(tmpdir)
            output_prefix = tmp_path / "chain"
            yaml_text = _build_cobaya_yaml(
                model_key=model_key,
                entries=entries,
                prior_bounds=prior_bounds,
                parameter_order=parameter_order,
                sampler=sampler,
                output_prefix=output_prefix,
                seed=seed,
            )
            yaml_path = tmp_path / "config.yaml"
            yaml_path.write_text(yaml_text, encoding="utf-8")

            timeout = (
                timeout_s
                if timeout_s is not None
                else (DEFAULT_EVALUATE_TIMEOUT_S if sampler == "evaluate" else DEFAULT_MCMC_TIMEOUT_S)
            )
            completed = _run_cobaya_subprocess(yaml_path, timeout_s=timeout)
            samples_per_chain, chain_meta = _parse_chain_files(
                output_prefix=output_prefix,
                parameter_order=parameter_order,
            )
            summaries, diagnostics = _summarise_samples(
                samples_per_chain=samples_per_chain,
                parameter_order=parameter_order,
            )
            result = _runner_success(
                model_key=model_key,
                entries=entries,
                seed=seed,
                sampler=sampler,
                summaries=summaries,
                diagnostics=diagnostics,
                chain_meta=chain_meta,
                stdout_tail=_tail(completed.stdout),
                data_verification=cmb_verification,
            )
            if include_chain_payload:
                # Capture the sampler's own chain files (true weights and
                # -logpost columns, getdist-native) BEFORE the tempdir is
                # deleted. Same internal-only contract as the in-process
                # payload: the chat exec wrapper pops this key and uploads;
                # raw bytes never reach normalization or the SSE stream.
                raw_files = {
                    path.name: path.read_bytes()
                    for path in sorted(tmp_path.glob(f"{output_prefix.name}.*.txt"))
                }
                if raw_files:
                    input_yaml = tmp_path / f"{output_prefix.name}.input.yaml"
                    if input_yaml.is_file():
                        raw_files[input_yaml.name] = input_yaml.read_bytes()
                    result["_chain_payload"] = {
                        "raw_files": raw_files,
                        "parameter_order": list(parameter_order),
                        "prior_bounds": dict(prior_bounds),
                        "sampler": f"cobaya_{sampler}",
                        "seed": seed,
                    }
            return result
    except CobayaRunError as exc:
        return _runner_failure(
            model_key=model_key,
            entries=entries,
            seed=seed,
            error_class=exc.error_class,
            message=str(exc),
        )
    except Exception as exc:  # defensive — unexpected errors must still surface
        logger.exception("cobaya_runner unexpected failure")
        return _runner_failure(
            model_key=model_key,
            entries=entries,
            seed=seed,
            error_class="cobaya_runner_unexpected",
            message=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# YAML generation
# ---------------------------------------------------------------------------


def _build_cobaya_yaml(
    *,
    model_key: str,
    entries: list[CosmologyDatasetEntry],
    prior_bounds: dict[str, tuple[float, float]],
    parameter_order: list[str],
    sampler: str,
    output_prefix: Path,
    seed: int,
) -> str:
    """Render the Cobaya YAML config that step 3's resolver will hand off."""
    # NOTE: avoid pulling in pyyaml just for this; Cobaya is happy with the
    # simple subset of YAML we emit (no anchors/refs/multiline).
    lines: list[str] = []
    lines.append("# Generated by app.services.cobaya_runner")
    lines.append(f"output: {output_prefix}")

    packages_path = _resolve_packages_path()
    if packages_path:
        lines.append(f"packages_path: {packages_path}")

    lines.append("theory:")
    lines.append("  camb:")
    lines.append("    extra_args:")
    for key, value in _model_theory_args_safe(model_key).items():
        lines.append(f"      {key}: {_yaml_scalar(value)}")

    lines.append("likelihood:")
    for entry in entries:
        translated = _translate_likelihood_id(entry.cobaya_likelihood)
        lines.append(f"  {translated}: null")

    # tau's Gaussian pin is a stand-in for the low-l EE data; drop it when the
    # real likelihood is in the run (TAU_CONSTRAINING_KEYS) so tau is sampled
    # flat and constrained by the measurement instead.
    gaussian_pins = dict(COBAYA_GAUSSIAN_PRIORS)
    if any(entry.key in TAU_CONSTRAINING_KEYS for entry in entries):
        gaussian_pins.pop("tau", None)

    lines.append("params:")
    for name in parameter_order:
        low, high = prior_bounds[name]
        lines.append(f"  {name}:")
        if name in gaussian_pins:
            loc, scale = gaussian_pins[name]
            lines.append("    prior:")
            lines.append("      dist: norm")
            lines.append(f"      loc: {float(loc)}")
            lines.append(f"      scale: {float(scale)}")
            lines.append(f"    ref: {float(loc)}")
            lines.append(f"    proposal: {float(scale)}")
        else:
            lines.append("    prior:")
            lines.append(f"      min: {float(low)}")
            lines.append(f"      max: {float(high)}")
            lines.append(f"    ref: {0.5 * (float(low) + float(high))}")
            # Floor must be tiny enough not to wreck small-scale params (As~2e-9);
            # every O(1)+ param's (high-low)/50 already dominates it.
            proposal = max((float(high) - float(low)) / 50.0, 1e-12)
            lines.append(f"    proposal: {proposal}")
        if name in COBAYA_PARAM_ALIASES:
            # Sampled under the platform name (chain columns keep it); drop
            # excludes it from the theory input — the dynamic alias param
            # below hands CAMB the same value under the name it consumes.
            lines.append("    drop: true")

    for platform_name, cobaya_name in COBAYA_PARAM_ALIASES.items():
        if platform_name in parameter_order:
            lines.append(f"  {cobaya_name}:")
            lines.append(f"    value: \"lambda {platform_name}: {platform_name}\"")
            lines.append("    derived: false")

    lines.append("sampler:")
    if sampler == "evaluate":
        lines.append("  evaluate: null")
    elif sampler == "mcmc":
        lines.append("  mcmc:")
        # Match the platform publication threshold.  A looser 0.05 stop used
        # to let the subprocess finish before the result could satisfy the
        # advertised rank-normalized R-hat < 1.01 requirement.
        lines.append("    Rminus1_stop: 0.01")
        lines.append("    max_samples: 200000")
        # Pin the sampler seed so the run is reproducible — the result envelope
        # and provenance stamp this exact value as random_seed; without it the
        # chain would draw from an unseeded RNG and the provenance claim is false.
        lines.append(f"    seed: {int(seed)}")
    else:
        raise CobayaConfigError(
            f"unsupported cobaya sampler {sampler!r}; allowed: evaluate / mcmc"
        )

    return "\n".join(lines) + "\n"


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return f"\"{value}\""


def _model_theory_args_safe(model_key: str) -> dict[str, Any]:
    """Read ``_model_theory_args`` if exported, otherwise return a minimal LCDM stub.

    Avoids a hard import-cycle on ``cosmology_likelihoods`` during module
    load. The cosmology-likelihoods module is imported lazily here.
    """
    try:
        from app.services.cosmology_likelihoods import _model_theory_args  # type: ignore
        return _model_theory_args(model_key)
    except Exception:
        return {"halofit_version": "mead2020"}


def _translate_likelihood_id(cobaya_likelihood: str) -> str:
    """Map a registry adapter name to a real Cobaya likelihood identifier.

    Step 3 (2026-05-20) wires this to ``cobaya_adapter_registry.resolve``.
    When the resolver returns ``None`` (every adapter today, until each TODO
    is filled in the registry), this falls back to the original adapter
    string so the YAML builder can still produce a structurally valid
    config. ``dispatch_external_cobaya`` separately gates whether the
    config is ever handed to a real ``cobaya-run`` subprocess via
    :func:`_cobaya_likelihood_translatable`, so the as-is string can never
    reach Cobaya in production while step-3 entries remain unfilled.
    """
    from app.services.cobaya_adapter_registry import resolve

    resolved = resolve(cobaya_likelihood)
    return resolved if resolved is not None else cobaya_likelihood


def _cobaya_likelihood_translatable(cobaya_likelihood: str | None) -> bool:
    """Return True iff step 3's resolver currently knows how to translate this id.

    Step 3 (2026-05-20) delegates to ``cobaya_adapter_registry.resolve``:
    the resolver returns a real Cobaya import path when the adapter's TODO
    has been filled in, and ``None`` otherwise. Every adapter is ``None``
    today, so behaviour is byte-identical to the step-2 always-deny shipped
    in ``02edf97`` until a specific dataset is unlocked.

    Note: ``gaussian:H0=…,sigma=…`` adapters are intentionally treated as
    NOT translatable here even though the registry returns them as-is.
    Building a real cobaya gaussian-likelihood YAML block requires inline
    parsing that has not been written yet; until it is, the runner must
    stay on the platform's compressed-Gaussian fallback path.
    """
    from app.services.cobaya_adapter_registry import GAUSSIAN_PREFIX, resolve

    resolved = resolve(cobaya_likelihood)
    if resolved is None:
        return False
    if resolved.startswith(GAUSSIAN_PREFIX):
        return False
    return True


# ---------------------------------------------------------------------------
# subprocess
# ---------------------------------------------------------------------------


def _run_cobaya_subprocess(yaml_path: Path, *, timeout_s: float) -> subprocess.CompletedProcess:
    # Invoke via the SAME interpreter (`python -m cobaya run`) rather than a bare
    # `cobaya-run` on PATH: the web worker / CI venv often does not have venv/bin/
    # on PATH, but sys.executable always resolves the cobaya in this environment.
    # dispatch_external_cobaya has already confirmed cobaya is importable.
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "cobaya", "run", str(yaml_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CobayaSubprocessTimeout(
            f"cobaya-run timed out after {timeout_s:.0f} s on {yaml_path}"
        ) from exc
    if completed.returncode != 0:
        # Cobaya prints its traceback to stdout, so surface BOTH tails — a
        # stderr-only message was empty on real failures.
        raise CobayaSubprocessFailure(
            f"cobaya-run exited with code {completed.returncode}; "
            f"stderr tail: {_tail(completed.stderr) or _tail(completed.stdout)}"
        )
    return completed


def _tail(text: str | None, *, limit: int = 2_000) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[-limit:]


# ---------------------------------------------------------------------------
# Chain parsing
# ---------------------------------------------------------------------------


def _parse_chain_files(
    *,
    output_prefix: Path,
    parameter_order: list[str],
) -> tuple[list[np.ndarray], dict[str, Any]]:
    """Locate and parse cobaya chain files at ``output_prefix.{n}.txt``.

    Each chain file is whitespace-delimited; cobaya writes the columns in the
    order (see ``cobaya.collection.BaseCollection``)::

        weight  -log(post)  sampled_param_1  sampled_param_2  ...  -log(prior) ...

    i.e. the sampled parameters start at column index 2 (right after weight and
    -log(post)); -log(prior) and the chi2 columns follow *after* the parameters,
    not before them.

    We return one ``ndarray`` per chain shaped ``(n_draws, n_params)`` aligned
    to ``parameter_order``. The first column (the Metropolis-Hastings
    multiplicity weight) is expanded via ``np.repeat`` so each posterior draw
    appears with its true multiplicity — every downstream summary / diagnostic
    then operates on weight-correct draws without carrying a separate weight
    array. (The default mcmc sampler emits temperature-1 integer weights.)

    ``chain_meta`` additionally carries ``best_chi2`` — the minimum of the
    total ``chi2`` column (== -2 ln L) over posterior draws across all chains,
    located via the '#' header cobaya writes in each chain file — or ``None``
    with the reason in ``best_chi2_note`` when it cannot be read from the real
    products (never guessed positionally).
    """
    chain_paths = sorted(output_prefix.parent.glob(f"{output_prefix.name}.*.txt"))
    if not chain_paths:
        raise CobayaParseError(
            f"no cobaya chain files found at {output_prefix.parent}/{output_prefix.name}.*.txt"
        )
    info_path = output_prefix.with_suffix(".input.yaml")
    column_names = _read_chain_column_names(info_path) if info_path.exists() else None

    samples_per_chain: list[np.ndarray] = []
    chi2_minima: list[float] = []
    chi2_unavailable: list[str] = []
    for path in chain_paths:
        try:
            # ndmin=2 so a single-row chain file (the `evaluate` sampler writes
            # exactly one sample) parses as (1, ncols) rather than a 1-D (ncols,)
            # array that would fail the arr.ndim != 2 check below.
            arr = np.loadtxt(path, ndmin=2)
        except Exception as exc:
            raise CobayaParseError(f"failed to parse chain file {path}: {exc}") from exc
        if arr.ndim != 2 or arr.shape[1] < 2 + len(parameter_order):
            raise CobayaParseError(
                f"chain file {path} has unexpected shape {arr.shape}; "
                f"expected at least {2 + len(parameter_order)} columns "
                "(weight, -logpost, params...)."
            )
        if column_names is not None and len(column_names) >= 2 + len(parameter_order):
            indices: list[int] = []
            for name in parameter_order:
                try:
                    indices.append(column_names.index(name))
                except ValueError as exc:
                    raise CobayaParseError(
                        f"parameter {name!r} not in chain column list {column_names}"
                    ) from exc
            samples = arr[:, indices]
        else:
            samples = arr[:, 2 : 2 + len(parameter_order)]
        # P3b (2026-07-07): best-fit chi2 from the run's REAL products, for the
        # fit_statistics the model-comparison pairing consumes. cobaya (>= the
        # pinned 3.6.2; verified against collection._dump_slice__txt AND a live
        # toy-likelihood run) writes a "#"-prefixed header naming every column
        # of the chain .txt, including the total ``chi2`` column (== -2 ln L,
        # summed over likelihoods; per-component values live in chi2__<name>).
        # Minimum is taken over posterior draws only (rounded weight > 0 — the
        # weight-0 burn-in seed point cobaya discards is not a posterior draw;
        # if NO row has positive weight, fall back to all rows, mirroring
        # _expand_by_weight). No header / no chi2 column / a header that does
        # not describe this file's columns -> that chain contributes nothing
        # and the whole-run best_chi2 stays honestly None (a minimum over a
        # subset of chains is not the run's best fit).
        header_columns = _chain_file_header_columns(path)
        if (
            header_columns is not None
            and "chi2" in header_columns
            and len(header_columns) == arr.shape[1]
        ):
            chi2_column = arr[:, header_columns.index("chi2")]
            positive_weight = np.rint(arr[:, 0]) > 0
            chi2_draws = (
                chi2_column[positive_weight] if positive_weight.any() else chi2_column
            )
            chi2_minima.append(float(np.min(chi2_draws)))
        else:
            chi2_unavailable.append(path.name)
        samples = _expand_by_weight(samples, arr[:, 0])
        samples_per_chain.append(samples)

    if chi2_minima and not chi2_unavailable:
        best_chi2: float | None = float(min(chi2_minima))
        best_chi2_note = (
            "minimum of the total chi2 column (-2 ln L) over posterior draws "
            "(weight > 0) across all chains, read from the chain-file header"
        )
    else:
        best_chi2 = None
        best_chi2_note = (
            "total chi2 column not extractable from chain file(s) "
            f"{', '.join(chi2_unavailable)}: no '#' header naming a 'chi2' "
            "column that matches the file's column count"
        )

    chain_meta = {
        "chain_paths": [str(p) for p in chain_paths],
        "n_chains": len(samples_per_chain),
        "n_draws_total": int(sum(s.shape[0] for s in samples_per_chain)),
        "best_chi2": best_chi2,
        "best_chi2_note": best_chi2_note,
    }
    return samples_per_chain, chain_meta


def _expand_by_weight(samples: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Replicate each row of ``samples`` by its integer Metropolis weight.

    Cobaya's first chain column is the multiplicity weight (how many proposed
    steps were rejected while the chain sat on that point). Summarising the
    unweighted unique points biases every posterior median/quantile and makes
    R̂/ESS treat a weight-N point as a single draw. Expanding by the rounded
    integer weight reconstructs the true draw sequence so the existing
    unweighted summary/diagnostic code is correct as-is. Rows with a zero or
    negative weight (cobaya discards the burn-in seed point with weight 0) are
    dropped."""
    counts = np.rint(weights).astype(int)
    counts = np.clip(counts, 0, None)
    if counts.sum() == 0:
        # Degenerate (e.g. an ``evaluate`` run with a single weight-0 row) —
        # fall back to the raw rows so we never return an empty chain.
        return samples
    return np.repeat(samples, counts, axis=0)


def _chain_file_header_columns(path: Path) -> list[str] | None:
    """Column names from the '#'-prefixed header line of a cobaya chain .txt.

    cobaya (pinned 3.6.2, ``collection._dump_slice__txt``) writes the full
    column list — ``weight minuslogpost <params...> minuslogprior[...] chi2
    chi2__<like>...`` — as the first line of every chain file; verified against
    a live toy-likelihood run (2026-07-07). Returns ``None`` when the first
    line is not a '#' header (e.g. hand-rolled or truncated files), in which
    case callers must treat header-dependent columns as unavailable rather
    than guess positions. (The .input.yaml-based ``_read_chain_column_names``
    below only knows the sampled params — it cannot locate the chi2 column.)
    """
    try:
        with open(path, encoding="utf-8") as fh:
            first_line = fh.readline()
    except Exception:
        return None
    if not first_line.startswith("#"):
        return None
    names = first_line.lstrip("#").split()
    return names or None


def _read_chain_column_names(info_path: Path) -> list[str] | None:
    """Best-effort extraction of column names from cobaya's ``.input.yaml``.

    cobaya does not actually emit a column header in the chain ``.txt`` file;
    the canonical mapping is encoded in the ``params:`` block of the dumped
    info YAML. Returning ``None`` falls back to positional indexing
    (weight, -logpost, then the sampled params in order). The sampled
    parameters start at column index 2 in cobaya's layout — the -log(prior)
    and chi2 columns come *after* the parameters, so no placeholder column
    precedes them here.
    """
    try:
        text = info_path.read_text(encoding="utf-8")
    except Exception:
        return None
    names: list[str] = ["weight", "-logpost"]
    in_params = False
    indent_param = None
    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped:
            continue
        if stripped.startswith("params:"):
            in_params = True
            continue
        if in_params:
            indent = len(line) - len(line.lstrip(" "))
            if line[:1] not in {" ", "\t"} and stripped:
                # left-flush => exited params block
                in_params = False
                continue
            if stripped.endswith(":") and (indent_param is None or indent == indent_param):
                indent_param = indent
                names.append(stripped[:-1].strip())
    return names if len(names) > 2 else None


# ---------------------------------------------------------------------------
# Summaries / diagnostics
# ---------------------------------------------------------------------------


def _summarise_samples(
    *,
    samples_per_chain: list[np.ndarray],
    parameter_order: list[str],
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    pooled = np.concatenate(samples_per_chain, axis=0)
    summaries: dict[str, dict[str, float]] = {}
    for index, name in enumerate(parameter_order):
        col = pooled[:, index]
        median = float(np.median(col))
        lo, hi = (float(x) for x in np.quantile(col, [0.16, 0.84]))
        mean = float(np.mean(col))
        std = float(np.std(col, ddof=1)) if col.size > 1 else 0.0
        summaries[name] = {
            "mean": mean,
            "std": std,
            "median": median,
            "q16": lo,
            "q84": hi,
            "lower": lo,
            "upper": hi,
        }
    diagnostics = _compute_diagnostics(samples_per_chain, parameter_order)
    return summaries, diagnostics


def _compute_diagnostics(
    samples_per_chain: list[np.ndarray],
    parameter_order: list[str],
) -> dict[str, Any]:
    """Compute rank-normalized R-hat and per-parameter bulk ESS.

    Fewer than four independent chain files are always preliminary.  In
    particular, a single chain has no defined R-hat; it must never receive the
    historical synthetic value ``1.0``.
    """
    from app.services.cosmology_likelihoods.verification import (
        PUBLICATION_ESS_MIN,
        PUBLICATION_MIN_INDEPENDENT_CHAINS,
        PUBLICATION_RHAT_MAX,
    )

    thresholds = {
        "min_independent_chains": PUBLICATION_MIN_INDEPENDENT_CHAINS,
        "rhat_method": "rank",
        "rhat_max_exclusive": PUBLICATION_RHAT_MAX,
        "ess_method": "bulk",
        "ess_min": PUBLICATION_ESS_MIN,
    }
    n_chains = len(samples_per_chain)
    if n_chains == 0:
        return {
            "overall_status": "no_chains",
            "rhat": None,
            "ess_bulk": None,
            "n_chains": 0,
            "n_independent_chains": 0,
            "per_parameter": {},
            "thresholds": thresholds,
        }
    if n_chains < 2:
        return {
            "overall_status": "single_chain_only",
            "rhat": None,
            "ess_bulk": None,
            "n_chains": 1,
            "n_independent_chains": 1,
            "n_draws": int(samples_per_chain[0].shape[0]),
            "per_parameter": {
                name: {"rhat": None, "ess_bulk": None}
                for name in parameter_order
            },
            "thresholds": thresholds,
        }
    try:
        import arviz as az  # type: ignore

        n_draws = min(s.shape[0] for s in samples_per_chain)
        if n_draws == 0:
            raise CobayaParseError("at least one chain has zero draws")
        stacked = np.stack([s[:n_draws] for s in samples_per_chain], axis=0)
        idata = az.from_dict(
            posterior={
                name: stacked[:, :, idx]
                for idx, name in enumerate(parameter_order)
            }
        )
        rhat_ds = az.rhat(idata, method="rank")
        ess_ds = az.ess(idata, method="bulk")
        per_param = {
            name: {
                "rhat": float(rhat_ds[name].values.item()),
                "ess_bulk": float(ess_ds[name].values.item()),
            }
            for name in parameter_order
        }
        finite = all(
            math.isfinite(p["rhat"]) and math.isfinite(p["ess_bulk"])
            for p in per_param.values()
        )
        rhat_max = max(p["rhat"] for p in per_param.values()) if finite else None
        ess_min = min(p["ess_bulk"] for p in per_param.values()) if finite else None
        diagnostics_ok = bool(
            n_chains >= PUBLICATION_MIN_INDEPENDENT_CHAINS
            and rhat_max is not None
            and rhat_max < PUBLICATION_RHAT_MAX
            and ess_min is not None
            and ess_min >= PUBLICATION_ESS_MIN
        )
        return {
            "overall_status": "ok" if diagnostics_ok else "insufficient",
            "rhat": rhat_max,
            "ess_bulk": ess_min,
            "n_chains": n_chains,
            "n_independent_chains": n_chains,
            "n_draws": int(n_draws),
            "per_parameter": per_param,
            "thresholds": thresholds,
        }
    except Exception as exc:
        logger.warning("arviz diagnostics failed: %s", exc)
        return {
            "overall_status": "diagnostics_unavailable",
            "rhat": None,
            "ess_bulk": None,
            "n_chains": n_chains,
            "n_independent_chains": n_chains,
            "per_parameter": {},
            "thresholds": thresholds,
            "diagnostics_error": f"{type(exc).__name__}: {exc}",
        }


# ---------------------------------------------------------------------------
# Result envelopes
# ---------------------------------------------------------------------------


def _runner_success(
    *,
    model_key: str,
    entries: list[CosmologyDatasetEntry],
    seed: int,
    sampler: str,
    summaries: dict[str, dict[str, float]],
    diagnostics: dict[str, Any],
    chain_meta: dict[str, Any],
    stdout_tail: str,
    data_verification: dict[str, Any] | None = None,
    model_adequacy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.services.cosmology_likelihoods import (
        MODEL_LABELS,
        _collect_citations,
    )
    from app.services.cosmology_oracle import chain_is_off_anchor

    # Off-anchor guard (mirrors the in-process sampling path): a converged
    # extended-model chain whose frontier parameters (w/w0/wa) have no reproduced
    # anchor is never publication-ready, even via the external Cobaya backend —
    # otherwise claim_validator (which keys only on publication_ready) would let
    # its w0/wa be quoted as a published conclusion.
    off_anchor = chain_is_off_anchor(model_key, [entry.key for entry in entries])
    from app.services.cosmology_likelihoods.verification import (
        _assess_publication_gate,
        build_model_adequacy_subject,
    )

    data_verified = bool(
        data_verification is not None and data_verification.get("hash_verified")
    )
    selected_keys = {entry.key for entry in entries}
    verified_dataset_keys = {
        str(key)
        for key in (data_verification or {}).get("verified_dataset_keys", [])
    }
    real_tau_likelihood_selected = bool(
        selected_keys.intersection(TAU_CONSTRAINING_KEYS)
    )
    real_tau_likelihood_verified = bool(
        real_tau_likelihood_selected
        and verified_dataset_keys.intersection(TAU_CONSTRAINING_KEYS)
    )
    tau_is_sampled = "tau" in summaries
    tau_standin_used = tau_is_sampled and not real_tau_likelihood_selected
    tau_likelihood_unverified = (
        tau_is_sampled
        and real_tau_likelihood_selected
        and not real_tau_likelihood_verified
    )
    adequacy_subject = build_model_adequacy_subject(
        model=model_key,
        dataset_keys=[entry.key for entry in entries],
        random_seed=seed,
        summaries=summaries,
        diagnostics=diagnostics,
        data_verification=data_verification,
    )
    publication_gate = _assess_publication_gate(
        cov_fidelity="full" if data_verified else None,
        # When the real low-l EE likelihood is absent, _build_cobaya_yaml uses
        # the hand-entered Planck lowE posterior as a Gaussian tau stand-in.
        # That is scientifically useful for a preliminary high-l/lensing run,
        # but it is a compressed data substitution and cannot satisfy the same
        # gate as the hash-verified lowE likelihood itself.
        likelihood_is_compressed_or_approximate=tau_standin_used,
        n_independent_chains=int(
            diagnostics.get("n_independent_chains")
            or diagnostics.get("n_chains")
            or 0
        ),
        per_parameter=diagnostics.get("per_parameter"),
        critical_parameters=list(summaries),
        model_adequacy=model_adequacy,
        model_adequacy_subject=adequacy_subject,
    )
    if off_anchor and "off_anchor_frontier" not in publication_gate["reasons"]:
        publication_gate["reasons"].append("off_anchor_frontier")
    if tau_likelihood_unverified:
        publication_gate["reasons"].append("tau_constraining_likelihood_unverified")
    publication_gate["eligible"] = not publication_gate["reasons"]
    publication_ready = bool(publication_gate["eligible"])
    preliminary_ready = bool(
        summaries
        and data_verified
        and diagnostics.get("overall_status")
        not in {"no_chains", "diagnostics_unavailable"}
    )
    # Three-tier verdict mirroring the in-process runners, so downstream
    # consumers (compute_model_comparison's validity ladder) can vet external
    # chains instead of refusing them as unvetted. Unverified data or a failed
    # sampler status is 'blocked'; a converged-but-demoted chain (off-anchor,
    # marginal R-hat/ESS) is 'exploratory'.
    chain_tier = (
        "publication"
        if publication_ready
        else "exploratory"
        if preliminary_ready
        else "blocked"
    )
    # P3b (2026-07-07): fit_statistics from the run's real chain products, so
    # external chains can enter model-comparison pairing (research_program.py
    # gates on isinstance(result.get("fit_statistics"), dict); before this the
    # ok_*/mnu models — reachable only via this external CMB path — had no
    # model comparison at all). Semantics mirror the in-process runners:
    # chi2 = minimum chi2 among the actual posterior draws (the chain's total
    # chi2 column, == -2 ln L), aic = chi2 + 2k with k = sampled parameters
    # (nuisance included, as on the in-process path). BIC and n_constraints
    # are honestly ABSENT: the chain products do not record the likelihood
    # data-vector length N and this runner refuses to guess it. When the chi2
    # column could not be read, fit_statistics is omitted entirely (with the
    # reason in warnings) — an envelope without it simply stays out of the
    # pairing, exactly the pre-fix behaviour. NOTE: fit_statistics never
    # upgrades chain_tier/publication_ready — compute_model_comparison's
    # validity ladder still refuses verdicts from blocked/unvetted chains and
    # caveats exploratory ones.
    best_chi2 = chain_meta.get("best_chi2")
    fit_statistics: dict[str, Any] | None = None
    if (
        isinstance(best_chi2, (int, float))
        and not isinstance(best_chi2, bool)
        and math.isfinite(float(best_chi2))
    ):
        n_parameters = len(summaries)
        fit_statistics = {
            "chi2": round(float(best_chi2), 6),
            "chi2_kind": "posterior_draw_minimum",
            "likelihood_only": False,
            "optimizer_converged": False,
            "aic": round(float(best_chi2) + 2.0 * n_parameters, 6),
            "n_parameters": int(n_parameters),
            "chi2_source": chain_meta.get("best_chi2_note"),
            "note": (
                "bic and n_constraints are not reported: the cobaya chain "
                "products do not record the likelihood data-vector length "
                "(N); computing them would require re-instantiating each "
                "likelihood, so they are honestly absent rather than guessed."
            ),
        }
    warnings = [
        "External Cobaya backend run; verify likelihood data versions match the registered citations."
    ]
    if tau_standin_used:
        warnings.append(
            "The real Planck low-l EE likelihood was not selected; tau used the "
            "compressed Gaussian lowE stand-in (0.0544 +/- 0.0073). This run is "
            "preliminary and cannot be publication-ready."
        )
    elif tau_likelihood_unverified:
        warnings.append(
            "The Planck low-l EE likelihood was selected but its pinned inputs "
            "were not individually verified; the tau constraint is preliminary "
            "and cannot be publication-ready."
        )
    if publication_gate["reasons"]:
        warnings.append(
            "Publication gate withheld: "
            + ", ".join(publication_gate["reasons"])
            + ". The posterior is preliminary only."
        )
    if fit_statistics is None:
        warnings.append(
            "fit_statistics omitted: "
            + str(chain_meta.get("best_chi2_note"))
            + " — without a chain-derived chi2 this run cannot enter "
            "model-comparison pairing."
        )
    envelope = {
        "success": True,
        "__tool_status__": "COMPLETED" if publication_ready else "PARTIAL",
        "analysis_status": "EXTERNAL_COBAYA_READY" if publication_ready else "PARTIAL",
        "publication_ready": publication_ready,
        "preliminary_ready": preliminary_ready,
        "publication_gate": publication_gate,
        "preliminary_reasons": list(publication_gate["reasons"]),
        "approximate_likelihood_components": (
            ["planck_lowE_tau_gaussian_standin"] if tau_standin_used else []
        ),
        "chain_tier": chain_tier,
        "off_anchor_review_required": off_anchor,
        "model": model_key,
        "model_label": MODEL_LABELS.get(model_key, model_key),
        "sampler": f"cobaya:{sampler}",
        "parameters": summaries,
        "posterior_summary": summaries,
        "chain_diagnostics": diagnostics,
        "datasets_used": [entry.to_dict() for entry in entries],
        "datasets_not_run": [],
        "dataset_keys": [entry.key for entry in entries],
        "random_seed": seed,
        "warnings": warnings,
        "__message_to_model__": (
            "External Cobaya MCMC produced this posterior. "
            "Publication requires at least four independent chains, rank-normalized "
            "R-hat < 1.01, bulk ESS >= 400 for every critical parameter, and "
            "hash-verified non-compressed likelihood inputs, including the real "
            "low-l EE likelihood when tau is sampled. Otherwise report it only "
            "as preliminary."
        ),
        "provenance": {
            "cosmology_likelihood": {
                "registry_version": "2026-04-30",
                "runner": f"cobaya:{sampler}",
                "dataset_keys": [entry.key for entry in entries],
                "datasets_used": [entry.key for entry in entries],
                "datasets_not_run": [],
                "citations": _collect_citations(entries),
                "publication_ready": publication_ready,
                "preliminary_ready": preliminary_ready,
                "publication_gate": publication_gate,
                "model_adequacy_subject": adequacy_subject,
                "chain_meta": chain_meta,
                "stdout_tail": stdout_tail,
                "data_verification": data_verification,
                "tau_constraint": {
                    "mode": (
                        "compressed_lowE_gaussian_standin"
                        if tau_standin_used
                        else "selected_lowE_likelihood_unverified"
                        if tau_likelihood_unverified
                        else "verified_lowE_likelihood_or_tau_not_sampled"
                    ),
                    "publication_eligible": not (
                        tau_standin_used or tau_likelihood_unverified
                    ),
                },
            }
        },
    }
    if fit_statistics is not None:
        envelope["fit_statistics"] = fit_statistics
    return envelope


def _runner_failure(
    *,
    model_key: str,
    entries: list[CosmologyDatasetEntry],
    seed: int,
    error_class: str,
    message: str,
) -> dict[str, Any]:
    from app.services.cosmology_likelihoods import (
        MODEL_LABELS,
        _collect_citations,
    )

    return {
        "success": True,
        "__tool_status__": "PARTIAL",
        "analysis_status": "EXTERNAL_COBAYA_NOT_RUN",
        "publication_ready": False,
        "__do_not_claim__": True,
        "model": model_key,
        "model_label": MODEL_LABELS.get(model_key, model_key),
        "sampler": "cobaya:not_run",
        "dataset_keys": [entry.key for entry in entries],
        "datasets": [entry.to_dict() for entry in entries],
        "datasets_used": [],
        "datasets_not_run": [entry.to_dict() for entry in entries],
        "random_seed": seed,
        "warnings": [message],
        "error_class": error_class,
        "__message_to_model__": (
            f"External Cobaya backend did not produce a usable posterior "
            f"(error_class={error_class}). {message} "
            "Do not quote posterior constraints, S8/H0/Omega_m tensions, "
            "AIC/BIC, or significance from this result."
        ),
        "provenance": {
            "cosmology_likelihood": {
                "registry_version": "2026-04-30",
                "runner": "cobaya:not_run",
                "error_class": error_class,
                "dataset_keys": [entry.key for entry in entries],
                "datasets_used": [],
                "datasets_not_run": [entry.key for entry in entries],
                "citations": _collect_citations(entries),
                "publication_ready": False,
            }
        },
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env_truthy(name: str) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _cobaya_import_ok() -> bool:
    try:
        import cobaya  # noqa: F401
    except Exception as exc:
        logger.debug("cobaya import probe failed: %s", exc)
        return False
    return True

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
    },
}


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
            if f is None or not f.is_file():
                mismatches.append(f"{relpath}: file missing")
                continue
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            files_sha256[relpath] = digest
            if digest != pins.get(role):
                mismatches.append(f"{relpath}: sha256 mismatch")
    return {
        "packages_path": packages_path,
        "hash_verified": not mismatches,
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
            return _runner_success(
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

    lines.append("params:")
    for name in parameter_order:
        low, high = prior_bounds[name]
        lines.append(f"  {name}:")
        if name in COBAYA_GAUSSIAN_PRIORS:
            loc, scale = COBAYA_GAUSSIAN_PRIORS[name]
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

    lines.append("sampler:")
    if sampler == "evaluate":
        lines.append("  evaluate: null")
    elif sampler == "mcmc":
        lines.append("  mcmc:")
        lines.append("    Rminus1_stop: 0.05")
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
    """
    chain_paths = sorted(output_prefix.parent.glob(f"{output_prefix.name}.*.txt"))
    if not chain_paths:
        raise CobayaParseError(
            f"no cobaya chain files found at {output_prefix.parent}/{output_prefix.name}.*.txt"
        )
    info_path = output_prefix.with_suffix(".input.yaml")
    column_names = _read_chain_column_names(info_path) if info_path.exists() else None

    samples_per_chain: list[np.ndarray] = []
    for path in chain_paths:
        try:
            arr = np.loadtxt(path)
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
        samples = _expand_by_weight(samples, arr[:, 0])
        samples_per_chain.append(samples)

    chain_meta = {
        "chain_paths": [str(p) for p in chain_paths],
        "n_chains": len(samples_per_chain),
        "n_draws_total": int(sum(s.shape[0] for s in samples_per_chain)),
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
    """Compute R̂ / ESS via arviz; fall back to a single-chain ``rhat=1.0``."""
    n_chains = len(samples_per_chain)
    if n_chains == 0:
        return {"overall_status": "no_chains", "rhat": None, "ess_bulk": None}
    if n_chains < 2:
        return {
            "overall_status": "single_chain_only",
            "rhat": 1.0,
            "ess_bulk": int(samples_per_chain[0].shape[0]),
            "n_chains": 1,
            "n_draws": int(samples_per_chain[0].shape[0]),
            "thresholds": {"ess_min": 400, "rhat_max": 1.05},
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
        rhat_ds = az.rhat(idata)
        ess_ds = az.ess(idata)
        per_param = {
            name: {
                "rhat": float(rhat_ds[name].values.item()),
                "ess_bulk": float(ess_ds[name].values.item()),
            }
            for name in parameter_order
        }
        rhat_max = max(p["rhat"] for p in per_param.values())
        ess_min = min(p["ess_bulk"] for p in per_param.values())
        return {
            "overall_status": "ok" if rhat_max <= 1.05 and ess_min >= 400 else "insufficient",
            "rhat": rhat_max,
            "ess_bulk": ess_min,
            "n_chains": n_chains,
            "n_draws": int(n_draws),
            "per_parameter": per_param,
            "thresholds": {"ess_min": 400, "rhat_max": 1.05},
        }
    except Exception as exc:
        logger.warning("arviz diagnostics failed: %s", exc)
        return {
            "overall_status": "diagnostics_unavailable",
            "rhat": None,
            "ess_bulk": None,
            "n_chains": n_chains,
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
    publication_ready = (
        diagnostics.get("overall_status") == "ok"
        and (diagnostics.get("rhat") or 0.0) <= 1.05
        and (diagnostics.get("ess_bulk") or 0.0) >= 400
        and not off_anchor
        # a pinned CMB run is publication-ready only if its data sha256-verified
        and not (data_verification is not None and not data_verification.get("hash_verified"))
    )
    return {
        "success": True,
        "__tool_status__": "COMPLETED" if publication_ready else "PARTIAL",
        "analysis_status": "EXTERNAL_COBAYA_READY" if publication_ready else "PARTIAL",
        "publication_ready": publication_ready,
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
        "warnings": [
            "External Cobaya backend run; verify likelihood data versions match the registered citations."
        ],
        "__message_to_model__": (
            "External Cobaya MCMC produced this posterior. "
            "publication_ready is the union of R̂ <= 1.05, ess_bulk >= 400, and overall_status == 'ok'."
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
                "chain_meta": chain_meta,
                "stdout_tail": stdout_tail,
                "data_verification": data_verification,
            }
        },
    }


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

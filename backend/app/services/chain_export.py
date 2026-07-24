"""Persist posterior chains as downloadable getdist artifacts.

Chains were previously discarded after summary extraction, leaving nothing a
cosmologist could load into getdist, replot, or attach to a referee reply.
This module renders getdist-format files from a sampling payload and uploads
them to durable object storage under ``chains/{owner_id}/{run_id}/``.

Two payload shapes are supported:

- in-process (``samples``): equal-weight resampled draws; the .txt columns
  are weight=1 and -loglike=0, declared in-band by a ``#`` header comment on
  the chain file itself and out-of-band by ``loglike_available: false`` in
  the returned block. Real per-sample likelihoods are not available on this
  path and are never faked.
- external cobaya (``raw_files``): the sampler's own chain files (true
  weights and -logpost columns) captured verbatim before the run's tempdir
  is deleted; ``.paramnames``/``.ranges`` sidecars are generated.

Persistence is atomic per run: files are staged for cleanup, uploaded, and
their cleanup grace renewed only after EVERY upload succeeded. On any
failure the block reports ``persist_failed`` with an empty file list — the
partially uploaded strays stay staged and the artifact janitor removes them,
so a broken triplet is never registered or advertised. Fail-open by design:
a storage failure must never discard the completed scientific result (see
durable_research_records for the precedent), but it must be labelled.

Blocked-tier chains are never exported: the runner redacts their parameter
summaries, and the sampling layer withholds the payload for that tier so a
download cannot bypass the redaction.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_CHAIN_HEADER = (
    "# Standard Astro chain export: equal-weight resampled draws; the\n"
    "# -loglike column is 0 because per-sample likelihoods are not available\n"
    "# on the in-process sampling path (loglike_available=false).\n"
)

# getdist parameter labels for the axes this runner can sample or derive.
_PARAM_LABELS: dict[str, str] = {
    "H0": r"H_0",
    "omegam": r"\Omega_m",
    "rd": r"r_d",
    "sigma8": r"\sigma_8",
    "S8": r"S_8",
    "w": r"w",
    "w0": r"w_0",
    "wa": r"w_a",
    "M_B": r"M_B",
    "H0_rd": r"H_0 r_d",
    "ombh2": r"\Omega_b h^2",
    "omch2": r"\Omega_c h^2",
    "ns": r"n_s",
    "As": r"A_s",
    "tau": r"\tau",
    "A_planck": r"A_{\rm planck}",
}


def _paramnames_bytes(names: list[str]) -> bytes:
    lines = []
    for name in names:
        bare = name.rstrip("*")
        label = _PARAM_LABELS.get(bare, bare)
        lines.append(f"{name}\t{label}")
    return ("\n".join(lines) + "\n").encode()


def _ranges_bytes(
    parameter_order: list[str], prior_bounds: dict[str, tuple[float, float]]
) -> bytes | None:
    lines = []
    for name in parameter_order:
        bounds = prior_bounds.get(name)
        if bounds is None:
            continue
        lines.append(f"{name} {bounds[0]:.8g} {bounds[1]:.8g}")
    if not lines:
        return None
    return ("\n".join(lines) + "\n").encode()


def render_getdist_files(payload: dict[str, Any]) -> dict[str, bytes]:
    """Render a chain payload as getdist-format file bytes."""
    parameter_order: list[str] = list(payload["parameter_order"])
    prior_bounds: dict[str, tuple[float, float]] = payload.get("prior_bounds") or {}

    raw_files = payload.get("raw_files")
    if raw_files:
        # External cobaya path: the sampler's own chain files are already
        # getdist-native; pass them through verbatim and add the sidecars.
        files = {str(name): bytes(data) for name, data in raw_files.items()}
        files["chain.paramnames"] = _paramnames_bytes(parameter_order)
        ranges = _ranges_bytes(parameter_order, prior_bounds)
        if ranges:
            files["chain.ranges"] = ranges
        return files

    samples = np.asarray(payload["samples"], dtype=float)
    if samples.ndim != 2 or samples.shape[1] != len(parameter_order):
        raise ValueError("chain payload shape does not match parameter order")

    derived_raw = payload.get("derived_samples") or {}
    for name, values in derived_raw.items():
        if np.asarray(values).shape != (samples.shape[0],):
            # A shape mismatch is a producer bug; dropping it silently would
            # publish a triplet missing a column the tool result advertises.
            raise ValueError(f"derived sample '{name}' does not match chain length")
    derived = {
        name: np.asarray(values, dtype=float) for name, values in derived_raw.items()
    }

    columns = [samples[:, index] for index in range(samples.shape[1])]
    names = list(parameter_order)
    for name, values in derived.items():
        if name in names:
            continue
        columns.append(values)
        names.append(f"{name}*")  # getdist convention: derived params end in *

    table = np.column_stack(
        [np.ones(samples.shape[0]), np.zeros(samples.shape[0]), *columns]
    )
    txt = _CHAIN_HEADER + "\n".join(
        " ".join(f"{value:.8e}" for value in row) for row in table
    ) + "\n"

    files = {
        "chain_1.txt": txt.encode(),
        "chain.paramnames": _paramnames_bytes(names),
    }
    ranges = _ranges_bytes(parameter_order, prior_bounds)
    if ranges:
        files["chain.ranges"] = ranges
    return files


def persist_chain_artifacts(
    payload: dict[str, Any],
    *,
    owner_id: str,
) -> dict[str, Any]:
    """Upload the getdist files; return the ``chain_downloads`` result block.

    Every file entry carries ``output_path`` so the tool dispatcher's existing
    artifact-registration pass records it in the DataFile ledger (ownership +
    account-deletion GC) without any new plumbing. Registration and renewal
    happen only for complete uploads — see the module docstring.
    """
    run_id = str(uuid.uuid4())
    failed: dict[str, Any] = {"run_id": run_id, "status": "persist_failed", "files": []}
    try:
        rendered = render_getdist_files(payload)
    except Exception as exc:
        logger.warning("chain artifact rendering failed: %s", exc)
        return failed

    from app.services.artifact_cleanup import (
        renew_artifact_cleanup_grace_sync,
        stage_artifact_cleanup_sync,
    )
    from app.storage import get_storage_metadata, upload_fits

    uploaded: list[dict[str, Any]] = []
    try:
        for name, data in rendered.items():
            key = f"chains/{owner_id}/{run_id}/{name}"
            stage_artifact_cleanup_sync(
                key,
                user_id=owner_id,
                reason_class="uncommitted_chain_artifact",
            )
            upload_fits(key, data)
            metadata = get_storage_metadata(key)
            uploaded.append(
                {
                    "name": name,
                    "output_path": key,
                    "sha256": metadata.get("sha256"),
                    "size_bytes": len(data),
                }
            )
    except Exception as exc:
        # Atomic fail-open: report nothing as persisted. Already-uploaded
        # strays were staged but never renewed, so the artifact janitor
        # removes them — a partial triplet is never registered or offered.
        logger.warning("chain artifact upload failed: %s", exc)
        return failed

    for entry in uploaded:
        renew_artifact_cleanup_grace_sync(entry["output_path"])

    return {
        "run_id": run_id,
        "status": "persisted",
        "format": "getdist",
        "loglike_available": bool(payload.get("raw_files")),
        "sampler": payload.get("sampler"),
        "seed": payload.get("seed"),
        "files": uploaded,
    }

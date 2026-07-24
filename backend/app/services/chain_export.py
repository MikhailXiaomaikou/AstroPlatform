"""Persist posterior chains as downloadable getdist artifacts.

Chains were previously discarded after summary extraction, leaving nothing a
cosmologist could load into getdist, replot, or attach to a referee reply.
This module writes the getdist triplet (.txt / .paramnames / .ranges) from an
in-process sampling payload and uploads it to durable object storage under
``chains/{owner_id}/{run_id}/``.

Persistence is deliberately fail-open: a storage failure must never discard
the only completed scientific output (see durable_research_records for the
precedent), but it must be labelled — ``status`` is ``persist_failed``, never
a silent success. The chain files are reproducibility artifacts, not claim
evidence; the anti-fabrication gates neither read nor trust them.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

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


def render_getdist_files(payload: dict[str, Any]) -> dict[str, bytes]:
    """Render an in-process chain payload as getdist triplet bytes.

    The in-process samplers return equal-weight resampled draws without
    per-sample log-likelihoods, so the .txt columns are weight=1 and
    -loglike=0; ``chains.json`` records ``loglike_available: false`` so a
    consumer never mistakes the zeros for real likelihood values.
    """
    samples = np.asarray(payload["samples"], dtype=float)
    parameter_order: list[str] = list(payload["parameter_order"])
    if samples.ndim != 2 or samples.shape[1] != len(parameter_order):
        raise ValueError("chain payload shape does not match parameter order")

    derived = {
        name: np.asarray(values, dtype=float)
        for name, values in (payload.get("derived_samples") or {}).items()
        if np.asarray(values).shape == (samples.shape[0],)
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
    txt = "\n".join(
        " ".join(f"{value:.8e}" for value in row) for row in table
    ) + "\n"

    paramnames_lines = []
    for name in names:
        bare = name.rstrip("*")
        label = _PARAM_LABELS.get(bare, bare)
        paramnames_lines.append(f"{name}\t{label}")
    paramnames = "\n".join(paramnames_lines) + "\n"

    prior_bounds: dict[str, tuple[float, float]] = payload.get("prior_bounds") or {}
    ranges_lines = []
    for name in parameter_order:
        bounds = prior_bounds.get(name)
        if bounds is None:
            continue
        ranges_lines.append(f"{name} {bounds[0]:.8g} {bounds[1]:.8g}")
    ranges = "\n".join(ranges_lines) + "\n" if ranges_lines else ""

    files = {
        "chain_1.txt": txt.encode(),
        "chain.paramnames": paramnames.encode(),
    }
    if ranges:
        files["chain.ranges"] = ranges.encode()
    return files


def persist_chain_artifacts(
    payload: dict[str, Any],
    *,
    owner_id: str,
) -> dict[str, Any]:
    """Upload the getdist triplet; return the ``chain_artifacts`` result block.

    Every file entry carries ``output_path`` so the tool dispatcher's existing
    artifact-registration pass records it in the DataFile ledger (ownership +
    account-deletion GC) without any new plumbing.
    """
    run_id = str(uuid.uuid4())
    try:
        rendered = render_getdist_files(payload)
    except Exception as exc:
        logger.warning("chain artifact rendering failed: %s", exc)
        return {"run_id": run_id, "status": "persist_failed", "files": []}

    from app.services.artifact_cleanup import (
        renew_artifact_cleanup_grace_sync,
        stage_artifact_cleanup_sync,
    )
    from app.storage import get_storage_metadata, upload_fits

    files: list[dict[str, Any]] = []
    status = "persisted"
    for name, data in rendered.items():
        key = f"chains/{owner_id}/{run_id}/{name}"
        try:
            stage_artifact_cleanup_sync(
                key,
                user_id=owner_id,
                reason_class="uncommitted_chain_artifact",
            )
            upload_fits(key, data)
            renew_artifact_cleanup_grace_sync(key)
            metadata = get_storage_metadata(key)
            files.append(
                {
                    "name": name,
                    "output_path": key,
                    "sha256": metadata.get("sha256"),
                    "size_bytes": len(data),
                }
            )
        except Exception as exc:
            # Fail-open with a loud label: the scientific result outranks the
            # convenience artifact, but a gap must never look like coverage.
            logger.warning("chain artifact upload failed for %s: %s", key, exc)
            status = "persist_failed"

    return {
        "run_id": run_id,
        "status": status if files else "persist_failed",
        "format": "getdist",
        "loglike_available": False,
        "sampler": payload.get("sampler"),
        "seed": payload.get("seed"),
        "files": files,
    }

"""Fail-closed runner for non-formal Workflow Foundry demonstrations.

Candidate bundles are data, never executable instructions.  Every permitted
``entrypoint_id`` is bound in this module, and every result is normalized to a
non-formal contract that cannot support a Claim Audit or Evidence Pack.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import os
import platform
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:  # ``resource`` is unavailable on native Windows Python.
    import resource as _resource
except ImportError:  # pragma: no cover - exercised by Windows CI/clients
    _resource = None


CANDIDATE_SCHEMA_VERSION = 1
NON_FORMAL_EVIDENCE_CLASS = "NON_FORMAL_DEMO"
DEMO_STATUSES = frozenset({"PASSED", "PARTIAL", "FAILED"})
_CANDIDATE_ID = re.compile(r"^[a-z][a-z0-9_]{2,96}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_STREAM_CAPTURE_LIMIT_BYTES = 1024 * 1024


class FoundryDemoContractError(ValueError):
    """A candidate or result attempted to leave the non-formal lane."""


class _BoundedTextCapture(io.TextIOBase):
    """Capture at most one UTF-8 byte budget while accepting further writes.

    Candidate output is untrusted.  ``StringIO`` grows without a limit, so a
    noisy candidate could exhaust the Validation Runner before the container
    resource limit terminates it.  This sink keeps a deterministic prefix and
    separately counts all bytes the candidate attempted to write.
    """

    def __init__(self, limit_bytes: int = _STREAM_CAPTURE_LIMIT_BYTES) -> None:
        super().__init__()
        self._limit_bytes = limit_bytes
        self._captured = bytearray()
        self.observed_bytes = 0
        self.truncated = False

    @property
    def encoding(self) -> str:  # pragma: no cover - TextIO protocol metadata
        return "utf-8"

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        text = str(value)
        encoded = text.encode("utf-8", errors="replace")
        self.observed_bytes += len(encoded)
        remaining = self._limit_bytes - len(self._captured)
        if remaining > 0:
            self._captured.extend(encoded[:remaining])
        if len(encoded) > remaining:
            self.truncated = True
        return len(text)

    def get_bytes(self) -> bytes:
        return bytes(self._captured)


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FoundryDemoContractError("candidate_payload_not_canonical_json") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _contains_formal_claim_escape(value: Any) -> bool:
    """Detect formal-evidence fields at any depth in candidate output."""

    if isinstance(value, list):
        return any(_contains_formal_claim_escape(item) for item in value)
    if not isinstance(value, dict):
        return False
    for raw_key, item in value.items():
        key = str(raw_key).strip().lower()
        if key in {"publication_ready", "claim_eligible"} and item is True:
            return True
        if key == "scientific_verdict" and str(item).upper() == "SUPPORTED":
            return True
        if key in {
            "evidence_pack",
            "evidence_pack_id",
            "formal_evidence_pack",
        } and item:
            return True
        if _contains_formal_claim_escape(item):
            return True
    return False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_candidate_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Return a defensive copy of one declarative, non-formal candidate."""

    if not isinstance(bundle, dict):
        raise FoundryDemoContractError("candidate_bundle_must_be_object")
    required = {
        "schema_version",
        "candidate_id",
        "candidate_version",
        "proposed_workflow_id",
        "entrypoint_id",
        "risk_level",
        "workflow_spec",
        "source_pins",
        "fixture_hashes",
        "dependency_lock_sha256",
        "runner_definition_sha256",
        "generation",
        "limitations",
        "output_policy",
    }
    if set(bundle) != required:
        raise FoundryDemoContractError("candidate_bundle_shape_not_registered")
    if bundle.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
        raise FoundryDemoContractError("candidate_schema_version_unsupported")
    candidate_id = str(bundle.get("candidate_id") or "")
    workflow_id = str(bundle.get("proposed_workflow_id") or "")
    if not _CANDIDATE_ID.fullmatch(candidate_id):
        raise FoundryDemoContractError("candidate_id_invalid")
    if not _CANDIDATE_ID.fullmatch(workflow_id):
        raise FoundryDemoContractError("candidate_workflow_id_invalid")
    version = bundle.get("candidate_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise FoundryDemoContractError("candidate_version_invalid")
    if bundle.get("risk_level") not in {"R0", "R1", "R2", "R3"}:
        raise FoundryDemoContractError("candidate_risk_level_invalid")
    for field in ("dependency_lock_sha256", "runner_definition_sha256"):
        if not _HEX64.fullmatch(str(bundle.get(field) or "")):
            raise FoundryDemoContractError(f"candidate_{field}_invalid")
    if not isinstance(bundle.get("workflow_spec"), dict):
        raise FoundryDemoContractError("candidate_workflow_spec_invalid")
    if not isinstance(bundle.get("source_pins"), list) or not bundle["source_pins"]:
        raise FoundryDemoContractError("candidate_source_pins_required")
    if not isinstance(bundle.get("fixture_hashes"), list):
        raise FoundryDemoContractError("candidate_fixture_hashes_invalid")
    if not isinstance(bundle.get("generation"), dict):
        raise FoundryDemoContractError("candidate_generation_invalid")
    if not isinstance(bundle.get("limitations"), list) or not bundle["limitations"]:
        raise FoundryDemoContractError("candidate_limitations_required")
    output_policy = bundle.get("output_policy")
    if output_policy != {
        "evidence_class": NON_FORMAL_EVIDENCE_CLASS,
        "publication_ready": False,
        "claim_eligible": False,
        "evidence_pack_allowed": False,
    }:
        raise FoundryDemoContractError("candidate_output_policy_not_non_formal")
    if str(bundle.get("entrypoint_id") or "") not in _ENTRYPOINTS:
        raise FoundryDemoContractError("candidate_entrypoint_not_allowlisted")
    return json.loads(_canonical_json(bundle))


def load_candidate_bundle(path: str | Path) -> dict[str, Any]:
    candidate_path = Path(path).resolve()
    try:
        payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FoundryDemoContractError("candidate_bundle_unreadable") from exc
    return validate_candidate_bundle(payload)


def _run_desi_dr2_official_chain_summary(
    bundle: dict[str, Any],
    *,
    cache_root: str | Path | None,
) -> dict[str, Any]:
    from app.services.cosmology_likelihoods.analysis_registry import (
        audit_cosmology_analysis_registry,
    )
    from app.services.cosmology_likelihoods.dark_energy_matrix import (
        run_dark_energy_evidence_matrix,
    )

    issues = audit_cosmology_analysis_registry()
    if issues:
        return {
            "status": "FAILED",
            "failure_class": "official_registry_integrity_failed",
            "result": {"registry_issues": issues, "parameter_intervals": {}},
            "validation_summary": {
                "registry_integrity": False,
                "official_mirror_verified": False,
                "ready_cells": 0,
            },
        }

    workflow_spec = bundle["workflow_spec"]
    inputs = workflow_spec.get("demo_inputs") or {}
    prior_root = os.environ.get("DESI_DR2_OFFICIAL_CHAIN_ROOT")
    configured_root = cache_root if cache_root is not None else prior_root
    if cache_root is not None:
        os.environ["DESI_DR2_OFFICIAL_CHAIN_ROOT"] = str(cache_root)
    try:
        matrix = run_dark_energy_evidence_matrix(
            model=str(inputs.get("model") or "w0wa_cdm"),
            supernova_sets=list(inputs.get("supernova_sets") or ["union3"]),
            include_desi_dr1_reference=False,
        )
    finally:
        if cache_root is not None:
            if prior_root is None:
                os.environ.pop("DESI_DR2_OFFICIAL_CHAIN_ROOT", None)
            else:
                os.environ["DESI_DR2_OFFICIAL_CHAIN_ROOT"] = prior_root

    ready_cells = int(matrix.get("official_ready_cells") or 0)
    withheld_cells = int(matrix.get("official_withheld_cells") or 0)
    mirror_was_configured = bool(str(configured_root or "").strip())
    withheld_reasons = sorted(
        {
            str(reason)
            for cell in matrix.get("matrix") or []
            if isinstance(cell, dict)
            for reason in cell.get("withheld_reasons") or []
            if str(reason).strip()
        }
    )
    if ready_cells > 0 and withheld_cells == 0:
        status = "PASSED"
        failure_class = None
    elif mirror_was_configured:
        # A configured cache that is missing, corrupt, schema-incompatible, or
        # scientifically invalid is not equivalent to the operator declining
        # to provide a mirror.  Preserve the failed receipt, but make the public
        # wrapper return non-zero so an integrity failure cannot look like the
        # expected no-mirror demonstration.
        status = "FAILED"
        failure_class = "official_chain_mirror_integrity_failed"
    else:
        status = "PARTIAL"
        failure_class = "official_chain_mirror_unavailable"
    return {
        "status": status,
        "failure_class": failure_class,
        "result": {
            "analysis_status": matrix.get("analysis_status"),
            "official_ready_cells": ready_cells,
            "official_withheld_cells": withheld_cells,
            "matrix": matrix.get("matrix") or [],
            "provenance": matrix.get("provenance") or {},
            "parameter_intervals_are_non_formal": True,
        },
        "validation_summary": {
            "registry_integrity": True,
            "official_mirror_verified": ready_cells > 0,
            "official_mirror_configured": mirror_was_configured,
            "ready_cells": ready_cells,
            "withheld_cells": withheld_cells,
            "withheld_reasons": withheld_reasons,
            "numeric_claim_gate": "NON_FORMAL_DEMO",
        },
    }


def _run_generated_candidate_validation(
    bundle: dict[str, Any],
    *,
    cache_root: str | Path | None,
) -> dict[str, Any]:
    """Load one candidate module only inside the isolated Validation image.

    ``candidate_id`` is already constrained to lower-case identifier syntax.
    The fixed namespace prevents a bundle from selecting an arbitrary module.
    Formal Workers do not expose this entrypoint.
    """

    candidate_id = str(bundle["candidate_id"])
    module_name = f"app.services.foundry_generated.{candidate_id}"
    module = importlib.import_module(module_name)
    entrypoint = getattr(module, "run_demo", None)
    if not callable(entrypoint):
        raise FoundryDemoContractError("generated_candidate_run_demo_missing")
    result = entrypoint(bundle, cache_root=cache_root)
    if not isinstance(result, dict):
        raise FoundryDemoContractError("generated_candidate_result_not_object")
    return result


_ENTRYPOINTS: dict[
    str,
    Callable[..., dict[str, Any]],
] = {
    "desi_dr2_official_chain_summary_demo_v1": (
        _run_desi_dr2_official_chain_summary
    ),
    "candidate_generated_python_demo_v1": _run_generated_candidate_validation,
}


def run_candidate_demo(
    bundle: dict[str, Any],
    *,
    cache_root: str | Path | None = None,
    runner_image_digest: str | None = None,
    candidate_version_sha256: str | None = None,
    started_at: datetime | None = None,
    captured_streams: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """Run an allowlisted candidate and return an immutable DemoReport body."""

    normalized = validate_candidate_bundle(bundle)
    started = started_at or _utc_now()
    demo_run_id = str(uuid.uuid4())
    entrypoint = _ENTRYPOINTS[normalized["entrypoint_id"]]
    stdout_buffer = _BoundedTextCapture()
    stderr_buffer = _BoundedTextCapture()
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(
        stderr_buffer
    ):
        try:
            outcome = entrypoint(normalized, cache_root=cache_root)
        except Exception as exc:  # fail closed; never expose a Python traceback
            outcome = {
                "status": "FAILED",
                "failure_class": exc.__class__.__name__,
                "result": {},
                "validation_summary": {"runner_exception": True},
            }
    status = str(outcome.get("status") or "FAILED").upper()
    if status not in DEMO_STATUSES:
        raise FoundryDemoContractError("candidate_demo_status_invalid")
    result = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
    if _contains_formal_claim_escape(result) or _contains_formal_claim_escape(
        outcome.get("validation_summary")
    ):
        status = "FAILED"
        result = {}
        outcome["failure_class"] = "candidate_formal_claim_escape_blocked"
        outcome["validation_summary"] = {"formal_claim_escape_blocked": True}
    completed = _utc_now()
    stdout_bytes = stdout_buffer.get_bytes()
    stderr_bytes = stderr_buffer.get_bytes()
    if captured_streams is not None:
        captured_streams.clear()
        captured_streams.update(
            {"stdout.log": stdout_bytes, "stderr.log": stderr_bytes}
        )
    usage = (
        _resource.getrusage(_resource.RUSAGE_SELF)
        if _resource is not None
        else None
    )
    environment = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "tool_version": str(
            os.getenv("TOOL_VERSION") or os.getenv("GIT_COMMIT") or "unknown"
        ),
        "entrypoint_id": normalized["entrypoint_id"],
    }
    report = {
        "schema_version": 1,
        "candidate_id": normalized["candidate_id"],
        "candidate_version": normalized["candidate_version"],
        "demo_run_id": demo_run_id,
        "status": status,
        "evidence_class": NON_FORMAL_EVIDENCE_CLASS,
        "publication_ready": False,
        "claim_eligible": False,
        "evidence_pack_allowed": False,
        "candidate_bundle_sha256": _sha256(normalized),
        "candidate_version_sha256": str(
            candidate_version_sha256 or _sha256(normalized)
        ),
        "workflow_spec_sha256": _sha256(normalized["workflow_spec"]),
        "dependency_lock_sha256": normalized["dependency_lock_sha256"],
        "runner_definition_sha256": normalized["runner_definition_sha256"],
        "runner_image_digest": str(runner_image_digest or "unavailable"),
        "environment": environment,
        "environment_sha256": _sha256(environment),
        "generation": normalized["generation"],
        "source_pins": normalized["source_pins"],
        "fixture_hashes": normalized["fixture_hashes"],
        "started_at": _iso(started),
        "completed_at": _iso(completed),
        "duration_ms": max(0, int((completed - started).total_seconds() * 1000)),
        "stdout_sha256": _sha256_bytes(stdout_bytes),
        "stderr_sha256": _sha256_bytes(stderr_bytes),
        "stdout_bytes": len(stdout_bytes),
        "stderr_bytes": len(stderr_bytes),
        "artifact_manifest": [
            {
                "path": "stdout.log",
                "kind": "STDOUT",
                "sha256": _sha256_bytes(stdout_bytes),
                "bytes": len(stdout_bytes),
            },
            {
                "path": "stderr.log",
                "kind": "STDERR",
                "sha256": _sha256_bytes(stderr_bytes),
                "bytes": len(stderr_bytes),
            },
        ],
        "resource_usage": {
            "max_rss_kib_platform_value": (
                int(usage.ru_maxrss) if usage is not None else None
            ),
            "user_cpu_seconds": (
                round(float(usage.ru_utime), 6) if usage is not None else None
            ),
            "system_cpu_seconds": (
                round(float(usage.ru_stime), 6) if usage is not None else None
            ),
            "stream_capture_limit_bytes": _STREAM_CAPTURE_LIMIT_BYTES,
            "stdout_observed_bytes": stdout_buffer.observed_bytes,
            "stderr_observed_bytes": stderr_buffer.observed_bytes,
            "stdout_truncated": stdout_buffer.truncated,
            "stderr_truncated": stderr_buffer.truncated,
        },
        "failure_class": outcome.get("failure_class"),
        "validation_summary": outcome.get("validation_summary") or {},
        "limitations": list(normalized["limitations"]),
        "result": result,
    }
    report["demo_report_sha256"] = _sha256(report)
    return report


__all__ = [
    "CANDIDATE_SCHEMA_VERSION",
    "DEMO_STATUSES",
    "FoundryDemoContractError",
    "NON_FORMAL_EVIDENCE_CLASS",
    "load_candidate_bundle",
    "run_candidate_demo",
    "validate_candidate_bundle",
]

"""Typed, immutable registry for formal scientific workflows.

The built-in registry is compiled with the application release. Database
records may describe Foundry candidates, but they never extend the runtime
registry. A candidate becomes executable only after a protected signer emits
a byte-pinned release JSON and startup verifies that release against an
external trusted keyring and the image's static entrypoint table.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Final, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.services.evidence_pack_v2 import jcs_canonicalize
from app.services.worker_contract import canonical_json


RegistryState = Literal["REGISTERED", "SUSPENDED", "SUPERSEDED", "REVOKED"]
RiskLevel = Literal["R0", "R1", "R2", "R3", "R4"]
ExecutionBackend = Literal[
    "https_worker",
    "control_plane",
    "reference_verifier",
]

REGISTRY_SCHEMA_VERSION: Final = "standard_astro_workflow_registry_v2"
REGISTRY_EPOCH: Final = "2026-07-21.1"
REGISTRY_STATUS: Final = "ACTIVE"
REQUIREMENTS_LOCK_SHA256: Final = (
    "1ad3021413d90076bfca5105f360cc53c2a716b59b2f828cc95e64715771529d"
)
CONTROL_IMAGE_BUILD_SPEC_SHA256: Final = (
    "04bce6bb5c0f2842ccff2aa8080a9dc9496732a18abc791515876d3f6bb62852"
)
WORKER_IMAGE_BUILD_SPEC_SHA256: Final = (
    "d9323265164c0cef9b3e57a5ec8b3425bd3c3a9999c0b246ebeb62a489a95075"
)

UNION3_REPRODUCTION_WORKFLOW_ID: Final = "union3_flat_lcdm_sn_only_v1"
DESI_DR2_MATRIX_WORKFLOW_ID: Final = "desi_dr2_dark_energy_matrix_v1"

UNION3_PRIMARY_ENTRYPOINT_ID: Final = "union3.primary_reproduction.v1"
UNION3_VERIFIER_ENTRYPOINT_ID: Final = "union3.independent_verification.v1"
DESI_DR2_MATRIX_ENTRYPOINT_ID: Final = "run_dark_energy_evidence_matrix"
DESI_DR2_CONTRACT_VERIFIER_ENTRYPOINT_ID: Final = (
    "desi_dr2.matrix_contract_verifier.v1"
)

UNION3_REGISTERED_CLAIM: Final = (
    "Using the released Union3/UNITY1.5 22-node distance product and full "
    "covariance, the supernova-only flat-LambdaCDM profile-chi-square "
    "calculation reproduces omega_m = 0.356 (+0.028/-0.026) at 68.3% "
    "confidence (Delta chi-square = 1), consistent with Rubin et al. Table 9."
)
UNION3_RADIATION_CONVENTION: Final = "omega_r_zero_author_code_approximation"
UNION3_VECTOR_SHA256: Final = (
    "a840fe71c606bda11b869dbfcacc21c0199a5dc393f3790d10a7b58de97deae7"
)
UNION3_COVARIANCE_SHA256: Final = (
    "64c79abd24bf5154bc1e38ad0c031e31dd6247cdcc5ca930829698169809a146"
)
UNION3_PDF_SHA256: Final = (
    "6a8fccccecfc083d24c07f508d15ba273ebec1333fe4702d226520ffbaa603c9"
)

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,127}$")
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_RELEASE_FILE_BYTES: Final = 8 * 1024 * 1024
_EXECUTABLE_STATES = frozenset({"REGISTERED"})
_REGISTRY_STATES = frozenset({"REGISTERED", "SUSPENDED", "SUPERSEDED", "REVOKED"})
_RISK_LEVELS = frozenset({"R0", "R1", "R2", "R3", "R4"})
_EXECUTION_BACKENDS = frozenset(
    {"https_worker", "control_plane", "reference_verifier"}
)
_TOOL_PERMISSION_KEYS = frozenset(
    {
        "arbitrary_code",
        "shell",
        "network",
        "production_credentials",
        "dataset_keys",
        "read_pinned_chain_cache",
    }
)


class WorkflowRegistryError(ValueError):
    """A stable, fail-closed formal-registry rejection."""

    def __init__(self, code: str, *, status_code: int = 422):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _deepcopy_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(dict(value))


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One fixed primitive available to a compiled workflow."""

    tool_id: str
    version: str
    entrypoint_id: str
    execution_backend: ExecutionBackend
    code_binding: Mapping[str, Any]
    dependency_lock_sha256: str
    image_build_spec_sha256: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    permissions: Mapping[str, Any]
    provenance_policy: Mapping[str, Any]
    claim_policy: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "version": self.version,
            "entrypoint_id": self.entrypoint_id,
            "execution_backend": self.execution_backend,
            "code_binding": _deepcopy_mapping(self.code_binding),
            "dependency_lock_sha256": self.dependency_lock_sha256,
            "image_build_spec_sha256": self.image_build_spec_sha256,
            "input_schema": _deepcopy_mapping(self.input_schema),
            "output_schema": _deepcopy_mapping(self.output_schema),
            "permissions": _deepcopy_mapping(self.permissions),
            "provenance_policy": _deepcopy_mapping(self.provenance_policy),
            "claim_policy": _deepcopy_mapping(self.claim_policy),
        }


@dataclass(frozen=True, slots=True)
class WorkflowToolRef:
    """A node in a deterministic workflow DAG."""

    node_id: str
    tool_id: str
    tool_version: str
    entrypoint_id: str
    depends_on: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WorkflowSpec:
    """A versioned scientific contract compiled into the formal registry."""

    workflow_id: str
    version: str
    display_name: str
    summary: str
    state: RegistryState
    risk_level: RiskLevel
    execution_backend: ExecutionBackend
    source_profiles: tuple[str, ...]
    claim_types: tuple[str, ...]
    dataset_pins: tuple[Mapping[str, str], ...]
    tool_dag: tuple[WorkflowToolRef, ...]
    primary_entrypoint_id: str
    independent_verifier_entrypoint_id: str | None
    science_contract: Mapping[str, Any]
    acceptance: Mapping[str, Any]
    output_policy: Mapping[str, Any]
    allowed_claims: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    evidence_pack_requirements: tuple[str, ...]
    catalog_visible: bool = True
    local_worker_executable: bool = False
    revocation_reason: str | None = None
    legacy_contract: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self, *, include_legacy_contract: bool = False) -> dict[str, Any]:
        result = {
            "workflow_id": self.workflow_id,
            "version": self.version,
            "display_name": self.display_name,
            "summary": self.summary,
            "state": self.state,
            "risk_level": self.risk_level,
            "execution_backend": self.execution_backend,
            "source_profiles": list(self.source_profiles),
            "claim_types": list(self.claim_types),
            "dataset_pins": [dict(pin) for pin in self.dataset_pins],
            "tool_dag": [node.to_dict() for node in self.tool_dag],
            "primary_entrypoint_id": self.primary_entrypoint_id,
            "independent_verifier_entrypoint_id": (
                self.independent_verifier_entrypoint_id
            ),
            "science_contract": _deepcopy_mapping(self.science_contract),
            "acceptance": _deepcopy_mapping(self.acceptance),
            "output_policy": _deepcopy_mapping(self.output_policy),
            "allowed_claims": list(self.allowed_claims),
            "forbidden_claims": list(self.forbidden_claims),
            "evidence_pack_requirements": list(self.evidence_pack_requirements),
            "catalog_visible": self.catalog_visible,
            "local_worker_executable": self.local_worker_executable,
            "revocation_reason": self.revocation_reason,
        }
        if include_legacy_contract:
            result["legacy_contract"] = _deepcopy_mapping(self.legacy_contract)
        return result


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    """One deterministic formal-registry release."""

    schema_version: str
    epoch: str
    status: str
    tools: tuple[ToolSpec, ...]
    workflows: tuple[WorkflowSpec, ...]
    workflow_entry_hashes: Mapping[str, str]
    workflow_release_bindings: Mapping[str, Mapping[str, Any]]
    registry_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "epoch": self.epoch,
            "status": self.status,
            "tools": [tool.to_dict() for tool in self.tools],
            "workflows": [workflow.to_dict() for workflow in self.workflows],
            "workflow_entry_hashes": dict(self.workflow_entry_hashes),
            "workflow_release_bindings": {
                key: _deepcopy_mapping(value)
                for key, value in self.workflow_release_bindings.items()
            },
            "registry_hash": self.registry_hash,
        }


def _validate_identifier(value: str, *, field_name: str) -> None:
    if not _IDENTIFIER_RE.fullmatch(value) or value.startswith("app."):
        raise WorkflowRegistryError(f"invalid_{field_name}")


def _validate_tool(tool: ToolSpec) -> None:
    _validate_identifier(tool.tool_id, field_name="tool_id")
    _validate_identifier(tool.entrypoint_id, field_name="entrypoint_id")
    if not _SEMVER_RE.fullmatch(tool.version):
        raise WorkflowRegistryError("invalid_tool_version")
    if tool.execution_backend not in _EXECUTION_BACKENDS:
        raise WorkflowRegistryError("invalid_tool_execution_backend")
    if tool.code_binding != {"kind": "release_git_commit", "required": True}:
        raise WorkflowRegistryError("invalid_tool_code_binding")
    if not _SHA256_RE.fullmatch(tool.dependency_lock_sha256):
        raise WorkflowRegistryError("invalid_dependency_lock_sha256")
    if not _SHA256_RE.fullmatch(tool.image_build_spec_sha256):
        raise WorkflowRegistryError("invalid_image_build_spec_sha256")
    if not set(tool.permissions).issubset(_TOOL_PERMISSION_KEYS):
        raise WorkflowRegistryError("unknown_tool_permission")
    for permission in (
        "arbitrary_code",
        "shell",
        "network",
        "production_credentials",
    ):
        if tool.permissions.get(permission) is not False:
            raise WorkflowRegistryError(f"{permission}_permission_forbidden")
    if tool.claim_policy != {
        "may_write_supported": False,
        "publication_ready": False,
    }:
        raise WorkflowRegistryError("invalid_tool_claim_policy")
    for schema in (tool.input_schema, tool.output_schema):
        if schema.get("type") != "object":
            raise WorkflowRegistryError("invalid_tool_schema")


def _validate_dataset_pins(pins: Sequence[Mapping[str, str]]) -> None:
    keys: set[str] = set()
    for pin in pins:
        key = str(pin.get("key") or "")
        digest = str(pin.get("sha256") or "").removeprefix("sha256:")
        if not key or key in keys or not _SHA256_RE.fullmatch(digest):
            raise WorkflowRegistryError("invalid_dataset_pin")
        keys.add(key)


def _validate_workflow(
    workflow: WorkflowSpec,
    *,
    tools_by_identity: Mapping[tuple[str, str], ToolSpec],
) -> None:
    _validate_identifier(workflow.workflow_id, field_name="workflow_id")
    if not _SEMVER_RE.fullmatch(workflow.version):
        raise WorkflowRegistryError("invalid_workflow_version")
    if workflow.state not in _REGISTRY_STATES:
        raise WorkflowRegistryError("invalid_workflow_state")
    if workflow.risk_level not in _RISK_LEVELS:
        raise WorkflowRegistryError("invalid_workflow_risk_level")
    if workflow.risk_level == "R4":
        raise WorkflowRegistryError("r4_workflow_forbidden")
    if workflow.execution_backend not in _EXECUTION_BACKENDS:
        raise WorkflowRegistryError("invalid_workflow_execution_backend")
    if not workflow.source_profiles or not workflow.claim_types:
        raise WorkflowRegistryError("workflow_scope_missing")
    if workflow.state == "REVOKED" and not workflow.revocation_reason:
        raise WorkflowRegistryError("revoked_workflow_missing_reason")
    if workflow.state != "REVOKED" and workflow.revocation_reason:
        raise WorkflowRegistryError("active_workflow_has_revocation_reason")
    _validate_dataset_pins(workflow.dataset_pins)
    nodes = {node.node_id: node for node in workflow.tool_dag}
    if not nodes or len(nodes) != len(workflow.tool_dag):
        raise WorkflowRegistryError("invalid_workflow_dag")
    entrypoints: set[str] = set()
    for node in workflow.tool_dag:
        _validate_identifier(node.node_id, field_name="workflow_node_id")
        tool = tools_by_identity.get((node.tool_id, node.tool_version))
        if tool is None or tool.entrypoint_id != node.entrypoint_id:
            raise WorkflowRegistryError("workflow_tool_binding_invalid")
        if any(parent not in nodes or parent == node.node_id for parent in node.depends_on):
            raise WorkflowRegistryError("workflow_dependency_invalid")
        entrypoints.add(node.entrypoint_id)
    pending = set(nodes)
    resolved: set[str] = set()
    while pending:
        ready = {
            node_id
            for node_id in pending
            if set(nodes[node_id].depends_on).issubset(resolved)
        }
        if not ready:
            raise WorkflowRegistryError("workflow_dependency_cycle")
        pending -= ready
        resolved |= ready
    if workflow.primary_entrypoint_id not in entrypoints:
        raise WorkflowRegistryError("workflow_primary_entrypoint_unbound")
    primary_tool = next(
        tool
        for tool in tools_by_identity.values()
        if tool.entrypoint_id == workflow.primary_entrypoint_id
    )
    if primary_tool.execution_backend != workflow.execution_backend:
        raise WorkflowRegistryError("workflow_execution_backend_mismatch")
    if (
        workflow.independent_verifier_entrypoint_id is not None
        and workflow.independent_verifier_entrypoint_id not in entrypoints
    ):
        raise WorkflowRegistryError("workflow_verifier_entrypoint_unbound")
    if workflow.risk_level in {"R2", "R3"}:
        verifier_entrypoint = workflow.independent_verifier_entrypoint_id
        if (
            verifier_entrypoint is None
            or verifier_entrypoint == workflow.primary_entrypoint_id
        ):
            raise WorkflowRegistryError("independent_verifier_required")
        verifier_tool = next(
            tool
            for tool in tools_by_identity.values()
            if tool.entrypoint_id == verifier_entrypoint
        )
        if verifier_tool.execution_backend != "reference_verifier":
            raise WorkflowRegistryError("independent_verifier_backend_invalid")
        if (
            workflow.risk_level == "R3"
            and verifier_tool.provenance_policy.get(
                "independent_implementation_required"
            )
            is not True
        ):
            raise WorkflowRegistryError("r3_independent_implementation_required")
    if workflow.output_policy.get("publication_ready") is not False:
        raise WorkflowRegistryError("publication_ready_workflow_forbidden")


def compile_registry(
    *,
    tools: Sequence[ToolSpec],
    workflows: Sequence[WorkflowSpec],
    epoch: str = REGISTRY_EPOCH,
    status: str = REGISTRY_STATUS,
    workflow_release_bindings: Mapping[str, Mapping[str, Any]] | None = None,
) -> RegistrySnapshot:
    """Validate and deterministically hash a registry release."""

    ordered_tools = tuple(sorted(tools, key=lambda item: (item.tool_id, item.version)))
    ordered_workflows = tuple(
        sorted(workflows, key=lambda item: (item.workflow_id, item.version))
    )
    tool_identities = [(tool.tool_id, tool.version) for tool in ordered_tools]
    if len(tool_identities) != len(set(tool_identities)):
        raise WorkflowRegistryError("duplicate_tool_version")
    entrypoints = [tool.entrypoint_id for tool in ordered_tools]
    if len(entrypoints) != len(set(entrypoints)):
        raise WorkflowRegistryError("duplicate_entrypoint_id")
    for tool in ordered_tools:
        _validate_tool(tool)
    tools_by_identity = dict(zip(tool_identities, ordered_tools, strict=True))

    workflow_identities = [
        (workflow.workflow_id, workflow.version) for workflow in ordered_workflows
    ]
    if len(workflow_identities) != len(set(workflow_identities)):
        raise WorkflowRegistryError("duplicate_workflow_version")
    for workflow in ordered_workflows:
        _validate_workflow(workflow, tools_by_identity=tools_by_identity)

    release_bindings = {
        str(key): _deepcopy_mapping(value)
        for key, value in (workflow_release_bindings or {}).items()
    }
    valid_entry_keys = {
        f"{workflow.workflow_id}@{workflow.version}"
        for workflow in ordered_workflows
    }
    if not set(release_bindings).issubset(valid_entry_keys):
        raise WorkflowRegistryError("orphan_workflow_release_binding")

    entry_hashes: dict[str, str] = {}
    for workflow in ordered_workflows:
        referenced = {
            (node.tool_id, node.tool_version) for node in workflow.tool_dag
        }
        entry_key = f"{workflow.workflow_id}@{workflow.version}"
        entry_payload = {
            "workflow": workflow.to_dict(),
            "tools": [
                tools_by_identity[identity].to_dict()
                for identity in sorted(referenced)
            ],
            "release_binding": release_bindings.get(entry_key),
        }
        entry_hashes[entry_key] = (
            "sha256:" + hashlib.sha256(canonical_json(entry_payload)).hexdigest()
        )

    payload = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "epoch": epoch,
        "status": status,
        "tools": [tool.to_dict() for tool in ordered_tools],
        "workflows": [workflow.to_dict() for workflow in ordered_workflows],
        "workflow_entry_hashes": entry_hashes,
        "workflow_release_bindings": release_bindings,
    }
    registry_hash = "sha256:" + hashlib.sha256(canonical_json(payload)).hexdigest()
    return RegistrySnapshot(
        schema_version=REGISTRY_SCHEMA_VERSION,
        epoch=epoch,
        status=status,
        tools=ordered_tools,
        workflows=ordered_workflows,
        workflow_entry_hashes=entry_hashes,
        workflow_release_bindings=release_bindings,
        registry_hash=registry_hash,
    )


def _union3_dataset_pins() -> tuple[dict[str, str], ...]:
    return (
        {"key": "union3_lcparam_full", "sha256": UNION3_VECTOR_SHA256},
        {"key": "union3_mag_covmat", "sha256": UNION3_COVARIANCE_SHA256},
        {"key": "union3_arxiv_2311_12098v4_pdf", "sha256": UNION3_PDF_SHA256},
    )


def _desi_dr2_dataset_pins() -> tuple[dict[str, str], ...]:
    from app.services.cosmology_likelihoods.analysis_registry import (
        DESI_DR2_CHAIN_MANIFEST_SHA256,
        list_cosmology_analyses,
    )

    pins = [
        {
            "key": "desi_dr2_official_chain_manifest_v1",
            "sha256": DESI_DR2_CHAIN_MANIFEST_SHA256,
        }
    ]
    for analysis in list_cosmology_analyses(model="w0wa_cdm"):
        for artifact in analysis.artifacts:
            pins.append(
                {
                    "key": f"{analysis.key}:{artifact.relative_path}",
                    "sha256": artifact.sha256,
                }
            )
    return tuple(pins)


_NO_CODE_PERMISSIONS: Final = {
    "arbitrary_code": False,
    "shell": False,
    "network": False,
    "production_credentials": False,
}
_RELEASE_CODE_BINDING: Final = {
    "kind": "release_git_commit",
    "required": True,
}

_FORMAL_TOOLS: Final[tuple[ToolSpec, ...]] = (
    ToolSpec(
        tool_id="union3_primary_reproduction",
        version="1.0.0",
        entrypoint_id=UNION3_PRIMARY_ENTRYPOINT_ID,
        execution_backend="https_worker",
        code_binding=_RELEASE_CODE_BINDING,
        dependency_lock_sha256=REQUIREMENTS_LOCK_SHA256,
        image_build_spec_sha256=WORKER_IMAGE_BUILD_SPEC_SHA256,
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object"},
        permissions={**_NO_CODE_PERMISSIONS, "dataset_keys": ["union3"]},
        provenance_policy={"current_run_required": True, "dataset_pins_required": True},
        claim_policy={"may_write_supported": False, "publication_ready": False},
    ),
    ToolSpec(
        tool_id="union3_independent_verification",
        version="1.0.0",
        entrypoint_id=UNION3_VERIFIER_ENTRYPOINT_ID,
        execution_backend="reference_verifier",
        code_binding=_RELEASE_CODE_BINDING,
        dependency_lock_sha256=REQUIREMENTS_LOCK_SHA256,
        image_build_spec_sha256=CONTROL_IMAGE_BUILD_SPEC_SHA256,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        permissions={**_NO_CODE_PERMISSIONS, "dataset_keys": ["union3"]},
        provenance_policy={"independent_implementation_required": True},
        claim_policy={"may_write_supported": False, "publication_ready": False},
    ),
    ToolSpec(
        tool_id="desi_dr2_dark_energy_matrix",
        version="1.0.0",
        entrypoint_id=DESI_DR2_MATRIX_ENTRYPOINT_ID,
        execution_backend="control_plane",
        code_binding=_RELEASE_CODE_BINDING,
        dependency_lock_sha256=REQUIREMENTS_LOCK_SHA256,
        image_build_spec_sha256=CONTROL_IMAGE_BUILD_SPEC_SHA256,
        input_schema={
            "type": "object",
            "properties": {
                "model": {"const": "w0wa_cdm"},
                "supernova_sets": {
                    "const": ["pantheon_plus", "union3", "des_sn5yr"]
                },
                "include_desi_dr1_reference": {"const": False},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        permissions={
            **_NO_CODE_PERMISSIONS,
            "dataset_keys": ["desi_dr2_bao"],
            "read_pinned_chain_cache": True,
        },
        provenance_policy={"official_manifest_required": True},
        claim_policy={"may_write_supported": False, "publication_ready": False},
    ),
    ToolSpec(
        tool_id="desi_dr2_matrix_contract_verifier",
        version="1.0.0",
        entrypoint_id=DESI_DR2_CONTRACT_VERIFIER_ENTRYPOINT_ID,
        execution_backend="reference_verifier",
        code_binding=_RELEASE_CODE_BINDING,
        dependency_lock_sha256=REQUIREMENTS_LOCK_SHA256,
        image_build_spec_sha256=CONTROL_IMAGE_BUILD_SPEC_SHA256,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        permissions=_NO_CODE_PERMISSIONS,
        provenance_policy={"server_owned_result_required": True},
        claim_policy={"may_write_supported": False, "publication_ready": False},
    ),
)


_UNION3_LEGACY_CONTRACT: Final = {
    "workflow_id": UNION3_REPRODUCTION_WORKFLOW_ID,
    "workflow_version": "1.0.0",
    "primary_executor": UNION3_PRIMARY_ENTRYPOINT_ID,
    "independent_verifier": UNION3_VERIFIER_ENTRYPOINT_ID,
    "entrypoint_id": UNION3_PRIMARY_ENTRYPOINT_ID,
    "dataset_key": "union3",
    "model": "flat_lcdm",
    "parameter": "omega_m",
    "statistic": "profile_chi_square",
    "claim_scope": "reproduction_of_published_constraint",
    "sources": {
        "paper": {
            "identifier": "arXiv:2311.12098v4",
            "url": "https://arxiv.org/abs/2311.12098v4",
            "locator": "PDF Table 9, Flat LambdaCDM, SNe row",
            "pdf_sha256": UNION3_PDF_SHA256,
        },
        "execution_files": {
            "repository": "CobayaSampler/sn_data",
            "commit": "261e3564f532964b83647ae88b3d2eb01a015257",
            "measurement_vector_sha256": UNION3_VECTOR_SHA256,
            "covariance_sha256": UNION3_COVARIANCE_SHA256,
        },
        "author_execution_convention": {
            "repository": "rubind/union3_release",
            "release": "v1.0",
            "commit": "7f805c9cc4e7643f0392faad03a275094501f8a2",
            "path": "stan_code_fixed.txt",
            "sha256": (
                "ed156b739f7bffd208f1dec48ddbe7039b1c88c8ac85ee3703195ae4e5f23c2d"
            ),
        },
    },
    "method": {
        "omega_m_min": "0.05",
        "omega_m_max": "0.80",
        "grid_step": "0.0005",
        "refinement": "Brent minimum and Delta-chi-square=1 roots",
        "magnitude_offset": "analytically_eliminated",
        "confidence_level": "0.683",
        "radiation_convention": UNION3_RADIATION_CONVENTION,
    },
    "dataset_pins": list(_union3_dataset_pins()),
    "acceptance": {
        "profile_points": "41",
        "max_normalized_chi2_difference": "0.0001",
        "max_best_or_endpoint_difference": "0.0002",
        "paper_center_max_sigma": "0.1",
        "paper_interval_width_max_relative_difference": "0.05",
        "paper_chi2_max_absolute_difference": "0.2",
        "paper_degrees_of_freedom": "20",
    },
    "output_policy": {
        "reproduction_ready_requires_all_gates": True,
        "publication_ready": False,
        "scientific_verdict_before_human_review": "WITHHELD",
        "supported_requires_different_human_reviewer": True,
        "numeric_encoding": "decimal_strings",
        "mcmc_diagnostics": "not_applicable",
    },
}


_DESI_TOOL_ARGUMENTS: Final = {
    "model": "w0wa_cdm",
    "supernova_sets": ["pantheon_plus", "union3", "des_sn5yr"],
    "include_desi_dr1_reference": False,
}

_DESI_LEGACY_CONTRACT: Final = {
    "workflow_id": DESI_DR2_MATRIX_WORKFLOW_ID,
    "workflow_version": "1.0.0",
    "primary_executor": DESI_DR2_MATRIX_ENTRYPOINT_ID,
    "independent_verifier": DESI_DR2_CONTRACT_VERIFIER_ENTRYPOINT_ID,
    "entrypoint_id": DESI_DR2_MATRIX_ENTRYPOINT_ID,
    "dataset_key": "desi_dr2_bao",
    "model": "w0wa_cdm",
    "supernova_sets": ["pantheon_plus", "union3", "des_sn5yr"],
    "claim_scope": "published_external_chain_summary_context",
    "tool_arguments": copy.deepcopy(_DESI_TOOL_ARGUMENTS),
    "dataset_pins": list(_desi_dr2_dataset_pins()),
    "output_policy": {
        "publication_ready": False,
        "do_not_claim": True,
        "scientific_verdict": "WITHHELD",
    },
}


_FORMAL_WORKFLOWS: Final[tuple[WorkflowSpec, ...]] = (
    WorkflowSpec(
        workflow_id=UNION3_REPRODUCTION_WORKFLOW_ID,
        version="1.0.0",
        display_name="Union3 flat-LambdaCDM SN-only reproduction",
        summary="Reproduce the published Union3 Table 9 omega_m interval.",
        state="REGISTERED",
        risk_level="R3",
        execution_backend="https_worker",
        source_profiles=("union3_arxiv_v1",),
        claim_types=("PARAMETER_INTERVAL_REPRODUCTION",),
        dataset_pins=_union3_dataset_pins(),
        tool_dag=(
            WorkflowToolRef(
                node_id="primary",
                tool_id="union3_primary_reproduction",
                tool_version="1.0.0",
                entrypoint_id=UNION3_PRIMARY_ENTRYPOINT_ID,
            ),
            WorkflowToolRef(
                node_id="independent_verifier",
                tool_id="union3_independent_verification",
                tool_version="1.0.0",
                entrypoint_id=UNION3_VERIFIER_ENTRYPOINT_ID,
                depends_on=("primary",),
            ),
        ),
        primary_entrypoint_id=UNION3_PRIMARY_ENTRYPOINT_ID,
        independent_verifier_entrypoint_id=UNION3_VERIFIER_ENTRYPOINT_ID,
        science_contract=_UNION3_LEGACY_CONTRACT["method"],
        acceptance=_UNION3_LEGACY_CONTRACT["acceptance"],
        output_policy=_UNION3_LEGACY_CONTRACT["output_policy"],
        allowed_claims=("reproduction_of_published_constraint",),
        forbidden_claims=(
            "new_discovery",
            "h0_measurement",
            "posterior_claim",
            "publication_ready",
        ),
        evidence_pack_requirements=(
            "primary_analysis.json",
            "independent_analysis.json",
            "reviews.json",
        ),
        local_worker_executable=True,
        legacy_contract=_UNION3_LEGACY_CONTRACT,
    ),
    WorkflowSpec(
        workflow_id=DESI_DR2_MATRIX_WORKFLOW_ID,
        version="1.0.0",
        display_name="DESI DR2 dark-energy evidence matrix",
        summary="Validate and summarize three official DESI DR2 w0wa chains.",
        state="SUSPENDED",
        risk_level="R2",
        execution_backend="control_plane",
        source_profiles=("desi_dr2_official_chains_v1",),
        claim_types=("PARAMETER_INTERVAL_REPORT", "COMPARISON_REPORT"),
        dataset_pins=_desi_dr2_dataset_pins(),
        tool_dag=(
            WorkflowToolRef(
                node_id="official_chain_matrix",
                tool_id="desi_dr2_dark_energy_matrix",
                tool_version="1.0.0",
                entrypoint_id=DESI_DR2_MATRIX_ENTRYPOINT_ID,
            ),
            WorkflowToolRef(
                node_id="contract_verifier",
                tool_id="desi_dr2_matrix_contract_verifier",
                tool_version="1.0.0",
                entrypoint_id=DESI_DR2_CONTRACT_VERIFIER_ENTRYPOINT_ID,
                depends_on=("official_chain_matrix",),
            ),
        ),
        primary_entrypoint_id=DESI_DR2_MATRIX_ENTRYPOINT_ID,
        independent_verifier_entrypoint_id=(
            DESI_DR2_CONTRACT_VERIFIER_ENTRYPOINT_ID
        ),
        science_contract={
            "tool_arguments": copy.deepcopy(_DESI_TOOL_ARGUMENTS),
            "analysis_kind": "published_external_posterior_chain_summary",
            "shared_data_tension_policy": "correlated_tension_withheld",
        },
        acceptance={
            "expected_cells": 3,
            "official_manifest_required": True,
            "all_artifacts_checksum_verified": True,
        },
        output_policy=_DESI_LEGACY_CONTRACT["output_policy"],
        allowed_claims=("published_external_chain_summary_context",),
        forbidden_claims=(
            "publication_ready",
            "independent_tension_sigma",
            "likelihood_execution_claim",
        ),
        evidence_pack_requirements=("registered_workflows", "support_artifacts"),
        legacy_contract=_DESI_LEGACY_CONTRACT,
    ),
)


_BUILTIN_SNAPSHOT: Final = compile_registry(
    tools=_FORMAL_TOOLS,
    workflows=_FORMAL_WORKFLOWS,
)


_EXECUTION_ADAPTER_METADATA_FIELDS: Final = frozenset(
    {
        "workflow_id",
        "version",
        "display_name",
        "summary",
        "state",
        "revocation_reason",
    }
)
_STATIC_EXECUTION_ADAPTER_WORKFLOWS: Final = {
    "union3_profile_chi_square_v1": UNION3_REPRODUCTION_WORKFLOW_ID,
}


def _execution_adapter_contract(workflow: WorkflowSpec) -> dict[str, Any]:
    """Return the scientific/runtime fields that a static adapter implements.

    A signed Registry entry may rename or re-version a shipped workflow, but it
    cannot reinterpret the static Python entrypoint.  Operational state and
    human-facing labels are deliberately excluded; every scientific, data,
    DAG, policy, risk, and execution field remains in the comparison.
    """

    contract = workflow.to_dict()
    for field_name in _EXECUTION_ADAPTER_METADATA_FIELDS:
        contract.pop(field_name, None)
    return contract


def _static_execution_adapter_for_spec(workflow: WorkflowSpec) -> dict[str, Any]:
    observed = _execution_adapter_contract(workflow)
    builtin_by_id = {
        item.workflow_id: item for item in _BUILTIN_SNAPSHOT.workflows
    }
    for adapter_id, canonical_workflow_id in sorted(
        _STATIC_EXECUTION_ADAPTER_WORKFLOWS.items()
    ):
        canonical = builtin_by_id[canonical_workflow_id]
        if observed == _execution_adapter_contract(canonical):
            return {
                "execution_adapter_id": adapter_id,
                "canonical_workflow_id": canonical.workflow_id,
                "primary_entrypoint_id": canonical.primary_entrypoint_id,
                "independent_verifier_entrypoint_id": (
                    canonical.independent_verifier_entrypoint_id
                ),
            }
    raise WorkflowRegistryError("workflow_execution_adapter_not_static")


def get_static_execution_adapter_binding(
    workflow_id: str,
    version: str | None = None,
) -> dict[str, Any]:
    """Resolve an active Registry identity to one shipped scientific adapter."""

    workflow = get_formal_workflow_spec(workflow_id, version)
    return _static_execution_adapter_for_spec(workflow)


def get_static_execution_adapter_contract(adapter_id: str) -> dict[str, Any]:
    """Return the immutable image-side contract for one shipped adapter."""

    canonical_workflow_id = _STATIC_EXECUTION_ADAPTER_WORKFLOWS.get(adapter_id)
    if canonical_workflow_id is None:
        raise WorkflowRegistryError("workflow_execution_adapter_not_static")
    workflow = next(
        item
        for item in _BUILTIN_SNAPSHOT.workflows
        if item.workflow_id == canonical_workflow_id
    )
    tool = next(
        item
        for item in _BUILTIN_SNAPSHOT.tools
        if item.entrypoint_id == workflow.primary_entrypoint_id
    )
    return {
        "execution_adapter_id": adapter_id,
        "canonical_workflow_id": workflow.workflow_id,
        "primary_entrypoint_id": workflow.primary_entrypoint_id,
        "independent_verifier_entrypoint_id": (
            workflow.independent_verifier_entrypoint_id
        ),
        "dataset_pins": [dict(pin) for pin in workflow.dataset_pins],
        "tool_spec_hash": _tool_spec_hash(tool),
    }


def _tool_spec_hash(tool: ToolSpec) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(tool.to_dict())).hexdigest()


def list_static_worker_entrypoint_capabilities(
    *,
    worker_image_digest: str,
) -> list[dict[str, str]]:
    """Describe code-static Worker entrypoints without reading a signed Registry.

    Formal images are built before a Registry release is signed.  They therefore
    advertise only code that is physically present in the image; the control
    plane later maps signed workflow aliases onto these immutable capabilities.
    """

    digest = str(worker_image_digest or "unknown").strip().lower()
    if digest != "unknown" and not _IMAGE_DIGEST_RE.fullmatch(digest):
        raise WorkflowRegistryError("invalid_worker_image_digest")
    builtin_tools = {
        tool.entrypoint_id: tool for tool in _BUILTIN_SNAPSHOT.tools
    }
    entrypoints = {
        next(
            workflow.primary_entrypoint_id
            for workflow in _BUILTIN_SNAPSHOT.workflows
            if workflow.workflow_id == canonical_workflow_id
        )
        for canonical_workflow_id in _STATIC_EXECUTION_ADAPTER_WORKFLOWS.values()
    }
    return [
        {
            "entrypoint_id": entrypoint_id,
            "tool_spec_hash": _tool_spec_hash(builtin_tools[entrypoint_id]),
            "worker_image_digest": digest,
        }
        for entrypoint_id in sorted(entrypoints)
    ]


def _version_sort_key(version: str) -> tuple[tuple[int, int, str], ...]:
    """Numeric-aware ordering so "1.10.0" outranks "1.2.0" (plain string
    sort puts "1.10.0" first)."""

    return tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part)
        for part in re.split(r"[.\-+]", version)
    )


def _workflow_indexes(
    snapshot: RegistrySnapshot,
) -> tuple[
    dict[tuple[str, str], WorkflowSpec],
    dict[str, WorkflowSpec],
]:
    by_identity = {
        (workflow.workflow_id, workflow.version): workflow
        for workflow in snapshot.workflows
    }
    by_id: dict[str, WorkflowSpec] = {}
    for workflow_id in sorted({item.workflow_id for item in snapshot.workflows}):
        versions = [
            item for item in snapshot.workflows if item.workflow_id == workflow_id
        ]
        active = [item for item in versions if item.state == "REGISTERED"]
        if len(active) > 1:
            raise WorkflowRegistryError("multiple_registered_workflow_versions")
        by_id[workflow_id] = max(
            active or versions,
            key=lambda item: _version_sort_key(item.version),
        )
    return by_identity, by_id


_REGISTRY_LOCK: Final = threading.RLock()
_SNAPSHOT = _BUILTIN_SNAPSHOT
_WORKFLOWS_BY_IDENTITY, _WORKFLOWS_BY_ID = _workflow_indexes(_SNAPSHOT)
_TOOLS_BY_ENTRYPOINT = {item.entrypoint_id: item for item in _SNAPSHOT.tools}
_REGISTRY_LOOKUP_STARTED = False
_REGISTRY_ACTIVATED = False
_ACTIVE_RELEASE_TRUST: dict[str, Any] = {
    "release_kind": "builtin_code_registry",
    "signature_algorithm": None,
    "signature_key_id": None,
    "signed_payload_sha256": None,
}


def _active_registry() -> tuple[
    RegistrySnapshot,
    Mapping[tuple[str, str], WorkflowSpec],
    Mapping[str, WorkflowSpec],
    Mapping[str, ToolSpec],
]:
    global _REGISTRY_LOOKUP_STARTED
    with _REGISTRY_LOCK:
        _REGISTRY_LOOKUP_STARTED = True
        return (
            _SNAPSHOT,
            _WORKFLOWS_BY_IDENTITY,
            _WORKFLOWS_BY_ID,
            _TOOLS_BY_ENTRYPOINT,
        )


def registry_snapshot() -> dict[str, Any]:
    """Return a defensive, JSON-safe copy of the compiled registry."""

    snapshot, _, _, _ = _active_registry()
    return snapshot.to_dict()


def builtin_registry_identity() -> dict[str, str]:
    """Return the immutable code-registry identity used by every overlay.

    Foundry releases are complete cumulative overlays.  They always replay
    from this fixed base, even after a previous signed release is active, so a
    newly signed file can be verified and loaded by a fresh process without
    needing any older release file.
    """

    return {
        "schema_version": _BUILTIN_SNAPSHOT.schema_version,
        "registry_epoch": _BUILTIN_SNAPSHOT.epoch,
        "registry_hash": _BUILTIN_SNAPSHOT.registry_hash,
        "status": _BUILTIN_SNAPSHOT.status,
    }


def active_registry_identity() -> dict[str, Any]:
    """Return non-secret release identity shared by API, workers and health."""

    snapshot, _, _, _ = _active_registry()
    return {
        "schema_version": snapshot.schema_version,
        "registry_epoch": snapshot.epoch,
        "registry_hash": snapshot.registry_hash,
        "status": snapshot.status,
        "release_kind": _ACTIVE_RELEASE_TRUST["release_kind"],
        "signature_algorithm": _ACTIVE_RELEASE_TRUST["signature_algorithm"],
        "signature_key_id": _ACTIVE_RELEASE_TRUST["signature_key_id"],
        "signed_payload_sha256": _ACTIVE_RELEASE_TRUST[
            "signed_payload_sha256"
        ],
    }


def get_registry_snapshot_object() -> RegistrySnapshot:
    """Return a defensive typed copy of the in-process snapshot."""

    snapshot, _, _, _ = _active_registry()
    return copy.deepcopy(snapshot)


def _entry_hash(workflow: WorkflowSpec, snapshot: RegistrySnapshot) -> str:
    return snapshot.workflow_entry_hashes[
        f"{workflow.workflow_id}@{workflow.version}"
    ]


def _catalog_record(
    workflow: WorkflowSpec,
    snapshot: RegistrySnapshot,
) -> dict[str, Any]:
    return {
        "workflow_id": workflow.workflow_id,
        "workflow_version": workflow.version,
        "display_name": workflow.display_name,
        "summary": workflow.summary,
        "state": workflow.state,
        "risk_level": workflow.risk_level,
        "execution_backend": workflow.execution_backend,
        "source_profiles": list(workflow.source_profiles),
        "claim_types": list(workflow.claim_types),
        "dataset_keys": [pin["key"] for pin in workflow.dataset_pins],
        "allowed_claims": list(workflow.allowed_claims),
        "forbidden_claims": list(workflow.forbidden_claims),
        "publication_ready": False,
        "local_worker_executable": workflow.local_worker_executable,
        "registry_epoch": snapshot.epoch,
        "registry_entry_hash": _entry_hash(workflow, snapshot),
        "registry_hash": snapshot.registry_hash,
        "revocation_reason": workflow.revocation_reason,
    }


def list_formal_workflows(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    """Serialize the server-owned catalog without runtime implementation paths."""

    snapshot, _, _, _ = _active_registry()
    return [
        _catalog_record(workflow, snapshot)
        for workflow in snapshot.workflows
        if workflow.catalog_visible
        and (include_inactive or workflow.state in _EXECUTABLE_STATES)
    ]


def get_formal_workflow(
    workflow_id: str,
    version: str | None = None,
) -> dict[str, Any]:
    """Return one safe catalog record, including its immutable registry binding."""

    snapshot, workflows_by_identity, workflows_by_id, _ = _active_registry()
    workflow = (
        workflows_by_identity.get((workflow_id, version))
        if version is not None
        else workflows_by_id.get(workflow_id)
    )
    if workflow is None:
        raise WorkflowRegistryError("workflow_not_registered")
    return _catalog_record(workflow, snapshot)


def get_formal_workflow_spec(
    workflow_id: str,
    version: str | None = None,
) -> WorkflowSpec:
    """Return a defensive typed workflow for trusted internal dispatch."""

    _, workflows_by_identity, workflows_by_id, _ = _active_registry()
    workflow = (
        workflows_by_identity.get((workflow_id, version))
        if version is not None
        else workflows_by_id.get(workflow_id)
    )
    if workflow is None:
        raise WorkflowRegistryError("workflow_not_registered")
    return copy.deepcopy(workflow)


def get_tool_spec_for_entrypoint(entrypoint_id: str) -> ToolSpec:
    _, _, _, tools_by_entrypoint = _active_registry()
    try:
        return copy.deepcopy(tools_by_entrypoint[entrypoint_id])
    except KeyError as exc:
        raise WorkflowRegistryError("entrypoint_not_registered") from exc


def assert_workflow_executable(
    workflow_id: str,
    version: str | None = None,
    *,
    registry_epoch: str | None = None,
    registry_entry_hash: str | None = None,
) -> dict[str, Any]:
    """Fail closed for unknown, inactive, stale, or tampered workflow bindings."""

    snapshot, workflows_by_identity, workflows_by_id, _ = _active_registry()
    if snapshot.status != "ACTIVE":
        raise WorkflowRegistryError("workflow_registry_inactive")
    workflow = (
        workflows_by_identity.get((workflow_id, version))
        if version is not None
        else workflows_by_id.get(workflow_id)
    )
    if workflow is None:
        raise WorkflowRegistryError("workflow_not_registered")
    if workflow.state not in _EXECUTABLE_STATES:
        raise WorkflowRegistryError(f"workflow_{workflow.state.lower()}")
    expected_hash = _entry_hash(workflow, snapshot)
    if registry_epoch is not None and not hmac.compare_digest(
        registry_epoch, snapshot.epoch
    ):
        raise WorkflowRegistryError("workflow_registry_epoch_mismatch")
    if registry_entry_hash is not None and not hmac.compare_digest(
        registry_entry_hash, expected_hash
    ):
        raise WorkflowRegistryError("workflow_registry_entry_hash_mismatch")
    return _catalog_record(workflow, snapshot)


def get_legacy_workflow_contract(workflow_id: str) -> dict[str, Any]:
    workflow = get_formal_workflow_spec(workflow_id)
    return _deepcopy_mapping(workflow.legacy_contract)


def get_worker_execution_binding(
    workflow_id: str,
    version: str | None = None,
) -> dict[str, Any]:
    snapshot, workflows_by_identity, workflows_by_id, tools_by_entrypoint = (
        _active_registry()
    )
    workflow = (
        workflows_by_identity.get((workflow_id, version))
        if version is not None
        else workflows_by_id.get(workflow_id)
    )
    if workflow is None:
        raise WorkflowRegistryError("workflow_not_registered")
    if not workflow.local_worker_executable:
        raise WorkflowRegistryError("workflow_not_local_worker_executable")
    if snapshot.status != "ACTIVE" or workflow.state not in _EXECUTABLE_STATES:
        raise WorkflowRegistryError(f"workflow_{workflow.state.lower()}")
    adapter_binding = _static_execution_adapter_for_spec(workflow)
    entry_key = f"{workflow.workflow_id}@{workflow.version}"
    release_binding = snapshot.workflow_release_bindings.get(entry_key) or {}
    return {
        "workflow_id": workflow.workflow_id,
        "workflow_version": workflow.version,
        "registry_epoch": snapshot.epoch,
        "registry_entry_hash": _entry_hash(workflow, snapshot),
        "entrypoint_id": workflow.primary_entrypoint_id,
        "tool_spec_hash": _tool_spec_hash(
            tools_by_entrypoint[workflow.primary_entrypoint_id]
        ),
        **adapter_binding,
        "binding_kind": release_binding.get(
            "binding_kind", "release_configured_builtin"
        ),
        "candidate_id": release_binding.get("candidate_id"),
        "candidate_version": release_binding.get("candidate_version"),
        "candidate_version_hash": release_binding.get("candidate_version_hash"),
        "approved_worker_image_digest": release_binding.get(
            "approved_worker_image_digest"
        ),
        "approval_attestation_hash": release_binding.get(
            "approval_attestation_hash"
        ),
        "build_attestation_hash": release_binding.get("build_attestation_hash"),
        "registry_hash": snapshot.registry_hash,
        "registry_release_kind": _ACTIVE_RELEASE_TRUST["release_kind"],
        "registry_release_signature_algorithm": _ACTIVE_RELEASE_TRUST[
            "signature_algorithm"
        ],
        "registry_release_key_id": _ACTIVE_RELEASE_TRUST["signature_key_id"],
        "registry_release_payload_sha256": _ACTIVE_RELEASE_TRUST[
            "signed_payload_sha256"
        ],
    }


def worker_image_digest_is_approved(
    worker_image_digest: str,
    *,
    builtin_worker_image_digest: str | None,
) -> bool:
    """Return whether one image digest is trusted by the active Registry.

    Candidate-derived releases carry their own approved image digest in the
    signed release binding.  Built-in workflows predate those bindings, so
    production pins their official image through ``DOCKER_IMAGE_DIGEST``.
    Merely advertising a static entrypoint is never an image trust proof.
    """

    observed = str(worker_image_digest or "").strip().lower()
    builtin = str(builtin_worker_image_digest or "").strip().lower()
    if not _IMAGE_DIGEST_RE.fullmatch(observed):
        return False
    if builtin and not _IMAGE_DIGEST_RE.fullmatch(builtin):
        return False
    snapshot, _, _, _ = _active_registry()
    for workflow in snapshot.workflows:
        if (
            not workflow.local_worker_executable
            or workflow.state not in _EXECUTABLE_STATES
        ):
            continue
        try:
            binding = get_worker_execution_binding(
                workflow.workflow_id,
                workflow.version,
            )
        except WorkflowRegistryError:
            continue
        approved = binding.get("approved_worker_image_digest")
        if approved is None and binding.get("binding_kind") == "release_configured_builtin":
            approved = builtin or None
        if approved and hmac.compare_digest(str(approved), observed):
            return True
    return False


def bind_formal_registry_record(
    record: Any,
    binding: Mapping[str, Any],
    *,
    runner_image_digest: str | None = None,
) -> None:
    """Persist a registry binding when the target model exposes v2 columns.

    The compatibility check lets this registry/Worker change land before the
    additive database migration.  Once those columns exist, the same call path
    writes every explicit field as well as the JSON/task-envelope receipts.
    """

    values = {
        "workflow_id": binding.get("workflow_id"),
        "workflow_version": binding.get("workflow_version"),
        "registry_epoch": binding.get("registry_epoch"),
        "registry_entry_hash": binding.get("registry_entry_hash"),
        "entrypoint_id": binding.get("entrypoint_id"),
        "runner_image_digest": runner_image_digest,
    }
    for field_name, value in values.items():
        if value is not None and hasattr(record, field_name):
            setattr(record, field_name, value)


def list_worker_execution_bindings(*, worker_image_digest: str) -> list[dict[str, Any]]:
    digest = str(worker_image_digest or "unknown").strip().lower()
    if digest != "unknown" and not _IMAGE_DIGEST_RE.fullmatch(digest):
        raise WorkflowRegistryError("invalid_worker_image_digest")
    snapshot, _, _, _ = _active_registry()
    bindings: list[dict[str, Any]] = []
    for workflow in snapshot.workflows:
        if not workflow.local_worker_executable or workflow.state not in _EXECUTABLE_STATES:
            continue
        binding = get_worker_execution_binding(workflow.workflow_id)
        approved_digest = binding.get("approved_worker_image_digest")
        if approved_digest and not hmac.compare_digest(str(approved_digest), digest):
            continue
        bindings.append({**binding, "worker_image_digest": digest})
    return bindings


def _tool_spec_from_mapping(value: Mapping[str, Any]) -> ToolSpec:
    return ToolSpec(
        tool_id=str(value["tool_id"]),
        version=str(value["version"]),
        entrypoint_id=str(value["entrypoint_id"]),
        execution_backend=str(value["execution_backend"]),  # type: ignore[arg-type]
        code_binding=dict(value["code_binding"]),
        dependency_lock_sha256=str(value["dependency_lock_sha256"]),
        image_build_spec_sha256=str(value["image_build_spec_sha256"]),
        input_schema=dict(value["input_schema"]),
        output_schema=dict(value["output_schema"]),
        permissions=dict(value["permissions"]),
        provenance_policy=dict(value["provenance_policy"]),
        claim_policy=dict(value["claim_policy"]),
    )


def _workflow_spec_from_mapping(
    value: Mapping[str, Any],
    *,
    force_registered: bool,
) -> WorkflowSpec:
    return WorkflowSpec(
        workflow_id=str(value["workflow_id"]),
        version=str(value["version"]),
        display_name=str(value["display_name"]),
        summary=str(value["summary"]),
        state=(
            "REGISTERED"
            if force_registered
            else str(value["state"])  # type: ignore[arg-type]
        ),
        risk_level=str(value["risk_level"]),  # type: ignore[arg-type]
        execution_backend=str(value["execution_backend"]),  # type: ignore[arg-type]
        source_profiles=tuple(str(item) for item in value["source_profiles"]),
        claim_types=tuple(str(item) for item in value["claim_types"]),
        dataset_pins=tuple(dict(item) for item in value["dataset_pins"]),
        tool_dag=tuple(
            WorkflowToolRef(
                node_id=str(node["node_id"]),
                tool_id=str(node["tool_id"]),
                tool_version=str(node["tool_version"]),
                entrypoint_id=str(node["entrypoint_id"]),
                depends_on=tuple(str(item) for item in node.get("depends_on") or ()),
            )
            for node in value["tool_dag"]
        ),
        primary_entrypoint_id=str(value["primary_entrypoint_id"]),
        independent_verifier_entrypoint_id=(
            str(value["independent_verifier_entrypoint_id"])
            if value.get("independent_verifier_entrypoint_id")
            else None
        ),
        science_contract=dict(value["science_contract"]),
        acceptance=dict(value["acceptance"]),
        output_policy=dict(value["output_policy"]),
        allowed_claims=tuple(str(item) for item in value["allowed_claims"]),
        forbidden_claims=tuple(str(item) for item in value["forbidden_claims"]),
        evidence_pack_requirements=tuple(
            str(item) for item in value["evidence_pack_requirements"]
        ),
        catalog_visible=bool(value.get("catalog_visible", True)),
        local_worker_executable=bool(value.get("local_worker_executable", False)),
        revocation_reason=(
            str(value["revocation_reason"])
            if value.get("revocation_reason")
            else None
        ),
    )


def build_registry_entry_from_approved_candidate(
    candidate_version: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate an approved candidate and build a non-installed release entry.

    This deliberately has no mutation path.  The returned record is suitable
    for a protected release PR; it does not alter ``_SNAPSHOT``.
    """

    candidate_hash = str(candidate_version.get("candidate_version_hash") or "")
    approved_hash = str(candidate_version.get("approved_candidate_version_hash") or "")
    candidate_id = str(candidate_version.get("candidate_id") or "").strip()
    candidate_number = candidate_version.get("candidate_version")
    if (
        candidate_version.get("status") != "APPROVED"
        or not candidate_id
        or type(candidate_number) is not int
        or candidate_number < 1
        or not _SHA256_RE.fullmatch(candidate_hash.removeprefix("sha256:"))
        or not hmac.compare_digest(candidate_hash, approved_hash)
    ):
        raise WorkflowRegistryError("candidate_not_approved")
    reviews = candidate_version.get("reviews")
    if not isinstance(reviews, list) or not any(
        isinstance(review, Mapping)
        and review.get("decision") == "APPROVED"
        and review.get("reviewer_type") == "human"
        and hmac.compare_digest(
            str(review.get("candidate_version_hash") or ""), candidate_hash
        )
        for review in reviews
    ):
        raise WorkflowRegistryError("candidate_human_approval_missing")
    workflow_entry = candidate_version.get("workflow_spec")
    if not isinstance(workflow_entry, Mapping):
        raise WorkflowRegistryError("candidate_workflow_spec_missing")
    workflow_spec_hash = str(candidate_version.get("workflow_spec_hash") or "")
    expected_workflow_spec_hash = (
        "sha256:" + hashlib.sha256(jcs_canonicalize(workflow_entry)).hexdigest()
    )
    if not hmac.compare_digest(workflow_spec_hash, expected_workflow_spec_hash):
        raise WorkflowRegistryError("candidate_workflow_spec_hash_mismatch")
    worker_image_digest = str(
        candidate_version.get("worker_image_digest") or ""
    ).lower()
    if not _IMAGE_DIGEST_RE.fullmatch(worker_image_digest):
        raise WorkflowRegistryError("candidate_worker_image_digest_invalid")
    required = {
        "workflow_id",
        "version",
        "display_name",
        "summary",
        "risk_level",
        "execution_backend",
        "source_profiles",
        "claim_types",
        "dataset_pins",
        "tool_dag",
        "primary_entrypoint_id",
        "science_contract",
        "acceptance",
        "output_policy",
        "allowed_claims",
        "forbidden_claims",
        "evidence_pack_requirements",
    }
    if not required.issubset(workflow_entry):
        raise WorkflowRegistryError("candidate_workflow_spec_invalid")
    try:
        workflow = _workflow_spec_from_mapping(
            workflow_entry,
            force_registered=True,
        )
        candidate_tools = tuple(
            _tool_spec_from_mapping(tool)
            for tool in candidate_version.get("tool_specs") or []
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, WorkflowRegistryError):
            raise
        raise WorkflowRegistryError("candidate_registry_entry_invalid") from exc
    _static_execution_adapter_for_spec(workflow)
    approved_human_reviews = [
        review
        for review in reviews
        if isinstance(review, Mapping)
        and review.get("decision") == "APPROVED"
        and review.get("reviewer_type") == "human"
        and hmac.compare_digest(
            str(review.get("candidate_version_hash") or ""), candidate_hash
        )
    ]
    if workflow.risk_level == "R2" and not any(
        review.get("review_role") == "scientific"
        for review in approved_human_reviews
    ):
        raise WorkflowRegistryError("candidate_scientific_approval_missing")
    if workflow.risk_level == "R3":
        reviewer_ids = {
            str(review.get("reviewer_id") or "") for review in approved_human_reviews
        }
        reviewer_ids.discard("")
        roles = {
            str(review.get("review_role") or "") for review in approved_human_reviews
        }
        if len(reviewer_ids) < 2 or not {"engineering", "scientific"}.issubset(roles):
            raise WorkflowRegistryError("candidate_r3_approvals_incomplete")
    entry_key = f"{workflow.workflow_id}@{workflow.version}"
    approval_attestation_hash = "sha256:" + hashlib.sha256(
        jcs_canonicalize(approved_human_reviews)
    ).hexdigest()
    build_attestation_hash = "sha256:" + hashlib.sha256(
        jcs_canonicalize(
            {
                "workflow_spec_hash": workflow_spec_hash,
                "worker_image_digest": worker_image_digest,
                "tool_specs": [tool.to_dict() for tool in candidate_tools],
            }
        )
    ).hexdigest()
    release_binding = {
        "binding_kind": "signed_candidate_release",
        "candidate_id": candidate_id,
        "candidate_version": candidate_number,
        "candidate_version_hash": candidate_hash,
        "approved_worker_image_digest": worker_image_digest,
        "approval_attestation_hash": approval_attestation_hash,
        "build_attestation_hash": build_attestation_hash,
    }
    compiled = compile_registry(
        tools=candidate_tools,
        workflows=(workflow,),
        epoch="candidate-validation",
        workflow_release_bindings={entry_key: release_binding},
    )
    referenced_tool_identities = {
        (node.tool_id, node.tool_version) for node in workflow.tool_dag
    }
    return {
        "candidate_id": candidate_id,
        "candidate_version": candidate_number,
        "candidate_version_hash": candidate_hash,
        "workflow_spec_hash": workflow_spec_hash,
        "worker_image_digest": worker_image_digest,
        "approval_attestation_hash": approval_attestation_hash,
        "build_attestation_hash": build_attestation_hash,
        "workflow": workflow.to_dict(),
        "tools": [
            tool.to_dict()
            for tool in compiled.tools
            if (tool.tool_id, tool.version) in referenced_tool_identities
        ],
        "registry_entry_hash": compiled.workflow_entry_hashes[entry_key],
        "installation_status": "PENDING_RELEASE",
        "runtime_registry_modified": False,
    }


def validate_candidate_registration(
    workflow_spec: Mapping[str, Any],
    workflow_spec_hash: str,
    worker_image_digest: str,
    *,
    candidate_id: str,
    candidate_version: int,
    candidate_version_hash: str,
    approved_candidate_version_hash: str,
    tool_specs: Sequence[Mapping[str, Any]],
    reviews: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Fail-closed validation used before a registration ledger write.

    The result is a release candidate only.  A caller must still obtain a
    detached registry signature and ship the resulting snapshot in a release.
    """

    return build_registry_entry_from_approved_candidate(
        {
            "candidate_id": candidate_id,
            "candidate_version": candidate_version,
            "candidate_version_hash": candidate_version_hash,
            "approved_candidate_version_hash": approved_candidate_version_hash,
            "status": "APPROVED",
            "workflow_spec": dict(workflow_spec),
            "workflow_spec_hash": workflow_spec_hash,
            "worker_image_digest": worker_image_digest,
            "tool_specs": [dict(tool) for tool in tool_specs],
            "reviews": [dict(review) for review in reviews],
        }
    )


def _decode_signing_private_key(value: str) -> Ed25519PrivateKey:
    if not str(value or "").strip():
        raise WorkflowRegistryError(
            "registry_signing_key_unavailable",
            status_code=503,
        )
    try:
        raw = base64.b64decode(str(value).strip(), validate=True)
        if len(raw) != 32:
            raise ValueError
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (TypeError, ValueError) as exc:
        raise WorkflowRegistryError(
            "registry_signing_key_invalid",
            status_code=503,
        ) from exc


def _decode_signing_public_key(value: str) -> Ed25519PublicKey:
    try:
        raw = base64.b64decode(str(value).strip(), validate=True)
        if len(raw) != 32:
            raise ValueError
        return Ed25519PublicKey.from_public_bytes(raw)
    except (TypeError, ValueError) as exc:
        raise WorkflowRegistryError("registry_public_key_invalid") from exc


def _validate_pending_registry_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    if (
        entry.get("installation_status") != "PENDING_RELEASE"
        or entry.get("runtime_registry_modified") is not False
    ):
        raise WorkflowRegistryError("registry_entry_not_pending_release")
    workflow = entry.get("workflow")
    tools = entry.get("tools")
    if not isinstance(workflow, Mapping) or not isinstance(tools, list):
        raise WorkflowRegistryError("registry_entry_invalid")
    candidate_hash = str(entry.get("candidate_version_hash") or "")
    image_digest = str(entry.get("worker_image_digest") or "").lower()
    approval_hash = str(entry.get("approval_attestation_hash") or "")
    build_hash = str(entry.get("build_attestation_hash") or "")
    if (
        not str(entry.get("candidate_id") or "").strip()
        or type(entry.get("candidate_version")) is not int
        or not _SHA256_RE.fullmatch(candidate_hash.removeprefix("sha256:"))
        or not _IMAGE_DIGEST_RE.fullmatch(image_digest)
        or not _SHA256_RE.fullmatch(approval_hash.removeprefix("sha256:"))
        or not _SHA256_RE.fullmatch(build_hash.removeprefix("sha256:"))
    ):
        raise WorkflowRegistryError("registry_release_binding_invalid")
    release_binding = {
        "binding_kind": "signed_candidate_release",
        "candidate_id": str(entry["candidate_id"]),
        "candidate_version": int(entry["candidate_version"]),
        "candidate_version_hash": candidate_hash,
        "approved_worker_image_digest": image_digest,
        "approval_attestation_hash": approval_hash,
        "build_attestation_hash": build_hash,
    }
    try:
        parsed_workflow = _workflow_spec_from_mapping(
            workflow,
            force_registered=False,
        )
        entry_key = f"{parsed_workflow.workflow_id}@{parsed_workflow.version}"
        compiled = compile_registry(
            tools=tuple(
                _tool_spec_from_mapping(tool)
                for tool in tools
                if isinstance(tool, Mapping)
            ),
            workflows=(parsed_workflow,),
            epoch="pending-release-validation",
            workflow_release_bindings={entry_key: release_binding},
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, WorkflowRegistryError):
            raise
        raise WorkflowRegistryError("registry_entry_invalid") from exc
    if len(compiled.tools) != len(tools) or len(compiled.workflows) != 1:
        raise WorkflowRegistryError("registry_entry_invalid")
    expected_hash = compiled.workflow_entry_hashes[entry_key]
    if not hmac.compare_digest(
        str(entry.get("registry_entry_hash") or ""), expected_hash
    ):
        raise WorkflowRegistryError("registry_entry_hash_mismatch")
    return copy.deepcopy(dict(entry))


def assert_registry_entry_static_compatible(
    entry: Mapping[str, Any],
    *,
    static_tool_specs: Sequence[ToolSpec] | None = None,
) -> dict[str, Any]:
    """Require every candidate ToolSpec to exist in the shipped dispatch table.

    A protected build proves which bytes are present in a Worker image, but a
    signed Registry release must not turn an arbitrary module path into an
    executable entrypoint.  Registration calls this gate *before* creating a
    release request; startup repeats the same comparison before activation.

    AI-generated ``SCIENCE_CODE`` may therefore remain a durable Candidate
    Demo until its primary executor and independent verifier have been added
    to the reviewed, image-static ToolSpec table.  A Demo-only entrypoint is
    deliberately insufficient.
    """

    normalized = _validate_pending_registry_entry(entry)
    try:
        workflow = _workflow_spec_from_mapping(
            normalized["workflow"],
            force_registered=False,
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, WorkflowRegistryError):
            raise
        raise WorkflowRegistryError("registry_release_entry_invalid") from exc
    _static_execution_adapter_for_spec(workflow)
    shipped_tools = tuple(
        _BUILTIN_SNAPSHOT.tools
        if static_tool_specs is None
        else static_tool_specs
    )
    static_by_entrypoint: dict[str, ToolSpec] = {}
    static_by_identity: dict[tuple[str, str], ToolSpec] = {}
    for tool in shipped_tools:
        _validate_tool(tool)
        identity = (tool.tool_id, tool.version)
        if (
            tool.entrypoint_id in static_by_entrypoint
            or identity in static_by_identity
        ):
            raise WorkflowRegistryError("duplicate_static_tool_binding")
        static_by_entrypoint[tool.entrypoint_id] = tool
        static_by_identity[identity] = tool

    for raw_tool in normalized["tools"]:
        try:
            release_tool = _tool_spec_from_mapping(raw_tool)
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, WorkflowRegistryError):
                raise
            raise WorkflowRegistryError("registry_release_entry_invalid") from exc
        shipped_tool = static_by_entrypoint.get(release_tool.entrypoint_id)
        if shipped_tool is None:
            raise WorkflowRegistryError("registry_release_entrypoint_not_static")
        if release_tool.to_dict() != shipped_tool.to_dict():
            raise WorkflowRegistryError("registry_release_static_tool_mismatch")
    return normalized


def build_signed_registry_snapshot(
    entries: Sequence[Mapping[str, Any]],
    signing_private_key: str,
    key_id: str,
    *,
    epoch: str = REGISTRY_EPOCH,
    base_snapshot: RegistrySnapshot | None = None,
    operation_sequence: int = 1,
) -> dict[str, Any]:
    """JCS-canonicalize and sign a pending registry release with Ed25519.

    The key is an explicit argument and is never read from Worker-task or
    Evidence-Pack configuration.  Missing key material is a 503-class error.
    """

    clean_key_id = str(key_id or "").strip()
    if not clean_key_id:
        raise WorkflowRegistryError(
            "registry_signing_key_id_unavailable",
            status_code=503,
        )
    private_key = _decode_signing_private_key(signing_private_key)
    if not str(epoch or "").strip():
        raise WorkflowRegistryError("registry_epoch_missing")
    if type(operation_sequence) is not int or operation_sequence < 1:
        raise WorkflowRegistryError("registry_operation_sequence_invalid")
    release_base = base_snapshot or _BUILTIN_SNAPSHOT
    normalized = [_validate_pending_registry_entry(entry) for entry in entries]
    if not normalized:
        raise WorkflowRegistryError("registry_snapshot_empty")
    identities = [
        (
            str(entry["workflow"].get("workflow_id") or ""),
            str(entry["workflow"].get("version") or ""),
        )
        for entry in normalized
    ]
    if any(not all(identity) for identity in identities) or len(identities) != len(
        set(identities)
    ):
        raise WorkflowRegistryError("registry_snapshot_duplicate_entry")
    payload = {
        "schema_version": "standard_astro_signed_workflow_registry_v1",
        "registry_epoch": str(epoch),
        "base_registry_epoch": release_base.epoch,
        "base_registry_hash": release_base.registry_hash,
        "operation_sequence": operation_sequence,
        "entries": [
            entry
            for _identity, entry in sorted(
                zip(identities, normalized, strict=True),
                key=lambda item: item[0],
            )
        ],
    }
    canonical = jcs_canonicalize(payload)
    signature = private_key.sign(canonical)
    return {
        "payload": payload,
        "payload_sha256": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        "signature": {
            "algorithm": "ed25519",
            "key_id": clean_key_id,
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }


def build_registry_status_entry(
    existing_entry: Mapping[str, Any],
    requested_state: RegistryState,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    """Build a hash-correct signed-release overlay for suspend/revoke flows."""

    normalized = _validate_pending_registry_entry(existing_entry)
    current_state = str(normalized["workflow"].get("state") or "")
    allowed = {
        "REGISTERED": {"REGISTERED", "SUSPENDED", "SUPERSEDED", "REVOKED"},
        "SUSPENDED": {"REGISTERED", "SUSPENDED", "SUPERSEDED", "REVOKED"},
        "SUPERSEDED": {"SUPERSEDED", "REVOKED"},
        "REVOKED": {"REVOKED"},
    }
    if requested_state not in allowed.get(current_state, set()):
        raise WorkflowRegistryError("registry_release_state_transition_invalid")
    normalized["workflow"]["state"] = requested_state
    normalized["workflow"]["revocation_reason"] = (
        str(reason or "").strip() if requested_state == "REVOKED" else None
    )
    if requested_state == "REVOKED" and not normalized["workflow"]["revocation_reason"]:
        raise WorkflowRegistryError("revoked_workflow_missing_reason")
    workflow = _workflow_spec_from_mapping(
        normalized["workflow"],
        force_registered=False,
    )
    tools = tuple(_tool_spec_from_mapping(item) for item in normalized["tools"])
    entry_key = f"{workflow.workflow_id}@{workflow.version}"
    release_binding = {
        "binding_kind": "signed_candidate_release",
        "candidate_id": str(normalized["candidate_id"]),
        "candidate_version": int(normalized["candidate_version"]),
        "candidate_version_hash": str(normalized["candidate_version_hash"]),
        "approved_worker_image_digest": str(normalized["worker_image_digest"]),
        "approval_attestation_hash": str(normalized["approval_attestation_hash"]),
        "build_attestation_hash": str(normalized["build_attestation_hash"]),
    }
    compiled = compile_registry(
        tools=tools,
        workflows=(workflow,),
        epoch="status-release-validation",
        workflow_release_bindings={entry_key: release_binding},
    )
    normalized["registry_entry_hash"] = compiled.workflow_entry_hashes[entry_key]
    return normalized


def verify_signed_registry_snapshot(
    signed_snapshot: Mapping[str, Any],
    trusted_public_keys: Mapping[str, str],
) -> dict[str, Any]:
    """Verify one signed release against an external trusted keyring."""

    payload = signed_snapshot.get("payload")
    signature = signed_snapshot.get("signature")
    if not isinstance(payload, Mapping) or not isinstance(signature, Mapping):
        raise WorkflowRegistryError("registry_snapshot_signature_missing")
    if (
        payload.get("schema_version")
        != "standard_astro_signed_workflow_registry_v1"
        or not str(payload.get("registry_epoch") or "").strip()
        or set(payload)
        != {
            "schema_version",
            "registry_epoch",
            "base_registry_epoch",
            "base_registry_hash",
            "operation_sequence",
            "entries",
        }
        or type(payload.get("operation_sequence")) is not int
        or int(payload["operation_sequence"]) < 1
    ):
        raise WorkflowRegistryError("registry_snapshot_schema_invalid")
    key_id = str(signature.get("key_id") or "")
    if signature.get("algorithm") != "ed25519" or key_id not in trusted_public_keys:
        raise WorkflowRegistryError("registry_snapshot_key_untrusted")
    canonical = jcs_canonicalize(payload)
    expected_hash = "sha256:" + hashlib.sha256(canonical).hexdigest()
    if not hmac.compare_digest(
        str(signed_snapshot.get("payload_sha256") or ""), expected_hash
    ):
        raise WorkflowRegistryError("registry_snapshot_payload_hash_mismatch")
    try:
        encoded_signature = base64.b64decode(
            str(signature.get("value") or ""), validate=True
        )
        _decode_signing_public_key(trusted_public_keys[key_id]).verify(
            encoded_signature,
            canonical,
        )
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise WorkflowRegistryError("registry_snapshot_signature_invalid") from exc
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise WorkflowRegistryError("registry_snapshot_empty")
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise WorkflowRegistryError("registry_entry_invalid")
        _validate_pending_registry_entry(entry)
    return copy.deepcopy(dict(payload))


def _read_signed_registry_release(
    release_source: Mapping[str, Any] | str | Path,
) -> dict[str, Any]:
    if isinstance(release_source, Mapping):
        return copy.deepcopy(dict(release_source))
    path = Path(release_source)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise WorkflowRegistryError(
            "registry_release_unavailable",
            status_code=503,
        ) from exc
    if len(raw) > _MAX_RELEASE_FILE_BYTES:
        raise WorkflowRegistryError("registry_release_too_large")
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowRegistryError("registry_release_json_invalid") from exc
    if not isinstance(decoded, dict):
        raise WorkflowRegistryError("registry_release_json_invalid")
    return decoded


def load_verified_registry_release(
    release_source: Mapping[str, Any] | str | Path | None = None,
    trusted_public_keys: Mapping[str, str] | None = None,
    *,
    base_snapshot: RegistrySnapshot | None = None,
    static_tool_specs: Sequence[ToolSpec] | None = None,
) -> RegistrySnapshot:
    """Load a signed, release-bundled registry without executing candidate code.

    ``None`` intentionally means "built-ins only".  A configured release may
    be supplied as already-decoded JSON or as a fixed package/deployment path.
    Every referenced entrypoint must already exist in the release's static
    dispatch table.  The signed document can therefore compose shipped tools,
    but it cannot install Python, module paths, or dynamic plugins.

    The function has no database or process-global side effects.  Startup code
    may adopt the returned snapshot only after its trusted keyring is loaded
    from deployment configuration.
    """

    base = copy.deepcopy(base_snapshot or _BUILTIN_SNAPSHOT)
    if release_source is None:
        return base
    if not trusted_public_keys:
        raise WorkflowRegistryError(
            "registry_trusted_keyring_unavailable",
            status_code=503,
        )
    signed_release = _read_signed_registry_release(release_source)
    payload = verify_signed_registry_snapshot(signed_release, trusted_public_keys)
    if (
        payload.get("base_registry_epoch") != base.epoch
        or payload.get("base_registry_hash") != base.registry_hash
    ):
        raise WorkflowRegistryError("registry_release_base_mismatch")

    shipped_tools = tuple(static_tool_specs or base.tools)
    static_by_entrypoint: dict[str, ToolSpec] = {}
    static_by_identity: dict[tuple[str, str], ToolSpec] = {}
    for tool in shipped_tools:
        _validate_tool(tool)
        identity = (tool.tool_id, tool.version)
        if (
            tool.entrypoint_id in static_by_entrypoint
            or identity in static_by_identity
        ):
            raise WorkflowRegistryError("duplicate_static_tool_binding")
        static_by_entrypoint[tool.entrypoint_id] = tool
        static_by_identity[identity] = tool

    merged_tools: dict[tuple[str, str], ToolSpec] = {
        (tool.tool_id, tool.version): tool for tool in base.tools
    }
    merged_workflows: dict[tuple[str, str], WorkflowSpec] = {
        (workflow.workflow_id, workflow.version): workflow
        for workflow in base.workflows
    }
    merged_release_bindings: dict[str, Mapping[str, Any]] = {
        key: _deepcopy_mapping(value)
        for key, value in base.workflow_release_bindings.items()
    }
    entries = payload["entries"]
    for entry in entries:
        workflow_value = entry["workflow"]
        tools_value = entry["tools"]
        try:
            workflow = _workflow_spec_from_mapping(
                workflow_value,
                force_registered=False,
            )
            release_tools = tuple(
                _tool_spec_from_mapping(tool) for tool in tools_value
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, WorkflowRegistryError):
                raise
            raise WorkflowRegistryError("registry_release_entry_invalid") from exc
        _static_execution_adapter_for_spec(workflow)
        identity = (workflow.workflow_id, workflow.version)
        entry_key = f"{workflow.workflow_id}@{workflow.version}"
        release_binding = {
            "binding_kind": "signed_candidate_release",
            "candidate_id": str(entry["candidate_id"]),
            "candidate_version": int(entry["candidate_version"]),
            "candidate_version_hash": str(entry["candidate_version_hash"]),
            "approved_worker_image_digest": str(entry["worker_image_digest"]),
            "approval_attestation_hash": str(entry["approval_attestation_hash"]),
            "build_attestation_hash": str(entry["build_attestation_hash"]),
        }
        for release_tool in release_tools:
            shipped_tool = static_by_entrypoint.get(release_tool.entrypoint_id)
            if shipped_tool is None:
                raise WorkflowRegistryError(
                    "registry_release_entrypoint_not_static"
                )
            if release_tool.to_dict() != shipped_tool.to_dict():
                raise WorkflowRegistryError(
                    "registry_release_static_tool_mismatch"
                )
            tool_identity = (release_tool.tool_id, release_tool.version)
            existing_tool = merged_tools.get(tool_identity)
            if existing_tool is not None and (
                existing_tool.to_dict() != release_tool.to_dict()
            ):
                raise WorkflowRegistryError("registry_release_tool_conflict")
            merged_tools[tool_identity] = copy.deepcopy(shipped_tool)

        existing_workflow = merged_workflows.get(identity)
        if existing_workflow is None:
            # A fixed-base release is a complete cumulative overlay. A later
            # release can therefore contain a candidate that was registered
            # in an earlier release and is now suspended, superseded, or
            # revoked. The trusted cumulative signer and catalog binding prove
            # that history; the final state still remains non-executable.
            merged_workflows[identity] = workflow
            merged_release_bindings[entry_key] = release_binding
            continue

        existing_contract = existing_workflow.to_dict()
        release_contract = workflow.to_dict()
        for field_name in ("state", "revocation_reason"):
            existing_contract.pop(field_name, None)
            release_contract.pop(field_name, None)
        if existing_contract != release_contract:
            raise WorkflowRegistryError("registry_release_workflow_conflict")
        allowed_transitions = {
            "REGISTERED": {"REGISTERED", "SUSPENDED", "SUPERSEDED", "REVOKED"},
            "SUSPENDED": {"REGISTERED", "SUSPENDED", "SUPERSEDED", "REVOKED"},
            "SUPERSEDED": {"SUPERSEDED", "REVOKED"},
            "REVOKED": {"REVOKED"},
        }
        if workflow.state not in allowed_transitions[existing_workflow.state]:
            raise WorkflowRegistryError("registry_release_state_transition_invalid")
        merged_workflows[identity] = replace(
            existing_workflow,
            state=workflow.state,
            revocation_reason=workflow.revocation_reason,
        )
        merged_release_bindings[entry_key] = release_binding

    return compile_registry(
        tools=tuple(merged_tools.values()),
        workflows=tuple(merged_workflows.values()),
        epoch=str(payload["registry_epoch"]),
        status="ACTIVE",
        workflow_release_bindings=merged_release_bindings,
    )


def activate_verified_registry_release(
    release_source: Mapping[str, Any] | str | Path | None,
    trusted_public_keys: Mapping[str, str] | None,
    *,
    static_tool_specs: Sequence[ToolSpec] | None = None,
) -> RegistrySnapshot:
    """Atomically adopt one verified release before the first registry lookup.

    Startup is the only supported activation boundary.  Once any catalog,
    dispatch, lease, or finalizer lookup has observed the active snapshot, a
    later activation is rejected so one process cannot mix registry epochs.
    """

    global _REGISTRY_ACTIVATED
    global _ACTIVE_RELEASE_TRUST
    global _REGISTRY_LOOKUP_STARTED
    global _SNAPSHOT
    global _TOOLS_BY_ENTRYPOINT
    global _WORKFLOWS_BY_ID
    global _WORKFLOWS_BY_IDENTITY

    with _REGISTRY_LOCK:
        if _REGISTRY_LOOKUP_STARTED or _REGISTRY_ACTIVATED:
            raise WorkflowRegistryError(
                "registry_activation_window_closed",
                status_code=409,
            )
        snapshot = load_verified_registry_release(
            release_source,
            trusted_public_keys,
            base_snapshot=_BUILTIN_SNAPSHOT,
            static_tool_specs=static_tool_specs,
        )
        workflows_by_identity, workflows_by_id = _workflow_indexes(snapshot)
        tools_by_entrypoint = {
            tool.entrypoint_id: tool for tool in snapshot.tools
        }
        _SNAPSHOT = snapshot
        _WORKFLOWS_BY_IDENTITY = workflows_by_identity
        _WORKFLOWS_BY_ID = workflows_by_id
        _TOOLS_BY_ENTRYPOINT = tools_by_entrypoint
        if release_source is None:
            release_trust = {
                "release_kind": "builtin_code_registry",
                "signature_algorithm": None,
                "signature_key_id": None,
                "signed_payload_sha256": None,
            }
        else:
            signed_release = _read_signed_registry_release(release_source)
            signature = signed_release.get("signature")
            if not isinstance(signature, Mapping):
                raise WorkflowRegistryError("registry_snapshot_signature_missing")
            release_trust = {
                "release_kind": "signed_registry_release",
                "signature_algorithm": signature.get("algorithm"),
                "signature_key_id": signature.get("key_id"),
                "signed_payload_sha256": signed_release.get("payload_sha256"),
            }
        _ACTIVE_RELEASE_TRUST = release_trust
        _REGISTRY_ACTIVATED = True
        return copy.deepcopy(snapshot)


def activate_configured_registry_release(
    environ: Mapping[str, str] | None = None,
) -> RegistrySnapshot | None:
    """Activate the fixed release path for any API, control, or Worker role."""

    environment = environ or os.environ
    release_path = str(environment.get("WORKFLOW_REGISTRY_RELEASE_PATH") or "").strip()
    if not release_path:
        return None
    keyring_json = str(
        environment.get("WORKFLOW_REGISTRY_TRUSTED_KEYRING_JSON") or ""
    ).strip()
    keyring_path = str(
        environment.get("WORKFLOW_REGISTRY_TRUSTED_KEYRING_PATH") or ""
    ).strip()
    if bool(keyring_json) == bool(keyring_path):
        raise WorkflowRegistryError(
            "registry_trusted_keyring_unavailable",
            status_code=503,
        )
    try:
        raw_keyring = (
            json.loads(keyring_json)
            if keyring_json
            else json.loads(Path(keyring_path).read_text(encoding="utf-8"))
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowRegistryError(
            "registry_trusted_keyring_invalid",
            status_code=503,
        ) from exc
    if not isinstance(raw_keyring, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in raw_keyring.items()
    ):
        raise WorkflowRegistryError(
            "registry_trusted_keyring_invalid",
            status_code=503,
        )
    return activate_verified_registry_release(
        Path(release_path),
        raw_keyring,
    )


_PACKAGED_ACTIVATION_DIRECTORY = (
    Path(__file__).resolve().parents[1] / "registry_releases"
)
_PACKAGED_ACTIVATION_MANIFEST = (
    _PACKAGED_ACTIVATION_DIRECTORY / "activation-manifest.json"
)

if os.getenv("WORKFLOW_REGISTRY_RELEASE_PATH"):
    # A configured-but-invalid release must fail process boot in every role.
    activate_configured_registry_release()
elif _PACKAGED_ACTIVATION_MANIFEST.is_file():
    # Render cannot safely inject a new immutable file at deploy time. A
    # protected activation PR therefore bakes only public signed material into
    # the image. Presence of its manifest is an explicit boot-time activation
    # request; later startup guards verify the manifest and DB import exactly.
    activate_configured_registry_release(
        {
            "WORKFLOW_REGISTRY_RELEASE_PATH": str(
                _PACKAGED_ACTIVATION_DIRECTORY / "active-signed-registry.json"
            ),
            "WORKFLOW_REGISTRY_TRUSTED_KEYRING_PATH": str(
                _PACKAGED_ACTIVATION_DIRECTORY / "trusted-registry-keyring.json"
            ),
        }
    )


__all__ = [
    "CONTROL_IMAGE_BUILD_SPEC_SHA256",
    "DESI_DR2_MATRIX_ENTRYPOINT_ID",
    "DESI_DR2_MATRIX_WORKFLOW_ID",
    "REGISTRY_EPOCH",
    "REGISTRY_SCHEMA_VERSION",
    "REQUIREMENTS_LOCK_SHA256",
    "RegistrySnapshot",
    "ToolSpec",
    "UNION3_COVARIANCE_SHA256",
    "UNION3_PDF_SHA256",
    "UNION3_PRIMARY_ENTRYPOINT_ID",
    "UNION3_RADIATION_CONVENTION",
    "UNION3_REGISTERED_CLAIM",
    "UNION3_REPRODUCTION_WORKFLOW_ID",
    "UNION3_VECTOR_SHA256",
    "UNION3_VERIFIER_ENTRYPOINT_ID",
    "WORKER_IMAGE_BUILD_SPEC_SHA256",
    "WorkflowRegistryError",
    "WorkflowSpec",
    "WorkflowToolRef",
    "assert_registry_entry_static_compatible",
    "assert_workflow_executable",
    "activate_verified_registry_release",
    "active_registry_identity",
    "activate_configured_registry_release",
    "bind_formal_registry_record",
    "builtin_registry_identity",
    "build_signed_registry_snapshot",
    "build_registry_status_entry",
    "build_registry_entry_from_approved_candidate",
    "compile_registry",
    "get_formal_workflow",
    "get_formal_workflow_spec",
    "get_legacy_workflow_contract",
    "get_registry_snapshot_object",
    "get_static_execution_adapter_binding",
    "get_static_execution_adapter_contract",
    "get_tool_spec_for_entrypoint",
    "get_worker_execution_binding",
    "list_formal_workflows",
    "list_static_worker_entrypoint_capabilities",
    "list_worker_execution_bindings",
    "load_verified_registry_release",
    "registry_snapshot",
    "validate_candidate_registration",
    "verify_signed_registry_snapshot",
]

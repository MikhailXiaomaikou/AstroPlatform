"""Compatibility facade for the typed formal workflow registry.

New code should use :mod:`app.services.workflow_registry_v2`.  These helpers
preserve the dictionary API used by the existing Union3 research loop while
removing executable Python import paths from registry data.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, Final

from app.services.workflow_registry_v2 import (
    DESI_DR2_MATRIX_WORKFLOW_ID,
    UNION3_COVARIANCE_SHA256,
    UNION3_PDF_SHA256,
    UNION3_RADIATION_CONVENTION,
    UNION3_REGISTERED_CLAIM,
    UNION3_REPRODUCTION_WORKFLOW_ID,
    UNION3_VECTOR_SHA256,
    get_formal_workflow_spec,
    get_legacy_workflow_contract,
    list_formal_workflows,
)


class _RegisteredWorkflowView(Mapping[str, dict[str, Any]]):
    """Lazy compatibility view so startup may activate a signed release first."""

    def __getitem__(self, workflow_id: str) -> dict[str, Any]:
        return get_legacy_workflow_contract(workflow_id)

    def __iter__(self) -> Iterator[str]:
        return iter(list_registered_workflows())

    def __len__(self) -> int:
        return len(list_formal_workflows())


REGISTERED_WORKFLOWS: Final[Mapping[str, dict[str, Any]]] = _RegisteredWorkflowView()


def get_registered_workflow(workflow_id: str) -> dict[str, Any]:
    """Return a defensive copy of a registered workflow contract."""

    return get_legacy_workflow_contract(workflow_id)


def list_registered_workflows() -> tuple[str, ...]:
    """Return formal workflow identifiers in deterministic order."""

    return tuple(item["workflow_id"] for item in list_formal_workflows())


def get_registered_dataset_pins(workflow_id: str) -> list[dict[str, str]]:
    """Return the only dataset-pin envelope accepted for a workflow."""

    workflow = get_formal_workflow_spec(workflow_id)
    return [dict(pin) for pin in workflow.dataset_pins]


__all__ = [
    "DESI_DR2_MATRIX_WORKFLOW_ID",
    "REGISTERED_WORKFLOWS",
    "UNION3_COVARIANCE_SHA256",
    "UNION3_PDF_SHA256",
    "UNION3_RADIATION_CONVENTION",
    "UNION3_REGISTERED_CLAIM",
    "UNION3_REPRODUCTION_WORKFLOW_ID",
    "UNION3_VECTOR_SHA256",
    "get_registered_dataset_pins",
    "get_registered_workflow",
    "list_registered_workflows",
]

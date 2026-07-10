"""Owner-authorization plan for every pipeline storage capability.

Pipeline nodes historically used several path spellings (``fits_path``,
``science_fits_path``, calibration-frame lists, and so on).  Keeping separate
allow-lists in the HTTP API, AI tool bridge, and scheduler worker is unsafe:
one missed alias becomes a cross-tenant object read.  This module is the one
declarative source of truth used by all three execution paths.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PipelineNodeStorageSpec:
    """Storage-bearing parameters and default-input behaviour for one node."""

    scalar_fields: tuple[str, ...] = ()
    list_fields: tuple[str, ...] = ()
    consumes_default_input: bool = False
    consumes_default_input_when_nested: bool = False
    default_shadow_fields: tuple[str, ...] = ()
    inject_default_as: str | None = None


# ``fits_path`` is intentionally universal: several nodes accept it today and
# future file-reading nodes should inherit authorization without needing a new
# entry merely for the conventional spelling.
_UNIVERSAL_SCALAR_FIELDS = ("fits_path",)


PIPELINE_NODE_STORAGE_SPECS: dict[str, PipelineNodeStorageSpec] = {
    "LoadData": PipelineNodeStorageSpec(
        consumes_default_input=True,
        consumes_default_input_when_nested=True,
        default_shadow_fields=("fits_path",),
        inject_default_as="fits_path",
    ),
    "ImportWorkspace": PipelineNodeStorageSpec(
        scalar_fields=("path",),
        consumes_default_input=True,
        consumes_default_input_when_nested=True,
        # The engine injects ``path`` from input_data_id whenever that key is
        # absent, before ImportWorkspace can consider its legacy fits_path
        # alias.  Therefore fits_path alone does not shadow the default.
        default_shadow_fields=("path",),
        inject_default_as="path",
    ),
    "PSFPhotometry": PipelineNodeStorageSpec(
        consumes_default_input=True,
        default_shadow_fields=("fits_path",),
        inject_default_as="fits_path",
    ),
    "BiasSubtract": PipelineNodeStorageSpec(
        scalar_fields=("science_fits_path",),
        list_fields=("bias_paths",),
        consumes_default_input=True,
        default_shadow_fields=("science_fits_path",),
    ),
    "DarkCorrect": PipelineNodeStorageSpec(
        scalar_fields=("science_fits_path",),
        list_fields=("dark_paths",),
        consumes_default_input=True,
        default_shadow_fields=("science_fits_path",),
    ),
    "FlatField": PipelineNodeStorageSpec(
        scalar_fields=("science_fits_path",),
        list_fields=("flat_paths",),
        consumes_default_input=True,
        default_shadow_fields=("science_fits_path",),
    ),
    "CosmicRayReject": PipelineNodeStorageSpec(
        consumes_default_input=True,
        default_shadow_fields=("fits_path",),
    ),
    "AstrometricSolve": PipelineNodeStorageSpec(
        consumes_default_input=True,
        default_shadow_fields=("fits_path",),
    ),
    "SourceExtract": PipelineNodeStorageSpec(
        consumes_default_input=True,
        default_shadow_fields=("fits_path",),
    ),
    "Reproject": PipelineNodeStorageSpec(
        scalar_fields=("target_wcs_fits",),
        consumes_default_input=True,
    ),
    "PSFMatch": PipelineNodeStorageSpec(consumes_default_input=True),
    "Deblend": PipelineNodeStorageSpec(consumes_default_input=True),
    "Mosaic": PipelineNodeStorageSpec(list_fields=("fits_paths",)),
}


class PipelineStorageInputError(ValueError):
    """Raised when a DAG contains a malformed storage-path declaration."""


@dataclass
class _StorageSlot:
    container: dict[str, Any]
    field: str
    index: int | None
    raw_path: str


@dataclass
class _DefaultInputUse:
    params: dict[str, Any]
    inject_field: str | None


def _node_params(node: dict[str, Any]) -> dict[str, Any]:
    data = node.get("data")
    if data is None:
        data = {}
        node["data"] = data
    if not isinstance(data, dict):
        raise PipelineStorageInputError("Pipeline node data must be an object")
    params = data.get("params")
    if params is None:
        params = {}
        data["params"] = params
    if not isinstance(params, dict):
        raise PipelineStorageInputError("Pipeline node params must be an object")
    return params


def _nonempty_path_value(params: dict[str, Any], field: str) -> bool:
    value = params.get(field)
    return isinstance(value, str) and bool(value.strip())


def _storage_plan(
    dag: dict[str, Any],
) -> tuple[list[_StorageSlot], list[_DefaultInputUse]]:
    nodes = dag.get("nodes")
    if not isinstance(nodes, list):
        raise PipelineStorageInputError("Pipeline DAG nodes must be a list")
    # Legacy scheduled rows may predate the explicit empty-edge list.  Treat a
    # missing key as no edges; current HTTP/AI entry points still perform their
    # own structural DAG validation before execution.
    edges = dag.get("edges", [])
    if not isinstance(edges, list):
        raise PipelineStorageInputError("Pipeline DAG edges must be a list")

    target_ids = {
        str(edge.get("target"))
        for edge in edges
        if isinstance(edge, dict) and edge.get("target") is not None
    }
    explicit: list[_StorageSlot] = []
    defaults: list[_DefaultInputUse] = []

    for node in nodes:
        if not isinstance(node, dict):
            raise PipelineStorageInputError("Pipeline nodes must be objects")
        node_type = str(node.get("type") or "")
        spec = PIPELINE_NODE_STORAGE_SPECS.get(
            node_type, PipelineNodeStorageSpec()
        )
        params = _node_params(node)

        scalar_fields = tuple(
            dict.fromkeys((*_UNIVERSAL_SCALAR_FIELDS, *spec.scalar_fields))
        )
        for field in scalar_fields:
            value = params.get(field)
            if value in (None, ""):
                continue
            if not isinstance(value, str) or not value.strip():
                raise PipelineStorageInputError(
                    f"Pipeline storage field {field} must be a non-empty string"
                )
            explicit.append(_StorageSlot(params, field, None, value))

        for field in spec.list_fields:
            value = params.get(field)
            if value in (None, []):
                continue
            if not isinstance(value, list):
                raise PipelineStorageInputError(
                    f"Pipeline storage field {field} must be a list of strings"
                )
            for index, item in enumerate(value):
                if not isinstance(item, str) or not item.strip():
                    raise PipelineStorageInputError(
                        f"Pipeline storage field {field} must contain non-empty strings"
                    )
                explicit.append(_StorageSlot(params, field, index, item))

        node_id = str(node.get("id") or "")
        is_root = node_id not in target_ids
        default_is_shadowed = any(
            _nonempty_path_value(params, field)
            for field in spec.default_shadow_fields
        )
        may_use_default = is_root or spec.consumes_default_input_when_nested
        if spec.consumes_default_input and not default_is_shadowed:
            # Every root starts with input_data_id exposed as ``fits_path`` and
            # ``path``.  Non-storage nodes are allowed to propagate that input
            # unchanged, so a downstream file reader can still consume the
            # root capability even when it is not itself a root node.  Always
            # owner-bind the capability when any reader can observe it.  Keep
            # parameter injection limited to the engine's existing direct-
            # default semantics so authorization does not rewrite nested DAGs.
            defaults.append(
                _DefaultInputUse(
                    params,
                    spec.inject_default_as if may_use_default else None,
                )
            )

    return explicit, defaults


def collect_pipeline_storage_paths(
    *, dag: dict[str, Any], input_data_id: str
) -> list[str]:
    """Return every raw object key a persisted pipeline can read directly."""

    # Work on a copy because normalizing missing data/params is useful to the
    # async binder but a scheduler authorization check must not mutate its ORM
    # JSON value merely by inspecting it.
    explicit, defaults = _storage_plan(deepcopy(dag))
    paths = [slot.raw_path for slot in explicit]
    if defaults:
        if not isinstance(input_data_id, str) or not input_data_id.strip():
            raise PipelineStorageInputError(
                "Pipeline input_data_id must be a non-empty storage key"
            )
        paths.append(input_data_id)
    return paths


async def bind_pipeline_storage_inputs(
    *,
    dag: dict[str, Any],
    input_data_id: str,
    resolve_key: Callable[[str], Awaitable[str]],
) -> tuple[dict[str, Any], str]:
    """Deep-copy a DAG and replace every file capability with an owned key."""

    bound = deepcopy(dag)
    explicit, defaults = _storage_plan(bound)

    for slot in explicit:
        resolved = await resolve_key(slot.raw_path)
        if slot.index is None:
            slot.container[slot.field] = resolved
        else:
            values = list(slot.container.get(slot.field) or [])
            values[slot.index] = resolved
            slot.container[slot.field] = values

    canonical_default = input_data_id
    if defaults:
        if not isinstance(input_data_id, str) or not input_data_id.strip():
            raise PipelineStorageInputError(
                "Pipeline input_data_id must be a non-empty storage key"
            )
        canonical_default = await resolve_key(input_data_id)
        for default in defaults:
            if default.inject_field:
                default.params[default.inject_field] = canonical_default

    return bound, canonical_default

"""PARAM tag scanner for non-standard TAP provenance metadata."""

from __future__ import annotations

from typing import Any

from app.services.provenance_v2.registry_loader import dataset_from_registry

PARAM_FIELD_MAP = {
    "PUBLISHER": "publisher",
    "CREATOR": "creator",
    "TERMS": "rights_uri",
    "HELP": "reference_url",
    "CITATION": "article",
}


def scan_params(params: Any) -> dict[str, Any]:
    """Map VOTable PARAM-like objects or dicts into provenance fields."""
    result: dict[str, Any] = {}
    for item in _iter_items(params):
        name = _get_attr(item, "name")
        if not name:
            continue
        target = PARAM_FIELD_MAP.get(str(name).upper())
        if not target:
            continue
        value = _get_attr(item, "value")
        if value not in (None, ""):
            result[target] = str(value)
    return result


def resolve_param_provenance(params: Any, *, service_hint: str = "gaia") -> dict[str, Any] | None:
    return dataset_from_registry(
        service_hint,
        source_authority="datacenter_non_standard_tag",
        archive_version="Gaia DR3" if service_hint in {"gaia", "gaia_dr3"} else None,
        supplements=scan_params(params),
    )


def _iter_items(items: Any):
    if isinstance(items, dict):
        yield from items.values()
    elif isinstance(items, (list, tuple)):
        yield from items
    elif items is not None:
        yield items


def _get_attr(item: Any, attr: str) -> Any:
    if isinstance(item, dict):
        return item.get(attr) or item.get(attr.upper())
    return getattr(item, attr, None)

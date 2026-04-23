"""INFO tag scanner for datacenters without IVOA DataOrigin blocks."""

from __future__ import annotations

from typing import Any

from app.services.provenance_v2.registry_loader import dataset_from_registry

INFO_FIELD_MAP = {
    "PROVIDER": "publisher",
    "PUBLISHER": "publisher",
    "CREATOR": "creator",
    "REFERENCE": "reference_url",
    "CITATION": "article",
}


def scan_infos(infos: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in _iter_items(infos):
        name = _get_attr(item, "name")
        if not name:
            continue
        target = INFO_FIELD_MAP.get(str(name).upper())
        if not target:
            continue
        value = _get_attr(item, "value")
        if value not in (None, ""):
            result[target] = str(value)
    return result


def resolve_info_provenance(infos: Any, *, service_hint: str = "ned") -> dict[str, Any] | None:
    return dataset_from_registry(
        service_hint,
        source_authority="datacenter_non_standard_info",
        supplements=scan_infos(infos),
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

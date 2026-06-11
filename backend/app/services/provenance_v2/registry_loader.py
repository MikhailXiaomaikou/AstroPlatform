"""Fallback registry loader for provenance v2."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import json
import logging
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path(__file__).with_name("fallback_registry.yaml")
logger = logging.getLogger(__name__)

DEFAULT_ARCHIVE_VERSIONS = {
    "vizier": "VizieR live",
    "gaia": "Gaia DR3",
    "simbad": "SIMBAD current",
    "ned": "NED current",
    "2mass": "2MASS PSC II/246",
    "alma": "ALMA Science Archive current",
}

DEFAULT_SOURCE_AUTHORITIES = {
    "vizier": "datacenter_ivoa_compliant",
    "gaia": "datacenter_non_standard_tag",
    "simbad": "project_registry",
    "ned": "datacenter_non_standard_info",
    "2mass": "datacenter_ivoa_compliant",
    "alma": "datacenter_ivoa_compliant",
}


def load_registry(path: str | Path | None = None) -> dict[str, Any]:
    """Load the in-repo provenance fallback registry.

    The file is JSON-compatible YAML so this loader works without adding a
    hard dependency on PyYAML. If PyYAML is available, non-JSON YAML remains
    supported for future maintenance.
    """
    registry_path = Path(path) if path is not None else REGISTRY_PATH
    try:
        text = registry_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"version": None, "schema_version": 1, "services": {}}

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml

            loaded = yaml.safe_load(text)
            payload = loaded if isinstance(loaded, dict) else {}
        except Exception:
            payload = {}

    services = payload.get("services")
    if not isinstance(services, dict):
        payload["services"] = {}
    return payload


def resolve_service(hint: str, registry: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Resolve a registry service by key, URL substring, catalog ID, or IVOID."""
    if not hint:
        return None
    reg = registry or load_registry()
    needle = str(hint).lower()
    services = reg.get("services") or {}
    if not isinstance(services, dict):
        return None

    for service_key, entry in services.items():
        if needle == str(service_key).lower():
            return _entry_with_key(str(service_key), entry)
        provenance = entry.get("provenance") if isinstance(entry, dict) else {}
        if isinstance(provenance, dict):
            for field in ("service_key", "service_name", "ivoid", "article", "reference_url"):
                value = provenance.get(field)
                if value and needle in str(value).lower():
                    return _entry_with_key(str(service_key), entry)
        for pattern in entry.get("match_patterns", []) if isinstance(entry, dict) else []:
            if needle in str(pattern).lower() or str(pattern).lower() in needle:
                return _entry_with_key(str(service_key), entry)
    return None


def normalize_service_key(hint: str | None) -> str | None:
    """Map connector aliases/catalog hints to the registry's service keys."""
    if not hint:
        return None
    raw = str(hint).strip().lower()
    aliases = {
        "gaia_dr3": "gaia",
        "twomass": "2mass",
        "two_mass": "2mass",
        "ii/246": "2mass",
        "alma science archive": "alma",
    }
    if raw in aliases:
        return aliases[raw]
    resolved = resolve_service(raw)
    if resolved:
        return str(resolved.get("service_key") or raw)
    return raw


def dataset_from_registry(
    hint: str | None,
    *,
    source_authority: str | None = None,
    archive_version: str | None = None,
    supplements: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build one dataset-level provenance payload from registry + supplements."""
    service_key = normalize_service_key(hint)
    if not service_key:
        return None
    entry = resolve_service(service_key)
    if not entry:
        logger.warning("provenance registry has no service entry for %s", service_key)
        return None
    provenance = deepcopy(entry.get("provenance") or {})
    if not isinstance(provenance, dict):
        provenance = {}
    provenance["service_key"] = service_key
    provenance.setdefault("service_name", _display_name(service_key))
    if supplements:
        provenance.update({k: v for k, v in supplements.items() if v not in (None, "", [])})
    provenance.setdefault("archive_version", archive_version or DEFAULT_ARCHIVE_VERSIONS.get(service_key))
    provenance.setdefault(
        "source_authority",
        source_authority or DEFAULT_SOURCE_AUTHORITIES.get(service_key, "project_registry"),
    )
    provenance.setdefault(
        "source_urls",
        _dedupe(
            str(value)
            for value in (
                provenance.get("reference_url"),
                provenance.get("credits_page_url"),
            )
            if value
        ),
    )
    provenance.setdefault(
        "archive_ids",
        _dedupe(str(value) for value in (provenance.get("ivoid"), provenance.get("service_key")) if value),
    )
    return provenance


def check_freshness(
    registry: dict[str, Any] | None = None,
    *,
    warn_days: int = 180,
    today: date | None = None,
) -> list[str]:
    """Return warning strings for entries older than warn_days."""
    reg = registry or load_registry()
    current = today or date.today()
    warnings: list[str] = []
    services = reg.get("services")
    if not isinstance(services, dict) or not services:
        # Total registry loss (missing/unparseable file -> empty services) is
        # the most severe provenance failure, yet it produces no per-entry
        # staleness warning. Surface it explicitly so the startup freshness
        # gate fails closed instead of silently serving traffic with no
        # registry-backed dataset provenance.
        warnings.append("registry has no service entries (missing or unparseable fallback_registry.yaml)")
        return warnings

    for service_key, entry in services.items():
        metadata = entry.get("metadata") if isinstance(entry, dict) else {}
        last_verified_raw = metadata.get("last_verified") if isinstance(metadata, dict) else None
        if not last_verified_raw:
            warnings.append(f"{service_key}: missing metadata.last_verified")
            continue
        try:
            last_verified = datetime.strptime(str(last_verified_raw), "%Y-%m-%d").date()
        except ValueError:
            warnings.append(f"{service_key}: invalid metadata.last_verified={last_verified_raw!r}")
            continue
        age_days = (current - last_verified).days
        if age_days > warn_days:
            warnings.append(f"{service_key}: registry entry is {age_days} days old")
    return warnings


def _entry_with_key(service_key: str, entry: Any) -> dict[str, Any]:
    cloned = deepcopy(entry) if isinstance(entry, dict) else {}
    cloned.setdefault("service_key", service_key)
    provenance = cloned.get("provenance")
    if isinstance(provenance, dict):
        provenance.setdefault("service_key", service_key)
    return cloned


def _display_name(service_key: str) -> str:
    return {
        "vizier": "VizieR",
        "gaia": "Gaia DR3",
        "simbad": "SIMBAD",
        "ned": "NED",
        "2mass": "2MASS",
        "alma": "ALMA Science Archive",
    }.get(service_key, service_key)


def _dedupe(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result

"""Fallback registry loader for provenance v2."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path(__file__).with_name("fallback_registry.yaml")


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
    services = reg.get("services") or {}
    if not isinstance(services, dict):
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

"""Best-effort IVOA DataOrigin resolver for provenance v2."""

from __future__ import annotations

import logging
from typing import Any

from app.services.provenance_v2.registry_loader import dataset_from_registry

logger = logging.getLogger(__name__)


def resolve_ivoa_dataorigin(
    payload: Any,
    *,
    service_hint: str,
    archive_version: str | None = None,
) -> dict[str, Any] | None:
    """Resolve dataset provenance via IVOA DataOrigin, falling back to registry.

    The astropy DataOrigin helper is optional across environments. Resolver
    failures warn and return registry metadata rather than failing a connector.
    """
    supplements: dict[str, Any] = {}
    try:
        from astropy.io.votable.dataorigin import extract_data_origin

        origin = extract_data_origin(payload)
        supplements.update(_origin_to_dict(origin))
    except Exception as exc:
        logger.warning("DataOrigin resolver failed for %s: %s", service_hint, exc)

    return dataset_from_registry(
        service_hint,
        source_authority="datacenter_ivoa_compliant",
        archive_version=archive_version,
        supplements=supplements,
    )


def _origin_to_dict(origin: Any) -> dict[str, Any]:
    if origin is None:
        return {}
    if isinstance(origin, dict):
        return {str(k): v for k, v in origin.items()}
    result: dict[str, Any] = {}
    field_map = {
        "ivoid": ("ivoid", "ivo_id", "publisher_did"),
        "publisher": ("publisher",),
        "creator": ("creator",),
        "article": ("article", "citation", "reference"),
        "reference_url": ("reference_url", "referenceURL", "url"),
        "rights_uri": ("rights_uri", "rightsURI"),
        "archive_version": ("archive_version", "version"),
    }
    for target, candidates in field_map.items():
        for candidate in candidates:
            value = getattr(origin, candidate, None)
            if value:
                result[target] = str(value)
                break
    return result

"""Availability gate for the provenance-v2 connector rollout."""

from __future__ import annotations

from collections.abc import Iterable
import logging
from typing import Any

logger = logging.getLogger(__name__)

V2_AVAILABLE_CONNECTORS = frozenset(
    {"vizier", "gaia", "simbad", "ned", "2mass", "alma"}
)
# User-facing alternatives list (recommended in UNAVAILABLE banner).
AVAILABLE_ALTERNATIVES = (
    "vizier", "gaia", "simbad", "ned", "2mass", "alma",
)


class ConnectorUnavailableError(RuntimeError):
    """Raised before importing or executing a connector that is gated."""

    def __init__(self, connector_name: str):
        self.connector_name = connector_name
        self.response = build_unavailable_response(connector_name)
        super().__init__(self.response["error"])


def is_available(connector_name: str) -> bool:
    """Return whether a connector is active during the provenance-v2 rollout."""
    return connector_name in V2_AVAILABLE_CONNECTORS


def record_connector_gated(connector_name: str) -> None:
    """Emit observability for a connector blocked by the rollout gate."""
    logger.info("connector_gated connector=%s", connector_name)
    try:
        from app.observability.metrics import record_counter

        record_counter("connector_gated_total", 1.0, connector_name=connector_name)
    except Exception as e:
        logger.debug("metric record_counter(connector_gated_total) failed: %s", e)


def build_unavailable_response(
    connector_name: str | Iterable[str],
    *,
    tool_name: str = "search_objects",
) -> dict[str, Any]:
    """Build the banner payload shown to the model for gated connectors."""
    connector_names = _normalize_connector_names(connector_name)
    label = ", ".join(connector_names)
    alternatives = list(AVAILABLE_ALTERNATIVES)
    message = (
        f"Connector(s) under maintenance during the provenance v2 rollout: {label}. "
        f"Use one of the currently available v2 sources instead: "
        f"{', '.join(alternatives)}."
    )
    banner = {
        "__tool_status__": "UNAVAILABLE",
        "__do_not_claim__": True,
        "__message_to_model__": (
            f"Tool `{tool_name}` could not use connector(s) {label!r}: {message} "
            "You MUST NOT claim any numerical result from the unavailable connector(s). "
            "Tell the user the source is temporarily under maintenance and suggest "
            "one of the available provenance-v2 alternatives."
        ),
        "__suggested_next_step__": (
            "Retry with one or more available v2 sources: "
            f"{', '.join(alternatives)}."
        ),
    }
    response = dict(banner)
    response.update(
        {
            "error": message,
            "error_class": "connector_under_maintenance",
            "data_origin": "unavailable",
            "analysis_status": "failed",
            "results": [],
            "total": 0,
            "per_source": [
                {
                    "source": name,
                    "status": "UNAVAILABLE",
                    "error": message,
                    "available_alternatives": alternatives,
                }
                for name in connector_names
            ],
            "unavailable_sources": connector_names,
            "available_alternatives": alternatives,
            "suggested_actions": [],
        }
    )
    return response


def _normalize_connector_names(connector_name: str | Iterable[str]) -> list[str]:
    if isinstance(connector_name, str):
        return [connector_name]
    return [str(name) for name in connector_name]

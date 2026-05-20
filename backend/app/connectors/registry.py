"""Connector registry — lazy initialization to avoid heavy imports at startup."""

from __future__ import annotations

from app.connectors.availability import (
    ConnectorUnavailableError,
    V2_AVAILABLE_CONNECTORS,
    is_available,
    record_connector_gated,
)

_connectors = None


def _init():
    global _connectors
    if _connectors is None:
        from app.connectors.gaia import GaiaConnector
        from app.connectors.alma import ALMAConnector
        from app.connectors.simbad import SIMBADConnector
        from app.connectors.vizier import VizierConnector
        from app.connectors.ned import NEDConnector
        from app.connectors.twomass import TwoMASSConnector
        from app.connectors.jpl import JPLHorizonsConnector
        from app.connectors.mpc import MPCConnector
        from app.connectors.nasa_exoplanet_archive import NASAExoplanetArchiveConnector

        _connectors = {
            "gaia": GaiaConnector(),
            "alma": ALMAConnector(),
            "simbad": SIMBADConnector(),
            "vizier": VizierConnector(),
            "ned": NEDConnector(),
            "2mass": TwoMASSConnector(),
            "jpl": JPLHorizonsConnector(),
            "mpc": MPCConnector(),
            "nasa_exoplanet_archive": NASAExoplanetArchiveConnector(),
        }


CONNECTORS_KEYS = [
    "sdss", "sdss_spec", "gaia", "simbad", "vizier", "mast", "ned", "2mass",
    "chandra", "allwise", "alma", "eso", "irsa", "jwst", "lamost", "desi",
    "panstarrs", "xmm", "nvss", "first",
    "jpl", "mpc", "atnf_pulsar", "sparc", "frbstats",
    "nasa_exoplanet_archive",
]


def get_connector(source: str):
    if source not in CONNECTORS_KEYS:
        raise ValueError(f"Unknown data source: {source}. Available: {CONNECTORS_KEYS}")
    if not is_available(source):
        record_connector_gated(source)
        raise ConnectorUnavailableError(source)

    _init()
    connector = _connectors.get(source)
    if connector is None:
        raise ValueError(
            f"Connector {source!r} is marked available but is not registered. "
            f"Available v2 connectors: {sorted(V2_AVAILABLE_CONNECTORS)}"
        )
    return connector

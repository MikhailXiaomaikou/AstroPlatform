"""Connector registry — lazy initialization to avoid heavy imports at startup."""

from __future__ import annotations

_connectors = None


def _init():
    global _connectors
    if _connectors is None:
        from app.connectors.sdss import SDSSConnector
        from app.connectors.gaia import GaiaConnector
        from app.connectors.simbad import SIMBADConnector
        from app.connectors.vizier import VizierConnector
        from app.connectors.mast import MASTConnector
        from app.connectors.ned import NEDConnector
        from app.connectors.twomass import TwoMASSConnector
        from app.connectors.chandra import ChandraConnector
        from app.connectors.allwise import AllWISEConnector
        from app.connectors.alma import ALMAConnector
        from app.connectors.eso import ESOConnector
        from app.connectors.irsa import IRSAConnector
        from app.connectors.jwst import JWSTConnector
        from app.connectors.lamost import LAMOSTConnector
        from app.connectors.panstarrs import PanSTARRSConnector
        from app.connectors.xmm import XMMConnector
        from app.connectors.desi import DESIConnector
        from app.connectors.radio import NVSSConnector, FIRSTConnector
        from app.connectors.jpl import JPLHorizonsConnector
        from app.connectors.atnf_pulsar import ATNFPulsarConnector
        from app.connectors.sparc import SPARCConnector
        from app.connectors.frbstats import FRBStatsConnector
        _connectors = {
            "sdss": SDSSConnector(),
            "gaia": GaiaConnector(),
            "simbad": SIMBADConnector(),
            "vizier": VizierConnector(),
            "mast": MASTConnector(),
            "ned": NEDConnector(),
            "2mass": TwoMASSConnector(),
            "chandra": ChandraConnector(),
            "allwise": AllWISEConnector(),
            "alma": ALMAConnector(),
            "eso": ESOConnector(),
            "irsa": IRSAConnector(),
            "jwst": JWSTConnector(),
            "lamost": LAMOSTConnector(),
            "desi": DESIConnector(),
            "panstarrs": PanSTARRSConnector(),
            "xmm": XMMConnector(),
            "nvss": NVSSConnector(),
            "first": FIRSTConnector(),
            "jpl": JPLHorizonsConnector(),
            "atnf_pulsar": ATNFPulsarConnector(),
            "sparc": SPARCConnector(),
            "frbstats": FRBStatsConnector(),
        }


CONNECTORS_KEYS = [
    "sdss", "gaia", "simbad", "vizier", "mast", "ned", "2mass", "chandra", "allwise", "alma",
    "eso", "irsa", "jwst", "lamost", "desi", "panstarrs", "xmm", "nvss", "first",
    "jpl", "atnf_pulsar", "sparc", "frbstats",
]


def get_connector(source: str):
    _init()
    connector = _connectors.get(source)
    if connector is None:
        raise ValueError(f"Unknown data source: {source}. Available: {CONNECTORS_KEYS}")
    return connector

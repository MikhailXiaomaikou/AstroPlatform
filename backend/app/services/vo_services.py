"""Virtual Observatory standards service using pyvo.

Provides SIA v2 (image access), SSA (spectral access), federated TAP queries,
and VO registry service discovery.
"""

import logging

logger = logging.getLogger(__name__)


def query_sia(service_url: str, ra: float, dec: float, radius: float = 0.1) -> dict:
    """Query a Simple Image Access v2 service."""
    try:
        import pyvo
        sia_service = pyvo.sia2.SIA2Service(service_url)
        results = sia_service.search(pos=(ra, dec, radius))

        records = []
        for r in results[:100]:  # Limit to 100
            record = {
                "title": str(getattr(r, 'title', '')),
                "access_url": str(getattr(r, 'access_url', '')),
                "access_format": str(getattr(r, 'access_format', '')),
                "instrument": str(getattr(r, 'instrument_name', '')),
            }
            try:
                record["ra"] = float(r.ra)
                record["dec"] = float(r.dec)
            except Exception:
                pass
            records.append(record)

        return {"records": records, "total": len(results), "service_url": service_url}
    except ImportError:
        return {"error": "pyvo not installed"}
    except Exception as e:
        return {"error": str(e), "service_url": service_url}


def query_ssa(service_url: str, ra: float, dec: float, radius: float = 0.01) -> dict:
    """Query a Simple Spectral Access service."""
    try:
        import pyvo
        ssa_service = pyvo.ssa.SSAService(service_url)
        results = ssa_service.search(pos=(ra, dec), diameter=radius * 2)

        records = []
        for r in results[:100]:
            record = {
                "title": str(getattr(r, 'title', '')),
                "access_url": str(getattr(r, 'acref', getattr(r, 'access_url', ''))),
                "format": str(getattr(r, 'format', '')),
            }
            records.append(record)

        return {"records": records, "total": len(results), "service_url": service_url}
    except ImportError:
        return {"error": "pyvo not installed"}
    except Exception as e:
        return {"error": str(e), "service_url": service_url}


def federated_tap(adql: str, services: list[str] | None = None) -> dict:
    """Execute ADQL across multiple TAP services and merge results."""
    try:
        import pyvo
    except ImportError:
        return {"error": "pyvo not installed"}

    if services is None:
        services = ["gaia", "simbad", "vizier"]

    # Known TAP service URLs
    TAP_URLS = {
        "gaia": "https://gea.esac.esa.int/tap-server/tap",
        "simbad": "https://simbad.cds.unistra.fr/simbad/sim-tap",
        "vizier": "https://tapvizier.cds.unistra.fr/TAPVizieR/tap",
        "cadc": "https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus",
        "ned": "https://ned.ipac.caltech.edu/tap",
        "heasarc": "https://heasarc.gsfc.nasa.gov/xamin/vo/tap",
    }

    all_results = []
    errors = {}

    for svc_name in services:
        url = TAP_URLS.get(svc_name)
        if not url:
            errors[svc_name] = f"Unknown TAP service: {svc_name}"
            continue
        try:
            tap = pyvo.dal.TAPService(url)
            result = tap.run_sync(adql, maxrec=1000)
            table = result.to_table()
            rows = []
            for row in table:
                r = {}
                for col in table.colnames:
                    val = row[col]
                    if hasattr(val, 'item'):
                        val = val.item()
                    if isinstance(val, (float, int)):
                        if val != val:  # NaN
                            val = None
                        r[col.lower()] = val
                    else:
                        r[col.lower()] = str(val) if val is not None else None
                r["_source_tap"] = svc_name
                rows.append(r)
            all_results.extend(rows)
        except Exception as e:
            errors[svc_name] = str(e)

    return {
        "results": all_results,
        "total": len(all_results),
        "services_queried": services,
        "errors": errors if errors else None,
    }


def discover_services(keyword: str, service_type: str = "tap") -> list[dict]:
    """Discover VO services matching a keyword via the VO Registry."""
    try:
        import pyvo

        if service_type == "tap":
            results = pyvo.regsearch(keywords=[keyword], servicetype="tap")
        elif service_type == "sia":
            results = pyvo.regsearch(keywords=[keyword], servicetype="sia")
        elif service_type == "ssa":
            results = pyvo.regsearch(keywords=[keyword], servicetype="ssa")
        else:
            results = pyvo.regsearch(keywords=[keyword])

        services = []
        for r in results[:50]:
            svc = {
                "title": str(getattr(r, 'res_title', '')),
                "description": str(getattr(r, 'res_description', ''))[:200],
                "access_url": str(getattr(r, 'access_url', '')),
                "ivoid": str(getattr(r, 'ivoid', '')),
                "service_type": service_type,
            }
            services.append(svc)

        return services
    except ImportError:
        return [{"error": "pyvo not installed"}]
    except Exception as e:
        return [{"error": str(e)}]

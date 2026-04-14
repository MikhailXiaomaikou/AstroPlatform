"""Pipeline node: exoplanet transit model fitting."""
import logging
logger = logging.getLogger(__name__)

def transit_fit(input_data: dict, params: dict) -> dict:
    from app.services.time_domain_pro import fit_transit

    time = input_data.get("time", [])
    flux = input_data.get("flux", [])
    flux_err = input_data.get("flux_err")

    result = fit_transit(
        time, flux, flux_err,
        period=params.get("period", 1.0),
        t0=params.get("t0", 0.0),
        rp_rs=params.get("rp_rs", 0.1),
        a_rs=params.get("a_rs", 10.0),
        inc=params.get("inc", 90.0),
    )
    return {**input_data, **result, "node_type": "TransitFit"}

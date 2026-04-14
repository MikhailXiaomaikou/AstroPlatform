"""Pipeline node: Gaussian Process light curve detrending."""
import logging
logger = logging.getLogger(__name__)

def gp_detrend_node(input_data: dict, params: dict) -> dict:
    from app.services.time_domain_pro import gp_detrend

    time = input_data.get("time", [])
    flux = input_data.get("flux", [])
    flux_err = input_data.get("flux_err")

    result = gp_detrend(time, flux, flux_err, kernel=params.get("kernel", "matern32"))
    return {**input_data, **result, "node_type": "GPDetrend"}

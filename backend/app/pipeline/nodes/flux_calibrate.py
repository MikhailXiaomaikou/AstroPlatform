"""Pipeline node: flux calibration using sensitivity function."""
import logging
logger = logging.getLogger(__name__)


def flux_calibrate(input_data: dict, params: dict) -> dict:
    """Apply flux calibration to a spectrum."""
    from app.services.spectral_analysis_pro import flux_calibrate as _calibrate

    wavelength = input_data.get("wavelength", [])
    flux = input_data.get("flux", [])
    sens_w = params.get("sensitivity_wavelength")
    sens_r = params.get("sensitivity_response")

    result = _calibrate(wavelength, flux, sens_w, sens_r)
    return {**input_data, **result, "node_type": "FluxCalibrate"}

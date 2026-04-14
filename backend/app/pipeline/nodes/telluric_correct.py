"""Pipeline node: telluric absorption correction."""
import logging
logger = logging.getLogger(__name__)


def telluric_correct(input_data: dict, params: dict) -> dict:
    """Correct telluric absorption in a spectrum."""
    from app.services.spectral_analysis_pro import telluric_correct as _correct

    wavelength = input_data.get("wavelength", [])
    flux = input_data.get("flux", [])
    tel_w = params.get("telluric_wavelength")
    tel_t = params.get("telluric_transmission")

    result = _correct(wavelength, flux, tel_w, tel_t)
    return {**input_data, **result, "node_type": "TelluricCorrect"}

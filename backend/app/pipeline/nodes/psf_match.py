"""Pipeline node: PSF matching."""
import logging
logger = logging.getLogger(__name__)

def psf_match_node(input_data: dict, params: dict) -> dict:
    from app.services.image_processing_pro import psf_match
    fits_path = input_data.get("fits_path", input_data.get("output_path", ""))
    result = psf_match(fits_path,
                      target_fwhm=params.get("target_fwhm", 5.0),
                      current_fwhm=params.get("current_fwhm"))
    return {**input_data, **result, "node_type": "PSFMatch"}

"""Pipeline node: source deblending."""
import logging
logger = logging.getLogger(__name__)

def deblend_node(input_data: dict, params: dict) -> dict:
    from app.services.image_processing_pro import deblend_sources
    fits_path = input_data.get("fits_path", input_data.get("output_path", ""))
    result = deblend_sources(fits_path,
                            threshold_sigma=params.get("threshold_sigma", 2.0),
                            npixels=params.get("npixels", 5),
                            contrast=params.get("contrast", 0.001))
    return {**input_data, **result, "node_type": "Deblend"}

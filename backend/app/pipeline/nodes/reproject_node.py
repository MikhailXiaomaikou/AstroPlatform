"""Pipeline node: image reprojection."""
import logging
logger = logging.getLogger(__name__)

def reproject_node(input_data: dict, params: dict) -> dict:
    from app.services.image_processing_pro import reproject_image
    fits_path = input_data.get("fits_path", input_data.get("output_path", ""))
    result = reproject_image(
        fits_path,
        target_wcs_fits=params.get("target_wcs_fits"),
        target_ra=params.get("target_ra"),
        target_dec=params.get("target_dec"),
        pixscale=params.get("pixscale", 1.0),
        size_pix=params.get("size_pix", 500),
        method=params.get("method", "interp"),
    )
    return {**input_data, **result, "node_type": "Reproject"}

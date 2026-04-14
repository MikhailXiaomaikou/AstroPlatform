"""Pipeline node: image mosaicking."""
import logging
logger = logging.getLogger(__name__)

def mosaic_node(input_data: dict, params: dict) -> dict:
    from app.services.image_processing_pro import mosaic_images
    fits_paths = input_data.get("fits_paths", params.get("fits_paths", []))
    if not fits_paths and "output_path" in input_data:
        fits_paths = [input_data["output_path"]]
    result = mosaic_images(fits_paths, method=params.get("method", "mean"),
                          pixscale=params.get("pixscale"))
    return {**input_data, **result, "node_type": "Mosaic"}

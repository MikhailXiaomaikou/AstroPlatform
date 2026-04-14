"""Pipeline node: research-grade photometric redshift estimation."""
import logging
logger = logging.getLogger(__name__)

def photo_z_pro(input_data: dict, params: dict) -> dict:
    """Estimate photometric redshift using enhanced template fitting."""
    from app.services.photo_z_pro import fit_template_enhanced

    magnitudes = input_data.get("magnitudes", params.get("magnitudes", {}))
    mag_errors = input_data.get("mag_errors", params.get("mag_errors"))

    if not magnitudes:
        # Try to extract from search results
        if "extra" in input_data:
            extra = input_data["extra"]
            for band in ["u", "g", "r", "i", "z", "J", "H", "Ks"]:
                key = f"mag_{band}"
                if key in extra:
                    magnitudes[band] = extra[key]

    if not magnitudes:
        return {**input_data, "error": "No photometric data found", "node_type": "PhotoZPro"}

    result = fit_template_enhanced(
        magnitudes=magnitudes,
        mag_errors=mag_errors,
        z_range=tuple(params.get("z_range", [0.0, 6.0])),
        z_step=params.get("z_step", 0.01),
        prior=params.get("prior", "flat"),
        include_emission_lines=params.get("emission_lines", True),
        include_igm=params.get("igm", True),
    )

    return {**input_data, **result, "node_type": "PhotoZPro"}

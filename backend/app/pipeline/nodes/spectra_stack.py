"""Pipeline node: stack multiple spectra."""
import logging
import numpy as np
logger = logging.getLogger(__name__)


def spectra_stack(input_data: dict, params: dict) -> dict:
    """Stack multiple spectra onto a common wavelength grid."""
    spectra = input_data.get("spectra", [])
    method = params.get("method", "median")
    rest_frame = params.get("rest_frame", False)
    z_key = params.get("z_key", "redshift")

    if not spectra or len(spectra) < 2:
        return {**input_data, "error": "Need at least 2 spectra to stack"}

    # Determine common wavelength grid
    all_waves = []
    for s in spectra:
        w = np.array(s.get("wavelength", []))
        if rest_frame:
            z = s.get(z_key, 0.0)
            w = w / (1.0 + z)
        all_waves.append(w)

    wave_min = max(w.min() for w in all_waves)
    wave_max = min(w.max() for w in all_waves)
    dw = np.median([np.median(np.diff(w)) for w in all_waves])
    common_wave = np.arange(wave_min, wave_max, dw)

    # Interpolate and stack
    from scipy.interpolate import interp1d
    flux_matrix = []
    for i, s in enumerate(spectra):
        w = all_waves[i]
        f = np.array(s.get("flux", []))
        interp = interp1d(w, f, bounds_error=False, fill_value=np.nan)
        flux_matrix.append(interp(common_wave))

    flux_matrix = np.array(flux_matrix)
    if method == "median":
        stacked = np.nanmedian(flux_matrix, axis=0)
    elif method == "mean":
        stacked = np.nanmean(flux_matrix, axis=0)
    else:
        stacked = np.nanmedian(flux_matrix, axis=0)

    scatter = np.nanstd(flux_matrix, axis=0) / np.sqrt(np.sum(~np.isnan(flux_matrix), axis=0))

    return {
        "wavelength": common_wave.tolist(),
        "flux": stacked.tolist(),
        "flux_err": scatter.tolist(),
        "n_spectra": len(spectra),
        "method": method,
        "rest_frame": rest_frame,
        "node_type": "SpectraStack",
    }

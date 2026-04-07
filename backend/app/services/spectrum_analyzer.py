"""Automated spectrum analysis service.

Extracts features from a spectrum (peaks, continuum, redshift) and
optionally sends a compact summary to Claude for AI interpretation.
"""

import io
import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Peak:
    wavelength: float
    flux: float
    snr: float
    is_emission: bool  # True = emission, False = absorption


@dataclass
class SpectrumSummary:
    wavelength_min: float
    wavelength_max: float
    n_points: int
    mean_flux: float
    median_flux: float
    std_flux: float
    continuum_shape: str  # "rising", "falling", "flat", "peaked", "u-shaped"
    continuum_slope: float
    peaks: list[Peak] = field(default_factory=list)


def extract_spectrum_from_fits(fits_path: str, max_points: int = 2000) -> dict:
    """Read spectrum from a FITS file. Returns {wavelength: [...], flux: [...]}."""
    from astropy.io import fits
    from astropy.table import Table
    from app.storage import download_fits

    raw = download_fits(fits_path)
    hdul = fits.open(io.BytesIO(raw))

    try:
        # Try binary table first
        for hdu in hdul:
            if isinstance(hdu, (fits.BinTableHDU, fits.TableHDU)):
                table = Table.read(hdu)
                # Find wavelength and flux columns
                wave_col = None
                flux_col = None
                for col in table.colnames:
                    cl = col.lower()
                    if cl in ("wavelength", "wave", "lambda", "loglam"):
                        wave_col = col
                    elif cl in ("flux", "counts", "intensity", "data"):
                        flux_col = col

                if wave_col and flux_col:
                    wave = np.array(table[wave_col], dtype=float)
                    flux_arr = np.array(table[flux_col], dtype=float)

                    # Handle loglam (SDSS uses log10 wavelength)
                    if wave_col.lower() == "loglam":
                        wave = 10**wave

                    # Clean NaN/Inf
                    valid = np.isfinite(wave) & np.isfinite(flux_arr)
                    wave = wave[valid]
                    flux_arr = flux_arr[valid]

                    # Downsample
                    if len(wave) > max_points:
                        step = len(wave) // max_points
                        wave = wave[::step]
                        flux_arr = flux_arr[::step]

                    return {"wavelength": wave.tolist(), "flux": flux_arr.tolist()}

        # Fallback: primary HDU 1D data
        if hdul[0].data is not None and hdul[0].data.ndim == 1:
            flux_arr = hdul[0].data.astype(float)

            # Build wavelength from header WCS BEFORE filtering NaN
            header = hdul[0].header
            crval1 = header.get("CRVAL1", 0)
            cdelt1 = header.get("CDELT1", header.get("CD1_1", 1))
            crpix1 = header.get("CRPIX1", 1)
            n_orig = len(flux_arr)
            wave = crval1 + (np.arange(n_orig) - (crpix1 - 1)) * cdelt1

            # Now filter NaN from both arrays in sync
            valid = np.isfinite(flux_arr)
            flux_arr = flux_arr[valid]
            wave = wave[valid]

            if len(wave) > max_points:
                step = len(wave) // max_points
                wave = wave[::step]
                flux_arr = flux_arr[::step]

            return {"wavelength": wave.tolist(), "flux": flux_arr.tolist()}

        raise ValueError("No spectrum data found in FITS file")
    finally:
        hdul.close()


def analyze_spectrum(wavelength: list[float], flux: list[float]) -> SpectrumSummary:
    """Run automated spectral analysis. Returns a compact summary."""
    w = np.array(wavelength)
    f = np.array(flux)

    if len(w) < 20:
        raise ValueError("Spectrum too short for analysis (need >= 20 points)")

    # Basic statistics
    mean_flux = float(np.mean(f))
    median_flux = float(np.median(f))
    std_flux = float(np.std(f))

    # Continuum: fit low-order polynomial after sigma-clipping
    from scipy.stats import sigmaclip
    clipped_f, low, high = sigmaclip(f, low=2.5, high=2.5)
    mask = (f >= low) & (f <= high)
    if np.sum(mask) > 3:
        coeffs = np.polyfit(w[mask], f[mask], deg=2)
        cont_model = np.poly1d(coeffs)
        cont_slope = float(coeffs[-2]) if len(coeffs) > 1 else 0.0
    else:
        cont_model = lambda x: np.full_like(x, median_flux)
        cont_slope = 0.0
        coeffs = [0, 0, median_flux]

    # Classify continuum shape
    cont_vals = cont_model(w)
    left_third = np.mean(cont_vals[:len(w) // 3])
    right_third = np.mean(cont_vals[-len(w) // 3:])
    mid_third = np.mean(cont_vals[len(w) // 3: 2 * len(w) // 3])
    frac_change = (right_third - left_third) / max(abs(median_flux), 1e-30)

    if abs(frac_change) < 0.1:
        if mid_third > max(left_third, right_third) * 1.1:
            continuum_shape = "peaked"
        elif mid_third < min(left_third, right_third) * 0.9:
            continuum_shape = "u-shaped"
        else:
            continuum_shape = "flat"
    elif frac_change > 0.1:
        continuum_shape = "rising"
    else:
        continuum_shape = "falling"

    # Peak detection: find emission peaks (above continuum) and absorption troughs
    residuals = f - cont_model(w)
    mad = np.median(np.abs(residuals))
    noise = mad * 1.4826 if mad > 0 else std_flux

    peaks: list[Peak] = []

    # Emission peaks
    for i in range(2, len(f) - 2):
        if residuals[i] > residuals[i - 1] and residuals[i] > residuals[i + 1]:
            snr = residuals[i] / max(noise, 1e-30)
            if snr > 3.0:
                peaks.append(Peak(
                    wavelength=float(w[i]),
                    flux=float(f[i]),
                    snr=float(snr),
                    is_emission=True,
                ))

    # Absorption troughs
    for i in range(2, len(f) - 2):
        if residuals[i] < residuals[i - 1] and residuals[i] < residuals[i + 1]:
            snr = abs(residuals[i]) / max(noise, 1e-30)
            if snr > 3.0:
                peaks.append(Peak(
                    wavelength=float(w[i]),
                    flux=float(f[i]),
                    snr=float(snr),
                    is_emission=False,
                ))

    # Sort by SNR, keep top 30
    peaks.sort(key=lambda p: p.snr, reverse=True)
    peaks = peaks[:30]

    return SpectrumSummary(
        wavelength_min=float(w[0]),
        wavelength_max=float(w[-1]),
        n_points=len(w),
        mean_flux=mean_flux,
        median_flux=median_flux,
        std_flux=std_flux,
        continuum_shape=continuum_shape,
        continuum_slope=cont_slope,
        peaks=peaks,
    )


def build_claude_prompt(summary: SpectrumSummary, redshift_result: dict | None) -> str:
    """Build a compact prompt for Claude from the spectrum summary."""
    lines = [
        f"Spectrum: {summary.n_points} points, "
        f"{summary.wavelength_min:.1f} - {summary.wavelength_max:.1f} Angstroms",
        f"Continuum: {summary.continuum_shape} (slope={summary.continuum_slope:.4e})",
        f"Flux stats: mean={summary.mean_flux:.4e}, median={summary.median_flux:.4e}, std={summary.std_flux:.4e}",
        "",
    ]

    em_peaks = [p for p in summary.peaks if p.is_emission]
    abs_peaks = [p for p in summary.peaks if not p.is_emission]

    if em_peaks:
        lines.append(f"Emission peaks ({len(em_peaks)}):")
        for p in em_peaks[:15]:
            lines.append(f"  {p.wavelength:.1f} A  SNR={p.snr:.1f}")

    if abs_peaks:
        lines.append(f"Absorption features ({len(abs_peaks)}):")
        for p in abs_peaks[:15]:
            lines.append(f"  {p.wavelength:.1f} A  SNR={p.snr:.1f}")

    if redshift_result:
        z = redshift_result.get("best_z", 0)
        conf = redshift_result.get("confidence", 0)
        quality = redshift_result.get("quality", "low")
        matched = redshift_result.get("matched_lines", [])
        lines.append(f"\nAutomated redshift estimate: z={z:.6f} (confidence={conf:.2f}, quality={quality})")
        if matched:
            lines.append("Matched lines:")
            for m in matched[:10]:
                lines.append(f"  {m['line']}: rest={m['rest_wavelength']:.1f} obs={m['observed_wavelength']:.1f}")

    return "\n".join(lines)


SPECTRUM_SYSTEM_PROMPT = """You are an expert astronomical spectroscopist analyzing a spectrum.
Based on the provided spectrum summary (peak wavelengths, continuum shape, automated redshift estimate), provide your analysis.

Respond with ONLY valid JSON (no markdown, no extra text) with these keys:
{
  "classification": "object type (e.g. 'Seyfert 2 galaxy', 'K2 III star', 'QSO')",
  "confidence": "high" or "medium" or "low",
  "redshift": {"value": number or null, "uncertainty": number or null},
  "lines": [
    {"name": "line name", "rest_wavelength": number, "observed_wavelength": number,
     "type": "emission" or "absorption", "strength": "strong/moderate/weak",
     "ew_note": "optional note about equivalent width"}
  ],
  "continuum_interpretation": "what the continuum shape tells us",
  "special_features": ["list of notable features"],
  "summary": "2-3 sentence summary for the astronomer",
  "narrative": "1-2 paragraph detailed explanation",
  "next_steps": ["suggested analysis steps"]
}"""


async def ai_interpret(
    summary: SpectrumSummary,
    redshift_result: dict | None,
    api_key: str,
) -> dict:
    """Send spectrum summary to Claude and get AI interpretation."""
    import anthropic

    prompt_text = build_claude_prompt(summary, redshift_result)

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        system=SPECTRUM_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt_text}],
    )

    import json
    text = response.content[0].text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Claude returned invalid JSON: %s", text[:200])
        return {
            "classification": "unknown",
            "confidence": "low",
            "summary": text[:500],
            "narrative": text,
            "lines": [],
            "special_features": [],
            "next_steps": ["Manual inspection recommended — AI response was not structured"],
        }

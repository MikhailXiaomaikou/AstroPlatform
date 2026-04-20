"""Equivalent width node — measure EW of absorption/emission spectral lines."""

import numpy as np
from scipy.integrate import trapezoid


def equivalent_width(input_data: dict, params: dict) -> dict:
    """Measure the equivalent width of a spectral line.

    params:
        line_center: float — central wavelength of the line (required)
        continuum_window: list[float, float] — [lo, hi] wavelength range for
            continuum estimation (default: line_center +/- 50 A, excluding line).
            Legacy single-window form; mixes in line wings if user's window
            is too narrow.
        continuum_left: list[float, float] — L2-d (audit 2026-04-20): 显式
            左侧连续谱窗口 [lo, hi].  与 continuum_right 一起使用时取代
            continuum_window, **不包含** line_window, 符合 Gray 2005 恒星
            分光惯例 (两段独立连续谱 + polynomial degree ≤ 2).
        continuum_right: list[float, float] — 显式右侧连续谱窗口.
        line_window: list[float, float] — [lo, hi] wavelength range for
            the line integration (default: line_center +/- 10 A)
        flux_key: str — key for flux array (default "flux")
        wavelength_key: str — key for wavelength array (default "wavelength")
        continuum_method: str — "linear" (default), "median", "polynomial", "spline"
        poly_order: int — polynomial order for "polynomial" method (default 3;
            但 L2-d: 若传 continuum_left/right 则强制改 ≤2 避免越线翼)
    """
    flux_key = params.get("flux_key", "flux")
    wave_key = params.get("wavelength_key", "wavelength")
    line_center = params.get("line_center")
    continuum_method = params.get("continuum_method", "linear")
    poly_order = int(params.get("poly_order", 3))

    if line_center is None:
        raise ValueError("EquivalentWidth: 'line_center' parameter is required")

    line_center = float(line_center)

    data = input_data.get("data", {})
    flux = np.array(data.get(flux_key, []), dtype=float)
    wavelength = np.array(data.get(wave_key, []), dtype=float)

    if len(flux) == 0 or len(wavelength) == 0:
        raise ValueError("EquivalentWidth: empty flux or wavelength array")

    # Default windows
    line_window = params.get("line_window", [line_center - 10.0, line_center + 10.0])
    line_lo, line_hi = float(line_window[0]), float(line_window[1])

    # L2-d: 显式两段连续谱窗口优先于 legacy continuum_window.  Gray 2005
    # 推荐双侧多项式拟合 (左+右两段分别选 ~10-20 Å), 不越线翼, polynomial
    # degree ≤ 2.
    continuum_left = params.get("continuum_left")
    continuum_right = params.get("continuum_right")
    using_two_window = continuum_left is not None and continuum_right is not None

    if using_two_window:
        left_lo, left_hi = float(continuum_left[0]), float(continuum_left[1])
        right_lo, right_hi = float(continuum_right[0]), float(continuum_right[1])
        # 验证两段不与 line_window 重叠
        if left_hi > line_lo:
            raise ValueError(
                f"EquivalentWidth: continuum_left upper bound ({left_hi}) "
                f"enters line_window ({line_lo}); widen the gap."
            )
        if right_lo < line_hi:
            raise ValueError(
                f"EquivalentWidth: continuum_right lower bound ({right_lo}) "
                f"enters line_window ({line_hi}); widen the gap."
            )
        cont_mask = (
            ((wavelength >= left_lo) & (wavelength <= left_hi))
            | ((wavelength >= right_lo) & (wavelength <= right_hi))
        )
        cont_lo, cont_hi = left_lo, right_hi  # for bookkeeping in return
        # 两段模式下 polynomial degree 强制 ≤ 2 (Gray 2005)
        if continuum_method == "polynomial":
            poly_order = min(poly_order, 2)
    else:
        # Legacy 单窗口形式 (向后兼容) — 仍然在 continuum_window 内排除
        # line_window.  默认 continuum_window 是 line_center ± 50 Å, 这
        # 等价于左右各 40 Å 的带减去中间 20 Å, 行为上跟两段已经是分离的.
        continuum_window = params.get(
            "continuum_window", [line_center - 50.0, line_center + 50.0]
        )
        cont_lo, cont_hi = float(continuum_window[0]), float(continuum_window[1])
        cont_mask = (
            (wavelength >= cont_lo)
            & (wavelength <= cont_hi)
            & ~((wavelength >= line_lo) & (wavelength <= line_hi))
        )

    if np.sum(cont_mask) < 2:
        raise ValueError(
            "EquivalentWidth: not enough continuum pixels. "
            "Adjust continuum_window or line_window."
        )

    cont_wave = wavelength[cont_mask]
    cont_flux = flux[cont_mask]

    # Build continuum model based on method
    if continuum_method == "median":
        cont_level_val = float(np.median(cont_flux))
        def continuum_model(w, _v=cont_level_val):
            return np.full_like(w, _v)
    elif continuum_method == "polynomial":
        order = min(poly_order, len(cont_wave) - 1)
        coeffs = np.polyfit(cont_wave, cont_flux, deg=order)
        continuum_model = np.poly1d(coeffs)
    elif continuum_method == "spline":
        try:
            from scipy.interpolate import UnivariateSpline
            spl = UnivariateSpline(cont_wave, cont_flux, s=len(cont_wave))
            continuum_model = spl
        except Exception:
            # Fall back to linear
            coeffs = np.polyfit(cont_wave, cont_flux, deg=1)
            continuum_model = np.poly1d(coeffs)
    else:
        # linear (default)
        coeffs = np.polyfit(cont_wave, cont_flux, deg=1)
        continuum_model = np.poly1d(coeffs)

    # Select line pixels
    line_mask = (wavelength >= line_lo) & (wavelength <= line_hi)
    if np.sum(line_mask) < 2:
        raise ValueError("EquivalentWidth: not enough pixels in line window")

    line_wave = wavelength[line_mask]
    line_flux = flux[line_mask]
    line_continuum = continuum_model(line_wave)

    # Guard against zero or near-zero continuum
    safe_continuum = np.where(np.abs(line_continuum) < 1e-30, 1e-30, line_continuum)

    # Equivalent width: integral of (1 - F_line / F_continuum) dλ
    # Positive EW = absorption, negative EW = emission
    integrand = 1.0 - line_flux / safe_continuum
    ew_value = float(trapezoid(integrand, line_wave))

    # Line flux (integrated flux above/below continuum)
    line_flux_integrated = float(trapezoid(line_flux - line_continuum, line_wave))

    # Error estimate via continuum RMS
    cont_residuals = cont_flux - continuum_model(cont_wave)
    cont_rms = float(np.std(cont_residuals))
    cont_level = float(np.mean(line_continuum))

    n_line_pix = np.sum(line_mask)
    if len(line_wave) > 1:
        delta_lambda = float(np.median(np.diff(line_wave)))
    else:
        delta_lambda = 1.0

    ew_error = float(
        np.sqrt(n_line_pix) * delta_lambda * cont_rms / max(cont_level, 1e-30)
    )

    # Signal-to-noise of the line detection
    snr = abs(ew_value) / ew_error if ew_error > 0 else 0.0

    # Line type classification
    if ew_value > 0:
        line_type = "absorption"
    elif ew_value < 0:
        line_type = "emission"
    else:
        line_type = "none"

    return {
        **input_data,
        "equivalent_width_result": {
            "ew_value": ew_value,
            "ew_error": ew_error,
            "snr": float(snr),
            "line_type": line_type,
            "continuum_level": cont_level,
            "continuum_rms": cont_rms,
            "continuum_method": continuum_method,
            "line_flux": line_flux_integrated,
            "line_center": line_center,
            "line_window": [line_lo, line_hi],
            "continuum_window": [cont_lo, cont_hi],
            "n_line_pixels": int(n_line_pix),
        },
    }

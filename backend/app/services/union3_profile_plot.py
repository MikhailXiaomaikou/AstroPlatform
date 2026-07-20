"""Pure deterministic SVG renderer shared by Worker and control plane."""

from __future__ import annotations

from typing import Any


class Union3ProfilePlotError(ValueError):
    """Raised when a primary-analysis receipt cannot produce the fixed plot."""


def render_union3_profile_svg(primary_analysis: dict[str, Any]) -> str:
    """Render the registered 41-point profile without control-plane imports."""

    try:
        rows = primary_analysis["normalized_profile"]
        statistics = primary_analysis["statistics"]
        samples = [
            (
                float(row["omega_m"]),
                max(0.0, float(row["normalized_chi_square"])),
            )
            for row in rows
        ]
        best = float(statistics["omega_m_best"])
        lower = float(statistics["omega_m_lower"])
        upper = float(statistics["omega_m_upper"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Union3ProfilePlotError(
            "The verified primary receipt cannot produce a profile plot"
        ) from exc
    if len(samples) != 41 or samples != sorted(samples):
        raise Union3ProfilePlotError(
            "The verified primary profile grid is invalid"
        )

    width, height = 800, 480
    left, right, top, bottom = 78, 28, 28, 66
    plot_width = width - left - right
    plot_height = height - top - bottom
    x_min, x_max = samples[0][0], samples[-1][0]
    y_max = 10.0

    def x_position(value: float) -> float:
        return left + (value - x_min) / (x_max - x_min) * plot_width

    def y_position(value: float) -> float:
        return top + (1.0 - min(value, y_max) / y_max) * plot_height

    points = " ".join(
        f"{x_position(omega):.2f},{y_position(delta):.2f}"
        for omega, delta in samples
    )
    marker_lines = "\n".join(
        (
            f'<line x1="{x_position(lower):.2f}" y1="{top}" '
            f'x2="{x_position(lower):.2f}" y2="{top + plot_height}" '
            'stroke="#7c3aed" stroke-dasharray="5 5"/>',
            f'<line x1="{x_position(best):.2f}" y1="{top}" '
            f'x2="{x_position(best):.2f}" y2="{top + plot_height}" '
            'stroke="#dc2626" stroke-width="2"/>',
            f'<line x1="{x_position(upper):.2f}" y1="{top}" '
            f'x2="{x_position(upper):.2f}" y2="{top + plot_height}" '
            'stroke="#7c3aed" stroke-dasharray="5 5"/>',
        )
    )
    delta_one_y = y_position(1.0)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="480" '
        'viewBox="0 0 800 480" role="img" '
        'aria-label="Union3 normalized profile chi-square curve">\n'
        '<rect width="800" height="480" fill="white"/>\n'
        f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" '
        f'y2="{top + plot_height}" stroke="#111827"/>\n'
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" '
        'stroke="#111827"/>\n'
        f'<line x1="{left}" y1="{delta_one_y:.2f}" x2="{left + plot_width}" '
        f'y2="{delta_one_y:.2f}" stroke="#059669" stroke-dasharray="7 5"/>\n'
        f"{marker_lines}\n"
        f'<polyline points="{points}" fill="none" stroke="#2563eb" '
        'stroke-width="2.5" stroke-linejoin="round"/>\n'
        '<text x="400" y="458" text-anchor="middle" font-family="sans-serif" '
        'font-size="17">Ωm</text>\n'
        '<text x="22" y="220" text-anchor="middle" font-family="sans-serif" '
        'font-size="17" transform="rotate(-90 22 220)">Δχ²</text>\n'
        '<text x="790" y="54" text-anchor="end" font-family="sans-serif" '
        'font-size="13" fill="#059669">Δχ² = 1</text>\n'
        '<text x="790" y="76" text-anchor="end" font-family="sans-serif" '
        'font-size="13" fill="#374151">Union3 SN-only flat ΛCDM</text>\n'
        "</svg>\n"
    )

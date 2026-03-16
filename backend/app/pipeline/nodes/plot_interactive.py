"""Interactive plot node — generates Plotly JSON for astronomy visualizations."""

import numpy as np
from typing import Any


CHART_TEMPLATES = {
    "hr_diagram": {
        "name": "HR Diagram",
        "description": "Hertzsprung-Russell diagram (color-magnitude or Teff-Luminosity)",
        "required_keys": ["color_or_teff", "magnitude_or_luminosity"],
    },
    "sed_fit": {
        "name": "SED Fit",
        "description": "Spectral Energy Distribution with fit overlay",
        "required_keys": ["wavelength", "flux"],
    },
    "spectrum_overlay": {
        "name": "Spectrum Overlay",
        "description": "Multiple spectra overlaid for comparison",
        "required_keys": ["wavelength", "flux"],
    },
    "redshift_histogram": {
        "name": "Redshift Histogram",
        "description": "Distribution of redshift values",
        "required_keys": ["redshift"],
    },
    "sky_coverage": {
        "name": "Sky Coverage (Aitoff)",
        "description": "All-sky Aitoff projection of source positions",
        "required_keys": ["ra", "dec"],
    },
    "correlation_scatter": {
        "name": "Correlation Scatter",
        "description": "Scatter plot with optional linear/polynomial fit",
        "required_keys": ["x", "y"],
    },
    "corner_plot": {
        "name": "Corner Plot",
        "description": "Corner/triangle plot for parameter correlations",
        "required_keys": ["params"],
    },
}

DARK_LAYOUT = {
    "paper_bgcolor": "rgba(28, 28, 30, 1)",
    "plot_bgcolor": "rgba(44, 44, 46, 1)",
    "font": {"color": "rgba(255, 255, 255, 0.85)", "family": "SF Pro Display, system-ui"},
    "xaxis": {"gridcolor": "rgba(255,255,255,0.06)", "zerolinecolor": "rgba(255,255,255,0.1)"},
    "yaxis": {"gridcolor": "rgba(255,255,255,0.06)", "zerolinecolor": "rgba(255,255,255,0.1)"},
    "colorway": ["#38bdf8", "#f472b6", "#34d399", "#fbbf24", "#a78bfa", "#fb923c"],
    "margin": {"l": 60, "r": 30, "t": 50, "b": 50},
}


def _base_layout(**overrides) -> dict:
    """Return a copy of the dark layout with optional overrides merged in."""
    import copy
    layout = copy.deepcopy(DARK_LAYOUT)
    for k, v in overrides.items():
        if isinstance(v, dict) and k in layout and isinstance(layout[k], dict):
            layout[k].update(v)
        else:
            layout[k] = v
    return layout


def _build_hr_diagram(data: dict, params: dict) -> dict:
    """Hertzsprung-Russell diagram with inverted y-axis and blue-to-red color scale."""
    color_or_teff = np.array(data.get("color_or_teff", []), dtype=float)
    mag_or_lum = np.array(data.get("magnitude_or_luminosity", []), dtype=float)

    if len(color_or_teff) == 0 or len(mag_or_lum) == 0:
        return _empty_figure("HR Diagram: no data provided")

    x_label = params.get("x_label", "B-V Color Index" if np.nanmax(color_or_teff) < 100 else "Teff (K)")
    y_label = params.get("y_label", "Magnitude" if np.nanmax(mag_or_lum) > 10 else "Luminosity (L_sun)")
    invert_y = params.get("invert_y", bool(np.nanmax(mag_or_lum) > 10))

    trace = {
        "type": "scatter",
        "mode": "markers",
        "x": color_or_teff.tolist(),
        "y": mag_or_lum.tolist(),
        "marker": {
            "color": color_or_teff.tolist(),
            "colorscale": [[0, "#38bdf8"], [0.5, "#fbbf24"], [1, "#f472b6"]],
            "size": 4,
            "opacity": 0.7,
            "colorbar": {"title": x_label},
        },
        "name": "Stars",
    }

    layout = _base_layout(
        title="HR Diagram",
        xaxis={"title": x_label, "gridcolor": "rgba(255,255,255,0.06)", "zerolinecolor": "rgba(255,255,255,0.1)"},
        yaxis={
            "title": y_label,
            "autorange": "reversed" if invert_y else True,
            "gridcolor": "rgba(255,255,255,0.06)",
            "zerolinecolor": "rgba(255,255,255,0.1)",
        },
    )

    return {"data": [trace], "layout": layout}


def _build_sed_fit(data: dict, params: dict) -> dict:
    """Spectral Energy Distribution with data points and optional fit curve on log-log scale."""
    wavelength = np.array(data.get("wavelength", []), dtype=float)
    flux = np.array(data.get("flux", []), dtype=float)

    if len(wavelength) == 0 or len(flux) == 0:
        return _empty_figure("SED Fit: no data provided")

    traces = [
        {
            "type": "scatter",
            "mode": "markers",
            "x": wavelength.tolist(),
            "y": flux.tolist(),
            "marker": {"size": 6, "color": "#38bdf8"},
            "name": "Observed",
        },
    ]

    fit_wavelength = data.get("fit_wavelength", data.get("wavelength"))
    fit_flux = data.get("fit_flux")
    if fit_flux is not None:
        fit_wavelength = np.array(fit_wavelength, dtype=float)
        fit_flux = np.array(fit_flux, dtype=float)
        traces.append({
            "type": "scatter",
            "mode": "lines",
            "x": fit_wavelength.tolist(),
            "y": fit_flux.tolist(),
            "line": {"color": "#f472b6", "width": 2},
            "name": params.get("fit_label", "Best Fit"),
        })

    layout = _base_layout(
        title=params.get("title", "Spectral Energy Distribution"),
        xaxis={
            "title": "Wavelength (A)",
            "type": "log",
            "gridcolor": "rgba(255,255,255,0.06)",
            "zerolinecolor": "rgba(255,255,255,0.1)",
        },
        yaxis={
            "title": "Flux (erg/s/cm2/A)",
            "type": "log",
            "gridcolor": "rgba(255,255,255,0.06)",
            "zerolinecolor": "rgba(255,255,255,0.1)",
        },
    )

    return {"data": traces, "layout": layout}


def _build_spectrum_overlay(data: dict, params: dict) -> dict:
    """Multiple spectra overlaid for comparison."""
    wavelength = data.get("wavelength")
    flux = data.get("flux")

    if wavelength is None or flux is None:
        return _empty_figure("Spectrum Overlay: no data provided")

    traces = []

    if isinstance(flux, dict):
        wav = np.array(wavelength, dtype=float).tolist()
        for name, f_values in flux.items():
            traces.append({
                "type": "scatter",
                "mode": "lines",
                "x": wav,
                "y": np.array(f_values, dtype=float).tolist(),
                "name": name,
                "line": {"width": 1.5},
            })
    elif isinstance(flux, list) and len(flux) > 0 and isinstance(flux[0], list):
        wav = np.array(wavelength, dtype=float).tolist()
        labels = params.get("labels", [f"Spectrum {i+1}" for i in range(len(flux))])
        for i, f_values in enumerate(flux):
            label = labels[i] if i < len(labels) else f"Spectrum {i+1}"
            traces.append({
                "type": "scatter",
                "mode": "lines",
                "x": wav,
                "y": np.array(f_values, dtype=float).tolist(),
                "name": label,
                "line": {"width": 1.5},
            })
    else:
        traces.append({
            "type": "scatter",
            "mode": "lines",
            "x": np.array(wavelength, dtype=float).tolist(),
            "y": np.array(flux, dtype=float).tolist(),
            "name": params.get("label", "Spectrum"),
            "line": {"width": 1.5, "color": "#38bdf8"},
        })

    layout = _base_layout(
        title=params.get("title", "Spectrum Overlay"),
        xaxis={"title": "Wavelength (A)", "gridcolor": "rgba(255,255,255,0.06)", "zerolinecolor": "rgba(255,255,255,0.1)"},
        yaxis={"title": "Flux", "gridcolor": "rgba(255,255,255,0.06)", "zerolinecolor": "rgba(255,255,255,0.1)"},
        showlegend=True,
    )

    return {"data": traces, "layout": layout}


def _build_redshift_histogram(data: dict, params: dict) -> dict:
    """Distribution of redshift values with optional KDE overlay."""
    redshift = np.array(data.get("redshift", []), dtype=float)

    if len(redshift) == 0:
        return _empty_figure("Redshift Histogram: no data provided")

    n_bins = params.get("n_bins", 30)
    counts, bin_edges = np.histogram(redshift, bins=n_bins)
    bin_centers = ((bin_edges[:-1] + bin_edges[1:]) / 2).tolist()

    traces = [
        {
            "type": "bar",
            "x": bin_centers,
            "y": counts.tolist(),
            "marker": {"color": "#38bdf8", "opacity": 0.7},
            "name": "Count",
            "width": float(bin_edges[1] - bin_edges[0]) * 0.9,
        },
    ]

    if params.get("kde", False) and len(redshift) > 2:
        try:
            from scipy.stats import gaussian_kde
            kde = gaussian_kde(redshift)
            x_kde = np.linspace(float(np.nanmin(redshift)), float(np.nanmax(redshift)), 200)
            y_kde = kde(x_kde) * len(redshift) * (bin_edges[1] - bin_edges[0])
            traces.append({
                "type": "scatter",
                "mode": "lines",
                "x": x_kde.tolist(),
                "y": y_kde.tolist(),
                "line": {"color": "#f472b6", "width": 2},
                "name": "KDE",
            })
        except ImportError:
            pass

    layout = _base_layout(
        title=params.get("title", "Redshift Distribution"),
        xaxis={"title": "Redshift (z)", "gridcolor": "rgba(255,255,255,0.06)", "zerolinecolor": "rgba(255,255,255,0.1)"},
        yaxis={"title": "Count", "gridcolor": "rgba(255,255,255,0.06)", "zerolinecolor": "rgba(255,255,255,0.1)"},
    )

    return {"data": traces, "layout": layout}


def _build_sky_coverage(data: dict, params: dict) -> dict:
    """All-sky Aitoff projection of source positions."""
    ra = np.array(data.get("ra", []), dtype=float)
    dec = np.array(data.get("dec", []), dtype=float)

    if len(ra) == 0 or len(dec) == 0:
        return _empty_figure("Sky Coverage: no data provided")

    ra_centered = np.where(ra > 180, ra - 360, ra)

    color_key = params.get("color_key")
    color_values = None
    colorbar_title = ""
    if color_key and color_key in data:
        color_values = np.array(data[color_key], dtype=float).tolist()
        colorbar_title = color_key

    marker: dict[str, Any] = {
        "size": params.get("marker_size", 3),
        "opacity": 0.6,
    }
    if color_values is not None:
        marker["color"] = color_values
        marker["colorscale"] = "Viridis"
        marker["colorbar"] = {"title": colorbar_title}
    else:
        marker["color"] = "#38bdf8"

    trace = {
        "type": "scattergeo",
        "lon": ra_centered.tolist(),
        "lat": dec.tolist(),
        "mode": "markers",
        "marker": marker,
        "name": "Sources",
    }

    layout = _base_layout(
        title=params.get("title", "Sky Coverage"),
        geo={
            "projection": {"type": "mollweide"},
            "bgcolor": "rgba(44, 44, 46, 1)",
            "showland": False,
            "showocean": False,
            "showframe": True,
            "framecolor": "rgba(255,255,255,0.2)",
            "gridcolor": "rgba(255,255,255,0.1)",
            "lonaxis": {"range": [-180, 180], "dtick": 30},
            "lataxis": {"range": [-90, 90], "dtick": 30},
        },
    )

    return {"data": [trace], "layout": layout}


def _build_correlation_scatter(data: dict, params: dict) -> dict:
    """Scatter plot with optional linear/polynomial fit and R-squared."""
    x = np.array(data.get("x", []), dtype=float)
    y = np.array(data.get("y", []), dtype=float)

    if len(x) == 0 or len(y) == 0:
        return _empty_figure("Correlation Scatter: no data provided")

    color_values = None
    if "color" in data:
        color_values = np.array(data["color"], dtype=float).tolist()

    marker: dict[str, Any] = {"size": 5, "opacity": 0.7}
    if color_values is not None:
        marker["color"] = color_values
        marker["colorscale"] = "Viridis"
        marker["colorbar"] = {"title": params.get("color_label", "Color")}
    else:
        marker["color"] = "#38bdf8"

    traces = [
        {
            "type": "scatter",
            "mode": "markers",
            "x": x.tolist(),
            "y": y.tolist(),
            "marker": marker,
            "name": "Data",
        },
    ]

    fit_degree = params.get("fit_degree", 1)
    if len(x) >= 2:
        mask = np.isfinite(x) & np.isfinite(y)
        x_clean = x[mask]
        y_clean = y[mask]
        if len(x_clean) >= 2:
            coeffs = np.polyfit(x_clean, y_clean, deg=fit_degree)
            x_fit = np.linspace(float(np.nanmin(x_clean)), float(np.nanmax(x_clean)), 100)
            y_fit = np.polyval(coeffs, x_fit)

            y_pred = np.polyval(coeffs, x_clean)
            ss_res = np.sum((y_clean - y_pred) ** 2)
            ss_tot = np.sum((y_clean - np.mean(y_clean)) ** 2)
            r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

            fit_label = f"Fit (R2={r_squared:.4f})"
            traces.append({
                "type": "scatter",
                "mode": "lines",
                "x": x_fit.tolist(),
                "y": y_fit.tolist(),
                "line": {"color": "#f472b6", "width": 2, "dash": "dash"},
                "name": fit_label,
            })

    layout = _base_layout(
        title=params.get("title", "Correlation Scatter"),
        xaxis={"title": params.get("x_label", "X"), "gridcolor": "rgba(255,255,255,0.06)", "zerolinecolor": "rgba(255,255,255,0.1)"},
        yaxis={"title": params.get("y_label", "Y"), "gridcolor": "rgba(255,255,255,0.06)", "zerolinecolor": "rgba(255,255,255,0.1)"},
        showlegend=True,
    )

    return {"data": traces, "layout": layout}


def _build_corner_plot(data: dict, params: dict) -> dict:
    """Corner/triangle plot: N x N grid with 2D scatter off-diagonal and 1D histograms on diagonal."""
    param_data = data.get("params", {})

    if not param_data or not isinstance(param_data, dict):
        return _empty_figure("Corner Plot: no parameter data provided")

    param_names = list(param_data.keys())
    n = len(param_names)

    if n < 2:
        return _empty_figure("Corner Plot: need at least 2 parameters")

    arrays = {name: np.array(param_data[name], dtype=float) for name in param_names}
    colors = ["#38bdf8", "#f472b6", "#34d399", "#fbbf24", "#a78bfa", "#fb923c"]

    traces = []
    spacing = 0.02
    cell_size = (1.0 - spacing * (n - 1)) / n

    for i in range(n):
        for j in range(n):
            if j > i:
                continue

            axis_idx = i * n + j + 1
            xaxis_name = f"x{axis_idx}" if axis_idx > 1 else "x"
            yaxis_name = f"y{axis_idx}" if axis_idx > 1 else "y"

            if i == j:
                vals = arrays[param_names[i]]
                counts, bin_edges = np.histogram(vals[np.isfinite(vals)], bins=20)
                bin_centers = ((bin_edges[:-1] + bin_edges[1:]) / 2).tolist()
                traces.append({
                    "type": "bar",
                    "x": bin_centers,
                    "y": counts.tolist(),
                    "marker": {"color": colors[i % len(colors)], "opacity": 0.7},
                    "xaxis": xaxis_name,
                    "yaxis": yaxis_name,
                    "showlegend": False,
                })
            else:
                traces.append({
                    "type": "scatter",
                    "mode": "markers",
                    "x": arrays[param_names[j]].tolist(),
                    "y": arrays[param_names[i]].tolist(),
                    "marker": {"size": 2, "color": colors[0], "opacity": 0.4},
                    "xaxis": xaxis_name,
                    "yaxis": yaxis_name,
                    "showlegend": False,
                })

    layout = _base_layout(
        title=params.get("title", "Corner Plot"),
        showlegend=False,
    )

    for i in range(n):
        for j in range(n):
            if j > i:
                continue
            axis_idx = i * n + j + 1
            x_domain_start = j * (cell_size + spacing)
            x_domain_end = x_domain_start + cell_size
            y_domain_start = 1.0 - (i + 1) * (cell_size + spacing) + spacing
            y_domain_end = y_domain_start + cell_size

            xaxis_key = f"xaxis{axis_idx}" if axis_idx > 1 else "xaxis"
            yaxis_key = f"yaxis{axis_idx}" if axis_idx > 1 else "yaxis"

            layout[xaxis_key] = {
                "domain": [x_domain_start, x_domain_end],
                "title": param_names[j] if i == n - 1 else "",
                "showticklabels": i == n - 1,
                "gridcolor": "rgba(255,255,255,0.06)",
                "zerolinecolor": "rgba(255,255,255,0.1)",
            }
            layout[yaxis_key] = {
                "domain": [y_domain_start, y_domain_end],
                "title": param_names[i] if j == 0 else "",
                "showticklabels": j == 0,
                "gridcolor": "rgba(255,255,255,0.06)",
                "zerolinecolor": "rgba(255,255,255,0.1)",
            }

    return {"data": traces, "layout": layout}


def _empty_figure(message: str) -> dict:
    """Return a figure with a centered text annotation."""
    layout = _base_layout(
        title=message,
        xaxis={"visible": False},
        yaxis={"visible": False},
        annotations=[{
            "text": message,
            "xref": "paper",
            "yref": "paper",
            "x": 0.5,
            "y": 0.5,
            "showarrow": False,
            "font": {"size": 16, "color": "rgba(255,255,255,0.6)"},
        }],
    )
    return {"data": [], "layout": layout}


# Dispatch table
_BUILDERS = {
    "hr_diagram": _build_hr_diagram,
    "sed_fit": _build_sed_fit,
    "spectrum_overlay": _build_spectrum_overlay,
    "redshift_histogram": _build_redshift_histogram,
    "sky_coverage": _build_sky_coverage,
    "correlation_scatter": _build_correlation_scatter,
    "corner_plot": _build_corner_plot,
}


def interactive_plot_node(input_data: dict, params: dict) -> dict:
    """Pipeline node: generate an interactive Plotly JSON figure.

    params:
        chart_type: str -- one of CHART_TEMPLATES keys (default "spectrum_overlay")
        ... additional params passed to the template function
    """
    chart_type = params.get("chart_type", "spectrum_overlay")
    data = input_data.get("data", {})

    builder = _BUILDERS.get(chart_type)
    if builder is None:
        raise ValueError(
            f"Unknown chart type: {chart_type}. "
            f"Available: {list(CHART_TEMPLATES.keys())}"
        )

    figure_dict = builder(data, params)

    return {
        **input_data,
        "plot_json": figure_dict,
        "chart_type": chart_type,
    }


def build_chart(chart_type: str, data: dict, params: dict) -> dict:
    """Public API: build a Plotly figure dict from chart type, data, and params.

    Used by the chat action executor to generate plots on demand.
    """
    builder = _BUILDERS.get(chart_type)
    if builder is None:
        return _empty_figure(
            f"Unknown chart type: {chart_type}. "
            f"Available: {', '.join(CHART_TEMPLATES.keys())}"
        )
    return builder(data, params)

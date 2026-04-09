"""Plot node — generates PNG/HTML plots from pipeline data."""

import base64
import io

import numpy as np


def plot_node(input_data: dict, params: dict) -> dict:
    """Generate a plot from the data.

    params:
        plot_type: str — "spectrum", "scatter", or "image" (default "spectrum")
        x_key: str — key for x data (default "index")
        y_key: str — key for y data (default "flux")
        title: str — plot title
        x_label: str — x-axis label
        y_label: str — y-axis label
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plot_type = params.get("plot_type", "spectrum")
    x_key = params.get("x_key", "index")
    y_key = params.get("y_key", "flux")
    title = params.get("title", "Pipeline Plot")
    x_label = params.get("x_label", x_key)
    y_label = params.get("y_label", y_key)

    data = input_data.get("data", {})
    fig, ax = plt.subplots(figsize=(10, 5))

    if plot_type in ("spectrum", "scatter"):
        x = np.array(data.get(x_key, []), dtype=float)
        y = np.array(data.get(y_key, []), dtype=float)

        if len(x) == 0 or len(y) == 0:
            ax.text(0.5, 0.5, "No data to plot", ha="center", va="center", transform=ax.transAxes)
        elif plot_type == "spectrum":
            ax.plot(x, y, linewidth=0.8, color="#38bdf8")
            # Overlay fit if present
            fit = input_data.get("fit_result", {})
            if fit.get("success") and "fitted_y" in fit:
                ax.plot(x, fit["fitted_y"], linewidth=1.2, color="#f97316",
                        linestyle="--", label=f'{fit["model"]} fit')
                ax.legend()
        else:
            ax.scatter(x, y, s=4, alpha=0.6, color="#38bdf8")
    elif plot_type == "image":
        # Expect 2D array
        shape = data.get("shape")
        if shape and "mean" in data:
            ax.text(0.5, 0.5, f"Image {shape[0]}x{shape[1]}\nmean={data['mean']:.2f}",
                    ha="center", va="center", transform=ax.transAxes, fontsize=14)
        else:
            ax.text(0.5, 0.5, "Image preview not available", ha="center", va="center",
                    transform=ax.transAxes)

    ax.set_title(title)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    buf.seek(0)
    png_b64 = base64.b64encode(buf.read()).decode()

    return {
        **input_data,
        "plot": {
            "format": "png",
            "data_uri": f"data:image/png;base64,{png_b64}",
            "plot_type": plot_type,
        },
    }

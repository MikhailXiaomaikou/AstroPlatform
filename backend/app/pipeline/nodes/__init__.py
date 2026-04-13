"""Built-in pipeline node registry — lazy imports to avoid startup failures."""

from typing import Callable


def _get_registry() -> dict[str, Callable]:
    from app.pipeline.nodes.load_data import load_data
    from app.pipeline.nodes.query_data import query_data
    from app.pipeline.nodes.import_workspace import import_workspace
    from app.pipeline.nodes.denoise import denoise
    from app.pipeline.nodes.spectral_fit import spectral_fit
    from app.pipeline.nodes.coord_transform import coord_transform
    from app.pipeline.nodes.plot import plot_node
    from app.pipeline.nodes.redshift import redshift_estimate
    from app.pipeline.nodes.equivalent_width import equivalent_width
    from app.pipeline.nodes.sed_fit import sed_fit
    from app.pipeline.nodes.crossmatch import crossmatch
    from app.pipeline.nodes.phot_calibrate import phot_calibrate
    from app.pipeline.nodes.image_stack import image_stack
    from app.pipeline.nodes.plot_interactive import interactive_plot_node
    from app.pipeline.nodes.custom_script import custom_script
    from app.pipeline.nodes.timeseries import timeseries_analysis
    from app.pipeline.nodes.bias_subtract import bias_subtract_node
    from app.pipeline.nodes.dark_correct import dark_correct
    from app.pipeline.nodes.flat_field import flat_field
    from app.pipeline.nodes.cosmic_ray_reject import cosmic_ray_reject_node
    from app.pipeline.nodes.astrometric_solve import astrometric_solve
    from app.pipeline.nodes.source_extract import source_extract
    from app.pipeline.nodes.psf_photometry import psf_photometry
    from app.pipeline.nodes.condition import condition

    return {
        "QueryData": query_data,
        "ImportWorkspace": import_workspace,
        "LoadData": load_data,
        "BiasSubtract": bias_subtract_node,
        "DarkCorrect": dark_correct,
        "FlatField": flat_field,
        "CosmicRayReject": cosmic_ray_reject_node,
        "AstrometricSolve": astrometric_solve,
        "SourceExtract": source_extract,
        "PSFPhotometry": psf_photometry,
        "Denoise": denoise,
        "SpectralFit": spectral_fit,
        "CoordTransform": coord_transform,
        "Plot": plot_node,
        "RedshiftEstimate": redshift_estimate,
        "EquivalentWidth": equivalent_width,
        "SEDFit": sed_fit,
        "CrossMatch": crossmatch,
        "PhotCalibrate": phot_calibrate,
        "ImageStack": image_stack,
        "InteractivePlot": interactive_plot_node,
        "CustomScript": custom_script,
        "TimeSeriesAnalysis": timeseries_analysis,
        "Condition": condition,
    }


class _LazyRegistry:
    """Dict-like that defers imports until first access."""

    def __init__(self):
        self._inner: dict[str, Callable] | None = None

    def _load(self):
        if self._inner is None:
            self._inner = _get_registry()

    def get(self, key, default=None):
        self._load()
        return self._inner.get(key, default)

    def __contains__(self, key):
        self._load()
        return key in self._inner

    def keys(self):
        self._load()
        return self._inner.keys()

    def __getitem__(self, key):
        self._load()
        return self._inner[key]


registry = _LazyRegistry()

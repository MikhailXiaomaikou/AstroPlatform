"""Sandboxed Python code executor for the AI research agent.

Executes user/AI-generated Python code in a restricted environment with
astronomy libraries available. Captures stdout, stderr, and matplotlib
figures as base64 PNG images.
"""

import base64
import io
import inspect
import logging
import signal
import sys
import traceback
from contextlib import contextmanager
from types import ModuleType

logger = logging.getLogger(__name__)

# Maximum execution time in seconds
MAX_EXEC_TIME = 75

# Maximum output size in characters (raised from 50k to 500k for large tables)
MAX_OUTPUT_SIZE = 500_000

# Memory warning threshold in bytes (default 512 MB)
MEMORY_WARN_THRESHOLD = 512 * 1024 * 1024

# Maximum array/table rows allowed in a single variable repr
MAX_REPR_ROWS = 2000

# Session-scoped variable store — persists between code executions
_session_vars: dict[str, dict] = {}
_astro_module: ModuleType | None = None


def get_session_vars(session_id: str = "default") -> dict:
    """Get or create a session variable store."""
    if session_id not in _session_vars:
        _session_vars[session_id] = {}
    return _session_vars[session_id]


def clear_session_vars(session_id: str = "default") -> None:
    _session_vars.pop(session_id, None)


def _get_astro_module() -> ModuleType:
    """Return the Standard Astro helper module exposed as `astro`."""
    global _astro_module
    if _astro_module is None:
        from app.services import astro_analysis as astro_analysis

        module = ModuleType("astro")
        module.__doc__ = astro_analysis.__doc__
        for name in dir(astro_analysis):
            if name.startswith("_"):
                continue
            setattr(module, name, getattr(astro_analysis, name))
        _astro_module = module
    return _astro_module


# Allowed imports — astronomy + data science stack
ALLOWED_MODULES = {
    # Core
    "math", "statistics", "collections", "itertools", "functools",
    "json", "csv", "re", "datetime", "io",
    "inspect",
    # Data science
    "numpy", "np",
    "scipy", "scipy.optimize", "scipy.signal", "scipy.stats",
    "scipy.interpolate", "scipy.integrate",
    # Astronomy
    "astropy", "astropy.io", "astropy.io.fits", "astropy.table",
    "astropy.coordinates", "astropy.units", "astropy.cosmology",
    "astropy.wcs", "astropy.stats", "astropy.modeling",
    "astropy.convolution",
    "astro",
    # Visualization
    "matplotlib", "matplotlib.pyplot", "plt",
    # Tables
    "pandas",
}

# Blocked operations
BLOCKED_BUILTINS = {
    "exec", "eval", "compile", "__import__",
    "open",  # file I/O blocked — use provided data access functions
    "exit", "quit",
}


class CodeExecutionResult:
    def __init__(self):
        self.stdout: str = ""
        self.stderr: str = ""
        self.error: str | None = None
        self.figures: list[str] = []  # base64 PNG strings
        self.variables: dict[str, str] = {}  # name -> repr (for key results)
        self.variable_types: dict[str, str] = {}  # name -> type name
        self.success: bool = True


@contextmanager
def _timeout(seconds: int):
    """Context manager that raises TimeoutError after `seconds`.

    Uses SIGALRM on main thread, falls back to no-op on worker threads/Windows.
    The actual timeout enforcement is handled by the caller via run_in_executor.
    """
    import threading
    if sys.platform == "win32" or threading.current_thread() is not threading.main_thread():
        # Can't use SIGALRM in worker threads — skip (timeout handled externally)
        yield
        return

    def _handler(_signum, _frame):
        raise TimeoutError(f"Code execution timed out after {seconds} seconds")

    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _safe_import(name, *args, **kwargs):
    """Restricted import that only allows whitelisted modules."""
    if name == "astro":
        return _get_astro_module()
    top_level = name.split(".")[0]
    if top_level not in ALLOWED_MODULES and name not in ALLOWED_MODULES:
        hint = ""
        if name == "astro":
            hint = " Hint: 'astro' is the Standard Astro helper toolkit. It is pre-loaded and also importable as 'astro'."
        raise ImportError(
            f"Import of '{name}' is not allowed. "
            f"Available: numpy, scipy, astropy, matplotlib, pandas.{hint}"
        )
    return __builtins__.__import__(name, *args, **kwargs) if hasattr(__builtins__, '__import__') else __import__(name, *args, **kwargs)


def _get_memory_usage_bytes() -> int:
    """Return approximate RSS memory usage in bytes (best-effort)."""
    try:
        import resource
        # ru_maxrss is in bytes on Linux, kilobytes on macOS
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return usage  # already bytes on macOS
        return usage * 1024  # KB -> bytes on Linux
    except Exception:
        return 0


def _check_memory(warn_stream: io.StringIO | None = None) -> int:
    """Check memory usage; write a warning if approaching the threshold.

    Returns current usage in bytes.
    """
    usage = _get_memory_usage_bytes()
    if usage > MEMORY_WARN_THRESHOLD and warn_stream is not None:
        mb = usage / (1024 * 1024)
        limit_mb = MEMORY_WARN_THRESHOLD / (1024 * 1024)
        warn_stream.write(
            f"\n⚠ Memory warning: ~{mb:.0f} MB in use (threshold {limit_mb:.0f} MB). "
            "Consider using process_in_chunks() or reducing data size.\n"
        )
    return usage


def _make_sandbox_helpers():
    """Create large-data processing helpers available inside the sandbox."""

    def process_in_chunks(data, chunk_size, func):
        """Process a list/array in chunks to limit peak memory usage.

        Args:
            data: list, numpy array, pandas DataFrame, or astropy Table
            chunk_size: number of rows/elements per chunk
            func: callable that receives a chunk and returns a result

        Returns:
            list of results, one per chunk

        Example::

            results = process_in_chunks(big_table, 1000, lambda chunk: chunk['flux'].mean())
        """
        results = []
        length = len(data)
        for start in range(0, length, chunk_size):
            end = min(start + chunk_size, length)
            chunk = data[start:end]
            results.append(func(chunk))
        return results

    def load_votable(path: str):
        """Load a VOTable file efficiently, returning an astropy Table.

        Large VOTables are read with a streaming parser that converts
        to a Table only after filtering if needed.
        """
        from astropy.io.votable import parse as _parse_votable
        votable = _parse_votable(path)
        table = votable.get_first_table().to_table()
        return table

    def load_csv(path: str, **kwargs):
        """Load a CSV file efficiently using pandas or astropy.

        Accepts the same keyword arguments as pandas.read_csv (e.g.
        ``usecols``, ``nrows``, ``dtype``).  Falls back to the csv
        stdlib module if pandas is unavailable.

        Returns:
            pandas DataFrame (preferred) or list[dict]
        """
        try:
            import pandas as _pd
            return _pd.read_csv(path, **kwargs)
        except ImportError:
            pass
        # Fallback: stdlib csv
        import csv as _csv
        nrows = kwargs.get("nrows")
        rows = []
        with open(path, newline="") as fh:
            reader = _csv.DictReader(fh)
            for i, row in enumerate(reader):
                if nrows is not None and i >= nrows:
                    break
                rows.append(row)
        return rows

    def memory_usage_mb() -> float:
        """Return approximate process memory usage in megabytes."""
        return _get_memory_usage_bytes() / (1024 * 1024)

    return {
        "process_in_chunks": process_in_chunks,
        "load_votable": load_votable,
        "load_csv": load_csv,
        "memory_usage_mb": memory_usage_mb,
    }


def _make_data_accessor(session_id: str):
    """Create helper functions that the code can call to access platform data."""
    def load_fits(fits_path: str):
        """Load a FITS file from the platform storage. Returns an astropy HDUList."""
        from app.storage import download_fits
        from astropy.io import fits
        raw = download_fits(fits_path)
        return fits.open(io.BytesIO(raw))

    def get_search_results():
        """Get the most recent search results as a list of dicts."""
        from app.services.ai_tools import get_cached_results
        return get_cached_results(f"latest:{session_id}") or get_cached_results("latest") or []

    def get_adql_results():
        """Get the latest ADQL query results as a list of dicts."""
        from app.services.ai_tools import get_cached_results
        return get_cached_results(f"latest_adql:{session_id}") or get_cached_results("latest_adql") or []

    return {
        "load_fits": load_fits,
        "get_search_results": get_search_results,
        "get_adql_results": get_adql_results,
    }


def _should_persist_value(val) -> bool:
    """Keep most Python objects alive across cells, but skip modules and figures."""
    if isinstance(val, ModuleType):
        return False
    try:
        import matplotlib.figure
        if isinstance(val, matplotlib.figure.Figure):
            return False
    except Exception:
        pass
    if inspect.ismodule(val):
        return False
    return True


def execute_python(code: str, context: dict | None = None, session_id: str = "default") -> CodeExecutionResult:
    """Execute Python code in a sandboxed environment.

    Args:
        code: Python source code to execute
        context: Optional dict of variables to inject (e.g., data from previous tool calls)

    Returns:
        CodeExecutionResult with stdout, stderr, figures, and key variables
    """
    result = CodeExecutionResult()

    # Capture stdout/stderr
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()

    # Build safe globals
    import builtins
    safe_builtins = {k: v for k, v in vars(builtins).items() if k not in BLOCKED_BUILTINS}
    safe_builtins["__import__"] = _safe_import

    # Pre-import common libraries for convenience
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")  # Non-interactive backend
    import matplotlib.pyplot as plt

    exec_globals = {
        "__builtins__": safe_builtins,
        "np": np,
        "numpy": np,
        "plt": plt,
        "matplotlib": matplotlib,
        **_make_data_accessor(session_id),
        **_make_sandbox_helpers(),
    }

    # Try to pre-import astropy
    try:
        import astropy
        import astropy.units as u
        from astropy.table import Table
        from astropy.coordinates import SkyCoord
        exec_globals["astropy"] = astropy
        exec_globals["u"] = u
        exec_globals["Table"] = Table
        exec_globals["SkyCoord"] = SkyCoord
    except ImportError:
        pass

    # Try to pre-import scipy
    try:
        import scipy
        exec_globals["scipy"] = scipy
    except ImportError:
        pass

    # Try to pre-import pandas
    try:
        import pandas as pd
        exec_globals["pd"] = pd
        exec_globals["pandas"] = pd
    except ImportError:
        pass

    # Pre-import astropy.cosmology
    try:
        from astropy.cosmology import FlatLambdaCDM, Planck18
        exec_globals["FlatLambdaCDM"] = FlatLambdaCDM
        exec_globals["Planck18"] = Planck18
    except ImportError:
        pass

    # Pre-import astronomy analysis toolkit
    try:
        astro = _get_astro_module()
        exec_globals["astro"] = astro
        # Also expose top-level convenience functions
        exec_globals["pub_figure"] = astro.pub_figure
        exec_globals["pub_style"] = astro.pub_style
        exec_globals["get_isochrone"] = astro.get_isochrone
        exec_globals["fit_isochrone"] = astro.fit_isochrone
        exec_globals["compare_models"] = astro.compare_models
        exec_globals["analyze_residuals"] = astro.analyze_residuals
        exec_globals["plot_hr_diagram"] = astro.plot_hr_diagram
        exec_globals["plot_bpt"] = astro.plot_bpt
        exec_globals["plot_sed"] = astro.plot_sed
        exec_globals["plot_lightcurve"] = astro.plot_lightcurve
        exec_globals["plot_sky_distribution"] = astro.plot_sky_distribution
        exec_globals["bpt_classify"] = astro.bpt_classify
        exec_globals["compute_absolute_magnitude"] = astro.compute_absolute_magnitude
        exec_globals["compute_luminosity_distance"] = astro.compute_luminosity_distance
        exec_globals["k_correction"] = astro.k_correction
        exec_globals["spectral_stacking"] = astro.spectral_stacking
        exec_globals["multi_gaussian_fit"] = astro.multi_gaussian_fit
        exec_globals["continuum_normalize"] = astro.continuum_normalize
        exec_globals["batch_equivalent_width"] = astro.batch_equivalent_width
        exec_globals["cosmological_calculator"] = astro.cosmological_calculator
        exec_globals["redshift_at_age"] = astro.redshift_at_age
        exec_globals["extinction_curve"] = astro.extinction_curve
        exec_globals["deredden"] = astro.deredden
        exec_globals["estimate_ebv"] = astro.estimate_ebv
        exec_globals["monte_carlo_propagate"] = astro.monte_carlo_propagate
        exec_globals["bootstrap_statistic"] = astro.bootstrap_statistic
        exec_globals["error_weighted_mean"] = astro.error_weighted_mean
        exec_globals["lomb_scargle_period"] = astro.lomb_scargle_period
        exec_globals["phase_fold"] = astro.phase_fold
        exec_globals["plot_periodogram"] = astro.plot_periodogram
        exec_globals["plot_phase_folded"] = astro.plot_phase_folded
        exec_globals["variability_indices"] = astro.variability_indices
        exec_globals["classify_variable"] = astro.classify_variable
        exec_globals["voigt_fit"] = astro.voigt_fit
        exec_globals["velocity_dispersion"] = astro.velocity_dispersion
        exec_globals["radial_velocity"] = astro.radial_velocity
        exec_globals["spectral_template_match"] = astro.spectral_template_match
        exec_globals["available_functions"] = astro.available_functions
        exec_globals["target_visibility"] = astro.target_visibility
        exec_globals["airmass_plot"] = astro.airmass_plot
        exec_globals["exposure_time_estimate"] = astro.exposure_time_estimate
    except ImportError:
        pass

    # Photo-z functions are lazily imported in astro_analysis.available_functions()
    # and thus not available as module-level attributes on the astro module.
    # Import them directly from photo_z.
    try:
        from app.services.photo_z import (
            estimate_photo_z_template,
            estimate_photo_z_ml,
            estimate_photo_z,
        )
        exec_globals["estimate_photo_z_template"] = estimate_photo_z_template
        exec_globals["estimate_photo_z_ml"] = estimate_photo_z_ml
        exec_globals["estimate_photo_z"] = estimate_photo_z
    except ImportError:
        pass

    # Inject session variables (persist between code blocks)
    session_vars = get_session_vars(session_id)
    exec_globals.update(session_vars)

    # Inject context variables
    if context:
        exec_globals.update(context)

    # Record pre-existing keys to skip in variable extraction
    pre_existing_keys = set(exec_globals.keys())

    # Close any existing matplotlib figures
    plt.close("all")

    old_stdout = sys.stdout
    old_stderr = sys.stderr

    try:
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture

        with _timeout(MAX_EXEC_TIME):
            exec(code, exec_globals)  # noqa: S102

        result.success = True

    except TimeoutError as e:
        result.error = str(e)
        result.success = False
    except Exception as e:
        tb = traceback.format_exc()
        result.error = f"{type(e).__name__}: {e}"
        result.stderr = tb
        result.success = False
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    # Check memory usage and append warning if needed
    _check_memory(stderr_capture)

    # Capture output
    result.stdout = stdout_capture.getvalue()[:MAX_OUTPUT_SIZE]
    stderr_text = stderr_capture.getvalue()
    if stderr_text and not result.stderr:
        result.stderr = stderr_text[:MAX_OUTPUT_SIZE]

    # Capture matplotlib figures as base64 PNG
    try:
        fig_nums = plt.get_fignums()
        for fig_num in fig_nums:
            fig = plt.figure(fig_num)
            buf = io.BytesIO()
            # Use dark theme for figures (matches default dark UI)
            fig.patch.set_facecolor("#1c1c1e")
            for ax in fig.get_axes():
                ax.set_facecolor("#2c2c2e")
                ax.tick_params(colors="#ccc")
                ax.xaxis.label.set_color("#ccc")
                ax.yaxis.label.set_color("#ccc")
                ax.title.set_color("#eee")
                for spine in ax.spines.values():
                    spine.set_edgecolor("#555")
            fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                       facecolor="#1c1c1e", edgecolor="none")
            buf.seek(0)
            b64 = base64.b64encode(buf.read()).decode("utf-8")
            result.figures.append(b64)
        plt.close("all")
    except Exception as e:
        logger.warning("Failed to capture matplotlib figures: %s", e)

    # Save user-defined variables back to session for persistence
    for name, val in exec_globals.items():
        if name.startswith("_") or name in pre_existing_keys:
            continue
        try:
            if _should_persist_value(val):
                session_vars[name] = val
        except Exception:
            pass

    # Extract key result variables (allow larger representations)
    for name, val in exec_globals.items():
        if name.startswith("_") or name in pre_existing_keys:
            continue
        try:
            r = repr(val)
            if len(r) < 5000:  # Raised from 500 to 5000 for larger tables
                result.variables[name] = r
                result.variable_types[name] = type(val).__name__
        except Exception:
            pass

    return result

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
MAX_EXEC_TIME = 30

# Maximum output size in characters
MAX_OUTPUT_SIZE = 50_000

# Session-scoped variable store — persists between code executions
_session_vars: dict[str, dict] = {}


def get_session_vars(session_id: str = "default") -> dict:
    """Get or create a session variable store."""
    if session_id not in _session_vars:
        _session_vars[session_id] = {}
    return _session_vars[session_id]


def clear_session_vars(session_id: str = "default") -> None:
    _session_vars.pop(session_id, None)

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
    top_level = name.split(".")[0]
    if top_level not in ALLOWED_MODULES and name not in ALLOWED_MODULES:
        raise ImportError(
            f"Import of '{name}' is not allowed. "
            f"Available: numpy, scipy, astropy, matplotlib, pandas"
        )
    return __builtins__.__import__(name, *args, **kwargs) if hasattr(__builtins__, '__import__') else __import__(name, *args, **kwargs)


def _make_data_accessor():
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
        return get_cached_results("latest") or []

    return {
        "load_fits": load_fits,
        "get_search_results": get_search_results,
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
        **_make_data_accessor(),
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
        from app.services import astro_analysis as astro
        exec_globals["astro"] = astro
        # Also expose top-level convenience functions
        exec_globals["pub_figure"] = astro.pub_figure
        exec_globals["pub_style"] = astro.pub_style
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
        exec_globals["available_functions"] = astro.available_functions
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

    # Extract key result variables (skip large objects)
    for name, val in exec_globals.items():
        if name.startswith("_") or name in pre_existing_keys:
            continue
        try:
            r = repr(val)
            if len(r) < 500:  # Only capture small representations
                result.variables[name] = r
        except Exception:
            pass

    return result

"""Custom Python script node — run user-defined code in the pipeline."""

import logging

logger = logging.getLogger(__name__)


def custom_script(input_data: dict, params: dict) -> dict:
    """Execute a user-defined Python script within the pipeline.

    params:
        code: str — Python code to execute
        output_key: str — key name for the script's output variable (default "result")

    The code has access to:
        - input_data: dict from upstream nodes
        - numpy as np
        - scipy
        - astropy (Table, SkyCoord, units as u)
        - All functions from astro_analysis module

    The code should assign its output to a variable named by output_key.
    """
    code = params.get("code", "")
    output_key = params.get("output_key", "result")

    if not code.strip():
        raise ValueError("CustomScript: 'code' parameter is required")

    import numpy as np

    # H15: subject user code to the same BLOCKED_BUILTINS filter used by
    # run_python.  Previously this node passed the full builtins module,
    # exposing open/exec/__import__ to the user script — a sandbox escape
    # when the inproc backend runs inside the FastAPI worker.  Pair the
    # filtered builtins with _safe_import so legitimate imports (numpy,
    # scipy, astropy) still work.
    try:
        from app.services.code_executor import (
            BLOCKED_BUILTINS as _BLOCKED,
            _safe_import,
        )
    except Exception:  # pragma: no cover
        _BLOCKED = {"exec", "eval", "compile", "__import__", "open", "exit", "quit"}
        _safe_import = None
    import builtins as _builtins
    _safe_builtins = {k: v for k, v in vars(_builtins).items() if k not in _BLOCKED}
    if _safe_import is not None:
        _safe_builtins["__import__"] = _safe_import

    # Build execution environment
    exec_globals: dict = {
        "__builtins__": _safe_builtins,
        "input_data": input_data,
        "np": np,
        "numpy": np,
    }

    # Add scipy
    try:
        import scipy
        exec_globals["scipy"] = scipy
    except ImportError:
        pass

    # Add astropy
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

    # Add analysis toolkit
    try:
        from app.services import astro_analysis as astro
        exec_globals["astro"] = astro
    except ImportError:
        pass

    try:
        exec(code, exec_globals)  # noqa: S102
    except Exception as e:
        raise ValueError(f"CustomScript error: {type(e).__name__}: {e}")

    # Extract the output
    if output_key in exec_globals:
        output = exec_globals[output_key]
        # Convert common types to serializable format
        if isinstance(output, np.ndarray):
            output = output.tolist()
        if not isinstance(output, (dict, list, str, int, float, type(None))):
            output = str(output)
    else:
        output = None

    return {
        **input_data,
        "custom_output": output,
        "custom_script_status": "success",
    }

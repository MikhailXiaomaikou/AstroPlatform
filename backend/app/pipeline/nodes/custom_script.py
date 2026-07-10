"""Custom Python script node — run user-defined code in the pipeline."""

import logging
import uuid

logger = logging.getLogger(__name__)


def custom_script(input_data: dict, params: dict) -> dict:
    """Execute a user-defined Python script within the pipeline.

    params:
        code: str — Python code to execute
        output_key: str — key name for the script's output variable (default "result")

    The code has access to:
        - input_data: dict from upstream nodes
        - numpy, scipy, astropy, matplotlib and other pre-loaded libraries (see code_executor.ALLOWED_MODULES)
        - astro: the astro_analysis toolkit

    The code should assign its output to a variable named by output_key.

    Security note: arbitrary Python is disabled by default and always disabled
    in hosted production.  On a trusted single-user development machine an
    operator may explicitly enable the legacy executor for crash/resource
    containment, but filtered builtins are not an OS security boundary.
    """
    from app.services.code_executor import execute_python, get_session_vars

    code = params.get("code", "")
    output_key = params.get("output_key", "result")

    if not code.strip():
        raise ValueError("CustomScript: 'code' parameter is required")

    # Use a unique session_id each time to prevent state leakage between pipeline
    # runs; execute_python's _sweep_idle_sessions cleans them up automatically via LRU.
    session_id = f"custom_script_{uuid.uuid4().hex[:8]}"
    result = execute_python(
        code,
        context={"input_data": input_data},
        session_id=session_id,
        timeout_seconds=75,
    )
    if not result.success:
        raise ValueError(f"CustomScript error: {result.error or 'execution failed'}")

    # Retrieve the actual output object from session vars (result.variables holds repr strings, not usable)
    sess = get_session_vars(session_id)
    output = sess.get(output_key)

    # Serialisation compatibility (consistent with original implementation)
    try:
        import numpy as _np
        if isinstance(output, _np.ndarray):
            output = output.tolist()
    except ImportError:
        pass
    if not isinstance(output, (dict, list, str, int, float, type(None))):
        output = str(output)

    return {
        **input_data,
        "custom_output": output,
        "custom_script_status": "success",
    }

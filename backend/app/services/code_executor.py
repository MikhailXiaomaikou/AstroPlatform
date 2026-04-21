"""Sandboxed Python code executor for the AI research agent.

Executes user/AI-generated Python code in a restricted environment with
astronomy libraries available. Captures stdout, stderr, and matplotlib
figures as base64 PNG images.
"""

import base64
import hashlib
import io
import inspect
import logging
import pickle
import re
import signal
import sys
import time as _session_time
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

# Session-scoped variable store — persists between code executions.
# Without eviction this grew unbounded: every unique python_session_id seen
# over the life of the Render process kept its locals and imported-module
# references alive.  Long-running instances leaked hundreds of MB.
# _session_last_access tracks monotonic-clock last touch; _sweep_idle_sessions
# evicts entries older than MAX_SESSION_IDLE_SECONDS when the registry grows
# past MAX_TRACKED_SESSIONS.

_session_vars: dict[str, dict] = {}
_session_replay_signatures: dict[str, str] = {}
_session_last_access: dict[str, float] = {}
_astro_module: ModuleType | None = None

# S1 (PART S): subprocess sandbox 每次 fresh fork, 没有 _session_vars 之类
# in-process 持久化.  记录成功跑过的 code blocks, 下次新 subprocess fork 前
# 把历史代码作为 prefix 拼在当前 code 前, 一次 exec 里重建前序变量.
# (Jupyter-like 状态模拟).  对 in-process 路径无影响 — _session_vars 仍然
# 直接走更快的 globals 注入.
_session_code_history: dict[str, list[str]] = {}

# E0.2: a NGC 752 reviewer saw 7/8 run_python calls fail partly because
# intermediate DataFrames evicted mid-chat.  Raise the cap so a long
# research session (multiple chat turns × concurrent tool runs) can keep
# its state; shrink idle TTL to 2 h so production instances don't
# accumulate stale sessions over multi-day uptime.
MAX_SESSION_IDLE_SECONDS: float = 2 * 60 * 60  # 2 hours
MAX_TRACKED_SESSIONS: int = 512


def _touch_session(session_id: str) -> None:
    """Update the last-access timestamp for eviction bookkeeping."""
    _session_last_access[session_id] = _session_time.monotonic()


def _sweep_idle_sessions() -> int:
    """Drop stale sessions.  Runs lazily on writes to keep the registry bounded.

    Returns the number of evicted sessions (0 when nothing to do).
    """
    now = _session_time.monotonic()
    evicted = 0

    # Pass 1: evict anything idle beyond the TTL.
    stale = [
        sid for sid, last in _session_last_access.items()
        if (now - last) > MAX_SESSION_IDLE_SECONDS
    ]
    for sid in stale:
        _session_vars.pop(sid, None)
        _session_replay_signatures.pop(sid, None)
        _session_last_access.pop(sid, None)
        _session_code_history.pop(sid, None)
        evicted += 1

    # Pass 2: if we're still over the cap, evict oldest-first until we're under.
    if len(_session_vars) > MAX_TRACKED_SESSIONS:
        over = len(_session_vars) - MAX_TRACKED_SESSIONS
        by_age = sorted(_session_last_access.items(), key=lambda kv: kv[1])
        for sid, _ts in by_age[:over]:
            _session_vars.pop(sid, None)
            _session_replay_signatures.pop(sid, None)
            _session_last_access.pop(sid, None)
            _session_code_history.pop(sid, None)
            evicted += 1

    if evicted:
        logger.info(
            "code_executor swept %d idle session(s); %d remain",
            evicted, len(_session_vars),
        )
    return evicted


def get_session_vars(session_id: str = "default") -> dict:
    """Get or create a session variable store."""
    if session_id not in _session_vars:
        _session_vars[session_id] = {}
        # Sweep on new-session creation so the registry can't grow forever
        # even if every request uses a fresh session id.
        if len(_session_vars) > MAX_TRACKED_SESSIONS:
            _sweep_idle_sessions()
    _touch_session(session_id)
    return _session_vars[session_id]


def clear_session_vars(session_id: str = "default") -> None:
    _session_vars.pop(session_id, None)
    _session_replay_signatures.pop(session_id, None)
    _session_last_access.pop(session_id, None)
    _session_code_history.pop(session_id, None)


def has_session_state(session_id: str = "default") -> bool:
    return bool(_session_vars.get(session_id))


# ── S1 (PART S): session code history for subprocess replay ─────────────
MAX_SESSION_CODE_BLOCKS: int = 40  # 防止极长会话无限膨胀
MAX_SESSION_CODE_PREFIX_BYTES: int = 200_000  # 200 KB 上限 — subprocess pipe 也有自己的限


def append_session_code_block(session_id: str, code: str) -> None:
    """在成功执行后记录一个 code block.  Subprocess 下次 fork 会 replay."""
    if not session_id or session_id == "default":
        return
    if not isinstance(code, str) or not code.strip():
        return
    history = _session_code_history.setdefault(session_id, [])
    history.append(code)
    # 溢出时丢掉最老的, 保证最近 N 个仍可 replay
    if len(history) > MAX_SESSION_CODE_BLOCKS:
        del history[: len(history) - MAX_SESSION_CODE_BLOCKS]
    _touch_session(session_id)


def get_session_code_history(session_id: str) -> list[str]:
    return list(_session_code_history.get(session_id, []))


def get_session_code_prefix(session_id: str) -> str:
    """拼接历史 code blocks 作为 subprocess 预 exec 的前缀.

    返回空字符串表示没有历史可 replay, 调用方直接 exec 当前 code.
    """
    blocks = _session_code_history.get(session_id, [])
    if not blocks:
        return ""
    joined = "\n\n# === prior cell ===\n\n".join(blocks)
    # 再加一个 separator 和当前 cell 分开
    prefix = joined + "\n\n# === current cell ===\n\n"
    # 若前缀过长 (session 代码累积太多), 从老到新丢弃直到合规
    while len(prefix.encode("utf-8")) > MAX_SESSION_CODE_PREFIX_BYTES and len(blocks) > 1:
        blocks = blocks[1:]
        joined = "\n\n# === prior cell ===\n\n".join(blocks)
        prefix = joined + "\n\n# === current cell ===\n\n"
    return prefix


def get_session_helper_calls(session_id: str) -> set[str]:
    """返回 session 历史 code 里调过的所有 Name/Attribute 标识符.

    G1 contract validator 用它来允许 "前 cell 已经 call 过 get_adql_results,
    本 cell 直接用变量" 这种合法模式, 解决 R9-NEW-1 detector 过严.
    """
    import ast as _ast
    called: set[str] = set()
    for block in _session_code_history.get(session_id, []):
        try:
            tree = _ast.parse(block)
        except SyntaxError:
            continue
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Name):
                called.add(node.id)
            elif isinstance(node, _ast.Attribute):
                called.add(node.attr)
            elif isinstance(node, _ast.ImportFrom):
                for alias in node.names:
                    called.add(alias.asname or alias.name)
            elif isinstance(node, _ast.Import):
                for alias in node.names:
                    called.add(alias.asname or alias.name)
    return called


def replay_session_history(session_id: str, code_blocks: list[str]) -> None:
    """Rebuild a Python session by replaying prior successful code blocks."""
    normalized_blocks = [block for block in code_blocks if isinstance(block, str) and block.strip()]
    if not normalized_blocks:
        return

    signature = hashlib.sha256("\n\n# === cell ===\n\n".join(normalized_blocks).encode("utf-8")).hexdigest()
    if has_session_state(session_id) and _session_replay_signatures.get(session_id) == signature:
        return

    clear_session_vars(session_id)
    for block in normalized_blocks:
        result = execute_python(block, session_id=session_id)
        if not result.success:
            logger.warning("Skipping failed replay block for session %s: %s", session_id, result.error)
            clear_session_vars(session_id)
            return
    _session_replay_signatures[session_id] = signature


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
    "warnings",  # Safe stdlib — astronomical packages emit many warnings
    "time",  # stdlib timing utilities (sleep, perf_counter, etc.)
    # Scientific visualization
    "seaborn",
    # Data science
    "numpy", "np",
    "scipy", "scipy.optimize", "scipy.signal", "scipy.stats",
    "scipy.interpolate", "scipy.integrate",
    # Astronomy
    "astropy", "astropy.io", "astropy.io.fits", "astropy.table",
    "astropy.coordinates", "astropy.units", "astropy.cosmology",
    "astropy.wcs", "astropy.stats", "astropy.modeling",
    "astropy.convolution",
    "specutils", "specutils.fitting", "specutils.analysis", "specutils.manipulation",
    "dynesty", "dynesty.utils",
    "ultranest",
    "arviz",
    "celerite2", "batman",
    "astro",
    "reproject", "photutils", "photutils.segmentation", "photutils.detection",
    "photutils.aperture", "photutils.psf",
    "pyvo", "pyvo.dal", "pyvo.sia2", "pyvo.ssa",
    # ML & statistics
    "sklearn", "sklearn.cluster", "sklearn.mixture", "sklearn.preprocessing",
    "sklearn.decomposition", "sklearn.neighbors", "sklearn.metrics",
    "sklearn.model_selection", "sklearn.ensemble",
    "emcee", "corner",
    # Visualization
    "matplotlib", "matplotlib.pyplot", "plt",
    # Tables
    "pandas",
    # Distributed / large-data
    "dask", "dask.dataframe", "dask.array", "dask.bag",
    # X-ray spectral fitting (Doe+ 2007 ASP 376, 543)
    "sherpa", "sherpa.astro", "sherpa.astro.ui",
    # RV orbit fitting (Fulton+ 2018 PASP 130, 044504)
    "radvel",
    # Sparse RV sampling for binaries (Price-Whelan+ 2017 ApJ 837, 20)
    "thejoker",
    # Galactic dynamics (Bovy 2015 ApJS 216, 29)
    "galpy", "galpy.potential", "galpy.orbit", "galpy.util", "galpy.df",
    "galpy.actionAngle",
    # Stellar parameters from spectra (Piskunov & Valenti 2017 A&A 597, A16)
    "pysme",
    # Integrated spectroscopic analysis (Blanco-Cuaresma+ 2014 A&A 569, A111)
    "ispec",
    # Galaxy morphology (Rodriguez-Gomez+ 2019 MNRAS 483, 4140)
    "statmorph",
    # Voronoi 2D binning (Cappellari & Copin 2003 MNRAS 342, 345)
    "vorbin",
    # Kinematic fitting of stellar spectra (Cappellari 2017 MNRAS 466, 798)
    "ppxf",
    # IVOA/astropy database queries (Ginsburg+ 2019 AJ 157, 98)
    "astroquery", "astroquery.jplhorizons", "astroquery.mpc",
    "astroquery.vizier", "astroquery.simbad", "astroquery.ned",
    "astroquery.gaia", "astroquery.mast", "astroquery.irsa",
    # 3D dust maps (Green 2018 JOSS 3, 695)
    "dustmaps", "dustmaps.sfd", "dustmaps.bayestar", "dustmaps.planck",
    # HEALPix spherical data (Górski+ 2005 ApJ 622, 759)
    "healpy",
    # Strong gravitational lensing (Birrer & Amara 2018 Phys Dark Univ 22, 189)
    "lenstronomy",
    # Microlensing fits (Poleski & Yee 2019 Astronomy & Computing 26, 35)
    "MulensModel",
    # 2-point correlation / BAO (Sinha & Garrison 2020 MNRAS 491, 3022)
    # Note: "Corrfunc" requires C build which fails on Python 3.14 — use treecorr instead
    "treecorr",
    # N-body / hydrodynamic simulation post-processing (Turk+ 2011 ApJS 192, 9)
    "yt",
    # Pulsar timing (Luo+ 2021 ApJ 911, 45)
    "pint",
    # ATNF Pulsar catalogue Python interface (Pitkin 2018 JOSS 3, 538)
    "psrqpy",
    # Image restoration / PSF deconvolution
    "skimage", "skimage.restoration",
}

# Blocked operations
BLOCKED_BUILTINS = {
    "exec", "eval", "compile", "__import__",
    "open",  # file I/O blocked — use provided data access functions
    "exit", "quit",
}

# Blocked modules — security-sensitive system access
BLOCKED_MODULES = {
    "os", "sys", "subprocess", "shutil", "pathlib", "glob",
    "socket", "http", "urllib", "requests", "httpx",
}

# Hard memory limit (1 GB) — raises MemoryError if exceeded
MEMORY_HARD_LIMIT = 1024 * 1024 * 1024


class CodeExecutionResult:
    def __init__(self):
        self.stdout: str = ""
        self.stderr: str = ""
        self.error: str | None = None
        self.figures: list[str] = []  # base64 PNG strings
        self.variables: dict[str, str] = {}  # name -> repr (for key results)
        self.variable_types: dict[str, str] = {}  # name -> type name
        self.success: bool = True
        self.backend: str = "unknown"
        self.duration_ms: int = 0
        self.exit_code: int | None = None


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
    if top_level in BLOCKED_MODULES:
        raise ImportError(
            f"Import of '{name}' is blocked for security. "
            f"System modules like os, sys, subprocess are not available in the sandbox."
        )
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

    Raises MemoryError if usage exceeds MEMORY_HARD_LIMIT.
    Returns current usage in bytes.
    """
    usage = _get_memory_usage_bytes()
    if usage > MEMORY_HARD_LIMIT:
        mb = usage / (1024 * 1024)
        limit_mb = MEMORY_HARD_LIMIT / (1024 * 1024)
        raise MemoryError(
            f"Code execution exceeded memory hard limit: ~{mb:.0f} MB in use (limit {limit_mb:.0f} MB). "
            "Reduce data size or use process_in_chunks()."
        )
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

    def lazy_load_table(path: str, format: str = "auto"):
        """Load a large table lazily with Dask for out-of-core processing."""
        try:
            import dask.dataframe as dd
            if format == "csv" or path.endswith(".csv"):
                return dd.read_csv(path)
            elif format == "parquet" or path.endswith(".parquet"):
                return dd.read_parquet(path)
            else:
                # Fall back to pandas for small files
                import pandas as pd
                return pd.read_csv(path)
        except ImportError:
            import pandas as pd
            return pd.read_csv(path)

    return {
        "process_in_chunks": process_in_chunks,
        "load_votable": load_votable,
        "load_csv": load_csv,
        "memory_usage_mb": memory_usage_mb,
        "lazy_load_table": lazy_load_table,
    }


def _make_data_accessor(session_id: str):
    """Create helper functions that the code can call to access platform data."""
    def _normalize_adql_rows(payload):
        if isinstance(payload, dict):
            rows = payload.get("rows")
            return rows if isinstance(rows, list) else []
        if isinstance(payload, list):
            if payload and isinstance(payload[0], dict) and "rows" in payload[0] and "columns" in payload[0]:
                nested_rows = payload[0].get("rows")
                return nested_rows if isinstance(nested_rows, list) else []
            return payload
        return []

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
        """Get the latest ADQL query results as a row-wise list of dicts."""
        from app.services.ai_tools import get_cached_results
        payload = get_cached_results(f"latest_adql:{session_id}") or get_cached_results("latest_adql") or []
        return _normalize_adql_rows(payload)

    def get_latest_adql_result():
        """Get the latest ADQL result set with service/query metadata and rows."""
        from app.services.ai_tools import get_cached_results
        return (
            get_cached_results(f"latest_adql_set:{session_id}")
            or get_cached_results("latest_adql_set")
            or {}
        )

    def get_adql_result_sets():
        """Get recent ADQL result sets for multi-query workflows."""
        from app.services.ai_tools import get_cached_results
        return (
            get_cached_results(f"latest_adql_sets:{session_id}")
            or get_cached_results("latest_adql_sets")
            or []
        )

    def get_cached_results_for_session(key: str):
        """Get cached platform data, preferring this chat/session scope."""
        from app.services.ai_tools import get_cached_results
        scoped_key = f"{key}:{session_id}" if session_id and session_id != "default" else None
        payload = (get_cached_results(scoped_key) if scoped_key else None) or get_cached_results(key)
        if key == "latest_adql":
            return _normalize_adql_rows(payload)
        return payload

    return {
        "load_fits": load_fits,
        "get_search_results": get_search_results,
        "get_adql_results": get_adql_results,
        "get_latest_adql_result": get_latest_adql_result,
        "get_adql_result_sets": get_adql_result_sets,
        "get_cached_results": get_cached_results_for_session,
    }


def _normalize_code(code: str) -> str:
    """Rewrite legacy helper imports to the supported `astro` module."""
    normalized: list[str] = []
    for line in code.splitlines():
        stripped = line.strip()
        if re.fullmatch(r"from\s+app\.services\s+import\s+astro_analysis", stripped):
            normalized.append("import astro as astro_analysis")
            continue
        match = re.fullmatch(
            r"from\s+app\.services\s+import\s+astro_analysis\s+as\s+([A-Za-z_][A-Za-z0-9_]*)",
            stripped,
        )
        if match:
            normalized.append(f"import astro as {match.group(1)}")
            continue
        match = re.fullmatch(r"import\s+astro_analysis(?:\s+as\s+([A-Za-z_][A-Za-z0-9_]*))?", stripped)
        if match:
            alias = match.group(1) or "astro_analysis"
            normalized.append(f"import astro as {alias}")
            continue
        match = re.fullmatch(
            r"import\s+app\.services\.astro_analysis\s+as\s+([A-Za-z_][A-Za-z0-9_]*)",
            stripped,
        )
        if match:
            normalized.append(f"import astro as {match.group(1)}")
            continue
        line = re.sub(r"\bfrom\s+astro_analysis\s+import\b", "from astro import", line)
        line = re.sub(r"\bfrom\s+app\.services\.astro_analysis\s+import\b", "from astro import", line)
        normalized.append(line)
    return "\n".join(normalized)


def _install_savefig_capture(plt):
    """捕获用户显式 savefig 后又 close 的图。"""
    try:
        from matplotlib.figure import Figure
    except Exception:
        return {}, None, lambda: None

    original_savefig = Figure.savefig
    saved_figures: dict[int, str] = {}
    in_capture = False

    def _capture(fig) -> None:
        nonlocal in_capture
        if in_capture:
            return
        try:
            in_capture = True
            buf = io.BytesIO()
            original_savefig(fig, buf, format="png", dpi=150, bbox_inches="tight")
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            if len(b64) <= 8 * 1024 * 1024:
                saved_figures[id(fig)] = b64
        except Exception:
            pass
        finally:
            in_capture = False

    def _wrapped_savefig(self, *args, **kwargs):
        _capture(self)
        return original_savefig(self, *args, **kwargs)

    Figure.savefig = _wrapped_savefig

    def _restore() -> None:
        try:
            Figure.savefig = original_savefig
        except Exception:
            pass

    return saved_figures, original_savefig, _restore


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


def _collect_subprocess_cache_context(session_id: str) -> dict:
    """Snapshot parent-process tool caches for the fresh subprocess."""
    try:
        from app.services import ai_tools
    except Exception:
        return {}

    visible: dict = {}
    session_suffix = f":{session_id}" if session_id and session_id != "default" else ""
    for key in list(getattr(ai_tools, "_search_result_cache", {}).keys()):
        value = ai_tools.get_cached_results(key)
        if value is None:
            continue
        try:
            pickle.dumps(value)
        except Exception:
            continue
        visible[key] = value
        if session_suffix and key.endswith(session_suffix):
            visible[key[: -len(session_suffix)]] = value
    return visible


def _dispatch_subprocess(
    code: str,
    timeout_seconds: float | None = None,
    session_id: str = "default",
) -> CodeExecutionResult | None:
    """Run `code` in the subprocess sandbox backend.

    Returns a CodeExecutionResult on success, or None if the backend could
    not be used (import failure, unsupported platform). Falls back caller
    to the in-process path on None.
    """
    try:
        from app.config import settings as _settings
        from app.services.sandbox.subprocess_backend import SubprocessBackend
    except ImportError as e:
        logger.warning("subprocess sandbox backend unavailable: %s", e)
        return None

    # S1 (PART S): subprocess fresh fork 没有持久化 state.  把 session
    # 历史所有成功 code blocks 作为 prefix 拼在当前 code 前, 一次 exec 里
    # 重建前序变量.  前缀为空时 (首次 run_python / 新 session) 直接跑 code.
    replay_prefix = get_session_code_prefix(session_id)
    effective_code = replay_prefix + code if replay_prefix else code

    backend = SubprocessBackend()
    try:
        raw = backend.execute(
            effective_code,
            timeout=int(timeout_seconds or _settings.sandbox_timeout_seconds),
            memory_bytes=_settings.sandbox_memory_bytes,
            cache_context=_collect_subprocess_cache_context(session_id),
        )
    except Exception as e:
        logger.warning("subprocess sandbox backend raised: %s", e)
        return None

    result = CodeExecutionResult()
    result.stdout = raw.stdout
    result.stderr = raw.stderr
    result.error = raw.error
    result.figures = list(raw.figures)
    result.variables = dict(raw.variables)
    result.variable_types = dict(raw.variable_types)
    result.success = raw.success
    result.backend = raw.backend
    result.duration_ms = raw.duration_ms
    result.exit_code = raw.exit_code
    return result


def _maybe_record_session_history(
    session_id: str, code: str, result: "CodeExecutionResult"
) -> None:
    """成功 run 后把原始 (未拼前缀) 代码 append 到 session history.

    只有 session_id 非 default, 且执行成功, 才记录. 失败的 cell 不入 replay
    (否则后续 subprocess fork 会重复 crash).
    """
    if not session_id or session_id == "default":
        return
    if not result.success:
        return
    try:
        append_session_code_block(session_id, code)
    except Exception as e:  # 记录失败不该阻断成功路径
        logger.debug("append_session_code_block failed: %s", e)


def execute_python(
    code: str,
    context: dict | None = None,
    session_id: str = "default",
    timeout_seconds: float | None = None,
) -> CodeExecutionResult:
    """Execute Python code in a sandboxed environment.

    Args:
        code: Python source code to execute
        context: Optional dict of variables to inject (e.g., data from previous tool calls)

    Returns:
        CodeExecutionResult with stdout, stderr, figures, and key variables
    """
    # Crash-isolated backend path: fresh subprocess per call, no session state.
    # Callers who need Jupyter-like persistence stay on the in-process path.
    # F0.4: log the chosen backend + fallback path so failed runs can be
    # triaged from Render logs without guessing which backend ran.
    try:
        from app.config import settings as _settings
        if _settings.sandbox_backend == "subprocess" and not context:
            logger.info("sandbox: using backend=subprocess (no context)")
            _normalized_for_subprocess = _normalize_code(code)
            sub_result = _dispatch_subprocess(
                _normalized_for_subprocess, timeout_seconds, session_id
            )
            if sub_result is not None:
                # S1: 只记录 "当前 cell" 原始代码到 session history, 供下次
                # subprocess fork 时 replay.  拼进去的 replay_prefix 已经
                # 是前序 history 再次展开, 这里不要二次 append 否则历史膨胀.
                _maybe_record_session_history(
                    session_id, _normalized_for_subprocess, sub_result
                )
                return sub_result
            logger.warning(
                "sandbox: subprocess dispatch returned None; falling back to in-process"
            )
    except Exception as e:
        logger.warning("sandbox: subprocess dispatch raised %s, using in-process", e)

    t0 = _session_time.monotonic()
    result = CodeExecutionResult()
    result.backend = "inprocess"
    code = _normalize_code(code)

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
    # Configure font fallback for CJK characters (avoids □□□ in labels)
    matplotlib.rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC",    # Docker (fonts-noto-cjk)
        "Noto Sans CJK",       # Alternative naming
        "WenQuanYi Micro Hei", # Linux fallback
        "PingFang SC",         # macOS
        "Microsoft YaHei",     # Windows
        "SimHei",              # Windows fallback
        "DejaVu Sans",         # Final ASCII fallback
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False  # Fix minus sign rendering
    import matplotlib.pyplot as plt
    saved_figures, original_savefig, restore_savefig = _install_savefig_capture(plt)

    data_accessors = _make_data_accessor(session_id)
    exec_globals = {
        "__builtins__": safe_builtins,
        "np": np,
        "numpy": np,
        "plt": plt,
        "matplotlib": matplotlib,
        **data_accessors,
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
        try:
            from scipy.optimize import curve_fit
            exec_globals["curve_fit"] = curve_fit
        except ImportError:
            pass
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
        for name, accessor in data_accessors.items():
            setattr(astro, name, accessor)
        exec_globals["astro"] = astro
        exec_globals["astro_analysis"] = astro
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
        exec_globals["pro_gp_detrend"] = astro.pro_gp_detrend
        exec_globals["pro_fit_transit"] = astro.pro_fit_transit
        exec_globals["pro_detect_flares"] = astro.pro_detect_flares
        exec_globals["pro_bls_search"] = astro.pro_bls_search
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

    # CCD reduction helpers
    try:
        from app.analysis.image_reduction import (
            bias_subtract,
            dark_subtract,
            flat_correct,
            cosmic_ray_reject,
            full_reduction,
            solve_astrometry,
            extract_and_photometer,
        )

        exec_globals["bias_subtract"] = bias_subtract
        exec_globals["dark_subtract"] = dark_subtract
        exec_globals["flat_correct"] = flat_correct
        exec_globals["cosmic_ray_reject"] = cosmic_ray_reject
        exec_globals["full_reduction"] = full_reduction
        exec_globals["solve_astrometry"] = solve_astrometry
        exec_globals["extract_and_photometer"] = extract_and_photometer
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

        with _timeout(int(timeout_seconds or MAX_EXEC_TIME)):
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
        open_figure_ids: set[int] = set()
        for fig_num in fig_nums:
            fig = plt.figure(fig_num)
            open_figure_ids.add(id(fig))
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
            if original_savefig is not None:
                original_savefig(
                    fig, buf, format="png", dpi=150, bbox_inches="tight",
                    facecolor="#1c1c1e", edgecolor="none",
                )
            else:
                fig.savefig(
                    buf, format="png", dpi=150, bbox_inches="tight",
                    facecolor="#1c1c1e", edgecolor="none",
                )
            buf.seek(0)
            b64 = base64.b64encode(buf.read()).decode("utf-8")
            result.figures.append(b64)
        for fig_id, b64 in saved_figures.items():
            if fig_id not in open_figure_ids and b64 not in result.figures:
                result.figures.append(b64)
        plt.close("all")
    except Exception as e:
        logger.warning("Failed to capture matplotlib figures: %s", e)
    finally:
        restore_savefig()

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

    result.duration_ms = int((_session_time.monotonic() - t0) * 1000)
    result.exit_code = 0 if result.success else None
    # S1: in-process 也 append history, 这样即使中途 backend 从 inproc 切
    # subprocess (例如 cache_context 变化) 历史仍然一致.
    _maybe_record_session_history(session_id, code, result)
    return result

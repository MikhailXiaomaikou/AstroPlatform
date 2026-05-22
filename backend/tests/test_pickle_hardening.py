"""T1 (PART T): connector_cache.loads_safe guards against pickle RCE.

Attack surface: Redis / SQLite cache or subprocess args with poisoned pickle bytes
carrying malicious __reduce__ → RCE on pickle.loads. RestrictedUnpickler uses a whitelist
find_class to block all non-data classes (subprocess.Popen, os.system, eval, builtins.exec).
"""

import pickle
import pytest


def test_allowed_primitives_roundtrip():
    from app.services.connector_cache import loads_safe

    # stdlib primitives
    for obj in [
        [1, 2, 3],
        {"a": 1, "b": [2, 3]},
        "hello",
        b"bytes",
        None,
        True,
        3.14,
        (1, 2, 3),
        {1, 2, 3},
    ]:
        assert loads_safe(pickle.dumps(obj)) == obj


def test_allowed_astro_object():
    from app.connectors.base import AstroObject
    from app.services.connector_cache import loads_safe

    obj = AstroObject(
        source="test", object_id="M31-id", name="M31",
        ra=10.6847, dec=41.2687, object_type="Galaxy",
    )
    raw = pickle.dumps(obj)
    restored = loads_safe(raw)
    assert restored.name == "M31"
    assert restored.ra == 10.6847


def test_malicious_reduce_os_system_blocked():
    """__reduce__ points to os.system — outside whitelist, should raise."""
    from app.services.connector_cache import loads_safe

    class Evil:
        def __reduce__(self):
            import os
            return (os.system, ("echo pwned",))

    raw = pickle.dumps(Evil())
    with pytest.raises(pickle.UnpicklingError, match="disallowed pickle class"):
        loads_safe(raw)


def test_malicious_reduce_subprocess_blocked():
    """subprocess.Popen is also not on the whitelist."""
    from app.services.connector_cache import loads_safe

    class Evil:
        def __reduce__(self):
            import subprocess
            return (subprocess.Popen, (["echo", "pwned"],))

    raw = pickle.dumps(Evil())
    with pytest.raises(pickle.UnpicklingError):
        loads_safe(raw)


def test_malicious_reduce_eval_blocked():
    """eval/exec is also not on the whitelist."""
    from app.services.connector_cache import loads_safe

    class Evil:
        def __reduce__(self):
            return (eval, ("__import__('os').system('echo pwned')",))

    raw = pickle.dumps(Evil())
    with pytest.raises(pickle.UnpicklingError):
        loads_safe(raw)


def test_arbitrary_user_class_blocked():
    """Non-whitelisted stdlib/external types should also be rejected."""
    from app.services.connector_cache import loads_safe

    # argparse.Namespace is a real class, picklable but NOT on the whitelist
    import argparse
    ns = argparse.Namespace(x=1, y=2)
    raw = pickle.dumps(ns)
    with pytest.raises(pickle.UnpicklingError):
        loads_safe(raw)


def test_numpy_ndarray_roundtrip():
    """When a connector returns ndarray, it should roundtrip successfully."""
    np = pytest.importorskip("numpy")
    from app.services.connector_cache import loads_safe

    arr = np.array([1.0, 2.0, 3.0])
    raw = pickle.dumps(arr)
    restored = loads_safe(raw)
    assert np.array_equal(restored, arr)


def test_pandas_dataframe_roundtrip():
    pd = pytest.importorskip("pandas")
    from app.services.connector_cache import loads_safe

    df = pd.DataFrame({"a": [1, 2, 3], "b": [4.0, 5.0, 6.0]})
    raw = pickle.dumps(df)
    restored = loads_safe(raw)
    pd.testing.assert_frame_equal(restored, df)


def test_cache_roundtrip_via_sqlite_backend(tmp_path):
    """End-to-end: write to SQLite cache, read it back through loads_safe."""
    from app.services.connector_cache import SQLiteBackend
    from app.connectors.base import AstroObject

    backend = SQLiteBackend(tmp_path / "cache.db")
    obj = AstroObject(
        source="test", object_id="NGC4993-id", name="NGC 4993",
        ra=197.4509, dec=-23.3814, object_type="Galaxy",
    )
    backend.set("test_key", [obj], ttl=3600)
    restored = backend.get("test_key")
    assert restored is not None
    assert len(restored) == 1
    assert restored[0].name == "NGC 4993"


def test_cache_rejects_malicious_entry(tmp_path):
    """Manually insert malicious pickle bytes into SQLite; read should safely reject."""
    import sqlite3
    import time
    from app.services.connector_cache import SQLiteBackend

    backend = SQLiteBackend(tmp_path / "cache.db")

    class Evil:
        def __reduce__(self):
            import os
            return (os.system, ("echo pwned",))

    evil_bytes = pickle.dumps(Evil())
    with sqlite3.connect(str(backend.path)) as db:
        db.execute(
            "INSERT INTO connector_cache (key, value, expires_at) VALUES (?, ?, ?)",
            ("evil_key", evil_bytes, time.time() + 3600),
        )

    # loads_safe inside .get() should raise → caught → returns None, not RCE
    result = backend.get("evil_key")
    assert result is None  # safely rejected, os.system not executed


def test_collect_subprocess_cache_context_skips_unsafe():
    """_collect_subprocess_cache_context should skip malicious cache values instead of leaking them to subprocess.

    _search_result_cache entries are (value, timestamp) tuples. We insert directly
    in tuple format, bypassing store_search_results helper, to simulate a Redis-poisoned
    cache containing malicious objects.
    """
    import time
    from app.services import ai_tools, code_executor

    class Evil:
        def __reduce__(self):
            import os
            return (os.system, ("echo pwned",))

    original_cache = dict(getattr(ai_tools, "_search_result_cache", {}))
    try:
        # directly poison the cache: (value, timestamp) format
        ai_tools._search_result_cache["evil:default"] = (Evil(), time.time())
        ai_tools._search_result_cache["good:default"] = (
            [{"name": "M31", "ra": 10.68, "dec": 41.26}], time.time()
        )
        ctx = code_executor._collect_subprocess_cache_context("default")
        # malicious entry should be skipped; well-formed list[dict] should be preserved
        assert "evil:default" not in ctx
        assert "good:default" in ctx
    finally:
        ai_tools._search_result_cache.clear()
        ai_tools._search_result_cache.update(original_cache)

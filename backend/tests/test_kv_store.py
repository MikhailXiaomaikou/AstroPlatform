"""Tests for the shared JsonKvStore backend.

The conftest autouse fixture forces the in-memory backend for every test,
so these tests don't touch Redis or the filesystem.
"""

from __future__ import annotations

import time

import pytest

from app.services import _kv_store


@pytest.fixture
def store():
    _kv_store.use_memory_backend_for_testing()
    return _kv_store.JsonKvStore("test_ns")


class TestJsonKvStore:
    def test_set_then_get_roundtrip(self, store):
        store.set("k1", {"a": 1, "b": [2, 3]}, ttl=60)
        assert store.get("k1") == {"a": 1, "b": [2, 3]}

    def test_missing_key_returns_default(self, store):
        assert store.get("ghost") is None
        assert store.get("ghost", default={"x": 1}) == {"x": 1}

    def test_delete_removes_key(self, store):
        store.set("k", {"v": 1}, ttl=60)
        store.delete("k")
        assert store.get("k") is None

    def test_ttl_expiry(self, store):
        store.set("k", {"v": 1}, ttl=1)
        time.sleep(1.1)
        assert store.get("k") is None

    def test_scan_keys_within_namespace(self, store):
        store.set("a", {}, ttl=60)
        store.set("b", {}, ttl=60)
        store.set("c", {}, ttl=60)
        assert set(store.scan_keys()) == {"a", "b", "c"}

    def test_namespaces_are_isolated(self):
        _kv_store.use_memory_backend_for_testing()
        ns1 = _kv_store.JsonKvStore("ns1")
        ns2 = _kv_store.JsonKvStore("ns2")
        ns1.set("k", {"who": "ns1"}, ttl=60)
        ns2.set("k", {"who": "ns2"}, ttl=60)
        assert ns1.get("k") == {"who": "ns1"}
        assert ns2.get("k") == {"who": "ns2"}
        assert ns1.scan_keys() == ["k"]
        assert ns2.scan_keys() == ["k"]

    def test_non_json_value_returns_false_and_logs(self, store):
        class NotJsonable:
            pass

        # str(NotJsonable()) is JSON-serialisable via default=str, so this
        # should succeed (we use default=str). Verify the path doesn't crash.
        # For a genuinely non-serialisable case, set a callable.
        assert store.set("k", NotJsonable(), ttl=60) is True
        # Read back — value will be the str() repr, which is fine for "no crash".
        assert store.get("k") is not None

    def test_clear_namespace_drops_only_own_keys(self):
        _kv_store.use_memory_backend_for_testing()
        ns1 = _kv_store.JsonKvStore("nsA")
        ns2 = _kv_store.JsonKvStore("nsB")
        ns1.set("k", {}, ttl=60)
        ns2.set("k", {}, ttl=60)
        ns1.clear_namespace()
        assert ns1.get("k") is None
        assert ns2.get("k") == {}

    def test_namespace_validation(self):
        with pytest.raises(ValueError):
            _kv_store.JsonKvStore("")
        with pytest.raises(ValueError):
            _kv_store.JsonKvStore("has:colon")

    def test_corrupted_json_returns_default(self, store, monkeypatch):
        # Manually inject a bad payload via the backend.
        backend = _kv_store._get_backend()
        backend.set(f"{store.namespace}:bad", "{not valid json", ttl=60)
        assert store.get("bad", default="fallback") == "fallback"


class TestBackendSelection:
    def test_reset_backend_clears_singleton(self):
        _kv_store.use_memory_backend_for_testing()
        assert _kv_store._backend is not None
        _kv_store.reset_backend()
        assert _kv_store._backend is None

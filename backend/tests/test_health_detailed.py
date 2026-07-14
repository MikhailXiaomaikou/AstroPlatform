"""Regression: /health/detailed must be able to report a healthy storage.

The old storage probe imported ``minio`` and connected to a MinIO server
that the platform no longer uses (FITS storage is local filesystem, see
app/storage.py), so the probe failed on every call and the endpoint's
"degraded" signal was permanently on. The probe must now check the real
backend: writability of ``settings.local_storage_dir``.
"""


class TestHealthDetailedStorageProbe:
    async def test_storage_probe_ok_on_healthy_system(
        self, app_client, test_user, monkeypatch, tmp_path
    ):
        from app.config import settings

        monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "fits"))

        # External astronomy probes are out of scope here — no network in tests.
        async def _fake_probe(url, timeout=2.0):
            return "ok", 1

        monkeypatch.setattr("app.api.health._probe_url", _fake_probe)

        _user, token = test_user
        resp = await app_client.get(
            "/health/detailed",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        storage = resp.json()["checks"]["storage"]
        assert storage["status"] == "ok"
        # No probe residue left behind.
        assert list((tmp_path / "fits").iterdir()) == []

    async def test_storage_probe_degrades_when_dir_unwritable(
        self, app_client, test_user, monkeypatch
    ):
        from app.config import settings

        # A path that cannot be created (parent is a file, not a directory).
        monkeypatch.setattr(settings, "local_storage_dir", "/dev/null/fits")

        async def _fake_probe(url, timeout=2.0):
            return "ok", 1

        monkeypatch.setattr("app.api.health._probe_url", _fake_probe)

        _user, token = test_user
        resp = await app_client.get(
            "/health/detailed",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["checks"]["storage"]["status"] == "error"
        assert body["status"] == "degraded"

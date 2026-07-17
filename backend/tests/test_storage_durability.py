"""Durability and scientific-integrity contracts for research storage."""

from __future__ import annotations

import pytest


def _local(monkeypatch, tmp_path):
    from app import storage

    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "local_storage_dir", str(tmp_path))
    monkeypatch.setattr(storage.settings, "storage_require_integrity", False)
    storage.reset_storage_clients()
    return storage


def test_local_round_trip_records_and_checks_sha256(monkeypatch, tmp_path):
    storage = _local(monkeypatch, tmp_path)
    key = storage.upload_fits("user/run/chain.npz", b"posterior-chain")

    assert key == "user/run/chain.npz"
    assert storage.download_fits(key) == b"posterior-chain"
    metadata = storage.get_storage_metadata(key)
    assert metadata["backend"] == "local"
    assert metadata["size_bytes"] == len(b"posterior-chain")
    assert len(metadata["sha256"]) == 64


def test_local_read_refuses_tampered_scientific_bytes(monkeypatch, tmp_path):
    storage = _local(monkeypatch, tmp_path)
    key = storage.upload_fits("chains/result.bin", b"trusted")
    (tmp_path / key).write_bytes(b"tampered")

    with pytest.raises(storage.StorageIntegrityError, match="SHA-256 mismatch"):
        storage.download_fits(key)


def test_legacy_local_object_without_sidecar_remains_readable(monkeypatch, tmp_path):
    storage = _local(monkeypatch, tmp_path)
    target = tmp_path / "legacy" / "old.fits"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"legacy")

    assert storage.download_fits("legacy/old.fits") == b"legacy"
    assert storage.get_storage_metadata("legacy/old.fits")["sha256"] is None


def test_production_integrity_mode_rejects_legacy_unhashed_bytes(monkeypatch, tmp_path):
    storage = _local(monkeypatch, tmp_path)
    target = tmp_path / "legacy" / "old.fits"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"legacy")
    monkeypatch.setattr(storage.settings, "storage_require_integrity", True)

    with pytest.raises(storage.StorageIntegrityError, match="No SHA-256 metadata"):
        storage.download_fits("legacy/old.fits")


def test_delete_removes_object_and_integrity_sidecar(monkeypatch, tmp_path):
    storage = _local(monkeypatch, tmp_path)
    key = storage.upload_fits("exports/report.pdf", b"pdf")
    storage.delete_fits(key)

    assert not (tmp_path / key).exists()
    assert not (tmp_path / "exports" / "report.pdf.sha256").exists()
    with pytest.raises(FileNotFoundError):
        storage.download_fits(key)


@pytest.mark.parametrize("path", ["../secret", "/absolute", "a/../../b", "a\\..\\b", ""])
def test_every_backend_rejects_unsafe_object_keys(monkeypatch, tmp_path, path):
    storage = _local(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        storage.upload_fits(path, b"x")


class _FakeBody:
    def __init__(self, value: bytes):
        self._value = value

    def read(self):
        return self._value


class _FakeS3:
    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self):
        self.objects = {}

    def put_object(self, *, Bucket, Key, Body, Metadata):
        self.objects[(Bucket, Key)] = (bytes(Body), dict(Metadata))

    def head_object(self, *, Bucket, Key):
        try:
            body, metadata = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise RuntimeError("missing") from exc
        return {"ContentLength": len(body), "Metadata": metadata, "VersionId": "v1"}

    def get_object(self, *, Bucket, Key):
        try:
            body, metadata = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise self.exceptions.NoSuchKey() from exc
        return {"Body": _FakeBody(body), "Metadata": metadata}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


def test_s3_round_trip_verifies_digest_and_health(monkeypatch, tmp_path):
    storage = _local(monkeypatch, tmp_path)
    fake = _FakeS3()
    monkeypatch.setattr(storage.settings, "storage_backend", "s3")
    monkeypatch.setattr(storage.settings, "s3_bucket", "science")
    monkeypatch.setattr(storage, "_s3_client", lambda: fake)

    key = storage.upload_fits("runs/42/posterior.nc", b"chain")
    assert storage.download_fits(key) == b"chain"
    assert storage.get_storage_metadata(key) == {
        "backend": "s3",
        "key": key,
        "size_bytes": 5,
        "sha256": "9414886b1ebf025db067a4cbd13a0903fbd9733a5372bba1b58bd72c1699b798",
        "version_id": "v1",
    }
    assert storage.storage_healthcheck() == {"ok": True, "backend": "s3"}


def test_s3_read_refuses_corrupt_payload(monkeypatch, tmp_path):
    storage = _local(monkeypatch, tmp_path)
    fake = _FakeS3()
    monkeypatch.setattr(storage.settings, "storage_backend", "s3")
    monkeypatch.setattr(storage.settings, "s3_bucket", "science")
    monkeypatch.setattr(storage, "_s3_client", lambda: fake)
    key = storage.upload_fits("runs/result.bin", b"trusted")
    body, metadata = fake.objects[("science", key)]
    fake.objects[("science", key)] = (b"corrupt", metadata)

    with pytest.raises(storage.StorageIntegrityError):
        storage.download_fits(key)


class _FakeVersionPaginator:
    def __init__(self, client):
        self.client = client

    def paginate(self, *, Bucket, Prefix):
        versions = [
            {"Key": key, "VersionId": version_id}
            for key, version_id in sorted(self.client.versions)
            if key.startswith(Prefix)
        ]
        markers = [
            {"Key": key, "VersionId": version_id}
            for key, version_id in sorted(self.client.markers)
            if key.startswith(Prefix)
        ]
        return [{"Versions": versions, "DeleteMarkers": markers}]


class _FakeVersionedS3:
    def __init__(self):
        self.versions: set[tuple[str, str]] = set()
        self.markers: set[tuple[str, str]] = set()

    def get_bucket_versioning(self, *, Bucket):
        assert Bucket == "science"
        return {"Status": "Enabled"}

    def get_paginator(self, name):
        assert name == "list_object_versions"
        return _FakeVersionPaginator(self)

    def delete_objects(self, *, Bucket, Delete):
        assert Bucket == "science"
        for item in Delete["Objects"]:
            pair = (item["Key"], item["VersionId"])
            self.versions.discard(pair)
            self.markers.discard(pair)
        return {"Deleted": list(Delete["Objects"])}


def test_account_erasure_removes_every_s3_object_version(monkeypatch, tmp_path):
    storage = _local(monkeypatch, tmp_path)
    fake = _FakeVersionedS3()
    key = "jobs/user-1/result.json.gz"
    fake.versions.update({(key, "v1"), (key, "v2"), (key + ".bak", "other")})
    fake.markers.add((key, "marker-1"))
    monkeypatch.setattr(storage.settings, "storage_backend", "s3")
    monkeypatch.setattr(storage.settings, "s3_bucket", "science")
    monkeypatch.setattr(storage, "_s3_client", lambda: fake)

    storage.delete_fits_all_versions(key)

    assert not {item for item in fake.versions if item[0] == key}
    assert not {item for item in fake.markers if item[0] == key}
    assert (key + ".bak", "other") in fake.versions

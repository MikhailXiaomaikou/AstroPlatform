"""Chain persistence: getdist export, fail-open labelling, and gate hygiene."""

from __future__ import annotations

import hashlib
import uuid

import numpy as np
import pytest

from app.services import chain_export

OWNER_ID = str(uuid.UUID("00000000-0000-4000-8000-000000000001"))


def _payload() -> dict:
    rng = np.random.default_rng(7)
    samples = np.column_stack(
        [rng.normal(67.4, 0.5, 64), rng.normal(0.315, 0.007, 64)]
    )
    return {
        "samples": samples,
        "parameter_order": ["H0", "omegam"],
        "prior_bounds": {"H0": (50.0, 90.0), "omegam": (0.1, 0.6)},
        "derived_samples": {"S8": rng.normal(0.83, 0.01, 64)},
        "sampler": "importance_bao",
        "seed": 20260724,
    }


def test_getdist_triplet_layout_and_consistency():
    files = chain_export.render_getdist_files(_payload())
    assert set(files) == {"chain_1.txt", "chain.paramnames", "chain.ranges"}

    rows = files["chain_1.txt"].decode().strip().splitlines()
    assert len(rows) == 64
    first = rows[0].split()
    # weight, -loglike, H0, omegam, S8(derived)
    assert len(first) == 5
    assert float(first[0]) == 1.0  # equal-weight resampled draws
    assert float(first[1]) == 0.0  # loglike unavailable, stated not faked

    names = [line.split("\t")[0] for line in files["chain.paramnames"].decode().strip().splitlines()]
    assert names == ["H0", "omegam", "S8*"]  # derived params carry getdist's *

    ranges = files["chain.ranges"].decode().strip().splitlines()
    assert ranges[0].split() == ["H0", "50", "90"]
    assert ranges[1].split()[0] == "omegam"


def test_render_rejects_shape_mismatch():
    payload = _payload()
    payload["parameter_order"] = ["H0"]
    with pytest.raises(ValueError):
        chain_export.render_getdist_files(payload)


def test_persist_uploads_verified_objects(monkeypatch, tmp_path):
    from app import storage

    monkeypatch.setattr(storage.settings, "local_storage_dir", str(tmp_path / "objects"))
    block = chain_export.persist_chain_artifacts(_payload(), owner_id=OWNER_ID)

    assert block["status"] == "persisted"
    assert block["format"] == "getdist"
    assert block["loglike_available"] is False
    assert len(block["files"]) == 3
    for entry in block["files"]:
        assert entry["output_path"].startswith(f"chains/{OWNER_ID}/{block['run_id']}/")
        data = storage.download_fits(entry["output_path"])
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        assert len(data) == entry["size_bytes"]


def test_persist_failure_is_fail_open_and_labelled(monkeypatch):
    from app import storage

    def _boom(path: str, data: bytes) -> str:
        raise RuntimeError("storage down")

    monkeypatch.setattr(storage, "upload_fits", _boom)
    block = chain_export.persist_chain_artifacts(_payload(), owner_id=OWNER_ID)
    assert block["status"] == "persist_failed"
    assert block["files"] == []


def test_chat_exec_wrapper_persists_and_strips_payload(monkeypatch, tmp_path):
    from app import storage
    from app.services.ai_tools_cosmology import _exec_run_cosmology_likelihood_chain

    monkeypatch.setattr(storage.settings, "local_storage_dir", str(tmp_path / "objects"))
    result = _exec_run_cosmology_likelihood_chain(
        {"model": "lcdm", "dataset_keys": ["desi_dr1_bao"], "n_samples": 512},
        user_id=OWNER_ID,
    )

    assert result.get("success") is True
    assert "_chain_payload" not in result  # raw arrays must never leave the wrapper
    block = result["chain_artifacts"]
    assert block["status"] == "persisted"
    assert {entry["name"] for entry in block["files"]} >= {"chain_1.txt", "chain.paramnames"}
    # The chat wrapper is the ONLY persistence caller; identity-less calls
    # (matrix / oracle / audit paths) must stay artifact-free.
    anonymous = _exec_run_cosmology_likelihood_chain(
        {"model": "lcdm", "dataset_keys": ["desi_dr1_bao"], "n_samples": 512},
        user_id=None,
    )
    assert "chain_artifacts" not in anonymous
    assert "_chain_payload" not in anonymous


def test_matrix_path_produces_no_chain_payload():
    from app.services.cosmology_likelihoods import run_likelihood_chain

    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["desi_dr1_bao"],
        n_samples=512,
    )
    assert "_chain_payload" not in result
    assert "chain_artifacts" not in result

"""Chain persistence: getdist export, fail-open labelling, and gate hygiene."""

from __future__ import annotations

import hashlib
import uuid

import numpy as np
import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.services import chain_export

OWNER_ID = str(uuid.UUID("00000000-0000-4000-8000-000000000001"))


@pytest.fixture(autouse=True)
def _isolated_cleanup_queue_table():
    """Make chain-export tests independent of a pre-migrated developer DB."""

    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.services.durable_research_records import _engine

    sync_engine = _engine()
    ArtifactCleanupQueue.__table__.create(bind=sync_engine, checkfirst=True)
    with Session(sync_engine) as db:
        db.execute(delete(ArtifactCleanupQueue))
        db.commit()
    yield
    with Session(sync_engine) as db:
        db.execute(delete(ArtifactCleanupQueue))
        db.commit()


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

    lines = files["chain_1.txt"].decode().strip().splitlines()
    header = [line for line in lines if line.startswith("#")]
    rows = [line for line in lines if not line.startswith("#")]
    # In-band disclaimer: the -loglike zeros must be declared on the file
    # itself, not only in the ephemeral tool result.
    assert any("loglike_available=false" in line for line in header)
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
    block = result["chain_downloads"]
    assert block["status"] == "persisted"
    assert {entry["name"] for entry in block["files"]} >= {"chain_1.txt", "chain.paramnames"}
    # The chat wrapper is the ONLY persistence caller; identity-less calls
    # (matrix / oracle / audit paths) must stay artifact-free.
    anonymous = _exec_run_cosmology_likelihood_chain(
        {"model": "lcdm", "dataset_keys": ["desi_dr1_bao"], "n_samples": 512},
        user_id=None,
    )
    assert "chain_downloads" not in anonymous
    assert "_chain_payload" not in anonymous


def test_matrix_path_produces_no_chain_payload():
    from app.services.cosmology_likelihoods import run_likelihood_chain

    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["desi_dr1_bao"],
        n_samples=512,
    )
    assert "_chain_payload" not in result
    assert "chain_downloads" not in result


def test_blocked_tier_chain_is_never_exported(monkeypatch, tmp_path):
    # Adversarial-review blocker (2026-07-25, reproduced live): a blocked-tier
    # run redacts its parameter summaries, but the raw sample cloud was still
    # uploaded and offered for download — recovering the exact CMB-free H0
    # the run refuses to state. desi_dr1_bao + bbn_ombh2_schoeneberg24 is the
    # reviewer's trivially-reachable chat repro.
    from app import storage
    from app.services.ai_tools_cosmology import _exec_run_cosmology_likelihood_chain

    monkeypatch.setattr(storage.settings, "local_storage_dir", str(tmp_path / "objects"))
    result = _exec_run_cosmology_likelihood_chain(
        {
            "model": "lcdm",
            "dataset_keys": ["desi_dr1_bao", "bbn_ombh2_schoeneberg24"],
            "n_samples": 512,
        },
        user_id=OWNER_ID,
    )
    assert result.get("chain_tier") == "blocked" or result.get("__do_not_claim__") is True
    assert "_chain_payload" not in result
    assert "chain_downloads" not in result
    # Nothing may have reached storage for this owner.
    objects_root = tmp_path / "objects"
    uploaded = list(objects_root.rglob("chain*")) if objects_root.exists() else []
    assert uploaded == []


def test_partial_upload_failure_is_atomic(monkeypatch, tmp_path):
    # Adversarial review (minor): a partial triplet must never be registered
    # or advertised — on any upload failure the block reports zero files, and
    # strays remain staged (never grace-renewed) for the artifact janitor.
    from app import storage

    monkeypatch.setattr(storage.settings, "local_storage_dir", str(tmp_path / "objects"))
    real_upload = storage.upload_fits
    calls = {"n": 0}

    def flaky(path: str, data: bytes) -> str:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("storage down mid-run")
        return real_upload(path, data)

    monkeypatch.setattr(storage, "upload_fits", flaky)
    renewed: list[str] = []
    from app.services import artifact_cleanup

    monkeypatch.setattr(
        artifact_cleanup,
        "renew_artifact_cleanup_grace_sync",
        lambda ref: renewed.append(ref),
    )

    block = chain_export.persist_chain_artifacts(_payload(), owner_id=OWNER_ID)
    assert block["status"] == "persist_failed"
    assert block["files"] == []
    assert renewed == []  # the stray stays staged for cleanup


def test_raw_cobaya_payload_roundtrip(monkeypatch, tmp_path):
    from app import storage

    monkeypatch.setattr(storage.settings, "local_storage_dir", str(tmp_path / "objects"))
    raw_chain = b"# weight -logpost H0 omegam\n1 12.5 67.4 0.31\n2 11.9 67.6 0.32\n"
    block = chain_export.persist_chain_artifacts(
        {
            "raw_files": {"chain.1.txt": raw_chain, "chain.input.yaml": b"sampler: mcmc\n"},
            "parameter_order": ["H0", "omegam"],
            "prior_bounds": {"H0": (50.0, 90.0), "omegam": (0.1, 0.6)},
            "sampler": "cobaya_mcmc",
            "seed": 7,
        },
        owner_id=OWNER_ID,
    )
    assert block["status"] == "persisted"
    assert block["loglike_available"] is True  # real -logpost column preserved
    names = {entry["name"] for entry in block["files"]}
    assert {"chain.1.txt", "chain.input.yaml", "chain.paramnames", "chain.ranges"} <= names
    for entry in block["files"]:
        if entry["name"] == "chain.1.txt":
            assert storage.download_fits(entry["output_path"]) == raw_chain


async def test_execute_tool_channel_normalizes_and_registers(monkeypatch, tmp_path):
    # The user-facing channel: execute_tool -> account guard -> dispatch ->
    # normalize -> artifact registration. Uses the sanctioned unit pattern
    # (guard + ledger monkeypatched; ledger DISCOVERY tested for real) since
    # the durable sync engine is not the fixture DB.
    from app import storage
    from app.services import account_deletion
    from app.services import ai_tools

    monkeypatch.setattr(storage.settings, "local_storage_dir", str(tmp_path / "objects"))
    monkeypatch.setattr(account_deletion, "account_runtime_is_active", lambda _uid: True)
    registered: list[dict] = []

    def capture_register(**kwargs):
        registered.append(kwargs)
        return []

    monkeypatch.setattr(account_deletion, "register_result_artifacts", capture_register)

    result = await ai_tools.execute_tool(
        "run_cosmology_likelihood_chain",
        {"model": "lcdm", "dataset_keys": ["desi_dr1_bao"], "n_samples": 512},
        user_id=OWNER_ID,
    )

    assert "_chain_payload" not in result
    block = result.get("chain_downloads")
    assert block and block["status"] == "persisted"
    assert result.get("reproducibility", {}).get("run_id")  # envelope intact

    # The dispatcher's registration pass ran for this owner...
    assert registered and str(registered[0]["user_id"]) == OWNER_ID
    # ...and the real discovery walk finds every chain key in the result.
    discovered = account_deletion.collect_result_output_paths(result)
    for entry in block["files"]:
        assert entry["output_path"] in discovered

"""P1 Claim Audit lifecycle, owner isolation, and Evidence Pack tests."""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.claim_audit_records import EvidencePack
from app.models.research_records import ResearchJob
from app.services.server_evidence import build_research_job_attestation


async def _register(app_client, username: str) -> dict[str, str]:
    response = await app_client.post(
        "/api/auth/register",
        json={"username": username, "password": "password123"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _enable_claim_audit(monkeypatch, tmp_path) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "claim_audit_enabled", True)
    monkeypatch.setattr(settings, "claim_audit_execution_mode", "inline")
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_require_integrity", True)
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "objects"))


def _missing_desi_matrix_result() -> dict:
    return {
        "success": True,
        "analysis_status": "DARK_ENERGY_EVIDENCE_MATRIX_PARTIAL",
        "publication_ready": False,
        "__do_not_claim__": True,
        "claim_scope": "dark_energy_evidence_matrix_diagnostic",
        "official_ready_cells": 0,
        "model": "w0wa_cdm",
        "bao_dataset_key": "desi_dr2_bao",
        "supernova_sets": ["pantheon_plus", "union3", "des_sn5yr"],
        "include_desi_dr1_reference": False,
        "matrix_size": 3,
        "matrix": [
            {
                "cell_id": f"desi_dr2:w0wa_cdm:{sn}:default_cmb",
                "model": "w0wa_cdm",
                "bao_dataset_key": "desi_dr2_bao",
                "supernova_selection": sn,
                "status": "WITHHELD",
                "withheld_reasons": ["official_chain_cache_not_configured"],
            }
            for sn in ("pantheon_plus", "union3", "des_sn5yr")
        ],
        "provenance": {
            "cosmology_analysis_registry": {
                "registry_version": "test-registry",
                "manifest_sha256": "a" * 64,
                "analysis_ids": [],
            }
        },
    }


def _ready_desi_matrix_result() -> dict:
    artifacts = [
        {
            "role": role,
            "relative_path": f"official/{filename}",
            "sha256": str(index) * 64,
            "url": f"https://data.desi.lbl.gov/official/{filename}",
        }
        for index, (role, filename) in enumerate(
            [
                ("config", "chain.updated.yaml"),
                ("checkpoint", "chain.checkpoint"),
                ("chain", "chain.1.txt"),
                ("chain", "chain.2.txt"),
                ("chain", "chain.3.txt"),
                ("chain", "chain.4.txt"),
                ("reference_summary", "chain.margestats"),
            ],
            start=1,
        )
    ]
    cells = []
    for sn in ("pantheon_plus", "union3", "des_sn5yr"):
        cells.append(
            {
                "cell_id": f"desi_dr2:w0wa_cdm:{sn}:default_cmb",
                "analysis_id": f"desi_dr2_w0wa_cdm_{sn}_default_cmb",
                "model": "w0wa_cdm",
                "bao_dataset_key": "desi_dr2_bao",
                "supernova_selection": sn,
                "official_sn_component": sn,
                "data_components": ["desi-bao-all", sn, "default-cmb"],
                "execution_source": "published_external",
                "evidence_tier": "published_external",
                "status": "COMPLETED",
                "publication_ready": False,
                "claim_scope": "published_external_chain_context",
                "overlap_groups": ["desi_dr2_bao:all", "cmb:default"],
                "source_url": "https://data.desi.lbl.gov/official/",
                "paper_arxiv": "2503.14738",
                "paper_doi": "10.1103/tr6y-kpc6",
                "license": {"name": "CC-BY-4.0", "url": "https://example.invalid/license"},
                "analysis_contract": {
                    "release": "DR2 v1.0",
                    "parameter_map": {"w0": "w", "wa": "wa"},
                    "weight_column": "weight",
                    "burn_in_rule": "checkpoint burn_in=0",
                    "chain_format": "Cobaya ASCII",
                },
                "parameter_intervals": {
                    "w0": {"mean": -0.8, "q16": -0.9, "q84": -0.7}
                },
                "two_dimensional_contours": {
                    "w0__wa": {
                        "representation": "weighted_histogram2d_hpd_display",
                        "probability_grid": [[1.0]],
                    }
                },
                "diagnostics": {
                    "rows_per_chain": [100, 100, 100, 100],
                    "rows_total": 400,
                    "column_order": ["weight", "w", "wa"],
                    "sum_weights": 500.0,
                    "kish_weight_ess": 350.0,
                    "checkpoint": {
                        "converged": True,
                        "rminus1_last": 0.001,
                        "burn_in": 0,
                        "mpi_size": 4,
                    },
                    "official_reference_acceptance": {
                        "status": "PASSED",
                        "reference_artifact": "chain.margestats",
                        "center_max_sigma": 0.1,
                        "interval_width_max_fraction": 0.05,
                        "checks": {"w0": {"center_pass": True, "width_pass": True}},
                    },
                },
                "support_artifacts": artifacts,
            }
        )
    return {
        "success": True,
        "analysis_status": "DARK_ENERGY_EVIDENCE_MATRIX_READY",
        "publication_ready": False,
        "__do_not_claim__": True,
        "claim_scope": "dark_energy_evidence_matrix_diagnostic",
        "model": "w0wa_cdm",
        "bao_dataset_key": "desi_dr2_bao",
        "supernova_sets": ["pantheon_plus", "union3", "des_sn5yr"],
        "include_desi_dr1_reference": False,
        "matrix_size": 3,
        "official_ready_cells": 3,
        "matrix": cells,
        "tension_lab": {
            "status": "correlated_tension_withheld",
            "naive_independent_sigma_allowed": False,
            "comparisons": [
                {
                    "left_cell_id": cells[0]["cell_id"],
                    "right_cell_id": cells[1]["cell_id"],
                    "parameter": "w0",
                    "tension_sigma": None,
                }
            ],
        },
        "literature_context": {
            "source": "https://data.desi.lbl.gov/official/",
            "paper_arxiv": "2503.14738",
            "paper_doi": "10.1103/tr6y-kpc6",
        },
        "provenance": {
            "cosmology_analysis_registry": {
                "registry_version": "test-registry",
                "manifest_url": "https://data.desi.lbl.gov/manifest",
                "manifest_sha256": "f" * 64,
                "analysis_ids": [cell["analysis_id"] for cell in cells],
            }
        },
    }


async def test_claim_audit_is_dark_until_feature_gate(app_client):
    headers = await _register(app_client, "audit-dark")
    response = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "H0 = 70",
            "source": {"kind": "doi", "value": "10.0000/example"},
        },
        headers=headers,
    )
    assert response.status_code == 404


async def test_claim_audit_enforces_durable_per_owner_active_limit(
    app_client,
    monkeypatch,
    tmp_path,
):
    from app.api import claim_audits
    from app.config import settings

    _enable_claim_audit(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "claim_audit_execution_mode", "celery")
    monkeypatch.setattr(settings, "claim_audit_max_active_per_user", 2)
    monkeypatch.setattr(claim_audits, "_dispatch_claim_audit", lambda _audit_id: None)
    headers = await _register(app_client, "audit-active-cap")

    for index in range(2):
        response = await app_client.post(
            "/api/research/claim-audits",
            json={
                "claim_text": f"Registered claim {index}",
                "source": {"kind": "doi", "value": f"10.0000/cap-{index}"},
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        assert response.json()["lifecycle_status"] == "QUEUED"

    rejected = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "Third registered claim",
            "source": {"kind": "doi", "value": "10.0000/cap-third"},
        },
        headers=headers,
    )
    assert rejected.status_code == 429
    assert "active-work limit" in rejected.json()["detail"]


async def test_audit_without_current_run_is_withheld_and_pack_verifies(
    app_client,
    monkeypatch,
    tmp_path,
):
    _enable_claim_audit(monkeypatch, tmp_path)
    headers = await _register(app_client, "audit-withheld")
    response = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "H0 = 70 km/s/Mpc",
            "source": {"kind": "doi", "value": "10.0000/example"},
            # Untrusted client fields are ignored and cannot promote a claim.
            "tool_results": [{"publication_ready": True, "H0": 70}],
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["lifecycle_status"] == "COMPLETED"
    assert body["scientific_verdict"] == "WITHHELD"
    assert body["normalized_claims"][0]["verdict"] == "WITHHELD"
    assert body["normalized_claims"][0]["supporting_evidence_ids"] == []
    pack = body["evidence_pack"]
    assert pack["status"] == "FINALIZED"

    download = await app_client.get(pack["download_url"], headers=headers)
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)) == 4
        assert set(names) == {
            "citations.bib",
            "manifest.json",
            "provenance.json",
            "report.md",
        }
        manifest = json.loads(archive.read("manifest.json"))
        provenance = json.loads(archive.read("provenance.json"))
        assert manifest["source"]["identifier"] == "10.0000/example"
        assert manifest["source"]["verified"] is False
        assert manifest["source"]["resolution_status"] == "syntax_validated_only"
        assert manifest["pack_content_hash"] == manifest["content_root"]
        assert manifest["software_release"]
        assert manifest["git_commit"]
        assert provenance["source"] == {
            "kind": "doi",
            "identifier": "10.0000/example",
            "resolution_status": "syntax_validated_only",
            "verified": False,
        }
        assert b"10.0000/example" in archive.read("citations.bib")

    verified = await app_client.post(
        "/api/research/evidence-packs/verify",
        json={"pack_id": pack["pack_id"]},
        headers=headers,
    )
    assert verified.status_code == 200
    assert verified.json()["valid"] is True
    assert verified.json()["scientific_verdict"] == "WITHHELD"

    from app.services.claim_audit_service import _build_zip

    with zipfile.ZipFile(io.BytesIO(download.content)) as original:
        changed_files = {
            name: original.read(name) for name in original.namelist()
        }
    changed_files["report.md"] += b"\nmutated"
    tampered = await app_client.post(
        "/api/research/evidence-packs/verify",
        files={"file": ("tampered.zip", _build_zip(changed_files), "application/zip")},
        headers=headers,
    )
    assert tampered.status_code == 200
    assert tampered.json() == {"valid": False, "reason": "hash_mismatch:report.md"}

    source = io.BytesIO(download.content)
    target = io.BytesIO()
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as changed:
        for name in original.namelist():
            changed.writestr(name, original.read(name))
        changed.writestr("untracked.txt", b"not covered by the manifest")
    extra_file = await app_client.post(
        "/api/research/evidence-packs/verify",
        files={"file": ("extra.zip", target.getvalue(), "application/zip")},
        headers=headers,
    )
    assert extra_file.json() == {"valid": False, "reason": "invalid_pack_layout"}


async def test_evidence_pack_remains_verifiable_after_key_rotation(
    app_client,
    monkeypatch,
    tmp_path,
):
    from app.config import settings

    _enable_claim_audit(monkeypatch, tmp_path)
    old_key = settings.evidence_signing_key
    old_key_id = settings.evidence_signing_key_id
    headers = await _register(app_client, "audit-key-rotation")
    created = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "The source reports a cosmological constraint.",
            "source": {"kind": "arxiv", "value": "2503.14738"},
        },
        headers=headers,
    )
    pack_id = created.json()["evidence_pack"]["pack_id"]

    monkeypatch.setattr(settings, "evidence_signing_key", "new-evidence-key-32-bytes-minimum-2026")
    monkeypatch.setattr(settings, "evidence_signing_key_id", "evidence-2026-08")
    monkeypatch.setattr(
        settings,
        "evidence_verification_keys",
        json.dumps({old_key_id: old_key}),
    )
    verified = await app_client.post(
        "/api/research/evidence-packs/verify",
        json={"pack_id": pack_id},
        headers=headers,
    )
    assert verified.json()["valid"] is True
    assert verified.json()["key_id"] == old_key_id


async def test_unknown_registered_execution_becomes_capability_gap(
    app_client,
    monkeypatch,
    tmp_path,
):
    _enable_claim_audit(monkeypatch, tmp_path)
    headers = await _register(app_client, "audit-gap")
    response = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "The unregistered release constrains H0.",
            "source": {"kind": "arxiv", "value": "2501.12345"},
            "mode": "execute_registered",
            "dataset_hints": ["not_a_real_release"],
        },
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["scientific_verdict"] == "CAPABILITY_GAP"
    assert body["capability_gaps"][0]["gap_code"] == "dataset_not_registered"


async def test_server_owned_ready_job_can_support_exact_current_run_value(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    _enable_claim_audit(monkeypatch, tmp_path)
    headers = await _register(app_client, "audit-supported")
    profile = await app_client.get("/api/auth/me", headers=headers)
    owner_id = uuid.UUID(profile.json()["id"])
    now = datetime.now(timezone.utc)
    job = ResearchJob(
        job_id="ready-h0-job",
        user_id=owner_id,
        tool_name="run_cosmology_likelihood_chain",
        inputs_hash="a" * 64,
        args={"dataset_keys": ["test_bao"]},
        args_replayable=True,
        status="completed",
        result={
            "success": True,
            "analysis_status": "COMPLETED",
            "publication_ready": True,
            "__do_not_claim__": False,
            "claim_scope": "test_current_run",
            "parameters": {
                "H0": {"median": 70.0, "hdi_94": [69.0, 71.0]},
            },
            "datasets_used": [
                {
                    "key": "test_bao",
                    "display_name": "Test BAO fixture",
                    "version": "v1",
                    "execution_mode": "full_likelihood",
                }
            ],
            "chain_diagnostics": {"rhat": 1.0, "ess_bulk": 1000},
            "reproducibility": {
                "run_id": "run-ready-h0",
                "query_hash": "b" * 64,
                "random_seed": 7,
                "tool_version": "test",
            },
        },
        background_backend="celery",
        created_at=now,
        started_at=now,
        completed_at=now,
        updated_at=now,
    )
    job.attestation = build_research_job_attestation(
        job_id=job.job_id,
        owner_id=job.user_id,
        session_id=job.session_id,
        tool_name=job.tool_name,
        inputs_hash=job.inputs_hash,
        args=job.args,
        args_replayable=job.args_replayable,
        result=job.result,
        background_backend=job.background_backend,
        completed_at=job.completed_at,
    )
    db_session.add(job)
    await db_session.commit()

    response = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "H0 = 70 km/s/Mpc",
            "source": {"kind": "bibcode", "value": "2025ApJ...123..456A"},
            "evidence_input_refs": [job.job_id],
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["scientific_verdict"] == "SUPPORTED"
    assert body["normalized_claims"][0]["supporting_evidence_ids"] == [job.job_id]
    assert body["normalized_claims"][0]["parse_coverage"] == "complete"
    pack_download = await app_client.get(
        body["evidence_pack"]["download_url"],
        headers=headers,
    )
    with zipfile.ZipFile(io.BytesIO(pack_download.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        dataset_releases = manifest["dataset_releases"]
        assert any(
            item.get("key") == "test_bao" and item.get("version") == "v1"
            for item in dataset_releases
        )
        tool_record = manifest["tool_records"][0]
        assert tool_record["run_id"] == "run-ready-h0"
        assert tool_record["random_seed"] == 7
        assert tool_record["diagnostics"]["chain_diagnostics"] == {
            "rhat": 1.0,
            "ess_bulk": 1000,
        }
        assert tool_record["arguments"] == {"dataset_keys": ["test_bao"]}
        assert tool_record["arguments_redacted"] is False
        assert tool_record["scientific_outputs"]["parameters"]["H0"]["median"] == 70.0
        claim_path = manifest["claim_evidence_paths"]["claim-1"]
        assert claim_path["verdict"] == "SUPPORTED"
        assert claim_path["supported_claims"][0]["evidence_path"]
        report_head = archive.read("report.md").decode("utf-8").splitlines()[:8]
        assert report_head[0] == "# Claim Audit — SUPPORTED"
        assert any("NOT PEER REVIEWED" in line for line in report_head)

    compound = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "H0 = 70 km/s/Mpc, therefore dark energy is evolving.",
            "source": {"kind": "bibcode", "value": "2025ApJ...123..456A"},
            "evidence_input_refs": [job.job_id],
        },
        headers=headers,
    )
    assert compound.status_code == 201
    compound_body = compound.json()
    assert compound_body["scientific_verdict"] == "WITHHELD"
    assert compound_body["normalized_claims"][0]["parse_coverage"] == "unparsed_residual"

    unsafe_claims = {
        "H0 = 70 ± 1000 km/s/Mpc": "unparsed_residual",
        "H0 = 70 dimensionless": "unparsed_residual",
        "H0 = 70 km s^1 Mpc^1": "unparsed_residual",
        "w0 = -1 km/s/Mpc": "unparsed_residual",
        # An equality must match the point estimate, not merely fall in its HDI.
        "H0 = 69 km/s/Mpc": "complete",
    }
    for index, (claim_text, expected_coverage) in enumerate(unsafe_claims.items()):
        rejected = await app_client.post(
            "/api/research/claim-audits",
            json={
                "claim_text": claim_text,
                "source": {"kind": "doi", "value": f"10.0000/unsafe-{index}"},
                "evidence_input_refs": [job.job_id],
            },
            headers=headers,
        )
        assert rejected.status_code == 201
        rejected_body = rejected.json()
        assert rejected_body["scientific_verdict"] == "WITHHELD"
        assert rejected_body["normalized_claims"][0]["parse_coverage"] == expected_coverage


async def test_generic_audit_cannot_finalize_an_r3_registered_run(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app.services.workflow_registry_v2 import (
        UNION3_REPRODUCTION_WORKFLOW_ID,
        get_worker_execution_binding,
    )

    _enable_claim_audit(monkeypatch, tmp_path)
    headers = await _register(app_client, "audit-r3-result-gate")
    profile = await app_client.get("/api/auth/me", headers=headers)
    owner_id = uuid.UUID(profile.json()["id"])
    binding = get_worker_execution_binding(UNION3_REPRODUCTION_WORKFLOW_ID)
    now = datetime.now(timezone.utc)
    job = ResearchJob(
        job_id="formal-r3-generic-audit-job",
        user_id=owner_id,
        tool_name=binding["entrypoint_id"],
        workflow_key=binding["workflow_id"],
        workflow_id=binding["workflow_id"],
        workflow_version=binding["workflow_version"],
        registry_epoch=binding["registry_epoch"],
        registry_entry_hash=binding["registry_entry_hash"],
        entrypoint_id=binding["entrypoint_id"],
        runner_image_digest=binding["approved_worker_image_digest"],
        inputs_hash="7" * 64,
        args={},
        args_replayable=True,
        status="completed",
        result={
            "success": True,
            "analysis_status": "COMPLETED",
            "publication_ready": True,
            "__do_not_claim__": False,
            "parameters": {"H0": {"median": 70.0, "hdi_94": [69.0, 71.0]}},
            "datasets_used": [{"key": "union3", "version": "v1"}],
        },
        background_backend="https_worker",
        created_at=now,
        started_at=now,
        completed_at=now,
        updated_at=now,
    )
    formal_binding = {
        "workflow_id": job.workflow_id,
        "workflow_version": job.workflow_version,
        "registry_epoch": job.registry_epoch,
        "registry_entry_hash": job.registry_entry_hash,
        "entrypoint_id": job.entrypoint_id,
        "runner_image_digest": job.runner_image_digest,
    }
    job.attestation = build_research_job_attestation(
        job_id=job.job_id,
        owner_id=job.user_id,
        session_id=job.session_id,
        tool_name=job.tool_name,
        inputs_hash=job.inputs_hash,
        args=job.args,
        args_replayable=job.args_replayable,
        result=job.result,
        background_backend=job.background_backend,
        completed_at=job.completed_at,
        formal_workflow_binding=formal_binding,
    )
    db_session.add(job)
    await db_session.commit()

    response = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "H0 = 70 km/s/Mpc",
            "source": {"kind": "doi", "value": "10.0000/r3-result-gate"},
            "evidence_input_refs": [job.job_id],
        },
        headers=headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["scientific_verdict"] == "WITHHELD"
    assert body["normalized_claims"][0]["supporting_evidence_ids"] == []
    assert body["normalized_claims"][0]["withheld_reason"] == (
        "independent_recomputation_and_human_result_review_required"
    )
    downloaded = await app_client.get(
        body["evidence_pack"]["download_url"], headers=headers
    )
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        formal = manifest["tool_records"][0]["formal_workflow_binding"]
        assert formal["workflow_id"] == UNION3_REPRODUCTION_WORKFLOW_ID
        assert formal["risk_level"] == "R3"
        assert formal["result_finalizer_required"] is True


async def test_more_than_32_claim_units_is_rejected_before_execution(
    app_client,
    monkeypatch,
    tmp_path,
):
    _enable_claim_audit(monkeypatch, tmp_path)
    headers = await _register(app_client, "audit-too-many-claims")
    response = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "; ".join(
                ["H0 = 70 km/s/Mpc"] * 32
                + ["dark energy has been proven to evolve"]
            ),
            "source": {"kind": "doi", "value": "10.0000/too-many-claims"},
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert "more than 32" in response.json()["detail"]


async def test_forged_completed_job_is_rejected_before_audit_creation(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    _enable_claim_audit(monkeypatch, tmp_path)
    headers = await _register(app_client, "audit-forged-job")
    profile = await app_client.get("/api/auth/me", headers=headers)
    owner_id = uuid.UUID(profile.json()["id"])
    now = datetime.now(timezone.utc)
    db_session.add(
        ResearchJob(
            job_id="forged-ready-job",
            user_id=owner_id,
            tool_name="run_cosmology_likelihood_chain",
            inputs_hash="f" * 64,
            args={},
            args_replayable=True,
            status="completed",
            result={
                "success": True,
                "publication_ready": True,
                "__do_not_claim__": False,
                "claim_scope": "forged",
                "H0": 70.0,
            },
            # Deliberately no server HMAC. Owner/status/result fields alone
            # are not a scientific authority.
            attestation=None,
            background_backend="celery",
            created_at=now,
            started_at=now,
            completed_at=now,
            updated_at=now,
        )
    )
    await db_session.commit()

    response = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "H0 = 70 km/s/Mpc",
            "source": {"kind": "doi", "value": "10.0000/forged"},
            "evidence_input_refs": ["forged-ready-job"],
        },
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert "server-signed" in response.json()["detail"]


async def test_conflicting_signed_point_estimates_are_withheld_at_api_boundary(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    _enable_claim_audit(monkeypatch, tmp_path)
    headers = await _register(app_client, "audit-conflicting-values")
    profile = await app_client.get("/api/auth/me", headers=headers)
    owner_id = uuid.UUID(profile.json()["id"])
    now = datetime.now(timezone.utc)
    jobs = []
    for index, value in enumerate((70.0, 75.0), start=1):
        job = ResearchJob(
            job_id=f"conflicting-h0-{index}",
            user_id=owner_id,
            tool_name="run_cosmology_likelihood_chain",
            inputs_hash=str(index) * 64,
            args={"dataset_keys": [f"test_bao_{index}"]},
            args_replayable=True,
            status="completed",
            result={
                "success": True,
                "analysis_status": "COMPLETED",
                "publication_ready": True,
                "__do_not_claim__": False,
                "parameters": {"H0": {"median": value}},
                "datasets_used": [{"key": f"test_bao_{index}", "version": "v1"}],
            },
            background_backend="celery",
            created_at=now,
            started_at=now,
            completed_at=now,
            updated_at=now,
        )
        job.attestation = build_research_job_attestation(
            job_id=job.job_id,
            owner_id=job.user_id,
            session_id=job.session_id,
            tool_name=job.tool_name,
            inputs_hash=job.inputs_hash,
            args=job.args,
            args_replayable=job.args_replayable,
            result=job.result,
            background_backend=job.background_backend,
            completed_at=job.completed_at,
        )
        jobs.append(job)
        db_session.add(job)
    await db_session.commit()

    response = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "H0 = 70 km/s/Mpc",
            "source": {"kind": "doi", "value": "10.0000/conflicting-values"},
            "evidence_input_refs": [job.job_id for job in jobs],
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["scientific_verdict"] == "WITHHELD"
    assert body["normalized_claims"][0]["verdict"] == "WITHHELD"
    assert body["normalized_claims"][0]["supporting_evidence_ids"] == []
    numeric_claims = [
        claim
        for claim in body["fact_check_report"]["claim_reports"]["claim-1"]["claims"]
        if claim["kind"] == "numeric"
    ]
    assert numeric_claims[0]["status"] == "contradicted"
    conflict_pack = await app_client.get(
        body["evidence_pack"]["download_url"], headers=headers
    )
    with zipfile.ZipFile(io.BytesIO(conflict_pack.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    claim_path = manifest["claim_evidence_paths"]["claim-1"]
    assert claim_path["verdict"] == "WITHHELD"
    assert claim_path["supported_claims"] == []
    assert claim_path["unsupported_claims"][0]["status"] == "contradicted"


async def test_claim_audit_and_pack_are_owner_isolated(
    app_client,
    monkeypatch,
    tmp_path,
):
    _enable_claim_audit(monkeypatch, tmp_path)
    owner = await _register(app_client, "audit-owner")
    stranger = await _register(app_client, "audit-stranger")
    created = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "H0 = 70",
            "source": {"kind": "doi", "value": "10.0000/private"},
        },
        headers=owner,
    )
    body = created.json()
    audit_id = body["audit_id"]
    pack_id = body["evidence_pack"]["pack_id"]
    assert (
        await app_client.get(
            f"/api/research/claim-audits/{audit_id}", headers=stranger
        )
    ).status_code == 404
    assert (
        await app_client.get(
            f"/api/research/evidence-packs/{pack_id}/download",
            headers=stranger,
        )
    ).status_code == 404


async def test_running_evidence_ref_is_not_cached_as_terminal_audit(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app.models.claim_audit_records import ClaimAudit

    _enable_claim_audit(monkeypatch, tmp_path)
    headers = await _register(app_client, "audit-pending-ref")
    profile = await app_client.get("/api/auth/me", headers=headers)
    owner_id = uuid.UUID(profile.json()["id"])
    now = datetime.now(timezone.utc)
    job = ResearchJob(
        job_id="pending-evidence-job",
        user_id=owner_id,
        tool_name="run_cosmology_likelihood_chain",
        inputs_hash="9" * 64,
        args={"dataset_keys": ["test_bao"]},
        args_replayable=True,
        status="running",
        result=None,
        attestation=None,
        background_backend="celery",
        created_at=now,
        started_at=now,
        updated_at=now,
    )
    db_session.add(job)
    await db_session.commit()
    request = {
        "claim_text": "H0 = 70 km/s/Mpc",
        "source": {"kind": "doi", "value": "10.0000/pending-ref"},
        "evidence_input_refs": [job.job_id],
    }
    pending = await app_client.post(
        "/api/research/claim-audits",
        json=request,
        headers=headers,
    )
    assert pending.status_code == 409
    assert await db_session.scalar(select(ClaimAudit.id)) is None

    job.status = "completed"
    job.result = {
        "success": True,
        "analysis_status": "COMPLETED",
        "publication_ready": True,
        "__do_not_claim__": False,
        "parameters": {"H0": {"median": 70.0, "hdi_94": [69.0, 71.0]}},
        "datasets_used": [{"key": "test_bao", "version": "v1"}],
    }
    job.completed_at = datetime.now(timezone.utc)
    job.attestation = build_research_job_attestation(
        job_id=job.job_id,
        owner_id=job.user_id,
        session_id=job.session_id,
        tool_name=job.tool_name,
        inputs_hash=job.inputs_hash,
        args=job.args,
        args_replayable=job.args_replayable,
        result=job.result,
        background_backend=job.background_backend,
        completed_at=job.completed_at,
    )
    await db_session.commit()
    completed = await app_client.post(
        "/api/research/claim-audits",
        json=request,
        headers=headers,
    )
    assert completed.status_code == 201, completed.text
    assert completed.json()["lifecycle_status"] == "COMPLETED"
    assert completed.json()["scientific_verdict"] == "SUPPORTED"


@pytest.mark.parametrize(
    "result",
    [70.0, ["client-shaped", "evidence"]],
    ids=["scalar", "list"],
)
async def test_signed_non_structured_evidence_ref_is_rejected_before_audit(
    result,
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app.models.claim_audit_records import ClaimAudit

    _enable_claim_audit(monkeypatch, tmp_path)
    headers = await _register(
        app_client,
        f"audit-non-structured-{type(result).__name__}",
    )
    profile = await app_client.get("/api/auth/me", headers=headers)
    owner_id = uuid.UUID(profile.json()["id"])
    now = datetime.now(timezone.utc)
    job = ResearchJob(
        job_id=f"non-structured-{type(result).__name__}",
        user_id=owner_id,
        tool_name="run_cosmology_likelihood_chain",
        inputs_hash="8" * 64,
        args={"dataset_keys": ["test_bao"]},
        args_replayable=True,
        status="completed",
        result=result,
        background_backend="celery",
        created_at=now,
        started_at=now,
        completed_at=now,
        updated_at=now,
    )
    job.attestation = build_research_job_attestation(
        job_id=job.job_id,
        owner_id=job.user_id,
        session_id=job.session_id,
        tool_name=job.tool_name,
        inputs_hash=job.inputs_hash,
        args=job.args,
        args_replayable=job.args_replayable,
        result=job.result,
        background_backend=job.background_backend,
        completed_at=job.completed_at,
    )
    db_session.add(job)
    await db_session.commit()

    response = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "H0 = 70 km/s/Mpc",
            "source": {"kind": "doi", "value": "10.0000/non-structured"},
            "evidence_input_refs": [job.job_id],
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert "structured result" in response.json()["detail"]
    assert await db_session.scalar(select(ClaimAudit.id)) is None


async def test_execute_registered_runs_fixed_desi_dr2_workflow_and_reports_missing_mirror(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app.services import ai_tools_cosmology

    _enable_claim_audit(monkeypatch, tmp_path)
    calls: list[tuple[str, dict]] = []

    async def fake_dispatch(tool_name, tool_input, *_args, **_kwargs):
        calls.append((tool_name, tool_input))
        return _missing_desi_matrix_result()

    monkeypatch.setattr(ai_tools_cosmology, "dispatch_cosmology", fake_dispatch)
    headers = await _register(app_client, "audit-execute-registered")
    response = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "DESI DR2 proves evolving dark energy.",
            "source": {"kind": "doi", "value": "10.1103/tr6y-kpc6"},
            "mode": "execute_registered",
            "dataset_hints": ["desi_dr2_bao"],
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["lifecycle_status"] == "COMPLETED"
    assert body["scientific_verdict"] == "CAPABILITY_GAP"
    assert body["capability_gaps"][0]["gap_code"] == "workflow_suspended"
    assert body["child_job_ids"] == []
    assert body["evidence_record_ids"] == []
    assert calls == []


async def test_execute_registered_without_exact_registry_selection_fails_closed(
    app_client,
    monkeypatch,
    tmp_path,
):
    _enable_claim_audit(monkeypatch, tmp_path)
    headers = await _register(app_client, "audit-execute-no-selection")
    response = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "Run a registered cosmology workflow.",
            "source": {"kind": "arxiv", "value": "2503.14738"},
            "mode": "execute_registered",
        },
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["scientific_verdict"] == "CAPABILITY_GAP"
    assert body["capability_gaps"][0]["gap_code"] == "registered_workflow_not_available"
    assert body["child_job_ids"] == []


async def test_suspended_desi_workflow_cannot_enter_formal_pack(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app.services import ai_tools_cosmology

    _enable_claim_audit(monkeypatch, tmp_path)

    async def ready_dispatch(*_args, **_kwargs):
        return _ready_desi_matrix_result()

    monkeypatch.setattr(
        ai_tools_cosmology,
        "dispatch_cosmology",
        ready_dispatch,
    )
    headers = await _register(app_client, "audit-desi-pack")
    profile = await app_client.get("/api/auth/me", headers=headers)
    owner_id = uuid.UUID(profile.json()["id"])
    now = datetime.now(timezone.utc)
    ordinary = ResearchJob(
        job_id="ordinary-job-before-matrix",
        user_id=owner_id,
        tool_name="run_cosmology_likelihood_chain",
        inputs_hash="8" * 64,
        args={"dataset_keys": ["test_bao"], "model": "lcdm"},
        args_replayable=True,
        status="completed",
        result={
            "success": True,
            "analysis_status": "COMPLETED",
            "publication_ready": True,
            "parameters": {"H0": {"median": 70.0}},
            "datasets_used": [{"key": "test_bao", "version": "v1"}],
        },
        background_backend="celery",
        created_at=now,
        started_at=now,
        completed_at=now,
        updated_at=now,
    )
    ordinary.attestation = build_research_job_attestation(
        job_id=ordinary.job_id,
        owner_id=ordinary.user_id,
        session_id=ordinary.session_id,
        tool_name=ordinary.tool_name,
        inputs_hash=ordinary.inputs_hash,
        args=ordinary.args,
        args_replayable=ordinary.args_replayable,
        result=ordinary.result,
        background_backend=ordinary.background_backend,
        completed_at=ordinary.completed_at,
    )
    db_session.add(ordinary)
    await db_session.commit()
    response = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "DESI DR2 proves evolving dark energy.",
            "source": {"kind": "doi", "value": "10.1103/tr6y-kpc6"},
            "mode": "execute_registered",
            "dataset_hints": ["desi_dr2_bao"],
            "evidence_input_refs": [ordinary.job_id],
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["lifecycle_status"] == "COMPLETED"
    assert body["scientific_verdict"] == "CAPABILITY_GAP"
    assert body["capability_gaps"][0]["gap_code"] == "workflow_suspended"
    assert body["child_job_ids"] == []


async def test_retry_reuses_signed_child_and_preserves_capability_gap(
    app_client,
    monkeypatch,
    tmp_path,
):
    from app import storage
    from app.services import ai_tools_cosmology

    _enable_claim_audit(monkeypatch, tmp_path)
    dispatch_calls = 0

    async def fake_dispatch(*_args, **_kwargs):
        nonlocal dispatch_calls
        dispatch_calls += 1
        return _missing_desi_matrix_result()

    original_upload = storage.upload_fits
    upload_calls = 0

    def fail_first_upload(*args, **kwargs):
        nonlocal upload_calls
        upload_calls += 1
        if upload_calls == 1:
            raise OSError("temporary object store outage")
        return original_upload(*args, **kwargs)

    monkeypatch.setattr(ai_tools_cosmology, "dispatch_cosmology", fake_dispatch)
    monkeypatch.setattr(storage, "upload_fits", fail_first_upload)
    headers = await _register(app_client, "audit-retry-gap")
    created = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "Run the official DESI DR2 matrix.",
            "source": {"kind": "doi", "value": "10.0000/retry-gap"},
            "mode": "execute_registered",
            "dataset_hints": ["desi_dr2_bao"],
        },
        headers=headers,
    )
    first = created.json()
    assert first["lifecycle_status"] == "FAILED_RETRYABLE"
    assert dispatch_calls == 0
    retried = await app_client.post(
        f"/api/research/claim-audits/{first['audit_id']}/retry",
        headers=headers,
    )
    assert retried.status_code == 200, retried.text
    body = retried.json()
    assert body["lifecycle_status"] == "COMPLETED"
    assert body["scientific_verdict"] == "CAPABILITY_GAP"
    assert body["capability_gaps"][0]["gap_code"] == "workflow_suspended"
    assert dispatch_calls == 0


async def test_suspended_registered_workflow_does_not_dispatch_provider(
    app_client,
    monkeypatch,
    tmp_path,
):
    from app.services import ai_tools_cosmology

    _enable_claim_audit(monkeypatch, tmp_path)
    headers = await _register(app_client, "audit-registered-failures")

    async def timed_out(*_args, **_kwargs):
        raise TimeoutError("official archive timed out")

    monkeypatch.setattr(ai_tools_cosmology, "dispatch_cosmology", timed_out)
    timeout_response = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "Run the registered DR2 matrix.",
            "source": {"kind": "doi", "value": "10.0000/timeout"},
            "mode": "execute_registered",
            "dataset_hints": ["desi_dr2_bao"],
        },
        headers=headers,
    )
    assert timeout_response.json()["lifecycle_status"] == "COMPLETED"
    assert timeout_response.json()["scientific_verdict"] == "CAPABILITY_GAP"
    assert timeout_response.json()["capability_gaps"][0]["gap_code"] == (
        "workflow_suspended"
    )


async def test_queued_audit_must_be_cancelled_before_delete(
    app_client,
    monkeypatch,
    tmp_path,
):
    from app.api import claim_audits
    from app.config import settings

    _enable_claim_audit(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "claim_audit_execution_mode", "celery")
    monkeypatch.setattr(claim_audits, "_dispatch_claim_audit", lambda _audit_id: None)
    headers = await _register(app_client, "audit-cancel-delete")
    created = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "Queue this claim.",
            "source": {"kind": "doi", "value": "10.0000/queued"},
        },
        headers=headers,
    )
    audit_id = created.json()["audit_id"]
    assert created.json()["lifecycle_status"] == "QUEUED"
    assert (
        await app_client.delete(
            f"/api/research/claim-audits/{audit_id}", headers=headers
        )
    ).status_code == 409
    cancelled = await app_client.post(
        f"/api/research/claim-audits/{audit_id}/cancel", headers=headers
    )
    assert cancelled.json()["lifecycle_status"] == "CANCELLED"
    assert (
        await app_client.delete(
            f"/api/research/claim-audits/{audit_id}", headers=headers
        )
    ).status_code == 204


async def test_download_rejects_object_replaced_after_finalization(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app.storage import download_fits, upload_fits

    _enable_claim_audit(monkeypatch, tmp_path)
    headers = await _register(app_client, "audit-object-swap")
    created = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "Keep this pack private and immutable.",
            "source": {"kind": "doi", "value": "10.0000/object-swap"},
        },
        headers=headers,
    )
    pack_id = uuid.UUID(created.json()["evidence_pack"]["pack_id"])
    pack = await db_session.scalar(select(EvidencePack).where(EvidencePack.id == pack_id))
    assert pack is not None
    original = download_fits(pack.artifact_ref)
    upload_fits(pack.artifact_ref, original + b"mutated")

    downloaded = await app_client.get(
        f"/api/research/evidence-packs/{pack_id}/download",
        headers=headers,
    )
    assert downloaded.status_code == 409


async def test_pack_upload_failure_is_retryable_and_leaves_no_hidden_row(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app import storage

    _enable_claim_audit(monkeypatch, tmp_path)
    monkeypatch.setattr(
        storage,
        "upload_fits",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("storage down")),
    )
    headers = await _register(app_client, "audit-upload-failure")
    response = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "H0 = 70 km/s/Mpc",
            "source": {"kind": "doi", "value": "10.0000/upload-failure"},
        },
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["lifecycle_status"] == "FAILED_RETRYABLE"
    assert response.json()["evidence_pack"] is None
    assert await db_session.scalar(select(EvidencePack)) is None


async def test_retry_refreshes_old_upload_lease_before_reusing_pack_row(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app.models.claim_audit_records import ClaimAudit

    _enable_claim_audit(monkeypatch, tmp_path)
    headers = await _register(app_client, "audit-upload-lease")
    created = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "H0 = 70 km/s/Mpc",
            "source": {"kind": "doi", "value": "10.0000/upload-lease"},
        },
        headers=headers,
    )
    audit_id = uuid.UUID(created.json()["audit_id"])
    audit = await db_session.get(ClaimAudit, audit_id)
    pack = await db_session.scalar(
        select(EvidencePack).where(EvidencePack.audit_id == audit_id)
    )
    assert audit is not None and pack is not None
    old_lease = datetime.now(timezone.utc) - timedelta(days=1)
    audit.lifecycle_status = "FAILED_RETRYABLE"
    audit.scientific_verdict = None
    pack.status = "UPLOADING"
    pack.finalized_at = None
    pack.upload_started_at = old_lease
    await db_session.commit()

    retried = await app_client.post(
        f"/api/research/claim-audits/{audit_id}/retry",
        headers=headers,
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["lifecycle_status"] == "COMPLETED"
    await db_session.refresh(pack)
    assert pack.status == "FINALIZED"
    lease = pack.upload_started_at
    if lease.tzinfo is None:
        lease = lease.replace(tzinfo=timezone.utc)
    assert lease > old_lease


async def test_stale_worker_recovery_fails_generated_child_then_allows_delete(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app.api import claim_audits
    from app.config import settings
    from app.models import database
    from app.models.claim_audit_records import ClaimAudit
    from app.tasks.claim_audit_tasks import _reconcile_stale

    _enable_claim_audit(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "claim_audit_execution_mode", "celery")
    monkeypatch.setattr(claim_audits, "_dispatch_claim_audit", lambda _audit_id: None)
    headers = await _register(app_client, "audit-stale-worker")
    profile = await app_client.get("/api/auth/me", headers=headers)
    owner_id = uuid.UUID(profile.json()["id"])
    created = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "Recover this interrupted workflow.",
            "source": {"kind": "doi", "value": "10.0000/stale-worker"},
        },
        headers=headers,
    )
    audit_id = uuid.UUID(created.json()["audit_id"])
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    audit = await db_session.get(ClaimAudit, audit_id)
    assert audit is not None
    audit.lifecycle_status = "RUNNING"
    audit.started_at = old
    audit.child_job_ids = ["stale-generated-child"]
    db_session.add(ResearchJob(
        job_id="stale-generated-child",
        user_id=owner_id,
        tool_name="run_dark_energy_evidence_matrix",
        inputs_hash="c" * 64,
        args={},
        args_replayable=True,
        status="running",
        background_backend="claim_audit",
        created_at=old,
        started_at=old,
    ))
    await db_session.commit()

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", session_factory)
    assert await _reconcile_stale() >= 1
    await db_session.refresh(audit)
    child = await db_session.get(ResearchJob, "stale-generated-child")
    await db_session.refresh(child)
    assert audit.lifecycle_status == "FAILED_RETRYABLE"
    assert child.status == "failed"

    deleted = await app_client.delete(
        f"/api/research/claim-audits/{audit_id}", headers=headers
    )
    assert deleted.status_code == 204, deleted.text


async def test_cancel_stops_generated_child_and_stale_fallback_does_not_revive_it(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app.api import claim_audits
    from app.config import settings
    from app.models import database
    from app.models.claim_audit_records import ClaimAudit
    from app.tasks.claim_audit_tasks import _reconcile_stale

    _enable_claim_audit(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "claim_audit_execution_mode", "celery")
    monkeypatch.setattr(claim_audits, "_dispatch_claim_audit", lambda _audit_id: None)
    headers = await _register(app_client, "audit-cancel-child")
    profile = await app_client.get("/api/auth/me", headers=headers)
    owner_id = uuid.UUID(profile.json()["id"])
    created = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "Cancel this generated workflow.",
            "source": {"kind": "doi", "value": "10.0000/cancel-child"},
        },
        headers=headers,
    )
    audit_id = uuid.UUID(created.json()["audit_id"])
    audit = await db_session.get(ClaimAudit, audit_id)
    assert audit is not None
    audit.lifecycle_status = "RUNNING"
    audit.started_at = datetime.now(timezone.utc)
    audit.child_job_ids = ["cancelled-generated-child"]
    child = ResearchJob(
        job_id="cancelled-generated-child",
        user_id=owner_id,
        tool_name="run_dark_energy_evidence_matrix",
        inputs_hash="d" * 64,
        args={},
        args_replayable=True,
        status="running",
        background_backend="claim_audit",
        created_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(child)
    await db_session.commit()

    cancelled = await app_client.post(
        f"/api/research/claim-audits/{audit_id}/cancel", headers=headers
    )
    assert cancelled.status_code == 200
    await db_session.refresh(child)
    assert child.status == "cancelled"

    # Simulate an older worker-loss state predating the atomic cancel fix.
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    child.status = "running"
    child.completed_at = None
    audit.updated_at = old
    await db_session.commit()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", session_factory)
    assert await _reconcile_stale() >= 1
    await db_session.refresh(child)
    assert child.status == "cancelled"

    deleted = await app_client.delete(
        f"/api/research/claim-audits/{audit_id}", headers=headers
    )
    assert deleted.status_code == 204, deleted.text


async def test_active_worker_lease_rejects_duplicate_delivery(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app.api import claim_audits
    from app.config import settings
    from app.models.claim_audit_records import ClaimAudit
    from app.services.claim_audit_service import process_claim_audit

    _enable_claim_audit(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "claim_audit_execution_mode", "celery")
    monkeypatch.setattr(claim_audits, "_dispatch_claim_audit", lambda _audit_id: None)
    headers = await _register(app_client, "audit-live-lease")
    created = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "Keep this task single-owner.",
            "source": {"kind": "doi", "value": "10.0000/live-lease"},
        },
        headers=headers,
    )
    audit = await db_session.get(ClaimAudit, uuid.UUID(created.json()["audit_id"]))
    assert audit is not None
    audit.lifecycle_status = "RUNNING"
    audit.worker_lease_id = "active-worker"
    audit.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    audit.attempt_count = 1
    await db_session.commit()

    result = await process_claim_audit(
        db_session,
        audit,
        worker_lease_id="duplicate-worker",
    )
    assert result.lifecycle_status == "RUNNING"
    assert result.worker_lease_id == "active-worker"
    assert result.attempt_count == 1


async def test_expired_worker_lease_is_taken_over_and_completed(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app.api import claim_audits
    from app.config import settings
    from app.models.claim_audit_records import ClaimAudit
    from app.services.claim_audit_service import process_claim_audit

    _enable_claim_audit(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "claim_audit_execution_mode", "celery")
    monkeypatch.setattr(claim_audits, "_dispatch_claim_audit", lambda _audit_id: None)
    headers = await _register(app_client, "audit-expired-lease")
    created = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "Recover this expired task.",
            "source": {"kind": "doi", "value": "10.0000/expired-lease"},
        },
        headers=headers,
    )
    audit = await db_session.get(ClaimAudit, uuid.UUID(created.json()["audit_id"]))
    assert audit is not None
    audit.lifecycle_status = "RUNNING"
    audit.worker_lease_id = "dead-worker"
    audit.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    audit.attempt_count = 1
    await db_session.commit()

    result = await process_claim_audit(
        db_session,
        audit,
        worker_lease_id="replacement-worker",
    )
    assert result.lifecycle_status == "COMPLETED"
    assert result.worker_lease_id is None
    assert result.lease_expires_at is None
    assert result.attempt_count == 2


async def test_stale_worker_cannot_write_terminal_failure_over_new_lease(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app.api import claim_audits
    from app.config import settings
    from app.models.claim_audit_records import ClaimAudit
    from app.services.claim_audit_service import _record_failure

    _enable_claim_audit(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "claim_audit_execution_mode", "celery")
    monkeypatch.setattr(claim_audits, "_dispatch_claim_audit", lambda _audit_id: None)
    headers = await _register(app_client, "audit-stale-terminal")
    created = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "Reject the stale finalizer.",
            "source": {"kind": "doi", "value": "10.0000/stale-finalizer"},
        },
        headers=headers,
    )
    audit = await db_session.get(ClaimAudit, uuid.UUID(created.json()["audit_id"]))
    assert audit is not None
    audit.lifecycle_status = "RUNNING"
    audit.worker_lease_id = "replacement-worker"
    audit.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    await db_session.commit()

    result = await _record_failure(
        db_session,
        audit_id=audit.id,
        lifecycle_status="FAILED_FINAL",
        exc=RuntimeError("old worker failed late"),
        worker_lease_id="old-worker",
    )
    assert result is not None
    assert result.lifecycle_status == "RUNNING"
    assert result.worker_lease_id == "replacement-worker"
    assert result.error is None


@pytest.mark.parametrize("upload_lease_id", ["active-worker", "older-worker"])
async def test_reconciler_never_deletes_upload_while_parent_lease_is_live(
    upload_lease_id,
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app import storage
    from app.models import database
    from app.models.claim_audit_records import ClaimAudit
    from app.tasks.claim_audit_tasks import _reconcile_stale

    _enable_claim_audit(monkeypatch, tmp_path)
    headers = await _register(app_client, f"audit-live-upload-{upload_lease_id}")
    created = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "Keep the live upload.",
            "source": {"kind": "doi", "value": f"10.0000/{upload_lease_id}"},
        },
        headers=headers,
    )
    audit_id = uuid.UUID(created.json()["audit_id"])
    audit = await db_session.get(ClaimAudit, audit_id)
    pack = await db_session.scalar(
        select(EvidencePack).where(EvidencePack.audit_id == audit_id)
    )
    assert audit is not None and pack is not None
    audit.lifecycle_status = "RUNNING"
    audit.worker_lease_id = "active-worker"
    audit.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    pack.status = "UPLOADING"
    pack.upload_lease_id = upload_lease_id
    pack.upload_started_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    pack.finalized_at = None
    await db_session.commit()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", session_factory)
    deletes: list[str] = []
    monkeypatch.setattr(storage, "delete_fits", lambda ref: deletes.append(ref))

    assert await _reconcile_stale() == 0
    assert deletes == []
    assert await db_session.scalar(
        select(EvidencePack.id).where(EvidencePack.audit_id == audit_id)
    ) == pack.id


async def test_reconciler_cleans_expired_upload_and_parent_lease(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app.models import database
    from app.models.claim_audit_records import ClaimAudit
    from app.tasks.claim_audit_tasks import _reconcile_stale

    _enable_claim_audit(monkeypatch, tmp_path)
    headers = await _register(app_client, "audit-expired-upload")
    created = await app_client.post(
        "/api/research/claim-audits",
        json={
            "claim_text": "Clean the expired upload.",
            "source": {"kind": "doi", "value": "10.0000/expired-upload"},
        },
        headers=headers,
    )
    audit_id = uuid.UUID(created.json()["audit_id"])
    audit = await db_session.get(ClaimAudit, audit_id)
    pack = await db_session.scalar(
        select(EvidencePack).where(EvidencePack.audit_id == audit_id)
    )
    assert audit is not None and pack is not None
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    audit.lifecycle_status = "RUNNING"
    audit.worker_lease_id = "expired-worker"
    audit.lease_expires_at = old
    pack.status = "UPLOADING"
    pack.upload_lease_id = "expired-worker"
    pack.upload_started_at = old
    pack.finalized_at = None
    await db_session.commit()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", session_factory)

    assert await _reconcile_stale() >= 1
    await db_session.refresh(audit)
    assert audit.lifecycle_status == "FAILED_RETRYABLE"
    assert audit.worker_lease_id is None
    assert audit.lease_expires_at is None
    assert await db_session.scalar(
        select(EvidencePack.id).where(EvidencePack.audit_id == audit_id)
    ) is None

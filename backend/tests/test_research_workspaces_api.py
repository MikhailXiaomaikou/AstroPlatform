"""HTTP contract tests for research workspaces, sources, and reviews."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.claim_audits import router as claim_audit_router
from app.api.research_workspaces import router
from app.auth import get_current_user
from app.models.claim_audit_records import ClaimAudit
from app.models.database import get_db
from app.models.schemas import User
from app.services.union3_reader import (
    UNION3_ARXIV_ID,
    UNION3_SOURCE_PROFILE_KEY,
    set_union3_snapshot_loader,
)
from app.services.research_workspace_service import (
    ingest_union3_source,
    process_queued_union3_source,
)
from tests.union3_source_test_support import registered_union3_snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "union3_2311_12098v4_table9.txt"


def _user(username: str) -> User:
    return User(
        id=uuid.uuid4(),
        username=username,
        email=f"{username}@example.test",
        password_hash="not-used-in-this-test",
        subscription_tier="solo",
    )


async def test_workspace_source_and_independent_review_http_contract(
    db_session,
    monkeypatch,
):
    owner = _user("http-owner")
    reviewer = _user("http-reviewer")
    outsider = _user("http-outsider")
    db_session.add_all([owner, reviewer, outsider])
    await db_session.commit()
    monkeypatch.setenv("SCIENTIFIC_REVIEWER_USERNAMES", reviewer.username)
    monkeypatch.setattr(
        "app.tasks.union3_source_tasks.enqueue_union3_source",
        lambda _source_document_id: None,
    )
    from app.config import settings

    monkeypatch.setattr(settings, "research_workspace_enabled", True)
    monkeypatch.setattr(settings, "arxiv_reader_enabled", True)

    app = FastAPI()
    app.include_router(router)
    app.include_router(claim_audit_router)
    state = {"user": owner}

    async def _db_override():
        yield db_session

    async def _user_override():
        return state["user"]

    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_current_user] = _user_override
    set_union3_snapshot_loader(
        lambda _identifier: registered_union3_snapshot(
            FIXTURE.read_text(encoding="utf-8")
        )
    )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            workspace_response = await client.post(
                "/api/research/workspaces",
                json={"title": "Union3 HTTP workspace"},
            )
            assert workspace_response.status_code == 201, workspace_response.text
            workspace_id = workspace_response.json()["workspace_id"]

            client_authored_source = await client.post(
                f"/api/research/workspaces/{workspace_id}/sources",
                json={
                    "source_profile_key": UNION3_SOURCE_PROFILE_KEY,
                    "identifier": UNION3_ARXIV_ID,
                    "pdf_text": "client-authored evidence is forbidden",
                },
            )
            assert client_authored_source.status_code == 422

            source_response = await client.post(
                f"/api/research/workspaces/{workspace_id}/sources",
                json={
                    "source_profile_key": UNION3_SOURCE_PROFILE_KEY,
                    "identifier": UNION3_ARXIV_ID,
                },
            )
            assert source_response.status_code == 202, source_response.text
            source = source_response.json()
            assert source["version"] == 1
            assert source["lifecycle_status"] == "QUEUED"
            assert source["extraction"] is None

            await process_queued_union3_source(
                db_session,
                source_document_id=uuid.UUID(source["source_document_id"]),
                trusted_snapshot=registered_union3_snapshot(
                    FIXTURE.read_text(encoding="utf-8")
                ),
            )
            completed_source = await client.get(
                f"/api/research/workspaces/{workspace_id}/sources/"
                f"{source['source_document_id']}"
            )
            assert completed_source.status_code == 200, completed_source.text
            source = completed_source.json()
            assert source["lifecycle_status"] == "COMPLETED"
            extraction = source["extraction"]
            candidate = extraction["extraction_payload"]["candidates"][0]

            listed_sources = await client.get(
                f"/api/research/workspaces/{workspace_id}/sources"
            )
            assert listed_sources.status_code == 200, listed_sources.text
            listed = listed_sources.json()["items"]
            assert len(listed) == 1
            assert listed[0]["source_document_id"] == source["source_document_id"]
            assert (
                listed[0]["extraction"]["source_extraction_id"]
                == (extraction["source_extraction_id"])
            )
            assert listed[0]["extraction"]["extraction_payload"]["candidates"] == [
                candidate
            ]

            audit = ClaimAudit(
                id=uuid.uuid4(),
                user_id=owner.id,
                request_hash=uuid.uuid4().hex,
                lifecycle_status="COMPLETED",
                scientific_verdict="WITHHELD",
                mode="audit_only",
                claim_text=candidate["claim_text"],
                source_kind="arxiv",
                source_value=UNION3_ARXIV_ID,
                workspace_id=uuid.UUID(workspace_id),
                source_document_id=uuid.UUID(source["source_document_id"]),
                source_extraction_id=uuid.UUID(extraction["source_extraction_id"]),
                atomic_claim=candidate,
                claim_hash=candidate["claim_hash"],
                normalized_claims=[candidate],
                machine_support_eligible=True,
                review_status="PENDING",
            )
            db_session.add(audit)
            await db_session.commit()

            state["user"] = outsider
            hidden_workspace = await client.get(
                f"/api/research/workspaces/{workspace_id}"
            )
            assert hidden_workspace.status_code == 404
            hidden_source = await client.get(
                f"/api/research/workspaces/{workspace_id}/sources/"
                f"{source['source_document_id']}"
            )
            assert hidden_source.status_code == 404

            state["user"] = reviewer
            review_queue = await client.get("/api/research/review-queue")
            assert review_queue.status_code == 200, review_queue.text
            queued_audit = review_queue.json()["items"][0]
            assert queued_audit["review_binding"] == {
                "source_document_id": source["source_document_id"],
                "source_extraction_id": extraction["source_extraction_id"],
                "candidate_id": candidate["candidate_id"],
                "claim_hash": candidate["claim_hash"],
                "source_hash": source["source_document_hash"],
                "anchor_ids": candidate["source_anchor_ids"],
            }
            assert queued_audit["review_evidence"]["anchors"]
            assert queued_audit["review_evidence"]["canonical_identifier"] == (
                UNION3_ARXIV_ID
            )

            review_response = await client.post(
                f"/api/research/workspaces/{workspace_id}/claim-audits/"
                f"{audit.id}/reviews",
                json={
                    "source_document_id": source["source_document_id"],
                    "source_extraction_id": extraction["source_extraction_id"],
                    "candidate_id": candidate["candidate_id"],
                    "claim_hash": candidate["claim_hash"],
                    "source_hash": source["source_document_hash"],
                    "anchor_ids": candidate["source_anchor_ids"],
                    "decision": "APPROVED",
                    "comment": "Independent source and claim check passed.",
                },
            )
            assert review_response.status_code == 201, review_response.text
            review = review_response.json()
            assert review["review_scope"] == "scientific_claim_review"
            assert review["supports_finalization"] is True
            assert review["reviewer_pseudonym"].startswith("reviewer:")
            assert "reviewer_user_id" not in review
            assert reviewer.username not in str(review)
            assert "scientific_verdict" not in review

            state["user"] = outsider
            hidden_reviews = await client.get(
                f"/api/research/workspaces/{workspace_id}/claim-audits/"
                f"{audit.id}/reviews"
            )
            assert hidden_reviews.status_code == 404

            state["user"] = owner
            owner_reviews = await client.get(
                f"/api/research/workspaces/{workspace_id}/claim-audits/"
                f"{audit.id}/reviews"
            )
            assert owner_reviews.status_code == 200
            assert owner_reviews.json()["items"] == [review]
    finally:
        set_union3_snapshot_loader(None)


async def test_workspace_audit_accepts_only_registered_candidate_selection(
    db_session,
    monkeypatch,
):
    owner = _user("workspace-audit-owner")
    db_session.add(owner)
    await db_session.commit()
    monkeypatch.setattr(
        "app.tasks.union3_source_tasks.enqueue_union3_source",
        lambda _source_document_id: None,
    )
    from app.config import settings

    for flag in (
        "research_workspace_enabled",
        "arxiv_reader_enabled",
        "claim_audit_enabled",
        "union3_reproduction_enabled",
        "local_science_worker_enabled",
        "evidence_pack_v2_enabled",
    ):
        monkeypatch.setattr(settings, flag, True)
    monkeypatch.setattr(settings, "claim_audit_max_active_per_user", 1)

    app = FastAPI()
    app.include_router(router)
    app.include_router(claim_audit_router)

    async def _db_override():
        yield db_session

    async def _user_override():
        return owner

    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_current_user] = _user_override
    set_union3_snapshot_loader(
        lambda _identifier: registered_union3_snapshot(
            FIXTURE.read_text(encoding="utf-8")
        )
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            workspace = (
                await client.post(
                    "/api/research/workspaces", json={"title": "Registered run"}
                )
            ).json()
            queued_source = await client.post(
                f"/api/research/workspaces/{workspace['workspace_id']}/sources",
                json={
                    "source_profile_key": UNION3_SOURCE_PROFILE_KEY,
                    "identifier": UNION3_ARXIV_ID,
                },
            )
            assert queued_source.status_code == 202, queued_source.text
            queued = queued_source.json()
            await process_queued_union3_source(
                db_session,
                source_document_id=uuid.UUID(queued["source_document_id"]),
                trusted_snapshot=registered_union3_snapshot(
                    FIXTURE.read_text(encoding="utf-8")
                ),
            )
            source_response = await client.get(
                f"/api/research/workspaces/{workspace['workspace_id']}/sources/"
                f"{queued['source_document_id']}"
            )
            assert source_response.status_code == 200, source_response.text
            source = source_response.json()
            candidate = source["extraction"]["extraction_payload"]["candidates"][0]
            payload = {
                "source_document_id": source["source_document_id"],
                "candidate_id": candidate["candidate_id"],
                "workflow_key": "union3_flat_lcdm_sn_only_v1",
            }
            forbidden = await client.post(
                f"/api/research/workspaces/{workspace['workspace_id']}/claim-audits",
                json={**payload, "publication_ready": True},
            )
            assert forbidden.status_code == 422

            created = await client.post(
                f"/api/research/workspaces/{workspace['workspace_id']}/claim-audits",
                json=payload,
            )
            assert created.status_code == 201, created.text
            audit = created.json()
            assert audit["lifecycle_status"] == "QUEUED"
            assert audit["scientific_verdict"] is None
            assert audit["reproduction_ready"] is False
            assert audit["publication_ready"] is False
            assert audit["child_job_ids"][0].startswith("union3-primary-")

            cancelled = await client.post(
                f"/api/research/claim-audits/{audit['audit_id']}/cancel"
            )
            assert cancelled.status_code == 200, cancelled.text
            revision_response = await client.post(
                f"/api/research/claim-audits/{audit['audit_id']}/revisions",
                json={},
            )
            assert revision_response.status_code == 201, revision_response.text
            revision = revision_response.json()
            assert revision["audit_id"] != audit["audit_id"]
            assert revision["supersedes_audit_id"] == audit["audit_id"]
            assert revision["lifecycle_status"] == "QUEUED"
            replay = await client.post(
                f"/api/research/claim-audits/{audit['audit_id']}/revisions",
                json={},
            )
            assert replay.status_code == 201, replay.text
            assert replay.json()["audit_id"] == revision["audit_id"]
            lineage_delete = await client.delete(
                f"/api/research/claim-audits/{audit['audit_id']}"
            )
            assert lineage_delete.status_code == 409, lineage_delete.text
            assert "newer revisions" in lineage_delete.json()["detail"]

            second_source, second_extraction = await ingest_union3_source(
                db_session,
                user_id=owner.id,
                workspace_id=uuid.UUID(workspace["workspace_id"]),
                source_profile_key=UNION3_SOURCE_PROFILE_KEY,
                identifier=UNION3_ARXIV_ID,
                trusted_snapshot=registered_union3_snapshot(
                    FIXTURE.read_text(encoding="utf-8")
                ),
                create_new_version=True,
            )
            second_candidate = second_extraction.extraction_payload["candidates"][0]
            limited = await client.post(
                f"/api/research/workspaces/{workspace['workspace_id']}/claim-audits",
                json={
                    "source_document_id": str(second_source.id),
                    "candidate_id": second_candidate["candidate_id"],
                    "workflow_key": "union3_flat_lcdm_sn_only_v1",
                },
            )
            assert limited.status_code == 429, limited.text

            archived = await client.delete(
                f"/api/research/workspaces/{workspace['workspace_id']}"
            )
            assert archived.status_code == 204, archived.text
            archived_create = await client.post(
                f"/api/research/workspaces/{workspace['workspace_id']}/claim-audits",
                json=payload,
            )
            assert archived_create.status_code == 409, archived_create.text

            listed = await client.get(
                f"/api/research/workspaces/{workspace['workspace_id']}/claim-audits"
            )
            assert listed.status_code == 200
            assert listed.json()["items"][0]["audit_id"] == revision["audit_id"]
    finally:
        set_union3_snapshot_loader(None)

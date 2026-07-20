"""Focused owner-isolation and immutable-review service tests."""

from __future__ import annotations

import copy
import uuid
from pathlib import Path

import pytest

from app.models.claim_audit_records import ClaimAudit
from app.models.schemas import User
from app.models.workspace_records import (
    ClaimAuditReview,
    SourceDocument,
    SourceExtraction,
)
from app.services.research_workspace_service import (
    WorkspaceConflictError,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
    WorkspaceServiceError,
    create_claim_audit_review,
    create_workspace,
    get_owned_source_document,
    get_owned_workspace,
    ingest_union3_source,
    list_claim_audit_reviews,
    process_queued_union3_source,
    queue_union3_source,
    reviewer_pseudonym,
    validate_registered_union3_source_receipt,
)
from app.services.union3_reader import (
    UNION3_ARXIV_ID,
    UNION3_PDF_SHA256,
    UNION3_SOURCE_PROFILE_KEY,
    Union3ReaderError,
)
from tests.union3_source_test_support import (
    TEST_METADATA_SHA256,
    registered_union3_snapshot,
)


FIXTURE = Path(__file__).parent / "fixtures" / "union3_2311_12098v4_table9.txt"


def _snapshot():
    return registered_union3_snapshot(FIXTURE.read_text(encoding="utf-8"))


def _user(username: str) -> User:
    return User(
        id=uuid.uuid4(),
        username=username,
        email=f"{username}@example.test",
        password_hash="not-used-in-this-test",
        subscription_tier="solo",
    )


async def test_source_documents_are_owner_isolated_idempotent_and_versioned(db_session):
    owner = _user("workspace-owner")
    outsider = _user("workspace-outsider")
    db_session.add_all([owner, outsider])
    await db_session.commit()

    workspace = await create_workspace(
        db_session,
        user_id=owner.id,
        title="Union3 reproduction",
    )
    with pytest.raises(WorkspaceNotFoundError):
        await get_owned_workspace(
            db_session,
            user_id=outsider.id,
            workspace_id=workspace.id,
        )

    document, extraction = await ingest_union3_source(
        db_session,
        user_id=owner.id,
        workspace_id=workspace.id,
        source_profile_key=UNION3_SOURCE_PROFILE_KEY,
        identifier=UNION3_ARXIV_ID,
        trusted_snapshot=_snapshot(),
    )
    assert isinstance(document, SourceDocument)
    assert isinstance(extraction, SourceExtraction)
    assert document.requested_identifier == UNION3_ARXIV_ID
    assert document.version == 1
    assert document.lifecycle_status == "COMPLETED"
    assert document.coverage_status == "UNION3_TABLE9_INTERVAL_READY"
    assert document.raw_artifact_hashes == {
        "atom_metadata": TEST_METADATA_SHA256,
        "source_tar": (
            "13d14b96ba72b0a548642c7d9e7c7cf6000de062cbc0dbe17bf30198ba1e1189"
        ),
        "pdf": UNION3_PDF_SHA256,
    }
    assert [item["role"] for item in document.raw_artifacts] == [
        "arxiv_atom_metadata",
        "version_pinned_source_tar",
        "authoritative_pdf",
    ]
    assert document.source_metadata["acquisition_order"] == [
        "arxiv_atom_metadata",
        "version_pinned_source_tar",
        "authoritative_pdf",
    ]
    assert document.source_metadata["authoritative_evidence_role"] == (
        "authoritative_pdf"
    )
    assert document.source_metadata["html_policy"] == "auxiliary_display_only"
    validate_registered_union3_source_receipt(
        raw_artifacts=document.raw_artifacts,
        raw_artifact_hashes=document.raw_artifact_hashes,
        source_metadata=document.source_metadata,
    )
    tampered_artifacts = copy.deepcopy(document.raw_artifacts)
    tampered_artifacts.reverse()
    with pytest.raises(Union3ReaderError):
        validate_registered_union3_source_receipt(
            raw_artifacts=tampered_artifacts,
            raw_artifact_hashes=document.raw_artifact_hashes,
            source_metadata=document.source_metadata,
        )
    tampered_hashes = dict(document.raw_artifact_hashes)
    tampered_hashes["source_tar"] = "0" * 64
    with pytest.raises(Union3ReaderError):
        validate_registered_union3_source_receipt(
            raw_artifacts=document.raw_artifacts,
            raw_artifact_hashes=tampered_hashes,
            source_metadata=document.source_metadata,
        )
    tampered_metadata = copy.deepcopy(document.source_metadata)
    tampered_metadata["html_policy"] = "authoritative"
    with pytest.raises(Union3ReaderError):
        validate_registered_union3_source_receipt(
            raw_artifacts=document.raw_artifacts,
            raw_artifact_hashes=document.raw_artifact_hashes,
            source_metadata=tampered_metadata,
        )
    assert extraction.source_document_id == document.id
    assert extraction.input_source_document_hash == document.source_document_hash

    same_document, same_extraction = await ingest_union3_source(
        db_session,
        user_id=owner.id,
        workspace_id=workspace.id,
        source_profile_key=UNION3_SOURCE_PROFILE_KEY,
        identifier=f"arXiv:{UNION3_ARXIV_ID}",
        trusted_snapshot=_snapshot(),
    )
    assert same_document.id == document.id
    assert same_extraction.id == extraction.id

    version_two, extraction_two = await ingest_union3_source(
        db_session,
        user_id=owner.id,
        workspace_id=workspace.id,
        source_profile_key=UNION3_SOURCE_PROFILE_KEY,
        identifier=UNION3_ARXIV_ID,
        trusted_snapshot=_snapshot(),
        create_new_version=True,
    )
    assert version_two.version == 2
    assert version_two.supersedes_source_document_id == document.id
    assert version_two.source_document_hash != document.source_document_hash
    assert extraction_two.input_source_document_hash == version_two.source_document_hash

    with pytest.raises(WorkspaceNotFoundError):
        await get_owned_source_document(
            db_session,
            user_id=outsider.id,
            workspace_id=workspace.id,
            source_document_id=document.id,
        )


async def test_source_acquisition_is_durable_and_idempotently_finalized(db_session):
    owner = _user("queued-source-owner")
    db_session.add(owner)
    await db_session.commit()
    workspace = await create_workspace(
        db_session,
        user_id=owner.id,
        title="Queued Union3 source",
    )

    queued, extraction = await queue_union3_source(
        db_session,
        user_id=owner.id,
        workspace_id=workspace.id,
        source_profile_key=UNION3_SOURCE_PROFILE_KEY,
        identifier=UNION3_ARXIV_ID,
    )
    assert queued.lifecycle_status == "QUEUED"
    assert queued.coverage_status == "PENDING"
    assert extraction is None
    assert all(
        artifact["verification_status"] == "NOT_ACQUIRED"
        for artifact in queued.raw_artifacts
    )

    repeated, repeated_extraction = await queue_union3_source(
        db_session,
        user_id=owner.id,
        workspace_id=workspace.id,
        source_profile_key=UNION3_SOURCE_PROFILE_KEY,
        identifier=UNION3_ARXIV_ID,
    )
    assert repeated.id == queued.id
    assert repeated_extraction is None

    completed, completed_extraction = await process_queued_union3_source(
        db_session,
        source_document_id=queued.id,
        trusted_snapshot=_snapshot(),
    )
    assert completed.lifecycle_status == "COMPLETED"
    assert completed_extraction is not None
    validate_registered_union3_source_receipt(
        raw_artifacts=completed.raw_artifacts,
        raw_artifact_hashes=completed.raw_artifact_hashes,
        source_metadata=completed.source_metadata,
    )

    idempotent, idempotent_extraction = await process_queued_union3_source(
        db_session,
        source_document_id=queued.id,
        trusted_snapshot=_snapshot(),
    )
    assert idempotent.id == completed.id
    assert idempotent_extraction.id == completed_extraction.id


async def test_review_requires_configured_independent_user_and_exact_hashes(db_session):
    owner = _user("audit-owner")
    reviewer = _user("science-reviewer")
    other = _user("not-a-reviewer")
    db_session.add_all([owner, reviewer, other])
    await db_session.commit()
    workspace = await create_workspace(
        db_session,
        user_id=owner.id,
        title="Review binding",
    )
    document, extraction = await ingest_union3_source(
        db_session,
        user_id=owner.id,
        workspace_id=workspace.id,
        source_profile_key=UNION3_SOURCE_PROFILE_KEY,
        identifier=UNION3_ARXIV_ID,
        trusted_snapshot=_snapshot(),
    )
    candidate = extraction.extraction_payload["candidates"][0]
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
    )
    db_session.add(audit)
    await db_session.commit()

    kwargs = {
        "workspace_id": workspace.id,
        "audit_id": audit.id,
        "source_document_id": document.id,
        "source_extraction_id": extraction.id,
        "candidate_id": candidate["candidate_id"],
        "claim_hash": candidate["claim_hash"],
        "source_hash": document.source_document_hash,
        "anchor_ids": candidate["source_anchor_ids"],
        "decision": "APPROVED",
        "comment": "The source and claim mapping are correct.",
        "reviewer_usernames": {reviewer.username},
    }
    with pytest.raises(WorkspaceConflictError) as machine_gate_error:
        await create_claim_audit_review(
            db_session,
            reviewer_user_id=reviewer.id,
            reviewer_username=reviewer.username,
            **kwargs,
        )
    assert machine_gate_error.value.error_class == "claim_audit_machine_gate_not_ready"

    audit.machine_support_eligible = True
    audit.review_status = "PENDING"
    await db_session.commit()
    with pytest.raises(WorkspaceConflictError) as anchor_error:
        await create_claim_audit_review(
            db_session,
            reviewer_user_id=reviewer.id,
            reviewer_username=reviewer.username,
            **{**kwargs, "anchor_ids": candidate["source_anchor_ids"][:-1]},
        )
    assert anchor_error.value.error_class == "anchor_ids_mismatch"

    with pytest.raises(WorkspacePermissionError) as same_owner_error:
        await create_claim_audit_review(
            db_session,
            reviewer_user_id=owner.id,
            reviewer_username=owner.username,
            **{**kwargs, "reviewer_usernames": {owner.username}},
        )
    assert same_owner_error.value.error_class == "independent_reviewer_required"

    with pytest.raises(WorkspacePermissionError) as unconfigured_error:
        await create_claim_audit_review(
            db_session,
            reviewer_user_id=other.id,
            reviewer_username=other.username,
            **kwargs,
        )
    assert unconfigured_error.value.error_class == "scientific_reviewer_not_configured"

    review = await create_claim_audit_review(
        db_session,
        reviewer_user_id=reviewer.id,
        reviewer_username=reviewer.username,
        **kwargs,
    )
    assert isinstance(review, ClaimAuditReview)
    assert review.audit_owner_user_id == owner.id
    assert review.reviewer_user_id == reviewer.id
    assert review.reviewer_username == reviewer_pseudonym(audit.id, reviewer.id)
    assert reviewer.username not in review.reviewer_username
    assert review.review_scope == "scientific_claim_review"
    assert review.supports_finalization is True
    assert review.claim_hash == candidate["claim_hash"]
    assert review.source_hash == document.source_document_hash
    assert review.anchor_ids == candidate["source_anchor_ids"]

    owner_view = await list_claim_audit_reviews(
        db_session,
        requester_user_id=owner.id,
        requester_username=owner.username,
        workspace_id=workspace.id,
        audit_id=audit.id,
        reviewer_usernames={reviewer.username},
    )
    assert [item.id for item in owner_view] == [review.id]


async def test_source_loader_gap_is_persisted_and_retry_creates_new_version(db_session):
    owner = _user("loader-gap-owner")
    db_session.add(owner)
    await db_session.commit()
    workspace = await create_workspace(
        db_session,
        user_id=owner.id,
        title="Loader gap",
    )

    with pytest.raises(WorkspaceServiceError) as first_error:
        await ingest_union3_source(
            db_session,
            user_id=owner.id,
            workspace_id=workspace.id,
            source_profile_key=UNION3_SOURCE_PROFILE_KEY,
            identifier=UNION3_ARXIV_ID,
        )
    assert first_error.value.error_class == "authoritative_source_loader_not_configured"
    assert first_error.value.retryable is True
    failed_id = first_error.value.source_document_id

    with pytest.raises(WorkspaceServiceError) as repeated_error:
        await ingest_union3_source(
            db_session,
            user_id=owner.id,
            workspace_id=workspace.id,
            source_profile_key=UNION3_SOURCE_PROFILE_KEY,
            identifier=UNION3_ARXIV_ID,
        )
    assert repeated_error.value.source_document_id == failed_id

    recovered_document, recovered_extraction = await ingest_union3_source(
        db_session,
        user_id=owner.id,
        workspace_id=workspace.id,
        source_profile_key=UNION3_SOURCE_PROFILE_KEY,
        identifier=UNION3_ARXIV_ID,
        trusted_snapshot=_snapshot(),
        create_new_version=True,
    )
    assert recovered_document.version == 2
    assert recovered_document.supersedes_source_document_id == failed_id
    assert recovered_document.lifecycle_status == "COMPLETED"
    assert recovered_extraction is not None

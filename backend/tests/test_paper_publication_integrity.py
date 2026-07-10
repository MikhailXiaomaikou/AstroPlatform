"""Regression tests for content-bound paper publication validation."""

from __future__ import annotations

import uuid
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.models.schemas import ChatSession, PaperDraft
from app.services.analysis_validator import (
    bind_paper_validation,
    build_evidence_snapshot,
    evidence_snapshot_fingerprint,
    paper_content_hash,
    paper_validation_is_current,
    paper_validation_is_publishable,
    validate_analysis,
)
from app.services.server_evidence import build_server_evidence_record


_UNIT_OWNER_ID = "00000000-0000-0000-0000-000000000001"


def _validation(
    status: str,
    *,
    session_id: str = "unit-test-session",
    owner_id: str = _UNIT_OWNER_ID,
    evidence_value: str = "synthetic-1",
) -> dict:
    record = build_server_evidence_record(
        session_id=session_id,
        owner_id=owner_id,
        run_id=f"run-{evidence_value}",
        assistant_reply="Synthetic evidence-backed validation.",
        tool_results=[
            {
                "tool": "search_objects",
                "input": {"query": "synthetic source"},
                "result": {
                    "source_id": evidence_value,
                    "bibcode": "2020ApJ...900....1S",
                },
            }
        ],
    )
    snapshot = build_evidence_snapshot(
        session_id=session_id,
        owner_id=owner_id,
        records=[record],
    )
    return {
        "overall_status": status,
        "score": 1.0 if status == "PASS" else 0.5,
        "checks": [
            {
                "name": "test_gate",
                "status": status,
                "details": f"Synthetic {status} result for publication-gate testing.",
                "recommendation": "Resolve the scientific issue before publication.",
            }
        ],
        "evidence_snapshot": snapshot,
        "evidence_fingerprint": evidence_snapshot_fingerprint(snapshot),
    }


def _generated(title: str = "Content-bound draft") -> dict:
    paper_json = {
        "title": title,
        "abstract": "A reproducible analysis draft.",
        "introduction": {"text": "Introduction."},
        "data_and_methods": {
            "data_sources": "Archive data.",
            "analysis_methods": "Recorded analysis.",
        },
        "results": {"text": "Result.", "figures": [], "tables": []},
        "discussion": {"text": "Discussion."},
        "conclusions": "Conclusion.",
        "acknowledgments": "Acknowledgments.",
    }
    return {
        "paper_json": paper_json,
        "latex_source": "\\documentclass{aastex631}\n\\begin{document}\nDraft\n\\end{document}",
        "bibtex": "@article{source, title={Source}}",
    }


async def _create_session(db_session, user) -> ChatSession:
    session = ChatSession(
        id=uuid.uuid4(),
        user_id=user.id,
        title="Publication integrity session",
        messages=[{"role": "user", "content": "Analyze a source."}],
    )
    session.audit_log = [
        build_server_evidence_record(
            session_id=session.id,
            owner_id=user.id,
            run_id="test-server-run",
            assistant_reply="A descriptive catalog result.",
            tool_results=[
                {
                    "tool": "search_objects",
                    "input": {"query": "catalog source"},
                    "result": {"bibcode": "2020ApJ...900....1S"},
                }
            ],
        )
    ]
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    return session


async def test_validator_assesses_the_current_draft_text(db_session, test_user):
    user, _ = test_user
    session = ChatSession(
        id=uuid.uuid4(),
        user_id=user.id,
        title="Draft-content validation session",
        messages=[
            {"role": "user", "content": "Analyze a catalog source."},
            {
                "role": "assistant",
                "content": (
                    "We report p=0.01 and p=0.02; the result follows "
                    "2020ApJ...900....1S."
                ),
                "actions": [
                    {
                        "action": "search",
                        "query": "M31",
                        "sources": ["simbad"],
                        "tool_result": [{"name": "M31"}],
                    }
                ],
            },
        ],
    )
    session.audit_log = [
        build_server_evidence_record(
            session_id=session.id,
            owner_id=user.id,
            run_id="draft-validation-run",
            assistant_reply="Catalog evidence from 2020ApJ...900....1S.",
            tool_results=[
                {
                    "tool": "search_objects",
                    "input": {"query": "M31"},
                    "result": {
                        "bibcode": "2020ApJ...900....1S",
                        "p_values": [0.01, 0.02],
                    },
                }
            ],
        )
    ]
    db_session.add(session)
    await db_session.commit()

    safe = await validate_analysis(
        str(session.id),
        db_session,
        owner_id=str(user.id),
        paper_json={
            "results": {
                "text": "We report p=0.01 and p=0.02 in a descriptive catalog result."
            }
        },
    )
    assert safe["overall_status"] == "PASS"
    statistical_check = next(
        check for check in safe["checks"] if check["name"] == "statistical_method_audit"
    )
    assert statistical_check["status"] == "PASS"

    risky = await validate_analysis(
        str(session.id),
        db_session,
        owner_id=str(user.id),
        paper_json={"results": {"text": "We claim a detection at S/N=2."}},
    )
    conclusion_check = next(
        check
        for check in risky["checks"]
        if check["name"] == "conclusion_data_consistency"
    )
    assert conclusion_check["status"] == "FAIL"
    assert risky["overall_status"] == "FAIL"


async def test_validator_snapshot_is_canonical_and_tracks_tool_evidence(
    db_session,
    test_user,
):
    user, _ = test_user
    session = ChatSession(
        id=uuid.uuid4(),
        user_id=user.id,
        title="Immutable evidence snapshot session",
        messages=[
            {"content": "Analyze the saved source.", "role": "user"},
            {
                "actions": [
                    {
                        "sources": ["simbad"],
                        "tool_result": {
                            "measurement": {"uncertainty": 0.2, "value": 1.5},
                            "bibcode": "2020ApJ...900....1S",
                        },
                        "query": "M31",
                        "action": "search",
                    }
                ],
                "content": "The source is documented by 2020ApJ...900....1S.",
                "role": "assistant",
            },
        ],
    )
    session.audit_log = [
        build_server_evidence_record(
            session_id=session.id,
            owner_id=user.id,
            run_id="canonical-evidence-run",
            assistant_reply="The source is documented by 2020ApJ...900....1S.",
            tool_results=[
                {
                    "tool": "search_objects",
                    "input": {"query": "M31"},
                    "result": {
                        "bibcode": "2020ApJ...900....1S",
                        "measurement": {"value": 1.5, "uncertainty": 0.2},
                    },
                }
            ],
        )
    ]
    db_session.add(session)
    await db_session.commit()

    first = await validate_analysis(
        str(session.id),
        db_session,
        owner_id=str(user.id),
        paper_json={"results": {"text": "A descriptive catalog result."}},
    )
    assert first["evidence_fingerprint"].startswith("sha256:")
    assert first["evidence_snapshot"]["session_id"] == str(session.id)

    # Mapping insertion order is not evidence and must not perturb the digest.
    session.messages = [
        {"role": "user", "content": "Analyze the saved source."},
        {
            "role": "assistant",
            "content": "The source is documented by 2020ApJ...900....1S.",
            "actions": [
                {
                    "action": "search",
                    "query": "M31",
                    "tool_result": {
                        "bibcode": "2020ApJ...900....1S",
                        "measurement": {"value": 1.5, "uncertainty": 0.2},
                    },
                    "sources": ["simbad"],
                }
            ],
        },
    ]
    await db_session.commit()
    reordered = await validate_analysis(
        str(session.id),
        db_session,
        owner_id=str(user.id),
        paper_json={"results": {"text": "A descriptive catalog result."}},
    )
    assert reordered["evidence_snapshot"] == first["evidence_snapshot"]
    assert reordered["evidence_fingerprint"] == first["evidence_fingerprint"]

    changed_messages = deepcopy(session.messages)
    changed_messages[1]["actions"][0]["tool_result"]["measurement"]["value"] = 1.6
    session.messages = changed_messages
    await db_session.commit()
    changed_client_transcript = await validate_analysis(
        str(session.id),
        db_session,
        owner_id=str(user.id),
        paper_json={"results": {"text": "A descriptive catalog result."}},
    )
    assert changed_client_transcript["evidence_fingerprint"] == first["evidence_fingerprint"]

    tampered = deepcopy(session.audit_log)
    tampered[0]["tool_results"][0]["result"]["measurement"]["value"] = 1.6
    session.audit_log = tampered
    await db_session.commit()
    rejected = await validate_analysis(
        str(session.id),
        db_session,
        owner_id=str(user.id),
        paper_json={"results": {"text": "A descriptive catalog result."}},
    )
    integrity = next(
        check
        for check in rejected["checks"]
        if check["name"] == "server_evidence_integrity"
    )
    assert integrity["status"] == "FAIL"
    assert rejected["evidence_fingerprint"] != first["evidence_fingerprint"]


def test_unchanged_paper_cannot_reuse_a_different_evidence_binding():
    generated = _generated()
    first = bind_paper_validation(
        _validation("PASS", evidence_value="measurement-v1"),
        session_id="unit-test-session",
        owner_id=_UNIT_OWNER_ID,
        paper_json=generated["paper_json"],
        latex_source=generated["latex_source"],
        bibtex=generated["bibtex"],
        journal_format="aastex",
    )
    second = bind_paper_validation(
        _validation("PASS", evidence_value="measurement-v2"),
        session_id="unit-test-session",
        owner_id=_UNIT_OWNER_ID,
        paper_json=generated["paper_json"],
        latex_source=generated["latex_source"],
        bibtex=generated["bibtex"],
        journal_format="aastex",
    )

    assert first["content_hash"] == second["content_hash"]
    assert first["evidence_fingerprint"] != second["evidence_fingerprint"]
    assert first["binding_hash"] != second["binding_hash"]

    # Swapping in later evidence while retaining the old paper validation is a
    # mismatched hybrid, even though the manuscript bytes did not change.
    hybrid = deepcopy(first)
    hybrid["evidence_snapshot"] = second["evidence_snapshot"]
    hybrid["evidence_fingerprint"] = second["evidence_fingerprint"]
    assert not paper_validation_is_current(
        hybrid,
        session_id="unit-test-session",
        owner_id=_UNIT_OWNER_ID,
        paper_json=generated["paper_json"],
        latex_source=generated["latex_source"],
        bibtex=generated["bibtex"],
        journal_format="aastex",
    )
    assert not paper_validation_is_publishable(
        hybrid,
        session_id="unit-test-session",
        owner_id=_UNIT_OWNER_ID,
        paper_json=generated["paper_json"],
        latex_source=generated["latex_source"],
        bibtex=generated["bibtex"],
        journal_format="aastex",
    )

    wrong_session = deepcopy(first)
    assert not paper_validation_is_publishable(
        wrong_session,
        session_id="different-session",
        owner_id=_UNIT_OWNER_ID,
        paper_json=generated["paper_json"],
        latex_source=generated["latex_source"],
        bibtex=generated["bibtex"],
        journal_format="aastex",
    )


def test_content_hash_binding_rejects_changed_artifacts():
    generated = _generated()
    bound = bind_paper_validation(
        _validation("PASS"),
        session_id="unit-test-session",
        owner_id=_UNIT_OWNER_ID,
        paper_json=generated["paper_json"],
        latex_source=generated["latex_source"],
        bibtex=generated["bibtex"],
        journal_format="aastex",
    )

    assert bound["content_hash"].startswith("sha256:")
    assert paper_validation_is_current(
        bound,
        session_id="unit-test-session",
        owner_id=_UNIT_OWNER_ID,
        paper_json=generated["paper_json"],
        latex_source=generated["latex_source"],
        bibtex=generated["bibtex"],
        journal_format="aastex",
    )
    assert paper_validation_is_publishable(
        bound,
        session_id="unit-test-session",
        owner_id=_UNIT_OWNER_ID,
        paper_json=generated["paper_json"],
        latex_source=generated["latex_source"],
        bibtex=generated["bibtex"],
        journal_format="aastex",
    )

    changed_json = {**generated["paper_json"], "title": "Changed after validation"}
    assert not paper_validation_is_current(
        bound,
        session_id="unit-test-session",
        owner_id=_UNIT_OWNER_ID,
        paper_json=changed_json,
        latex_source=generated["latex_source"],
        bibtex=generated["bibtex"],
        journal_format="aastex",
    )
    assert not paper_validation_is_current(
        bound,
        session_id="unit-test-session",
        owner_id=_UNIT_OWNER_ID,
        paper_json=generated["paper_json"],
        latex_source=generated["latex_source"] + "\n% changed",
        bibtex=generated["bibtex"],
        journal_format="aastex",
    )
    assert not paper_validation_is_current(
        bound,
        session_id="unit-test-session",
        owner_id=_UNIT_OWNER_ID,
        paper_json=generated["paper_json"],
        latex_source=generated["latex_source"],
        bibtex=generated["bibtex"] + "\n% changed",
        journal_format="aastex",
    )
    assert not paper_validation_is_current(
        bound,
        session_id="unit-test-session",
        owner_id=_UNIT_OWNER_ID,
        paper_json=generated["paper_json"],
        latex_source=generated["latex_source"],
        bibtex=generated["bibtex"],
        journal_format="mnras",
    )
    assert not paper_validation_is_publishable(
        {"overall_status": "PASS"},
        session_id="unit-test-session",
        owner_id=_UNIT_OWNER_ID,
        paper_json=generated["paper_json"],
        latex_source=generated["latex_source"],
        bibtex=generated["bibtex"],
        journal_format="aastex",
    )

    inconsistent = bind_paper_validation(
        {
            **_validation("PASS"),
            "checks": [{"name": "hidden_failure", "status": "FAIL"}],
        },
        session_id="unit-test-session",
        owner_id=_UNIT_OWNER_ID,
        paper_json=generated["paper_json"],
        latex_source=generated["latex_source"],
        bibtex=generated["bibtex"],
        journal_format="aastex",
    )
    assert inconsistent["publishable"] is False
    assert inconsistent["publication_status"] == "unverified_private_draft"


async def test_fail_draft_is_saved_privately_and_override_cannot_publish(
    app_client,
    test_user,
    db_session,
):
    user, token = test_user
    session = await _create_session(db_session, user)
    headers = {"Authorization": f"Bearer {token}"}
    validator = AsyncMock(
        return_value=_validation(
            "FAIL", session_id=str(session.id), owner_id=str(user.id)
        )
    )
    generator = AsyncMock(return_value=_generated())

    with (
        patch("app.api.paper.validate_analysis", new=validator),
        patch(
            "app.api.paper.generate_paper_draft",
            new=generator,
        ),
    ):
        generated = await app_client.post(
            "/api/paper/generate",
            json={
                "session_id": str(session.id),
                "journal_format": "aastex",
                "override_validation": True,
            },
            headers=headers,
        )
        assert generated.status_code == 200
        body = generated.json()
        assert body["is_public"] is False
        assert body["public_token"] is None
        assert body["validation"]["publishable"] is False
        assert body["validation"]["publication_status"] == "unverified_private_draft"
        assert body["validation"]["watermark"]
        assert "UNVERIFIED DRAFT --- NOT FOR PUBLICATION" in body["latex_source"]
        assert body["validation"]["content_hash"] == paper_content_hash(
            paper_json=body["paper_json"],
            latex_source=body["latex_source"],
            bibtex=body["bibtex"],
            journal_format=body["journal_format"],
        )

        publish = await app_client.post(
            f"/api/paper/{body['id']}/publish",
            headers=headers,
        )
        assert publish.status_code == 409
        assert publish.json()["detail"]["validation"]["overall_status"] == "FAIL"

    stored = (
        await db_session.execute(
            select(PaperDraft).where(PaperDraft.id == uuid.UUID(body["id"]))
        )
    ).scalar_one()
    assert stored.is_public is False
    assert stored.public_token is None
    assert validator.await_count == 2


async def test_public_endpoint_rejects_content_changed_after_pass(
    app_client,
    test_user,
    db_session,
):
    user, token = test_user
    session = await _create_session(db_session, user)
    headers = {"Authorization": f"Bearer {token}"}
    validator = AsyncMock(
        return_value=_validation(
            "PASS", session_id=str(session.id), owner_id=str(user.id)
        )
    )

    with (
        patch("app.api.paper.validate_analysis", new=validator),
        patch(
            "app.api.paper.generate_paper_draft",
            new=AsyncMock(return_value=_generated()),
        ),
    ):
        generated = await app_client.post(
            "/api/paper/generate",
            json={"session_id": str(session.id)},
            headers=headers,
        )
        paper_id = generated.json()["id"]
        publish = await app_client.post(
            f"/api/paper/{paper_id}/publish", headers=headers
        )
        assert publish.status_code == 200
        token_value = publish.json()["public_token"]
        assert (
            await app_client.get(f"/api/paper/public/{token_value}")
        ).status_code == 200

    draft = (
        await db_session.execute(
            select(PaperDraft).where(PaperDraft.id == uuid.UUID(paper_id))
        )
    ).scalar_one()
    draft.paper_json = {**draft.paper_json, "title": "Out-of-band content change"}
    await db_session.commit()

    public_after_change = await app_client.get(f"/api/paper/public/{token_value}")
    assert public_after_change.status_code == 404
    owner_view = await app_client.get(f"/api/paper/{paper_id}", headers=headers)
    assert owner_view.status_code == 200
    assert owner_view.json()["is_public"] is False
    assert owner_view.json()["public_token"] is None
    assert validator.await_count == 2


async def test_published_draft_keeps_its_validation_time_evidence_snapshot(
    app_client,
    test_user,
    db_session,
):
    user, token = test_user
    session = await _create_session(db_session, user)
    session.messages = [
        {"role": "user", "content": "Analyze a saved catalog source."},
        {
            "role": "assistant",
            "content": "A descriptive result based on 2020ApJ...900....1S.",
            "actions": [
                {
                    "action": "search",
                    "query": "M31",
                    "sources": ["simbad"],
                    "tool_result": {
                        "bibcode": "2020ApJ...900....1S",
                        "measurement": {"value": 1.5, "uncertainty": 0.2},
                    },
                }
            ],
        },
    ]
    await db_session.commit()
    headers = {"Authorization": f"Bearer {token}"}

    with patch(
        "app.api.paper.generate_paper_draft",
        new=AsyncMock(return_value=_generated()),
    ):
        generated = await app_client.post(
            "/api/paper/generate",
            json={"session_id": str(session.id)},
            headers=headers,
        )
    assert generated.status_code == 200
    publish = await app_client.post(
        f"/api/paper/{generated.json()['id']}/publish",
        headers=headers,
    )
    assert publish.status_code == 200
    published = publish.json()
    old_fingerprint = published["validation"]["evidence_fingerprint"]
    old_binding = published["validation"]["binding_hash"]
    public_token = published["public_token"]

    # Editing the source session later must not silently replace what the
    # already-published paper says was validated.
    changed_messages = deepcopy(session.messages)
    changed_messages[1]["actions"][0]["tool_result"]["measurement"]["value"] = 9.9
    session.messages = changed_messages
    await db_session.commit()

    public = await app_client.get(f"/api/paper/public/{public_token}")
    assert public.status_code == 200
    public_validation = public.json()["validation"]
    assert public_validation["evidence_fingerprint"] == old_fingerprint
    assert public_validation["binding_hash"] == old_binding
    assert "evidence_snapshot" not in public_validation
    assert public_validation["evidence_snapshot_redacted"] is True

    owner = await app_client.get(
        f"/api/paper/{published['id']}",
        headers=headers,
    )
    assert owner.status_code == 200
    owner_validation = owner.json()["validation"]
    assert owner_validation["evidence_fingerprint"] == old_fingerprint
    assert "evidence_snapshot" not in owner_validation
    assert owner_validation["evidence_snapshot_redacted"] is True

    stored = (
        await db_session.execute(
            select(PaperDraft).where(PaperDraft.id == uuid.UUID(published["id"]))
        )
    ).scalar_one()
    assert stored.validation["evidence_snapshot"]["source"] == "server_tool_execution"


async def test_pass_edit_still_requires_an_explicit_republish(
    app_client,
    test_user,
    db_session,
):
    user, token = test_user
    session = await _create_session(db_session, user)
    headers = {"Authorization": f"Bearer {token}"}
    validator = AsyncMock(
        return_value=_validation(
            "PASS", session_id=str(session.id), owner_id=str(user.id)
        )
    )

    with (
        patch("app.api.paper.validate_analysis", new=validator),
        patch(
            "app.api.paper.generate_paper_draft",
            new=AsyncMock(return_value=_generated()),
        ),
    ):
        generated = await app_client.post(
            "/api/paper/generate",
            json={"session_id": str(session.id)},
            headers=headers,
        )
        paper_id = generated.json()["id"]
        publish = await app_client.post(
            f"/api/paper/{paper_id}/publish", headers=headers
        )
        assert publish.status_code == 200
        old_token = publish.json()["public_token"]

        changed_json = {
            **generated.json()["paper_json"],
            "title": "Edited but still scientifically valid",
        }
        update = await app_client.put(
            f"/api/paper/{paper_id}",
            json={"paper_json": changed_json},
            headers=headers,
        )
        assert update.status_code == 200
        assert update.json()["validation"]["publishable"] is True
        assert update.json()["is_public"] is False
        assert update.json()["public_token"] is None
        assert (
            await app_client.get(f"/api/paper/public/{old_token}")
        ).status_code == 404


async def test_edit_revalidates_unpublishes_and_rebinds_current_content(
    app_client,
    test_user,
    db_session,
):
    user, token = test_user
    session = await _create_session(db_session, user)
    headers = {"Authorization": f"Bearer {token}"}
    validator = AsyncMock(
        side_effect=[
            _validation("PASS", session_id=str(session.id), owner_id=str(user.id)),
            _validation("PASS", session_id=str(session.id), owner_id=str(user.id)),
            _validation("FAIL", session_id=str(session.id), owner_id=str(user.id)),
            _validation("FAIL", session_id=str(session.id), owner_id=str(user.id)),
        ]
    )

    with (
        patch("app.api.paper.validate_analysis", new=validator),
        patch(
            "app.api.paper.generate_paper_draft",
            new=AsyncMock(return_value=_generated()),
        ),
    ):
        generated = await app_client.post(
            "/api/paper/generate",
            json={"session_id": str(session.id)},
            headers=headers,
        )
        original = generated.json()
        publish = await app_client.post(
            f"/api/paper/{original['id']}/publish",
            headers=headers,
        )
        assert publish.status_code == 200
        public_token = publish.json()["public_token"]
        published_hash = publish.json()["validation"]["content_hash"]

        changed_json = {**original["paper_json"], "title": "Edited scientific claim"}
        update = await app_client.put(
            f"/api/paper/{original['id']}",
            json={"paper_json": changed_json},
            headers=headers,
        )
        assert update.status_code == 200
        updated = update.json()
        assert updated["is_public"] is False
        assert updated["public_token"] is None
        assert updated["validation"]["publishable"] is False
        assert updated["validation"]["content_hash"] != published_hash
        assert "UNVERIFIED DRAFT --- NOT FOR PUBLICATION" in updated["latex_source"]
        assert (
            await app_client.get(f"/api/paper/public/{public_token}")
        ).status_code == 404
        assert (
            validator.await_args_list[2].kwargs["paper_json"]["title"]
            == "Edited scientific claim"
        )

        republish = await app_client.post(
            f"/api/paper/{original['id']}/publish",
            headers=headers,
        )
        assert republish.status_code == 409


async def test_legacy_public_row_fails_closed_then_upgrades_on_publish(
    app_client,
    test_user,
    db_session,
):
    user, token = test_user
    session = await _create_session(db_session, user)
    draft = PaperDraft(
        id=uuid.uuid4(),
        user_id=user.id,
        session_id=session.id,
        journal_format="aastex",
        paper_json=_generated("Legacy draft")["paper_json"],
        latex_source="\\documentclass{aastex631}",
        bibtex="@article{legacy, title={Legacy}}",
        validation={"overall_status": "PASS", "score": 1.0, "checks": []},
        is_public=True,
        public_token="legacy-public-token",
    )
    db_session.add(draft)
    await db_session.commit()
    headers = {"Authorization": f"Bearer {token}"}

    share = await app_client.post(
        f"/api/sessions/{session.id}/share",
        json={"access_level": "view", "expires_hours": 24},
        headers=headers,
    )
    assert share.status_code == 200
    share_token = share.json()["share_token"]

    assert (
        await app_client.get("/api/paper/public/legacy-public-token")
    ).status_code == 404
    shared_before = await app_client.get(f"/api/shared/{share_token}", headers=headers)
    assert shared_before.status_code == 200
    assert shared_before.json()["session"]["paper_drafts"] == []
    owner_before = await app_client.get(f"/api/paper/{draft.id}", headers=headers)
    assert owner_before.status_code == 200
    assert owner_before.json()["is_public"] is False

    validator = AsyncMock(
        return_value=_validation(
            "PASS", session_id=str(session.id), owner_id=str(user.id)
        )
    )
    with patch("app.api.paper.validate_analysis", new=validator):
        publish = await app_client.post(
            f"/api/paper/{draft.id}/publish", headers=headers
        )
    assert publish.status_code == 200
    body = publish.json()
    assert body["is_public"] is True
    assert body["public_token"] == "legacy-public-token"
    assert body["validation"]["schema_version"] == 4
    assert body["validation"]["publishable"] is True
    assert (
        await app_client.get("/api/paper/public/legacy-public-token")
    ).status_code == 200
    shared_after = await app_client.get(f"/api/shared/{share_token}", headers=headers)
    assert shared_after.status_code == 200
    assert len(shared_after.json()["session"]["paper_drafts"]) == 1

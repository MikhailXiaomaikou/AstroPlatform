"""Research-job API ownership and lifecycle contracts."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models.research_records import ResearchJob
from app.models.schemas import User


@pytest.mark.asyncio
async def test_list_and_detail_never_leak_foreign_jobs(app_client, db_session, test_user):
    user, token = test_user
    other = User(
        id=uuid.uuid4(),
        username="other_job_owner",
        email="other-jobs@example.com",
        password_hash="unused",
    )
    db_session.add(other)
    db_session.add_all([
        ResearchJob(
            job_id="mine-1",
            user_id=user.id,
            tool_name="fit_cosmology_mcmc",
            inputs_hash="a",
            args={"n_steps": 800},
            args_replayable=True,
            status="completed",
            result={"publication_ready": False},
            created_at=datetime.now(timezone.utc),
        ),
        ResearchJob(
            job_id="foreign-1",
            user_id=other.id,
            tool_name="fit_cosmology_mcmc",
            inputs_hash="b",
            args={},
            args_replayable=True,
            status="completed",
            result={"secret": 42},
            created_at=datetime.now(timezone.utc),
        ),
    ])
    await db_session.commit()
    headers = {"Authorization": f"Bearer {token}"}

    listed = await app_client.get("/api/jobs", headers=headers)
    assert listed.status_code == 200
    assert [item["job_id"] for item in listed.json()["items"]] == ["mine-1"]
    detail = await app_client.get("/api/jobs/mine-1", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["result"] == {"publication_ready": False}
    hidden = await app_client.get("/api/jobs/foreign-1", headers=headers)
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_retry_refuses_nonterminal_job(app_client, db_session, test_user):
    user, token = test_user
    db_session.add(ResearchJob(
        job_id="running-1",
        user_id=user.id,
        tool_name="fit_cosmology_mcmc",
        inputs_hash="a",
        args={},
        args_replayable=True,
        status="running",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()

    response = await app_client.post(
        "/api/jobs/running-1/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409

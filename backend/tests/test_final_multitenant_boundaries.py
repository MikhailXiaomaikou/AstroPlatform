"""Attack regressions for pipeline, notebook-export, and SAMP boundaries."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.auth import hash_password
from app.models.schemas import (
    DataFile,
    PipelineRun,
    PipelineTemplateDB,
    SharedPipeline,
    User,
)


def _victim() -> User:
    suffix = uuid.uuid4().hex[:10]
    return User(
        id=uuid.uuid4(),
        username=f"victim-{suffix}",
        email=f"victim-{suffix}@example.test",
        password_hash=hash_password("not-used-in-test"),
        subscription_tier="solo",
    )


async def test_pipeline_run_requires_auth_and_rejects_foreign_storage(
    app_client, db_session, test_user
):
    attacker, token = test_user
    victim = _victim()
    db_session.add(victim)
    await db_session.flush()
    db_session.add(
        DataFile(
            user_id=victim.id,
            source="upload",
            object_id="private-spectrum",
            fits_path="private/victim-spectrum.fits",
            metadata_={"sha256": "a" * 64},
        )
    )
    await db_session.commit()

    dag = {
        "nodes": [
            {
                "id": "load",
                "type": "LoadData",
                "data": {"params": {}},
            }
        ],
        "edges": [],
    }
    body = {"dag": dag, "input_data_id": "private/victim-spectrum.fits"}

    anonymous = await app_client.post("/api/pipeline/run?async_mode=false", json=body)
    assert anonymous.status_code == 401

    foreign = await app_client.post(
        "/api/pipeline/run?async_mode=false",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert foreign.status_code == 404
    assert foreign.json()["detail"] == "Pipeline input not found"

    # The caller identity used by the route is the authenticated owner, not a
    # shared anonymous placeholder.
    assert attacker.id != victim.id


async def test_batch_pipeline_rejects_foreign_storage(
    app_client, db_session, test_user
):
    _attacker, token = test_user
    victim = _victim()
    db_session.add(victim)
    await db_session.flush()
    db_session.add(
        DataFile(
            user_id=victim.id,
            source="upload",
            object_id="private-image",
            fits_path="private/victim-image.fits",
            metadata_={},
        )
    )
    await db_session.commit()

    response = await app_client.post(
        "/api/pipeline/batch-run",
        json={
            "dag": {
                "nodes": [
                    {
                        "id": "load",
                        "type": "LoadData",
                        "data": {"params": {}},
                    }
                ],
                "edges": [],
            },
            "input_data_ids": ["private/victim-image.fits"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    # Batch preserves per-item error accounting while withholding existence.
    assert response.status_code == 200
    assert response.json()["failed"] == 1
    assert response.json()["results"][0]["status"] == "failed"


async def test_scheduler_rejects_foreign_storage_at_creation(
    app_client, db_session, test_user
):
    _attacker, token = test_user
    victim = _victim()
    db_session.add(victim)
    await db_session.flush()
    db_session.add(
        DataFile(
            user_id=victim.id,
            source="upload",
            object_id="scheduled-private",
            fits_path="private/scheduled-victim.fits",
            metadata_={},
        )
    )
    await db_session.commit()

    response = await app_client.post(
        "/api/scheduler/schedules",
        json={
            "name": "steal later",
            "dag": {
                "nodes": [
                    {
                        "id": "load",
                        "type": "LoadData",
                        "data": {"params": {}},
                    }
                ],
                "edges": [],
            },
            "input_data_id": "private/scheduled-victim.fits",
            "cron_expr": "daily",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Pipeline input not found"


async def test_jupyter_export_enforces_run_owner_and_template_share(
    app_client, db_session, test_user
):
    attacker, token = test_user
    victim = _victim()
    db_session.add(victim)
    await db_session.flush()
    dag = {"nodes": [], "edges": []}
    template = PipelineTemplateDB(
        user_id=victim.id,
        name="Private method",
        description="unpublished",
        dag=dag,
        is_builtin=False,
    )
    run = PipelineRun(
        user_id=victim.id,
        dag=dag,
        status="completed",
    )
    db_session.add_all([template, run])
    await db_session.commit()
    await db_session.refresh(template)
    await db_session.refresh(run)

    headers = {"Authorization": f"Bearer {token}"}
    foreign_template = await app_client.post(
        "/api/integration/jupyter/export",
        json={"template_id": str(template.id)},
        headers=headers,
    )
    foreign_run = await app_client.post(
        "/api/integration/jupyter/export",
        json={"run_id": str(run.id)},
        headers=headers,
    )
    assert foreign_template.status_code == 404
    assert foreign_run.status_code == 404

    db_session.add(
        SharedPipeline(
            template_id=template.id,
            shared_by=victim.id,
            shared_with=attacker.id,
            permission="view",
        )
    )
    await db_session.commit()
    shared_template = await app_client.post(
        "/api/integration/jupyter/export",
        json={"template_id": str(template.id)},
        headers=headers,
    )
    assert shared_template.status_code == 200
    assert shared_template.headers["content-type"].startswith(
        "application/x-ipynb+json"
    )


async def test_samp_requires_auth_and_is_disabled_in_production(
    app_client, test_user, monkeypatch
):
    _user, token = test_user
    anonymous = await app_client.get("/api/integration/samp/received")
    assert anonymous.status_code == 401

    monkeypatch.setenv("ENV", "production")
    hosted = await app_client.get(
        "/api/integration/samp/received",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert hosted.status_code == 503
    assert "disabled" in hosted.json()["detail"].lower()


def test_samp_receive_buffers_are_owner_scoped():
    from app.api.integration import SAMPReceiver

    owner_a = uuid.uuid4()
    owner_b = uuid.uuid4()
    receiver_a = SAMPReceiver.get_instance(owner_a)
    receiver_b = SAMPReceiver.get_instance(owner_b)
    try:
        receiver_a._on_notification(
            "private",
            "topcat",
            "table.load.fits",
            {"url": "file:///owner-a/private.fits"},
            {},
        )
        assert len(receiver_a.get_received()) == 1
        assert receiver_b.get_received() == []
    finally:
        SAMPReceiver.drop_instance(owner_a)
        SAMPReceiver.drop_instance(owner_b)


def test_scheduler_worker_reauthorizes_persisted_paths_before_dispatch():
    from app.scheduler_worker import (
        _scheduled_inputs_are_owned,
        _scheduled_storage_keys,
    )

    schedule = SimpleNamespace(
        user_id=uuid.uuid4(),
        input_data_id="owned/default.fits",
        dag={
            "nodes": [
                {
                    "id": "load",
                    "type": "LoadData",
                    "data": {"params": {}},
                },
                {
                    "id": "workspace",
                    "type": "ImportWorkspace",
                    "data": {"params": {"path": "owned/table.csv"}},
                },
            ],
            "edges": [],
        },
    )
    assert _scheduled_storage_keys(schedule) == {
        "owned/default.fits",
        "owned/table.csv",
    }

    class _Scalars:
        def __init__(self, values):
            self.values = values

        def all(self):
            return list(self.values)

    class _Result:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return _Scalars(self.values)

    class _Session:
        def __init__(self, values):
            self.values = values

        def execute(self, _statement):
            return _Result(self.values)

    assert _scheduled_inputs_are_owned(
        _Session({"owned/default.fits", "owned/table.csv"}), schedule
    )
    assert not _scheduled_inputs_are_owned(
        _Session({"owned/default.fits"}), schedule
    )

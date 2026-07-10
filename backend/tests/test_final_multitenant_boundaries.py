"""Attack regressions for pipeline, notebook-export, and SAMP boundaries."""

from __future__ import annotations

import uuid
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

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


@pytest.mark.parametrize(
    ("node_type", "params"),
    [
        ("BiasSubtract", {"science_fits_path": "private/alias-victim.fits"}),
        ("BiasSubtract", {"bias_paths": ["private/alias-victim.fits"]}),
        ("DarkCorrect", {"dark_paths": ["private/alias-victim.fits"]}),
        ("FlatField", {"flat_paths": ["private/alias-victim.fits"]}),
        ("Reproject", {"target_wcs_fits": "private/alias-victim.fits"}),
        ("Mosaic", {"fits_paths": ["private/alias-victim.fits"]}),
    ],
)
async def test_pipeline_rejects_foreign_storage_aliases_and_lists(
    app_client,
    db_session,
    test_user,
    node_type,
    params,
):
    _attacker, token = test_user
    victim = _victim()
    db_session.add(victim)
    await db_session.flush()
    db_session.add(
        DataFile(
            user_id=victim.id,
            source="upload",
            object_id="alias-private",
            fits_path="private/alias-victim.fits",
            metadata_={},
        )
    )
    await db_session.commit()

    response = await app_client.post(
        "/api/pipeline/run?async_mode=false",
        json={
            "dag": {
                "nodes": [
                    {
                        "id": "reader",
                        "type": node_type,
                        "data": {"params": params},
                    }
                ],
                "edges": [],
            },
            "input_data_id": "opaque-unused-input",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Pipeline input not found"


@pytest.mark.parametrize(
    "node_type",
    [
        "BiasSubtract",
        "DarkCorrect",
        "FlatField",
        "CosmicRayReject",
        "AstrometricSolve",
        "SourceExtract",
        "Reproject",
        "PSFMatch",
        "Deblend",
    ],
)
async def test_root_file_consumers_reject_foreign_default_input(
    app_client,
    db_session,
    test_user,
    node_type,
):
    _attacker, token = test_user
    victim = _victim()
    db_session.add(victim)
    await db_session.flush()
    db_session.add(
        DataFile(
            user_id=victim.id,
            source="upload",
            object_id="root-private",
            fits_path="private/root-victim.fits",
            metadata_={},
        )
    )
    await db_session.commit()

    response = await app_client.post(
        "/api/pipeline/run?async_mode=false",
        json={
            "dag": {
                "nodes": [
                    {
                        "id": "reader",
                        "type": node_type,
                        "data": {"params": {}},
                    }
                ],
                "edges": [],
            },
            "input_data_id": "private/root-victim.fits",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Pipeline input not found"


async def test_pipeline_rejects_absolute_alias_path(app_client, test_user):
    _user, token = test_user
    response = await app_client.post(
        "/api/pipeline/run?async_mode=false",
        json={
            "dag": {
                "nodes": [
                    {
                        "id": "reader",
                        "type": "BiasSubtract",
                        "data": {
                            "params": {"science_fits_path": "/tmp/private.fits"}
                        },
                    }
                ],
                "edges": [],
            },
            "input_data_id": "opaque-unused-input",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid pipeline input path"


async def test_nested_load_node_cannot_hide_default_input_from_authorizer(
    app_client,
    db_session,
    test_user,
):
    _attacker, token = test_user
    victim = _victim()
    foreign_key = "private/nested-default-victim.fits"
    db_session.add(victim)
    await db_session.flush()
    db_session.add(
        DataFile(
            user_id=victim.id,
            source="upload",
            object_id="nested-default-private",
            fits_path=foreign_key,
            metadata_={},
        )
    )
    await db_session.commit()

    # The engine falls back to the global input_data_id when a nested LoadData
    # parent does not emit fits_path.  A forged incoming edge must not make the
    # owner binder mistake that capability for an unused default.
    response = await app_client.post(
        "/api/pipeline/run?async_mode=false",
        json={
            "dag": {
                "nodes": [
                    {
                        "id": "query",
                        "type": "QueryData",
                        "data": {
                            "params": {"query": "M31", "sources": "simbad"}
                        },
                    },
                    {
                        "id": "load",
                        "type": "LoadData",
                        "data": {"params": {}},
                    },
                ],
                "edges": [
                    {"id": "query-load", "source": "query", "target": "load"}
                ],
            },
            "input_data_id": foreign_key,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Pipeline input not found"


async def test_nested_psf_photometry_cannot_receive_foreign_default_via_condition(
    app_client,
    db_session,
    test_user,
):
    _attacker, token = test_user
    victim = _victim()
    foreign_key = "private/condition-forwarded-victim.fits"
    db_session.add(victim)
    await db_session.flush()
    db_session.add(
        DataFile(
            user_id=victim.id,
            source="upload",
            object_id="condition-forwarded-private",
            fits_path=foreign_key,
            metadata_={},
        )
    )
    await db_session.commit()

    # Every root receives input_data_id as fits_path/path.  Condition preserves
    # those fields, so a nested reader must cause the original capability to be
    # owner-authorized even though the reader is not itself a root node.
    response = await app_client.post(
        "/api/pipeline/run?async_mode=false",
        json={
            "dag": {
                "nodes": [
                    {
                        "id": "condition",
                        "type": "Condition",
                        "data": {"params": {"expression": "True"}},
                    },
                    {
                        "id": "photometry",
                        "type": "PSFPhotometry",
                        "data": {"params": {}},
                    },
                ],
                "edges": [
                    {
                        "id": "condition-photometry",
                        "source": "condition",
                        "target": "photometry",
                    }
                ],
            },
            "input_data_id": foreign_key,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Pipeline input not found"


@pytest.mark.asyncio
async def test_non_storage_dag_keeps_opaque_default_unresolved():
    from app.pipeline.storage_auth import bind_pipeline_storage_inputs

    resolved: list[str] = []

    async def resolve_key(path: str) -> str:
        resolved.append(path)
        return f"owned/{path}"

    dag = {
        "nodes": [
            {
                "id": "query",
                "type": "QueryData",
                "data": {"params": {"query": "M31", "sources": "simbad"}},
            }
        ],
        "edges": [],
    }
    bound, default = await bind_pipeline_storage_inputs(
        dag=dag,
        input_data_id="opaque-catalog-request",
        resolve_key=resolve_key,
    )

    assert resolved == []
    assert default == "opaque-catalog-request"
    assert bound == dag


@pytest.mark.asyncio
async def test_nested_reader_authorizes_default_without_rewriting_its_params():
    from app.pipeline.storage_auth import bind_pipeline_storage_inputs

    resolved: list[str] = []

    async def resolve_key(path: str) -> str:
        resolved.append(path)
        return f"owned/{path}"

    dag = {
        "nodes": [
            {
                "id": "condition",
                "type": "Condition",
                "data": {"params": {"expression": "True"}},
            },
            {
                "id": "photometry",
                "type": "PSFPhotometry",
                "data": {"params": {}},
            },
        ],
        "edges": [
            {
                "id": "condition-photometry",
                "source": "condition",
                "target": "photometry",
            }
        ],
    }
    bound, default = await bind_pipeline_storage_inputs(
        dag=dag,
        input_data_id="private/input.fits",
        resolve_key=resolve_key,
    )

    assert resolved == ["private/input.fits"]
    assert default == "owned/private/input.fits"
    photometry = next(
        node for node in bound["nodes"] if node["id"] == "photometry"
    )
    assert photometry["data"]["params"] == {}


def test_pipeline_cache_is_partitioned_by_owner_and_root_capability(monkeypatch):
    from app.pipeline import engine

    cache: dict[str, dict] = {}

    def cache_get(key: str):
        value = cache.get(key)
        return deepcopy(value) if value is not None else None

    def cache_set(key: str, value: dict, ttl=None):
        cache[key] = deepcopy(value)

    monkeypatch.setattr(engine, "_cache_get_sync", cache_get)
    monkeypatch.setattr(engine, "_cache_set_sync", cache_set)
    monkeypatch.setattr(engine, "_capture_environment", lambda: {})
    monkeypatch.setattr(engine, "_publish_progress", lambda *args, **kwargs: None)

    dag = {
        "nodes": [
            {
                "id": "condition",
                "type": "Condition",
                "data": {"params": {"expression": "True"}},
            }
        ],
        "edges": [],
    }

    victim = engine.execute_dag(
        deepcopy(dag),
        "private/victim.fits",
        "victim-run",
        "victim-owner",
    )
    attacker = engine.execute_dag(
        deepcopy(dag),
        "private/attacker.fits",
        "attacker-run",
        "attacker-owner",
    )
    same_owner_other_input = engine.execute_dag(
        deepcopy(dag),
        "private/victim-other.fits",
        "victim-other-run",
        "victim-owner",
    )
    victim_repeat = engine.execute_dag(
        deepcopy(dag),
        "private/victim.fits",
        "victim-repeat-run",
        "victim-owner",
    )

    assert victim["condition"]["fits_path"] == "private/victim.fits"
    assert attacker["condition"]["fits_path"] == "private/attacker.fits"
    assert "_cached" not in attacker["condition"]
    assert (
        same_owner_other_input["condition"]["fits_path"]
        == "private/victim-other.fits"
    )
    assert "_cached" not in same_owner_other_input["condition"]
    assert victim_repeat["condition"]["fits_path"] == "private/victim.fits"
    assert victim_repeat["condition"]["_cached"] is True


async def test_import_fits_alias_cannot_shadow_engine_default_path(
    app_client,
    db_session,
    test_user,
):
    attacker, token = test_user
    victim = _victim()
    own_key = "private/own-workspace.fits"
    foreign_key = "private/import-default-victim.fits"
    db_session.add(victim)
    await db_session.flush()
    db_session.add_all(
        [
            DataFile(
                user_id=attacker.id,
                source="upload",
                object_id="own-workspace",
                fits_path=own_key,
                metadata_={},
            ),
            DataFile(
                user_id=victim.id,
                source="upload",
                object_id="import-default-private",
                fits_path=foreign_key,
                metadata_={},
            ),
        ]
    )
    await db_session.commit()

    response = await app_client.post(
        "/api/pipeline/run?async_mode=false",
        json={
            "dag": {
                "nodes": [
                    {
                        "id": "workspace",
                        "type": "ImportWorkspace",
                        "data": {"params": {"fits_path": own_key}},
                    }
                ],
                "edges": [],
            },
            "input_data_id": foreign_key,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Pipeline input not found"


async def test_ai_pipeline_authorizer_rejects_nested_alias(
    db_session,
    test_user,
):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.services.ai_tools import _authorize_tool_storage_inputs

    user, _token = test_user
    victim = _victim()
    foreign_key = "private/ai-alias-victim.fits"
    db_session.add(victim)
    await db_session.flush()
    db_session.add(
        DataFile(
            user_id=victim.id,
            source="upload",
            object_id="ai-alias-private",
            fits_path=foreign_key,
            metadata_={},
        )
    )
    await db_session.commit()

    hostile = {
        "input_data_id": "opaque-unused-input",
        "dag": {
            "nodes": [
                {
                    "id": "bias",
                    "type": "BiasSubtract",
                    "data": {"params": {"bias_paths": [foreign_key]}},
                }
            ],
            "edges": [],
        },
    }
    original = deepcopy(hostile)
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    with patch("app.models.database.async_session", new=session_factory):
        _safe, error = await _authorize_tool_storage_inputs(
            "run_pipeline", hostile, user_id=str(user.id)
        )
    assert error["error_class"] == "storage_file_not_found"
    assert hostile == original


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
                {
                    "id": "bias",
                    "type": "BiasSubtract",
                    "data": {
                        "params": {
                            "science_fits_path": "owned/science.fits",
                            "bias_paths": ["owned/bias-1.fits", "owned/bias-2.fits"],
                        }
                    },
                },
                {
                    "id": "dark",
                    "type": "DarkCorrect",
                    "data": {
                        "params": {
                            "science_fits_path": "owned/science.fits",
                            "dark_paths": ["owned/dark.fits"],
                        }
                    },
                },
                {
                    "id": "flat",
                    "type": "FlatField",
                    "data": {
                        "params": {
                            "science_fits_path": "owned/science.fits",
                            "flat_paths": ["owned/flat.fits"],
                        }
                    },
                },
                {
                    "id": "reproject",
                    "type": "Reproject",
                    "data": {"params": {"target_wcs_fits": "owned/wcs.fits"}},
                },
                {
                    "id": "mosaic",
                    "type": "Mosaic",
                    "data": {
                        "params": {
                            "fits_paths": ["owned/mosaic-a.fits", "owned/mosaic-b.fits"]
                        }
                    },
                },
            ],
            "edges": [],
        },
    )
    expected = {
        "owned/default.fits",
        "owned/table.csv",
        "owned/science.fits",
        "owned/bias-1.fits",
        "owned/bias-2.fits",
        "owned/dark.fits",
        "owned/flat.fits",
        "owned/wcs.fits",
        "owned/mosaic-a.fits",
        "owned/mosaic-b.fits",
    }
    assert _scheduled_storage_keys(schedule) == expected

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

    assert _scheduled_inputs_are_owned(_Session(expected), schedule)
    assert not _scheduled_inputs_are_owned(
        _Session(expected - {"owned/dark.fits"}), schedule
    )

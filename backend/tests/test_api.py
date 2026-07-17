"""Tests for the FastAPI HTTP endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.auth import create_access_token, hash_password
from app.models.schemas import ChatSession, DataFile, PaperDraft, PipelineRun, RunResult, User
from app.utils.usernames import username_from_email


class TestHealthEndpoint:
    async def test_health_returns_200(self, app_client):
        resp = await app_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestAuthEndpoints:
    async def test_register_creates_user(self, app_client):
        resp = await app_client.post(
            "/api/auth/register",
            json={"username": "newastro", "password": "longpassword123"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        me = await app_client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.status_code == 200
        assert me.json()["username"] == "newastro"

    async def test_register_duplicate_username(self, app_client):
        payload = {"username": "dupastro", "password": "longpassword123"}
        await app_client.post("/api/auth/register", json=payload)
        resp = await app_client.post("/api/auth/register", json=payload)
        assert resp.status_code == 400
        assert "already registered" in resp.json()["detail"]

    async def test_register_short_password(self, app_client):
        resp = await app_client.post(
            "/api/auth/register",
            json={"username": "shortastro", "password": "abc"},
        )
        assert resp.status_code == 400

    async def test_login_correct_credentials(self, app_client):
        username, password = "loginastro", "securepassword123"
        await app_client.post(
            "/api/auth/register",
            json={"username": username, "password": password},
        )
        resp = await app_client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_login_wrong_password(self, app_client):
        username = "wrongastro"
        await app_client.post(
            "/api/auth/register",
            json={"username": username, "password": "correctpassword1"},
        )
        resp = await app_client.post(
            "/api/auth/login",
            json={"username": username, "password": "wrongpassword99"},
        )
        assert resp.status_code == 401

    async def test_login_nonexistent_user(self, app_client):
        resp = await app_client.post(
            "/api/auth/login",
            json={"username": "ghostastro", "password": "whatever123"},
        )
        assert resp.status_code == 401


class TestChatSessionPrivacy:
    async def _register_headers(self, app_client, username: str) -> dict[str, str]:
        resp = await app_client.post(
            "/api/auth/register",
            json={"username": username, "password": "longpassword123"},
        )
        assert resp.status_code == 201
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    async def test_sessions_are_scoped_to_current_account(self, app_client):
        owner_headers = await self._register_headers(app_client, "chatowner")
        other_headers = await self._register_headers(app_client, "chatother")

        save_resp = await app_client.post(
            "/api/chat/sessions/save",
            json={
                "title": "Private session",
                "messages": [{"role": "user", "content": "private science note"}],
            },
            headers=owner_headers,
        )
        assert save_resp.status_code == 200
        session_id = save_resp.json()["id"]

        owner_list = await app_client.get("/api/chat/sessions", headers=owner_headers)
        assert owner_list.status_code == 200
        assert [item["id"] for item in owner_list.json()] == [session_id]

        other_list = await app_client.get("/api/chat/sessions", headers=other_headers)
        assert other_list.status_code == 200
        assert other_list.json() == []

        other_get = await app_client.get(f"/api/chat/sessions/{session_id}", headers=other_headers)
        assert other_get.status_code == 404

        other_rename = await app_client.patch(
            f"/api/chat/sessions/{session_id}",
            json={"title": "Stolen"},
            headers=other_headers,
        )
        assert other_rename.status_code == 404

        other_delete = await app_client.delete(f"/api/chat/sessions/{session_id}", headers=other_headers)
        assert other_delete.status_code == 404


class TestPaperDraftPrivacy:
    async def _register_headers(self, app_client, username: str) -> tuple[dict[str, str], str]:
        resp = await app_client.post(
            "/api/auth/register",
            json={"username": username, "password": "longpassword123"},
        )
        assert resp.status_code == 201
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}, resp.json()["access_token"]

    async def test_paper_drafts_are_private_until_published(self, app_client, db_session):
        owner = User(
            id=uuid.uuid4(),
            username="paperowner",
            email="paperowner@example.com",
            password_hash=hash_password("securepassword123"),
            subscription_tier="solo",
        )
        other = User(
            id=uuid.uuid4(),
            username="paperother",
            email="paperother@example.com",
            password_hash=hash_password("securepassword123"),
            subscription_tier="solo",
        )
        session = ChatSession(
            id=uuid.uuid4(),
            user_id=owner.id,
            title="Private paper session",
            messages=[
                {"role": "user", "content": "Draft a catalog result."},
                {
                    "role": "assistant",
                    "content": "The interpretation follows 2020ApJ...900....1S.",
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
        from app.services.server_evidence import build_server_evidence_record

        session.audit_log = [
            build_server_evidence_record(
                session_id=session.id,
                owner_id=owner.id,
                run_id="private-paper-test-run",
                assistant_reply="The interpretation follows 2020ApJ...900....1S.",
                tool_results=[
                    {
                        "tool": "search_objects",
                        "input": {"query": "M31"},
                        "result": {"bibcode": "2020ApJ...900....1S"},
                    }
                ],
            )
        ]
        draft = PaperDraft(
            id=uuid.uuid4(),
            user_id=owner.id,
            session_id=session.id,
            journal_format="aastex",
            paper_json={"title": "Private Draft"},
            latex_source="\\documentclass{aastex631}",
            bibtex="@article{x, title={X}}",
            validation={"overall_status": "PASS", "score": 1.0},
        )
        db_session.add_all([owner, other, session, draft])
        await db_session.commit()
        owner_headers = {"Authorization": f"Bearer {create_access_token(owner.id)}"}
        other_headers = {"Authorization": f"Bearer {create_access_token(other.id)}"}

        owner_list = await app_client.get("/api/paper", headers=owner_headers)
        assert owner_list.status_code == 200
        assert owner_list.json()[0]["paper_json"]["title"] == "Private Draft"
        assert owner_list.json()[0]["is_public"] is False

        other_list = await app_client.get("/api/paper", headers=other_headers)
        assert other_list.status_code == 200
        assert other_list.json() == []

        other_get = await app_client.get(f"/api/paper/{draft.id}", headers=other_headers)
        assert other_get.status_code == 404

        public_before = await app_client.get("/api/paper/public/not-a-real-token")
        assert public_before.status_code == 404

        language_review = await app_client.post(
            f"/api/paper/{draft.id}/language-review",
            json={"confirmed_english": True},
            headers=owner_headers,
        )
        assert language_review.status_code == 200
        assert language_review.json()["validation"]["checks"][1]["status"] == "PASS"

        publish = await app_client.post(f"/api/paper/{draft.id}/publish", headers=owner_headers)
        assert publish.status_code == 200
        assert publish.json()["is_public"] is True
        token = publish.json()["public_token"]

        public_after = await app_client.get(f"/api/paper/public/{token}")
        assert public_after.status_code == 200
        assert public_after.json()["paper_json"]["title"] == "Private Draft"

        unpublish = await app_client.delete(f"/api/paper/{draft.id}/publish", headers=owner_headers)
        assert unpublish.status_code == 200
        assert unpublish.json()["is_public"] is False

        public_revoked = await app_client.get(f"/api/paper/public/{token}")
        assert public_revoked.status_code == 404


class TestDataSearchEndpoint:
    async def test_search_returns_list(self, app_client):
        """Search endpoint should return a list (even if connectors error out)."""
        resp = await app_client.get("/api/data/search", params={"q": "M31"})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestAlertsEndpoint:
    async def test_alerts_list_accepts_no_trailing_slash(self, app_client):
        resp = await app_client.get("/api/alerts")
        assert resp.status_code == 200
        body = resp.json()
        assert "alerts" in body
        assert "count" in body


class TestPipelineEndpoints:
    async def test_list_node_types(self, app_client):
        resp = await app_client.get("/api/pipeline/nodes/types")
        assert resp.status_code == 200
        types = resp.json()
        assert isinstance(types, list)
        assert len(types) > 0
        type_names = {t["type"] for t in types}
        assert "Denoise" in type_names
        assert "SpectralFit" in type_names
        assert "Plot" in type_names
        assert "BiasSubtract" in type_names
        assert "AstrometricSolve" in type_names
        assert "SourceExtract" in type_names

    async def test_list_templates(self, app_client):
        resp = await app_client.get("/api/pipeline/templates")
        assert resp.status_code == 200
        templates = resp.json()
        assert isinstance(templates, list)
        # Built-in templates should be seeded
        assert len(templates) >= 1
        for tpl in templates:
            assert "name" in tpl
            assert "dag" in tpl

    async def test_sync_pipeline_mode_blocks_heavy_async_dispatch(
        self, app_client, test_user, monkeypatch
    ):
        from app.api import pipeline as pipeline_api
        from app.config import settings

        monkeypatch.setattr(settings, "pipeline_mode", "sync")

        def _fail_delay(*_args, **_kwargs):
            raise AssertionError("Celery dispatch should not run in sync mode")

        monkeypatch.setattr(pipeline_api.execute_pipeline_task, "delay", _fail_delay)
        dag = {
            "nodes": [{"id": "stack", "type": "ImageStack", "params": {}}],
            "edges": [],
        }

        _user, token = test_user
        resp = await app_client.post(
            "/api/pipeline/run?async_mode=true",
            json={"dag": dag, "input_data_id": "test.fits"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 503
        assert "heavy nodes" in resp.json()["detail"]


class TestSchedulerEndpoints:
    async def test_scheduler_crud(self, app_client, test_user):
        _, token = test_user
        headers = {"Authorization": f"Bearer {token}"}

        # Create a schedule
        resp = await app_client.post(
            "/api/scheduler/schedules",
            json={
                "name": "Nightly M31 check",
                "dag": {
                    "nodes": [
                        {
                            "id": "query",
                            "type": "QueryData",
                            "data": {"params": {}},
                        }
                    ],
                    "edges": [],
                },
                "input_data_id": "test-data-123",
                "cron_expr": "daily",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        schedule = resp.json()
        schedule_id = schedule["id"]
        assert schedule["name"] == "Nightly M31 check"
        assert schedule["enabled"] is True

        # List schedules
        resp = await app_client.get("/api/scheduler/schedules", headers=headers)
        assert resp.status_code == 200
        schedules = resp.json()
        assert len(schedules) == 1
        assert schedules[0]["id"] == schedule_id

        # Toggle (disable)
        resp = await app_client.patch(
            f"/api/scheduler/schedules/{schedule_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        # Toggle back (enable)
        resp = await app_client.patch(
            f"/api/scheduler/schedules/{schedule_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

        # Delete
        resp = await app_client.delete(
            f"/api/scheduler/schedules/{schedule_id}",
            headers=headers,
        )
        assert resp.status_code == 200

        # Verify deleted
        resp = await app_client.get("/api/scheduler/schedules", headers=headers)
        assert len(resp.json()) == 0

    async def test_scheduler_requires_auth(self, app_client):
        resp = await app_client.get("/api/scheduler/schedules")
        assert resp.status_code == 401


class TestExportEndpoints:
    """Tests for the /api/export/* endpoints."""

    async def _create_pipeline_run(self, db_session, user):
        """Helper: insert a PipelineRun and RunResult into the DB."""
        run_id = uuid.uuid4()
        run = PipelineRun(
            id=run_id,
            user_id=user.id,
            dag={"nodes": [], "edges": []},
            status="completed",
        )
        db_session.add(run)
        await db_session.flush()

        result = RunResult(
            id=uuid.uuid4(),
            run_id=run_id,
            node_id="denoise_1",
            output_path="/output/denoise_1.fits",
            logs="Sigma clip applied, 3 outliers removed",
        )
        db_session.add(result)
        await db_session.commit()
        return run_id

    async def test_export_csv_nonexistent_run(self, app_client, test_user):
        """Should return 404 for a non-existent run_id."""
        _, token = test_user
        headers = {"Authorization": f"Bearer {token}"}
        fake_id = str(uuid.uuid4())
        resp = await app_client.get(f"/api/export/run/{fake_id}/csv", headers=headers)
        assert resp.status_code == 404

    async def test_export_csv_unauthorized(self, app_client):
        """Should return 401 without auth."""
        fake_id = str(uuid.uuid4())
        resp = await app_client.get(f"/api/export/run/{fake_id}/csv")
        assert resp.status_code == 401

    async def test_export_csv_valid_run(self, app_client, test_user, db_session):
        """Create a pipeline run then export CSV; verify text/csv response."""
        user, token = test_user
        run_id = await self._create_pipeline_run(db_session, user)
        headers = {"Authorization": f"Bearer {token}"}

        resp = await app_client.get(f"/api/export/run/{run_id}/csv", headers=headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        body = resp.text
        assert "run_id" in body
        assert "denoise_1" in body

    async def test_export_votable_valid_run(self, app_client, test_user, db_session):
        """Create a pipeline run then export VOTable; verify XML response."""
        user, token = test_user
        run_id = await self._create_pipeline_run(db_session, user)
        headers = {"Authorization": f"Bearer {token}"}

        resp = await app_client.get(
            f"/api/export/run/{run_id}/votable", headers=headers
        )
        assert resp.status_code == 200
        content_type = resp.headers["content-type"]
        assert "xml" in content_type
        body = resp.text
        assert "VOTABLE" in body
        assert "denoise_1" in body

    async def test_export_pdf_valid_run(self, app_client, test_user, db_session):
        """Create a pipeline run then export PDF; verify PDF or HTML fallback."""
        user, token = test_user
        run_id = await self._create_pipeline_run(db_session, user)
        headers = {"Authorization": f"Bearer {token}"}

        resp = await app_client.get(f"/api/export/run/{run_id}/pdf", headers=headers)
        assert resp.status_code == 200
        content_type = resp.headers["content-type"]
        # Endpoint returns PDF if reportlab is installed, HTML otherwise
        assert "application/pdf" in content_type or "text/html" in content_type
        if "text/html" in content_type:
            assert "Pipeline Run Report" in resp.text
            assert "denoise_1" in resp.text

    async def _create_rich_pipeline_run(self, db_session, user):
        """Helper: insert a PipelineRun with a multi-node DAG and results."""
        run_id = uuid.uuid4()
        run = PipelineRun(
            id=run_id,
            user_id=user.id,
            dag={
                "nodes": [
                    {"id": "load_1", "type": "LoadData", "data": {"label": "Load FITS", "params": {"fits_path": "/data/m31.fits"}}},
                    {"id": "denoise_1", "type": "Denoise", "data": {"label": "Sigma Clip", "params": {"sigma": 3}}},
                    {"id": "plot_1", "type": "Plot", "data": {"label": "Preview", "params": {}}},
                ],
                "edges": [
                    {"source": "load_1", "target": "denoise_1"},
                    {"source": "denoise_1", "target": "plot_1"},
                ],
            },
            status="completed",
            results={
                "denoise_1": {"data": {"flux": [1.0, 2.0, 3.0]}, "outliers_removed": 3},
            },
        )
        db_session.add(run)
        await db_session.flush()

        for nid, logs in [("load_1", "Loaded 100x100 image"), ("denoise_1", "Sigma clip applied"), ("plot_1", "Plot saved")]:
            db_session.add(RunResult(
                id=uuid.uuid4(),
                run_id=run_id,
                node_id=nid,
                output_path=f"/output/{nid}.fits",
                logs=logs,
            ))
        await db_session.commit()
        return run_id

    async def test_export_notebook_valid_run(self, app_client, test_user, db_session):
        """Export a multi-node pipeline run as a Jupyter notebook."""
        import json as _json

        user, token = test_user
        run_id = await self._create_rich_pipeline_run(db_session, user)
        headers = {"Authorization": f"Bearer {token}"}

        resp = await app_client.get(f"/api/export/run/{run_id}/notebook", headers=headers)
        assert resp.status_code == 200
        assert "ipynb" in resp.headers["content-type"] or "json" in resp.headers["content-type"]
        assert "attachment" in resp.headers.get("content-disposition", "")

        nb = _json.loads(resp.text)

        # Valid .ipynb structure
        assert nb["nbformat"] == 4
        assert "cells" in nb
        assert nb["metadata"]["kernelspec"]["name"] == "python3"

        cells = nb["cells"]
        # Should have at least: header + deps + 3 nodes * (markdown + code) + summary
        assert len(cells) >= 9

        # First cell is markdown with run metadata
        assert cells[0]["cell_type"] == "markdown"
        src_text = "".join(cells[0]["source"])
        assert "Pipeline Run Report" in src_text
        assert str(run_id) in src_text
        assert "completed" in src_text

        # Second cell is code with imports
        assert cells[1]["cell_type"] == "code"
        assert cells[1]["execution_count"] is None
        assert cells[1]["outputs"] == []
        imports_text = "".join(cells[1]["source"])
        assert "import numpy" in imports_text

        # Last cell is markdown summary
        assert cells[-1]["cell_type"] == "markdown"
        summary_text = "".join(cells[-1]["source"])
        assert "Summary" in summary_text
        assert "Standard Astro" in summary_text

        # Nodes appear in topological order (load_1 before denoise_1 before plot_1)
        all_text = _json.dumps(cells)
        load_pos = all_text.index("load_1")
        denoise_pos = all_text.index("denoise_1")
        plot_pos = all_text.index("plot_1")
        assert load_pos < denoise_pos < plot_pos

        # Node code cells contain type-specific templates
        code_texts = [
            "".join(c["source"]) for c in cells if c["cell_type"] == "code"
        ]
        code_all = "\n".join(code_texts)
        assert "sigma_clip" in code_all  # Denoise template
        assert "matplotlib" in code_all  # Plot template
        assert "astropy.io" in code_all  # LoadData template

        # Results are embedded for denoise_1
        assert "node_output" in code_all
        assert "outliers_removed" in code_all

        # Logs appear in markdown cells
        md_texts = [
            "".join(c["source"]) for c in cells if c["cell_type"] == "markdown"
        ]
        md_all = "\n".join(md_texts)
        assert "Sigma clip applied" in md_all

    async def test_export_notebook_empty_dag(self, app_client, test_user, db_session):
        """Export a run with an empty DAG — should still return valid notebook."""
        import json as _json

        user, token = test_user
        run_id = await self._create_pipeline_run(db_session, user)  # empty DAG
        headers = {"Authorization": f"Bearer {token}"}

        resp = await app_client.get(f"/api/export/run/{run_id}/notebook", headers=headers)
        assert resp.status_code == 200
        nb = _json.loads(resp.text)
        assert nb["nbformat"] == 4
        # At minimum: header + deps + summary = 3 cells
        assert len(nb["cells"]) >= 3

    async def test_export_notebook_nonexistent_run(self, app_client, test_user):
        """Should return 404 for a non-existent run_id."""
        _, token = test_user
        headers = {"Authorization": f"Bearer {token}"}
        fake_id = str(uuid.uuid4())
        resp = await app_client.get(f"/api/export/run/{fake_id}/notebook", headers=headers)
        assert resp.status_code == 404


class TestPaperEndpoints:
    async def _create_chat_session(self, db_session, user):
        session = ChatSession(
            id=uuid.uuid4(),
            user_id=user.id,
            title="M31 draft session",
            messages=[
                {"role": "user", "content": "Study M31 stellar populations."},
                {
                    "role": "assistant",
                    "content": "I searched SIMBAD and summarized the result.",
                    "actions": [
                        {
                            "action": "search",
                            "query": "M31",
                            "sources": ["simbad"],
                            "tool_result": [
                                {"name": "M31", "ra": 10.684, "dec": 41.269, "object_type": "Galaxy"},
                            ],
                        }
                    ],
                },
            ],
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)
        return session

    async def test_validate_session_for_paper(self, app_client, test_user, db_session):
        user, token = test_user
        session = await self._create_chat_session(db_session, user)
        resp = await app_client.post(
            f"/api/paper/validate/{session.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["overall_status"] in {"PASS", "WARN", "FAIL"}
        assert len(body["checks"]) >= 5

    async def test_generate_and_update_paper_draft(self, app_client, test_user, db_session):
        user, token = test_user
        session = await self._create_chat_session(db_session, user)
        headers = {"Authorization": f"Bearer {token}"}

        generate = await app_client.post(
            "/api/paper/generate",
            json={"session_id": str(session.id), "journal_format": "aastex"},
            headers=headers,
        )
        assert generate.status_code == 200
        body = generate.json()
        assert body["paper_json"]["title"]
        assert "\\documentclass" in body["latex_source"]

        paper_id = body["id"]
        updated_json = body["paper_json"]
        updated_json["title"] = "Updated M31 Draft"

        update = await app_client.put(
            f"/api/paper/{paper_id}",
            json={"paper_json": updated_json},
            headers=headers,
        )
        assert update.status_code == 200
        updated = update.json()
        assert updated["paper_json"]["title"] == "Updated M31 Draft"

        tex = await app_client.get(f"/api/paper/{paper_id}/download", headers=headers)
        assert tex.status_code == 200
        assert tex.headers["content-type"].startswith("application/x-tex")

        bib = await app_client.get(f"/api/paper/{paper_id}/bibtex", headers=headers)
        assert bib.status_code == 200
        assert bib.headers["content-type"].startswith("application/x-bibtex")


class TestErrorHandling:
    """Tests for error handling in the data search endpoint."""

    async def test_search_invalid_source(self, app_client):
        """Search with source='nonexistent' should return 400."""
        resp = await app_client.get(
            "/api/data/search",
            params={"q": "M31", "sources": "nonexistent"},
        )
        assert resp.status_code == 400
        assert "Unknown source" in resp.json()["detail"]

    async def test_search_error_includes_error_type(self, app_client):
        """Search with a source that fails should include error_type in the result."""
        mock_connector = AsyncMock()
        mock_connector.search = AsyncMock(
            side_effect=ConnectionError("connection refused")
        )

        with patch(
            "app.api.data.get_connector", return_value=mock_connector
        ), patch(
            "app.api.data.CONNECTORS_KEYS", ["sdss", "gaia", "simbad", "fakesrc"]
        ):
            resp = await app_client.get(
                "/api/data/search",
                params={"q": "M31", "sources": "fakesrc"},
            )

        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 1
        error_result = results[0]
        assert error_result["source"] == "fakesrc"
        assert error_result["object_id"] == "error"
        assert error_result["error_type"] == "connection"
        assert "retries_attempted" in error_result["extra"]


class TestPipelineVersioning:
    """Tests for pipeline template versioning endpoints."""

    _DAG_V1 = {
        "nodes": [
            {"id": "n1", "type": "LoadData", "data": {"label": "Load"}},
            {"id": "n2", "type": "Denoise", "data": {"label": "Denoise", "params": {"sigma": 3.0}}},
        ],
        "edges": [
            {"id": "e1-2", "source": "n1", "target": "n2"},
        ],
    }

    _DAG_V2 = {
        "nodes": [
            {"id": "n1", "type": "LoadData", "data": {"label": "Load"}},
            {"id": "n2", "type": "Denoise", "data": {"label": "Denoise", "params": {"sigma": 5.0}}},
            {"id": "n3", "type": "Plot", "data": {"label": "Plot"}},
        ],
        "edges": [
            {"id": "e1-2", "source": "n1", "target": "n2"},
            {"id": "e2-3", "source": "n2", "target": "n3"},
        ],
    }

    async def _register_and_get_headers(self, app_client):
        """Helper: register a user and return auth headers."""
        import secrets
        username = f"pipeline-test-{secrets.token_hex(4)}"
        resp = await app_client.post("/api/auth/register", json={"username": username, "password": "testpassword123"})
        assert resp.status_code == 201
        token = resp.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    async def _create_template(self, app_client):
        """Helper: save a template and return its ID."""
        headers = await self._register_and_get_headers(app_client)
        resp = await app_client.post(
            "/api/pipeline/save",
            json={"name": "Test Template", "description": "For versioning tests", "dag": self._DAG_V1},
            headers=headers,
        )
        assert resp.status_code == 200
        return resp.json()["id"], headers

    async def _create_version(self, app_client, template_id, dag, change_note, headers):
        """Helper: create a version and return the response JSON."""
        resp = await app_client.post(
            f"/api/pipeline/templates/{template_id}/versions",
            json={"dag": dag, "change_note": change_note},
            headers=headers,
        )
        assert resp.status_code == 200
        return resp.json()

    async def test_save_template_then_create_version(self, app_client, test_user):
        template_id, headers = await self._create_template(app_client)
        version = await self._create_version(
            app_client, template_id, self._DAG_V1, "Initial version", headers
        )

        assert version["version"] == 1
        assert version["change_note"] == "Initial version"
        assert "id" in version
        assert "created_at" in version

    async def test_list_versions(self, app_client, test_user):
        template_id, headers = await self._create_template(app_client)
        await self._create_version(app_client, template_id, self._DAG_V1, "v1", headers)
        await self._create_version(app_client, template_id, self._DAG_V2, "v2", headers)

        resp = await app_client.get(
            f"/api/pipeline/templates/{template_id}/versions",
            headers=headers,
        )
        assert resp.status_code == 200
        versions = resp.json()
        assert len(versions) == 2
        # Newest first
        assert versions[0]["version"] == 2
        assert versions[1]["version"] == 1

    async def test_get_single_version(self, app_client, test_user):
        _, token = test_user
        headers = {"Authorization": f"Bearer {token}"}

        template_id, headers = await self._create_template(app_client)
        version = await self._create_version(
            app_client, template_id, self._DAG_V1, "first", headers
        )
        version_id = version["id"]

        resp = await app_client.get(
            f"/api/pipeline/templates/{template_id}/versions/{version_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["id"] == version_id
        assert detail["version"] == 1
        assert detail["dag"] == self._DAG_V1

    async def test_diff_two_versions(self, app_client, test_user):
        _, token = test_user
        headers = {"Authorization": f"Bearer {token}"}

        template_id, headers = await self._create_template(app_client)
        v1 = await self._create_version(
            app_client, template_id, self._DAG_V1, "v1", headers
        )
        v2 = await self._create_version(
            app_client, template_id, self._DAG_V2, "v2", headers
        )

        resp = await app_client.get(
            f"/api/pipeline/templates/{template_id}/diff",
            params={"v1": v1["id"], "v2": v2["id"]},
            headers=headers,
        )
        assert resp.status_code == 200
        diff = resp.json()

        # n3 (Plot) was added in v2
        added_ids = [n["id"] for n in diff["added_nodes"]]
        assert "n3" in added_ids

        # No nodes were removed
        assert diff["removed_nodes"] == []

        # n2 was modified (sigma changed from 3.0 to 5.0)
        modified_ids = [n["id"] for n in diff["modified_nodes"]]
        assert "n2" in modified_ids

        # Edge e2-3 was added
        added_edge_ids = [e["id"] for e in diff["added_edges"]]
        assert "e2-3" in added_edge_ids

        # No edges removed
        assert diff["removed_edges"] == []

    async def test_version_requires_auth(self, app_client):
        # Create a template with auth
        template_id, headers = await self._create_template(app_client)

        # Attempt to create version WITHOUT auth token
        resp = await app_client.post(
            f"/api/pipeline/templates/{template_id}/versions",
            json={"dag": self._DAG_V1, "change_note": "no auth"},
        )
        assert resp.status_code == 401

        # Attempt to list versions without auth token
        resp = await app_client.get(
            f"/api/pipeline/templates/{template_id}/versions",
        )
        assert resp.status_code == 401

        # Attempt to diff without auth token
        resp = await app_client.get(
            f"/api/pipeline/templates/{template_id}/diff",
            params={
                "v1": "00000000-0000-0000-0000-000000000000",
                "v2": "00000000-0000-0000-0000-000000000001",
            },
        )
        assert resp.status_code == 401


class TestVisualizationEndpoints:
    """Tests for the /api/viz/* endpoints and interactive_plot_node."""

    async def test_list_templates(self, app_client):
        """GET /api/viz/templates returns 7 templates."""
        resp = await app_client.get("/api/viz/templates")
        assert resp.status_code == 200
        templates = resp.json()
        assert isinstance(templates, dict)
        assert len(templates) == 7
        expected_keys = {
            "hr_diagram", "sed_fit", "spectrum_overlay",
            "redshift_histogram", "sky_coverage",
            "correlation_scatter", "corner_plot",
        }
        assert set(templates.keys()) == expected_keys
        for key, tpl in templates.items():
            assert "name" in tpl
            assert "description" in tpl
            assert "required_keys" in tpl

    async def test_generate_hr_diagram(self, app_client):
        """POST /api/viz/generate with hr_diagram data returns plotly JSON."""
        import numpy as np
        np.random.seed(42)
        data = {
            "color_or_teff": np.random.uniform(0.0, 2.0, 50).tolist(),
            "magnitude_or_luminosity": np.random.uniform(0.0, 15.0, 50).tolist(),
        }
        resp = await app_client.post(
            "/api/viz/generate",
            json={"chart_type": "hr_diagram", "data": data},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "plot_json" in body
        assert body["chart_type"] == "hr_diagram"
        plot = body["plot_json"]
        assert "data" in plot
        assert "layout" in plot
        assert len(plot["data"]) >= 1
        # HR diagram should have inverted y-axis for magnitudes > 10
        assert plot["layout"]["yaxis"]["autorange"] == "reversed"

    async def test_generate_spectrum(self, app_client):
        """POST /api/viz/generate with spectrum_overlay data returns plotly JSON."""
        import numpy as np
        wavelength = np.linspace(3800, 7200, 100).tolist()
        flux = np.random.uniform(0.5, 1.5, 100).tolist()
        resp = await app_client.post(
            "/api/viz/generate",
            json={
                "chart_type": "spectrum_overlay",
                "data": {"wavelength": wavelength, "flux": flux},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "plot_json" in body
        assert body["chart_type"] == "spectrum_overlay"
        plot = body["plot_json"]
        assert "data" in plot
        assert len(plot["data"]) >= 1
        assert plot["data"][0]["type"] == "scatter"

    async def test_generate_invalid_type(self, app_client):
        """POST /api/viz/generate with unknown chart_type returns 400."""
        resp = await app_client.post(
            "/api/viz/generate",
            json={"chart_type": "nonexistent_chart", "data": {}},
        )
        assert resp.status_code == 400
        assert "Unknown chart type" in resp.json()["detail"]

    async def test_interactive_plot_node(self, app_client):
        """Test the interactive_plot_node function directly with synthetic data."""
        from app.pipeline.nodes.plot_interactive import interactive_plot_node
        import numpy as np

        np.random.seed(123)
        input_data = {
            "data": {
                "wavelength": np.linspace(4000, 7000, 200).tolist(),
                "flux": np.random.normal(1.0, 0.1, 200).tolist(),
            }
        }
        params = {"chart_type": "spectrum_overlay"}
        result = interactive_plot_node(input_data, params)

        assert "plot_json" in result
        assert result["chart_type"] == "spectrum_overlay"
        assert "data" in result["plot_json"]
        assert "layout" in result["plot_json"]
        assert len(result["plot_json"]["data"]) >= 1

        # Test with correlation scatter
        input_data2 = {
            "data": {
                "x": np.random.uniform(0, 10, 50).tolist(),
                "y": np.random.uniform(0, 10, 50).tolist(),
            }
        }
        params2 = {"chart_type": "correlation_scatter"}
        result2 = interactive_plot_node(input_data2, params2)
        assert "plot_json" in result2
        assert result2["chart_type"] == "correlation_scatter"
        # Should have data trace + fit line
        assert len(result2["plot_json"]["data"]) >= 2

        # Test unknown chart type raises ValueError
        with pytest.raises(ValueError, match="Unknown chart type"):
            interactive_plot_node(input_data, {"chart_type": "bad_type"})


class TestTeamEndpoints:
    """Tests for the team/collaboration API endpoints."""

    async def _register_user(self, app_client, username, password="securepassword123"):
        """Helper: register a user via the API and return (user_id, token, email)."""
        resp = await app_client.post(
            "/api/auth/register",
            json={"username": username, "password": password},
        )
        assert resp.status_code == 201
        token = resp.json()["access_token"]
        # Get user_id via /me
        me_resp = await app_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        profile = me_resp.json()
        return profile["id"], token, profile["email"]

    async def _create_lab_user(self, db_session, email="lab@astro.io"):
        """Helper: insert a lab-tier user directly into the DB and return (user, token)."""
        from app.auth import create_access_token

        user = User(
            id=uuid.uuid4(),
            username=username_from_email(email),
            email=email,
            password_hash=hash_password("securepassword123"),
            subscription_tier="lab",
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        token = create_access_token(user.id)
        return user, token

    async def test_invite_works_during_beta(self, app_client, db_session):
        """During beta, all users can invite (tier check disabled)."""
        # Create users
        _user1_id, user1_token, _ = await self._register_user(app_client, "solo_owner")
        _, _, invitee_email = await self._register_user(app_client, "invitee")
        headers1 = {"Authorization": f"Bearer {user1_token}"}

        # Invite should succeed for any tier during beta
        resp = await app_client.post(
            "/api/team/invite",
            json={"email": invitee_email, "role": "member"},
            headers=headers1,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == invitee_email
        assert body["role"] == "member"

    async def test_invite_and_list_members(self, app_client, db_session):
        """Invite a user, then list members and verify the invited user appears."""
        owner, owner_token = await self._create_lab_user(db_session, "owner@astro.io")
        headers = {"Authorization": f"Bearer {owner_token}"}

        # Register a second user via the API
        _, _, member_email = await self._register_user(app_client, "member")

        # Invite
        resp = await app_client.post(
            "/api/team/invite",
            json={"email": member_email, "role": "member"},
            headers=headers,
        )
        assert resp.status_code == 200

        # List members
        resp = await app_client.get("/api/team/members", headers=headers)
        assert resp.status_code == 200
        members = resp.json()
        assert len(members) == 1
        assert members[0]["email"] == member_email
        assert members[0]["role"] == "member"

    async def test_remove_member(self, app_client, db_session):
        """Invite then remove a member, verify they no longer appear."""
        owner, owner_token = await self._create_lab_user(db_session, "rmowner@astro.io")
        headers = {"Authorization": f"Bearer {owner_token}"}

        _, _, remove_email = await self._register_user(app_client, "toremove")

        # Invite
        resp = await app_client.post(
            "/api/team/invite",
            json={"email": remove_email, "role": "member"},
            headers=headers,
        )
        assert resp.status_code == 200
        member_id = resp.json()["id"]

        # Remove
        resp = await app_client.delete(
            f"/api/team/members/{member_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Verify removed
        resp = await app_client.get("/api/team/members", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 0

    async def test_update_member_role(self, app_client, db_session):
        """Invite with 'member' role, update to 'admin', verify."""
        owner, owner_token = await self._create_lab_user(db_session, "roleowner@astro.io")
        headers = {"Authorization": f"Bearer {owner_token}"}

        _, _, role_email = await self._register_user(app_client, "roleuser")

        # Invite as member
        resp = await app_client.post(
            "/api/team/invite",
            json={"email": role_email, "role": "member"},
            headers=headers,
        )
        assert resp.status_code == 200
        member_id = resp.json()["id"]
        assert resp.json()["role"] == "member"

        # Update to admin
        resp = await app_client.patch(
            f"/api/team/members/{member_id}",
            json={"role": "admin"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "admin"

    async def test_share_pipeline(self, app_client, db_session):
        """Save a template, share it with another user, verify via shared list."""
        owner, owner_token = await self._create_lab_user(db_session, "pipeowner@astro.io")
        headers_owner = {"Authorization": f"Bearer {owner_token}"}

        # Register second user
        user2_id, user2_token, _ = await self._register_user(app_client, "pipeguest")
        headers_guest = {"Authorization": f"Bearer {user2_token}"}

        # Save a pipeline template
        resp = await app_client.post(
            "/api/pipeline/save",
            json={
                "name": "Shared Pipeline",
                "description": "A test pipeline",
                "dag": {"nodes": [], "edges": []},
            },
            headers=headers_owner,
        )
        assert resp.status_code == 200
        template_id = resp.json()["id"]

        # Share with user2
        resp = await app_client.post(
            f"/api/team/pipelines/{template_id}/share",
            json={"user_id": user2_id, "permission": "view"},
            headers=headers_owner,
        )
        assert resp.status_code == 200
        assert resp.json()["template_name"] == "Shared Pipeline"
        assert resp.json()["permission"] == "view"

        # Verify as user2
        resp = await app_client.get(
            "/api/team/pipelines/shared",
            headers=headers_guest,
        )
        assert resp.status_code == 200
        shared = resp.json()
        assert len(shared) == 1
        assert shared[0]["template_id"] == template_id
        assert shared[0]["shared_by_email"] == "pipeowner@astro.io"

    async def test_pipeline_comments(self, app_client, db_session):
        """Add a comment to a pipeline template, list comments, verify."""
        owner, owner_token = await self._create_lab_user(db_session, "cmtowner@astro.io")
        headers = {"Authorization": f"Bearer {owner_token}"}

        # Save a template
        resp = await app_client.post(
            "/api/pipeline/save",
            json={
                "name": "Commented Pipeline",
                "description": "",
                "dag": {"nodes": [], "edges": []},
            },
            headers=headers,
        )
        assert resp.status_code == 200
        template_id = resp.json()["id"]

        # Add a comment
        resp = await app_client.post(
            f"/api/team/pipelines/{template_id}/comments",
            json={"content": "Looks good!"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["content"] == "Looks good!"
        assert resp.json()["email"] == "cmtowner@astro.io"

        # List comments
        resp = await app_client.get(
            f"/api/team/pipelines/{template_id}/comments",
            headers=headers,
        )
        assert resp.status_code == 200
        comments = resp.json()
        assert len(comments) == 1
        assert comments[0]["content"] == "Looks good!"

    async def test_share_dataset(self, app_client, db_session):
        """Create a data file, share it, verify via shared datasets list."""
        owner, owner_token = await self._create_lab_user(db_session, "dsowner@astro.io")
        headers_owner = {"Authorization": f"Bearer {owner_token}"}

        user2_id, user2_token, _ = await self._register_user(app_client, "dsguest")
        headers_guest = {"Authorization": f"Bearer {user2_token}"}

        # Insert a data file directly
        data_file = DataFile(
            id=uuid.uuid4(),
            user_id=owner.id,
            source="sdss",
            object_id="SDSS-J001",
            fits_path="/data/test.fits",
        )
        db_session.add(data_file)
        await db_session.commit()
        await db_session.refresh(data_file)
        file_id = str(data_file.id)

        # Share with user2
        resp = await app_client.post(
            f"/api/team/datasets/{file_id}/share",
            json={"user_id": user2_id},
            headers=headers_owner,
        )
        assert resp.status_code == 200
        assert resp.json()["source"] == "sdss"
        assert resp.json()["object_id"] == "SDSS-J001"

        # Verify as user2
        resp = await app_client.get(
            "/api/team/datasets/shared",
            headers=headers_guest,
        )
        assert resp.status_code == 200
        shared = resp.json()
        assert len(shared) == 1
        assert shared[0]["data_file_id"] == file_id
        assert shared[0]["shared_by_email"] == "dsowner@astro.io"

    async def test_team_requires_auth(self, app_client):
        """All team endpoints should return 401 without a token."""
        endpoints = [
            ("POST", "/api/team/invite"),
            ("GET", "/api/team/members"),
            ("GET", "/api/team/pipelines/shared"),
            ("GET", "/api/team/datasets/shared"),
        ]
        for method, url in endpoints:
            if method == "GET":
                resp = await app_client.get(url)
            else:
                resp = await app_client.post(url, json={})
            assert resp.status_code == 401, f"{method} {url} should require auth"


class TestCollaborationAndMemoryEndpoints:
    async def _create_session(self, db_session):
        user = User(
            id=uuid.uuid4(),
            username="sessionowner",
            email="sessionowner@example.com",
            password_hash=hash_password("securepassword123"),
            subscription_tier="solo",
        )
        db_session.add(user)
        await db_session.flush()
        session = ChatSession(
            id=uuid.uuid4(),
            user_id=user.id,
            title="Shared M31 Session",
            messages=[
                {"role": "user", "content": "Analyze M31"},
                {
                    "role": "assistant",
                    "content": "Loaded archive context following 2020ApJ...900....1S.",
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
        from app.services.server_evidence import build_server_evidence_record

        session.audit_log = [
            build_server_evidence_record(
                session_id=session.id,
                owner_id=user.id,
                run_id="shared-session-test-run",
                assistant_reply="Loaded archive context following 2020ApJ...900....1S.",
                tool_results=[
                    {
                        "tool": "search_objects",
                        "input": {"query": "M31"},
                        "result": {"bibcode": "2020ApJ...900....1S"},
                    }
                ],
            )
        ]
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)
        return user, create_access_token(user.id), session

    async def test_session_share_snapshot_and_research_memory(self, app_client, db_session):
        from sqlalchemy import select

        owner, owner_token, session = await self._create_session(db_session)
        draft = PaperDraft(
            id=uuid.uuid4(),
            user_id=owner.id,
            session_id=session.id,
            journal_format="aastex",
            paper_json={"title": "Initial Draft"},
            latex_source="\\documentclass{aastex631}",
            bibtex="@article{m31, title={M31}}",
            validation={"overall_status": "PASS"},
        )
        db_session.add(draft)
        await db_session.commit()
        collaborator = User(
            id=uuid.uuid4(),
            username="collabuser",
            email="collab@example.com",
            password_hash=hash_password("securepassword123"),
            subscription_tier="solo",
        )
        db_session.add(collaborator)
        await db_session.commit()
        collab_token = create_access_token(collaborator.id)

        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        collab_headers = {"Authorization": f"Bearer {collab_token}"}

        share_resp = await app_client.post(
            f"/api/sessions/{session.id}/share",
            json={"access_level": "fork", "expires_hours": 24},
            headers=owner_headers,
        )
        assert share_resp.status_code == 200
        share_body = share_resp.json()
        assert share_body["share_token"]

        list_resp = await app_client.get(f"/api/sessions/{session.id}/shares", headers=owner_headers)
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1

        shared_resp = await app_client.get(f"/api/shared/{share_body['share_token']}", headers=collab_headers)
        assert shared_resp.status_code == 200
        assert shared_resp.json()["session"]["title"] == "Shared M31 Session"
        assert shared_resp.json()["can_fork"] is True
        assert shared_resp.json()["session"]["paper_drafts"] == []

        language_review = await app_client.post(
            f"/api/paper/{draft.id}/language-review",
            json={"confirmed_english": True},
            headers=owner_headers,
        )
        assert language_review.status_code == 200, language_review.text
        assert language_review.json()["validation"]["checks"][1]["status"] == "PASS"

        publish_resp = await app_client.post(f"/api/paper/{draft.id}/publish", headers=owner_headers)
        assert publish_resp.status_code == 200, publish_resp.text
        assert publish_resp.json()["is_public"] is True
        assert publish_resp.json()["public_url"].startswith("/papers/public/")

        shared_resp = await app_client.get(f"/api/shared/{share_body['share_token']}", headers=collab_headers)
        assert shared_resp.status_code == 200
        assert len(shared_resp.json()["session"]["paper_drafts"]) == 1
        assert shared_resp.json()["session"]["paper_drafts"][0]["paper_json"]["title"] == "Initial Draft"
        shared_validation = shared_resp.json()["session"]["paper_drafts"][0]["validation"]
        assert "evidence_snapshot" not in shared_validation
        assert shared_validation["evidence_snapshot_redacted"] is True

        fork_resp = await app_client.post(f"/api/shared/{share_body['share_token']}/fork", headers=collab_headers)
        assert fork_resp.status_code == 200
        assert fork_resp.json()["forked_from"] == str(session.id)
        forked_id = uuid.UUID(fork_resp.json()["id"])
        forked_drafts = (
            await db_session.execute(select(PaperDraft).where(PaperDraft.session_id == forked_id))
        ).scalars().all()
        assert len(forked_drafts) == 1
        assert forked_drafts[0].paper_json["title"] == "Initial Draft"
        assert forked_drafts[0].validation is None

        snapshot_resp = await app_client.post(
            f"/api/sessions/{session.id}/snapshots",
            json={"name": "before edits"},
            headers=owner_headers,
        )
        assert snapshot_resp.status_code == 200
        snapshot_id = snapshot_resp.json()["id"]

        session.messages = [
            {"role": "user", "content": "Analyze M31"},
            {"role": "assistant", "content": "Loaded Gaia and SDSS context."},
            {"role": "assistant", "content": "Added a new CMD fit."},
        ]
        draft.paper_json = {"title": "Updated Draft"}
        draft.latex_source = "\\documentclass{aastex631}\n% updated"
        await db_session.commit()

        snapshot_resp_2 = await app_client.post(
            f"/api/sessions/{session.id}/snapshots",
            json={"name": "after cmd fit"},
            headers=owner_headers,
        )
        assert snapshot_resp_2.status_code == 200
        snapshot_id_2 = snapshot_resp_2.json()["id"]

        diff_resp = await app_client.get(
            f"/api/sessions/{session.id}/snapshots/diff",
            params={"a": snapshot_id, "b": snapshot_id_2},
            headers=owner_headers,
        )
        assert diff_resp.status_code == 200
        assert diff_resp.json()["added_messages"] >= 1
        assert diff_resp.json()["paper_draft_count"] == {"a": 1, "b": 1}

        profile_resp = await app_client.put(
            "/api/research/profile",
            json={"memory_enabled": True},
            headers=owner_headers,
        )
        assert profile_resp.status_code == 200

        refresh_resp = await app_client.post(
            "/api/research/profile/refresh",
            params={"session_id": str(session.id)},
            headers=owner_headers,
        )
        assert refresh_resp.status_code == 200

        history_resp = await app_client.get("/api/research/history", headers=owner_headers)
        assert history_resp.status_code == 200
        assert len(history_resp.json()) >= 1

        restore_resp = await app_client.post(
            f"/api/sessions/{session.id}/snapshots/{snapshot_id}/restore",
            headers=owner_headers,
        )
        assert restore_resp.status_code == 200
        restored_draft = (
            await db_session.execute(select(PaperDraft).where(PaperDraft.session_id == session.id))
        ).scalar_one()
        assert restored_draft.paper_json["title"] == "Initial Draft"

    async def test_shared_comment_author_can_delete_comment(self, app_client, db_session):
        owner, owner_token, session = await self._create_session(db_session)
        collaborator = User(
            id=uuid.uuid4(),
            username="commenter",
            email="commenter@example.com",
            password_hash=hash_password("securepassword123"),
            subscription_tier="solo",
        )
        db_session.add(collaborator)
        await db_session.commit()

        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        collab_headers = {"Authorization": f"Bearer {create_access_token(collaborator.id)}"}

        share_resp = await app_client.post(
            f"/api/sessions/{session.id}/share",
            json={"access_level": "comment", "expires_hours": 24},
            headers=owner_headers,
        )
        token = share_resp.json()["share_token"]

        add_resp = await app_client.post(
            f"/api/shared/{token}/comments",
            json={"content": "Please check the CMD fit", "target_type": "general"},
            headers=collab_headers,
        )
        assert add_resp.status_code == 200
        comment_id = add_resp.json()["id"]

        shared_resp = await app_client.get(f"/api/shared/{token}", headers=collab_headers)
        assert shared_resp.status_code == 200
        assert shared_resp.json()["comments"][0]["can_delete"] is True

        delete_resp = await app_client.delete(
            f"/api/shared/{token}/comments/{comment_id}",
            headers=collab_headers,
        )
        assert delete_resp.status_code == 200
        assert delete_resp.json()["deleted"] is True


class TestChatMultiAgentRouting:
    async def test_chat_replays_prior_successful_python_actions(self, app_client, test_user):
        from app.services.code_executor import clear_session_vars

        clear_session_vars("chat-replay")
        runtime_session_ids: list[str] = []

        async def fake_run_orchestrated_chat(**kwargs):
            runtime_session_ids.append(kwargs["python_session_id"])
            return {"reply": "ok", "actions": []}

        with patch("app.api.chat._run_orchestrated_chat", new=fake_run_orchestrated_chat):
            resp = await app_client.post(
                "/api/chat/message",
                headers={"Authorization": f"Bearer {test_user[1]}"},
                json={
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "I created a variable.",
                            "actions": [
                                {
                                    "action": "run_python",
                                    "tool_input": {"code": "x = 42"},
                                    "tool_result": {"success": True, "stdout": ""},
                                }
                            ],
                        },
                        {"role": "user", "content": "What is x?"},
                    ],
                    "context": {"python_session_id": "chat-replay"},
                },
            )

        assert resp.status_code == 200
        from app.services.code_executor import execute_python
        assert runtime_session_ids and runtime_session_ids[0] != "chat-replay"
        replay_check = execute_python("print(x)", session_id=runtime_session_ids[0])
        assert replay_check.success
        assert replay_check.stdout.strip() == "42"

    async def test_chat_message_executes_all_classified_agents(self, app_client, test_user):
        calls: list[str] = []

        async def fake_build_runtime(req, user, db):
            return {
                "base_system": "base",
                "system": "base",
                "toolset": [{"name": "search_objects"}, {"name": "run_python"}],
                "agent_names": ["data_agent", "analysis_agent"],
                "user_context": "",
            }

        async def fake_run_agent_loop(*, system, messages, tools, provider_api_keys, agent_name, python_session_id, preferred_backend=None, user_id=None, chat_session_id=None, on_event=None):
            calls.append(agent_name)
            return {
                "reply": f"{agent_name} complete",
                "actions": [{"action": agent_name, "tool_result": {"ok": True}}],
                "tool_results": [],
            }

        with patch("app.api.chat._build_runtime", new=fake_build_runtime), patch(
            "app.api.chat._run_agent_loop",
            new=fake_run_agent_loop,
        ):
            resp = await app_client.post(
                "/api/chat/message",
                headers={"Authorization": f"Bearer {test_user[1]}"},
                json={"messages": [{"role": "user", "content": "Find data and analyze it"}]},
            )

        assert resp.status_code == 200
        assert calls == ["data_agent", "analysis_agent"]
        body = resp.json()
        assert "data_agent complete" in body["reply"]
        assert "analysis_agent complete" in body["reply"]
        assert len(body["actions"]) == 2

    async def test_orchestrator_collapses_data_analysis_plot_fast_path(self):
        from app.ai.orchestrator import orchestrator

        agents, note = orchestrator._collapse_fast_path(
            ["data_agent", "analysis_agent", "visualization_agent"],
            "Query Gaia around Pleiades and plot an HR diagram",
        )

        assert agents == ["analysis_agent"]
        assert note is not None


class TestInferenceAdminEndpoints:
    async def test_admin_inference_config_and_health(self, app_client, db_session, monkeypatch):
        admin = User(
            id=uuid.uuid4(),
            username="astroadmin",
            email="astroadmin@example.com",
            password_hash=hash_password("securepassword123"),
            subscription_tier="admin",
        )
        db_session.add(admin)
        await db_session.commit()
        token = create_access_token(admin.id)
        headers = {"Authorization": f"Bearer {token}"}

        monkeypatch.setenv("ADMIN_USERNAMES", "astroadmin")

        cfg_resp = await app_client.get("/api/admin/inference/config", headers=headers)
        assert cfg_resp.status_code == 200
        assert "routing" in cfg_resp.json()

        update_resp = await app_client.put(
            "/api/admin/inference/config",
            json={"routing": {"analysis_agent": "claude"}},
            headers=headers,
        )
        assert update_resp.status_code == 200

        health_resp = await app_client.get("/api/admin/inference/health", headers=headers)
        assert health_resp.status_code == 200
        assert any(item["backend"] == "claude" for item in health_resp.json()["backends"])

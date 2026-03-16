"""Tests for the FastAPI HTTP endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.auth import hash_password
from app.models.schemas import DataFile, PipelineRun, PipelineTemplateDB, RunResult, User


class TestHealthEndpoint:
    async def test_health_returns_200(self, app_client):
        resp = await app_client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestAuthEndpoints:
    async def test_register_creates_user(self, app_client):
        resp = await app_client.post(
            "/api/auth/register",
            json={"email": "new@astro.io", "password": "longpassword123"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    async def test_register_duplicate_email(self, app_client):
        payload = {"email": "dup@astro.io", "password": "longpassword123"}
        await app_client.post("/api/auth/register", json=payload)
        resp = await app_client.post("/api/auth/register", json=payload)
        assert resp.status_code == 400
        assert "already registered" in resp.json()["detail"]

    async def test_register_short_password(self, app_client):
        resp = await app_client.post(
            "/api/auth/register",
            json={"email": "short@astro.io", "password": "abc"},
        )
        assert resp.status_code == 400

    async def test_login_correct_credentials(self, app_client):
        email, password = "login@astro.io", "securepassword123"
        await app_client.post(
            "/api/auth/register",
            json={"email": email, "password": password},
        )
        resp = await app_client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_login_wrong_password(self, app_client):
        email = "wrong@astro.io"
        await app_client.post(
            "/api/auth/register",
            json={"email": email, "password": "correctpassword1"},
        )
        resp = await app_client.post(
            "/api/auth/login",
            json={"email": email, "password": "wrongpassword99"},
        )
        assert resp.status_code == 401

    async def test_login_nonexistent_user(self, app_client):
        resp = await app_client.post(
            "/api/auth/login",
            json={"email": "ghost@astro.io", "password": "whatever123"},
        )
        assert resp.status_code == 401


class TestDataSearchEndpoint:
    async def test_search_returns_list(self, app_client):
        """Search endpoint should return a list (even if connectors error out)."""
        resp = await app_client.get("/api/data/search", params={"q": "M31"})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


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


class TestSchedulerEndpoints:
    async def test_scheduler_crud(self, app_client, test_user):
        _, token = test_user
        headers = {"Authorization": f"Bearer {token}"}

        # Create a schedule
        resp = await app_client.post(
            "/api/scheduler/schedules",
            json={
                "name": "Nightly M31 check",
                "dag": {"nodes": [], "edges": []},
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

    async def _create_template(self, app_client):
        """Helper: save a template and return its ID."""
        resp = await app_client.post(
            "/api/pipeline/save",
            json={"name": "Test Template", "description": "For versioning tests", "dag": self._DAG_V1},
        )
        assert resp.status_code == 200
        return resp.json()["id"]

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
        _, token = test_user
        headers = {"Authorization": f"Bearer {token}"}

        template_id = await self._create_template(app_client)
        version = await self._create_version(
            app_client, template_id, self._DAG_V1, "Initial version", headers
        )

        assert version["version"] == 1
        assert version["change_note"] == "Initial version"
        assert "id" in version
        assert "created_at" in version

    async def test_list_versions(self, app_client, test_user):
        _, token = test_user
        headers = {"Authorization": f"Bearer {token}"}

        template_id = await self._create_template(app_client)
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

        template_id = await self._create_template(app_client)
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

        template_id = await self._create_template(app_client)
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
        # Create a template (save endpoint allows optional auth)
        resp = await app_client.post(
            "/api/pipeline/save",
            json={"name": "No Auth Template", "description": "test", "dag": self._DAG_V1},
        )
        assert resp.status_code == 200
        template_id = resp.json()["id"]

        # Attempt to create version without auth token
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

    async def _register_user(self, app_client, email, password="securepassword123"):
        """Helper: register a user via the API and return (user_id, token)."""
        resp = await app_client.post(
            "/api/auth/register",
            json={"email": email, "password": password},
        )
        assert resp.status_code == 201
        token = resp.json()["access_token"]
        # Get user_id via /me
        me_resp = await app_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        user_id = me_resp.json()["id"]
        return user_id, token

    async def _create_lab_user(self, db_session, email="lab@astro.io"):
        """Helper: insert a lab-tier user directly into the DB and return (user, token)."""
        from app.auth import create_access_token

        user = User(
            id=uuid.uuid4(),
            email=email,
            password_hash=hash_password("securepassword123"),
            subscription_tier="lab",
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        token = create_access_token(user.id)
        return user, token

    async def test_invite_requires_lab_tier(self, app_client, db_session):
        """Solo-tier user cannot invite; upgrading to lab allows it."""
        from sqlalchemy import update

        # Create a solo-tier user (the inviter)
        user1_id, user1_token = await self._register_user(app_client, "solo_owner@astro.io")
        headers1 = {"Authorization": f"Bearer {user1_token}"}

        # Create a second user to be invited
        await self._register_user(app_client, "invitee@astro.io")

        # Invite should fail with 403 for solo tier
        resp = await app_client.post(
            "/api/team/invite",
            json={"email": "invitee@astro.io", "role": "member"},
            headers=headers1,
        )
        assert resp.status_code == 403

        # Upgrade user to lab tier
        from app.models.schemas import User as UserModel

        await db_session.execute(
            update(UserModel)
            .where(UserModel.id == uuid.UUID(user1_id))
            .values(subscription_tier="lab")
        )
        await db_session.commit()

        # Re-try invite (get_current_user re-fetches from DB so tier is updated)
        resp = await app_client.post(
            "/api/team/invite",
            json={"email": "invitee@astro.io", "role": "member"},
            headers=headers1,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "invitee@astro.io"
        assert body["role"] == "member"

    async def test_invite_and_list_members(self, app_client, db_session):
        """Invite a user, then list members and verify the invited user appears."""
        owner, owner_token = await self._create_lab_user(db_session, "owner@astro.io")
        headers = {"Authorization": f"Bearer {owner_token}"}

        # Register a second user via the API
        await self._register_user(app_client, "member@astro.io")

        # Invite
        resp = await app_client.post(
            "/api/team/invite",
            json={"email": "member@astro.io", "role": "member"},
            headers=headers,
        )
        assert resp.status_code == 200

        # List members
        resp = await app_client.get("/api/team/members", headers=headers)
        assert resp.status_code == 200
        members = resp.json()
        assert len(members) == 1
        assert members[0]["email"] == "member@astro.io"
        assert members[0]["role"] == "member"

    async def test_remove_member(self, app_client, db_session):
        """Invite then remove a member, verify they no longer appear."""
        owner, owner_token = await self._create_lab_user(db_session, "rmowner@astro.io")
        headers = {"Authorization": f"Bearer {owner_token}"}

        await self._register_user(app_client, "toremove@astro.io")

        # Invite
        resp = await app_client.post(
            "/api/team/invite",
            json={"email": "toremove@astro.io", "role": "member"},
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

        await self._register_user(app_client, "roleuser@astro.io")

        # Invite as member
        resp = await app_client.post(
            "/api/team/invite",
            json={"email": "roleuser@astro.io", "role": "member"},
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
        user2_id, user2_token = await self._register_user(app_client, "pipeguest@astro.io")
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

        user2_id, user2_token = await self._register_user(app_client, "dsguest@astro.io")
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

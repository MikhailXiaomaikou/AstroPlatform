"""Security test suite -- authentication, authorization, rate limiting, input validation."""

import uuid

import pytest

from app.auth import create_access_token, hash_password, verify_password
from app.models.schemas import User
from app.utils.usernames import username_from_email


class TestAuthSecurity:
    """Verify that protected endpoints reject unauthenticated / badly-authenticated requests."""

    async def test_protected_endpoint_without_token(self, app_client):
        """Accessing protected endpoints without auth returns 401."""
        protected = [
            "/api/team/members",
            "/api/scheduler/schedules",
        ]
        for path in protected:
            resp = await app_client.get(path)
            assert resp.status_code == 401, f"{path} should require auth, got {resp.status_code}"

    async def test_invalid_jwt_token(self, app_client):
        """Invalid JWT returns 401."""
        headers = {"Authorization": "Bearer invalid.jwt.token"}
        resp = await app_client.get("/api/team/members", headers=headers)
        assert resp.status_code == 401

    async def test_expired_token_format(self, app_client):
        """Malformed bearer tokens are rejected."""
        bad_tokens = [
            "Bearer ",
            "Bearer",
            "Basic dXNlcjpwYXNz",
        ]
        for bad in bad_tokens:
            headers = {"Authorization": bad}
            resp = await app_client.get("/api/team/members", headers=headers)
            assert resp.status_code in (401, 403), (
                f"Authorization '{bad}' should be rejected, got {resp.status_code}"
            )

    async def test_no_auth_header_at_all(self, app_client):
        """Request without Authorization header to protected endpoint returns 401."""
        resp = await app_client.get("/api/scheduler/schedules")
        assert resp.status_code == 401

    async def test_token_for_nonexistent_user(self, app_client):
        """A valid JWT for a user that does not exist in the DB returns 401."""
        fake_id = uuid.uuid4()
        token = create_access_token(fake_id)
        headers = {"Authorization": f"Bearer {token}"}
        resp = await app_client.get("/api/team/members", headers=headers)
        assert resp.status_code == 401

    async def test_valid_token_accepted(self, app_client, test_user):
        """A valid JWT for an existing user grants access."""
        _, token = test_user
        headers = {"Authorization": f"Bearer {token}"}
        resp = await app_client.get("/api/scheduler/schedules", headers=headers)
        assert resp.status_code == 200

    async def test_token_with_bad_sub_claim(self, app_client):
        """A token whose 'sub' claim is not a valid UUID must be rejected."""
        from jose import jwt as jose_jwt
        from app.config import settings

        bad_token = jose_jwt.encode(
            {"sub": "not-a-uuid", "exp": 9999999999},
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        resp = await app_client.get(
            "/api/team/members",
            headers={"Authorization": f"Bearer {bad_token}"},
        )
        assert resp.status_code == 401

    async def test_non_bearer_scheme_rejected(self, app_client):
        """Authorization header with a non-Bearer scheme should not authenticate."""
        resp = await app_client.get(
            "/api/team/members",
            headers={"Authorization": "Token some-value"},
        )
        assert resp.status_code in (401, 403)


class TestInputValidation:
    """Verify that dangerous input is rejected before reaching external services."""

    async def test_adql_drop_blocked(self, app_client):
        """DROP TABLE attempt via ADQL endpoint is blocked."""
        resp = await app_client.post(
            "/api/integration/adql/query",
            json={"query": "DROP TABLE gaiadr3.gaia_source", "service": "gaia"},
        )
        assert resp.status_code == 400
        assert "Forbidden" in resp.json()["detail"] or "DROP" in resp.json()["detail"]

    async def test_adql_delete_blocked(self, app_client):
        """DELETE via ADQL is blocked."""
        resp = await app_client.post(
            "/api/integration/adql/query",
            json={
                "query": "DELETE FROM gaiadr3.gaia_source WHERE 1=1",
                "service": "gaia",
            },
        )
        assert resp.status_code == 400

    async def test_adql_insert_blocked(self, app_client):
        """INSERT via ADQL is blocked."""
        resp = await app_client.post(
            "/api/integration/adql/query",
            json={
                "query": "INSERT INTO gaiadr3.gaia_source VALUES (1)",
                "service": "gaia",
            },
        )
        assert resp.status_code == 400

    async def test_adql_update_blocked(self, app_client):
        """UPDATE via ADQL is blocked."""
        resp = await app_client.post(
            "/api/integration/adql/query",
            json={
                "query": "UPDATE gaiadr3.gaia_source SET ra=0",
                "service": "gaia",
            },
        )
        assert resp.status_code == 400

    async def test_adql_create_blocked(self, app_client):
        """CREATE TABLE via ADQL is blocked."""
        resp = await app_client.post(
            "/api/integration/adql/query",
            json={
                "query": "CREATE TABLE evil (id INT)",
                "service": "gaia",
            },
        )
        assert resp.status_code == 400

    async def test_adql_alter_blocked(self, app_client):
        """ALTER TABLE via ADQL is blocked."""
        resp = await app_client.post(
            "/api/integration/adql/query",
            json={
                "query": "ALTER TABLE gaiadr3.gaia_source ADD COLUMN evil TEXT",
                "service": "gaia",
            },
        )
        assert resp.status_code == 400

    async def test_adql_injection_with_comment(self, app_client):
        """SQL injection via comment suffix with DROP must be blocked."""
        resp = await app_client.post(
            "/api/integration/adql/query",
            json={"query": "SELECT 1; DROP TABLE users; --", "service": "gaia"},
        )
        assert resp.status_code == 400

    async def test_unknown_adql_service_rejected(self, app_client):
        """Unknown ADQL service returns 400."""
        resp = await app_client.post(
            "/api/integration/adql/query",
            json={"query": "SELECT 1", "service": "malicious_service"},
        )
        assert resp.status_code == 400
        assert "Unknown service" in resp.json()["detail"]

    async def test_adql_services_listing(self, app_client):
        """GET /api/integration/adql/services should list known TAP services."""
        resp = await app_client.get("/api/integration/adql/services")
        assert resp.status_code == 200
        services = resp.json()
        service_ids = {s["id"] for s in services}
        assert "gaia" in service_ids
        assert "vizier" in service_ids
        assert "cadc" in service_ids

    async def test_register_short_password_rejected(self, app_client):
        """Registration with a too-short password is rejected."""
        resp = await app_client.post(
            "/api/auth/register",
            json={"username": "hackerman", "password": "abc"},
        )
        assert resp.status_code == 400


class TestSecurityHeaders:
    """Verify that the security-headers middleware is active."""

    async def test_response_has_x_content_type_options(self, app_client):
        """Responses include X-Content-Type-Options: nosniff."""
        resp = await app_client.get("/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    async def test_response_has_x_frame_options(self, app_client):
        """Responses include X-Frame-Options: DENY."""
        resp = await app_client.get("/health")
        assert resp.headers.get("X-Frame-Options") == "DENY"

    async def test_response_has_referrer_policy(self, app_client):
        """Responses include Referrer-Policy with strict-origin."""
        resp = await app_client.get("/health")
        referrer = resp.headers.get("Referrer-Policy", "")
        assert "strict-origin" in referrer

    async def test_response_has_permissions_policy(self, app_client):
        """Responses include Permissions-Policy disabling camera, mic, geolocation."""
        resp = await app_client.get("/health")
        perms = resp.headers.get("Permissions-Policy", "")
        assert "camera=()" in perms
        assert "microphone=()" in perms
        assert "geolocation=()" in perms

    async def test_response_has_xss_protection(self, app_client):
        """Responses include X-XSS-Protection header."""
        resp = await app_client.get("/health")
        assert resp.headers.get("X-XSS-Protection") == "1; mode=block"

    async def test_security_headers_on_error_responses(self, app_client):
        """Security headers are present even on 404 responses."""
        resp = await app_client.get("/nonexistent-path")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"

    async def test_security_headers_on_api_route(self, app_client):
        """Security headers must be present on API routes, not just /health."""
        resp = await app_client.get("/api/data/search", params={"q": "M31"})
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("X-XSS-Protection") == "1; mode=block"


class TestCORS:
    """Verify CORS is configured without wildcard origin."""

    async def test_cors_rejects_unknown_origin(self, app_client):
        """CORS does not return wildcard or echo back an evil origin."""
        resp = await app_client.options(
            "/health",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        allow_origin = resp.headers.get("access-control-allow-origin", "")
        assert allow_origin != "*"
        assert "evil.example.com" not in allow_origin

    async def test_cors_allows_localhost_dev(self, app_client):
        """CORS allows the local dev origin."""
        resp = await app_client.options(
            "/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        allow_origin = resp.headers.get("access-control-allow-origin", "")
        assert allow_origin == "http://localhost:5173"

    async def test_cors_allows_production_origin(self, app_client):
        """A known production origin must be allowed."""
        resp = await app_client.options(
            "/health",
            headers={
                "Origin": "https://astroplatform.pages.dev",
                "Access-Control-Request-Method": "GET",
            },
        )
        allow_origin = resp.headers.get("access-control-allow-origin", "")
        assert allow_origin == "https://astroplatform.pages.dev"

    async def test_cors_allows_authorization_header(self, app_client):
        """Preflight must advertise that the Authorization header is allowed."""
        resp = await app_client.options(
            "/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        allow_headers = resp.headers.get("access-control-allow-headers", "").lower()
        assert "authorization" in allow_headers


class TestRequestSizeLimits:
    """Verify the 1 MB body-size limit for non-upload endpoints."""

    async def test_oversized_body_rejected(self, app_client):
        """A request body larger than 1 MB is rejected with 413."""
        big_payload = "x" * (1_048_576 + 1)
        resp = await app_client.post(
            "/api/integration/adql/query",
            content=big_payload,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413


class TestPasswordHashing:
    """Verify bcrypt password handling in app.auth."""

    def test_hash_produces_bcrypt_format(self):
        """hash_password must return a bcrypt-formatted string."""
        hashed = hash_password("testpassword")
        assert hashed.startswith("$2b$") or hashed.startswith("$2a$")

    def test_hash_is_not_plaintext(self):
        hashed = hash_password("mysecret")
        assert hashed != "mysecret"

    def test_different_passwords_different_hashes(self):
        h1 = hash_password("password1")
        h2 = hash_password("password2")
        assert h1 != h2

    def test_same_password_unique_salts(self):
        """Two calls with the same input should produce different hashes (unique salt)."""
        h1 = hash_password("samepassword")
        h2 = hash_password("samepassword")
        assert h1 != h2

    def test_verify_password_roundtrip(self):
        """verify_password must accept the correct password and reject a wrong one."""
        password = "roundtrip_test_pw"
        hashed = hash_password(password)
        assert verify_password(password, hashed) is True
        assert verify_password("wrong_password", hashed) is False

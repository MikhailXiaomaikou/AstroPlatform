"""Fail-closed contract for arbitrary Python execution.

The bundled executors are useful local stability tools, but they are not an
OS security boundary.  Hosted production must therefore never run them or
silently fall back from subprocess to in-process execution.
"""

from __future__ import annotations


def test_disabled_backend_refuses_execution(monkeypatch):
    from app.config import settings
    from app.services.code_executor import execute_python

    monkeypatch.setattr(settings, "sandbox_backend", "disabled")
    result = execute_python("import pathlib; print(pathlib.Path('/etc/hosts').read_text())")

    assert result.success is False
    assert result.backend == "disabled"
    assert "OS-isolated" in (result.error or "")
    assert result.stdout == ""


def test_subprocess_failure_never_falls_back_to_inprocess(monkeypatch):
    from app.config import settings
    from app.services import code_executor

    monkeypatch.setattr(settings, "sandbox_backend", "subprocess")
    monkeypatch.setattr(code_executor, "_dispatch_subprocess", lambda *args, **kwargs: None)

    result = code_executor.execute_python("print('must not execute')")

    assert result.success is False
    assert result.backend == "disabled"
    assert "refused" in (result.error or "")
    assert result.stdout == ""


def test_production_configuration_rejects_legacy_executors(monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "_ENV", "production")
    for backend in ("inprocess", "subprocess"):
        try:
            config.Settings(
                jwt_secret="test-jwt",
                fernet_key="test-fernet",
                deletion_tombstone_key="deletion-tombstone-key-at-least-32-bytes",
                deletion_tombstone_key_id="deletion-v1",
                signup_mode="closed",
                claim_audit_execution_mode="celery",
                evidence_signing_key="test-evidence-signing-key-32-bytes",
                evidence_signing_key_id="test-v1",
                sandbox_backend=backend,
            )
        except ValueError as exc:
            assert "Production run_python is disabled" in str(exc)
        else:  # pragma: no cover - assertion rendered explicitly for clarity
            raise AssertionError(f"production accepted unsafe backend {backend}")


def test_production_configuration_accepts_disabled(monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "_ENV", "production")
    configured = config.Settings(
        jwt_secret="test-jwt",
        fernet_key="test-fernet",
        deletion_tombstone_key="deletion-tombstone-key-at-least-32-bytes",
        deletion_tombstone_key_id="deletion-v1",
        signup_mode="closed",
        claim_audit_execution_mode="celery",
        evidence_signing_key="test-evidence-signing-key-32-bytes",
        evidence_signing_key_id="test-v1",
        sandbox_backend="disabled",
        privacy_operator_name="Test Operator",
        privacy_contact="privacy@example.invalid",
        privacy_jurisdiction="Test Jurisdiction",
    )
    assert configured.sandbox_backend == "disabled"


def test_production_configuration_rejects_subscription_cli_children(monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "_ENV", "production")
    for field in ("openai_cli_enabled", "claude_cli_enabled"):
        try:
            config.Settings(
                jwt_secret="test-jwt",
                fernet_key="test-fernet",
                deletion_tombstone_key="deletion-tombstone-key-at-least-32-bytes",
                deletion_tombstone_key_id="deletion-v1",
                signup_mode="closed",
                claim_audit_execution_mode="celery",
                evidence_signing_key="test-evidence-signing-key-32-bytes",
                evidence_signing_key_id="test-v1",
                sandbox_backend="disabled",
                **{field: True},
            )
        except ValueError as exc:
            assert "local-only" in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"production accepted local CLI flag {field}")


async def test_disabled_backend_is_not_advertised_as_pipeline_node(monkeypatch):
    from app.api.pipeline import list_node_types
    from app.config import settings

    monkeypatch.setattr(settings, "sandbox_backend", "disabled")
    node_types = await list_node_types()
    assert "CustomScript" not in {node["type"] for node in node_types}

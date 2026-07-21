"""Static safety contracts for protected Registry activation automation."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ACTIVATION = ROOT / ".github/workflows/foundry-registry-activation.yml"
ACTIVATION_PR = ROOT / ".github/workflows/foundry-registry-activation-pr.yml"
RENDER = ROOT / "render.yaml"


def _service_block(text: str, name: str) -> str:
    marker = f"    name: {name}\n"
    start = text.index(marker)
    end = text.find("\n  - type:", start)
    return text[start:] if end < 0 else text[start:end]


def test_activation_uses_exact_render_create_and_poll_endpoints():
    text = ACTIVATION.read_text(encoding="utf-8")
    assert '"commitId": os.environ["ACTIVATION_COMMIT"]' in text
    assert '"https://api.render.com/v1/services/${service_id}/deploys")' in text
    assert (
        '"https://api.render.com/v1/services/${service_id}/deploys/${deploy_id}")'
        in text
    )
    assert 'commit != os.environ["ACTIVATION_COMMIT"]' in text
    assert 'test "$(git rev-parse origin/main)" = "$ACTIVATION_COMMIT"' in text
    assert "render_api_exact_commit" in text
    assert "/preflight" in text
    assert text.index("/preflight") < text.index(
        '"https://api.render.com/v1/services/${service_id}/deploys")'
    )


def test_activation_environments_never_receive_registry_private_key():
    for path in (ACTIVATION, ACTIVATION_PR):
        text = path.read_text(encoding="utf-8")
        assert "WORKFLOW_REGISTRY_SIGNING_PRIVATE_KEY" not in text
        assert "pull_request_target" not in text
    assert "FOUNDRY_REGISTRY_ACTIVATION_RESULT_SECRET" in (
        ACTIVATION.read_text(encoding="utf-8")
    )
    assert "FOUNDRY_REGISTRY_ACTIVATION_RESULT_SECRET" in (
        ACTIVATION_PR.read_text(encoding="utf-8")
    )


def test_activation_is_manual_and_import_is_not_reported_as_active():
    activation = ACTIVATION.read_text(encoding="utf-8")
    proposal = ACTIVATION_PR.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in activation
    assert "ACTIVATION_READY" in activation
    assert "/status" in activation
    assert "/confirm" in activation
    assert "protected_commit_and_fresh_process" not in proposal
    assert "/bundle" in proposal
    assert "git add backend/app/registry_releases" in proposal


def test_render_control_roles_disable_auto_deploy_before_activation():
    text = RENDER.read_text(encoding="utf-8")
    for service in (
        "standard-astro-backend",
        "standard-astro-celery-worker",
        "standard-astro-celery-beat",
    ):
        block = _service_block(text, service)
        assert "    autoDeployTrigger: off\n" in block

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app.services.foundry_ci_dispatch import (
    FoundryCIDispatchConfig,
    FoundryCIDispatchError,
    dispatch_candidate_validation,
    dispatch_formal_worker_build,
)


def _config() -> FoundryCIDispatchConfig:
    return FoundryCIDispatchConfig(
        repository="standard-astro/platform",
        ref="main",
        token="github-actions-token-for-tests",
    )


@pytest.mark.asyncio
async def test_validation_dispatch_contains_only_server_ids() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        seen["payload"] = json.loads(request.content)
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await dispatch_candidate_validation(
            _config(),
            candidate_key="desi_dr2_official_chain_summary_v1",
            validation_run_id="11111111-1111-4111-8111-111111111111",
            client=client,
        )

    assert seen["authorization"] == "Bearer github-actions-token-for-tests"
    assert seen["payload"] == {
        "ref": "main",
        "inputs": {
            "candidate": "desi_dr2_official_chain_summary_v1",
            "validation_run_id": "11111111-1111-4111-8111-111111111111",
        },
    }


@pytest.mark.asyncio
async def test_formal_build_dispatch_binds_exact_version() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(204)

    candidate_id = uuid.uuid4()
    version_id = uuid.uuid4()
    version_hash = "a" * 64
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await dispatch_formal_worker_build(
            _config(),
            candidate_id=candidate_id,
            candidate_version_id=version_id,
            candidate_version_hash=version_hash,
            client=client,
        )

    assert seen["payload"] == {
        "ref": "main",
        "inputs": {
            "candidate_id": str(candidate_id),
            "candidate_version_id": str(version_id),
            "candidate_version_hash": version_hash,
        },
    }


@pytest.mark.asyncio
async def test_dispatch_rejects_untrusted_identifiers_before_network() -> None:
    with pytest.raises(FoundryCIDispatchError, match="candidate_key_invalid"):
        await dispatch_candidate_validation(
            _config(),
            candidate_key="../../secret",
            validation_run_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_dispatch_failure_is_secret_free_and_classified() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="token=should-not-leak")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FoundryCIDispatchError) as raised:
            await dispatch_candidate_validation(
                _config(),
                candidate_key="desi_dr2_official_chain_summary_v1",
                validation_run_id=uuid.uuid4(),
                client=client,
            )

    assert raised.value.code == "foundry_ci_dispatch_rejected"
    assert raised.value.retryable is True
    assert "token" not in str(raised.value)

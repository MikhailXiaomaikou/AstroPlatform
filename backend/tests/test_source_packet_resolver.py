from __future__ import annotations

import asyncio
import gzip
import io
import json
import tarfile
from typing import Any

import pytest

from app.services import source_packet_resolver as resolver
from app.services.source_packet_resolver import (
    SourceResolutionError,
    match_expected_claims,
    normalize_source,
    resolve_source,
    resolve_sources,
)


class MemoryBackend:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key: str) -> Any:
        return self.values.get(key)

    def set(self, key: str, value: Any, ttl: int) -> None:
        self.values[key] = value
        self.ttls[key] = ttl


def _source(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "desi-dr2",
        "kind": "arxiv",
        "identifier": "https://arxiv.org/abs/2503.14738v2",
        "locator": "Table 4, LRG2",
        **overrides,
    }


def _claims() -> list[dict[str, Any]]:
    return [
        {
            "id": "D_M",
            "label": "D_M",
            "value": 17.351,
            "standard_uncertainty": 0.177,
            "source_ref": "desi-dr2",
        },
        {
            "id": "D_H",
            "label": "D_H",
            "value": 19.455,
            "standard_uncertainty": 0.330,
            "source_ref": "desi-dr2",
        },
    ]


def _document(*, second_value: str = "19.455 +/- 0.330") -> dict[str, Any]:
    return {
        "final_url": "https://ar5iv.labs.arxiv.org/html/2503.14738",
        "mime": "text/html",
        "sha256": "a" * 64,
        "extraction_method": "ar5iv_html",
        "text": "",
        "tables": [
            {
                "label": "Table 4",
                "caption": "BAO measurements",
                "columns": ["Tracer", "D_M", "D_H"],
                "rows": [["LRG2", "17.351 +/- 0.177", second_value]],
            }
        ],
    }


def test_source_identifier_normalization() -> None:
    arxiv = normalize_source(_source())
    doi = normalize_source(
        _source(id="act", kind="doi", identifier="https://doi.org/10.1234/example")
    )
    zenodo = normalize_source(
        _source(id="supp", kind="zenodo", identifier="10.5281/zenodo.123456")
    )

    assert arxiv.identifier == "2503.14738"
    assert doi.identifier == "10.1234/example"
    assert doi.url == "https://doi.org/10.1234/example"
    assert zenodo.identifier == "123456"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/data",
        "https://localhost/data",
        "https://127.0.0.1/data",
        "https://169.254.169.254/latest/meta-data",
        "https://user:pass@example.com/data",
    ],
)
def test_url_sources_block_non_https_and_ssrf_targets(url: str) -> None:
    with pytest.raises(SourceResolutionError):
        normalize_source(_source(kind="url", identifier=url))


@pytest.mark.asyncio
async def test_dns_allows_managed_proxy_synthetic_range_but_blocks_private(
    monkeypatch,
) -> None:
    def synthetic(*_args, **_kwargs):
        return [(None, None, None, None, ("198.18.0.31", 443))]

    monkeypatch.setattr(resolver.socket, "getaddrinfo", synthetic)
    await resolver._require_public_dns("arxiv.org")

    def private(*_args, **_kwargs):
        return [(None, None, None, None, ("10.0.0.1", 443))]

    monkeypatch.setattr(resolver.socket, "getaddrinfo", private)
    with pytest.raises(SourceResolutionError) as exc_info:
        await resolver._require_public_dns("attacker.example")
    assert exc_info.value.code == "ssrf_blocked"


def test_exact_match_requires_labels_and_values_in_same_window() -> None:
    status, detail = match_expected_claims(
        _document(), _claims(), locator="Table 4, LRG2"
    )

    assert status == "verified_exact"
    assert detail["reason"] == "labels_and_values_same_window"


def test_source_value_conflict_is_not_promoted_to_verified() -> None:
    status, detail = match_expected_claims(
        _document(second_value="19.999 +/- 0.330"),
        _claims(),
        locator="Table 4, LRG2",
    )

    assert status == "conflict"
    assert "values" in detail["reason"]


def test_arxiv_source_expansion_is_bounded_and_rejects_corrupt_gzip(
    monkeypatch,
) -> None:
    monkeypatch.setattr(resolver, "_MAX_EXPANDED_BYTES", 32)
    oversized = gzip.compress(b"x" * 64)
    with pytest.raises(SourceResolutionError) as oversized_error:
        resolver._safe_arxiv_source_texts(oversized)
    assert oversized_error.value.code == "expanded_source_too_large"

    with pytest.raises(SourceResolutionError) as corrupt_error:
        resolver._safe_arxiv_source_texts(b"\x1f\x8bcorrupt")
    assert corrupt_error.value.code == "compressed_source_invalid"


def test_arxiv_tar_source_extracts_only_text_members() -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        tex = b"\\begin{table} value 1.0 \\end{table}"
        member = tarfile.TarInfo("paper.tex")
        member.size = len(tex)
        archive.addfile(member, io.BytesIO(tex))
        binary = b"ignored"
        other = tarfile.TarInfo("figure.bin")
        other.size = len(binary)
        archive.addfile(other, io.BytesIO(binary))

    assert resolver._safe_arxiv_source_texts(buffer.getvalue()) == [
        "\\begin{table} value 1.0 \\end{table}"
    ]


@pytest.mark.asyncio
async def test_doi_adapter_accepts_public_pdf(monkeypatch) -> None:
    async def download(*_args, **_kwargs):
        return b"pdf", "https://publisher.example/paper.pdf", "application/pdf"

    monkeypatch.setattr(resolver, "_download", download)
    monkeypatch.setattr(resolver, "_extract_pdf_text", lambda _payload: "x = 1.0")
    source = normalize_source(
        _source(id="doi", kind="doi", identifier="10.1234/example")
    )

    document = await resolver._doi_content_adapter(source)

    assert document["extraction_method"] == "doi_publisher_pdf_text"
    assert document["text"] == "x = 1.0"


@pytest.mark.asyncio
async def test_zenodo_adapter_fetches_official_public_file(monkeypatch) -> None:
    calls: list[str] = []

    async def download(url, **_kwargs):
        calls.append(url)
        if url.endswith("/api/records/123"):
            return (
                json.dumps(
                    {
                        "files": [
                            {
                                "key": "supplement.txt",
                                "links": {
                                    "self": "https://zenodo.org/api/records/123/files/a/content"
                                },
                            }
                        ]
                    }
                ).encode(),
                url,
                "application/json",
            )
        return b"x = 1.0", url, "text/plain"

    monkeypatch.setattr(resolver, "_download", download)
    source = normalize_source(
        _source(id="zenodo", kind="zenodo", identifier="10.5281/zenodo.123")
    )

    document = await resolver._zenodo_file_adapter(source)

    assert len(calls) == 2
    assert document["extraction_method"] == "zenodo_public_text"


@pytest.mark.asyncio
async def test_successful_result_is_cached_for_24_hours(monkeypatch) -> None:
    backend = MemoryBackend()
    calls = 0

    async def adapter(_source_input):
        nonlocal calls
        calls += 1
        return _document()

    monkeypatch.setattr(resolver, "get_backend", lambda: backend)
    monkeypatch.setattr(resolver, "_ADAPTERS", {"arxiv": (adapter,)})

    first = await resolve_source(_source(), _claims())
    second = await resolve_source(_source(), _claims())

    assert first["status"] == "verified_exact"
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["sha256"] == first["sha256"]
    assert calls == 1
    assert next(iter(backend.ttls.values())) == resolver.TTL_METADATA


@pytest.mark.asyncio
async def test_source_document_cache_is_reused_for_different_claims(monkeypatch) -> None:
    backend = MemoryBackend()
    calls = 0

    async def adapter(_source_input):
        nonlocal calls
        calls += 1
        return _document()

    monkeypatch.setattr(resolver, "get_backend", lambda: backend)
    monkeypatch.setattr(resolver, "_ADAPTERS", {"arxiv": (adapter,)})

    first = await resolve_source(_source(), _claims())
    changed_claims = [dict(claim) for claim in _claims()]
    changed_claims[0]["value"] = 17.999
    second = await resolve_source(_source(), changed_claims)

    assert first["status"] == "verified_exact"
    assert second["status"] == "conflict"
    assert second["cache_hit"] is True
    assert calls == 1


@pytest.mark.asyncio
async def test_transient_adapter_failure_retries_once_and_uses_short_cache(
    monkeypatch,
) -> None:
    backend = MemoryBackend()
    calls = 0

    async def unavailable(_source_input):
        nonlocal calls
        calls += 1
        raise SourceResolutionError(
            "temporary", code="source_network_error", retryable=True
        )

    monkeypatch.setattr(resolver, "get_backend", lambda: backend)
    monkeypatch.setattr(resolver, "_ADAPTERS", {"arxiv": (unavailable,)})

    result = await resolve_source(_source(), _claims())

    assert result["status"] == "unavailable"
    assert result["error_class"] == "source_network_error"
    assert calls == 2
    assert next(iter(backend.ttls.values())) == resolver._FAILURE_TTL_SECONDS


@pytest.mark.asyncio
async def test_user_supplied_source_never_self_verifies(monkeypatch) -> None:
    backend = MemoryBackend()
    monkeypatch.setattr(resolver, "get_backend", lambda: backend)

    result = await resolve_sources(
        [
            {
                "id": "manual",
                "kind": "user_supplied",
                "identifier": "pasted table",
                "locator": "row 1",
            }
        ],
        [
            {
                "id": "x",
                "label": "x",
                "value": 1.0,
                "standard_uncertainty": 0.1,
                "source_ref": "manual",
            }
        ],
    )

    assert result[0]["status"] == "user_supplied_unverified"


@pytest.mark.asyncio
async def test_total_fetch_timeout_returns_unavailable_without_losing_inputs(
    monkeypatch,
) -> None:
    async def slow(_source_input):
        await asyncio.sleep(0.05)
        return _document()

    monkeypatch.setattr(resolver, "get_backend", lambda: MemoryBackend())
    monkeypatch.setattr(resolver, "_ADAPTERS", {"arxiv": (slow,)})
    monkeypatch.setattr(resolver, "_TOTAL_TIMEOUT_SECONDS", 0.001)

    result = await resolve_sources([_source()], _claims())

    assert result[0]["status"] == "unavailable"
    assert result[0]["error_class"] == "source_budget_exhausted"
    assert result[0]["identifier"] == _source()["identifier"]


@pytest.mark.asyncio
async def test_exact_adapter_match_wins_over_faster_conflicting_adapter(
    monkeypatch,
) -> None:
    async def conflict_first(_source_input):
        return _document(second_value="19.999 +/- 0.330")

    async def exact_later(_source_input):
        await asyncio.sleep(0.005)
        return _document()

    monkeypatch.setattr(resolver, "get_backend", lambda: MemoryBackend())
    monkeypatch.setattr(
        resolver, "_ADAPTERS", {"arxiv": (conflict_first, exact_later)}
    )

    result = await resolve_source(_source(), _claims())

    assert result["status"] == "verified_exact"


@pytest.mark.asyncio
async def test_no_more_than_two_source_adapters_run_concurrently(monkeypatch) -> None:
    active = 0
    maximum_active = 0

    async def adapter(_source_input):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.005)
        active -= 1
        return {**_document(), "tables": [], "text": "no matching values"}

    monkeypatch.setattr(resolver, "get_backend", lambda: MemoryBackend())
    monkeypatch.setattr(
        resolver, "_ADAPTERS", {"arxiv": (adapter, adapter, adapter)}
    )

    await resolve_source(_source(), _claims())

    assert maximum_active == 2


def test_rounded_substring_value_must_not_verify_exact() -> None:
    # Codex review P1 (PR #46): supplied 17.35/0.17 must not match the
    # paper's 17.351/0.177 via bare substring containment and gain
    # verified_exact, which would let truncated inputs be attributed to the
    # paper as independently matched.
    claims = _claims()
    claims[0]["value"] = 17.35
    claims[0]["standard_uncertainty"] = 0.17

    status, _detail = match_expected_claims(
        _document(), claims, locator="Table 4, LRG2"
    )

    assert status != "verified_exact"


def test_missing_requested_locator_must_not_verify_from_document_head() -> None:
    # Codex review P1 (PR #46): when a specific locator is requested but not
    # found, the document-head fallback windows must not grant verified_exact
    # for that locator — the requested table/equation was never checked.
    document = {
        "final_url": "https://ar5iv.labs.arxiv.org/html/2503.14738",
        "mime": "text/html",
        "sha256": "a" * 64,
        "extraction_method": "ar5iv_html",
        "tables": [],
        "text": (
            "In the abstract we quote D_M = 17.351 +/- 0.177 and "
            "D_H = 19.455 +/- 0.330 for the combined sample."
        ),
    }

    status, _detail = match_expected_claims(
        document, _claims(), locator="Table 9, QSO"
    )

    assert status != "verified_exact"

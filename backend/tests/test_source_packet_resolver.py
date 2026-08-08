from __future__ import annotations

import asyncio
import gzip
import io
import json
import ssl
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


class _FakePinnedHTTPStream:
    def __init__(self, events: dict[str, Any]) -> None:
        self.events = events
        self.response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: 2\r\n"
            b"Connection: close\r\n\r\nok"
        )

    async def read(self, _max_bytes: int, timeout=None) -> bytes:
        payload, self.response = self.response, b""
        return payload

    async def write(self, buffer: bytes, timeout=None) -> None:
        self.events.setdefault("writes", []).append(bytes(buffer))

    async def aclose(self) -> None:
        self.events["closed"] = True

    async def start_tls(
        self, ssl_context, server_hostname=None, timeout=None
    ) -> _FakePinnedHTTPStream:
        self.events["sni"] = server_hostname
        self.events["check_hostname"] = ssl_context.check_hostname
        self.events["verify_mode"] = ssl_context.verify_mode
        return self

    def get_extra_info(self, _info: str) -> None:
        return None


class _FakePinnedNetworkBackend:
    def __init__(self, events: dict[str, Any]) -> None:
        self.events = events

    async def connect_tcp(self, host, port, **_kwargs):
        self.events["tcp"] = (host, port)
        return _FakePinnedHTTPStream(self.events)

    async def connect_unix_socket(self, *_args, **_kwargs):
        raise AssertionError("Unix socket must not be used")

    async def sleep(self, _seconds: float) -> None:
        return None


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

    assert arxiv.identifier == "2503.14738v2"
    assert arxiv.url == "https://arxiv.org/abs/2503.14738v2"
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
        "https://Ｘ.com/data",
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_url", "expected_hostname"),
    [
        ("https://attacker.example/data", "attacker.example"),
        ("https://faß.de/data", "xn--fa-hia.de"),
    ],
)
async def test_download_connects_to_the_validated_dns_address(
    monkeypatch, source_url: str, expected_hostname: str
) -> None:
    # Codex review P1 (PR #46, round 7): validation and connection previously
    # resolved the hostname separately, so DNS rebinding could swap a public
    # validation answer for a private connection target. The HTTP transport
    # must connect to the exact address returned by the validation lookup.
    validated_address = "198.18.0.31"

    dns_hostnames: list[str] = []

    async def public_dns(hostname: str) -> tuple[str, ...]:
        dns_hostnames.append(hostname)
        return (validated_address,)

    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/plain", "content-length": "2"}

        async def aiter_bytes(self):
            yield b"ok"

    class FakeStream:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *_args):
            return None

    class FakeClient:
        def __init__(self, *, transport=None, **_kwargs):
            assert transport is not None
            captured["transport"] = transport

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, _method, url, **_kwargs):
            captured["url"] = url
            return FakeStream()

    class FakeNetworkBackend:
        def __init__(self) -> None:
            self.hosts: list[str] = []

        async def connect_tcp(self, host, _port, **_kwargs):
            self.hosts.append(host)
            return object()

    monkeypatch.setattr(resolver, "_require_public_dns", public_dns)
    monkeypatch.setattr(resolver.httpx, "AsyncClient", FakeClient)

    payload, final_url, _mime = await resolver._download(
        source_url,
        accept="text/plain",
        allowed_mime_prefixes=("text/",),
    )

    pinned_backend = captured["transport"]._pool._network_backend
    fake_backend = FakeNetworkBackend()
    pinned_backend._backend = fake_backend
    await pinned_backend.connect_tcp(expected_hostname, 443)

    assert payload == b"ok"
    assert final_url == source_url
    assert captured["url"] == final_url
    assert dns_hostnames == [expected_hostname]
    assert fake_backend.hosts == [validated_address]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "validated_address", ["93.184.216.34", "2606:4700:4700::1111"]
)
async def test_pinned_transport_preserves_http_and_tls_origin(
    monkeypatch, validated_address: str
) -> None:
    # Exercise the real HTTPX -> httpcore -> pinned backend path. A hostile
    # environment proxy must not replace the validated TCP destination, while
    # Host, SNI, and certificate verification keep the original hostname.
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    events: dict[str, Any] = {}
    transport = resolver._pinned_https_transport(
        "attacker.example", (validated_address,)
    )
    transport._pool._network_backend._backend = _FakePinnedNetworkBackend(events)

    async with resolver.httpx.AsyncClient(transport=transport, trust_env=True) as client:
        response = await client.get("https://attacker.example/data?q=1")

    request_bytes = b"".join(events["writes"])
    assert response.status_code == 200
    assert response.content == b"ok"
    assert events["tcp"] == (validated_address, 443)
    assert b"GET /data?q=1 HTTP/1.1\r\n" in request_bytes
    assert b"\r\nhost: attacker.example\r\n" in request_bytes.lower()
    assert events["sni"] == "attacker.example"
    assert events["check_hostname"] is True
    assert events["verify_mode"] == ssl.CERT_REQUIRED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("unicode_hostname", "wire_hostname"),
    [
        ("bücher.example", "xn--bcher-kva.example"),
        ("faß.de", "xn--fa-hia.de"),
        ("βόλος.com", "xn--nxasmm1c.com"),
    ],
)
async def test_pinned_transport_normalizes_idn_hostname(
    unicode_hostname: str, wire_hostname: str
) -> None:
    # Internal adversarial review: urllib.parse preserves Unicode hostnames,
    # while HTTPX passes the IDNA ASCII form to the network backend. The pin
    # identity must compare those canonical forms without escaping the normal
    # source-resolution error contract.
    events: dict[str, Any] = {}
    transport = resolver._pinned_https_transport(
        unicode_hostname, ("93.184.216.34",)
    )
    transport._pool._network_backend._backend = _FakePinnedNetworkBackend(events)

    async with resolver.httpx.AsyncClient(transport=transport) as client:
        response = await client.get(f"https://{unicode_hostname}/idn")

    request_bytes = b"".join(events["writes"])
    assert response.status_code == 200
    assert events["tcp"] == ("93.184.216.34", 443)
    assert f"\r\nhost: {wire_hostname}\r\n".encode() in request_bytes.lower()
    assert events["sni"] == wire_hostname


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


def test_repeated_label_after_valid_measurement_still_verifies_exact() -> None:
    # Codex review P2 (PR #46, round 28): binding only the final label mention
    # makes a later narrative reference hide an earlier exact assignment.
    document = {
        "final_url": "https://example.org/paper",
        "mime": "text/html",
        "sha256": "a" * 64,
        "extraction_method": "html",
        "tables": [
            {
                "label": "Table 4",
                "caption": "parameters",
                "columns": [],
                "rows": [["LRG2 alpha = 10 +/- 1. This alpha is used in Section 2."]],
            }
        ],
        "text": "",
    }
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
            "unit": "dimensionless",
        }
    ]

    status, detail = match_expected_claims(
        document, claims, locator="Table 4, LRG2"
    )

    assert status == "verified_exact"
    assert detail["reason"] == "labels_and_values_same_window"


@pytest.mark.parametrize(
    "field",
    [
        "It is not true that alpha = 10 +/- 1.",
        "We cannot conclude that alpha = 10 +/- 1.",
        "The data do not support alpha = 10 +/- 1.",
        "The available evidence does not support alpha = 10 +/- 1.",
        "There is no evidence that alpha = 10 +/- 1.",
        "There is no evidence to support alpha = 10 +/- 1.",
        "No evidence supports alpha = 10 +/- 1.",
        "There is insufficient evidence that alpha = 10 +/- 1.",
        "There is inadequate support for alpha = 10 +/- 1.",
        "There is not enough evidence that alpha = 10 +/- 1.",
        "There was no support for alpha = 10 +/- 1.",
    ],
)
def test_prelabel_proposition_rejection_must_not_verify_exact(field: str) -> None:
    # Codex review P1 (PR #46, round 29): negation can govern the complete
    # proposition from before the label, outside the label-to-value slice.
    document = {
        "final_url": "https://example.org/paper",
        "mime": "text/html",
        "sha256": "a" * 64,
        "extraction_method": "html",
        "tables": [
            {
                "label": "Table 4",
                "caption": "parameters",
                "columns": [],
                "rows": [[f"LRG2 {field}"]],
            }
        ],
        "text": "",
    }
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
            "unit": "dimensionless",
        }
    ]

    status, _detail = match_expected_claims(
        document, claims, locator="Table 4, LRG2"
    )

    assert status != "verified_exact"


def test_affirmed_prelabel_proposition_still_verifies_exact() -> None:
    document = {
        "final_url": "https://example.org/paper",
        "mime": "text/html",
        "sha256": "a" * 64,
        "extraction_method": "html",
        "tables": [
            {
                "label": "Table 4",
                "caption": "parameters",
                "columns": [],
                "rows": [["LRG2 It is true that alpha = 10 +/- 1."]],
            }
        ],
        "text": "",
    }
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
            "unit": "dimensionless",
        }
    ]

    status, _detail = match_expected_claims(
        document, claims, locator="Table 4, LRG2"
    )

    assert status == "verified_exact"


def test_prose_cooccurrence_requires_positive_measurement_syntax() -> None:
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
            "unit": "dimensionless",
        }
    ]

    unrelated, _detail = match_expected_claims(
        {
            "tables": [],
            "text": "We compare alpha against a calibration result of 10 +/- 1.",
        },
        claims,
        locator="",
    )
    structured, _detail = match_expected_claims(
        {
            "tables": [
                {
                    "label": "Table 4",
                    "caption": "parameters",
                    "columns": [],
                    "rows": [["LRG2 alpha 10 +/- 1"]],
                }
            ],
            "text": "",
        },
        claims,
        locator="Table 4, LRG2",
    )

    assert unrelated != "verified_exact"
    assert structured == "verified_exact"


def test_perfect_aspect_measurement_syntax_verifies_exact() -> None:
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
            "unit": "dimensionless",
        }
    ]

    status, _detail = match_expected_claims(
        {"tables": [], "text": "alpha has been measured as 10 +/- 1."},
        claims,
        locator="",
    )

    assert status == "verified_exact"


@pytest.mark.parametrize(
    "text",
    [
        "For the mock catalogue, alpha = 10 +/- 1.",
        "In the simulated data, alpha = 10 +/- 1.",
        "Our fiducial value is alpha = 10 +/- 1.",
        "For the fiducial cosmology, alpha = 10 +/- 1.",
        "For our baseline cosmology, alpha = 10 +/- 1.",
        "For our reference model, alpha = 10 +/- 1.",
        "In the benchmark configuration, alpha = 10 +/- 1.",
        "For our assumed cosmology, alpha = 10 +/- 1.",
        "For the adopted model, alpha = 10 +/- 1.",
        "For our default cosmology, alpha = 10 +/- 1.",
        "For the nominal model, alpha = 10 +/- 1.",
        "Using the fixed configuration, alpha = 10 +/- 1.",
        "For the input cosmology, alpha = 10 +/- 1.",
        "Under the fiducial cosmology, alpha = 10 +/- 1.",
        "Within the baseline model, alpha = 10 +/- 1.",
        "With the fiducial cosmology, alpha = 10 +/- 1.",
        "Given the reference model, alpha = 10 +/- 1.",
        "In the input model, alpha is given as 10 +/- 1.",
        "alpha = 10 +/- 1 was assumed as an input.",
        "alpha = 10 +/- 1 was adopted as the model input.",
        "alpha = 10 +/- 1 was fixed as an input.",
        "alpha = 10 +/- 1 was used as the fiducial value.",
        "alpha = 10 +/- 1 was set as the baseline parameter.",
        "alpha = 10 +/- 1 was taken as the default model input.",
        "alpha = 10 +/- 1 is used as the reference value.",
        "alpha = 10 +/- 1 is held fixed throughout the analysis.",
        "alpha = 10 +/- 1 was kept fixed in the analysis.",
        "We set alpha = 10 +/- 1.",
        "We fix alpha = 10 +/- 1 for the analysis.",
        "We adopt alpha = 10 +/- 1.",
        "We use alpha = 10 +/- 1 as an input.",
        "The analysis fixes alpha = 10 +/- 1.",
        "For this analysis we set alpha = 10 +/- 1.",
        "Within this study the authors adopt alpha = 10 +/- 1.",
        "We choose alpha = 10 +/- 1 for the analysis.",
        "We select alpha = 10 +/- 1 as the input.",
        "The analysis imposes alpha = 10 +/- 1.",
        "We chose alpha = 10 +/- 1 for the analysis.",
        "The authors selected alpha = 10 +/- 1 as the input.",
        "Our model imposed alpha = 10 +/- 1.",
        "We have chosen alpha = 10 +/- 1 for the analysis.",
        "The analysis has selected alpha = 10 +/- 1 as the input.",
        "The model had imposed alpha = 10 +/- 1.",
        "We hold alpha = 10 +/- 1 fixed.",
        "The analysis keeps alpha = 10 +/- 1 fixed.",
        "We held alpha = 10 +/- 1 fixed.",
        "The authors kept alpha = 10 +/- 1 fixed.",
        "The model has held alpha = 10 +/- 1 fixed.",
        "The analysis has kept alpha = 10 +/- 1 fixed.",
        "We adopt a Gaussian prior for alpha = 10 +/- 1.",
        "The analysis uses a normal prior on alpha = 10 +/- 1.",
        "For illustration, alpha = 10 +/- 1.",
    ],
)
def test_configuration_assignments_must_not_verify_exact(text: str) -> None:
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
            "unit": "dimensionless",
        }
    ]

    status, _detail = match_expected_claims(
        {"tables": [], "text": text}, claims, locator=""
    )

    assert status != "verified_exact", text


def test_configuration_context_preserves_explicit_measurement() -> None:
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
            "unit": "dimensionless",
        }
    ]

    status, _detail = match_expected_claims(
        {
            "tables": [],
            "text": (
                "In the best-fitting model, alpha has been measured as "
                "10 +/- 1."
            ),
        },
        claims,
        locator="",
    )

    assert status == "verified_exact"


def test_mock_comparison_does_not_hide_a_real_measurement() -> None:
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
            "unit": "dimensionless",
        }
    ]

    status, _detail = match_expected_claims(
        {
            "tables": [],
            "text": (
                "Unlike the mock catalogue, the observed alpha has been "
                "measured as 10 +/- 1."
            ),
        },
        claims,
        locator="",
    )

    assert status == "verified_exact"


def test_hypothetical_measurement_must_not_verify_exact() -> None:
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
            "unit": "dimensionless",
        }
    ]

    for text in (
        "If alpha were 10 +/- 1, the conclusion would change.",
        "Suppose alpha = 10 +/- 1 for illustration.",
        "Assuming alpha = 10 +/- 1, the model would shift.",
    ):
        status, _detail = match_expected_claims(
            {"tables": [], "text": text}, claims, locator=""
        )
        assert status != "verified_exact", text

    affirmed, _detail = match_expected_claims(
        {"tables": [], "text": "The source reports alpha = 10 +/- 1."},
        claims,
        locator="",
    )
    assert affirmed == "verified_exact"


def test_modal_or_trailing_conditional_measurement_must_not_verify_exact() -> None:
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
            "unit": "dimensionless",
        }
    ]

    for text in (
        "alpha could be 10 +/- 1 under an alternative calibration.",
        "alpha could have been 10 +/- 1 under an alternative calibration.",
        "alpha might have been measured as 10 +/- 1 under another pipeline.",
        "alpha would be 10 +/- 1 if the calibration changed.",
        "alpha = 10 +/- 1 if the calibration changed.",
        "alpha = 10 +/- 1 if anything in the calibration changes.",
    ):
        status, _detail = match_expected_claims(
            {"tables": [], "text": text}, claims, locator=""
        )
        assert status != "verified_exact", text

    affirmed, _detail = match_expected_claims(
        {"tables": [], "text": "alpha is 10 +/- 1."}, claims, locator=""
    )
    assert affirmed == "verified_exact"


def test_if_anything_qualifier_does_not_false_kill_exact_measurement() -> None:
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
            "unit": "dimensionless",
        }
    ]
    for text in (
        "alpha = 10 +/- 1, if anything a conservative estimate.",
        "alpha = 10 +/- 1, if anything, a conservative estimate.",
    ):
        qualified, _detail = match_expected_claims(
            {"tables": [], "text": text}, claims, locator=""
        )
        assert qualified == "verified_exact", text


def test_repeated_label_with_later_conflicting_measurement_stays_closed() -> None:
    # Reverse guard for occurrence-aware matching: a neutral later mention is
    # harmless, but a later numeric field for the same label cannot be ignored.
    document = {
        "final_url": "https://example.org/paper",
        "mime": "text/html",
        "sha256": "a" * 64,
        "extraction_method": "html",
        "tables": [
            {
                "label": "Table 4",
                "caption": "parameters",
                "columns": [],
                "rows": [["LRG2 alpha = 10 +/- 1. Later alpha = 20 +/- 2."]],
            }
        ],
        "text": "",
    }
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
            "unit": "dimensionless",
        }
    ]

    status, _detail = match_expected_claims(
        document, claims, locator="Table 4, LRG2"
    )

    assert status != "verified_exact"


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


def test_pdf_extraction_uses_killable_resource_limited_subprocess(
    monkeypatch,
) -> None:
    # Codex review P1 (PR #46, round 22): cancellation of a worker thread does
    # not terminate pdfminer. The parser must live in a subprocess with hard
    # resource/page/output limits and its own wall-clock timeout.
    calls = []

    def completed(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return resolver.subprocess.CompletedProcess(
            cmd, 0, stdout=b"alpha = 10 +/- 1", stderr=b""
        )

    monkeypatch.setattr(resolver.subprocess, "run", completed)

    assert resolver._extract_pdf_text(b"%PDF-safe") == "alpha = 10 +/- 1"
    cmd, kwargs = calls[0]
    assert cmd[:2] == [resolver.sys.executable, "-I"]
    assert "setrlimit" in cmd[-1]
    assert "PDF_PAGE_LIMIT" in cmd[-1]
    assert kwargs["timeout"] == resolver._PDF_EXTRACTION_TIMEOUT_SECONDS
    assert kwargs["start_new_session"] is True

    def timed_out(cmd, **kwargs):
        raise resolver.subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(resolver.subprocess, "run", timed_out)
    with pytest.raises(SourceResolutionError) as timeout_error:
        resolver._extract_pdf_text(b"%PDF-pathological")
    assert timeout_error.value.code == "pdf_text_timeout"


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


def test_partial_compound_locator_must_not_verify_from_other_regions() -> None:
    # Codex review P1 (PR #46, round 2): with locator "Table 4, LRG2", an
    # occurrence of "LRG2" elsewhere populated locator_indices and re-enabled
    # the head fallback, so labels and values outside Table 4 could still
    # earn verified_exact. Every locator fragment must co-occur in a window.
    document = {
        "final_url": "https://ar5iv.labs.arxiv.org/html/2503.14738",
        "mime": "text/html",
        "sha256": "a" * 64,
        "extraction_method": "ar5iv_html",
        "tables": [],
        "text": (
            "The LRG2 sample gives D_M = 17.351 +/- 0.177 and "
            "D_H = 19.455 +/- 0.330 in the abstract summary, far from any table."
        ),
    }

    status, _detail = match_expected_claims(
        document, _claims(), locator="Table 4, LRG2"
    )

    assert status != "verified_exact"


def test_locator_window_must_not_cross_into_next_table() -> None:
    # Codex review P1 (PR #46, round 8): a 12,000-character forward window
    # starting at Table 4 could borrow the requested labels and values from a
    # later Table 5 while still satisfying the Table 4 locator fragment.
    document = {
        "final_url": "https://ar5iv.labs.arxiv.org/html/2503.14738",
        "mime": "text/html",
        "sha256": "a" * 64,
        "extraction_method": "ar5iv_html",
        "tables": [],
        "text": (
            "Table 4 BAO measurements. LRG1 D_M = 99 +/- 9 and "
            "D_H = 88 +/- 8. "
            "Table 5 BAO measurements. LRG2 D_M = 17.351 +/- 0.177 and "
            "D_H = 19.455 +/- 0.330."
        ),
    }

    status, _detail = match_expected_claims(
        document, _claims(), locator="Table 4, LRG2"
    )

    assert status != "verified_exact"


def test_compound_locator_must_bind_claims_to_the_target_row() -> None:
    # Codex review P1 (PR #46, round 9): structural bounding kept a text
    # candidate inside Table 4, but the table-wide window could still combine
    # the requested LRG2 locator with LRG1's measurements.  A compound row
    # locator must restrict exact matching to the target row itself.
    document = {
        "final_url": "https://arxiv.org/pdf/2503.14738",
        "mime": "application/pdf",
        "sha256": "a" * 64,
        "extraction_method": "pdf_text",
        "tables": [],
        "text": (
            "Table 4 BAO measurements. "
            "LRG1 D_M = 17.351 +/- 0.177 and D_H = 19.455 +/- 0.330. "
            "LRG2 D_M = 30.000 +/- 0.300 and D_H = 40.000 +/- 0.400."
        ),
    }

    status, _detail = match_expected_claims(
        document, _claims(), locator="Table 4, LRG2"
    )

    assert status != "verified_exact"


def test_compound_locator_target_row_still_verifies_in_plain_text() -> None:
    document = {
        "final_url": "https://arxiv.org/pdf/2503.14738",
        "mime": "application/pdf",
        "sha256": "a" * 64,
        "extraction_method": "pdf_text",
        "tables": [],
        "text": (
            "Table 4 BAO measurements. "
            "LRG1 D_M = 30.000 +/- 0.300 and D_H = 40.000 +/- 0.400. "
            "LRG2 D_M = 17.351 +/- 0.177 and D_H = 19.455 +/- 0.330. "
            "LRG3 D_M = 50.000 +/- 0.500 and D_H = 60.000 +/- 0.600."
        ),
    }

    status, detail = match_expected_claims(
        document, _claims(), locator="Table 4, LRG2"
    )

    assert status == "verified_exact"
    assert detail["reason"] == "labels_and_values_same_window"


def test_row_locator_token_must_not_match_a_longer_row_name() -> None:
    # The row-local repair must preserve the same token-boundary rule as
    # claim labels: LRG2 is not a valid locator for the distinct LRG20 row.
    document = {
        "final_url": "https://arxiv.org/pdf/2503.14738",
        "mime": "application/pdf",
        "sha256": "a" * 64,
        "extraction_method": "pdf_text",
        "tables": [],
        "text": (
            "Table 4 BAO measurements. "
            "LRG20 D_M = 17.351 +/- 0.177 and D_H = 19.455 +/- 0.330."
        ),
    }

    status, _detail = match_expected_claims(
        document, _claims(), locator="Table 4, LRG2"
    )

    assert status != "verified_exact"


def test_value_before_its_label_must_not_verify_exact() -> None:
    # Codex review P1 (PR #46, round 10): value ordering was enforced only
    # between claims, so a value printed before its own label could still be
    # assigned to that later label.
    document = {
        "tables": [],
        "text": "10 +/- 1 is a calibration result; alpha is discussed later.",
    }
    claims = [
        {"id": "alpha", "label": "alpha", "value": 10.0,
         "standard_uncertainty": 1.0}
    ]

    status, _detail = match_expected_claims(document, claims, locator="")

    assert status != "verified_exact"


def test_unrelated_following_number_must_not_become_uncertainty() -> None:
    # Codex review P1 (PR #46, round 10): the next numeric token was accepted
    # as uncertainty even without a syntactic uncertainty marker.
    document = {
        "tables": [],
        "text": "alpha was 10, measured from 1 object.",
    }
    claims = [
        {"id": "alpha", "label": "alpha", "value": 10.0,
         "standard_uncertainty": 1.0}
    ]

    status, _detail = match_expected_claims(document, claims, locator="")

    assert status != "verified_exact"


def test_claim_level_row_locator_scopes_candidates_before_matching() -> None:
    # Codex review P1 (PR #46, round 10): when the source named only Table 4,
    # a more specific claim locator (Table 4, LRG2) was checked as a
    # table-wide predicate after values from LRG1 had already matched.
    document = {
        "tables": [],
        "text": (
            "Table 4 BAO measurements. "
            "LRG1 D_M = 17.351 +/- 0.177 and D_H = 19.455 +/- 0.330. "
            "LRG2 D_M = 30.000 +/- 0.300 and D_H = 40.000 +/- 0.400."
        ),
    }
    claims = _claims()
    for claim in claims:
        claim["source_locator"] = "Table 4, LRG2"

    status, _detail = match_expected_claims(document, claims, locator="Table 4")

    assert status != "verified_exact"


def test_more_specific_claim_locator_verifies_its_own_target_row() -> None:
    document = {
        "tables": [],
        "text": (
            "Table 4 BAO measurements. "
            "LRG1 D_M = 30.000 +/- 0.300 and D_H = 40.000 +/- 0.400. "
            "LRG2 D_M = 17.351 +/- 0.177 and D_H = 19.455 +/- 0.330."
        ),
    }
    claims = _claims()
    for claim in claims:
        claim["source_locator"] = "Table 4, LRG2"

    status, detail = match_expected_claims(document, claims, locator="Table 4")

    assert status == "verified_exact"
    assert detail["reason"] == "labels_and_values_same_window"


def test_generic_multiword_label_requires_the_complete_phrase() -> None:
    # Codex review P1 (PR #46, round 10): matching any word in a generic
    # multiword label let common words such as "constant" stand in for the
    # complete measurement label "Hubble constant".
    document = {
        "tables": [],
        "text": "The constant offset was 70 +/- 2 in this calibration.",
    }
    claims = [
        {"id": "h0", "label": "Hubble constant", "value": 70.0,
         "standard_uncertainty": 2.0}
    ]

    status, _detail = match_expected_claims(document, claims, locator="")

    assert status != "verified_exact"


def test_complete_generic_multiword_label_still_verifies() -> None:
    document = {
        "tables": [],
        "text": "The Hubble constant was 70 +/- 2 in this calibration.",
    }
    claims = [
        {"id": "h0", "label": "Hubble constant", "value": 70.0,
         "standard_uncertainty": 2.0}
    ]

    status, detail = match_expected_claims(document, claims, locator="")

    assert status == "verified_exact"
    assert detail["reason"] == "labels_and_values_same_window"


def test_claim_must_not_borrow_a_value_after_the_next_claim_label() -> None:
    # Codex review P1 (PR #46, round 11): own-label ordering alone still let
    # alpha consume beta's measurement and beta consume a later calibration.
    document = {
        "tables": [],
        "text": (
            "alpha was not measured; beta was 10 +/- 1; "
            "calibration was 20 +/- 2."
        ),
    }
    claims = [
        {"id": "alpha", "label": "alpha", "value": 10.0,
         "standard_uncertainty": 1.0},
        {"id": "beta", "label": "beta", "value": 20.0,
         "standard_uncertainty": 2.0},
    ]

    status, _detail = match_expected_claims(document, claims, locator="")

    assert status != "verified_exact"


def test_last_claim_must_not_borrow_from_a_later_field() -> None:
    # Codex review P1 (PR #46, round 12): the final expected claim had no next
    # label bound and could consume a later calibration field.
    document = {
        "tables": [],
        "text": (
            "alpha was 10 +/- 1; beta was not measured; "
            "calibration was 20 +/- 2."
        ),
    }
    claims = [
        {"id": "alpha", "label": "alpha", "value": 10.0,
         "standard_uncertainty": 1.0},
        {"id": "beta", "label": "beta", "value": 20.0,
         "standard_uncertainty": 2.0},
    ]

    status, _detail = match_expected_claims(document, claims, locator="")

    assert status != "verified_exact"


def test_claim_must_not_cross_a_comma_delimited_contrast_clause() -> None:
    # Codex review P1 (PR #46, round 17): a semicolon/sentence-only field
    # bound let a denied measurement borrow a calibration after ", while".
    document = {
        "tables": [],
        "text": "alpha was not measured, while calibration was 10 +/- 1.",
    }
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
        }
    ]

    status, _detail = match_expected_claims(document, claims, locator="")

    assert status != "verified_exact"


def test_claim_must_not_cross_a_comma_delimited_measurement_field() -> None:
    # Codex review P1 (PR #46, round 18): an ordinary comma could introduce a
    # new measurement field, yet the denied label before it could still borrow
    # that field's value and uncertainty.
    document = {
        "tables": [],
        "text": "alpha was not measured, calibration was 10 +/- 1.",
    }
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
        }
    ]

    status, _detail = match_expected_claims(document, claims, locator="")

    assert status != "verified_exact"


def test_appositive_commas_stay_inside_the_measurement_field() -> None:
    # Codex review P2 (PR #46, round 19): commas around a scientific qualifier
    # are not a new field boundary and must not block the label's own value.
    document = {
        "tables": [],
        "text": "The Hubble constant, measured from the sample, was 70 +/- 2.",
    }
    claims = [
        {
            "id": "h0",
            "label": "Hubble constant",
            "value": 70.0,
            "standard_uncertainty": 2.0,
        }
    ]

    status, detail = match_expected_claims(document, claims, locator="")

    assert status == "verified_exact"
    assert detail["reason"] == "labels_and_values_same_window"


def test_explicitly_negated_measurement_must_not_verify_exact() -> None:
    # Codex review P1 (PR #46, round 19): co-located label, value, and
    # uncertainty tokens cannot support a claim when the source negates the
    # assignment itself.
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
        }
    ]

    for text in (
        "alpha is not 10 +/- 1.",
        "alpha isn't 10 +/- 1.",
        "alpha cannot be 10 +/- 1.",
        "alpha can't be 10 +/- 1.",
    ):
        document = {"tables": [], "text": text}
        status, _detail = match_expected_claims(document, claims, locator="")
        assert status != "verified_exact", text


def test_governing_negation_survives_appositive_commas() -> None:
    # Codex review P1 (PR #46, round 25): keeping only the text after the last
    # comma erased the governing "is not" around an inserted appositive.
    claim = {
        "id": "alpha",
        "label": "alpha",
        "value": 10.0,
        "standard_uncertainty": 1.0,
    }

    negated, _detail = match_expected_claims(
        {
            "tables": [],
            "text": "alpha is not, contrary to prior work, 10 +/- 1.",
        },
        [claim],
        locator="",
    )
    unrelated, _detail = match_expected_claims(
        {
            "tables": [],
            "text": (
                "alpha, unlike a calibration that was not measured, "
                "was 10 +/- 1."
            ),
        },
        [claim],
        locator="",
    )

    assert negated != "verified_exact"
    assert unrelated == "verified_exact"


def test_relational_measurement_must_not_verify_exact() -> None:
    # Codex review P1 (PR #46, round 21): an upper/lower limit contains the
    # same tokens as an equality but cannot support an exact assignment.
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
        }
    ]

    for text in (
        "alpha < 10 +/- 1.",
        "alpha > 10 +/- 1.",
        "alpha ≤ 10 +/- 1.",
        "alpha ≥ 10 +/- 1.",
        "alpha is greater than 10 +/- 1.",
        "alpha is at most 10 +/- 1.",
    ):
        document = {"tables": [], "text": text}
        status, _detail = match_expected_claims(document, claims, locator="")
        assert status != "verified_exact", text


def test_postposed_measurement_disclaimer_must_not_verify_exact() -> None:
    # Codex review P1 (PR #46, round 23): prefix-only negation handling let a
    # complete-looking pair verify even when the rest of its field disclaimed
    # that measurement.
    claims = [
        {
            "id": "alpha",
            "label": "alpha",
            "value": 10.0,
            "standard_uncertainty": 1.0,
        }
    ]

    for text in (
        "alpha = 10 +/- 1 was never measured.",
        "alpha = 10 +/- 1 is not supported by the data.",
        "alpha = 10 +/- 1 isn't supported by the data.",
        "alpha = 10 +/- 1 cannot be used as a measurement.",
        "alpha = 10 +/- 1 is not to be used as a measurement.",
    ):
        status, _detail = match_expected_claims(
            {"tables": [], "text": text}, claims, locator=""
        )
        assert status != "verified_exact", text

    supported, _detail = match_expected_claims(
        {"tables": [], "text": "alpha = 10 +/- 1 was measured."},
        claims,
        locator="",
    )
    assert supported == "verified_exact"


def test_source_unit_must_match_for_exact_verification() -> None:
    # Codex review P1 (PR #46, round 23): identical numeric tokens in a
    # different unit cannot support the source measurement claim.
    claim = {
        "id": "alpha",
        "label": "alpha",
        "value": 10.0,
        "standard_uncertainty": 1.0,
        "unit": "Gpc",
    }

    mismatched, _detail = match_expected_claims(
        {"tables": [], "text": "alpha = 10 +/- 1 Mpc."},
        [claim],
        locator="",
    )
    matched, _detail = match_expected_claims(
        {"tables": [], "text": "alpha = 10 +/- 1 Gpc."},
        [claim],
        locator="",
    )
    borrowed_unit, _detail = match_expected_claims(
        {
            "tables": [],
            "text": "alpha = 10 +/- 1 and the calibration radius was 2 Gpc.",
        },
        [claim],
        locator="",
    )

    assert mismatched != "verified_exact"
    assert matched == "verified_exact"
    assert borrowed_unit != "verified_exact"

    dimensionless = dict(claim, unit="dimensionless")
    dimensionless_conflict, _detail = match_expected_claims(
        {"tables": [], "text": "alpha = 10 +/- 1 Mpc."},
        [dimensionless],
        locator="",
    )
    assert dimensionless_conflict != "verified_exact"

    unknown_dimensionless_conflict, _detail = match_expected_claims(
        {"tables": [], "text": "alpha = 10 +/- 1 eV."},
        [dimensionless],
        locator="",
    )
    assert unknown_dimensionless_conflict != "verified_exact"

    h0_claim = dict(
        claim,
        id="H0",
        label="H0",
        value=67.6,
        standard_uncertainty=1.2,
        unit="km/s/Mpc",
    )
    h0_matched, _detail = match_expected_claims(
        {
            "tables": [],
            "text": "H0 = 67.6 +/- 1.2 km s^-1 Mpc^-1.",
        },
        [h0_claim],
        locator="",
    )
    assert h0_matched == "verified_exact"


def test_equation_locator_stops_before_the_next_equation() -> None:
    # Codex review P1 (PR #46, round 12): Equation 42 could borrow a requested
    # measurement from Equation 43 in the same section.
    document = {
        "tables": [],
        "text": (
            "Section 2. Equation 42: alpha is not measured. "
            "Equation 43: alpha = 10 +/- 1."
        ),
    }
    claims = [
        {"id": "alpha", "label": "alpha", "value": 10.0,
         "standard_uncertainty": 1.0}
    ]

    status, _detail = match_expected_claims(
        document, claims, locator="Equation 42"
    )

    assert status != "verified_exact"


def test_structured_row_ignores_caption_label_mentions() -> None:
    # Codex review P2 (PR #46, round 12): caption mentions precede the
    # interleaved row fields and must not replace their local label positions.
    document = _document()
    document["tables"][0]["caption"] = "Measurements of D_M and D_H"

    status, detail = match_expected_claims(
        document, _claims(), locator="Table 4, LRG2"
    )

    assert status == "verified_exact"
    assert detail["reason"] == "labels_and_values_same_window"


def test_exponent_suffix_must_not_match_plain_number() -> None:
    # Codex review P1 (PR #46, round 3): the trailing boundary accepted an
    # exponent continuation, so supplied 17 matched source text 17e2.
    document = {
        "final_url": "https://ar5iv.labs.arxiv.org/html/2503.14738",
        "mime": "text/html",
        "sha256": "a" * 64,
        "extraction_method": "ar5iv_html",
        "tables": [
            {
                "label": "Table 4",
                "caption": "BAO measurements",
                "columns": ["Tracer", "D_M", "D_H"],
                "rows": [["LRG2", "17e2 +/- 0.177e3", "19.455 +/- 0.330"]],
            }
        ],
        "text": "",
    }
    claims = _claims()
    claims[0]["value"] = 17.0
    claims[0]["standard_uncertainty"] = 0.177

    status, _detail = match_expected_claims(
        document, claims, locator="Table 4, LRG2"
    )

    assert status != "verified_exact"


def test_lossy_six_digit_rendering_must_not_verify_exact() -> None:
    # Codex review P1 (PR #46, round 4): _number_tokens emitted lossy
    # six-significant-digit variants, so supplied 17.35144 matched a paper
    # that only prints 17.3514 — the extra supplied precision was never
    # verified.
    document = _document()
    document["tables"][0]["rows"] = [["LRG2", "17.3514 +/- 0.177", "19.455 +/- 0.330"]]
    claims = _claims()
    claims[0]["value"] = 17.35144

    status, _detail = match_expected_claims(
        document, claims, locator="Table 4, LRG2"
    )

    assert status != "verified_exact"


def test_claim_level_locator_conflict_must_not_verify_exact() -> None:
    # Codex review P1 (PR #46, round 4): matching used only the source-level
    # locator, so a quantity recorded as coming from Table 5 could be
    # verified from Table 4 values while the receipt kept saying Table 5.
    claims = _claims()
    for claim in claims:
        claim["source_locator"] = "Table 5, LRG2"

    status, _detail = match_expected_claims(
        _document(), claims, locator="Table 4, LRG2"
    )

    assert status != "verified_exact"


def test_matching_claim_level_locator_still_verifies_exact() -> None:
    claims = _claims()
    for claim in claims:
        claim["source_locator"] = "Table 4, LRG2"

    status, detail = match_expected_claims(
        _document(), claims, locator="Table 4, LRG2"
    )

    assert status == "verified_exact"
    assert detail["reason"] == "labels_and_values_same_window"


def test_permuted_label_value_pairs_must_not_verify_exact() -> None:
    # Codex review P1 (PR #46, round 5): labels and numbers were collected as
    # independent window-wide sets, so alpha=10±1, beta=20±2 verified against
    # a row stating alpha=20±2, beta=10±1.
    document = {
        "final_url": "https://ar5iv.labs.arxiv.org/html/2503.14738",
        "mime": "text/html",
        "sha256": "a" * 64,
        "extraction_method": "ar5iv_html",
        "tables": [
            {
                "label": "Table 4",
                "caption": "parameters",
                "columns": [],
                "rows": [["LRG2 alpha 20 +/- 2 beta 10 +/- 1"]],
            }
        ],
        "text": "",
    }
    claims = [
        {"id": "alpha", "label": "alpha", "value": 10.0, "standard_uncertainty": 1.0},
        {"id": "beta", "label": "beta", "value": 20.0, "standard_uncertainty": 2.0},
    ]

    status, _detail = match_expected_claims(
        document, claims, locator="Table 4, LRG2"
    )

    assert status != "verified_exact"


def test_cache_key_distinguishes_claim_locator_and_label() -> None:
    # Codex review P1 (PR #46, round 5): the claims digest omitted
    # source_locator (and label whenever id exists), so a verified result
    # cached for Table 4 was replayed for the same numbers attributed to
    # Table 5 or to a different label.
    from app.services.source_packet_resolver import _cache_key, normalize_source

    source = normalize_source(
        {"id": "s1", "kind": "arxiv", "identifier": "2503.14738", "locator": "Table 4"}
    )
    base = {"id": "q1", "label": "D_M", "value": 17.351,
            "standard_uncertainty": 0.177, "source_locator": "Table 4, LRG2"}
    relocated = dict(base, source_locator="Table 5, LRG2")
    relabeled = dict(base, label="D_H")
    reunitized = dict(base, unit="Gpc")

    assert _cache_key(source, [base]) != _cache_key(source, [relocated])
    assert _cache_key(source, [base]) != _cache_key(source, [relabeled])
    assert _cache_key(source, [base]) != _cache_key(source, [reunitized])


def test_short_label_inside_unrelated_word_must_not_verify() -> None:
    # Codex review P1 (PR #46, round 6): bare substring label matching let
    # "ns" match inside "constraints", verifying a field the text never
    # states.
    document = {
        "final_url": "https://ar5iv.labs.arxiv.org/html/2503.14452",
        "mime": "text/html",
        "sha256": "a" * 64,
        "extraction_method": "ar5iv_html",
        "tables": [],
        "text": "The constraints were 0.965 +/- 0.004 in the joint analysis.",
    }
    claims = [
        {"id": "ns", "label": "ns", "value": 0.965, "standard_uncertainty": 0.004}
    ]

    status, _detail = match_expected_claims(document, claims, locator="")

    assert status != "verified_exact"


def test_swapped_uncertainties_must_not_verify_exact() -> None:
    # Codex review P1 (PR #46, round 6): the value-order fix left
    # uncertainties as window-wide matches, so alpha=10±1, beta=20±2 still
    # verified against "alpha 10 +/- 2 beta 20 +/- 1".
    document = {
        "final_url": "https://ar5iv.labs.arxiv.org/html/2503.14738",
        "mime": "text/html",
        "sha256": "a" * 64,
        "extraction_method": "ar5iv_html",
        "tables": [
            {
                "label": "Table 4",
                "caption": "parameters",
                "columns": [],
                "rows": [["LRG2 alpha 10 +/- 2 beta 20 +/- 1"]],
            }
        ],
        "text": "",
    }
    claims = [
        {"id": "alpha", "label": "alpha", "value": 10.0, "standard_uncertainty": 1.0},
        {"id": "beta", "label": "beta", "value": 20.0, "standard_uncertainty": 2.0},
    ]

    status, _detail = match_expected_claims(
        document, claims, locator="Table 4, LRG2"
    )

    assert status != "verified_exact"

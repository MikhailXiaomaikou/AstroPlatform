"""Bounded, provenance-preserving source resolution for scalar receipts."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import ipaddress
import io
import json
import math
import re
import socket
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.observability.metrics import record_counter, record_histogram
from app.services.connector_cache import TTL_METADATA, get_backend


SOURCE_STATUSES = frozenset(
    {
        "verified_exact",
        "resolved_unmatched",
        "user_supplied_unverified",
        "conflict",
        "unavailable",
    }
)

_ARXIV_RE = re.compile(
    r"(?:arxiv:\s*|arxiv\.org/(?:abs|pdf)/)?([\d]{4}\.[\d]{4,5}(?:v\d+)?)",
    re.I,
)
_DOI_RE = re.compile(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/\S+)", re.I)
_ZENODO_RE = re.compile(r"(?:zenodo\.org/(?:records?|record)/|10\.5281/zenodo\.)(\d+)", re.I)
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_TRANSIENT_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
_MAX_RESPONSE_BYTES = 25 * 1024 * 1024
_MAX_EXPANDED_BYTES = 25 * 1024 * 1024
_MAX_SOURCE_TEXT_FILES = 64
_MAX_REDIRECTS = 3
_MAX_ADAPTERS = 2
_ATTEMPT_TIMEOUT_SECONDS = 8.0
_ADAPTER_DEADLINE_SECONDS = 15.0
_TOTAL_TIMEOUT_SECONDS = 30.0
_FAILURE_TTL_SECONDS = 5 * 60
_PDF_EXTRACTION_TIMEOUT_SECONDS = 8.0
_PDF_MAX_CPU_SECONDS = 6
_PDF_MAX_ADDRESS_SPACE_BYTES = 512 * 1024 * 1024
_PDF_MAX_PAGES = 512
_PDF_MAX_TEXT_BYTES = _MAX_EXPANDED_BYTES
_HTTPS_PROXY_SYNTHETIC_NETWORK = ipaddress.ip_network("198.18.0.0/15")

# Untrusted PDF parsing happens in a fresh interpreter, never in the API
# process.  The worker installs limits before importing pdfminer, suppresses
# parser output, and exposes only a bounded result (or one fixed marker) over
# its inherited result pipe.  ``-I`` also prevents cwd/user-site imports.
_PDF_EXTRACTION_WORKER = f"""
import io
import os
import sys
import threading
import time

_RESULT_FD = os.dup(sys.stdout.fileno())
_DEVNULL_FD = os.open(os.devnull, os.O_WRONLY)
os.dup2(_DEVNULL_FD, sys.stdout.fileno())
os.dup2(_DEVNULL_FD, sys.stderr.fileno())
os.close(_DEVNULL_FD)


def _write_result(data):
    view = memoryview(data)
    while view:
        written = os.write(_RESULT_FD, view)
        view = view[written:]


def _fail(marker, status):
    _write_result(marker.encode("ascii"))
    os.close(_RESULT_FD)
    os._exit(status)


try:
    import resource
except Exception:
    _fail("PDF_RESOURCE_LIMIT_SETUP", 70)


def _set_limit(name, requested):
    limit = getattr(resource, name, None)
    if limit is None:
        raise RuntimeError(name)
    _soft, hard = resource.getrlimit(limit)
    value = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
    resource.setrlimit(limit, (value, value))


try:
    _set_limit("RLIMIT_CPU", {_PDF_MAX_CPU_SECONDS})
    _set_limit("RLIMIT_NOFILE", 32)
    if hasattr(resource, "RLIMIT_CORE"):
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
except Exception:
    _fail("PDF_RESOURCE_LIMIT_SETUP", 70)

# Linux accepts a strict address-space ceiling.  Darwin's Python process has
# very large framework mappings and refuses a useful RLIMIT_AS even while its
# resident set is small, so keep the CPU limit and enforce the same memory
# budget with an in-process watchdog there.  The parent can still kill this
# whole interpreter on its independent wall-clock timeout.
try:
    _set_limit("RLIMIT_AS", {_PDF_MAX_ADDRESS_SPACE_BYTES})
except Exception:
    if sys.platform != "darwin":
        _fail("PDF_RESOURCE_LIMIT_SETUP", 70)


def _watch_resident_memory():
    while True:
        maximum_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform != "darwin":
            maximum_rss *= 1024
        if maximum_rss > {_PDF_MAX_ADDRESS_SPACE_BYTES}:
            _fail("PDF_RESOURCE_LIMIT", 75)
        time.sleep(0.025)


threading.Thread(target=_watch_resident_memory, daemon=True).start()

payload = sys.stdin.buffer.read({_MAX_RESPONSE_BYTES + 1})
if len(payload) > {_MAX_RESPONSE_BYTES}:
    _fail("PDF_INPUT_LIMIT", 71)

try:
    from pdfminer.high_level import extract_text
    from pdfminer.pdfpage import PDFPage
except Exception:
    _fail("PDF_EXTRACTOR_UNAVAILABLE", 72)

try:
    pages = PDFPage.get_pages(
        io.BytesIO(payload),
        maxpages={_PDF_MAX_PAGES + 1},
        caching=False,
        check_extractable=True,
    )
    if sum(1 for _page in pages) > {_PDF_MAX_PAGES}:
        _fail("PDF_PAGE_LIMIT", 73)
    text = extract_text(
        io.BytesIO(payload), maxpages={_PDF_MAX_PAGES}, caching=False
    )
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) > {_PDF_MAX_TEXT_BYTES}:
        _fail("PDF_TEXT_LIMIT", 74)
except MemoryError:
    _fail("PDF_RESOURCE_LIMIT", 75)
except BaseException:
    _fail("PDF_TEXT_INVALID", 76)

_write_result(encoded)
os.close(_RESULT_FD)
"""


class SourceResolutionError(RuntimeError):
    """A bounded source adapter failure with stable retry semantics."""

    def __init__(self, message: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class NormalizedSource:
    id: str
    kind: str
    identifier: str
    locator: str
    url: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "identifier": self.identifier,
            "locator": self.locator,
            "url": self.url,
        }


Adapter = Callable[[NormalizedSource], Awaitable[dict[str, Any]]]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _clean_identifier(value: Any) -> str:
    return str(value or "").strip().rstrip(".,;)")


def normalize_source(raw: Any) -> NormalizedSource:
    if not isinstance(raw, dict):
        raise SourceResolutionError(
            "Each source must be an object.", code="invalid_source"
        )
    source_id = str(raw.get("id") or "").strip()
    if not source_id:
        raise SourceResolutionError(
            "Each source requires a non-empty id.", code="invalid_source_id"
        )
    kind = str(raw.get("kind") or "").strip().lower()
    identifier = _clean_identifier(raw.get("identifier"))
    locator = str(raw.get("locator") or "").strip()
    if kind == "arxiv":
        match = _ARXIV_RE.search(identifier)
        if not match:
            raise SourceResolutionError("Invalid arXiv identifier.", code="invalid_arxiv_id")
        identifier = match.group(1)
        url = f"https://arxiv.org/abs/{identifier}"
    elif kind == "doi":
        match = _DOI_RE.search(identifier)
        if not match:
            raise SourceResolutionError("Invalid DOI identifier.", code="invalid_doi")
        identifier = match.group(1).rstrip(".")
        url = f"https://doi.org/{quote(identifier, safe='/():;._-')}"
    elif kind == "zenodo":
        match = _ZENODO_RE.search(identifier)
        if not match:
            raise SourceResolutionError("Invalid Zenodo identifier.", code="invalid_zenodo_id")
        identifier = match.group(1)
        url = f"https://zenodo.org/records/{identifier}"
    elif kind == "url":
        url = identifier
        _require_safe_https_url(url)
        identifier = url
    elif kind == "user_supplied":
        url = None
    else:
        raise SourceResolutionError(
            "Source kind must be arxiv, doi, zenodo, url, or user_supplied.",
            code="unsupported_source_kind",
        )
    return NormalizedSource(source_id, kind, identifier, locator, url)


def _canonical_httpx_hostname(hostname: str) -> str:
    """Match the UTS-46/IDNA hostname form HTTPX passes to httpcore."""
    return (
        httpx.URL(scheme="https", host=hostname.rstrip("."))
        .raw_host.decode("ascii")
        .rstrip(".")
        .lower()
    )


def _require_safe_https_url(value: str) -> str:
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise SourceResolutionError("Invalid source URL.", code="unsafe_url") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise SourceResolutionError(
            "Only public HTTPS source URLs are allowed.", code="unsafe_url"
        )
    if parsed.username or parsed.password or port not in {None, 443}:
        raise SourceResolutionError(
            "Source URL contains a forbidden authority component.", code="unsafe_url"
        )
    try:
        hostname = _canonical_httpx_hostname(parsed.hostname)
    except (httpx.InvalidURL, UnicodeError) as exc:
        raise SourceResolutionError("Invalid source URL.", code="unsafe_url") from exc
    if hostname in {"localhost", "localhost.localdomain"}:
        raise SourceResolutionError("Private source hosts are forbidden.", code="ssrf_blocked")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return value
    if not address.is_global:
        raise SourceResolutionError("Private source hosts are forbidden.", code="ssrf_blocked")
    return value


async def _require_public_dns(hostname: str) -> tuple[str, ...]:
    try:
        records = await asyncio.to_thread(
            socket.getaddrinfo, hostname, 443, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise SourceResolutionError(
            "Source host could not be resolved.", code="dns_unavailable", retryable=True
        ) from exc
    addresses = list(
        dict.fromkeys(ipaddress.ip_address(record[4][0]) for record in records)
    )
    # Managed HTTPS proxies may synthesize RFC 2544 benchmark addresses for
    # public DNS names. Direct 198.18/15 URL literals are still rejected by
    # _require_safe_https_url; only a hostname resolution may use this range.
    def public_or_proxy_synthetic(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return address.is_global or address in _HTTPS_PROXY_SYNTHETIC_NETWORK

    if not addresses or any(not public_or_proxy_synthetic(value) for value in addresses):
        raise SourceResolutionError("Private source hosts are forbidden.", code="ssrf_blocked")
    return tuple(str(value) for value in addresses)


class _PinnedAsyncNetworkBackend:
    """Connect an HTTP origin only through addresses from its validated lookup.

    HTTPX/httpcore still sees the original hostname as the request origin, so
    the Host header, certificate verification, and TLS SNI all retain that
    hostname. Only the TCP destination is substituted, closing the second-DNS-
    lookup window that permits rebinding between validation and connection.
    """

    def __init__(self, hostname: str, addresses: tuple[str, ...]) -> None:
        if not addresses:
            raise ValueError("at least one validated address is required")
        from httpcore._backends.auto import AutoBackend

        self._hostname = _canonical_httpx_hostname(hostname)
        self._addresses = addresses
        self._backend = AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        connection_hostname = _canonical_httpx_hostname(host)
        if connection_hostname != self._hostname:
            raise RuntimeError("pinned transport received an unexpected hostname")
        last_error: Exception | None = None
        for address in self._addresses:
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("source downloads never use Unix sockets")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


def _pinned_https_transport(
    hostname: str, addresses: tuple[str, ...]
) -> httpx.AsyncHTTPTransport:
    # trust_env=False is intentional: an environment proxy would become a
    # second resolver outside this pinned TCP path. Managed transparent HTTPS
    # proxies are represented by the validated 198.18/15 synthetic addresses.
    transport = httpx.AsyncHTTPTransport(trust_env=False)
    transport._pool._network_backend = _PinnedAsyncNetworkBackend(  # type: ignore[attr-defined]
        hostname, addresses
    )
    return transport


async def _download(
    url: str,
    *,
    accept: str,
    allowed_mime_prefixes: tuple[str, ...],
) -> tuple[bytes, str, str]:
    current_url = _require_safe_https_url(url)
    for redirect_index in range(_MAX_REDIRECTS + 1):
        parsed = urlparse(current_url)
        hostname = _canonical_httpx_hostname(str(parsed.hostname))
        addresses = await _require_public_dns(hostname)
        transport = _pinned_https_transport(hostname, addresses)
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_ATTEMPT_TIMEOUT_SECONDS),
                follow_redirects=False,
                transport=transport,
            ) as client:
                async with client.stream(
                    "GET",
                    current_url,
                    headers={"Accept": accept, "User-Agent": "Standard-Astro/0.2"},
                ) as response:
                    if response.status_code in _REDIRECT_CODES:
                        if redirect_index >= _MAX_REDIRECTS:
                            raise SourceResolutionError(
                                "Source exceeded the redirect limit.",
                                code="redirect_limit",
                                retryable=True,
                            )
                        location = response.headers.get("location")
                        if not location:
                            raise SourceResolutionError(
                                "Source returned an empty redirect.", code="invalid_redirect"
                            )
                        current_url = _require_safe_https_url(urljoin(current_url, location))
                        continue
                    if response.status_code != 200:
                        raise SourceResolutionError(
                            f"Source returned HTTP {response.status_code}.",
                            code="source_http_error",
                            retryable=response.status_code in _TRANSIENT_CODES,
                        )
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type and not any(
                        content_type.startswith(prefix) for prefix in allowed_mime_prefixes
                    ):
                        raise SourceResolutionError(
                            f"Unsupported source MIME type: {content_type}.",
                            code="unsupported_mime",
                        )
                    advertised = response.headers.get("content-length")
                    if advertised:
                        try:
                            advertised_size = int(advertised)
                        except ValueError as exc:
                            raise SourceResolutionError(
                                "Source returned an invalid content length.",
                                code="invalid_content_length",
                            ) from exc
                        if advertised_size < 0 or advertised_size > _MAX_RESPONSE_BYTES:
                            raise SourceResolutionError(
                                "Source response exceeds the 25 MB limit.",
                                code="source_too_large",
                            )
                    chunks: list[bytes] = []
                    observed = 0
                    async for chunk in response.aiter_bytes():
                        observed += len(chunk)
                        if observed > _MAX_RESPONSE_BYTES:
                            raise SourceResolutionError(
                                "Source response exceeds the 25 MB limit.",
                                code="source_too_large",
                            )
                        chunks.append(chunk)
                    return b"".join(chunks), current_url, content_type
        except SourceResolutionError:
            raise
        except httpx.HTTPError as exc:
            raise SourceResolutionError(
                "Source request failed.", code="source_network_error", retryable=True
            ) from exc
    raise SourceResolutionError(
        "Source exceeded the redirect limit.", code="redirect_limit", retryable=True
    )


def _html_document(payload: bytes, *, final_url: str, method: str) -> dict[str, Any]:
    from app.api.arxiv import _parse_html_tables

    text = payload.decode("utf-8", errors="replace")
    soup = BeautifulSoup(text, "html.parser")
    return {
        "final_url": final_url,
        "mime": "text/html",
        "sha256": _sha256_bytes(payload),
        "extraction_method": method,
        "text": soup.get_text(" ", strip=True),
        "tables": _parse_html_tables(text),
    }


def _plain_text_document(
    payload: bytes, *, final_url: str, method: str
) -> dict[str, Any]:
    return {
        "final_url": final_url,
        "mime": "text/plain",
        "sha256": _sha256_bytes(payload),
        "extraction_method": method,
        "text": payload.decode("utf-8", errors="replace"),
        "tables": [],
    }


def _extract_pdf_text(payload: bytes) -> str:
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise SourceResolutionError(
            "PDF source exceeds the 25 MB limit.", code="source_too_large"
        )
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-c", _PDF_EXTRACTION_WORKER],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_PDF_EXTRACTION_TIMEOUT_SECONDS,
            check=False,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise SourceResolutionError(
            "PDF text extraction exceeded its time limit.",
            code="pdf_text_timeout",
            retryable=True,
        ) from exc
    except OSError as exc:
        raise SourceResolutionError(
            "PDF text extraction is unavailable in this runtime.",
            code="pdf_extractor_unavailable",
        ) from exc

    if completed.returncode == 0:
        if len(completed.stdout) > _PDF_MAX_TEXT_BYTES:
            raise SourceResolutionError(
                "Extracted PDF text exceeds the 25 MB limit.",
                code="pdf_text_too_large",
            )
        return completed.stdout.decode("utf-8", errors="replace")

    marker = (completed.stdout + completed.stderr).decode(
        "ascii", errors="ignore"
    )
    if "PDF_EXTRACTOR_UNAVAILABLE" in marker:
        code = "pdf_extractor_unavailable"
        message = "PDF text extraction is unavailable in this runtime."
    elif "PDF_INPUT_LIMIT" in marker:
        code = "source_too_large"
        message = "PDF source exceeds the 25 MB limit."
    elif "PDF_PAGE_LIMIT" in marker:
        code = "pdf_page_limit"
        message = "PDF exceeds the 512-page extraction limit."
    elif "PDF_TEXT_LIMIT" in marker:
        code = "pdf_text_too_large"
        message = "Extracted PDF text exceeds the 25 MB limit."
    elif (
        "PDF_RESOURCE_LIMIT" in marker
        or completed.returncode < 0
    ):
        code = "pdf_resource_limit"
        message = "PDF text extraction exceeded a resource limit."
    else:
        code = "pdf_text_invalid"
        message = "PDF text extraction failed."
    raise SourceResolutionError(message, code=code)


def _safe_arxiv_source_texts(payload: bytes) -> list[str]:
    """Expand only bounded text members from an arXiv source packet."""
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise SourceResolutionError(
            "Compressed arXiv source exceeds the 25 MB limit.",
            code="source_too_large",
        )
    texts: list[str] = []
    expanded = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r|*") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                expanded += max(0, int(member.size))
                if expanded > _MAX_EXPANDED_BYTES:
                    raise SourceResolutionError(
                        "Expanded arXiv source exceeds the 25 MB limit.",
                        code="expanded_source_too_large",
                    )
                if len(texts) >= _MAX_SOURCE_TEXT_FILES:
                    continue
                if not member.name.lower().endswith((".tex", ".ltx", ".txt")):
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                raw = extracted.read(min(member.size, _MAX_EXPANDED_BYTES) + 1)
                if len(raw) > _MAX_EXPANDED_BYTES:
                    raise SourceResolutionError(
                        "Expanded arXiv source exceeds the 25 MB limit.",
                        code="expanded_source_too_large",
                    )
                texts.append(raw.decode("utf-8", errors="replace"))
        if texts:
            return texts
    except SourceResolutionError:
        raise
    except tarfile.TarError:
        pass

    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
            raw = compressed.read(_MAX_EXPANDED_BYTES + 1)
        if len(raw) > _MAX_EXPANDED_BYTES:
            raise SourceResolutionError(
                "Expanded arXiv source exceeds the 25 MB limit.",
                code="expanded_source_too_large",
            )
        return [raw.decode("utf-8", errors="replace")]
    except SourceResolutionError:
        raise
    except (gzip.BadGzipFile, EOFError, OSError):
        if payload.startswith(b"\x1f\x8b"):
            raise SourceResolutionError(
                "Compressed arXiv source is invalid.",
                code="compressed_source_invalid",
            )
        if len(payload) > _MAX_EXPANDED_BYTES:
            raise SourceResolutionError(
                "Expanded arXiv source exceeds the 25 MB limit.",
                code="expanded_source_too_large",
            )
        return [payload.decode("utf-8", errors="replace")]


async def _arxiv_html_adapter(source: NormalizedSource) -> dict[str, Any]:
    url = f"https://ar5iv.labs.arxiv.org/html/{source.identifier}"
    payload, final_url, _mime = await _download(
        url, accept="text/html", allowed_mime_prefixes=("text/html",)
    )
    return _html_document(payload, final_url=final_url, method="ar5iv_html")


async def _arxiv_pdf_adapter(source: NormalizedSource) -> dict[str, Any]:
    url = f"https://arxiv.org/pdf/{source.identifier}"
    payload, final_url, mime = await _download(
        url,
        accept="application/pdf",
        allowed_mime_prefixes=("application/pdf", "application/octet-stream"),
    )
    text = await asyncio.to_thread(_extract_pdf_text, payload)
    return {
        "final_url": final_url,
        "mime": mime or "application/pdf",
        "sha256": _sha256_bytes(payload),
        "extraction_method": "pdf_text",
        "text": text,
        "tables": [],
    }


async def _arxiv_source_adapter(source: NormalizedSource) -> dict[str, Any]:
    from app.api.arxiv import _parse_latex_tables

    url = f"https://arxiv.org/e-print/{source.identifier}"
    payload, final_url, mime = await _download(
        url,
        accept="application/gzip,application/x-tar,application/octet-stream,text/plain",
        allowed_mime_prefixes=(
            "application/gzip",
            "application/x-gzip",
            "application/x-tar",
            "application/octet-stream",
            "text/plain",
        ),
    )
    source_texts = await asyncio.to_thread(_safe_arxiv_source_texts, payload)
    total_chars = 0
    bounded_texts: list[str] = []
    tables: list[dict[str, Any]] = []
    for source_text in source_texts:
        remaining = _MAX_RESPONSE_BYTES - total_chars
        if remaining <= 0:
            raise SourceResolutionError(
                "Expanded arXiv source exceeds the 25 MB limit.",
                code="expanded_source_too_large",
            )
        bounded = source_text[:remaining]
        total_chars += len(bounded.encode("utf-8", errors="ignore"))
        bounded_texts.append(bounded)
        tables.extend(_parse_latex_tables(bounded))
    return {
        "final_url": final_url,
        "mime": mime or "application/octet-stream",
        "sha256": _sha256_bytes(payload),
        "extraction_method": "arxiv_latex_source",
        "text": "\n".join(bounded_texts),
        "tables": tables,
    }


async def _doi_content_adapter(source: NormalizedSource) -> dict[str, Any]:
    assert source.url
    payload, final_url, mime = await _download(
        source.url,
        accept="text/html,application/pdf;q=0.9",
        allowed_mime_prefixes=(
            "text/html",
            "application/xhtml+xml",
            "application/pdf",
        ),
    )
    if mime.startswith("application/pdf"):
        text = await asyncio.to_thread(_extract_pdf_text, payload)
        return {
            "final_url": final_url,
            "mime": mime,
            "sha256": _sha256_bytes(payload),
            "extraction_method": "doi_publisher_pdf_text",
            "text": text,
            "tables": [],
        }
    return _html_document(payload, final_url=final_url, method="doi_publisher_html")


async def _zenodo_html_adapter(source: NormalizedSource) -> dict[str, Any]:
    assert source.url
    payload, final_url, _mime = await _download(
        source.url, accept="text/html", allowed_mime_prefixes=("text/html",)
    )
    return _html_document(payload, final_url=final_url, method="zenodo_record_html")


async def _zenodo_file_adapter(source: NormalizedSource) -> dict[str, Any]:
    """Resolve one public text-bearing file from the official Zenodo record API."""
    api_url = f"https://zenodo.org/api/records/{source.identifier}"
    metadata_bytes, _final_url, _mime = await _download(
        api_url,
        accept="application/json",
        allowed_mime_prefixes=("application/json",),
    )
    try:
        metadata = json.loads(metadata_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceResolutionError(
            "Zenodo returned invalid record metadata.", code="zenodo_metadata_invalid"
        ) from exc
    files = metadata.get("files") if isinstance(metadata, dict) else None
    if not isinstance(files, list):
        raise SourceResolutionError(
            "Zenodo record has no public files.", code="zenodo_file_unavailable"
        )
    candidates: list[tuple[int, str]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        links = item.get("links")
        file_url = links.get("self") if isinstance(links, dict) else None
        key = str(item.get("key") or "").lower()
        if not isinstance(file_url, str):
            continue
        priority = 0 if key.endswith(".pdf") else 1 if key.endswith((".html", ".htm", ".txt")) else 2
        if priority < 2:
            candidates.append((priority, file_url))
    if not candidates:
        raise SourceResolutionError(
            "Zenodo record has no supported PDF, HTML, or text file.",
            code="zenodo_file_unavailable",
        )
    file_url = sorted(candidates, key=lambda item: item[0])[0][1]
    payload, final_url, mime = await _download(
        file_url,
        accept="application/pdf,text/html;q=0.9,text/plain;q=0.8",
        allowed_mime_prefixes=(
            "application/pdf",
            "text/html",
            "application/xhtml+xml",
            "text/plain",
        ),
    )
    if mime.startswith("application/pdf"):
        text = await asyncio.to_thread(_extract_pdf_text, payload)
        return {
            "final_url": final_url,
            "mime": mime,
            "sha256": _sha256_bytes(payload),
            "extraction_method": "zenodo_pdf_text",
            "text": text,
            "tables": [],
        }
    if mime.startswith("text/plain"):
        return _plain_text_document(
            payload, final_url=final_url, method="zenodo_public_text"
        )
    return _html_document(payload, final_url=final_url, method="zenodo_public_html")


async def _url_adapter(source: NormalizedSource) -> dict[str, Any]:
    assert source.url
    payload, final_url, mime = await _download(
        source.url,
        accept="text/html,application/pdf;q=0.9,text/plain;q=0.8",
        allowed_mime_prefixes=(
            "text/html",
            "application/xhtml+xml",
            "application/pdf",
            "text/plain",
        ),
    )
    if mime.startswith("application/pdf"):
        text = await asyncio.to_thread(_extract_pdf_text, payload)
        return {
            "final_url": final_url,
            "mime": mime,
            "sha256": _sha256_bytes(payload),
            "extraction_method": "pdf_text",
            "text": text,
            "tables": [],
        }
    if mime.startswith("text/plain"):
        return _plain_text_document(
            payload, final_url=final_url, method="public_text"
        )
    return _html_document(payload, final_url=final_url, method="public_html")


_ADAPTERS: dict[str, tuple[Adapter, ...]] = {
    "arxiv": (_arxiv_html_adapter, _arxiv_source_adapter, _arxiv_pdf_adapter),
    "doi": (_doi_content_adapter,),
    "zenodo": (_zenodo_file_adapter, _zenodo_html_adapter),
    "url": (_url_adapter,),
}


def _number_tokens(value: float) -> set[str]:
    if not math.isfinite(value):
        return set()
    rendered = {
        f"{value:.12g}",
        f"{value:.8g}",
        f"{value:.6g}",
        str(value),
    }
    tokens: set[str] = set()
    for item in rendered:
        # Codex review P1 (PR #46, round 4): only keep renderings that parse
        # back to the exact supplied value. A lossy six-digit variant let
        # 17.35144 match a paper that only prints 17.3514, granting
        # verified_exact to precision the paper never stated.
        try:
            if float(item) != value:
                continue
        except ValueError:
            continue
        tokens.add(item)
        tokens.add(item.replace("-", "−"))
    return tokens


def _label_tokens(label: Any) -> set[str]:
    text = str(label or "").lower()
    text = text.replace("\\", "").replace("{", "").replace("}", "")
    aliases = {
        "dm": {"d_m", "dm", "transverse comoving distance"},
        "dh": {"d_h", "dh", "hubble distance"},
        "dmrd": {
            "d_m/r_d",
            "dm/rd",
            "d m / r d",
            "transverse comoving distance",
        },
        "dhrd": {"d_h/r_d", "dh/rd", "d h / r d", "hubble distance"},
        "rho": {"rho", "ρ", "correlation"},
    }
    compact = re.sub(r"[^a-z0-9ρ]+", "", text)
    for key, values in aliases.items():
        if compact == key:
            return values
    words = [word for word in re.split(r"[^a-z0-9ρ]+", text) if len(word) >= 2]
    # Codex review P1 (PR #46, round 10): generic multiword labels are one
    # semantic name, not a bag of independently sufficient common words.
    # Known aliases above remain explicit alternatives; every other label
    # must occur as its complete normalized phrase.
    if len(words) > 1:
        return {" ".join(words)}
    return set(words) or ({compact} if compact else set())


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).lower()


def _label_token_present(
    token: str, window: str, *, last: bool = False
) -> re.Match[str] | None:
    """Boundary-aware label containment.

    Codex review P1 (PR #46, round 6): bare substring matching let "ns"
    match inside "constraints". A label token must stand on its own
    alphanumeric boundary.
    """
    pattern = rf"(?<![a-z0-9ρ_]){re.escape(token)}(?![a-z0-9ρ_])"
    found: re.Match[str] | None = None
    for candidate in re.finditer(pattern, window):
        found = candidate
        if not last:
            break
    return found


def _locator_fragment_alternatives(fragment: str) -> tuple[str, ...]:
    """Accepted renderings of one locator fragment inside a compacted window."""
    alternatives = [fragment]
    equation_number = re.search(r"(?:equation|eq\.?)\s*(\d+)", fragment, re.I)
    if equation_number:
        number = equation_number.group(1)
        alternatives.extend((f"({number})", f"eq. {number}", f"equation {number}"))
    return tuple(alternatives)


def _locator_fragment_present(fragment: str, window: str) -> bool:
    """Require locator renderings on alphanumeric token boundaries."""
    return any(
        re.search(
            rf"(?<![a-z0-9]){re.escape(alternative)}(?![a-z0-9])",
            window,
            re.I,
        )
        is not None
        for alternative in _locator_fragment_alternatives(fragment)
    )


_TEXT_STRUCTURE_BOUNDARY = re.compile(
    r"(?<![a-z0-9])(?:(?:table|tab\.?|section|sec\.?|equation|eq\.?)\s*"
    r"\d+(?:\.\d+)*[a-z]?|\(\s*\d+(?:\.\d+)*[a-z]?\s*\))"
    r"(?![a-z0-9])",
    re.I,
)

_ROW_LOCATOR_FAMILY = re.compile(
    r"(?<![a-z0-9])([a-z][a-z0-9_-]*?)\s*(\d+[a-z]?)(?![a-z0-9])",
    re.I,
)


def _bounded_locator_window(text: str, locator_index: int) -> str:
    """Keep a locator candidate inside its containing table/section."""
    start = max(0, locator_index - 500)
    end = min(len(text), locator_index + 12000)
    previous_boundary: re.Match[str] | None = None
    next_boundary: re.Match[str] | None = None
    for boundary in _TEXT_STRUCTURE_BOUNDARY.finditer(text):
        if boundary.start() <= locator_index:
            previous_boundary = boundary
            continue
        next_boundary = boundary
        break
    if previous_boundary is not None:
        start = max(start, previous_boundary.start())
    if next_boundary is not None:
        end = min(end, next_boundary.start())
    return text[start:end]


def _bounded_row_locator_window(
    text: str, locator_index: int, row_fragment: str
) -> str | None:
    """Keep a compound locator candidate inside its target row.

    Plain-text/PDF extraction has no structured ``rows`` collection.  A
    table-wide window is therefore unsafe for a locator such as
    ``Table 4, LRG2``: LRG2 can coexist with measurements printed on LRG1.
    Accept only numbered row-family anchors, start matching at the requested
    row, and stop at the next sibling row or structural boundary.  Other
    compound text locators fail closed; structured HTML tables still use the
    explicit per-row path above.
    """
    family = _ROW_LOCATOR_FAMILY.fullmatch(row_fragment.strip())
    if family is None:
        return None
    family_name = family.group(1)
    start = locator_index
    end = min(len(text), locator_index + 12000)
    previous_boundary: re.Match[str] | None = None
    for boundary in _TEXT_STRUCTURE_BOUNDARY.finditer(text):
        if boundary.start() <= locator_index:
            previous_boundary = boundary
            continue
        end = min(end, boundary.start())
        break
    sibling_pattern = re.compile(
        rf"(?<![a-z0-9]){re.escape(family_name)}\s*\d+[a-z]?(?![a-z0-9])",
        re.I,
    )
    for sibling in sibling_pattern.finditer(text, locator_index + len(row_fragment), end):
        end = sibling.start()
        break
    # Carry only the containing structure label into the row-local window so
    # the compound-locator filter can prove that this row belongs to the
    # requested table/section.  Measurements before the row anchor remain
    # unavailable to exact matching.
    structure = previous_boundary.group() if previous_boundary is not None else ""
    return _compact_text(f"{structure} {text[start:end]}")


def _candidate_windows(document: dict[str, Any], locator: str) -> list[str]:
    windows: list[str] = []
    locator_text = _compact_text(locator)
    for table in document.get("tables") or []:
        if not isinstance(table, dict):
            continue
        table_header = " ".join(
            str(table.get(key) or "") for key in ("label", "name", "caption")
        )
        if locator_text and locator_text not in _compact_text(table_header):
            locator_number = re.search(r"\d+", locator_text)
            header_number = re.search(r"\d+", table_header)
            if locator_number and header_number and locator_number.group() != header_number.group():
                continue
        column_values = [str(value) for value in table.get("columns") or []]
        columns = " ".join(column_values)
        for row in table.get("rows") or []:
            if isinstance(row, list):
                row_values = [str(value) for value in row]
                # Codex review P1 (PR #46, round 11): keep structured table
                # fields local by interleaving each header with its own cell.
                # A flat "all headers, then all cells" window cannot prove
                # which measurement belongs to which label.
                if column_values and len(column_values) == len(row_values):
                    row_text = " ".join(
                        f"{column} {value}"
                        for column, value in zip(column_values, row_values)
                    )
                    columns_for_window = ""
                else:
                    row_text = " ".join(row_values)
                    columns_for_window = columns
            else:
                row_text = str(row)
                columns_for_window = columns
            windows.append(
                _compact_text(f"{table_header} {columns_for_window} {row_text}")
            )
    text = _compact_text(document.get("text"))
    if text:
        if locator_text:
            fragments = [
                part.strip() for part in locator_text.split(",") if part.strip()
            ]
            row_fragment = fragments[-1] if len(fragments) > 1 else ""
            row_family = _ROW_LOCATOR_FAMILY.fullmatch(row_fragment)
            locator_indices: list[int] = []
            if row_family is not None:
                # Codex review P1 (PR #46, round 9): compound row locators
                # must never re-enable table-wide candidates. Anchor only on
                # the requested row fragment and trim to its row-local region.
                locator_indices.extend(
                    match.start()
                    for match in list(
                        re.finditer(
                            rf"(?<![a-z0-9]){re.escape(row_fragment)}"
                            rf"(?![a-z0-9])",
                            text,
                            re.I,
                        )
                    )[:12]
                )
            elif len(fragments) <= 1:
                exact_index = text.find(locator_text)
                if exact_index >= 0:
                    locator_indices.append(exact_index)
                locator_indices.extend(
                    match.start()
                    for match in list(
                        re.finditer(re.escape(locator_text), text, re.I)
                    )[:12]
                )
            if not locator_indices:
                equation_number = re.search(
                    r"(?:equation|eq\.?)\s*(\d+)", locator_text, re.I
                )
                if equation_number:
                    candidates = (
                        f"({equation_number.group(1)})",
                        f"eq. {equation_number.group(1)}",
                        f"equation {equation_number.group(1)}",
                    )
                    locator_indices.extend(
                        index
                        for index in (text.find(candidate) for candidate in candidates)
                        if index >= 0
                    )
            for locator_index in sorted(set(locator_indices))[:24]:
                # Codex review P1 (PR #46, round 8): a fixed 12,000-character
                # forward window could start at Table 4 and borrow labels and
                # values from Table 5. Bound both sides to the containing
                # table/section so compound locator fragments cannot be
                # assembled across source structures.
                if row_family is not None:
                    row_window = _bounded_row_locator_window(
                        text, locator_index, row_fragment
                    )
                    if row_window is not None:
                        windows.append(row_window)
                else:
                    windows.append(_bounded_locator_window(text, locator_index))
        # Codex review P1 (PR #46): the document-head fallback may only run
        # when no locator was requested. Otherwise labels and numbers elsewhere
        # in the paper could earn verified_exact for a table/equation that was
        # never checked — or cross a later structural boundary.
        if not locator_text:
            windows.extend(
                text[index : index + 3000]
                for index in range(0, min(len(text), 12000), 3000)
            )
    # Codex review P1 (PR #46, round 2): a compound locator counted as found
    # when ANY comma-separated fragment matched, so "Table 4, LRG2" could be
    # satisfied by an "LRG2" occurrence far from Table 4 and verification
    # proceeded against unrelated regions. Every fragment (or an accepted
    # equation rendering of it) must co-occur inside a window for that window
    # to participate in verification.
    if locator_text:
        fragments = [
            part.strip() for part in locator_text.split(",") if part.strip()
        ]
        if fragments:
            windows = [
                window
                for window in windows
                if all(
                    _locator_fragment_present(fragment, window)
                    for fragment in fragments
                )
            ]
    return windows


def _number_token_present(token: str, window: str) -> bool:
    """Value-preserving numeric containment.

    Codex review P1 (PR #46): bare substring matching let a truncated supplied
    value (17.35) match inside the paper's 17.351 and earn verified_exact.
    The token must stand on its own numeric boundary; the only permitted
    continuations are ones that keep the parsed value identical — trailing
    zeros after a decimal token (0.33 matches the paper's 0.330) and a pure
    ``.0…`` tail after an integer token (73 matches 73.0 but never 73.04).
    """
    value_preserving_suffix = "0*" if "." in token else r"(?:\.0+)?"
    # Codex review P1 (PR #46, round 3): also reject exponent continuations —
    # supplied 17 must not match the source text 17e2.
    pattern = (
        rf"(?<![0-9.eE+−-]){re.escape(token)}{value_preserving_suffix}"
        rf"(?!\.?[0-9]|[eE][-+]?[0-9])"
    )
    return re.search(pattern, window) is not None


_GENERIC_NUMBER_TOKEN = re.compile(
    r"(?<![0-9.eE+−-])[-+−]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
    r"(?!\.?[0-9]|[eE][-+]?[0-9])"
)
_UNCERTAINTY_BINDING = re.compile(r"\s*(?:±|\+/-|\+-|\\pm)\s*", re.I)
_FIELD_TERMINATOR = re.compile(
    r";|(?<!\d)[.!?]|[.!?](?!\d)|"
    r"(?:,|，)\s*(?="
    r"(?:while|whereas|but|however|although|though|yet|instead|rather)\b|"
    r"而|但|然而|不过|"
    r"(?:(?:the|a|an)\s+)?"
    r"(?:[^\W\d_][\w-]*(?:\s+[^\W\d_][\w-]*){0,4})"
    r"(?:\s+(?:(?:was|were|is|are)\s+"
    r"(?:measured\s+(?:as|at)\s+)?|"
    r"(?:equals?|equaled|measures?|measured|reports?|reported|"
    r"estimates?|estimated|gives?|gave|yields?|yielded)\s+)"
    r"|\s*(?:=|:))[-+−]?\d|"
    r"[一-鿿]{1,12}\s*(?:为|是|=|:|：)\s*[-+−]?\d)",
    re.I,
)
_NON_EXACT_MEASUREMENT_ASSIGNMENT = re.compile(
    r"\b(?:is|are|was|were|should|must|can|could|may|might|will|would|"
    r"does|do|did)\s+(?:(?:explicitly|definitely|clearly|directly|simply)\s+){0,2}"
    r"(?:not|never)\b|"
    r"\b(?:(?:is|are|was|were|should|must|could|might|would|does|do|did|"
    r"has|have|had)n['’]t|can['’]t|won['’]t|cannot)\b|"
    r"(?:!=|≠|<=|>=|<|>|≤|≥|≲|≳)|"
    r"\b(?:less|lower|smaller|greater|higher|larger)\s+than"
    r"(?:\s+or\s+equal\s+to)?\b|"
    r"\b(?:at\s+(?:most|least)|no\s+(?:more|less)\s+than|"
    r"(?:upper|lower)\s+(?:limit|bound))\b",
    re.I,
)
_POSTPOSED_MEASUREMENT_DISCLAIMER = re.compile(
    r"\b(?:is|are|was|were|has|have|had|should|must|can|could|may|might|"
    r"will|would)\s+"
    r"(?:(?:explicitly|directly|clearly|statistically|currently)\s+){0,2}"
    r"(?:not(?!\s+only)|never)\s+(?:(?:to\s+be|be|have\s+been)\s+)?"
    r"(?:measured|reported|supported|used|included|adopted|accepted|"
    r"validated|trusted|established|confirmed)\b|"
    r"\b(?:(?:is|are|was|were|has|have|had|should|must|can|could|may|"
    r"might|will|would)n['’]t|cannot)\s+"
    r"(?:(?:to\s+be|be|have\s+been)\s+)?"
    r"(?:measured|reported|supported|used|included|adopted|accepted|"
    r"validated|trusted|established|confirmed)\b|"
    r"\b(?:is|are|was|were)\s+"
    r"(?:(?:explicitly|directly|clearly)\s+)?"
    r"(?:unsupported|unmeasured|excluded|discarded|retracted|withdrawn|invalid)\b|"
    r"\b(?:should|must)\s+be\s+(?:ignored|discarded|excluded|rejected)\b",
    re.I,
)
_PRELABEL_PROPOSITION_REJECTION = re.compile(
    r"\b(?:"
    r"(?:it|this|that)\s+(?:is|was)\s+(?:not|never)\s+"
    r"(?:true|established|supported|shown|demonstrated)\s+that|"
    r"(?:we|the\s+(?:data|analysis|study|source|paper|results?))\s+"
    r"(?:cannot|can['’]t|do\s+not|don['’]t|does\s+not|doesn['’]t|"
    r"did\s+not|didn['’]t)\s+"
    r"(?:conclude|infer|claim|show|support|establish|confirm|demonstrate|find)\s+that|"
    r"(?:cannot|can['’]t)\s+be\s+"
    r"(?:concluded|inferred|claimed|shown|supported|established|confirmed)\s+that"
    r")\s*$",
    re.I,
)
_EXPLICIT_MEASUREMENT_ASSIGNMENT_PREFIX = re.compile(
    r"\s*(?:"
    r"(?:=|:|：)|"
    r"(?:is|are|was|were)\s*"
    r"(?:(?:measured|reported|estimated|given|found)\s+)?"
    r"(?:(?:as|at|to\s+be)\s*)?|"
    r"(?:equals?|equaled|measures?|measured|reports?|reported|"
    r"estimates?|estimated|gives?|gave|yields?|yielded)\s*"
    r")\s*",
    re.I,
)
_SOURCE_H0_UNIT = re.compile(
    r"(?<![a-z0-9])km\s*"
    r"(?:(?:/\s*s)|s\s*(?:\^?\s*\{?\s*[-−]\s*1\s*\}?|⁻¹))\s*"
    r"(?:/\s*)?mpc"
    r"(?:\s*(?:\^?\s*\{?\s*[-−]\s*1\s*\}?|⁻¹))?"
    r"(?![a-z0-9])",
    re.I,
)
_SOURCE_DISTANCE_UNIT = re.compile(
    r"(?<![a-z0-9])(?P<base>gpc|mpc|kpc|pc)"
    r"(?P<inverse>\s*(?:\^?\s*\{?\s*[-−]\s*1\s*\}?|⁻¹))?"
    r"(?![a-z0-9])",
    re.I,
)
_SOURCE_PHYSICAL_UNIT_PREFIX = re.compile(
    r"^\s*(?:%|°|(?:"
    r"mas|arcmin|arcsec|deg|"
    r"gpc|mpc|kpc|pc|au|"
    r"tev|gev|mev|kev|ev|"
    r"thz|ghz|mhz|khz|hz|"
    r"gyr|myr|yr|days?|"
    r"m_sun|l_sun|"
    r"erg(?:/s)?(?:/cm\^?2)?|"
    r"μjy|ujy|mjy|jy|"
    r"å|μm|um|nm|mm|cm|km|kg|mag|dex|k|m|g|s"
    r")(?![a-z0-9_]))",
    re.I,
)


def _measurement_assignment_is_non_exact(
    window: str, label_position: int, value_position: int
) -> bool:
    """Reject negated or relational label-to-value assignments."""
    field_start = 0
    for terminator in _FIELD_TERMINATOR.finditer(window, 0, label_position):
        field_start = terminator.end()
    if _PRELABEL_PROPOSITION_REJECTION.search(
        window[field_start:label_position]
    ):
        return True
    governing_text = window[label_position:value_position]
    # Appositive clauses may themselves contain unrelated negation, but the
    # governing predicate can straddle them ("alpha is not, contrary to ...,"
    # 10). Collapse paired comma-delimited appositives instead of discarding
    # the entire prefix before the final comma. New measurement fields are
    # bounded separately by _FIELD_TERMINATOR.
    comma_segments = re.split(r"[,，]", governing_text)
    if len(comma_segments) >= 3:
        governing_text = " ".join(comma_segments[::2])
    return _NON_EXACT_MEASUREMENT_ASSIGNMENT.search(governing_text) is not None


def _measurement_suffix_is_non_exact(field_suffix: str) -> bool:
    """Reject a value/uncertainty pair disclaimed later in its own field."""
    return _POSTPOSED_MEASUREMENT_DISCLAIMER.search(field_suffix) is not None


def _source_units(field: str) -> set[str]:
    """Recognize the conservative unit vocabulary accepted by scalar inputs."""
    from app.services.scalar_derivation import normalize_unit

    units: set[str] = set()
    composite_spans: list[tuple[int, int]] = []
    for match in _SOURCE_H0_UNIT.finditer(field):
        composite_spans.append(match.span())
        units.add("km s^-1 Mpc^-1")
    for match in _SOURCE_DISTANCE_UNIT.finditer(field):
        if any(
            start <= match.start() and match.end() <= end
            for start, end in composite_spans
        ):
            continue
        unit = normalize_unit(match.group("base"))
        if match.group("inverse"):
            unit = f"{unit}^-1"
        units.add(unit)
    return units


def _measurement_unit_matches(
    claim_unit: Any, field: str, measurement_suffix: str
) -> bool:
    """Require a claim's unit to agree with its bounded source field."""
    from app.services.scalar_derivation import normalize_unit

    expected = normalize_unit(claim_unit)
    detected = _source_units(field)
    if expected == "dimensionless":
        return not detected and _SOURCE_PHYSICAL_UNIT_PREFIX.match(
            measurement_suffix
        ) is None
    if detected:
        return detected == {expected}
    # Unknown units are accepted only as their own complete literal token;
    # recognized but different physical units have already failed above.
    literal = _compact_text(expected)
    if not literal:
        return False
    return re.search(
        rf"(?<![a-z0-9]){re.escape(literal)}(?![a-z0-9])", field, re.I
    ) is not None


def _value_positions(value: float, window: str) -> list[int]:
    """Start offsets of boundary-valid occurrences of the value in the window."""
    positions: set[int] = set()
    for token in _number_tokens(value):
        token = token.lower()
        value_preserving_suffix = "0*" if "." in token else r"(?:\.0+)?"
        pattern = (
            rf"(?<![0-9.eE+−-]){re.escape(token)}{value_preserving_suffix}"
            rf"(?!\.?[0-9]|[eE][-+]?[0-9])"
        )
        positions.update(match.start() for match in re.finditer(pattern, window))
    return sorted(positions)


def _values_follow_label_order(
    expected_claims: list[dict[str, Any]], window: str
) -> bool:
    """Require value order to be assignable consistently with label order.

    Codex review P1 (PR #46, round 5): labels and numbers were checked as two
    independent window-wide sets, so permuting values between labels still
    verified. An occurrence-aware monotone assignment accepts both prose
    ("Planck ... 67.36 ... SH0ES ... 73.04") and structured table rows while
    rejecting swapped assignments. A prose order reversed relative to the
    label order fails toward resolved_unmatched, which is the safe direction.
    Every boundary-valid label occurrence is considered: a later narrative
    mention must not hide an earlier exact assignment, while a later numeric
    field for the same label must repeat the expected measurement exactly.
    """
    labeled_claims: list[
        tuple[dict[str, Any], list[tuple[int, int]]]
    ] = []
    for claim in expected_claims:
        labels = _label_tokens(claim.get("label") or claim.get("id"))
        if not labels:
            continue
        label_occurrences = sorted(
            {
                (match.start(), match.end())
                for label in labels
                for match in re.finditer(
                    rf"(?<![a-z0-9ρ_]){re.escape(label)}(?![a-z0-9ρ_])",
                    window,
                )
            }
        )
        if not label_occurrences:
            continue
        labeled_claims.append((claim, label_occurrences))
    if not labeled_claims:
        return True

    def measurement_end_in_field(
        claim: dict[str, Any],
        label_position: int,
        cursor: int,
        field_limit: int,
    ) -> int | None:
        try:
            value = float(claim["value"])
        except (KeyError, TypeError, ValueError):
            return None
        uncertainty: float | None
        try:
            uncertainty = float(claim["standard_uncertainty"])
        except (KeyError, TypeError, ValueError):
            uncertainty = None
        for position in _value_positions(value, window):
            # Codex review P1 (PR #46, round 10): a measurement cannot be
            # assigned to a label introduced only later in the row/prose.
            if position <= max(cursor, label_position):
                continue
            # Codex review P1 (PR #46, round 11): a claim cannot borrow the
            # first matching number after the next claim's label. Structured
            # tables are rendered above as local header/cell pairs, so the
            # same field boundary works for prose and tables.
            if position >= field_limit:
                continue
            # Codex review P1 (PR #46, round 19): token co-occurrence cannot
            # prove a measurement that the source explicitly negates, such as
            # "alpha is not 10 +/- 1".
            if _measurement_assignment_is_non_exact(
                window, label_position, position
            ):
                continue
            value_token = _GENERIC_NUMBER_TOKEN.match(window, position)
            if value_token is None:
                continue
            measurement_end = value_token.end()
            # Codex review P1 (PR #46, round 6): the uncertainty must be
            # bound to its own value — it has to be the immediately
            # following numeric token, as in "17.351 +/- 0.177". A swapped
            # pair like "alpha 10 +/- 2" fails here instead of matching
            # window-wide.
            if uncertainty is not None:
                following = _GENERIC_NUMBER_TOKEN.search(
                    window, value_token.end()
                )
                if following is None:
                    continue
                # Codex review P1 (PR #46, round 10): the next number is not
                # automatically an uncertainty.  Require an explicit
                # plus/minus marker with no intervening prose or field.
                binding = window[value_token.end() : following.start()]
                if _UNCERTAINTY_BINDING.fullmatch(binding) is None:
                    continue
                if following.start() not in _value_positions(
                    uncertainty, window
                ):
                    continue
                measurement_end = following.end()
            # Codex review P1 (PR #46, round 23): a suffix in the same field
            # can disclaim a syntactically complete pair ("was never
            # measured", "is not supported"). Prefix-only inspection is not
            # enough to grant verified_exact.
            if _measurement_suffix_is_non_exact(
                window[measurement_end:field_limit]
            ):
                continue
            # A numeric match in Mpc cannot support a Gpc claim. Require the
            # normalized source unit inside this exact label/value field; an
            # implicit dimensionless claim fails if a physical unit is shown.
            if not _measurement_unit_matches(
                claim.get("unit"),
                window[label_position:field_limit],
                window[measurement_end:field_limit],
            ):
                continue
            return measurement_end
        return None

    def matching_measurement_end(
        claim: dict[str, Any],
        label_position: int,
        label_occurrences: list[tuple[int, int]],
        cursor: int,
        next_label_position: int | None,
    ) -> int | None:
        field_terminator = _FIELD_TERMINATOR.search(window, label_position + 1)
        field_end = field_terminator.start() if field_terminator else len(window)
        field_limit = (
            field_end
            if next_label_position is None
            else min(next_label_position, field_end)
        )
        measurement_end = measurement_end_in_field(
            claim, label_position, cursor, field_limit
        )
        if measurement_end is None:
            return None

        def has_measurement_assignment_syntax(
            label_end: int, field_limit: int
        ) -> bool:
            numeric = _GENERIC_NUMBER_TOKEN.search(window, label_end, field_limit)
            if numeric is None:
                return False
            prefix = window[label_end:numeric.start()]
            if _EXPLICIT_MEASUREMENT_ASSIGNMENT_PREFIX.fullmatch(prefix):
                return True
            # Structured table renderings can be the bare "label 20 +/- 2"
            # form. Treat that as a measurement only when the number is
            # immediately uncertainty-bound; "Section 2" / "Equation 42"
            # and citation numbers remain ordinary cross-references.
            if prefix.strip():
                return False
            following = _GENERIC_NUMBER_TOKEN.search(
                window, numeric.end(), field_limit
            )
            return following is not None and _UNCERTAINTY_BINDING.fullmatch(
                window[numeric.end():following.start()]
            ) is not None

        # Occurrence-aware matching must not turn into first-match wins. A
        # neutral later mention is harmless, but a later numeric field for the
        # same label must repeat the expected measurement exactly; otherwise
        # the source is internally conflicting and cannot earn verified_exact.
        for later_position, later_end in label_occurrences:
            if later_position <= label_position:
                continue
            later_terminator = _FIELD_TERMINATOR.search(
                window, later_position + 1
            )
            later_limit = (
                later_terminator.start() if later_terminator else len(window)
            )
            if not has_measurement_assignment_syntax(later_end, later_limit):
                continue
            if (
                measurement_end_in_field(
                    claim, later_position, -1, later_limit
                )
                is None
            ):
                return None
        return measurement_end

    # Codex review P2 (PR #46, round 28): selecting only the final label
    # occurrence makes "alpha = 10 +/- 1. This alpha is used below" fail.
    # Search for a monotone, non-overlapping assignment across every label
    # occurrence.  Remaining-claim labels bound the current field, preserving
    # the anti-permutation and cross-field guards while avoiding a Cartesian
    # product over all occurrences.
    memo: dict[tuple[tuple[int, ...], int], bool] = {}

    def assign(remaining: tuple[int, ...], cursor: int) -> bool:
        if not remaining:
            return True
        key = (remaining, cursor)
        if key in memo:
            return memo[key]
        for claim_index in remaining:
            claim, label_occurrences = labeled_claims[claim_index]
            other_indices = tuple(
                index for index in remaining if index != claim_index
            )
            other_label_positions = sorted(
                occurrence[0]
                for index in other_indices
                for occurrence in labeled_claims[index][1]
            )
            for label_position, _label_end in label_occurrences:
                if label_position <= cursor:
                    continue
                next_label_position = next(
                    (
                        position
                        for position in other_label_positions
                        if position > label_position
                    ),
                    None,
                )
                measurement_end = matching_measurement_end(
                    claim,
                    label_position,
                    label_occurrences,
                    cursor,
                    next_label_position,
                )
                if measurement_end is not None and assign(
                    other_indices, measurement_end
                ):
                    memo[key] = True
                    return True
        memo[key] = False
        return False

    return assign(tuple(range(len(labeled_claims))), -1)


def match_expected_claims(
    document: dict[str, Any], expected_claims: list[dict[str, Any]], *, locator: str
) -> tuple[str, dict[str, Any]]:
    """Require every label and numeric value to coexist in one source window."""
    if not expected_claims:
        return "resolved_unmatched", {"reason": "no_expected_claims"}
    compact_source_locator = _compact_text(locator)
    claim_locators = {
        str(claim.get("source_locator") or "").strip()
        for claim in expected_claims
        if str(claim.get("source_locator") or "").strip()
    }
    if len(claim_locators) > 1:
        return "conflict", {"reason": "claims_use_different_source_locators"}
    effective_locator = locator
    if claim_locators:
        claim_locator = next(iter(claim_locators))
        compact_claim_locator = _compact_text(claim_locator)
        if compact_claim_locator != compact_source_locator:
            source_fragments = [
                part.strip()
                for part in compact_source_locator.split(",")
                if part.strip()
            ]
            if compact_source_locator and not all(
                _locator_fragment_present(fragment, compact_claim_locator)
                for fragment in source_fragments
            ):
                return "conflict", {
                    "reason": "claim_locator_conflicts_with_source_locator"
                }
            # Codex review P1 (PR #46, round 10): a more specific claim-level
            # locator must scope candidate construction before any label/value
            # matching.  Treating it as a later table-wide predicate can pair
            # an LRG2 locator with LRG1 measurements.
            effective_locator = claim_locator
    windows = _candidate_windows(document, effective_locator)
    label_seen = False
    compact_source_locator = _compact_text(effective_locator)
    for window in windows:
        all_labels = True
        all_numbers = True
        for claim in expected_claims:
            labels = _label_tokens(claim.get("label") or claim.get("id"))
            label_match = not labels or any(
                _label_token_present(label, window) for label in labels
            )
            label_seen = label_seen or label_match
            # Codex review P1 (PR #46, round 4): a claim that carries its own
            # source_locator must be verified inside a window that actually
            # contains that locator — a Table 5 quantity must not be granted
            # verified_exact from a Table 4 window just because the source
            # object pointed there.
            claim_locator = _compact_text(claim.get("source_locator") or "")
            if claim_locator and claim_locator != compact_source_locator:
                fragments = [
                    part.strip()
                    for part in claim_locator.split(",")
                    if part.strip()
                ]
                if not all(
                    _locator_fragment_present(fragment, window)
                    for fragment in fragments
                ):
                    all_labels = False
            expected_numbers = [_number_tokens(float(claim["value"]))]
            if claim.get("standard_uncertainty") is not None:
                expected_numbers.append(
                    _number_tokens(float(claim["standard_uncertainty"]))
                )
            number_match = all(
                any(_number_token_present(token.lower(), window) for token in tokens)
                for tokens in expected_numbers
            )
            all_labels = all_labels and label_match
            all_numbers = all_numbers and number_match
        if (
            all_labels
            and all_numbers
            and _values_follow_label_order(expected_claims, window)
        ):
            return "verified_exact", {"reason": "labels_and_values_same_window"}
    if label_seen:
        return "conflict", {"reason": "labels_found_values_differ_or_missing"}
    return "resolved_unmatched", {"reason": "expected_labels_not_located"}


async def _run_adapter(adapter: Adapter, source: NormalizedSource) -> dict[str, Any]:
    started = time.monotonic()
    adapter_name = adapter.__name__.removeprefix("_").removesuffix("_adapter")
    document_cache_key = (
        f"lightweight_source_document_v1:{source.kind}:{source.identifier}:"
        f"{adapter_name}"
    )
    backend = get_backend()
    cached_document = backend.get(document_cache_key)
    if isinstance(cached_document, dict):
        record_counter(
            "lightweight_source_document_cache_total",
            source_kind=source.kind,
            adapter=adapter_name,
            outcome="hit",
        )
        return {**cached_document, "_document_cache_hit": True}
    record_counter(
        "lightweight_source_document_cache_total",
        source_kind=source.kind,
        adapter=adapter_name,
        outcome="miss",
    )
    last_error: SourceResolutionError | None = None
    try:
        for attempt in range(2):
            try:
                document = await asyncio.wait_for(
                    adapter(source), timeout=_ADAPTER_DEADLINE_SECONDS
                )
                break
            except TimeoutError as exc:
                last_error = SourceResolutionError(
                    "Source adapter timed out.", code="source_timeout", retryable=True
                )
                if attempt == 1:
                    raise last_error from exc
            except SourceResolutionError as exc:
                last_error = exc
                if not exc.retryable or attempt == 1:
                    raise
        else:  # pragma: no cover - loop always returns or raises
            assert last_error is not None
            raise last_error
    except SourceResolutionError as exc:
        record_counter(
            "lightweight_source_adapter_requests_total",
            source_kind=source.kind,
            adapter=adapter_name,
            status="timeout" if exc.code == "source_timeout" else "failed",
        )
        raise
    finally:
        record_histogram(
            "lightweight_source_adapter_duration_seconds",
            time.monotonic() - started,
            source_kind=source.kind,
            adapter=adapter_name,
        )
    record_counter(
        "lightweight_source_adapter_requests_total",
        source_kind=source.kind,
        adapter=adapter_name,
        status="success",
    )
    backend.set(document_cache_key, document, TTL_METADATA)
    return document


async def _resolve_uncached(
    source: NormalizedSource,
    expected_claims: list[dict[str, Any]],
    *,
    adapter_semaphore: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    if source.kind == "user_supplied":
        return {
            **source.as_dict(),
            "status": "user_supplied_unverified",
            "cache_hit": False,
            "extraction_method": "none",
            "fetched_at_unix": None,
            "sha256": None,
            "match": {"reason": "user_supplied_sources_are_never_self_verified"},
        }
    adapters = _ADAPTERS.get(source.kind, ())
    errors: list[SourceResolutionError] = []
    semaphore = adapter_semaphore or asyncio.Semaphore(_MAX_ADAPTERS)

    async def invoke(adapter: Adapter) -> dict[str, Any]:
        async with semaphore:
            return await _run_adapter(adapter, source)

    tasks = [asyncio.create_task(invoke(adapter)) for adapter in adapters]
    conflict_fallback: dict[str, Any] | None = None
    resolved_fallback: dict[str, Any] | None = None
    try:
        for completed in asyncio.as_completed(tasks):
            try:
                document = await completed
            except SourceResolutionError as exc:
                errors.append(exc)
                continue
            status, match = match_expected_claims(
                document, expected_claims, locator=source.locator
            )
            if status == "verified_exact":
                for task in tasks:
                    if not task.done():
                        task.cancel()
                return {
                    **source.as_dict(),
                    "status": status,
                    "cache_hit": bool(document.get("_document_cache_hit")),
                    "final_url": document.get("final_url"),
                    "mime": document.get("mime"),
                    "extraction_method": document.get("extraction_method"),
                    "fetched_at_unix": int(time.time()),
                    "sha256": document.get("sha256"),
                    "match": match,
                }
            candidate = {
                **source.as_dict(),
                "status": status,
                "cache_hit": bool(document.get("_document_cache_hit")),
                "final_url": document.get("final_url"),
                "mime": document.get("mime"),
                "extraction_method": document.get("extraction_method"),
                "fetched_at_unix": int(time.time()),
                "sha256": document.get("sha256"),
                "match": match,
            }
            if status == "conflict":
                conflict_fallback = candidate
            else:
                resolved_fallback = candidate
        if conflict_fallback is not None:
            return conflict_fallback
        if resolved_fallback is not None:
            return resolved_fallback
        error = errors[-1] if errors else SourceResolutionError(
            "No source adapter is available.", code="source_unavailable"
        )
        return {
            **source.as_dict(),
            "status": "unavailable",
            "cache_hit": False,
            "extraction_method": None,
            "fetched_at_unix": int(time.time()),
            "sha256": None,
            "error_class": error.code,
            "retryable": error.retryable,
        }
    finally:
        await asyncio.gather(*tasks, return_exceptions=True)


def _cache_key(source: NormalizedSource, expected_claims: list[dict[str, Any]]) -> str:
    # Codex review P1 (PR #46, round 5): every field match_expected_claims
    # consumes must be part of the digest — id AND label AND the per-claim
    # source_locator — or a result verified for one attribution is replayed
    # for a different one straight from the cache.
    from app.services.scalar_derivation import normalize_unit

    claims_digest = hashlib.sha256(
        repr(
            sorted(
                (
                    str(claim.get("id") or ""),
                    str(claim.get("label") or ""),
                    claim.get("value"),
                    claim.get("standard_uncertainty"),
                    normalize_unit(claim.get("unit")),
                    str(claim.get("source_locator") or ""),
                )
                for claim in expected_claims
            )
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"lightweight_source_v7:{source.kind}:{source.identifier}:{source.locator}:{claims_digest}"


async def resolve_source(
    raw_source: dict[str, Any],
    expected_claims: list[dict[str, Any]],
    *,
    adapter_semaphore: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    source = normalize_source(raw_source)
    cache_key = _cache_key(source, expected_claims)
    backend = get_backend()
    cached = backend.get(cache_key)
    if isinstance(cached, dict):
        record_counter(
            "lightweight_source_cache_total", source_kind=source.kind, outcome="hit"
        )
        return {**cached, "cache_hit": True}
    record_counter(
        "lightweight_source_cache_total", source_kind=source.kind, outcome="miss"
    )
    result = await _resolve_uncached(
        source, expected_claims, adapter_semaphore=adapter_semaphore
    )
    ttl = TTL_METADATA if result.get("status") != "unavailable" else _FAILURE_TTL_SECONDS
    backend.set(cache_key, result, ttl)
    return result


async def resolve_sources(
    sources: list[dict[str, Any]], expected_claims: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not sources:
        return []
    source_ids = [str(source.get("id") or "") for source in sources if isinstance(source, dict)]
    if len(source_ids) != len(set(source_ids)):
        raise SourceResolutionError(
            "Source ids must be unique.", code="duplicate_source_id"
        )

    adapter_semaphore = asyncio.Semaphore(_MAX_ADAPTERS)

    async def one(source: dict[str, Any]) -> dict[str, Any]:
        source_id = str(source.get("id") or "")
        claims = [
            claim
            for claim in expected_claims
            if str(claim.get("source_ref") or "") == source_id
        ]
        try:
            return await resolve_source(
                source, claims, adapter_semaphore=adapter_semaphore
            )
        except SourceResolutionError as exc:
            return {
                **source,
                "status": "unavailable",
                "cache_hit": False,
                "error_class": exc.code,
                "retryable": exc.retryable,
            }

    try:
        return await asyncio.wait_for(
            asyncio.gather(*(one(source) for source in sources)),
            timeout=_TOTAL_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        return [
            {
                **source,
                "status": "unavailable",
                "cache_hit": False,
                "error_class": "source_budget_exhausted",
                "retryable": True,
            }
            for source in sources
        ]

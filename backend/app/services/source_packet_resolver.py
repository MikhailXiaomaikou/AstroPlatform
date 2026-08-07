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

_ARXIV_RE = re.compile(r"(?:arxiv:\s*|arxiv\.org/(?:abs|pdf)/)?([\d]{4}\.[\d]{4,5})(?:v\d+)?", re.I)
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
_HTTPS_PROXY_SYNTHETIC_NETWORK = ipaddress.ip_network("198.18.0.0/15")


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
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"}:
        raise SourceResolutionError("Private source hosts are forbidden.", code="ssrf_blocked")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return value
    if not address.is_global:
        raise SourceResolutionError("Private source hosts are forbidden.", code="ssrf_blocked")
    return value


async def _require_public_dns(hostname: str) -> None:
    try:
        records = await asyncio.to_thread(
            socket.getaddrinfo, hostname, 443, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise SourceResolutionError(
            "Source host could not be resolved.", code="dns_unavailable", retryable=True
        ) from exc
    addresses = {ipaddress.ip_address(record[4][0]) for record in records}
    # Managed HTTPS proxies may synthesize RFC 2544 benchmark addresses for
    # public DNS names. Direct 198.18/15 URL literals are still rejected by
    # _require_safe_https_url; only a hostname resolution may use this range.
    def public_or_proxy_synthetic(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return address.is_global or address in _HTTPS_PROXY_SYNTHETIC_NETWORK

    if not addresses or any(not public_or_proxy_synthetic(value) for value in addresses):
        raise SourceResolutionError("Private source hosts are forbidden.", code="ssrf_blocked")


async def _download(
    url: str,
    *,
    accept: str,
    allowed_mime_prefixes: tuple[str, ...],
) -> tuple[bytes, str, str]:
    current_url = _require_safe_https_url(url)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(_ATTEMPT_TIMEOUT_SECONDS), follow_redirects=False
    ) as client:
        for redirect_index in range(_MAX_REDIRECTS + 1):
            parsed = urlparse(current_url)
            await _require_public_dns(str(parsed.hostname))
            try:
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
    try:
        from pdfminer.high_level import extract_text
    except ImportError as exc:
        raise SourceResolutionError(
            "PDF text extraction is unavailable in this runtime.",
            code="pdf_extractor_unavailable",
        ) from exc
    try:
        return extract_text(io.BytesIO(payload))
    except Exception as exc:
        raise SourceResolutionError(
            "PDF text extraction failed.", code="pdf_text_invalid"
        ) from exc


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
    words = {word for word in re.split(r"[^a-z0-9ρ]+", text) if len(word) >= 2}
    return words or ({compact} if compact else set())


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).lower()


def _label_token_present(token: str, window: str) -> re.Match[str] | None:
    """Boundary-aware label containment.

    Codex review P1 (PR #46, round 6): bare substring matching let "ns"
    match inside "constraints". A label token must stand on its own
    alphanumeric boundary.
    """
    return re.search(
        rf"(?<![a-z0-9ρ_]){re.escape(token)}(?![a-z0-9ρ_])", window
    )


def _locator_fragment_alternatives(fragment: str) -> tuple[str, ...]:
    """Accepted renderings of one locator fragment inside a compacted window."""
    alternatives = [fragment]
    equation_number = re.search(r"(?:equation|eq\.?)\s*(\d+)", fragment, re.I)
    if equation_number:
        number = equation_number.group(1)
        alternatives.extend((f"({number})", f"eq. {number}", f"equation {number}"))
    return tuple(alternatives)


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
        columns = " ".join(str(value) for value in table.get("columns") or [])
        for row in table.get("rows") or []:
            row_text = " ".join(str(value) for value in row) if isinstance(row, list) else str(row)
            windows.append(_compact_text(f"{table_header} {columns} {row_text}"))
    text = _compact_text(document.get("text"))
    if text:
        if locator_text:
            locator_indices: list[int] = []
            exact_index = text.find(locator_text)
            if exact_index >= 0:
                locator_indices.append(exact_index)
            for locator_part in (
                part.strip() for part in locator_text.split(",") if part.strip()
            ):
                locator_indices.extend(
                    match.start()
                    for match in list(
                        re.finditer(re.escape(locator_part), text, re.I)
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
                windows.append(
                    text[max(0, locator_index - 500) : locator_index + 12000]
                )
        # Codex review P1 (PR #46): the document-head fallback may only run
        # when no locator was requested or the requested locator was actually
        # found. Otherwise labels and numbers occurring elsewhere in the paper
        # would earn verified_exact for a table/equation that was never
        # checked — a false source attribution.
        if not locator_text or locator_indices:
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
                    any(
                        alternative in window
                        for alternative in _locator_fragment_alternatives(fragment)
                    )
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
    r"(?<![0-9.eE+−-])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?!\.?[0-9]|[eE][-+]?[0-9])"
)


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
    verified. Sorting the labeled claims by their first label occurrence and
    greedily assigning each claim's value to a strictly later position
    accepts both prose ("Planck ... 67.36 ... SH0ES ... 73.04") and
    column-major table rows, while rejecting swapped assignments. A prose
    order reversed relative to the label order fails toward
    resolved_unmatched, which is the safe direction.
    """
    ordered: list[tuple[int, dict[str, Any]]] = []
    for claim in expected_claims:
        labels = _label_tokens(claim.get("label") or claim.get("id"))
        if not labels:
            continue
        label_positions = [
            match.start()
            for match in (_label_token_present(label, window) for label in labels)
            if match is not None
        ]
        if not label_positions:
            continue
        ordered.append((min(label_positions), claim))
    if not ordered:
        return True
    ordered.sort(key=lambda item: item[0])
    cursor = -1
    for _, claim in ordered:
        try:
            value = float(claim["value"])
        except (KeyError, TypeError, ValueError):
            continue
        uncertainty: float | None
        try:
            uncertainty = float(claim["standard_uncertainty"])
        except (KeyError, TypeError, ValueError):
            uncertainty = None
        chosen: int | None = None
        for position in _value_positions(value, window):
            if position <= cursor:
                continue
            # Codex review P1 (PR #46, round 6): the uncertainty must be
            # bound to its own value — it has to be the immediately
            # following numeric token, as in "17.351 +/- 0.177". A swapped
            # pair like "alpha 10 +/- 2" fails here instead of matching
            # window-wide.
            if uncertainty is not None:
                following = _GENERIC_NUMBER_TOKEN.search(
                    window, position + len(f"{value:g}")
                )
                if following is None:
                    continue
                if following.start() not in _value_positions(
                    uncertainty, window
                ):
                    continue
            chosen = position
            break
        if chosen is None:
            return False
        cursor = chosen
    return True


def match_expected_claims(
    document: dict[str, Any], expected_claims: list[dict[str, Any]], *, locator: str
) -> tuple[str, dict[str, Any]]:
    """Require every label and numeric value to coexist in one source window."""
    if not expected_claims:
        return "resolved_unmatched", {"reason": "no_expected_claims"}
    windows = _candidate_windows(document, locator)
    label_seen = False
    compact_source_locator = _compact_text(locator)
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
                    any(
                        alternative in window
                        for alternative in _locator_fragment_alternatives(fragment)
                    )
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
    claims_digest = hashlib.sha256(
        repr(
            sorted(
                (
                    str(claim.get("id") or ""),
                    str(claim.get("label") or ""),
                    claim.get("value"),
                    claim.get("standard_uncertainty"),
                    str(claim.get("source_locator") or ""),
                )
                for claim in expected_claims
            )
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"lightweight_source_v6:{source.kind}:{source.identifier}:{source.locator}:{claims_digest}"


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

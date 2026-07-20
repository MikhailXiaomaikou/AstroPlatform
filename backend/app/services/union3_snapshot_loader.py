"""Secure, version-pinned acquisition of the registered Union3 arXiv source."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse

import httpx

from app.services.union3_reader import (
    UNION3_ARXIV_ID,
    UNION3_ATOM_URL,
    UNION3_PDF_SHA256,
    UNION3_SOURCE_TAR_SHA256,
    UNION3_SOURCE_TAR_URL,
    UNION3_SOURCE_URL,
    Union3PdfTextSnapshot,
    Union3ReaderError,
)
from app.storage import download_fits, upload_fits

_ALLOWED_HOSTS = frozenset({"arxiv.org", "export.arxiv.org"})
_MAX_RESPONSE_BYTES = 100 * 1024 * 1024
_MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
_MAX_TAR_ENTRIES = 10_000
_MAX_REDIRECTS = 3
_REQUEST_TIMEOUT_SECONDS = 30.0
_MAX_TEXT_BYTES = 32 * 1024 * 1024
_ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
_ARXIV_NAMESPACE = "http://arxiv.org/schemas/atom"
_SNAPSHOT_PREFIX = f"source-snapshots/union3/{UNION3_ARXIV_ID}"
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _metadata_artifact_ref(sha256: str) -> str:
    return f"{_SNAPSHOT_PREFIX}/{sha256}.atom.xml"


def _source_tar_artifact_ref() -> str:
    return f"{_SNAPSHOT_PREFIX}/{UNION3_SOURCE_TAR_SHA256}.tar.gz"


def _pdf_artifact_ref() -> str:
    return f"{_SNAPSHOT_PREFIX}/{UNION3_PDF_SHA256}.pdf"


def _require_allowed_url(value: str) -> str:
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise Union3ReaderError(
            "source_redirect_rejected",
            "The registered source URL has an invalid authority component",
        ) from exc
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        raise Union3ReaderError(
            "source_redirect_rejected",
            "The registered source redirected outside the arXiv allowlist",
        )
    if parsed.username or parsed.password or port not in {None, 443}:
        raise Union3ReaderError(
            "source_redirect_rejected",
            "The registered source URL contains a forbidden authority component",
        )
    return value


async def _download_registered_artifact(
    *,
    url: str,
    accept: str,
    artifact_label: str,
) -> tuple[bytes, str, str]:
    """Download one allowlisted artifact with explicit redirect and size limits."""

    current_url = url
    timeout = httpx.Timeout(_REQUEST_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for redirect_count in range(_MAX_REDIRECTS + 1):
            _require_allowed_url(current_url)
            try:
                async with client.stream(
                    "GET",
                    current_url,
                    headers={"Accept": accept, "User-Agent": "Standard-Astro/0.5"},
                ) as response:
                    if response.status_code in _REDIRECT_STATUS_CODES:
                        if redirect_count >= _MAX_REDIRECTS:
                            raise Union3ReaderError(
                                "source_redirect_limit_exceeded",
                                "The registered source exceeded three redirects",
                                retryable=True,
                            )
                        location = response.headers.get("location")
                        if not location:
                            raise Union3ReaderError(
                                "source_redirect_invalid",
                                "The registered source returned an invalid redirect",
                            )
                        current_url = _require_allowed_url(
                            urljoin(current_url, location)
                        )
                        continue
                    if response.status_code != 200:
                        raise Union3ReaderError(
                            f"authoritative_{artifact_label}_http_error",
                            f"The authoritative Union3 {artifact_label} could not be downloaded",
                            retryable=(
                                response.status_code >= 500
                                or response.status_code == 429
                            ),
                        )
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            advertised_length = int(content_length)
                        except ValueError as exc:
                            raise Union3ReaderError(
                                "source_content_length_invalid",
                                "The registered source returned an invalid content length",
                            ) from exc
                        if advertised_length < 0:
                            raise Union3ReaderError(
                                "source_content_length_invalid",
                                "The registered source returned an invalid content length",
                            )
                        if advertised_length > _MAX_RESPONSE_BYTES:
                            raise Union3ReaderError(
                                "source_response_too_large",
                                "The registered source response exceeds 100 MB",
                            )
                    chunks: list[bytes] = []
                    observed = 0
                    async for chunk in response.aiter_bytes():
                        observed += len(chunk)
                        if observed > _MAX_RESPONSE_BYTES:
                            raise Union3ReaderError(
                                "source_response_too_large",
                                "The registered source response exceeds 100 MB",
                            )
                        chunks.append(chunk)
                    content_type = response.headers.get("content-type", "").lower()
                    return b"".join(chunks), current_url, content_type
            except Union3ReaderError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                raise Union3ReaderError(
                    f"authoritative_{artifact_label}_load_failed",
                    f"The authoritative Union3 {artifact_label} could not be loaded",
                    retryable=True,
                ) from exc
    raise Union3ReaderError(
        "source_redirect_limit_exceeded",
        "The registered source exceeded three redirects",
        retryable=True,
    )


def _normalized_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _parse_and_verify_atom_metadata(atom_bytes: bytes) -> dict[str, object]:
    """Verify that Atom metadata identifies exactly arXiv 2311.12098v4."""

    lowered_document = atom_bytes.lower()
    if b"<!doctype" in lowered_document or b"<!entity" in lowered_document:
        raise Union3ReaderError(
            "source_metadata_xml_rejected",
            "The arXiv metadata contains a forbidden XML declaration",
        )
    try:
        root = ET.fromstring(atom_bytes)
    except ET.ParseError as exc:
        raise Union3ReaderError(
            "source_metadata_invalid",
            "The registered arXiv Atom metadata is not well formed",
        ) from exc
    if root.tag != f"{{{_ATOM_NAMESPACE}}}feed":
        raise Union3ReaderError(
            "source_metadata_invalid",
            "The registered arXiv metadata is not an Atom feed",
        )
    entries = root.findall(f"{{{_ATOM_NAMESPACE}}}entry")
    if len(entries) != 1:
        raise Union3ReaderError(
            "source_metadata_identity_mismatch",
            "The arXiv metadata must contain exactly one registered entry",
        )
    entry = entries[0]
    entry_id = _normalized_text(entry.find(f"{{{_ATOM_NAMESPACE}}}id"))
    try:
        parsed_entry_id = urlparse(entry_id)
        entry_port = parsed_entry_id.port
    except ValueError as exc:
        raise Union3ReaderError(
            "source_metadata_identity_mismatch",
            "The arXiv metadata entry identifier is invalid",
        ) from exc
    expected_path = f"/abs/{UNION3_ARXIV_ID}"
    if (
        parsed_entry_id.scheme not in {"http", "https"}
        or parsed_entry_id.hostname != "arxiv.org"
        or entry_port not in {None, 80, 443}
        or parsed_entry_id.username
        or parsed_entry_id.password
        or parsed_entry_id.path.rstrip("/") != expected_path
        or parsed_entry_id.query
        or parsed_entry_id.fragment
    ):
        raise Union3ReaderError(
            "source_metadata_identity_mismatch",
            "The arXiv metadata does not identify exactly 2311.12098v4",
        )

    pdf_links: list[str] = []
    for link in entry.findall(f"{{{_ATOM_NAMESPACE}}}link"):
        href = str(link.attrib.get("href") or "").strip()
        media_type = str(link.attrib.get("type") or "").lower()
        title = str(link.attrib.get("title") or "").lower()
        if href and (media_type == "application/pdf" or title == "pdf"):
            pdf_links.append(href)
    expected_pdf_path = f"/pdf/{UNION3_ARXIV_ID}"
    matching_pdf_links = []
    for link in pdf_links:
        try:
            parsed_link = urlparse(link)
            link_port = parsed_link.port
        except ValueError:
            continue
        if (
            parsed_link.scheme in {"http", "https"}
            and parsed_link.hostname == "arxiv.org"
            and link_port in {None, 80, 443}
            and parsed_link.path.removesuffix(".pdf").rstrip("/")
            == expected_pdf_path
            and not parsed_link.query
            and not parsed_link.fragment
        ):
            matching_pdf_links.append(link)
    if len(matching_pdf_links) != 1:
        raise Union3ReaderError(
            "source_metadata_identity_mismatch",
            "The arXiv metadata lacks one exact v4 PDF binding",
        )

    authors = [
        _normalized_text(author.find(f"{{{_ATOM_NAMESPACE}}}name"))
        for author in entry.findall(f"{{{_ATOM_NAMESPACE}}}author")
    ]
    authors = [author for author in authors if author]
    categories = sorted(
        {
            str(category.attrib.get("term") or "").strip()
            for category in entry.findall(f"{{{_ATOM_NAMESPACE}}}category")
            if str(category.attrib.get("term") or "").strip()
        }
    )
    primary_category_node = entry.find(f"{{{_ARXIV_NAMESPACE}}}primary_category")
    primary_category = (
        str(primary_category_node.attrib.get("term") or "").strip()
        if primary_category_node is not None
        else ""
    )
    title = _normalized_text(entry.find(f"{{{_ATOM_NAMESPACE}}}title"))
    published = _normalized_text(entry.find(f"{{{_ATOM_NAMESPACE}}}published"))
    updated = _normalized_text(entry.find(f"{{{_ATOM_NAMESPACE}}}updated"))
    if not title or not published or not updated or not authors:
        raise Union3ReaderError(
            "source_metadata_incomplete",
            "The registered arXiv metadata is missing required identity fields",
        )
    return {
        "schema_version": "union3_arxiv_atom_identity_v1",
        "entry_id": entry_id,
        "canonical_identifier": UNION3_ARXIV_ID,
        "base_identifier": "2311.12098",
        "version": 4,
        "title": title,
        "authors": authors,
        "published": published,
        "updated": updated,
        "primary_category": primary_category or None,
        "categories": categories,
        "pdf_link": matching_pdf_links[0],
    }


def _validate_tar_member_name(name: str) -> str:
    if not name or len(name) > 4096 or "\\" in name or "\x00" in name:
        raise Union3ReaderError(
            "source_tar_path_rejected",
            "The registered source tarball contains an unsafe member path",
        )
    trimmed = name[:-1] if name.endswith("/") else name
    raw_parts = trimmed.split("/")
    path = PurePosixPath(trimmed)
    if (
        not trimmed
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in raw_parts)
        or any(ord(character) < 32 for character in trimmed)
    ):
        raise Union3ReaderError(
            "source_tar_path_rejected",
            "The registered source tarball contains an unsafe member path",
        )
    return path.as_posix()


def _validate_source_tar(source_bytes: bytes) -> dict[str, object]:
    """Inspect the exact source tar in memory without extracting any member."""

    seen: set[str] = set()
    manifest: list[dict[str, object]] = []
    total_size = 0
    regular_file_count = 0
    directory_count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(source_bytes), mode="r:*") as archive:
            for entry_count, member in enumerate(archive, start=1):
                if entry_count > _MAX_TAR_ENTRIES:
                    raise Union3ReaderError(
                        "source_tar_entry_limit_exceeded",
                        "The registered source tarball exceeds 10000 entries",
                    )
                safe_name = _validate_tar_member_name(member.name)
                if safe_name in seen:
                    raise Union3ReaderError(
                        "source_tar_duplicate_member",
                        "The registered source tarball contains duplicate member paths",
                    )
                seen.add(safe_name)
                if member.issym() or member.islnk():
                    raise Union3ReaderError(
                        "source_tar_link_rejected",
                        "The registered source tarball contains a symbolic or hard link",
                    )
                if getattr(member, "issparse", lambda: False)():
                    raise Union3ReaderError(
                        "source_tar_member_type_rejected",
                        "The registered source tarball contains a sparse member",
                    )
                if member.isfile():
                    if member.size < 0:
                        raise Union3ReaderError(
                            "source_tar_size_invalid",
                            "The registered source tarball contains an invalid member size",
                        )
                    total_size += member.size
                    if total_size > _MAX_UNCOMPRESSED_BYTES:
                        raise Union3ReaderError(
                            "source_tar_uncompressed_limit_exceeded",
                            "The registered source tarball exceeds 500 MB uncompressed",
                        )
                    regular_file_count += 1
                    member_type = "file"
                elif member.isdir():
                    directory_count += 1
                    member_type = "directory"
                else:
                    raise Union3ReaderError(
                        "source_tar_member_type_rejected",
                        "The registered source tarball contains a special member type",
                    )
                manifest.append(
                    {
                        "name": safe_name,
                        "size": int(member.size),
                        "type": member_type,
                    }
                )
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise Union3ReaderError(
            "source_tar_invalid",
            "The registered Union3 source artifact is not a valid tar archive",
        ) from exc
    if not manifest:
        raise Union3ReaderError(
            "source_tar_entry_limit_exceeded",
            "The registered source tarball has no entries",
        )
    if "merged.tex" not in seen or "00README.json" not in seen:
        raise Union3ReaderError(
            "source_tar_registered_files_missing",
            "The registered source tarball lacks its expected root source files",
        )
    return {
        "schema_version": "union3_source_tar_validation_v1",
        "entry_count": len(manifest),
        "regular_file_count": regular_file_count,
        "directory_count": directory_count,
        "uncompressed_bytes": total_size,
        "member_manifest_sha256": _sha256_bytes(_canonical_json(manifest)),
        "required_root_files": ["00README.json", "merged.tex"],
        "extracted_to_disk": False,
    }


def _validate_pdf(pdf_bytes: bytes) -> None:
    if not pdf_bytes.startswith(b"%PDF-"):
        raise Union3ReaderError(
            "source_content_type_invalid",
            "The authoritative Union3 PDF has an invalid file signature",
        )


def _require_media_type(content_type: str, *, kind: str) -> None:
    media_type = content_type.split(";", 1)[0].strip()
    allowed = {
        "metadata": {"application/atom+xml", "application/xml", "text/xml"},
        "source_tar": {
            "application/gzip",
            "application/octet-stream",
            "application/x-eprint-tar",
            "application/x-gzip",
            "application/x-tar",
        },
        "pdf": {"application/pdf"},
    }[kind]
    if media_type not in allowed:
        raise Union3ReaderError(
            "source_content_type_invalid",
            f"The registered Union3 {kind} returned an unexpected content type",
        )


async def _load_pinned_artifact(
    *,
    artifact_ref: str,
    expected_sha256: str,
    source_url: str,
    accept: str,
    artifact_label: str,
    media_kind: str,
) -> tuple[bytes, str, str, bool]:
    """Load and checksum a pinned object without storing unvalidated bytes."""

    loaded_from_cache = False
    try:
        payload = await asyncio.to_thread(download_fits, artifact_ref)
        loaded_from_cache = True
        resolved_url = source_url
        content_type = {
            "source_tar": "application/x-eprint-tar",
            "pdf": "application/pdf",
        }[media_kind]
    except FileNotFoundError:
        payload, resolved_url, content_type = await _download_registered_artifact(
            url=source_url,
            accept=accept,
            artifact_label=artifact_label,
        )
        _require_media_type(content_type, kind=media_kind)
    except Exception as exc:
        raise Union3ReaderError(
            "source_snapshot_cache_invalid",
            f"The cached Union3 {artifact_label} failed its storage integrity check",
        ) from exc
    observed_hash = _sha256_bytes(payload)
    if observed_hash != expected_sha256:
        raise Union3ReaderError(
            f"{artifact_label}_checksum_mismatch",
            f"The Union3 {artifact_label} checksum does not match the registered v4 artifact",
        )
    return payload, resolved_url, content_type, loaded_from_cache


async def _store_verified_artifact(
    *, artifact_ref: str, payload: bytes, artifact_label: str
) -> None:
    try:
        uploaded_ref = await asyncio.to_thread(upload_fits, artifact_ref, payload)
    except Exception as exc:
        raise Union3ReaderError(
            "source_snapshot_store_failed",
            f"The verified Union3 {artifact_label} could not be stored durably",
            retryable=True,
        ) from exc
    if uploaded_ref != artifact_ref:
        raise Union3ReaderError(
            "source_snapshot_store_binding_invalid",
            "The source snapshot store returned an unexpected object key",
        )


def _project_pdf_text(pdf_bytes: bytes) -> str:
    with tempfile.TemporaryDirectory(prefix="standard-astro-union3-") as directory:
        pdf_path = Path(directory) / "union3-v4.pdf"
        pdf_path.write_bytes(pdf_bytes)
        projection_environment = os.environ.copy()
        if sys.platform == "darwin" and projection_environment.get("LC_ALL") == "C.UTF-8":
            # macOS ships en_US.UTF-8 but not the GNU/Linux C.UTF-8 locale.
            projection_environment["LC_ALL"] = "en_US.UTF-8"
            projection_environment["LANG"] = "en_US.UTF-8"
        try:
            completed = subprocess.run(
                ["pdftotext", "-raw", "-enc", "UTF-8", str(pdf_path), "-"],
                check=False,
                capture_output=True,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                env=projection_environment,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise Union3ReaderError(
                "pdf_text_projection_unavailable",
                "The deterministic PDF text projection is unavailable",
                retryable=True,
            ) from exc
    if completed.returncode != 0 or not completed.stdout:
        raise Union3ReaderError(
            "pdf_text_projection_failed",
            "The deterministic PDF text projection failed",
            retryable=True,
        )
    if len(completed.stdout) > _MAX_TEXT_BYTES:
        raise Union3ReaderError(
            "pdf_text_projection_too_large",
            "The PDF text projection exceeds its safety limit",
        )
    try:
        return completed.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Union3ReaderError(
            "pdf_text_projection_invalid",
            "The PDF text projection is not valid UTF-8",
        ) from exc


async def _acquire_and_store_metadata() -> tuple[
    bytes,
    str,
    str,
    str,
    dict[str, object],
]:
    metadata_bytes, resolved_url, content_type = await _download_registered_artifact(
        url=UNION3_ATOM_URL,
        accept="application/atom+xml, application/xml;q=0.9",
        artifact_label="metadata",
    )
    _require_media_type(content_type, kind="metadata")
    metadata_identity = _parse_and_verify_atom_metadata(metadata_bytes)
    metadata_sha256 = _sha256_bytes(metadata_bytes)
    artifact_ref = _metadata_artifact_ref(metadata_sha256)
    try:
        uploaded_ref = await asyncio.to_thread(
            upload_fits, artifact_ref, metadata_bytes
        )
    except Exception as exc:
        raise Union3ReaderError(
            "source_snapshot_store_failed",
            "The verified Union3 Atom metadata could not be stored durably",
            retryable=True,
        ) from exc
    if uploaded_ref != artifact_ref:
        raise Union3ReaderError(
            "source_snapshot_store_binding_invalid",
            "The source snapshot store returned an unexpected object key",
        )
    return (
        metadata_bytes,
        resolved_url,
        content_type,
        artifact_ref,
        metadata_identity,
    )


async def fetch_registered_union3_snapshot(
    canonical_identifier: str,
) -> Union3PdfTextSnapshot:
    """Acquire Atom metadata, exact source tar, then exact PDF and project it."""

    if canonical_identifier != UNION3_ARXIV_ID:
        raise Union3ReaderError(
            "source_identifier_not_registered",
            "The source identifier is not registered for the Union3 reader",
        )

    # This order is part of the scientific source contract.  Metadata proves
    # the requested version before any versioned bytes are accepted; the source
    # tar is preserved and inspected before the PDF becomes extractable evidence.
    (
        metadata_bytes,
        metadata_resolved_url,
        metadata_content_type,
        metadata_artifact_ref,
        metadata_identity,
    ) = await _acquire_and_store_metadata()
    (
        source_tar_bytes,
        source_tar_resolved_url,
        source_tar_content_type,
        source_tar_from_cache,
    ) = (
        await _load_pinned_artifact(
            artifact_ref=_source_tar_artifact_ref(),
            expected_sha256=UNION3_SOURCE_TAR_SHA256,
            source_url=UNION3_SOURCE_TAR_URL,
            accept="application/x-eprint-tar, application/x-tar, application/gzip",
            artifact_label="source_tar",
            media_kind="source_tar",
        )
    )
    source_tar_validation = await asyncio.to_thread(
        _validate_source_tar, source_tar_bytes
    )
    if not source_tar_from_cache:
        await _store_verified_artifact(
            artifact_ref=_source_tar_artifact_ref(),
            payload=source_tar_bytes,
            artifact_label="source_tar",
        )
    (
        pdf_bytes,
        pdf_resolved_url,
        pdf_content_type,
        pdf_from_cache,
    ) = await _load_pinned_artifact(
        artifact_ref=_pdf_artifact_ref(),
        expected_sha256=UNION3_PDF_SHA256,
        source_url=UNION3_SOURCE_URL,
        accept="application/pdf",
        artifact_label="pdf",
        media_kind="pdf",
    )
    _validate_pdf(pdf_bytes)
    text = await asyncio.to_thread(_project_pdf_text, pdf_bytes)
    if not pdf_from_cache:
        await _store_verified_artifact(
            artifact_ref=_pdf_artifact_ref(),
            payload=pdf_bytes,
            artifact_label="pdf",
        )

    return Union3PdfTextSnapshot(
        text=text,
        pdf_sha256=UNION3_PDF_SHA256,
        metadata_sha256=_sha256_bytes(metadata_bytes),
        source_tar_sha256=UNION3_SOURCE_TAR_SHA256,
        metadata_identity=metadata_identity,
        source_tar_validation=source_tar_validation,
        metadata_artifact_ref=metadata_artifact_ref,
        source_tar_artifact_ref=_source_tar_artifact_ref(),
        metadata_resolved_url=metadata_resolved_url,
        source_tar_resolved_url=source_tar_resolved_url,
        pdf_resolved_url=pdf_resolved_url,
        metadata_content_type="application/atom+xml",
        source_tar_content_type="application/gzip",
        metadata_byte_size=len(metadata_bytes),
        source_tar_byte_size=len(source_tar_bytes),
        pdf_byte_size=len(pdf_bytes),
        source_url=UNION3_SOURCE_URL,
        content_type="application/pdf",
        artifact_ref=_pdf_artifact_ref(),
    )


__all__ = ["fetch_registered_union3_snapshot"]

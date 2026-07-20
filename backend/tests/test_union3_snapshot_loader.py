"""Security and acquisition-order tests for the Union3 source-chain loader."""

from __future__ import annotations

import hashlib
import io
import tarfile

import httpx
import pytest

from app.services import union3_snapshot_loader as loader
from app.services.union3_reader import (
    UNION3_ARXIV_ID,
    UNION3_ATOM_URL,
    UNION3_SOURCE_TAR_URL,
    UNION3_SOURCE_URL,
    Union3ReaderError,
)


def _atom(identifier: str = UNION3_ARXIV_ID) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/{identifier}</id>
    <updated>2025-06-21T00:00:00Z</updated>
    <published>2023-11-20T00:00:00Z</published>
    <title>Union Through UNITY</title>
    <author><name>D. Rubin</name></author>
    <category term="astro-ph.CO" />
    <arxiv:primary_category term="astro-ph.CO" />
    <link title="pdf" href="http://arxiv.org/pdf/{identifier}"
          rel="related" type="application/pdf" />
  </entry>
</feed>
""".encode()


def _tar_bytes(
    *, unsafe_name: str | None = None, special_type: bytes | None = None
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in (
            ("00README.json", b"{}"),
            ("merged.tex", b"registered source"),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mtime = 0
            archive.addfile(member, io.BytesIO(content))
        if unsafe_name is not None:
            member = tarfile.TarInfo(unsafe_name)
            content = b"unsafe"
            member.size = len(content)
            member.mtime = 0
            archive.addfile(member, io.BytesIO(content))
        if special_type is not None:
            member = tarfile.TarInfo("special-member")
            member.type = special_type
            if special_type in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                member.linkname = "merged.tex"
            member.mtime = 0
            archive.addfile(member)
    return output.getvalue()


async def test_loader_acquires_metadata_source_then_pdf_and_stores_by_hash(
    monkeypatch,
):
    atom_bytes = _atom()
    source_tar = _tar_bytes()
    pdf_bytes = b"%PDF-1.7\nregistered-union3-test\n%%EOF\n"
    source_sha = hashlib.sha256(source_tar).hexdigest()
    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    monkeypatch.setattr(loader, "UNION3_SOURCE_TAR_SHA256", source_sha)
    monkeypatch.setattr(loader, "UNION3_PDF_SHA256", pdf_sha)
    monkeypatch.setattr(
        loader,
        "download_fits",
        lambda _key: (_ for _ in ()).throw(FileNotFoundError()),
    )
    uploads: list[tuple[str, bytes]] = []

    def _upload(key: str, value: bytes) -> str:
        uploads.append((key, bytes(value)))
        return key

    monkeypatch.setattr(loader, "upload_fits", _upload)
    monkeypatch.setattr(loader, "_project_pdf_text", lambda _value: "Table 9 test")
    requested_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested_urls.append(url)
        if url == UNION3_ATOM_URL:
            return httpx.Response(
                200,
                headers={"content-type": "application/atom+xml"},
                content=atom_bytes,
            )
        if url == UNION3_SOURCE_TAR_URL:
            return httpx.Response(
                200,
                headers={"content-type": "application/gzip"},
                content=source_tar,
            )
        if url == UNION3_SOURCE_URL:
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=pdf_bytes,
            )
        raise AssertionError(f"unexpected URL: {url}")

    original_client = httpx.AsyncClient

    def _client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(_handler), **kwargs)

    monkeypatch.setattr(loader.httpx, "AsyncClient", _client_factory)
    snapshot = await loader.fetch_registered_union3_snapshot(UNION3_ARXIV_ID)

    assert requested_urls == [
        UNION3_ATOM_URL,
        UNION3_SOURCE_TAR_URL,
        UNION3_SOURCE_URL,
    ]
    assert [key.rsplit("/", 1)[-1] for key, _value in uploads] == [
        f"{hashlib.sha256(atom_bytes).hexdigest()}.atom.xml",
        f"{source_sha}.tar.gz",
        f"{pdf_sha}.pdf",
    ]
    assert snapshot.metadata_identity["canonical_identifier"] == UNION3_ARXIV_ID
    assert snapshot.source_tar_validation["extracted_to_disk"] is False
    assert snapshot.source_tar_validation["entry_count"] == 2
    assert snapshot.source_tar_sha256 == source_sha
    assert snapshot.pdf_sha256 == pdf_sha


@pytest.mark.parametrize("unsafe_name", ["../escape.tex", "/absolute.tex", "a\\b.tex"])
def test_source_tar_rejects_path_traversal(unsafe_name):
    with pytest.raises(Union3ReaderError) as error:
        loader._validate_source_tar(_tar_bytes(unsafe_name=unsafe_name))
    assert error.value.error_class == "source_tar_path_rejected"


@pytest.mark.parametrize(
    ("special_type", "error_class"),
    [
        (tarfile.SYMTYPE, "source_tar_link_rejected"),
        (tarfile.LNKTYPE, "source_tar_link_rejected"),
        (tarfile.FIFOTYPE, "source_tar_member_type_rejected"),
        (tarfile.CHRTYPE, "source_tar_member_type_rejected"),
    ],
)
def test_source_tar_rejects_links_and_special_members(special_type, error_class):
    with pytest.raises(Union3ReaderError) as error:
        loader._validate_source_tar(_tar_bytes(special_type=special_type))
    assert error.value.error_class == error_class


def test_source_tar_enforces_entry_and_uncompressed_limits(monkeypatch):
    monkeypatch.setattr(loader, "_MAX_TAR_ENTRIES", 1)
    with pytest.raises(Union3ReaderError) as entry_error:
        loader._validate_source_tar(_tar_bytes())
    assert entry_error.value.error_class == "source_tar_entry_limit_exceeded"

    monkeypatch.setattr(loader, "_MAX_TAR_ENTRIES", 10_000)
    monkeypatch.setattr(loader, "_MAX_UNCOMPRESSED_BYTES", 1)
    with pytest.raises(Union3ReaderError) as size_error:
        loader._validate_source_tar(_tar_bytes())
    assert size_error.value.error_class == "source_tar_uncompressed_limit_exceeded"


def test_atom_metadata_must_bind_exact_version_and_reject_entities():
    identity = loader._parse_and_verify_atom_metadata(_atom())
    assert identity["canonical_identifier"] == UNION3_ARXIV_ID
    assert identity["version"] == 4

    with pytest.raises(Union3ReaderError) as version_error:
        loader._parse_and_verify_atom_metadata(_atom("2311.12098v3"))
    assert version_error.value.error_class == "source_metadata_identity_mismatch"

    with pytest.raises(Union3ReaderError) as entity_error:
        loader._parse_and_verify_atom_metadata(
            b'<!DOCTYPE feed [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>' + _atom()
        )
    assert entity_error.value.error_class == "source_metadata_xml_rejected"


async def test_downloader_rejects_off_allowlist_redirect_and_oversize(monkeypatch):
    original_client = httpx.AsyncClient

    def _redirect_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/evil"})

    monkeypatch.setattr(
        loader.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(_redirect_handler), **kwargs
        ),
    )
    with pytest.raises(Union3ReaderError) as redirect_error:
        await loader._download_registered_artifact(
            url=UNION3_ATOM_URL,
            accept="application/atom+xml",
            artifact_label="metadata",
        )
    assert redirect_error.value.error_class == "source_redirect_rejected"

    def _oversize_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "application/atom+xml",
                "content-length": str(loader._MAX_RESPONSE_BYTES + 1),
            },
            content=b"x",
        )

    monkeypatch.setattr(
        loader.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(_oversize_handler), **kwargs
        ),
    )
    with pytest.raises(Union3ReaderError) as size_error:
        await loader._download_registered_artifact(
            url=UNION3_ATOM_URL,
            accept="application/atom+xml",
            artifact_label="metadata",
        )
    assert size_error.value.error_class == "source_response_too_large"

    def _looping_redirect_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": str(request.url)})

    monkeypatch.setattr(
        loader.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(_looping_redirect_handler), **kwargs
        ),
    )
    with pytest.raises(Union3ReaderError) as limit_error:
        await loader._download_registered_artifact(
            url=UNION3_ATOM_URL,
            accept="application/atom+xml",
            artifact_label="metadata",
        )
    assert limit_error.value.error_class == "source_redirect_limit_exceeded"

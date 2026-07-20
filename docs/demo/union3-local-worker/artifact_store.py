#!/usr/bin/env python
"""Localhost-only object-store fixture for the Union3 recorder."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlparse

from artifact_capability import (
    MAX_DEMO_ARTIFACT_BYTES,
    ArtifactCapability,
    decode_key,
    verify_signature,
)


def _safe_destination(root: Path, key: str) -> Path:
    if "\\" in key or any(ord(ch) < 32 for ch in key):
        raise ValueError("invalid_artifact_key")
    pure = PurePosixPath(key)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("invalid_artifact_key")
    destination = root.joinpath(*pure.parts)
    if not destination.resolve(strict=False).is_relative_to(root):
        raise ValueError("invalid_artifact_key")
    return destination


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


class ArtifactStoreHandler(BaseHTTPRequestHandler):
    server: "ArtifactStoreServer"

    def _respond(self, status: int, body: bytes = b"") -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._respond(200, b"ok\n")
        else:
            self._respond(404, b"not found\n")

    def do_PUT(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            prefix = "/upload/"
            if not parsed.path.startswith(prefix):
                raise ValueError("invalid_upload_path")
            key = decode_key(parsed.path[len(prefix) :])
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)

            def one(name: str) -> str:
                values = query.get(name, [])
                if len(values) != 1:
                    raise ValueError(f"invalid_{name}")
                return values[0]

            digest = one("sha256").lower()
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise ValueError("invalid_sha256")
            size_bytes = int(one("size_bytes"))
            expires_at = int(one("expires_at"))
            content_type = one("content_type")
            signature = one("signature").lower()
            now = int(time.time())
            if expires_at < now or expires_at > now + 60 * 60:
                raise PermissionError("expired_upload_capability")
            if size_bytes <= 0 or size_bytes > MAX_DEMO_ARTIFACT_BYTES:
                raise ValueError("invalid_size_bytes")
            if self.headers.get("Content-Type", "") != content_type:
                raise PermissionError("content_type_binding_mismatch")
            if int(self.headers.get("Content-Length", "-1")) != size_bytes:
                raise PermissionError("content_length_binding_mismatch")
            capability = ArtifactCapability(
                key=key,
                sha256=digest,
                size_bytes=size_bytes,
                content_type=content_type,
                expires_at=expires_at,
            )
            if not verify_signature(self.server.secret, capability, signature):
                raise PermissionError("invalid_upload_signature")
            payload = self.rfile.read(size_bytes)
            if len(payload) != size_bytes or not hmac.compare_digest(
                hashlib.sha256(payload).hexdigest(), digest
            ):
                raise PermissionError("artifact_integrity_mismatch")
            destination = _safe_destination(self.server.root, key)
            if destination.is_file():
                existing = destination.read_bytes()
                if len(existing) != size_bytes or not hmac.compare_digest(
                    hashlib.sha256(existing).hexdigest(), digest
                ):
                    self._respond(409, b"conflicting object\n")
                    return
            else:
                _atomic_write(destination, payload)
                _atomic_write(
                    destination.with_name(destination.name + ".sha256"),
                    f"{digest}\n".encode("ascii"),
                )
            self._respond(200, b"stored\n")
        except PermissionError as exc:
            self._respond(403, f"{exc}\n".encode("utf-8"))
        except (TypeError, ValueError) as exc:
            self._respond(400, f"{exc}\n".encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        return


class ArtifactStoreServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], *, root: Path, secret: str) -> None:
        super().__init__(address, ArtifactStoreHandler)
        self.root = root.resolve()
        self.secret = secret


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    secret = os.environ.get("DEMO_ARTIFACT_UPLOAD_SECRET", "")
    if len(secret) < 32:
        raise SystemExit("DEMO_ARTIFACT_UPLOAD_SECRET must contain at least 32 characters")
    args.root.mkdir(parents=True, exist_ok=True)
    server = ArtifactStoreServer(
        ("127.0.0.1", args.port),
        root=args.root,
        secret=secret,
    )
    server.serve_forever(poll_interval=0.2)


if __name__ == "__main__":
    main()

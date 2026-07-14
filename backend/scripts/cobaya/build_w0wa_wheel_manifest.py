#!/usr/bin/env python3
"""Build the pre-run PyPI wheel trust manifest for the exact w0wa profile.

This maintenance command resolves each exact version pin to the highest-priority
wheel compatible with the interpreter running the command. It records PyPI's
published SHA-256 and URL; the canonical preflight separately requires the
matching archives to exist locally and re-hashes their bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Sequence

from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename


_PIN_RE = re.compile(r"^([^#;\s][^=]*)==([^\s]+)$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _read_pins(path: Path) -> list[tuple[str, str]]:
    pins: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN_RE.fullmatch(line)
        if match is None:
            raise ValueError(f"line {line_number} is not one exact version pin")
        name, version = (part.strip() for part in match.groups())
        canonical_name = canonicalize_name(name)
        if canonical_name in seen:
            raise ValueError(f"duplicate project pin: {canonical_name}")
        seen.add(canonical_name)
        pins.append((canonical_name, version))
    if not pins:
        raise ValueError("requirements lock has no exact pins")
    return pins


def _pypi_json(project: str, version: str) -> dict[str, Any]:
    encoded_project = urllib.parse.quote(project, safe="")
    encoded_version = urllib.parse.quote(version, safe="")
    url = f"https://pypi.org/pypi/{encoded_project}/{encoded_version}/json"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "astro-platform-wheel-lock/1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError(f"PyPI returned a non-object for {project}=={version}")
    return payload


def _select_wheel(
    project: str,
    version: str,
    files: Sequence[dict[str, Any]],
    tag_priority: dict[Any, int],
) -> dict[str, Any]:
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for record in files:
        filename = str(record.get("filename") or "")
        if not filename.endswith(".whl") or record.get("yanked") is True:
            continue
        try:
            wheel_name, wheel_version, _build, tags = parse_wheel_filename(filename)
        except ValueError:
            continue
        if canonicalize_name(wheel_name) != project or str(wheel_version) != version:
            continue
        compatible = [tag_priority[tag] for tag in tags if tag in tag_priority]
        if compatible:
            candidates.append((min(compatible), filename, record))
    if not candidates:
        raise ValueError(
            f"no compatible non-yanked wheel for {project}=={version} on this interpreter"
        )
    _priority, filename, selected = min(candidates, key=lambda item: (item[0], item[1]))
    digests = selected.get("digests") or {}
    sha256 = str(digests.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError(f"PyPI wheel has no valid SHA-256: {filename}")
    return {
        "project": project,
        "version": version,
        "filename": filename,
        "sha256": f"sha256:{sha256}",
        "size_bytes": int(selected["size"]),
        "url": str(selected["url"]),
        "upload_time_iso_8601": str(selected.get("upload_time_iso_8601") or ""),
        "source_api": f"https://pypi.org/pypi/{project}/{version}/json",
    }


def build_manifest(requirements_path: Path) -> dict[str, Any]:
    tags = list(sys_tags())
    priority = {tag: index for index, tag in enumerate(tags)}
    wheels = []
    for project, version in _read_pins(requirements_path):
        payload = _pypi_json(project, version)
        wheels.append(
            _select_wheel(project, version, payload.get("urls") or [], priority)
        )
    return {
        "schema_version": 1,
        "profile_id": "desi_2024_vi_table3_desi_cmb_pantheonplus_v1",
        "created_before_smoke_or_formal_run": True,
        "requirements_path": requirements_path.name,
        "requirements_sha256": _sha256(requirements_path),
        "python_version": sys.version,
        "platform_tags": [str(tag) for tag in tags],
        "selection_rule": "highest_priority_non_yanked_compatible_pypi_wheel",
        "wheels": wheels,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = build_manifest(args.requirements.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(manifest['wheels'])} wheel records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

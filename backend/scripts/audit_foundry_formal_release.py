#!/usr/bin/env python3
"""Deterministic, offline supply-chain gates for a Foundry formal release.

The script is intentionally self-contained and has three modes:

* ``static`` verifies the approved tracked source, hash-locked dependency
  declarations, and common live-secret patterns without importing candidate
  code.
* ``environment`` runs inside each final platform image.  It checks that the
  installed environment exactly matches the active lock pins, runs
  ``python -m pip check``, and inventories declared license evidence.
* ``aggregate`` hashes the static and per-platform receipts together with the
  BuildKit SBOM.  It does not claim vulnerability/CVE coverage because no
  advisory database is consulted.

Every output is canonical JSON plus one trailing newline.  No network access,
third-party scanner, production credential, or candidate module is required.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform as runtime_platform_module
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


POLICY_SCHEMA = "standard_astro_formal_release_policy_v1"
STATIC_SCHEMA = "standard_astro_formal_static_audit_v1"
DEPENDENCY_LOCK_SCHEMA = "standard_astro_dependency_lock_receipt_v1"
SECRET_SCHEMA = "standard_astro_secret_scan_receipt_v1"
DEPENDENCY_ENV_SCHEMA = "standard_astro_dependency_integrity_receipt_v1"
LICENSE_SCHEMA = "standard_astro_license_policy_receipt_v1"
ENVIRONMENT_SCHEMA = "standard_astro_formal_environment_audit_v1"
AGGREGATE_SCHEMA = "standard_astro_formal_release_audit_v1"
SOURCE_MANIFEST_SCHEMA = "standard_astro_tracked_source_manifest_v1"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*")
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)(\[[^\]]+\])?==([^\s;]+)(?:\s*;\s*(.+))?$")
_HASH_FLAG = re.compile(r"(?:^|\s)--hash=sha256:([0-9a-f]{64})(?=\s|$)")
_MAX_POLICY_BYTES = 256 * 1024
_MAX_MANIFEST_BYTES = 32 * 1024 * 1024
_MAX_SOURCE_BYTES = 512 * 1024 * 1024
_MAX_LICENSE_FILE_BYTES = 4 * 1024 * 1024

# Assemble prefixes so scanning this trusted script does not match its own
# pattern definitions.  Findings never include the matched credential bytes.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    (
        "private_key_block",
        re.compile(
            rb"-----" + rb"BEGIN (?:RSA |EC |OPENSSH |DSA )?" + rb"PRIVATE KEY-----"
        ),
    ),
    ("aws_access_key_id", re.compile(rb"A" + rb"KIA[0-9A-Z]{16}")),
    (
        "github_legacy_token",
        re.compile(rb"g" + rb"h[pousr]_[A-Za-z0-9]{36,255}"),
    ),
    (
        "github_fine_grained_pat",
        re.compile(rb"github" + rb"_pat_[A-Za-z0-9_]{70,255}"),
    ),
    ("google_api_key", re.compile(rb"AI" + rb"za[0-9A-Za-z_-]{35}")),
    (
        "slack_token",
        re.compile(rb"xo" + rb"x[baprs]-[0-9A-Za-z-]{24,255}"),
    ),
    (
        "openai_live_key",
        re.compile(
            rb"sk-" + rb"(?:proj-|svcacct-)?[A-Za-z0-9_-]{48,255}"
        ),
    ),
    (
        "anthropic_live_key",
        re.compile(rb"sk-ant-" + rb"api[0-9]{2}-[A-Za-z0-9_-]{64,255}"),
    ),
    (
        "stripe_live_secret",
        re.compile(rb"sk_" + rb"live_[A-Za-z0-9]{24,255}"),
    ),
)


class ReleaseAuditError(ValueError):
    """A stable fail-closed release-audit rejection."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _bounded_regular_bytes(path: Path, *, maximum: int, label: str) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReleaseAuditError(f"{label}_unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise ReleaseAuditError(f"{label}_not_bounded_regular_file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            return handle.read(maximum + 1)
    except OSError as exc:
        raise ReleaseAuditError(f"{label}_unavailable") from exc


def _read_json(path: Path, *, maximum: int, label: str) -> Any:
    raw = _bounded_regular_bytes(path, maximum=maximum, label=label)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseAuditError(f"{label}_invalid_json") from exc


def _write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = canonical_json(value) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o644)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
    except FileExistsError as exc:
        raise ReleaseAuditError("release_audit_output_exists") from exc
    return sha256_bytes(encoded)


def _normalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def load_policy(path: Path) -> tuple[dict[str, Any], str]:
    policy = _read_json(
        path,
        maximum=_MAX_POLICY_BYTES,
        label="formal_release_policy",
    )
    if not isinstance(policy, dict) or set(policy) != {
        "schema_version",
        "policy_id",
        "dependency_policy",
        "license_policy",
        "secret_policy",
    }:
        raise ReleaseAuditError("formal_release_policy_shape_invalid")
    if (
        policy.get("schema_version") != POLICY_SCHEMA
        or not isinstance(policy.get("policy_id"), str)
        or not policy["policy_id"]
    ):
        raise ReleaseAuditError("formal_release_policy_identity_invalid")
    dependency = policy.get("dependency_policy")
    if not isinstance(dependency, dict) or set(dependency) != {
        "allow_unlocked_runtime_tools",
        "require_exact_pins",
        "require_sha256_hashes",
        "require_installed_environment_match",
    }:
        raise ReleaseAuditError("dependency_policy_invalid")
    if any(dependency.get(key) is not True for key in (
        "require_exact_pins",
        "require_sha256_hashes",
        "require_installed_environment_match",
    )):
        raise ReleaseAuditError("dependency_policy_weakened")
    runtime_tools = dependency.get("allow_unlocked_runtime_tools")
    if not isinstance(runtime_tools, list) or any(
        not isinstance(item, str) or not item for item in runtime_tools
    ):
        raise ReleaseAuditError("dependency_runtime_tool_allowlist_invalid")
    license_policy = policy.get("license_policy")
    if (
        not isinstance(license_policy, dict)
        or set(license_policy) != {"missing_evidence", "forbidden_markers"}
        or license_policy.get("missing_evidence") != "DENY"
        or not isinstance(license_policy.get("forbidden_markers"), list)
    ):
        raise ReleaseAuditError("license_policy_invalid")
    for marker in license_policy["forbidden_markers"]:
        if (
            not isinstance(marker, dict)
            or set(marker) != {"id", "fragments"}
            or not isinstance(marker.get("id"), str)
            or not marker["id"]
            or not isinstance(marker.get("fragments"), list)
            or not marker["fragments"]
            or any(not isinstance(item, str) or not item for item in marker["fragments"])
        ):
            raise ReleaseAuditError("license_policy_marker_invalid")
    secret_policy = policy.get("secret_policy")
    expected_pattern_ids = [item[0] for item in _SECRET_PATTERNS]
    if (
        not isinstance(secret_policy, dict)
        or set(secret_policy) != {"allowlist", "pattern_ids"}
        or secret_policy.get("pattern_ids") != expected_pattern_ids
        or not isinstance(secret_policy.get("allowlist"), list)
    ):
        raise ReleaseAuditError("secret_policy_invalid")
    for exception in secret_policy["allowlist"]:
        if (
            not isinstance(exception, dict)
            or set(exception) != {"path", "sha256", "pattern_ids"}
            or not str(exception.get("path") or "").startswith(
                ("backend/tests/", "frontend/src/__tests__/")
            )
            or _HEX64.fullmatch(str(exception.get("sha256") or "")) is None
            or not isinstance(exception.get("pattern_ids"), list)
            or not exception["pattern_ids"]
            or any(item not in expected_pattern_ids for item in exception["pattern_ids"])
        ):
            raise ReleaseAuditError("secret_allowlist_entry_invalid")
    raw = _bounded_regular_bytes(
        path,
        maximum=_MAX_POLICY_BYTES,
        label="formal_release_policy",
    )
    return policy, sha256_bytes(raw)


def _logical_requirement_lines(raw: bytes, *, label: str) -> list[str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseAuditError(f"{label}_not_utf8") from exc
    values: list[str] = []
    pending = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        continuation = line.endswith("\\")
        fragment = line[:-1].rstrip() if continuation else line
        pending = f"{pending} {fragment}".strip()
        if continuation:
            continue
        values.append(pending)
        pending = ""
    if pending:
        raise ReleaseAuditError(f"{label}_truncated_continuation")
    return values


def parse_locked_requirements(raw: bytes) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in _logical_requirement_lines(raw, label="requirements_lock"):
        hashes = sorted(set(_HASH_FLAG.findall(line)))
        requirement_text = _HASH_FLAG.sub("", line).strip()
        if requirement_text.startswith(("-", ".", "/")) or " @ " in requirement_text:
            raise ReleaseAuditError("requirements_lock_unpinned_source")
        match = _PIN.fullmatch(requirement_text)
        if (
            match is None
            or "*" in match.group(3)
            or not match.group(3)[0].isalnum()
            or any(character in match.group(3) for character in "/@")
        ):
            raise ReleaseAuditError("requirements_lock_not_exactly_pinned")
        if not hashes:
            raise ReleaseAuditError("requirements_lock_hash_missing")
        records.append(
            {
                "name": _normalize_name(match.group(1)),
                "version": match.group(3),
                "marker": (match.group(4) or "").strip() or None,
                "sha256": hashes,
            }
        )
    if not records:
        raise ReleaseAuditError("requirements_lock_empty")
    return records


def parse_direct_requirements(raw: bytes) -> list[str]:
    names: list[str] = []
    for line in _logical_requirement_lines(raw, label="requirements_input"):
        if line.startswith(("-", ".", "/")) or " @ " in line:
            raise ReleaseAuditError("requirements_input_unpinned_source")
        match = _NAME.match(line)
        if match is None:
            raise ReleaseAuditError("requirements_input_invalid")
        names.append(_normalize_name(match.group(0)))
    if not names:
        raise ReleaseAuditError("requirements_input_empty")
    return sorted(set(names))


def audit_dependency_lock(
    requirements_input: Path,
    requirements_lock: Path,
    *,
    policy_id: str,
    policy_sha256: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    input_raw = _bounded_regular_bytes(
        requirements_input,
        maximum=16 * 1024 * 1024,
        label="requirements_input",
    )
    lock_raw = _bounded_regular_bytes(
        requirements_lock,
        maximum=32 * 1024 * 1024,
        label="requirements_lock",
    )
    records = parse_locked_requirements(lock_raw)
    direct = parse_direct_requirements(input_raw)
    locked_names = {item["name"] for item in records}
    missing = sorted(set(direct) - locked_names)
    if missing:
        raise ReleaseAuditError("direct_dependency_missing_from_lock")
    receipt = {
        "schema_version": DEPENDENCY_LOCK_SCHEMA,
        "status": "PASSED",
        "policy_id": policy_id,
        "policy_sha256": policy_sha256,
        "requirements_input_sha256": sha256_bytes(input_raw),
        "requirements_lock_sha256": sha256_bytes(lock_raw),
        "direct_dependency_count": len(direct),
        "locked_requirement_count": len(records),
        "direct_dependencies_sha256": sha256_bytes(canonical_json(direct)),
        "locked_requirements_sha256": sha256_bytes(canonical_json(records)),
        "unpinned_candidate_dependencies": [],
        "missing_direct_dependencies": [],
    }
    return receipt, records


def _manifest_blob(repo_root: Path, entry: Mapping[str, Any]) -> bytes:
    relative = str(entry.get("path") or "")
    mode = str(entry.get("mode") or "")
    if (
        not relative
        or relative.startswith("/")
        or any(part in {"", ".", "..", ".git"} for part in relative.split("/"))
        or mode not in {"100644", "100755", "120000"}
    ):
        raise ReleaseAuditError("source_manifest_entry_invalid")
    path = repo_root.joinpath(*relative.split("/"))
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReleaseAuditError("source_manifest_file_unavailable") from exc
    if mode == "120000":
        if not stat.S_ISLNK(info.st_mode):
            raise ReleaseAuditError("source_manifest_mode_mismatch")
        try:
            return os.readlink(path).encode("utf-8")
        except (OSError, UnicodeEncodeError) as exc:
            raise ReleaseAuditError("source_manifest_symlink_invalid") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ReleaseAuditError("source_manifest_mode_mismatch")
    return _bounded_regular_bytes(
        path,
        maximum=_MAX_SOURCE_BYTES,
        label="tracked_source",
    )


def audit_tracked_source_secrets(
    repo_root: Path,
    source_manifest: Path,
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
) -> dict[str, Any]:
    manifest = _read_json(
        source_manifest,
        maximum=_MAX_MANIFEST_BYTES,
        label="source_manifest",
    )
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema", "entries"}
        or manifest.get("schema") != SOURCE_MANIFEST_SCHEMA
        or not isinstance(manifest.get("entries"), list)
    ):
        raise ReleaseAuditError("source_manifest_invalid")
    entries = manifest["entries"]
    paths = [str(item.get("path") or "") for item in entries if isinstance(item, dict)]
    if len(paths) != len(entries) or paths != sorted(paths, key=lambda item: item.encode("utf-8")):
        raise ReleaseAuditError("source_manifest_order_invalid")
    if len(set(paths)) != len(paths):
        raise ReleaseAuditError("source_manifest_duplicate_path")
    allowlist = {
        (str(item["path"]), str(item["sha256"])): frozenset(item["pattern_ids"])
        for item in policy["secret_policy"]["allowlist"]
    }
    findings: list[dict[str, Any]] = []
    allowed: list[dict[str, Any]] = []
    generated: list[dict[str, Any]] = []
    scanned_bytes = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "mode", "sha256", "bytes"}:
            raise ReleaseAuditError("source_manifest_entry_invalid")
        blob = _manifest_blob(repo_root, entry)
        digest = sha256_bytes(blob)
        declared_size = entry.get("bytes")
        if (
            digest != entry.get("sha256")
            or type(declared_size) is not int
            or declared_size != len(blob)
        ):
            raise ReleaseAuditError("source_manifest_content_mismatch")
        scanned_bytes += len(blob)
        if scanned_bytes > _MAX_SOURCE_BYTES:
            raise ReleaseAuditError("source_manifest_too_large")
        path = str(entry["path"])
        if path.startswith("backend/app/services/foundry_generated/"):
            generated.append({"path": path, "sha256": digest, "bytes": len(blob)})
        for pattern_id, pattern in _SECRET_PATTERNS:
            for match in pattern.finditer(blob):
                record = {
                    "path": path,
                    "sha256": digest,
                    "pattern_id": pattern_id,
                    "line": blob.count(b"\n", 0, match.start()) + 1,
                }
                if pattern_id in allowlist.get((path, digest), frozenset()):
                    allowed.append(record)
                else:
                    findings.append(record)
    if findings:
        raise ReleaseAuditError("tracked_source_secret_detected")
    source_tree_sha256 = sha256_bytes(canonical_json(manifest))
    return {
        "schema_version": SECRET_SCHEMA,
        "status": "PASSED",
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_sha256,
        "source_manifest_sha256": sha256_bytes(
            _bounded_regular_bytes(
                source_manifest,
                maximum=_MAX_MANIFEST_BYTES,
                label="source_manifest",
            )
        ),
        "source_tree_sha256": source_tree_sha256,
        "scanned_file_count": len(entries),
        "scanned_bytes": scanned_bytes,
        "pattern_ids": [item[0] for item in _SECRET_PATTERNS],
        "generated_candidate_paths": generated,
        "allowed_test_fixture_findings": allowed,
        "unresolved_findings": [],
    }


def _active_locked_requirements(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    try:
        from packaging.markers import Marker, default_environment
    except ImportError as exc:
        raise ReleaseAuditError("packaging_marker_evaluator_unavailable") from exc
    environment = default_environment()
    active: list[dict[str, str]] = []
    for item in records:
        marker = item.get("marker")
        try:
            enabled = not marker or Marker(str(marker)).evaluate(environment)
        except Exception as exc:
            raise ReleaseAuditError("requirements_lock_marker_invalid") from exc
        if enabled:
            active.append({"name": str(item["name"]), "version": str(item["version"])})
    names = [item["name"] for item in active]
    if len(names) != len(set(names)):
        raise ReleaseAuditError("requirements_lock_active_pin_conflict")
    return sorted(active, key=lambda item: item["name"])


def _license_files(distribution: importlib.metadata.Distribution) -> list[dict[str, Any]]:
    declarations = distribution.metadata.get_all("License-File") or []
    files = list(distribution.files or [])
    candidates = []
    for relative in files:
        value = str(relative).replace("\\", "/")
        lower = value.lower()
        if ".dist-info/licenses/" in lower or (
            ".dist-info/" in lower
            and Path(lower).name.startswith(("license", "copying", "notice"))
        ):
            candidates.append(relative)
    results: list[dict[str, Any]] = []
    for relative in sorted(candidates, key=lambda item: str(item).encode("utf-8")):
        path = Path(distribution.locate_file(relative))
        raw = _bounded_regular_bytes(
            path,
            maximum=_MAX_LICENSE_FILE_BYTES,
            label="installed_license_file",
        )
        if not raw:
            raise ReleaseAuditError("installed_license_file_empty")
        results.append(
            {
                "path": str(relative).replace("\\", "/"),
                "sha256": sha256_bytes(raw),
                "bytes": len(raw),
                "declared": any(Path(item).name == Path(str(relative)).name for item in declarations),
                # License families identify themselves near the beginning.
                # Bounding classification text avoids misclassifying GPLv3
                # merely because its later compatibility section names AGPL.
                "_policy_text": raw[:8192]
                .decode("utf-8", errors="replace")
                .lower(),
            }
        )
    return results


def _installed_inventory() -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    seen: set[str] = set()
    for distribution in importlib.metadata.distributions():
        raw_name = str(distribution.metadata.get("Name") or "").strip()
        if not raw_name:
            raise ReleaseAuditError("installed_distribution_name_missing")
        name = _normalize_name(raw_name)
        if name in seen:
            raise ReleaseAuditError("installed_distribution_duplicate")
        seen.add(name)
        license_expression = str(
            distribution.metadata.get("License-Expression") or ""
        ).strip()
        license_field = str(distribution.metadata.get("License") or "").strip()
        if license_expression.upper() in {"UNKNOWN", "N/A", "NONE"}:
            license_expression = ""
        if license_field.upper() in {"UNKNOWN", "N/A", "NONE"}:
            license_field = ""
        classifiers = sorted(
            item
            for item in (distribution.metadata.get_all("Classifier") or [])
            if item.startswith("License ::")
        )
        files = _license_files(distribution)
        policy_text = "\n".join(
            [license_expression.lower(), license_field[:8192].lower()]
            + [item.lower() for item in classifiers]
            + [str(item.pop("_policy_text")) for item in files]
        )
        inventory.append(
            {
                "name": name,
                "version": str(distribution.version),
                "license_expression": license_expression or None,
                "license_field_sha256": sha256_bytes(license_field.encode("utf-8"))
                if license_field
                else None,
                "license_field_preview": " ".join(license_field.split())[:256]
                if license_field
                else None,
                "license_classifiers": classifiers,
                "license_files": files,
                "_policy_text": policy_text,
            }
        )
    return sorted(inventory, key=lambda item: item["name"])


def audit_installed_environment(
    requirements_lock: Path,
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
    platform: str,
    inventory: list[dict[str, Any]] | None = None,
    pip_check: tuple[int, bytes] | None = None,
    runtime_platform: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if platform not in {"linux/amd64", "linux/arm64"}:
        raise ReleaseAuditError("formal_release_platform_invalid")
    if runtime_platform is None:
        machine = runtime_platform_module.machine().lower()
        system = runtime_platform_module.system().lower()
        aliases = {
            ("linux", "x86_64"): "linux/amd64",
            ("linux", "amd64"): "linux/amd64",
            ("linux", "aarch64"): "linux/arm64",
            ("linux", "arm64"): "linux/arm64",
        }
        runtime_platform = aliases.get((system, machine))
    if runtime_platform != platform:
        raise ReleaseAuditError("formal_release_runtime_platform_mismatch")
    lock_raw = _bounded_regular_bytes(
        requirements_lock,
        maximum=32 * 1024 * 1024,
        label="requirements_lock",
    )
    records = parse_locked_requirements(lock_raw)
    active = _active_locked_requirements(records)
    if inventory is None:
        inventory = _installed_inventory()
    installed = {
        _normalize_name(str(item.get("name") or "")): str(item.get("version") or "")
        for item in inventory
    }
    if len(installed) != len(inventory) or not all(installed.values()):
        raise ReleaseAuditError("installed_distribution_inventory_invalid")
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError as exc:
        raise ReleaseAuditError("packaging_version_evaluator_unavailable") from exc
    mismatches: list[dict[str, str | None]] = []
    active_by_name = {item["name"]: item["version"] for item in active}
    for name, expected in active_by_name.items():
        actual = installed.get(name)
        try:
            equal = actual is not None and Version(actual) == Version(expected)
        except InvalidVersion:
            equal = False
        if not equal:
            mismatches.append({"name": name, "expected": expected, "actual": actual})
    runtime_tools = {
        _normalize_name(item)
        for item in policy["dependency_policy"]["allow_unlocked_runtime_tools"]
    }
    unexpected = sorted(set(installed) - set(active_by_name) - runtime_tools)
    if pip_check is None:
        completed = subprocess.run(
            [sys.executable, "-m", "pip", "check"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=120,
        )
        pip_check = (completed.returncode, completed.stdout)
    pip_exit, pip_output = pip_check
    if mismatches or unexpected or pip_exit != 0:
        raise ReleaseAuditError("installed_dependency_integrity_failed")
    installed_pairs = [
        {"name": name, "version": installed[name]} for name in sorted(installed)
    ]
    dependency_receipt = {
        "schema_version": DEPENDENCY_ENV_SCHEMA,
        "status": "PASSED",
        "platform": platform,
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_sha256,
        "requirements_lock_sha256": sha256_bytes(lock_raw),
        "active_lock_pins": active,
        "installed_distributions": installed_pairs,
        "runtime_tool_allowlist": sorted(runtime_tools),
        "missing_or_mismatched": [],
        "unexpected_installed_distributions": [],
        "pip_check": {
            "command": "python -m pip check",
            "exit_code": 0,
            "output_sha256": sha256_bytes(pip_output),
            "passed": True,
        },
    }

    missing_license: list[str] = []
    forbidden: list[dict[str, Any]] = []
    public_inventory: list[dict[str, Any]] = []
    for item in inventory:
        entry = dict(item)
        policy_text = str(entry.pop("_policy_text", ""))
        evidence = bool(
            entry.get("license_expression")
            or entry.get("license_field_sha256")
            or entry.get("license_classifiers")
            or entry.get("license_files")
        )
        if not evidence:
            missing_license.append(str(entry.get("name")))
        triggered = []
        for marker in policy["license_policy"]["forbidden_markers"]:
            if any(fragment.lower() in policy_text for fragment in marker["fragments"]):
                triggered.append(str(marker["id"]))
        if triggered:
            forbidden.append(
                {
                    "name": str(entry.get("name")),
                    "version": str(entry.get("version")),
                    "policy_markers": sorted(triggered),
                }
            )
        entry["policy_decision"] = "ALLOWED" if evidence and not triggered else "DENIED"
        public_inventory.append(entry)
    if missing_license or forbidden:
        raise ReleaseAuditError("installed_license_policy_failed")
    license_receipt = {
        "schema_version": LICENSE_SCHEMA,
        "status": "PASSED",
        "platform": platform,
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_sha256,
        "requirements_lock_sha256": sha256_bytes(lock_raw),
        "inventory": public_inventory,
        "missing_license_evidence": [],
        "forbidden_license_evidence": [],
        "policy_scope": "declared_metadata_and_installed_license_files",
        "legal_review_complete": False,
    }
    return dependency_receipt, license_receipt


def _receipt(path: Path, *, schema: str, platform: str | None = None) -> tuple[dict[str, Any], str]:
    value = _read_json(path, maximum=64 * 1024 * 1024, label="release_audit_receipt")
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != schema
        or value.get("status") != "PASSED"
        or (platform is not None and value.get("platform") != platform)
    ):
        raise ReleaseAuditError("release_audit_receipt_invalid")
    raw = _bounded_regular_bytes(
        path,
        maximum=64 * 1024 * 1024,
        label="release_audit_receipt",
    )
    return value, sha256_bytes(raw)


def aggregate_receipts(
    *,
    static_dir: Path,
    amd64_dir: Path,
    arm64_dir: Path,
    sbom_path: Path,
) -> dict[str, Any]:
    static, static_hash = _receipt(
        static_dir / "static-audit-receipt.json", schema=STATIC_SCHEMA
    )
    secret, secret_hash = _receipt(
        static_dir / "secret-scan-receipt.json", schema=SECRET_SCHEMA
    )
    lock, lock_hash = _receipt(
        static_dir / "dependency-lock-receipt.json", schema=DEPENDENCY_LOCK_SCHEMA
    )
    receipts: dict[str, str] = {
        "static_audit": static_hash,
        "secret_scan": secret_hash,
        "dependency_lock": lock_hash,
    }
    environments = []
    for platform, directory, prefix in (
        ("linux/amd64", amd64_dir, "linux_amd64"),
        ("linux/arm64", arm64_dir, "linux_arm64"),
    ):
        environment, environment_hash = _receipt(
            directory / "environment-audit-receipt.json",
            schema=ENVIRONMENT_SCHEMA,
            platform=platform,
        )
        dependency, dependency_hash = _receipt(
            directory / "dependency-integrity-receipt.json",
            schema=DEPENDENCY_ENV_SCHEMA,
            platform=platform,
        )
        license_receipt, license_hash = _receipt(
            directory / "license-policy-receipt.json",
            schema=LICENSE_SCHEMA,
            platform=platform,
        )
        if (
            environment.get("dependency_integrity_receipt_sha256") != dependency_hash
            or environment.get("license_policy_receipt_sha256") != license_hash
            or dependency.get("requirements_lock_sha256")
            != lock.get("requirements_lock_sha256")
            or license_receipt.get("requirements_lock_sha256")
            != lock.get("requirements_lock_sha256")
            or environment.get("policy_sha256") != static.get("policy_sha256")
            or dependency.get("policy_sha256") != static.get("policy_sha256")
            or license_receipt.get("policy_sha256") != static.get("policy_sha256")
        ):
            raise ReleaseAuditError("release_audit_cross_receipt_mismatch")
        receipts[f"{prefix}_environment"] = environment_hash
        receipts[f"{prefix}_dependency_integrity"] = dependency_hash
        receipts[f"{prefix}_license_policy"] = license_hash
        environments.append(environment)
    sbom_raw = _bounded_regular_bytes(
        sbom_path,
        maximum=128 * 1024 * 1024,
        label="formal_sbom",
    )
    try:
        sbom_value = json.loads(sbom_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseAuditError("formal_sbom_invalid_json") from exc
    if sbom_value in (None, {}, []):
        raise ReleaseAuditError("formal_sbom_empty")
    if (
        static.get("dependency_lock_receipt_sha256") != lock_hash
        or static.get("secret_scan_receipt_sha256") != secret_hash
        or static.get("source_tree_sha256") != secret.get("source_tree_sha256")
        or static.get("requirements_lock_sha256")
        != lock.get("requirements_lock_sha256")
    ):
        raise ReleaseAuditError("release_audit_static_receipt_mismatch")
    return {
        "schema_version": AGGREGATE_SCHEMA,
        "status": "PASSED",
        "policy_id": static["policy_id"],
        "policy_sha256": static["policy_sha256"],
        "source_tree_sha256": static["source_tree_sha256"],
        "dependency_lock_sha256": static["requirements_lock_sha256"],
        "formal_sbom_sha256": sha256_bytes(sbom_raw),
        "architectures": ["linux/amd64", "linux/arm64"],
        "receipts": dict(sorted(receipts.items())),
        "gates": {
            "dependency_integrity": True,
            "license_inventory_policy": True,
            "tracked_source_secret_scan": True,
        },
        "advisory_database_checked": False,
        "vulnerability_status": "NOT_EVALUATED",
        "legal_review_complete": False,
    }


def run_static(args: argparse.Namespace) -> None:
    policy, policy_hash = load_policy(Path(args.policy))
    lock_receipt, _ = audit_dependency_lock(
        Path(args.requirements_input),
        Path(args.requirements_lock),
        policy_id=policy["policy_id"],
        policy_sha256=policy_hash,
    )
    secret_receipt = audit_tracked_source_secrets(
        Path(args.repo_root),
        Path(args.source_manifest),
        policy=policy,
        policy_sha256=policy_hash,
    )
    output = Path(args.output_dir)
    lock_hash = _write_json(output / "dependency-lock-receipt.json", lock_receipt)
    secret_hash = _write_json(output / "secret-scan-receipt.json", secret_receipt)
    static_receipt = {
        "schema_version": STATIC_SCHEMA,
        "status": "PASSED",
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_hash,
        "source_tree_sha256": secret_receipt["source_tree_sha256"],
        "requirements_lock_sha256": lock_receipt["requirements_lock_sha256"],
        "dependency_lock_receipt_sha256": lock_hash,
        "secret_scan_receipt_sha256": secret_hash,
    }
    _write_json(output / "static-audit-receipt.json", static_receipt)


def run_environment(args: argparse.Namespace) -> None:
    policy, policy_hash = load_policy(Path(args.policy))
    dependency, license_receipt = audit_installed_environment(
        Path(args.requirements_lock),
        policy=policy,
        policy_sha256=policy_hash,
        platform=args.platform,
    )
    output = Path(args.output_dir)
    dependency_hash = _write_json(
        output / "dependency-integrity-receipt.json", dependency
    )
    license_hash = _write_json(output / "license-policy-receipt.json", license_receipt)
    environment = {
        "schema_version": ENVIRONMENT_SCHEMA,
        "status": "PASSED",
        "platform": args.platform,
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_hash,
        "requirements_lock_sha256": dependency["requirements_lock_sha256"],
        "dependency_integrity_receipt_sha256": dependency_hash,
        "license_policy_receipt_sha256": license_hash,
        "advisory_database_checked": False,
        "vulnerability_status": "NOT_EVALUATED",
    }
    _write_json(output / "environment-audit-receipt.json", environment)


def run_aggregate(args: argparse.Namespace) -> None:
    value = aggregate_receipts(
        static_dir=Path(args.static_dir),
        amd64_dir=Path(args.amd64_dir),
        arm64_dir=Path(args.arm64_dir),
        sbom_path=Path(args.sbom),
    )
    _write_json(Path(args.output), value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    static_parser = subparsers.add_parser("static")
    static_parser.add_argument("--repo-root", required=True)
    static_parser.add_argument("--source-manifest", required=True)
    static_parser.add_argument("--requirements-input", required=True)
    static_parser.add_argument("--requirements-lock", required=True)
    static_parser.add_argument("--policy", required=True)
    static_parser.add_argument("--output-dir", required=True)
    static_parser.set_defaults(handler=run_static)

    environment_parser = subparsers.add_parser("environment")
    environment_parser.add_argument("--requirements-lock", required=True)
    environment_parser.add_argument("--policy", required=True)
    environment_parser.add_argument(
        "--platform", required=True, choices=("linux/amd64", "linux/arm64")
    )
    environment_parser.add_argument("--output-dir", required=True)
    environment_parser.set_defaults(handler=run_environment)

    aggregate_parser = subparsers.add_parser("aggregate")
    aggregate_parser.add_argument("--static-dir", required=True)
    aggregate_parser.add_argument("--amd64-dir", required=True)
    aggregate_parser.add_argument("--arm64-dir", required=True)
    aggregate_parser.add_argument("--sbom", required=True)
    aggregate_parser.add_argument("--output", required=True)
    aggregate_parser.set_defaults(handler=run_aggregate)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        args.handler(args)
    except ReleaseAuditError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

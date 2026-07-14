#!/usr/bin/env python3
"""Fail-closed guards for the scheduled scientific-validation workflow.

Two failure modes are intentionally handled outside pytest:

* a scientific dependency can disappear and turn a critical test into a skip;
* a downloaded Cobaya data product can be present under the expected name but
  no longer match the release bytes used to establish likelihood parity.

The ``preflight`` command imports the scientific runtime and, when requested,
verifies every file in the pinned BAO manifest.  The ``assert-junit`` command
rejects a pytest report containing skipped tests (including xfails), too few
collected tests, failures, or errors.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import pathlib
import re
import shutil
import sys
import xml.etree.ElementTree as ET


_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent

_REQUIRED_DISTRIBUTIONS = {
    "numpy": "numpy",
    "scipy": "scipy",
    "astropy": "astropy",
    "emcee": "emcee",
    "cobaya": "cobaya",
    "camb": "camb",
}

_REQUIRED_BAO_PATHS = {
    "packages/data/bao_data/sdss_DR16_ELG_BAO_DVtable.txt",
    "packages/data/bao_data/sdss_DR16_LYAUTO_BAO_DMDHgrid.txt",
    "packages/data/bao_data/sdss_DR16_LYxQSO_BAO_DMDHgrid.txt",
    "packages/data/bao_data/sdss_DR12Consensus_bao.dat",
}


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(path: pathlib.Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]) is None:
            raise ValueError(f"invalid sha256 manifest row {path}:{line_number}")
        relative = parts[1].lstrip("*").strip()
        candidate = pathlib.PurePosixPath(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"unsafe manifest path {relative!r} at {path}:{line_number}")
        if relative in entries:
            raise ValueError(f"duplicate manifest path {relative!r} at {path}:{line_number}")
        entries[relative] = parts[0].lower()
    return entries


def _preflight(manifest: pathlib.Path | None) -> int:
    failures: list[str] = []
    versions: dict[str, str] = {}
    for module_name, distribution_name in _REQUIRED_DISTRIBUTIONS.items():
        try:
            importlib.import_module(module_name)
            versions[module_name] = importlib.metadata.version(distribution_name)
        except Exception as exc:  # noqa: BLE001 - report every missing/broken dependency
            failures.append(f"{module_name}: {type(exc).__name__}: {exc}")

    if shutil.which("cobaya-install") is None:
        failures.append("cobaya-install executable is unavailable")

    print("Scientific runtime:")
    for name, version in sorted(versions.items()):
        print(f"  {name}=={version}")

    if manifest is not None:
        if not manifest.is_file():
            failures.append(f"BAO manifest not found: {manifest}")
        else:
            try:
                entries = _read_manifest(manifest)
                listed_paths = set(entries)
                if listed_paths != _REQUIRED_BAO_PATHS:
                    missing = sorted(_REQUIRED_BAO_PATHS - listed_paths)
                    unexpected = sorted(listed_paths - _REQUIRED_BAO_PATHS)
                    failures.append(
                        "BAO manifest inventory mismatch: "
                        f"missing={missing or 'none'}, unexpected={unexpected or 'none'}"
                    )
                for relative, expected in entries.items():
                    data_path = _BACKEND_ROOT / relative
                    if not data_path.is_file():
                        failures.append(f"BAO data file not found: {data_path}")
                        continue
                    actual = _sha256(data_path)
                    if actual != expected:
                        failures.append(
                            f"BAO sha256 mismatch for {relative}: expected {expected}, got {actual}"
                        )
                if not failures:
                    print(f"Verified {len(entries)} pinned BAO data files via {manifest}")
            except (OSError, ValueError) as exc:
                failures.append(str(exc))

    if failures:
        print("Scientific validation preflight failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


def _assert_junit(path: pathlib.Path, minimum_tests: int) -> int:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        print(f"Cannot read pytest JUnit report {path}: {exc}", file=sys.stderr)
        return 1

    cases = root.findall(".//testcase")
    skipped = [case for case in cases if case.find("skipped") is not None]
    failed = [case for case in cases if case.find("failure") is not None]
    errored = [case for case in cases if case.find("error") is not None]

    problems: list[str] = []
    if len(cases) < minimum_tests:
        problems.append(f"collected {len(cases)} tests, expected at least {minimum_tests}")
    if skipped:
        names = ", ".join(case.get("name", "<unnamed>") for case in skipped[:20])
        problems.append(f"{len(skipped)} test(s) skipped or xfailed: {names}")
    if failed:
        problems.append(f"{len(failed)} test(s) failed")
    if errored:
        problems.append(f"{len(errored)} test(s) errored")

    if problems:
        print("Pytest scientific-suite guard failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"JUnit guard passed: {len(cases)} tests, zero skips, failures, or errors")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="verify scientific dependencies and data")
    preflight.add_argument(
        "--bao-manifest",
        type=pathlib.Path,
        help="sha256 manifest whose paths are resolved from the backend directory",
    )

    junit = subparsers.add_parser("assert-junit", help="reject skipped or incomplete pytest runs")
    junit.add_argument("report", type=pathlib.Path)
    junit.add_argument("--minimum-tests", type=int, default=1)

    args = parser.parse_args()
    if args.command == "preflight":
        manifest = args.bao_manifest.resolve() if args.bao_manifest else None
        return _preflight(manifest)
    if args.minimum_tests < 1:
        parser.error("--minimum-tests must be positive")
    return _assert_junit(args.report, args.minimum_tests)


if __name__ == "__main__":
    raise SystemExit(main())

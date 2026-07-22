#!/usr/bin/env python3
"""Build the pinned ASCII-target subset of Unicode UTS #39 confusables.

Download the exact upstream file separately, then run:

    python backend/scripts/generate_uts39_ascii_confusables.py \
      /path/to/confusables-17.0.0.txt --check

The generator deliberately has no network access. It accepts only the pinned
upstream SHA-256 and either checks or writes the tracked Python module.
"""

from __future__ import annotations

import argparse
import hashlib
from collections import defaultdict
from pathlib import Path


SOURCE_VERSION = "17.0.0"
SOURCE_DATE = "2025-07-22"
SOURCE_SHA256 = "091c7f82fc39ef208faf8f94d29c244de99254675e09de163160c810d13ef22a"
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "uts39_ascii_confusables.py"
)


def _selected_groups(source: bytes) -> dict[str, list[str]]:
    """Apply the complete, closed filter used by the runtime subset."""

    groups: dict[str, list[str]] = defaultdict(list)
    seen_sources: set[int] = set()
    for raw_line in source.decode("utf-8").splitlines():
        content = raw_line.split("#", 1)[0].strip()
        if not content:
            continue
        fields = [field.strip() for field in content.split(";")]
        if len(fields) < 2:
            raise ValueError(f"malformed confusables record: {raw_line!r}")
        source_codepoints = fields[0].split()
        target_codepoints = fields[1].split()
        if len(source_codepoints) != 1:
            continue
        source_codepoint = int(source_codepoints[0], 16)
        target = "".join(chr(int(codepoint, 16)) for codepoint in target_codepoints)

        # Keep every non-ASCII source whose official skeleton is a short,
        # printable ASCII token. This recipe intentionally depends only on
        # the pinned UTS file—not the host Python Unicode database—so a newer
        # Unicode release cannot silently change the generated artifact.
        if (
            source_codepoint < 0x80
            or not target
            or len(target) > 4
            or any(not 0x21 <= ord(character) <= 0x7E for character in target)
        ):
            continue
        if source_codepoint in seen_sources:
            raise ValueError(f"duplicate selected source U+{source_codepoint:04X}")
        seen_sources.add(source_codepoint)
        groups[target].append(f"{source_codepoint:X}")
    return groups


def _render(source: bytes) -> str:
    digest = hashlib.sha256(source).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(
            "unexpected confusables.txt SHA-256: "
            f"expected {SOURCE_SHA256}, got {digest}"
        )
    groups = _selected_groups(source)
    mapping_count = sum(len(codepoints) for codepoints in groups.values())
    if mapping_count != 1844:
        raise ValueError(f"unexpected selected mapping count: {mapping_count}")

    lines = [
        '"""Compact ASCII-target subset of Unicode UTS #39 confusables.',
        "",
        f"Generated from Unicode Security Mechanisms confusables.txt v{SOURCE_VERSION}, dated",
        f"{SOURCE_DATE}, SHA-256 {SOURCE_SHA256}.",
        "All non-ASCII sources with printable ASCII targets of at most four characters",
        "are retained using a host-Unicode-version-independent filter.",
        "Copyright © 1991-2026 Unicode, Inc. Distributed under the Unicode License v3;",
        "see docs/third_party/UNICODE_LICENSE_V3.txt. Rebuild this module with",
        "backend/scripts/generate_uts39_ascii_confusables.py.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "_UTS39_ASCII_GROUPS: tuple[tuple[str, str], ...] = (",
    ]
    lines.extend(
        f"    ({target!r}, {' '.join(codepoints)!r}),"
        for target, codepoints in sorted(groups.items())
    )
    lines.extend(
        [
            ")",
            "",
            "UTS39_ASCII_TRANSLATION = {",
            "    int(codepoint, 16): target",
            "    for target, codepoints in _UTS39_ASCII_GROUPS",
            "    for codepoint in codepoints.split()",
            "}",
            "",
            '__all__ = ["UTS39_ASCII_TRANSLATION"]',
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="pinned Unicode confusables.txt")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail unless the tracked output exactly matches the generated module",
    )
    args = parser.parse_args()
    rendered = _render(args.source.read_bytes())
    if args.check:
        if args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"generated output differs from {args.output}")
        return 0
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

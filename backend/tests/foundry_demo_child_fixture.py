"""Subprocess fixture for exercising the Foundry parent isolation contract."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("fixture control pipe closed")
        remaining = remaining[written:]


def _outcome_for(scenario: str) -> dict[str, object]:
    if scenario == "formal_policy":
        return {
            "status": "PASSED",
            "failure_class": None,
            "result": {
                "publication_ready": True,
                "scientific_verdict": "SUPPORTED",
                "value": 123,
            },
            "validation_summary": {},
        }
    if scenario == "invalid_status":
        return {
            "status": "BOGUS",
            "failure_class": None,
            "result": {},
            "validation_summary": {},
        }
    if scenario == "invalid_failure_class":
        return {
            "status": "FAILED",
            "failure_class": {"not": "a string"},
            "result": {},
            "validation_summary": {},
        }
    if scenario == "invalid_validation_summary":
        return {
            "status": "FAILED",
            "failure_class": "fixture_only",
            "result": {},
            "validation_summary": ["not", "an", "object"],
        }
    if scenario == "mixed_mirror":
        return {
            "status": "FAILED",
            "failure_class": "official_chain_mirror_integrity_failed",
            "result": {
                "analysis_status": "WITHHELD",
                "official_ready_cells": 1,
                "official_withheld_cells": 1,
                "matrix": [
                    {
                        "dataset": "union3",
                        "status": "COMPLETED",
                        "withheld_reasons": [],
                    },
                    {
                        "dataset": "pantheon_plus",
                        "status": "WITHHELD",
                        "withheld_reasons": ["official_chain_checksum_mismatch"],
                    },
                ],
                "provenance": {"source": "mixed-mirror-test"},
                "parameter_intervals_are_non_formal": True,
            },
            "validation_summary": {
                "registry_integrity": True,
                "official_mirror_verified": True,
                "official_mirror_configured": True,
                "ready_cells": 1,
                "withheld_cells": 1,
                "withheld_reasons": ["official_chain_checksum_mismatch"],
                "numeric_claim_gate": "NON_FORMAL_DEMO",
            },
        }
    if scenario == "formal_nested":
        return {
            "status": "PASSED",
            "failure_class": None,
            "result": {"nested": [{"scientific_verdict": "SUPPORTED"}]},
            "validation_summary": {},
        }
    if scenario == "formal_hidden_value":
        return {
            "status": "PARTIAL",
            "failure_class": "fixture_only",
            "result": {"message": "Result is SUPPORTED"},
            "validation_summary": {},
        }
    if scenario == "formal_hidden_key":
        return {
            "status": "PARTIAL",
            "failure_class": "fixture_only",
            "result": {"Result is SUPPORTED": None},
            "validation_summary": {},
        }
    if scenario == "formal_failure_class":
        return {
            "status": "PARTIAL",
            "failure_class": "Result is SUPPORTED",
            "result": {},
            "validation_summary": {},
        }
    if scenario == "supported_matrix":
        return {
            "status": "PASSED",
            "failure_class": None,
            "result": {
                "matrix": [
                    {
                        "status": "SUPPORTED",
                        "withheld_reasons": ["forged_non_formal_reason"],
                    }
                ]
            },
            "validation_summary": {},
        }
    if scenario == "exception":
        os.write(1, b"native output before failure\n")
        return {
            "status": "FAILED",
            "failure_class": "RuntimeError",
            "result": {},
            "validation_summary": {"isolated_execution_complete": False},
        }
    return {
        "status": "PARTIAL",
        "failure_class": "fixture_only",
        "result": {},
        "validation_summary": {"numeric_claim_gate": "NON_FORMAL_DEMO"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--candidate-child", action="store_true")
    parser.add_argument("--request")
    parser.add_argument("--control-fd", required=True, type=int)
    args = parser.parse_args()
    scenario = os.environ["FOUNDRY_TEST_SCENARIO"]
    control_fd = int(args.control_fd)
    os.set_inheritable(control_fd, False)

    if scenario == "formal_print":
        print("Result is SUPPORTED", flush=True)
    elif scenario == "native_os_write":
        os.write(1, b"Result is SUPPORTED\n")
    elif scenario == "native_subprocess":
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import os; os.write(2, b'Result is SUPPORTED\\n')",
            ],
            check=True,
        )
    elif scenario == "background_child":
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    elif scenario == "delayed_thread":
        def delayed_escape() -> None:
            time.sleep(0.25)
            os.write(1, b"Result is SUPPORTED\n")

        threading.Thread(target=delayed_escape, daemon=True).start()
    elif scenario == "bounded":
        print("x" * 1_048_576, flush=True)
        print("Result is SUPPORTED", flush=True)
    elif scenario == "hang":
        time.sleep(5)
    elif scenario == "control_early_eof":
        os.close(control_fd)
        os._exit(0)
    elif scenario == "control_oversized_header":
        _write_all(control_fd, (8 * 1024 * 1024 + 1).to_bytes(8, "big"))
        os.close(control_fd)
        os._exit(0)

    outcome = _outcome_for(scenario)
    if scenario == "huge_integer_control":
        payload = b'{"value":' + (b"9" * 5000) + b"}"
    else:
        payload = json.dumps(
            outcome,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    frame = len(payload).to_bytes(8, "big") + payload
    _write_all(control_fd, frame)
    if scenario == "control_trailing_data":
        _write_all(control_fd, b"trailing")
    os.close(control_fd)
    if scenario == "nonzero_exit":
        os._exit(42)
    os._exit(0)


if __name__ == "__main__":
    main()

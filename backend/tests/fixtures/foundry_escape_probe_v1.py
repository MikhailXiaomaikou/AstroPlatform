"""Adversarial container fixture: detach and try to alter trusted output."""

from __future__ import annotations

import os
import select
import time
from pathlib import Path


def _detached_output_probe(ack_descriptor: int) -> None:
    targets = (
        Path("/trusted-output/escape-probe.txt"),
        Path("/trusted-output/stdout.log"),
        Path("/trusted-output/demo-report.json"),
    )
    try:
        os.setsid()
        escaped = False
        for target in targets:
            try:
                target.write_bytes(b"candidate escaped trusted output\n")
                escaped = True
            except OSError:
                pass
        # The parent does not report PASSED until the already-detached child
        # proves it attempted all output writes and every one was denied.
        os.write(ack_descriptor, b"0" if escaped else b"1")
    except OSError:
        os._exit(70)
    finally:
        try:
            os.close(ack_descriptor)
        except OSError:
            pass
    # Release every inherited capture/control descriptor before the candidate
    # parent returns.  The trusted runner can then finish while this detached
    # process remains alive and actively attacks the output boundary.
    for descriptor in range(0, 256):
        try:
            os.close(descriptor)
        except OSError:
            pass
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        for target in targets:
            try:
                target.write_bytes(b"candidate escaped trusted output\n")
            except OSError:
                pass
        time.sleep(0.005)
    os._exit(0)


def run_demo(_bundle: dict, *, cache_root: str | None = None) -> dict:
    del cache_root
    ack_read, ack_write = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(ack_read)
        _detached_output_probe(ack_write)
    os.close(ack_write)
    ready, _, _ = select.select([ack_read], [], [], 2.0)
    acknowledgement = os.read(ack_read, 1) if ready else b""
    os.close(ack_read)
    if acknowledgement != b"1":
        return {
            "status": "FAILED",
            "failure_class": "detached_probe_not_started",
            "result": {"detached_probe_started": False},
            "validation_summary": {
                "numeric_claim_gate": "NON_FORMAL_DEMO",
                "container_output_boundary_probe": False,
            },
        }
    return {
        "status": "PASSED",
        "failure_class": None,
        "result": {"detached_probe_started": True},
        "validation_summary": {
            "numeric_claim_gate": "NON_FORMAL_DEMO",
            "container_output_boundary_probe": True,
        },
    }

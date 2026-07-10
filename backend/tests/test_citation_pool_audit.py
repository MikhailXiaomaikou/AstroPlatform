"""The citation reachability audit must inspect the emitted tool surface."""

from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace


_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "audit_citation_pool.py"
_SPEC = importlib.util.spec_from_file_location("citation_pool_audit", _SCRIPT)
assert _SPEC and _SPEC.loader
audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit)


class _Entry:
    def __init__(self, payload):
        self.citations = (
            SimpleNamespace(
                bibcode="2099XYZ...999X",
                arxiv="2099.99999",
                doi="10.9999/fake",
            ),
        )
        self._payload = payload

    def to_dict(self):
        return self._payload


def test_declared_identifiers_are_not_injected_into_reachable_pool():
    entry = _Entry(payload={})

    declared = audit._identifiers_of(entry)
    reachable = audit._identifiers_reachable_via_tool_result(entry)

    assert declared["bibcode"] == {"2099XYZ...999X"}
    assert all(not values for values in reachable.values())


def test_all_identifier_types_are_reachable_when_tool_payload_exposes_them():
    entry = _Entry(
        payload={
            "provenance": {
                "citations": [
                    {
                        "bibcode": "2099XYZ...999X",
                        "arxiv": "https://arxiv.org/abs/2099.99999",
                        "doi": "https://doi.org/10.9999/FAKE",
                    }
                ]
            }
        }
    )

    declared = audit._identifiers_of(entry)
    reachable = audit._identifiers_reachable_via_tool_result(entry)

    assert declared == reachable

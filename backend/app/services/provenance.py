"""Data provenance and reproducibility service.

Records activity provenance for pipeline executions, provides
lineage traversal, IVOA ProvDM-compatible export, and DOI metadata generation.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# In-memory provenance store (for environments without DB migration)
_provenance_records: list[dict] = []


def record_activity(
    entity_type: str,
    entity_id: str,
    activity: str,
    params: dict | None = None,
    parent_ids: list[str] | None = None,
    agent: str = "system",
    environment: dict | None = None,
) -> str:
    """Record a provenance activity."""
    record_id = str(uuid.uuid4())
    record = {
        "id": record_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "activity": activity,
        "params": params or {},
        "parent_ids": parent_ids or [],
        "agent": agent,
        "environment": environment or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _provenance_records.append(record)
    logger.debug("Provenance recorded: %s [%s] %s", entity_id, activity, record_id)
    return record_id


def get_lineage(entity_id: str) -> dict:
    """Build a complete lineage graph for an entity."""
    nodes = []
    edges = []
    visited = set()

    def _traverse(eid: str):
        if eid in visited:
            return
        visited.add(eid)

        records = [r for r in _provenance_records if r["entity_id"] == eid]
        for r in records:
            nodes.append({
                "id": r["entity_id"],
                "type": "activity",
                "label": f"{r['activity']} ({r['entity_type']})",
                "params": r["params"],
                "timestamp": r["timestamp"],
                "agent": r["agent"],
            })
            for parent_id in r.get("parent_ids", []):
                edges.append({"from": parent_id, "to": eid})
                _traverse(parent_id)

    _traverse(entity_id)

    # Deduplicate nodes
    seen_ids = set()
    unique_nodes = []
    for n in nodes:
        if n["id"] not in seen_ids:
            seen_ids.add(n["id"])
            unique_nodes.append(n)

    return {"nodes": unique_nodes, "edges": edges, "entity_id": entity_id}


def export_provenance_ivoa(entity_id: str) -> str:
    """Export provenance as IVOA ProvDM-compatible XML (W3C PROV serialization)."""
    lineage = get_lineage(entity_id)

    xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml_parts.append('<prov:document xmlns:prov="http://www.w3.org/ns/prov#"')
    xml_parts.append('              xmlns:voprov="http://www.ivoa.net/xml/ProvenanceDM/v1.0">')

    for node in lineage["nodes"]:
        xml_parts.append(f'  <prov:activity prov:id="{node["id"]}">')
        xml_parts.append(f'    <prov:type>{node.get("type", "activity")}</prov:type>')
        xml_parts.append(f'    <prov:label>{_xml_escape(node["label"])}</prov:label>')
        if node.get("timestamp"):
            xml_parts.append(f'    <prov:startTime>{node["timestamp"]}</prov:startTime>')
        if node.get("agent"):
            xml_parts.append(f'    <prov:wasAssociatedWith prov:agent="{node["agent"]}"/>')
        xml_parts.append('  </prov:activity>')

    for edge in lineage["edges"]:
        xml_parts.append(f'  <prov:wasDerivedFrom>')
        xml_parts.append(f'    <prov:generatedEntity prov:ref="{edge["to"]}"/>')
        xml_parts.append(f'    <prov:usedEntity prov:ref="{edge["from"]}"/>')
        xml_parts.append(f'  </prov:wasDerivedFrom>')

    xml_parts.append('</prov:document>')
    return '\n'.join(xml_parts)


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def generate_doi_metadata(entity_id: str, title: str = "",
                          authors: list[str] | None = None) -> dict:
    """Generate DataCite-compatible metadata for DOI minting."""
    lineage = get_lineage(entity_id)

    now = datetime.now(timezone.utc)

    return {
        "data": {
            "type": "dois",
            "attributes": {
                "doi": f"10.5281/standard-astro.{entity_id[:8]}",
                "titles": [{"title": title or f"Standard Astro Analysis {entity_id[:8]}"}],
                "creators": [{"name": a} for a in (authors or ["Standard Astro Platform"])],
                "publisher": "Standard Astro",
                "publicationYear": now.year,
                "resourceType": {"resourceTypeGeneral": "Dataset", "resourceType": "Analysis Result"},
                "dates": [{"date": now.isoformat(), "dateType": "Created"}],
                "descriptions": [{
                    "description": f"Automated astronomical analysis with {len(lineage['nodes'])} processing steps.",
                    "descriptionType": "Abstract",
                }],
                "relatedIdentifiers": [],
                "subjects": [{"subject": "Astronomy"}, {"subject": "Data Analysis"}],
                "version": "1.0",
            },
        },
        "provenance_summary": {
            "n_steps": len(lineage["nodes"]),
            "steps": [n["label"] for n in lineage["nodes"]],
        },
        "note": "This is metadata only. Actual DOI minting requires registration with DataCite or Zenodo.",
    }


def capture_environment() -> dict:
    """Capture current Python environment for reproducibility."""
    import sys
    import platform

    packages = {}
    try:
        import importlib.metadata
        for dist in importlib.metadata.distributions():
            packages[dist.metadata["Name"]] = dist.version
    except Exception:
        try:
            import pkg_resources
            packages = {pkg.key: pkg.version for pkg in pkg_resources.working_set}
        except Exception:
            pass

    return {
        "python_version": sys.version,
        "platform": platform.platform(),
        "packages": packages,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def export_requirements_pinned(entity_id: str | None = None) -> str:
    """Export current environment as pinned requirements.txt format."""
    env = capture_environment()
    lines = [f"# Standard Astro environment snapshot"]
    lines.append(f"# Python {env['python_version'].split()[0]}")
    lines.append(f"# Generated: {env['timestamp']}")
    for pkg, ver in sorted(env["packages"].items()):
        lines.append(f"{pkg}=={ver}")
    return "\n".join(lines)


def get_reproducibility_package(entity_id: str) -> dict:
    """Generate a reproducibility package for a pipeline run."""
    lineage = get_lineage(entity_id)

    records = [r for r in _provenance_records if r["entity_id"] == entity_id]

    return {
        "entity_id": entity_id,
        "lineage": lineage,
        "parameters": {r["activity"]: r["params"] for r in records},
        "environment": records[0]["environment"] if records else {},
        "captured_environment": capture_environment(),
        "reproduction_instructions": [
            "1. Install Standard Astro platform",
            "2. Import the pipeline DAG from this package",
            "3. Provide the same input data (paths listed in lineage)",
            "4. Execute the pipeline with identical parameters",
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

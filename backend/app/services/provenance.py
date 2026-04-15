"""Data provenance and reproducibility service.

Records activity provenance for pipeline executions, provides
lineage traversal, IVOA ProvDM-compatible export, and DOI metadata generation.
"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# In-memory provenance store (for environments without DB migration)
_provenance_records: list[dict] = []

# Cached environment manifest — computed once on first record_activity call
_env_manifest: dict | None = None


def _compute_environment_manifest() -> dict:
    """Capture a fingerprint of the execution environment.

    Captures:
    - Python version + platform
    - SHA-256 of key package versions (numpy, astropy, scipy, pandas,
      matplotlib, sklearn) so an environment change is detectable even
      when individual versions are not remembered
    - SHA-256 of the active SYSTEM_PROMPT text if available (tracks
      prompt iteration)

    Best-effort: missing modules or unreadable files produce "unknown"
    markers, never a crash.
    """
    import platform as _platform
    import sys as _sys

    versions: dict[str, str] = {
        "python": _sys.version.split()[0],
        "platform": _platform.platform(),
    }
    for pkg in ("numpy", "astropy", "scipy", "pandas", "matplotlib", "sklearn"):
        try:
            mod = __import__(pkg)
            versions[pkg] = getattr(mod, "__version__", "unknown")
        except Exception:
            versions[pkg] = "missing"

    fingerprint_payload = "|".join(f"{k}={v}" for k, v in sorted(versions.items()))
    fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()[:16]

    prompt_hash = "unknown"
    try:
        from app.api import chat as _chat
        prompt = getattr(_chat, "SYSTEM_PROMPT", None)
        if isinstance(prompt, str) and prompt:
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    except Exception:
        pass

    return {
        "versions": versions,
        "fingerprint": fingerprint,
        "system_prompt_hash": prompt_hash,
    }


def get_environment_manifest() -> dict:
    """Return the cached environment manifest (lazy-computed, immutable)."""
    global _env_manifest
    if _env_manifest is None:
        try:
            _env_manifest = _compute_environment_manifest()
        except Exception as e:
            logger.warning("environment manifest capture failed: %s", e)
            _env_manifest = {"fingerprint": "unknown", "error": str(e)}
    return _env_manifest


def record_activity(
    entity_type: str,
    entity_id: str,
    activity: str,
    params: dict | None = None,
    parent_ids: list[str] | None = None,
    agent: str = "system",
    environment: dict | None = None,
    data_release: str | None = None,
) -> str:
    """Record a provenance activity.

    Args:
        data_release: data archive version identifier
            (e.g. "Gaia DR3", "SDSS DR17", "SIMBAD 2024-01") for
            activities that touched an upstream catalog. Enables
            reproduction even when the upstream archive rev-bumps.
    """
    record_id = str(uuid.uuid4())
    # Merge caller-provided environment with the auto-captured manifest.
    # Caller-provided keys win (explicit override for edge cases).
    merged_env = dict(get_environment_manifest())
    if environment:
        merged_env.update(environment)
    record = {
        "id": record_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "activity": activity,
        "params": params or {},
        "parent_ids": parent_ids or [],
        "agent": agent,
        "environment": merged_env,
        "data_release": data_release,
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
        xml_parts.append('  <prov:wasDerivedFrom>')
        xml_parts.append(f'    <prov:generatedEntity prov:ref="{edge["to"]}"/>')
        xml_parts.append(f'    <prov:usedEntity prov:ref="{edge["from"]}"/>')
        xml_parts.append('  </prov:wasDerivedFrom>')

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
    lines = ["# Standard Astro environment snapshot"]
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

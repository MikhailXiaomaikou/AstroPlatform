"""Data provenance and reproducibility service.

Records activity provenance for pipeline executions, provides
lineage traversal, IVOA ProvDM-compatible export, and DOI metadata generation.
"""

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Hot in-process cache.  The database is authoritative; this list preserves
# compatibility with local/offline execution and avoids a read-after-write
# round trip in the current worker.
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
    user_id: str | None = None,
    session_id: str | None = None,
    artifact_sha256: str | None = None,
) -> str:
    """Record a provenance activity.

    Args:
        data_release: data archive version identifier
            (e.g. "Gaia DR3", "SDSS DR17", "SIMBAD 2024-01") for
            activities that touched an upstream catalog. Enables
            reproduction even when the upstream archive rev-bumps.
    """
    record_id = str(uuid.uuid4())
    # Older pipeline/tool call sites do not all carry the owner explicitly.
    # Resolve it from a trusted session/run table instead of accepting an
    # owner embedded in free-form params.
    if not user_id and (session_id or entity_type == "pipeline_node"):
        try:
            from app.services.durable_research_records import resolve_record_owner

            pipeline_run_id = entity_id.split(":", 1)[0] if entity_type == "pipeline_node" else None
            user_id = resolve_record_owner(
                session_id=session_id,
                pipeline_run_id=pipeline_run_id,
            )
        except Exception:
            logger.debug("provenance owner lookup failed", exc_info=True)
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
        "user_id": str(user_id) if user_id else None,
        "session_id": str(session_id) if session_id else None,
        "artifact_sha256": artifact_sha256,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _provenance_records.append(record)
    try:
        from app.services.durable_research_records import save_provenance

        save_provenance(record)
    except Exception:
        logger.warning("provenance durable persistence unavailable", exc_info=True)
    logger.debug("Provenance recorded: %s [%s] %s", entity_id, activity, record_id)
    return record_id


def _records_for_entity(entity_id: str, owner_id: str | None = None) -> list[dict]:
    memory_records = [
        record
        for record in _provenance_records
        if record.get("entity_id") == entity_id
        and (owner_id is None or str(record.get("user_id") or "") == str(owner_id))
    ]
    try:
        from app.services.durable_research_records import load_provenance

        durable_records = load_provenance(entity_id, owner_id=owner_id)
    except Exception:
        durable_records = []

    # A freshly recorded activity is normally present in both the hot cache
    # and database.  Preserve stable creation order while removing duplicates.
    by_id: dict[str, dict] = {}
    for record in [*durable_records, *memory_records]:
        by_id[str(record.get("id") or uuid.uuid4())] = record
    return sorted(by_id.values(), key=lambda record: str(record.get("timestamp") or ""))


def get_lineage(entity_id: str, *, owner_id: str | None = None) -> dict:
    """Build a complete lineage graph for an entity."""
    nodes = []
    edges = []
    visited = set()

    def _traverse(eid: str):
        if eid in visited:
            return
        visited.add(eid)

        records = _records_for_entity(eid, owner_id=owner_id)
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


def export_provenance_ivoa(entity_id: str, *, owner_id: str | None = None) -> str:
    """Export provenance as IVOA ProvDM-compatible XML (W3C PROV serialization)."""
    lineage = get_lineage(entity_id, owner_id=owner_id)

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
                          authors: list[str] | None = None,
                          *, owner_id: str | None = None) -> dict:
    """Generate DataCite-ready metadata for a provenance lineage.

    No DOI is minted or fabricated. The record carries a content-addressed
    URN over the canonical lineage; a real DOI requires registration with
    DataCite or Zenodo. (The previous behavior emitted a non-resolving
    identifier under Zenodo's real 10.5281 prefix, which an
    anti-fabrication platform must not do.)
    """
    lineage = get_lineage(entity_id, owner_id=owner_id)

    now = datetime.now(timezone.utc)
    lineage_digest = hashlib.sha256(
        json.dumps(
            lineage, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()

    return {
        "data": {
            "type": "dois",
            "attributes": {
                "doi": None,
                "doi_status": "not_minted",
                "alternateIdentifiers": [{
                    "alternateIdentifierType": "URN",
                    "alternateIdentifier": f"urn:sha256:{lineage_digest}",
                }],
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
        "note": "DataCite-ready metadata; no DOI is minted. The URN alternate identifier is a content address over the canonical lineage.",
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


def export_requirements_pinned(
    entity_id: str,
    *,
    owner_id: str,
) -> str:
    """Export the entity's persisted environment, never the live process.

    A requirements file is only reproducible when it is bound to an owned
    provenance record.  Unknown entities, another user's entities, and legacy
    records without a package snapshot all fail closed via ``LookupError``.
    """
    records = _records_for_entity(entity_id, owner_id=owner_id)
    if not records:
        raise LookupError("No provenance record found for this entity")

    aliases = {"sklearn": "scikit-learn"}
    excluded = {
        "python", "python_version", "platform", "timestamp", "fingerprint",
        "system_prompt_hash",
    }
    selected_record: dict | None = None
    packages: dict[str, str] = {}
    python_version = ""

    # Prefer the most recent usable snapshot if an entity contains multiple
    # activities. Every candidate comes from the persisted record itself.
    for record in reversed(records):
        environment = record.get("environment")
        if not isinstance(environment, dict):
            continue
        package_source = environment.get("packages")
        versions = environment.get("versions")
        if isinstance(package_source, dict) and package_source:
            candidate = package_source
            python_version = str(
                environment.get("python_version")
                or (versions.get("python") if isinstance(versions, dict) else "")
                or ""
            ).split()[0]
        elif isinstance(versions, dict):
            candidate = versions
            python_version = str(versions.get("python") or "").split()[0]
        else:
            # Some pipeline records store the version mapping flat.
            if not (environment.get("python") or environment.get("python_version")):
                continue
            candidate = environment
            python_version = str(
                environment.get("python") or environment.get("python_version") or ""
            ).split()[0]

        normalized: dict[str, str] = {}
        for raw_name, raw_version in candidate.items():
            name = str(raw_name or "").strip()
            version = str(raw_version or "").strip()
            if (
                not name
                or name.lower() in excluded
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) is None
                or not version
                or "\n" in version
                or "\r" in version
                or version.lower() in {"unknown", "missing", "none"}
            ):
                continue
            normalized[aliases.get(name.lower(), name)] = version
        if normalized:
            selected_record = record
            packages = normalized
            break

    if selected_record is None or not packages:
        raise LookupError("No saved environment package snapshot for this entity")

    lines = ["# Standard Astro entity-bound environment snapshot"]
    lines.append(f"# Entity: {entity_id}")
    if selected_record.get("id"):
        lines.append(f"# Provenance record: {selected_record['id']}")
    if python_version:
        lines.append(f"# Python {python_version}")
    if selected_record.get("timestamp"):
        lines.append(f"# Captured: {selected_record['timestamp']}")
    for package, version in sorted(packages.items(), key=lambda item: item[0].lower()):
        lines.append(f"{package}=={version}")
    return "\n".join(lines) + "\n"


def get_reproducibility_package(entity_id: str, *, owner_id: str | None = None) -> dict:
    """Generate a reproducibility package for a pipeline run."""
    lineage = get_lineage(entity_id, owner_id=owner_id)

    records = _records_for_entity(entity_id, owner_id=owner_id)

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

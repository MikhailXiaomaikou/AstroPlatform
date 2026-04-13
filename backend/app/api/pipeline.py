"""Pipeline API — run, query, save, and list pipeline templates."""

import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth import get_current_user, get_optional_user, hash_password
from app.rate_limit import limiter
from app.models.database import get_db
from app.models.schemas import PipelineRun, PipelineTemplateDB, PipelineVersion, User
from app.pipeline.engine import execute_dag, execute_pipeline_task, topological_sort
from app.pipeline.nodes import registry
from app.pipeline.validate import DAGValidationError, validate_dag
from app.utils.usernames import internal_email_for_username

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

_ANONYMOUS_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
_ANONYMOUS_USER_EMAIL = internal_email_for_username("guest")


# ── Request / Response models ──

class RunRequest(BaseModel):
    dag: dict
    input_data_id: str


class RunResponse(BaseModel):
    run_id: str
    status: str
    results: dict = {}
    warnings: list[str] = []


class PipelineTemplate(BaseModel):
    id: str
    name: str
    description: str
    dag: dict


class SaveTemplateRequest(BaseModel):
    name: str
    description: str
    dag: dict


class SaveVersionRequest(BaseModel):
    dag: dict
    change_note: str = ""


class VersionSummary(BaseModel):
    id: str
    version: int
    change_note: str
    created_at: str


class VersionDetail(BaseModel):
    id: str
    version: int
    change_note: str
    dag: dict
    created_at: str


class DiffResult(BaseModel):
    added_nodes: list[dict]
    removed_nodes: list[dict]
    modified_nodes: list[dict]
    added_edges: list[dict]
    removed_edges: list[dict]


async def _get_or_create_anonymous_user(db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.id == _ANONYMOUS_USER_ID))
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    user = User(
        id=_ANONYMOUS_USER_ID,
        username="guest",
        email=_ANONYMOUS_USER_EMAIL,
        password_hash=hash_password(str(uuid.uuid4())),
        subscription_tier="solo",
        display_name="Guest",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _get_owned_template(
    db: AsyncSession, template_id: uuid.UUID, user: User
) -> PipelineTemplateDB:
    result = await db.execute(
        select(PipelineTemplateDB).where(
            PipelineTemplateDB.id == template_id,
            PipelineTemplateDB.user_id == user.id,
        )
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


async def _get_accessible_template(
    db: AsyncSession, template_id: uuid.UUID, user: User
) -> PipelineTemplateDB:
    result = await db.execute(
        select(PipelineTemplateDB).where(
            PipelineTemplateDB.id == template_id,
            (
                (PipelineTemplateDB.user_id == user.id)
                | (PipelineTemplateDB.is_builtin.is_(True))
            ),
        )
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


# ── Built-in templates (seeded on first request) ──

_BUILTIN_TEMPLATES = [
    {
        "name": "Spectrum Clean & Fit",
        "description": "Load → Denoise → Spectral Fit → Plot",
        "dag": {
            "nodes": [
                {"id": "n1", "type": "LoadData", "position": {"x": 0, "y": 150}, "data": {"label": "Load Data", "params": {}}},
                {"id": "n2", "type": "Denoise", "position": {"x": 300, "y": 150}, "data": {"label": "Denoise", "params": {"sigma": 3.0}}},
                {"id": "n3", "type": "SpectralFit", "position": {"x": 600, "y": 100}, "data": {"label": "Spectral Fit", "params": {"model": "gaussian"}}},
                {"id": "n4", "type": "Plot", "position": {"x": 900, "y": 150}, "data": {"label": "Plot", "params": {"plot_type": "spectrum"}}},
            ],
            "edges": [
                {"id": "e1-2", "source": "n1", "target": "n2"},
                {"id": "e2-3", "source": "n2", "target": "n3"},
                {"id": "e3-4", "source": "n3", "target": "n4"},
            ],
        },
    },
    {
        "name": "Coordinate Transform",
        "description": "Load → Coord Transform (ICRS → Galactic) → Plot scatter",
        "dag": {
            "nodes": [
                {"id": "n1", "type": "LoadData", "position": {"x": 0, "y": 150}, "data": {"label": "Load Data", "params": {}}},
                {"id": "n2", "type": "CoordTransform", "position": {"x": 350, "y": 150}, "data": {"label": "Coord Transform", "params": {"from_frame": "icrs", "to_frame": "galactic"}}},
                {"id": "n3", "type": "Plot", "position": {"x": 700, "y": 150}, "data": {"label": "Plot", "params": {"plot_type": "scatter", "x_key": "l", "y_key": "b"}}},
            ],
            "edges": [
                {"id": "e1-2", "source": "n1", "target": "n2"},
                {"id": "e2-3", "source": "n2", "target": "n3"},
            ],
        },
    },
    {
        "name": "Redshift Analysis",
        "description": "Load \u2192 Denoise \u2192 Redshift Estimate \u2192 Plot",
        "dag": {
            "nodes": [
                {"id": "n1", "type": "LoadData", "position": {"x": 0, "y": 150}, "data": {"label": "Load Data", "params": {}}},
                {"id": "n2", "type": "Denoise", "position": {"x": 300, "y": 150}, "data": {"label": "Denoise", "params": {"sigma": 3.0}}},
                {"id": "n3", "type": "RedshiftEstimate", "position": {"x": 600, "y": 150}, "data": {"label": "Redshift", "params": {}}},
                {"id": "n4", "type": "Plot", "position": {"x": 900, "y": 150}, "data": {"label": "Plot", "params": {"plot_type": "spectrum"}}},
            ],
            "edges": [
                {"id": "e1-2", "source": "n1", "target": "n2"},
                {"id": "e2-3", "source": "n2", "target": "n3"},
                {"id": "e3-4", "source": "n3", "target": "n4"},
            ],
        },
    },
]


# ── Endpoints ──

@router.get("/nodes/types")
async def list_node_types():
    return [
        {"type": "QueryData", "label": "Query Data", "description": "Search catalog sources like SIMBAD, Gaia, SDSS, or MAST", "inputs": 0, "outputs": 1},
        {"type": "ImportWorkspace", "label": "Import Workspace", "description": "Load a saved Workspace file into the pipeline", "inputs": 0, "outputs": 1},
        {"type": "LoadData", "label": "Load Data", "description": "Load a FITS file from storage", "inputs": 0, "outputs": 1},
        {"type": "BiasSubtract", "label": "Bias Subtract", "description": "Subtract a master bias from an image", "inputs": 1, "outputs": 1},
        {"type": "DarkCorrect", "label": "Dark Correct", "description": "Subtract a scaled master dark", "inputs": 1, "outputs": 1},
        {"type": "FlatField", "label": "Flat Field", "description": "Correct an image with a master flat", "inputs": 1, "outputs": 1},
        {"type": "CosmicRayReject", "label": "Cosmic Ray Reject", "description": "Detect and clean cosmic rays in CCD data", "inputs": 1, "outputs": 2},
        {"type": "AstrometricSolve", "label": "Astrometric Solve", "description": "Plate-solve an image and attach WCS metadata", "inputs": 1, "outputs": 1},
        {"type": "SourceExtract", "label": "Source Extract", "description": "Detect sources and run aperture photometry", "inputs": 1, "outputs": 1},
        {"type": "Denoise", "label": "Denoise", "description": "Sigma-clip noise from spectrum", "inputs": 1, "outputs": 1},
        {"type": "SpectralFit", "label": "Spectral Fit", "description": "Fit Gaussian/Lorentzian to spectrum", "inputs": 1, "outputs": 1},
        {"type": "CoordTransform", "label": "Coord Transform", "description": "Transform coordinate frames", "inputs": 1, "outputs": 1},
        {"type": "Plot", "label": "Plot", "description": "Generate PNG plot", "inputs": 1, "outputs": 1},
        {"type": "RedshiftEstimate", "label": "Redshift", "description": "Estimate redshift from spectral lines", "inputs": 1, "outputs": 1},
        {"type": "EquivalentWidth", "label": "Equiv. Width", "description": "Measure spectral line equivalent width", "inputs": 1, "outputs": 1},
        {"type": "SEDFit", "label": "SED Fit", "description": "Fit spectral energy distribution", "inputs": 1, "outputs": 1},
        {"type": "CrossMatch", "label": "Cross-Match", "description": "Cross-match two catalogs by coordinates", "inputs": 1, "outputs": 1},
        {"type": "PhotCalibrate", "label": "Phot. Calibrate", "description": "Apply photometric calibration", "inputs": 1, "outputs": 1},
        {"type": "ImageStack", "label": "Image Stack", "description": "Stack/combine multiple images", "inputs": 1, "outputs": 1},
        {"type": "InteractivePlot", "label": "Interactive Plot", "description": "Generate interactive Plotly visualization", "inputs": 1, "outputs": 1},
        {"type": "CustomScript", "label": "Custom Script", "description": "Run custom Python code (numpy, scipy, astropy)", "inputs": 1, "outputs": 1},
        {"type": "TimeSeriesAnalysis", "label": "Time Series", "description": "Lomb-Scargle period detection and variability classification", "inputs": 1, "outputs": 1},
    ]


@router.get("/templates", response_model=list[PipelineTemplate])
async def list_templates(
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    # Seed built-in templates if not yet in DB
    result = await db.execute(
        select(PipelineTemplateDB).where(PipelineTemplateDB.is_builtin.is_(True))
    )
    builtins = result.scalars().all()
    if not builtins:
        for tpl_data in _BUILTIN_TEMPLATES:
            tpl = PipelineTemplateDB(
                name=tpl_data["name"],
                description=tpl_data["description"],
                dag=tpl_data["dag"],
                is_builtin=True,
            )
            db.add(tpl)
        await db.commit()

    # Fetch all templates: built-in + user's own
    query = select(PipelineTemplateDB).where(PipelineTemplateDB.is_builtin.is_(True))
    if user:
        query = select(PipelineTemplateDB).where(
            (PipelineTemplateDB.is_builtin.is_(True)) |
            (PipelineTemplateDB.user_id == user.id)
        )
    result = await db.execute(query)
    templates = result.scalars().all()

    return [
        PipelineTemplate(
            id=str(t.id),
            name=t.name,
            description=t.description,
            dag=t.dag,
        )
        for t in templates
    ]


@router.post("/save")
async def save_template(
    req: SaveTemplateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tpl = PipelineTemplateDB(
        name=req.name,
        description=req.description,
        dag=req.dag,
        user_id=user.id,
    )
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    return {"id": str(tpl.id)}


@router.post("/run", response_model=RunResponse)
@limiter.limit("20/minute")
async def run_pipeline(
    request: Request,
    req: RunRequest,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
    async_mode: bool = Query(True, description="When True, dispatch via Celery (requires Redis)"),
):
    """Validate DAG and execute pipeline.

    When async_mode=True (default), dispatches execution to Celery and returns
    immediately with status="running". When async_mode=False, runs synchronously
    and returns results inline.
    """
    if "nodes" not in req.dag or "edges" not in req.dag:
        raise HTTPException(status_code=400, detail="DAG must have 'nodes' and 'edges'")

    # Validate DAG structure (cycles, duplicates, dangling edges)
    try:
        dag_warnings = validate_dag(req.dag)
    except DAGValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    for node in req.dag["nodes"]:
        if node.get("type") not in registry:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown node type: {node.get('type')}. Available: {list(registry.keys())}",
            )

    try:
        topological_sort(req.dag)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Create DB record
    run_id = uuid.uuid4()
    owner = user or await _get_or_create_anonymous_user(db)

    run = PipelineRun(
        id=run_id,
        user_id=owner.id,
        dag=req.dag,
        status="pending",
    )
    db.add(run)
    await db.commit()

    run_id_str = str(run_id)

    # Async execution via Celery (when async_mode=True)
    if async_mode:
        try:
            execute_pipeline_task.delay(run_id_str, req.dag, req.input_data_id)
            return RunResponse(run_id=run_id_str, status="running", warnings=dag_warnings)
        except Exception as e:
            logger.warning(f"Celery dispatch failed, falling back to sync: {e}")

    # Synchronous execution in thread executor (avoids blocking async event loop)
    import asyncio
    loop = asyncio.get_running_loop()
    try:
        node_results = await loop.run_in_executor(
            None, execute_dag, req.dag, req.input_data_id, run_id_str
        )
    except Exception as e:
        logger.exception(f"Pipeline run {run_id_str} failed")
        run.status = "failed"
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Pipeline failed: {type(e).__name__}: {e}")

    # Trim results
    safe_results = _trim_results(node_results)

    run.status = "completed"
    run.results = safe_results
    run.completed_at = datetime.now(timezone.utc)
    await db.commit()

    return RunResponse(run_id=run_id_str, status="completed", results=safe_results, warnings=dag_warnings)


class BatchRunRequest(BaseModel):
    dag: dict
    input_data_ids: list[str]


class BatchRunResult(BaseModel):
    results: list[dict]
    total: int
    succeeded: int
    failed: int


@router.post("/batch-run", response_model=BatchRunResult)
@limiter.limit("5/minute")
async def batch_run_pipeline(
    request: Request,
    req: BatchRunRequest,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """Run the same pipeline on multiple input files. Max 20 per batch."""
    if len(req.input_data_ids) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 inputs per batch")

    if "nodes" not in req.dag or "edges" not in req.dag:
        raise HTTPException(status_code=400, detail="DAG must have 'nodes' and 'edges'")

    try:
        validate_dag(req.dag)
    except DAGValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    results = []
    succeeded = 0
    failed = 0

    for input_id in req.input_data_ids:
        run_id = str(uuid.uuid4())
        try:
            node_results = execute_dag(req.dag, input_id, run_id)
            safe = _trim_results(node_results)
            results.append({"input": input_id, "run_id": run_id, "status": "completed", "results": safe})
            succeeded += 1
        except Exception as e:
            results.append({"input": input_id, "run_id": run_id, "status": "failed", "error": str(e)})
            failed += 1

    return BatchRunResult(
        results=results,
        total=len(req.input_data_ids),
        succeeded=succeeded,
        failed=failed,
    )


@router.get("/{run_id}")
async def get_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        rid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid run ID")

    result = await db.execute(
        select(PipelineRun).where(PipelineRun.id == rid, PipelineRun.user_id == user.id)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    return {
        "run_id": str(run.id),
        "status": run.status,
        "dag": run.dag,
        "results": run.results or {},
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


# ── Version Management Endpoints ──

@router.post("/templates/{template_id}/versions", response_model=VersionSummary)
async def save_template_version(
    template_id: str,
    req: SaveVersionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Save a new version of a template. Auto-increments version number."""
    try:
        tid = uuid.UUID(template_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid template ID")

    template = await _get_owned_template(db, tid, user)

    # Determine next version number
    result = await db.execute(
        select(func.coalesce(func.max(PipelineVersion.version), 0))
        .where(PipelineVersion.template_id == tid)
    )
    max_version = result.scalar()
    next_version = max_version + 1

    version = PipelineVersion(
        template_id=tid,
        version=next_version,
        dag=req.dag,
        change_note=req.change_note,
    )
    db.add(version)

    # Update the template's DAG to the latest
    template.dag = req.dag
    await db.commit()
    await db.refresh(version)

    return VersionSummary(
        id=str(version.id),
        version=version.version,
        change_note=version.change_note,
        created_at=version.created_at.isoformat() if version.created_at else "",
    )


@router.get("/templates/{template_id}/versions", response_model=list[VersionSummary])
async def list_template_versions(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all versions of a template, newest first."""
    try:
        tid = uuid.UUID(template_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid template ID")

    await _get_accessible_template(db, tid, user)

    result = await db.execute(
        select(PipelineVersion)
        .where(PipelineVersion.template_id == tid)
        .order_by(PipelineVersion.version.desc())
    )
    versions = result.scalars().all()

    return [
        VersionSummary(
            id=str(v.id),
            version=v.version,
            change_note=v.change_note,
            created_at=v.created_at.isoformat() if v.created_at else "",
        )
        for v in versions
    ]


@router.get("/templates/{template_id}/versions/{version_id}", response_model=VersionDetail)
async def get_template_version(
    template_id: str,
    version_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get a single version's full DAG."""
    try:
        tid = uuid.UUID(template_id)
        vid = uuid.UUID(version_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID")

    await _get_accessible_template(db, tid, user)

    result = await db.execute(
        select(PipelineVersion)
        .where(PipelineVersion.id == vid, PipelineVersion.template_id == tid)
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")

    return VersionDetail(
        id=str(version.id),
        version=version.version,
        change_note=version.change_note,
        dag=version.dag,
        created_at=version.created_at.isoformat() if version.created_at else "",
    )


@router.get("/templates/{template_id}/diff", response_model=DiffResult)
async def diff_versions(
    template_id: str,
    v1: str,
    v2: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Compute structural diff between two versions of a template."""
    try:
        tid = uuid.UUID(template_id)
        vid1 = uuid.UUID(v1)
        vid2 = uuid.UUID(v2)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID")

    await _get_accessible_template(db, tid, user)

    result1 = await db.execute(
        select(PipelineVersion)
        .where(PipelineVersion.id == vid1, PipelineVersion.template_id == tid)
    )
    ver1 = result1.scalar_one_or_none()

    result2 = await db.execute(
        select(PipelineVersion)
        .where(PipelineVersion.id == vid2, PipelineVersion.template_id == tid)
    )
    ver2 = result2.scalar_one_or_none()

    if ver1 is None or ver2 is None:
        raise HTTPException(status_code=404, detail="One or both versions not found")

    return _compute_dag_diff(ver1.dag, ver2.dag)


def _compute_dag_diff(dag1: dict, dag2: dict) -> DiffResult:
    """Compute structural diff between two DAGs."""
    nodes1 = {n["id"]: n for n in dag1.get("nodes", [])}
    nodes2 = {n["id"]: n for n in dag2.get("nodes", [])}

    edges1 = {e["id"]: e for e in dag1.get("edges", [])}
    edges2 = {e["id"]: e for e in dag2.get("edges", [])}

    added_nodes = [nodes2[nid] for nid in nodes2 if nid not in nodes1]
    removed_nodes = [nodes1[nid] for nid in nodes1 if nid not in nodes2]

    modified_nodes = []
    for nid in nodes1:
        if nid in nodes2 and nodes1[nid] != nodes2[nid]:
            modified_nodes.append({
                "id": nid,
                "old": nodes1[nid],
                "new": nodes2[nid],
            })

    added_edges = [edges2[eid] for eid in edges2 if eid not in edges1]
    removed_edges = [edges1[eid] for eid in edges1 if eid not in edges2]

    return DiffResult(
        added_nodes=added_nodes,
        removed_nodes=removed_nodes,
        modified_nodes=modified_nodes,
        added_edges=added_edges,
        removed_edges=removed_edges,
    )


def _trim_results(node_results: dict) -> dict:
    safe_results = {}
    for nid, res in node_results.items():
        safe = dict(res)
        if "data" in safe and isinstance(safe["data"], dict):
            trimmed = {}
            for k, v in safe["data"].items():
                if isinstance(v, list) and len(v) > 20:
                    trimmed[k] = v[:10] + ["..."] + v[-10:]
                else:
                    trimmed[k] = v
            safe["data"] = trimmed
        safe_results[nid] = safe
    return safe_results

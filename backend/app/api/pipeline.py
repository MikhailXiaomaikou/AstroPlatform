"""Pipeline API — run, query, save, and list pipeline templates."""

import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth import get_current_user, get_optional_user
from app.config import settings
from app.rate_limit import limiter
from app.models.database import get_db
from app.models.schemas import PipelineRun, PipelineTemplateDB, PipelineVersion, RunResult, User
from app.pipeline.engine import execute_dag, execute_pipeline_task, topological_sort
from app.pipeline.nodes import dag_has_heavy_nodes
from app.pipeline.nodes import registry
from app.pipeline.storage_auth import (
    PipelineStorageInputError,
    bind_pipeline_storage_inputs,
)
from app.pipeline.validate import DAGValidationError, validate_dag

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

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


async def _bind_owned_pipeline_inputs(
    *,
    dag: dict,
    input_data_id: str,
    user: User,
    db: AsyncSession,
) -> tuple[dict, str]:
    """Bind every storage-reading node to a ``DataFile`` owned by ``user``.

    Pipeline nodes execute later in a thread or Celery worker and therefore
    cannot safely infer request ownership.  Resolve paths at the authenticated
    HTTP boundary, write only the authorised canonical key into a copied DAG,
    and pass that copy downstream.  A physical object existing in local/S3
    storage is never authorization by itself.
    """
    from app.storage import (
        StorageOwnerRequired,
        StorageOwnershipError,
        resolve_owned_storage_key,
    )

    for node in dag.get("nodes", []):
        if (
            isinstance(node, dict)
            and node.get("type") == "CustomScript"
            and settings.sandbox_backend == "disabled"
        ):
            raise HTTPException(
                status_code=503,
                detail=(
                    "CustomScript is disabled because no OS-isolated code "
                    "execution backend is configured"
                ),
            )

    async def _owned(raw: str) -> str:
        return await resolve_owned_storage_key(raw, owner_id=user.id, db=db)

    try:
        return await bind_pipeline_storage_inputs(
            dag=dag,
            input_data_id=input_data_id,
            resolve_key=_owned,
        )
    except (PipelineStorageInputError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid pipeline input path"
        ) from exc
    except (StorageOwnershipError, StorageOwnerRequired) as exc:
        # Uniform missing semantics prevent cross-account enumeration.
        raise HTTPException(status_code=404, detail="Pipeline input not found") from exc


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
    node_types = [
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
    if settings.sandbox_backend == "disabled":
        node_types = [node for node in node_types if node["type"] != "CustomScript"]
    return node_types


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
    user: User = Depends(get_current_user),
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

    bound_dag, bound_input_data_id = await _bind_owned_pipeline_inputs(
        dag=req.dag,
        input_data_id=req.input_data_id,
        user=user,
        db=db,
    )

    # Hosted HTTPS workers accept only registered workflow envelopes, never an
    # arbitrary Pipeline DAG.  Reject an asynchronous request before creating
    # a pending ledger row that no executor can consume.  Callers may still
    # request async_mode=false for explicitly classified light nodes.
    science_celery_enabled = settings.science_execution_backend == "celery"
    science_celery_dispatch = (
        async_mode
        and settings.pipeline_mode == "celery"
        and science_celery_enabled
    )
    if (
        async_mode
        and settings.pipeline_mode == "celery"
        and not science_celery_enabled
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "This deployment has no registered executor for arbitrary "
                "asynchronous pipelines; use a registered science workflow."
            ),
        )

    heavy_ids = dag_has_heavy_nodes(bound_dag)
    if heavy_ids and not science_celery_dispatch:
        raise HTTPException(
            status_code=503,
            detail=(
                f"This DAG contains heavy nodes ({', '.join(heavy_ids)}) but this "
                "deployment has no registered executor for arbitrary pipelines. "
                "Remove the heavy nodes or use a registered science workflow."
            ),
        )

    # Create DB record
    run_id = uuid.uuid4()

    run = PipelineRun(
        id=run_id,
        user_id=user.id,
        dag=bound_dag,
        status="pending",
    )
    db.add(run)
    await db.commit()

    run_id_str = str(run_id)

    # Publishing science tasks requires both Celery pipeline mode and an
    # actual Celery science executor.  Hosted Render keeps Celery for control
    # and verification work while SCIENCE_EXECUTION_BACKEND=https_worker; its
    # registered HTTPS Worker must never receive an arbitrary pipeline DAG.
    if science_celery_dispatch:
        try:
            execute_pipeline_task.delay(run_id_str, bound_dag, bound_input_data_id)
            return RunResponse(run_id=run_id_str, status="running", warnings=dag_warnings)
        except Exception as e:
            if heavy_ids:
                run.status = "failed"
                run.completed_at = datetime.now(timezone.utc)
                run.results = {
                    "success": False,
                    "error_class": "celery_dispatch_failed",
                    "error": "Heavy pipeline could not be queued.",
                }
                await db.commit()
                logger.exception(
                    "Heavy pipeline %s could not be published to Celery",
                    run_id_str,
                )
                raise HTTPException(
                    status_code=503,
                    detail="Heavy pipeline could not be queued; no local fallback ran.",
                ) from e
            logger.warning(f"Celery dispatch failed, falling back to sync: {e}")

    # Synchronous execution in thread executor (avoids blocking async event loop)
    import asyncio
    loop = asyncio.get_running_loop()
    try:
        node_results = await loop.run_in_executor(
            None,
            execute_dag,
            bound_dag,
            bound_input_data_id,
            run_id_str,
            str(user.id),
        )
    except Exception as e:
        logger.exception(f"Pipeline run {run_id_str} failed")
        run.status = "failed"
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Pipeline failed: {type(e).__name__}: {e}")

    # Store full results to DB; trim only for API response.
    # Mirror the Celery path: any node that failed (or was skipped because an
    # upstream node failed) carries an "error" key, so a run with such nodes is
    # reported as "failed", not "completed".
    has_errors = any(
        isinstance(res, dict) and "error" in res for res in node_results.values()
    )
    run_status = "failed" if has_errors else "completed"
    run.status = run_status
    run.results = node_results
    run.completed_at = datetime.now(timezone.utc)
    await db.commit()

    api_results = _trim_for_api(node_results)
    return RunResponse(run_id=run_id_str, status=run_status, results=api_results, warnings=dag_warnings)


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
    user: User = Depends(get_current_user),
):
    """Run the same pipeline on multiple input files. Max 200 per batch."""
    import asyncio

    if len(req.input_data_ids) > 200:
        raise HTTPException(status_code=400, detail="Maximum 200 inputs per batch")

    if "nodes" not in req.dag or "edges" not in req.dag:
        raise HTTPException(status_code=400, detail="DAG must have 'nodes' and 'edges'")

    try:
        validate_dag(req.dag)
    except DAGValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    results = []
    succeeded = 0
    failed = 0
    loop = asyncio.get_running_loop()

    for input_id in req.input_data_ids:
        run_id = str(uuid.uuid4())
        try:
            bound_dag, bound_input_id = await _bind_owned_pipeline_inputs(
                dag=req.dag,
                input_data_id=input_id,
                user=user,
                db=db,
            )
            node_results = await loop.run_in_executor(
                None,
                execute_dag,
                bound_dag,
                bound_input_id,
                run_id,
                str(user.id),
            )
            safe = _trim_for_api(node_results)
            # In-band node errors don't raise; a run with any errored/skipped
            # node is "failed", not a success.
            has_errors = any(
                isinstance(res, dict) and "error" in res for res in node_results.values()
            )
            if has_errors:
                results.append({"input": input_id, "run_id": run_id, "status": "failed", "results": safe})
                failed += 1
            else:
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


@router.get("/runs/compare")
async def compare_runs(
    run_ids: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Compare two pipeline runs: params, results, timing."""
    ids = [s.strip() for s in run_ids.split(",") if s.strip()]
    if len(ids) != 2:
        raise HTTPException(status_code=400, detail="Provide exactly 2 run IDs separated by comma")

    runs = []
    for rid_str in ids:
        try:
            rid = uuid.UUID(rid_str)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid run ID: {rid_str}")
        result = await db.execute(
            select(PipelineRun).where(PipelineRun.id == rid, PipelineRun.user_id == user.id)
        )
        run = result.scalar_one_or_none()
        if not run:
            raise HTTPException(status_code=404, detail=f"Run {rid_str} not found")
        runs.append(run)

    r1, r2 = runs

    # Compare DAGs
    dag_diff = {}
    dag1_nodes = {n["id"]: n for n in (r1.dag.get("nodes") or [])}
    dag2_nodes = {n["id"]: n for n in (r2.dag.get("nodes") or [])}
    all_node_ids = set(dag1_nodes) | set(dag2_nodes)
    for nid in all_node_ids:
        n1 = dag1_nodes.get(nid)
        n2 = dag2_nodes.get(nid)
        if n1 and not n2:
            dag_diff[nid] = {"status": "removed"}
        elif n2 and not n1:
            dag_diff[nid] = {"status": "added"}
        elif n1 and n2 and n1.get("data", {}).get("params") != n2.get("data", {}).get("params"):
            dag_diff[nid] = {
                "status": "changed",
                "params_1": n1["data"].get("params"),
                "params_2": n2["data"].get("params"),
            }

    # Compare results
    r1_results = r1.results or {}
    r2_results = r2.results or {}
    result_diff = {}
    for key in set(r1_results) | set(r2_results):
        if key.startswith("_"):
            continue
        v1 = r1_results.get(key)
        v2 = r2_results.get(key)
        if v1 != v2:
            result_diff[key] = {
                "run_1": str(v1)[:200] if v1 else None,
                "run_2": str(v2)[:200] if v2 else None,
            }

    return {
        "run_1": {"id": str(r1.id), "status": r1.status, "created_at": str(r1.created_at)},
        "run_2": {"id": str(r2.id), "status": r2.status, "created_at": str(r2.created_at)},
        "dag_diff": dag_diff,
        "result_diff": result_diff,
    }


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


@router.get("/runs/{run_id}/nodes/{node_id}")
async def get_node_result(
    run_id: str,
    node_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the output of a specific node from a pipeline run."""
    try:
        rid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid run ID")

    # Verify the run belongs to the user
    result = await db.execute(
        select(PipelineRun).where(
            PipelineRun.id == rid,
            PipelineRun.user_id == user.id,
        )
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # Check if results contain the node
    if run.results and node_id in run.results:
        return {"node_id": node_id, "result": run.results[node_id]}

    # Also check RunResult table
    rr = await db.execute(
        select(RunResult).where(
            RunResult.run_id == rid,
            RunResult.node_id == node_id,
        )
    )
    rr_row = rr.scalar_one_or_none()
    if rr_row:
        return {
            "node_id": node_id,
            "output_path": rr_row.output_path,
            "logs": rr_row.logs,
            "input_hash": rr_row.input_hash,
            "output_checksum": rr_row.output_checksum,
            "execution_time_ms": rr_row.execution_time_ms,
        }

    raise HTTPException(status_code=404, detail="Node result not found")


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


def _trim_for_api(node_results: dict) -> dict:
    """Trim large arrays for API responses (not for DB storage)."""
    safe_results = {}
    for nid, res in node_results.items():
        safe = dict(res)
        if "data" in safe and isinstance(safe["data"], dict):
            trimmed = {}
            truncated = False
            for k, v in safe["data"].items():
                if isinstance(v, list) and len(v) > 50:
                    trimmed[k] = v[:25] + ["..."] + v[-25:]
                    truncated = True
                else:
                    trimmed[k] = v
            if truncated:
                trimmed["_truncated"] = True
            safe["data"] = trimmed
        safe_results[nid] = safe
    return safe_results

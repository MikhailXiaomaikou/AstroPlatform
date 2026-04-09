"""Celery tasks for asynchronous pipeline execution."""

import logging
import uuid


from app.pipeline.nodes import registry

logger = logging.getLogger(__name__)


def _get_celery_app():
    from celery_worker import celery_app
    return celery_app


@_get_celery_app().task(bind=True, name="pipeline.run_node")
def run_node_task(self, node_id: str, node_type: str, params: dict,
                  input_data: dict, run_id: str) -> dict:
    """Execute a single pipeline node as a Celery task."""
    from app.api.ws import notify_progress_sync

    notify_progress_sync(run_id, {
        "type": "node_start",
        "node_id": node_id,
        "node_type": node_type,
    })

    node_fn = registry.get(node_type)
    if node_fn is None:
        error = f"Unknown node type: {node_type}"
        notify_progress_sync(run_id, {
            "type": "node_error",
            "node_id": node_id,
            "error": error,
        })
        return {"error": error, "node_id": node_id}

    try:
        result = node_fn(input_data, params)
        notify_progress_sync(run_id, {
            "type": "node_complete",
            "node_id": node_id,
        })
        return result
    except Exception as e:
        logger.error(f"[{run_id}] Node {node_id} failed: {e}")
        notify_progress_sync(run_id, {
            "type": "node_error",
            "node_id": node_id,
            "error": str(e),
        })
        return {"error": str(e), "node_id": node_id}


@_get_celery_app().task(bind=True, name="pipeline.finalize_run")
def finalize_run_task(self, node_results_list: list, run_id: str,
                      node_ids: list, dag: dict):
    """Finalize a pipeline run — store results in DB."""
    from app.api.ws import notify_progress_sync

    # Merge results from all nodes
    results = {}
    for node_id, result in zip(node_ids, node_results_list):
        results[node_id] = result

    notify_progress_sync(run_id, {
        "type": "run_complete",
        "status": "completed",
        "results": _trim_results(results),
    })

    # Store in database (sync)
    try:
        _store_results_sync(run_id, results)
    except Exception as e:
        logger.error(f"Failed to store results for run {run_id}: {e}")

    return {"run_id": run_id, "status": "completed", "results": _trim_results(results)}


def _trim_results(node_results: dict) -> dict:
    """Make results JSON-serializable by trimming large arrays."""
    safe = {}
    for nid, res in node_results.items():
        r = dict(res)
        if "data" in r and isinstance(r["data"], dict):
            trimmed = {}
            for k, v in r["data"].items():
                if isinstance(v, list) and len(v) > 20:
                    trimmed[k] = v[:10] + ["..."] + v[-10:]
                else:
                    trimmed[k] = v
            r["data"] = trimmed
        safe[nid] = r
    return safe


def _store_results_sync(run_id: str, results: dict):
    """Store pipeline results in PostgreSQL (synchronous, for Celery)."""
    import sqlalchemy
    from sqlalchemy.orm import Session
    from app.config import settings

    sync_url = settings.database_url.replace("+asyncpg", "+psycopg2")
    engine = sqlalchemy.create_engine(sync_url)

    try:
        with Session(engine) as session:
            from app.models.schemas import PipelineRun
            from sqlalchemy import update
            from datetime import datetime, timezone

            stmt = (
                update(PipelineRun)
                .where(PipelineRun.id == uuid.UUID(run_id))
                .values(
                    status="completed",
                    results=_trim_results(results),
                    completed_at=datetime.now(timezone.utc),
                )
            )
            session.execute(stmt)
            session.commit()
    finally:
        engine.dispose()

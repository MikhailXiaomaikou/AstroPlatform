"""Pipeline execution engine — topologically sorts a DAG and runs nodes synchronously (local mode)."""

import json
import logging
import uuid
from collections import deque
from datetime import datetime, timezone

from app.pipeline.nodes import registry

logger = logging.getLogger(__name__)


def topological_sort(dag: dict) -> list[list[str]]:
    """Return execution levels (each level can run in parallel).

    Args:
        dag: {"nodes": [{"id": ..., "type": ...}], "edges": [{"source": ..., "target": ...}]}

    Returns:
        List of levels, each level is a list of node IDs that can execute concurrently.
    """
    nodes = {n["id"] for n in dag["nodes"]}
    adj: dict[str, list[str]] = {nid: [] for nid in nodes}
    in_degree: dict[str, int] = {nid: 0 for nid in nodes}

    for edge in dag.get("edges", []):
        src, tgt = edge["source"], edge["target"]
        adj[src].append(tgt)
        in_degree[tgt] += 1

    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    levels: list[list[str]] = []

    while queue:
        level = list(queue)
        levels.append(level)
        queue.clear()
        for nid in level:
            for neighbor in adj[nid]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

    executed = sum(len(lvl) for lvl in levels)
    if executed != len(nodes):
        raise ValueError("Pipeline DAG contains a cycle")

    return levels


def build_node_map(dag: dict) -> dict[str, dict]:
    return {n["id"]: n for n in dag["nodes"]}


def build_parent_map(dag: dict) -> dict[str, list[str]]:
    parents: dict[str, list[str]] = {n["id"]: [] for n in dag["nodes"]}
    for edge in dag.get("edges", []):
        parents[edge["target"]].append(edge["source"])
    return parents


def execute_dag(dag: dict, input_data_id: str, run_id: str) -> dict:
    """Run the pipeline synchronously (local mode, no Celery needed).

    Returns dict of {node_id: result} for all nodes.
    """
    levels = topological_sort(dag)
    node_map = build_node_map(dag)
    parent_map = build_parent_map(dag)

    node_results: dict[str, dict] = {}

    for level in levels:
        for node_id in level:
            node_def = node_map[node_id]
            node_type = node_def["type"]
            params = node_def.get("data", {}).get("params", {})

            node_fn = registry.get(node_type)
            if node_fn is None:
                raise ValueError(f"Unknown node type: {node_type}")

            # Gather input from parents; skip if any parent errored
            parents = parent_map[node_id]
            if parents:
                parent_errors = [pid for pid in parents if "error" in node_results.get(pid, {})]
                if parent_errors:
                    err_msgs = "; ".join(node_results[p]["error"] for p in parent_errors)
                    node_results[node_id] = {"error": f"Skipped: upstream node(s) failed — {err_msgs}", "node_id": node_id}
                    continue
                input_data = {}
                for pid in parents:
                    if pid in node_results:
                        input_data.update(node_results[pid])
            else:
                input_data = {"fits_path": input_data_id}

            # LoadData needs fits_path in params
            if node_type == "LoadData" and "fits_path" not in params:
                params["fits_path"] = input_data.get("fits_path", input_data_id)

            logger.info(f"[{run_id}] Running node {node_id} ({node_type})")
            try:
                result = node_fn(input_data, params)
            except Exception as e:
                logger.error(f"[{run_id}] Node {node_id} failed: {e}")
                result = {"error": str(e), "node_id": node_id}

            node_results[node_id] = result

    return node_results


# ---------------------------------------------------------------------------
# Celery task: full DAG execution inside a single task
# ---------------------------------------------------------------------------

def _get_sync_session():
    """Create a synchronous SQLAlchemy session for use inside Celery tasks."""
    import sqlalchemy
    from sqlalchemy.orm import Session
    from app.config import settings

    # Convert async URL to sync URL
    sync_url = settings.database_url
    if "+aiosqlite" in sync_url:
        sync_url = sync_url.replace("+aiosqlite", "")
    elif "+asyncpg" in sync_url:
        sync_url = sync_url.replace("+asyncpg", "+psycopg2")

    engine = sqlalchemy.create_engine(sync_url)
    return engine, Session(engine)


def _publish_progress(run_id: str, data: dict):
    """Publish progress to Redis pub/sub for WebSocket relay."""
    try:
        import redis as redis_lib
        from app.config import settings
        kwargs = {}
        if settings.redis_ssl:
            kwargs["ssl_cert_reqs"] = "none"
        r = redis_lib.Redis.from_url(settings.redis_url, **kwargs)
        message = json.dumps({"run_id": run_id, **data})
        r.publish("pipeline_progress", message)
        r.close()
    except Exception as e:
        logger.warning(f"Failed to publish progress for run {run_id}: {e}")


def _get_celery_app():
    from celery_worker import celery_app
    return celery_app


@_get_celery_app().task(bind=True, name="pipeline.execute_pipeline")
def execute_pipeline_task(self, run_id: str, dag_dict: dict, input_data_id: str):
    """Celery task that executes an entire pipeline DAG.

    Steps:
    1. Update PipelineRun status to "running"
    2. Execute nodes in topological order
    3. Publish progress via Redis pub/sub
    4. Update status to "completed" or "failed"
    """
    from app.models.schemas import PipelineRun, RunResult

    engine, session = _get_sync_session()

    try:
        # 1. Mark run as "running"
        run = session.get(PipelineRun, uuid.UUID(run_id))
        if run is None:
            logger.error(f"PipelineRun {run_id} not found in database")
            return {"run_id": run_id, "status": "failed", "error": "Run not found"}

        run.status = "running"
        session.commit()

        _publish_progress(run_id, {"type": "run_start", "status": "running"})

        # 2. Execute DAG nodes in topological order
        levels = topological_sort(dag_dict)
        node_map = build_node_map(dag_dict)
        parent_map = build_parent_map(dag_dict)
        total_nodes = sum(len(lvl) for lvl in levels)

        node_results: dict[str, dict] = {}
        completed_count = 0
        has_errors = False

        for level in levels:
            for node_id in level:
                node_def = node_map[node_id]
                node_type = node_def["type"]
                params = node_def.get("data", {}).get("params", {})

                node_fn = registry.get(node_type)
                if node_fn is None:
                    error_msg = f"Unknown node type: {node_type}"
                    node_results[node_id] = {"error": error_msg, "node_id": node_id}
                    has_errors = True
                    _publish_progress(run_id, {
                        "type": "node_error",
                        "node_id": node_id,
                        "error": error_msg,
                    })
                    continue

                # Gather input from parents
                parents = parent_map[node_id]
                if parents:
                    parent_errors = [pid for pid in parents if "error" in node_results.get(pid, {})]
                    if parent_errors:
                        err_msgs = "; ".join(node_results[p]["error"] for p in parent_errors)
                        node_results[node_id] = {
                            "error": f"Skipped: upstream node(s) failed — {err_msgs}",
                            "node_id": node_id,
                        }
                        has_errors = True
                        _publish_progress(run_id, {
                            "type": "node_error",
                            "node_id": node_id,
                            "error": node_results[node_id]["error"],
                        })
                        continue
                    input_data = {}
                    for pid in parents:
                        if pid in node_results:
                            input_data.update(node_results[pid])
                else:
                    input_data = {"fits_path": input_data_id}

                if node_type == "LoadData" and "fits_path" not in params:
                    params["fits_path"] = input_data.get("fits_path", input_data_id)

                # Notify node start
                _publish_progress(run_id, {
                    "type": "node_start",
                    "node_id": node_id,
                    "node_type": node_type,
                })

                logger.info(f"[{run_id}] Running node {node_id} ({node_type})")
                try:
                    result = node_fn(input_data, params)
                except Exception as e:
                    logger.error(f"[{run_id}] Node {node_id} failed: {e}")
                    result = {"error": str(e), "node_id": node_id}
                    has_errors = True

                node_results[node_id] = result
                completed_count += 1

                # Store per-node result
                run_result = RunResult(
                    run_id=uuid.UUID(run_id),
                    node_id=node_id,
                    output_path=result.get("output_path"),
                    logs=json.dumps(result) if result else None,
                )
                session.add(run_result)
                session.commit()

                # Publish node progress
                status = "error" if "error" in result else "complete"
                _publish_progress(run_id, {
                    "type": f"node_{status}",
                    "node_id": node_id,
                    "progress": completed_count / total_nodes,
                })

        # 3. Trim and store final results
        safe_results = _trim_results(node_results)
        final_status = "completed" if not has_errors else "failed"

        run.status = final_status
        run.results = safe_results
        run.completed_at = datetime.now(timezone.utc)
        session.commit()

        # 4. Notify completion
        _publish_progress(run_id, {
            "type": "run_complete",
            "status": final_status,
            "results": safe_results,
        })

        logger.info(f"[{run_id}] Pipeline {final_status}")
        return {"run_id": run_id, "status": final_status, "results": safe_results}

    except Exception as exc:
        logger.exception(f"[{run_id}] Pipeline execution failed with exception")

        # Update DB status to failed
        try:
            run = session.get(PipelineRun, uuid.UUID(run_id))
            if run:
                run.status = "failed"
                run.completed_at = datetime.now(timezone.utc)
                session.commit()
        except Exception:
            logger.error(f"[{run_id}] Failed to update run status after error")

        _publish_progress(run_id, {
            "type": "run_error",
            "status": "failed",
            "error": str(exc),
        })

        raise
    finally:
        session.close()
        engine.dispose()


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

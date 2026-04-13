"""Pipeline execution engine — topologically sorts a DAG and runs nodes synchronously (local mode)."""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from app.pipeline.nodes import registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sync wrappers for async cache helpers (used in sync execute_dag & Celery)
# ---------------------------------------------------------------------------

def _cache_get_sync(key: str):
    """Synchronously fetch a cached value.  Returns None on any failure."""
    try:
        from app.cache import cache_get
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(cache_get(key))
        finally:
            loop.close()
    except Exception:
        return None


def _cache_set_sync(key: str, value, ttl: int = 300):
    """Synchronously store a value in the cache.  Silently ignores errors."""
    try:
        from app.cache import cache_set
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(cache_set(key, value, ttl=ttl))
        finally:
            loop.close()
    except Exception:
        pass


def _build_node_cache_key(node_type: str, params: dict, parents: list[str], node_results: dict) -> str | None:
    """Compute a deterministic cache key for a pipeline node.

    The key incorporates the node type, its parameters, and the output
    checksums of all parent nodes so that any upstream change invalidates
    the cache automatically.  Returns ``None`` if the key cannot be built.
    """
    try:
        parent_hashes = "".join(
            node_results.get(p, {}).get("_output_checksum", "") for p in parents
        ) if parents else ""
        cache_payload = json.dumps(
            {"params": params, "parent_hashes": parent_hashes},
            sort_keys=True, default=str,
        )
        return f"pipeline_node:{node_type}:{hashlib.sha256(cache_payload.encode()).hexdigest()}"
    except Exception:
        return None


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


def build_edge_index(dag: dict) -> dict[str, list[dict]]:
    """Map source node ID -> list of outgoing edges (with sourceHandle metadata)."""
    index: dict[str, list[dict]] = {n["id"]: [] for n in dag["nodes"]}
    for edge in dag.get("edges", []):
        index[edge["source"]].append(edge)
    return index


def _compute_skipped_nodes(
    node_id: str,
    condition_result: bool,
    edge_index: dict[str, list[dict]],
    parent_map: dict[str, list[str]],
    node_map: dict[str, dict],
) -> set[str]:
    """After a Condition node executes, determine which downstream nodes to skip.

    Edges from a Condition node carry a ``sourceHandle`` of ``"true"`` or
    ``"false"``.  The branch whose handle does NOT match the condition result
    is pruned.  Pruning propagates: if every parent of a node is either
    skipped or on the pruned branch, that node is skipped too.
    """
    skipped: set[str] = set()
    taken_handle = "true" if condition_result else "false"

    # Direct children on the NOT-taken branch
    for edge in edge_index.get(node_id, []):
        if edge.get("sourceHandle") and edge["sourceHandle"] != taken_handle:
            skipped.add(edge["target"])

    # Propagate: BFS from initially-skipped nodes
    queue = deque(skipped)
    while queue:
        sid = queue.popleft()
        for edge in edge_index.get(sid, []):
            child = edge["target"]
            if child in skipped:
                continue
            # Skip child only if ALL its parents are skipped
            if all(p in skipped or p == node_id for p in parent_map.get(child, [])):
                # … but not if the child is also reachable on the taken branch
                reachable_from_taken = any(
                    e["target"] == child
                    for e in edge_index.get(node_id, [])
                    if e.get("sourceHandle") == taken_handle
                )
                if not reachable_from_taken:
                    skipped.add(child)
                    queue.append(child)

    return skipped


def _capture_environment() -> dict:
    """Snapshot Python and key library versions for reproducibility."""
    import sys
    import platform as _platform
    versions = {"python": sys.version.split()[0], "platform": _platform.platform()}
    for pkg in ["numpy", "astropy", "scipy", "pandas", "scikit-learn"]:
        try:
            mod = __import__(pkg)
            versions[pkg] = getattr(mod, "__version__", "unknown")
        except ImportError:
            pass
    return versions


def execute_dag(dag: dict, input_data_id: str, run_id: str) -> dict:
    """Run the pipeline synchronously (local mode, no Celery needed).

    Returns dict of {node_id: result} for all nodes.
    """
    env_snapshot = _capture_environment()

    levels = topological_sort(dag)
    node_map = build_node_map(dag)
    parent_map = build_parent_map(dag)
    edge_index = build_edge_index(dag)

    node_results: dict[str, dict] = {"_environment": env_snapshot}
    skipped_nodes: set[str] = set()

    for level in levels:
        for node_id in level:
            # Skip nodes on un-taken condition branches
            if node_id in skipped_nodes:
                node_results[node_id] = {
                    "skipped": True,
                    "node_id": node_id,
                    "reason": "Condition branch not taken",
                }
                logger.info(f"[{run_id}] Skipping node {node_id} (condition branch not taken)")
                continue

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

                # Propagate uncertainties from parent nodes
                parent_uncertainties = {}
                for pid in parents:
                    parent_result = node_results.get(pid, {})
                    if "_uncertainties" in parent_result:
                        parent_uncertainties[pid] = parent_result["_uncertainties"]
                    # Also collect any error/uncertainty columns from data
                    parent_data = parent_result.get("data", {})
                    for key in parent_data:
                        if key.endswith("_err") or key.endswith("_error") or key.endswith("_uncertainty"):
                            parent_uncertainties.setdefault("_error_columns", {})[key] = True
                if parent_uncertainties:
                    input_data["_parent_uncertainties"] = parent_uncertainties
            else:
                input_data = {"fits_path": input_data_id, "path": input_data_id, "input_data_id": input_data_id}

            # LoadData needs fits_path in params
            if node_type == "LoadData" and "fits_path" not in params:
                params["fits_path"] = input_data.get("fits_path", input_data_id)
            if node_type == "ImportWorkspace" and "path" not in params:
                params["path"] = input_data.get("path", input_data_id)

            # -- Node-level cache: skip execution if result already cached ------
            # Build a cache key from node type, params, and parent output checksums.
            # Setting params["force_rerun"] = True bypasses the cache for this node.
            cache_key = _build_node_cache_key(node_type, params, parents, node_results)

            if cache_key and not params.get("force_rerun"):
                try:
                    cached = _cache_get_sync(cache_key)
                    if cached:
                        logger.info(f"[{run_id}] Node {node_id} cache hit")
                        cached["_cached"] = True
                        node_results[node_id] = cached
                        _publish_progress(run_id, {"node_id": node_id, "status": "completed", "cached": True})
                        # Handle Condition nodes even on cache hit
                        if node_type == "Condition" and "error" not in cached:
                            new_skips = _compute_skipped_nodes(
                                node_id, cached["_condition_result"],
                                edge_index, parent_map, node_map,
                            )
                            skipped_nodes |= new_skips
                        continue
                except Exception:
                    pass  # cache miss or error — proceed with normal execution
            # -- End cache lookup --------------------------------------------------

            logger.info(f"[{run_id}] Running node {node_id} ({node_type})")
            input_hash = hashlib.sha256(
                json.dumps(params, sort_keys=True, default=str).encode()
            ).hexdigest()
            t0 = time.monotonic()
            try:
                result = node_fn(input_data, params)
            except Exception as e:
                logger.error(f"[{run_id}] Node {node_id} failed: {e}")
                result = {"error": str(e), "node_id": node_id}
            execution_time_ms = int((time.monotonic() - t0) * 1000)
            output_checksum = hashlib.sha256(
                json.dumps(result, sort_keys=True, default=str).encode()
            ).hexdigest()

            result["_input_hash"] = input_hash
            result["_output_checksum"] = output_checksum
            result["_execution_time_ms"] = execution_time_ms
            node_results[node_id] = result

            # Store successful results in cache (24h TTL)
            if cache_key and "error" not in result:
                try:
                    _cache_set_sync(cache_key, result, ttl=86400)
                except Exception:
                    pass

            # After a Condition node, compute which branch to skip
            if node_type == "Condition" and "error" not in result:
                new_skips = _compute_skipped_nodes(
                    node_id, result["_condition_result"],
                    edge_index, parent_map, node_map,
                )
                skipped_nodes |= new_skips

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
        env_snapshot = _capture_environment()
        run.environment = env_snapshot
        session.commit()

        _publish_progress(run_id, {"type": "run_start", "status": "running"})

        # 2. Execute DAG nodes in topological order
        levels = topological_sort(dag_dict)
        node_map = build_node_map(dag_dict)
        parent_map = build_parent_map(dag_dict)
        edge_index = build_edge_index(dag_dict)
        total_nodes = sum(len(lvl) for lvl in levels)

        node_results: dict[str, dict] = {}
        completed_count = 0
        has_errors = False
        skipped_nodes: set[str] = set()

        for level in levels:
            for node_id in level:
                # Skip nodes on un-taken condition branches
                if node_id in skipped_nodes:
                    node_results[node_id] = {
                        "skipped": True,
                        "node_id": node_id,
                        "reason": "Condition branch not taken",
                    }
                    completed_count += 1
                    logger.info(f"[{run_id}] Skipping node {node_id} (condition branch not taken)")
                    _publish_progress(run_id, {
                        "type": "node_skipped",
                        "node_id": node_id,
                        "progress": completed_count / total_nodes,
                    })
                    continue

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

                    # Propagate uncertainties from parent nodes
                    parent_uncertainties = {}
                    for pid in parents:
                        parent_result = node_results.get(pid, {})
                        if "_uncertainties" in parent_result:
                            parent_uncertainties[pid] = parent_result["_uncertainties"]
                        # Also collect any error/uncertainty columns from data
                        parent_data = parent_result.get("data", {})
                        for key in parent_data:
                            if key.endswith("_err") or key.endswith("_error") or key.endswith("_uncertainty"):
                                parent_uncertainties.setdefault("_error_columns", {})[key] = True
                    if parent_uncertainties:
                        input_data["_parent_uncertainties"] = parent_uncertainties
                else:
                    input_data = {"fits_path": input_data_id, "path": input_data_id, "input_data_id": input_data_id}

                if node_type == "LoadData" and "fits_path" not in params:
                    params["fits_path"] = input_data.get("fits_path", input_data_id)
                if node_type == "ImportWorkspace" and "path" not in params:
                    params["path"] = input_data.get("path", input_data_id)

                # -- Node-level cache: skip execution if result already cached --
                # Build a cache key from node type, params, and parent output
                # checksums.  Set params["force_rerun"] = True to bypass cache.
                cache_key = _build_node_cache_key(node_type, params, parents, node_results)

                if cache_key and not params.get("force_rerun"):
                    try:
                        cached = _cache_get_sync(cache_key)
                        if cached:
                            logger.info(f"[{run_id}] Node {node_id} cache hit")
                            cached["_cached"] = True
                            node_results[node_id] = cached
                            completed_count += 1

                            # Handle Condition nodes even on cache hit
                            if node_type == "Condition" and "error" not in cached:
                                new_skips = _compute_skipped_nodes(
                                    node_id, cached["_condition_result"],
                                    edge_index, parent_map, node_map,
                                )
                                skipped_nodes |= new_skips

                            # Store per-node result in DB for cached hit
                            run_result = RunResult(
                                run_id=uuid.UUID(run_id),
                                node_id=node_id,
                                output_path=cached.get("output_path"),
                                logs=json.dumps(cached) if cached else None,
                                input_hash=cached.get("_input_hash", ""),
                                output_checksum=cached.get("_output_checksum", ""),
                                execution_time_ms=0,
                            )
                            session.add(run_result)
                            session.commit()

                            _publish_progress(run_id, {
                                "type": "node_complete",
                                "node_id": node_id,
                                "progress": completed_count / total_nodes,
                                "cached": True,
                            })
                            continue
                    except Exception:
                        pass  # cache miss or error — proceed with normal execution
                # -- End cache lookup -----------------------------------------------

                # Notify node start
                _publish_progress(run_id, {
                    "type": "node_start",
                    "node_id": node_id,
                    "node_type": node_type,
                })

                logger.info(f"[{run_id}] Running node {node_id} ({node_type})")
                input_hash = hashlib.sha256(
                    json.dumps(params, sort_keys=True, default=str).encode()
                ).hexdigest()
                t0 = time.monotonic()
                try:
                    result = node_fn(input_data, params)
                except Exception as e:
                    logger.error(f"[{run_id}] Node {node_id} failed: {e}")
                    result = {"error": str(e), "node_id": node_id}
                    has_errors = True
                execution_time_ms = int((time.monotonic() - t0) * 1000)
                output_checksum = hashlib.sha256(
                    json.dumps(result, sort_keys=True, default=str).encode()
                ).hexdigest()

                result["_input_hash"] = input_hash
                result["_output_checksum"] = output_checksum
                result["_execution_time_ms"] = execution_time_ms
                node_results[node_id] = result
                completed_count += 1

                # Store successful results in cache (24h TTL)
                if cache_key and "error" not in result:
                    try:
                        _cache_set_sync(cache_key, result, ttl=86400)
                    except Exception:
                        pass

                # After a Condition node, compute which branch to skip
                if node_type == "Condition" and "error" not in result:
                    new_skips = _compute_skipped_nodes(
                        node_id, result["_condition_result"],
                        edge_index, parent_map, node_map,
                    )
                    skipped_nodes |= new_skips

                # Store per-node result
                run_result = RunResult(
                    run_id=uuid.UUID(run_id),
                    node_id=node_id,
                    output_path=result.get("output_path"),
                    logs=json.dumps(result) if result else None,
                    input_hash=input_hash,
                    output_checksum=output_checksum,
                    execution_time_ms=execution_time_ms,
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

        # 3. Store full results to DB; trim only for API responses
        final_status = "completed" if not has_errors else "failed"
        api_results = _trim_for_api(node_results)

        run.status = final_status
        run.results = node_results
        run.completed_at = datetime.now(timezone.utc)
        session.commit()

        # 4. Notify completion (trimmed for WebSocket)
        _publish_progress(run_id, {
            "type": "run_complete",
            "status": final_status,
            "results": api_results,
        })

        logger.info(f"[{run_id}] Pipeline {final_status}")
        return {"run_id": run_id, "status": final_status, "results": api_results}

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


def _trim_for_api(node_results: dict) -> dict:
    """Trim large arrays for API/WebSocket responses (not for DB storage)."""
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

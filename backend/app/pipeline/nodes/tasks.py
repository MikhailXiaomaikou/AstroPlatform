"""Celery task wrapper for pipeline nodes."""

import json
import logging
import uuid

from celery_worker import celery_app
from app.pipeline.nodes import registry

logger = logging.getLogger(__name__)


# In-memory result store (per-worker). Production should use Redis/DB.
_node_results: dict[str, dict] = {}


def _result_key(run_id: str, node_id: str) -> str:
    return f"{run_id}:{node_id}"


@celery_app.task(bind=True, name="pipeline.run_node")
def run_node(
    self,
    *args,
    run_id: str,
    node_id: str,
    node_type: str,
    params: dict,
    parent_ids: list[str],
    input_data_id: str,
):
    """Execute a single pipeline node.

    Gathers outputs from parent nodes, runs the node function, stores the result.
    """
    logger.info(f"[{run_id}] Running node {node_id} (type={node_type})")

    node_fn = registry.get(node_type)
    if node_fn is None:
        raise ValueError(f"Unknown node type: {node_type}. Available: {list(registry.keys())}")

    # Gather input: merge parent outputs or use initial input_data_id
    if parent_ids:
        input_data = {}
        for pid in parent_ids:
            parent_result = _node_results.get(_result_key(run_id, pid))
            if parent_result:
                input_data.update(parent_result)
    else:
        # Root node — inject input_data_id as fits_path
        input_data = {"fits_path": input_data_id}

    # If this is a LoadData node, pass fits_path through params
    if node_type == "LoadData" and "fits_path" not in params:
        params["fits_path"] = input_data.get("fits_path", input_data_id)

    try:
        result = node_fn(input_data, params)
    except Exception as e:
        logger.error(f"[{run_id}] Node {node_id} failed: {e}")
        result = {"error": str(e), "node_id": node_id}

    _node_results[_result_key(run_id, node_id)] = result
    logger.info(f"[{run_id}] Node {node_id} complete")

    return {"run_id": run_id, "node_id": node_id, "status": "done"}

"""DAG validation before pipeline execution."""
from typing import Any

class DAGValidationError(Exception):
    pass

def validate_dag(dag: dict) -> list[str]:
    """Validate a pipeline DAG. Returns list of warnings. Raises DAGValidationError for fatal issues."""
    errors = []
    warnings = []

    nodes = dag.get("nodes", [])
    edges = dag.get("edges", [])

    if not nodes:
        raise DAGValidationError("Pipeline has no nodes")

    # Check for duplicate node IDs
    node_ids = [n["id"] for n in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise DAGValidationError("Duplicate node IDs found")

    node_id_set = set(node_ids)

    # Check edges reference valid nodes
    for edge in edges:
        if edge["source"] not in node_id_set:
            raise DAGValidationError(f"Edge references unknown source node: {edge['source']}")
        if edge["target"] not in node_id_set:
            raise DAGValidationError(f"Edge references unknown target node: {edge['target']}")

    # Check for cycles (topological sort)
    adj = {nid: [] for nid in node_ids}
    in_degree = {nid: 0 for nid in node_ids}
    for edge in edges:
        adj[edge["source"]].append(edge["target"])
        in_degree[edge["target"]] += 1

    queue = [nid for nid in node_ids if in_degree[nid] == 0]
    visited = 0
    while queue:
        node = queue.pop(0)
        visited += 1
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited != len(node_ids):
        raise DAGValidationError("Pipeline contains a cycle")

    # Check node types are valid
    from app.pipeline.nodes import registry
    valid_types = set(registry.keys())
    for node in nodes:
        node_type = node.get("type") or (node.get("data", {}).get("nodeType"))
        if node_type and node_type not in valid_types:
            warnings.append(f"Unknown node type: {node_type}")

    # Warn about disconnected nodes
    connected = set()
    for edge in edges:
        connected.add(edge["source"])
        connected.add(edge["target"])
    disconnected = node_id_set - connected
    if disconnected and len(nodes) > 1:
        warnings.append(f"Disconnected nodes: {', '.join(disconnected)}")

    return warnings

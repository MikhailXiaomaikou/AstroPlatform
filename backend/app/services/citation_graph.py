"""Citation graph builder — constructs a network of references and citations via NASA ADS."""

import os
import logging

import httpx

logger = logging.getLogger(__name__)

ADS_API_KEY = os.getenv("ADS_API_KEY", "")
ADS_BASE_URL = "https://api.adsabs.harvard.edu/v1/search/query"
MAX_NODES = 100


def _format_author(authors: list[str] | None) -> str:
    """Return 'First et al.' or single author name."""
    if not authors:
        return "Unknown"
    if len(authors) == 1:
        return authors[0]
    return f"{authors[0]} et al."


def _query_ads(query: str, rows: int = 50) -> list[dict]:
    """Run a single ADS query, return list of doc dicts."""
    if not ADS_API_KEY:
        return []

    headers = {"Authorization": f"Bearer {ADS_API_KEY}"}
    params = {
        "q": query,
        "fl": "bibcode,title,author,year,citation_count",
        "rows": min(rows, 200),
    }
    try:
        resp = httpx.get(ADS_BASE_URL, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        return resp.json().get("response", {}).get("docs", [])
    except Exception as e:
        logger.warning("ADS citation graph query failed (%s): %s", query[:60], e)
        return []


def _doc_to_node(doc: dict, in_original_set: bool) -> dict:
    """Convert an ADS doc to a graph node."""
    return {
        "id": doc.get("bibcode", ""),
        "title": (doc.get("title") or ["Untitled"])[0],
        "authors": _format_author(doc.get("author")),
        "year": doc.get("year", 0),
        "citations": doc.get("citation_count") or 0,
        "in_original_set": in_original_set,
    }


async def build_citation_graph(bibcodes: list[str], depth: int = 1) -> dict:
    """Build a citation graph for a set of bibcodes.

    Returns a graph dict with nodes, edges, and stats.
    If ADS_API_KEY is not set, returns an empty graph with an info message.
    """
    if not ADS_API_KEY:
        return {
            "nodes": [],
            "edges": [],
            "stats": {"total_nodes": 0, "total_edges": 0},
            "info": "ADS_API_KEY not configured. Set ADS_API_KEY environment variable to enable citation graph.",
        }

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    seen_edges: set[tuple[str, str]] = set()

    # Add seed bibcodes as nodes first (fetch their metadata)
    for bib in bibcodes:
        if len(nodes) >= MAX_NODES:
            break
        # Fetch the paper's own metadata
        docs = _query_ads(f"bibcode:{bib}", rows=1)
        if docs:
            nodes[bib] = _doc_to_node(docs[0], in_original_set=True)
        else:
            # Minimal placeholder if not found
            nodes[bib] = {
                "id": bib,
                "title": bib,
                "authors": "Unknown",
                "year": 0,
                "citations": 0,
                "in_original_set": True,
            }

    # BFS expansion up to requested depth
    current_layer = list(bibcodes)
    for _d in range(depth):
        next_layer: list[str] = []

        for bib in current_layer:
            if len(nodes) >= MAX_NODES:
                break

            # Fetch references (papers this paper cites)
            ref_docs = _query_ads(f"references(bibcode:{bib})", rows=50)
            for doc in ref_docs:
                if len(nodes) >= MAX_NODES:
                    break
                ref_bib = doc.get("bibcode", "")
                if not ref_bib:
                    continue
                if ref_bib not in nodes:
                    nodes[ref_bib] = _doc_to_node(doc, in_original_set=False)
                    next_layer.append(ref_bib)
                # Edge: bib cites ref_bib
                edge_key = (bib, ref_bib)
                if edge_key not in seen_edges:
                    edges.append({"source": bib, "target": ref_bib, "type": "cites"})
                    seen_edges.add(edge_key)

            if len(nodes) >= MAX_NODES:
                break

            # Fetch citations (papers that cite this paper)
            cit_docs = _query_ads(f"citations(bibcode:{bib})", rows=50)
            for doc in cit_docs:
                if len(nodes) >= MAX_NODES:
                    break
                cit_bib = doc.get("bibcode", "")
                if not cit_bib:
                    continue
                if cit_bib not in nodes:
                    nodes[cit_bib] = _doc_to_node(doc, in_original_set=False)
                    next_layer.append(cit_bib)
                # Edge: cit_bib cites bib
                edge_key = (cit_bib, bib)
                if edge_key not in seen_edges:
                    edges.append({"source": cit_bib, "target": bib, "type": "cites"})
                    seen_edges.add(edge_key)

        current_layer = next_layer

    node_list = list(nodes.values())
    return {
        "nodes": node_list,
        "edges": edges,
        "stats": {"total_nodes": len(node_list), "total_edges": len(edges)},
    }

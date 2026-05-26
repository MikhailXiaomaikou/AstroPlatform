"""Async anomaly scanning service for query result sets.

Wraps the core anomaly detection pipeline with an async-compatible
interface suitable for use from FastAPI endpoints.

Status (2026-05-26): dormant scaffold — not yet wired to any production
path. `api/anomalies` serves feed/stats/dismiss but does not call this
scanner; only the Week 3 integration test exercises it. Kept pending
endpoint integration; do not delete (per the paper_tool dead-code audit
precedent of keeping unwired-but-tested scaffolds).
"""

import logging
from typing import Optional

import pandas as pd

from app.services.anomaly_detection import detect_anomalies

logger = logging.getLogger(__name__)


async def scan_results_for_anomalies(
    results: list[dict],
    user_id: Optional[str] = None,
) -> dict:
    """Scan a list of result dicts for statistical anomalies.

    Parameters
    ----------
    results : list[dict]
        Query results, where each dict represents one astronomical object.
    user_id : str or None
        Optional user identifier for logging/auditing.

    Returns
    -------
    dict
        On success::

            {
                "anomalies": [
                    {<row dict>, "anomaly_score": float, "anomaly_methods": [...], ...},
                    ...
                ],
                "total_scanned": int,
                "anomaly_count": int,
            }

        If the dataset is too small::

            {"skipped": True, "reason": "insufficient data"}
    """
    if len(results) < 50:
        logger.info(
            "Skipping anomaly scan: only %d rows (need >= 50). user_id=%s",
            len(results),
            user_id,
        )
        return {"skipped": True, "reason": "insufficient data"}

    logger.info(
        "Starting anomaly scan on %d results. user_id=%s",
        len(results),
        user_id,
    )

    df = pd.DataFrame(results)

    # Run the ensemble detector with automatic feature selection
    df_result = detect_anomalies(df, features=None, methods="all")

    # Extract anomalous rows as list of dicts
    anomaly_mask = df_result["is_anomaly"].astype(bool)
    anomaly_df = df_result[anomaly_mask]

    anomalies = []
    for _, row in anomaly_df.iterrows():
        row_dict = {}
        for col, val in row.items():
            # Convert numpy types to native Python for JSON serialisation
            if isinstance(val, (pd.Timestamp,)):
                row_dict[col] = val.isoformat()
            elif hasattr(val, "item"):
                row_dict[col] = val.item()
            elif isinstance(val, float) and (val != val):
                row_dict[col] = None
            else:
                row_dict[col] = val
        anomalies.append(row_dict)

    anomaly_count = len(anomalies)
    total_scanned = len(df_result)

    logger.info(
        "Anomaly scan complete: %d anomalies / %d total. user_id=%s",
        anomaly_count,
        total_scanned,
        user_id,
    )

    return {
        "anomalies": anomalies,
        "total_scanned": total_scanned,
        "anomaly_count": anomaly_count,
    }

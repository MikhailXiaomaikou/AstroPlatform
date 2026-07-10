"""Database model package.

Import durable research records here so metadata-based test/bootstrap paths
see the tables even when an API module has not imported them yet.
"""

from app.models.research_records import ProvenanceRecord, ResearchJob

__all__ = ["ProvenanceRecord", "ResearchJob"]

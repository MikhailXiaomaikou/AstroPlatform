"""Database model package.

Import durable research records here so metadata-based test/bootstrap paths
see the tables even when an API module has not imported them yet.
"""

from app.models.claim_audit_records import (
    AccountDeletionTombstone,
    ArtifactCleanupQueue,
    ClaimAudit,
    EvidencePack,
    Invitation,
    PrivacyPreference,
)
from app.models.research_records import ProvenanceRecord, ResearchJob
from app.models.worker_records import (
    ScienceExecutionAttempt,
    WorkerArtifactIssuance,
    WorkerEnrollmentToken,
    WorkerNode,
)
from app.models.workspace_records import (
    ClaimAuditReview,
    ResearchWorkspace,
    SourceDocument,
    SourceExtraction,
)

__all__ = [
    "AccountDeletionTombstone",
    "ArtifactCleanupQueue",
    "ClaimAudit",
    "EvidencePack",
    "Invitation",
    "PrivacyPreference",
    "ProvenanceRecord",
    "ResearchJob",
    "ResearchWorkspace",
    "SourceDocument",
    "SourceExtraction",
    "ClaimAuditReview",
    "WorkerEnrollmentToken",
    "WorkerNode",
    "ScienceExecutionAttempt",
    "WorkerArtifactIssuance",
]

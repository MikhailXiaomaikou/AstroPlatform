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
from app.models.foundry_records import (
    CapabilityRequest,
    FoundryCandidate,
    FoundryCandidateEvent,
    FoundryCandidateVersion,
    FoundryDemoRun,
    FoundryFormalBuildAttestation,
    FoundryReview,
    FoundryValidationRun,
    WorkflowRegistryEntry,
    WorkflowRegistryRelease,
    WorkflowRegistryReleaseImport,
)
from app.models.foundry_materialization_records import (
    FoundryMaterializationAttestation,
    FoundryMaterializationReceipt,
)
from app.models.foundry_activation_records import (
    WorkflowRegistryActivationReceipt,
)
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
    "CapabilityRequest",
    "FoundryCandidate",
    "FoundryCandidateEvent",
    "FoundryCandidateVersion",
    "FoundryDemoRun",
    "FoundryFormalBuildAttestation",
    "FoundryReview",
    "FoundryValidationRun",
    "WorkflowRegistryEntry",
    "WorkflowRegistryRelease",
    "WorkflowRegistryReleaseImport",
    "FoundryMaterializationAttestation",
    "FoundryMaterializationReceipt",
    "WorkflowRegistryActivationReceipt",
    "ResearchWorkspace",
    "SourceDocument",
    "SourceExtraction",
    "ClaimAuditReview",
    "WorkerEnrollmentToken",
    "WorkerNode",
    "ScienceExecutionAttempt",
    "WorkerArtifactIssuance",
]

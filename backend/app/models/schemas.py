import base64
import hashlib
import json
import uuid
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, TypeDecorator, UniqueConstraint, func, text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


# ── Portable types (work with both SQLite and PostgreSQL) ──

class JSONType(TypeDecorator):
    """Stores JSON as TEXT for SQLite, uses native JSONB on PostgreSQL."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            return json.dumps(value)
        return None

    def process_result_value(self, value, dialect):
        if value is not None:
            return json.loads(value)
        return None

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import JSONB
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())


def _get_fernet():
    from app.config import settings
    # Derive a valid Fernet key from arbitrary string
    key = hashlib.sha256(settings.fernet_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


class EncryptedJSONType(TypeDecorator):
    """Stores JSON encrypted with Fernet.

    Legacy plaintext rows are still readable for migration compatibility.
    Any later write through this type re-encrypts the value with Fernet.
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            plaintext = json.dumps(value).encode("utf-8")
            return _get_fernet().encrypt(plaintext).decode("utf-8")
        return None

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        # PostgreSQL JSONB can contain legacy plaintext objects written before
        # this column was encrypted.  Keep those rows readable; the next
        # settings update writes them back as a Fernet-encrypted JSON string.
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            return None
        try:
            decrypted = _get_fernet().decrypt(value.encode("utf-8"))
            return json.loads(decrypted)
        except InvalidToken:
            try:
                # Migration compatibility: old rows were stored as plaintext
                # JSON. Keep them readable so BYOK settings do not disappear on
                # deploy; settings updates will write them back encrypted.
                return json.loads(value)
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
        except Exception:
            return None

    def load_dialect_impl(self, dialect):
        # The production schema historically created users.api_keys as JSONB.
        # Keeping a Text implementation on PostgreSQL makes asyncpg bind the
        # encrypted token as VARCHAR, which PostgreSQL correctly rejects for a
        # JSONB column.  A JSONB scalar string preserves the ciphertext while
        # using the database column's native bind/result processors.
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import JSONB

            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())


class UUIDType(TypeDecorator):
    """Stores UUID as String(36) for SQLite, uses native UUID on PostgreSQL."""
    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            return str(value)
        return None

    def process_result_value(self, value, dialect):
        if value is not None:
            return uuid.UUID(value) if not isinstance(value, uuid.UUID) else value
        return None

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import UUID
            return dialect.type_descriptor(UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))


# ── Models ──

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    subscription_tier: Mapped[str] = mapped_column(String(50), default="solo")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255))
    anthropic_api_key: Mapped[str | None] = mapped_column(Text)  # legacy, kept for migration compat
    api_keys: Mapped[dict | None] = mapped_column(EncryptedJSONType())  # {"anthropic": "sk-...", "openai": "sk-...", ...}
    google_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(String(255))
    # Authentication checks this value on every request.  Account deletion
    # therefore revokes an existing JWT immediately, before the asynchronous
    # data-erasure task has finished walking the user's records.
    account_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE", server_default="ACTIVE", index=True
    )
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DataFile(Base):
    __tablename__ = "data_files"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    object_id: Mapped[str] = mapped_column(String(255), nullable=False)
    fits_path: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONType())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_datafile_source", "source"),
        Index("idx_datafile_object_id", "object_id"),
        Index("idx_datafile_user_source", "user_id", "source"),
    )


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    dag: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    results: Mapped[dict | None] = mapped_column(JSONType())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    environment: Mapped[dict | None] = mapped_column(JSONType(), nullable=True)

    __table_args__ = (
        Index("idx_pipelinerun_status", "status"),
        Index("idx_pipelinerun_user_status", "user_id", "status"),
    )


class RunResult(Base):
    __tablename__ = "run_results"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("pipeline_runs.id"), nullable=False)
    node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    output_path: Mapped[str | None] = mapped_column(Text)
    logs: Mapped[str | None] = mapped_column(Text)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PipelineTemplateDB(Base):
    __tablename__ = "pipeline_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    dag: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    is_builtin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DataTag(Base):
    __tablename__ = "data_tags"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    data_file_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("data_files.id"), nullable=False)
    tag: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DataNote(Base):
    __tablename__ = "data_notes"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    data_file_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("data_files.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineVersion(Base):
    __tablename__ = "pipeline_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("pipeline_templates.id"), nullable=False)
    version: Mapped[int] = mapped_column(default=1)
    dag: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TeamMember(Base):
    __tablename__ = "team_members"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    member_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SharedPipeline(Base):
    __tablename__ = "shared_pipelines"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("pipeline_templates.id"), nullable=False)
    shared_by: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    shared_with: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    permission: Mapped[str] = mapped_column(String(20), default="view")  # "view" or "edit"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineComment(Base):
    __tablename__ = "pipeline_comments"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("pipeline_templates.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    parent_comment_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType(), ForeignKey("pipeline_comments.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SharedDataset(Base):
    __tablename__ = "shared_datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    data_file_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("data_files.id"), nullable=False)
    shared_by: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    shared_with: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SetupKey(Base):
    __tablename__ = "setup_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(255), default="")  # description like "beta-tester-01"
    used_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType(), ForeignKey("users.id"))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Friendship(Base):
    __tablename__ = "friendships"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    from_user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    to_user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, accepted, rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SearchHistory(Base):
    __tablename__ = "search_history"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[str] = mapped_column(String(500), default="")
    result_count: Mapped[int] = mapped_column(default=0)
    params: Mapped[dict | None] = mapped_column(JSONType())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SavedObject(Base):
    """User's bookmarked/favorited astronomical objects."""
    __tablename__ = "saved_objects"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ra: Mapped[float] = mapped_column(default=0.0)
    dec: Mapped[float] = mapped_column(default=0.0)
    object_type: Mapped[str] = mapped_column(String(100), default="")
    source: Mapped[str] = mapped_column(String(50), default="")
    redshift: Mapped[float | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    project: Mapped[str] = mapped_column(String(255), default="Default")
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONType())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("idx_chatsession_user", "user_id"),
        Index("idx_chatsession_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("research_workspaces.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255), default="New Chat")
    messages: Mapped[list] = mapped_column(JSONType(), default=list)  # [{role, content, actions}]
    # R7: audit log of thinking-stream events (agent_text / tool_call /
    # tool_result) captured during every agent run.  Lets us later answer
    # "why did the AI fabricate here?" with the exact prompt → tool-call
    # chain that produced the reply.  Nullable because legacy rows
    # predate the column.
    audit_log: Mapped[list | None] = mapped_column(JSONType(), nullable=True, default=None)
    # P1.3.b (2026-05-22): agent-loop status surfaces here so a SSE drop
    # can resume the in-flight loop instead of starting a new one. Values:
    # 'idle' (no loop active), 'running' (an agent loop is producing this
    # session right now), 'suspended' (loop ended early — restart via
    # resume_from_session=True). ``current_run_id`` is the most recent run id
    # so the frontend / api can look up workflow_checkpoint state.
    agent_status: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    current_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UserEvent(Base):
    __tablename__ = "user_events"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType(), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    event_data: Mapped[dict | None] = mapped_column(JSONType())
    page: Mapped[str | None] = mapped_column(String(100), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index("idx_user_event_type_time", "user_id", "event_type", "timestamp"),
    )


class PaperDraft(Base):
    __tablename__ = "paper_drafts"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("chat_sessions.id"), nullable=False, index=True)
    journal_format: Mapped[str] = mapped_column(String(50), default="aastex")
    paper_json: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    latex_source: Mapped[str] = mapped_column(Text, nullable=False)
    bibtex: Mapped[str] = mapped_column(Text, nullable=False, default="")
    validation: Mapped[dict | None] = mapped_column(JSONType())
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    public_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SharedSession(Base):
    __tablename__ = "shared_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("chat_sessions.id"), nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False, index=True)
    share_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    access_level: Mapped[str] = mapped_column(String(20), default="view")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SessionFork(Base):
    __tablename__ = "session_forks"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("chat_sessions.id"), nullable=False, unique=True, index=True)
    forked_from: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("chat_sessions.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SessionComment(Base):
    __tablename__ = "session_comments"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("chat_sessions.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(50), default="general")
    target_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType(), ForeignKey("session_comments.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SessionSnapshot(Base):
    __tablename__ = "session_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("chat_sessions.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    snapshot_data: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserResearchProfile(Base):
    __tablename__ = "user_research_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False, unique=True, index=True)
    frequently_queried_objects: Mapped[list | None] = mapped_column(JSONType(), default=list)
    preferred_databases: Mapped[list | None] = mapped_column(JSONType(), default=list)
    preferred_analysis_methods: Mapped[list | None] = mapped_column(JSONType(), default=list)
    research_interests: Mapped[list | None] = mapped_column(JSONType(), default=list)
    expertise_level: Mapped[str] = mapped_column(String(30), default="beginner")
    past_hypotheses: Mapped[list | None] = mapped_column(JSONType(), default=list)
    preferred_plotting_style: Mapped[dict | None] = mapped_column(JSONType(), default=dict)
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SessionEmbedding(Base):
    __tablename__ = "session_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("chat_sessions.id"), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list | None] = mapped_column(JSONType(), default=list)
    key_objects: Mapped[list | None] = mapped_column(JSONType(), default=list)
    key_methods: Mapped[list | None] = mapped_column(JSONType(), default=list)
    key_findings: Mapped[list | None] = mapped_column(JSONType(), default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InferenceLog(Base):
    __tablename__ = "inference_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    backend_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    model_profile: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    fallback_from: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class SharedResult(Base):
    """Search results shared with a team (by owner_id)."""
    __tablename__ = "shared_results"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    shared_by: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="")
    objects: Mapped[list] = mapped_column(JSONType(), nullable=False)  # list of object dicts
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SharedNotebook(Base):
    """Chat session notebooks shared with a team."""
    __tablename__ = "shared_notebooks"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    shared_by: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)  # markdown export
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TeamActivity(Base):
    """Activity feed entries for a team."""
    __tablename__ = "team_activities"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)  # "search", "pipeline_run", "shared_results", "shared_notebook"
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScheduledRun(Base):
    __tablename__ = "scheduled_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    dag: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    input_data_id: Mapped[str] = mapped_column(String(500), nullable=False)
    cron_expr: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "0 */6 * * *"
    enabled: Mapped[bool] = mapped_column(default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IsochroneCache(Base):
    __tablename__ = "isochrone_cache"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    log_age: Mapped[float] = mapped_column(Float, nullable=False)
    metallicity: Mapped[float] = mapped_column(Float, nullable=False)
    photometric_system: Mapped[str] = mapped_column(String(50), nullable=False, default="gaia")
    data: Mapped[dict] = mapped_column(JSONType(), nullable=False)  # DataFrame as list of dicts
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("log_age", "metallicity", "photometric_system", name="uq_isochrone_params"),
    )


class TransientAlert(Base):
    __tablename__ = "transient_alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # "ztf", "tns"
    source_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    ra: Mapped[float] = mapped_column(Float, nullable=False)
    dec: Mapped[float] = mapped_column(Float, nullable=False)
    discovery_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    magnitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    mag_band: Mapped[str | None] = mapped_column(String(10), nullable=True)
    classification: Mapped[str | None] = mapped_column(String(100), nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    redshift: Mapped[float | None] = mapped_column(Float, nullable=True)
    host_galaxy: Mapped[str | None] = mapped_column(String(200), nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSONType(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Trending visibility toggle (admin controls which trending data is public) ──
# 3 rows; key in {'objects', 'sources', 'delta'}, value = is_public bool
class TrendingVisibility(Base):
    __tablename__ = "trending_visibility"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Public comment section (Landing page) ──
# No login required; visitors submit with just a nickname and content; admins delete via X-Admin-Secret.
class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    author_name: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # is_visible defaults to True (visible immediately on post); admins set it
    # to False for soft-delete (list endpoint filters these out).
    # server_default uses sa_text("true") for compatibility with both PostgreSQL
    # (TRUE/FALSE) and SQLite (which accepts "true" as 1). Using "1" on PG
    # was interpreted as a string — an invalid boolean — and prevented table creation.
    is_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sa_text("true"))
    # Record client IP for spam / rate-limit investigation; not exposed to the frontend
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_comments_visible_created", "is_visible", "created_at"),
    )

import json
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, TypeDecorator, UniqueConstraint, func
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
    api_keys: Mapped[dict | None] = mapped_column(JSONType())  # {"anthropic": "sk-...", "openai": "sk-...", ...}
    google_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(String(255))
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


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    dag: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    results: Mapped[dict | None] = mapped_column(JSONType())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunResult(Base):
    __tablename__ = "run_results"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("pipeline_runs.id"), nullable=False)
    node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    output_path: Mapped[str | None] = mapped_column(Text)
    logs: Mapped[str | None] = mapped_column(Text)


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

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="New Chat")
    messages: Mapped[list] = mapped_column(JSONType(), default=list)  # [{role, content, actions}]
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

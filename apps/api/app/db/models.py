"""Database schema.

Note what is NOT here: no plaintext PII columns. Extraction results live in
`extractions.data_encrypted` as an AES-GCM blob. The only personal data in
queryable form is what the application genuinely needs to index on, which
turns out to be nothing.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True,
                                    default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now)


class Consent(Base):
    """Consent is recorded per version.

    When the consent text changes -- for example when a new third-party
    processor is added -- users must be asked again. Storing a hash of the
    exact text they agreed to is what makes that auditable.
    """
    __tablename__ = "consents"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True,
                                    default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(32))
    text_hash: Mapped[str] = mapped_column(String(64))
    allows_third_party: Mapped[bool] = mapped_column(Boolean, default=False)
    allows_training_use: Mapped[bool] = mapped_column(Boolean, default=False)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True,
                                    default=_uuid)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    doc_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stages_used: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    llm_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: _now() + timedelta(days=30), index=True)

    uploads = relationship("Upload", back_populates="job",
                           cascade="all, delete-orphan")
    extraction = relationship("Extraction", back_populates="job",
                              uselist=False, cascade="all, delete-orphan")


class Upload(Base):
    __tablename__ = "uploads"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True,
                                    default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    storage_key: Mapped[str] = mapped_column(String(512))
    mime: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    # Images are deleted long before extractions: they are the richest source
    # of personal data and are only needed while the user reviews the result.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: _now() + timedelta(hours=24), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now)
    job = relationship("Job", back_populates="uploads")


class Extraction(Base):
    __tablename__ = "extractions"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True,
                                    default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    data_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    overall_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    needs_review: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    warnings: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    model_versions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now)
    job = relationship("Job", back_populates="extraction")


class Template(Base):
    __tablename__ = "templates"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True,
                                    default=_uuid)
    owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    original_format: Mapped[str] = mapped_column(String(16))
    storage_key: Mapped[str] = mapped_column(String(512))
    original_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    spec: Mapped[dict] = mapped_column(JSONB)
    sanitization_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    conversion_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_formats: Mapped[list] = mapped_column(JSONB, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now)


class GeneratedDocument(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True,
                                    default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    template_id: Mapped[str] = mapped_column(ForeignKey("templates.id"))
    storage_key: Mapped[str] = mapped_column(String(512))
    format: Mapped[str] = mapped_column(String(8))
    missing_fields: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: _now() + timedelta(days=90), index=True)


class LLMUsage(Base):
    """Metrics only.

    Prompt and response CONTENT is deliberately absent: it is the personal data
    itself. Cost attribution does not require keeping it.
    """
    __tablename__ = "llm_usage"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True,
                                    default=_uuid)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    task: Mapped[str] = mapped_column(String(32))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True,
                                    default=_uuid)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_now, index=True)

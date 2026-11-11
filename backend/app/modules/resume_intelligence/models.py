"""Resume + ProfileFact — модели по DATABASE §3.3 (S2.1: схема, без API).

Инварианты: CHECK-evidence для llm_extraction (миграция 0003);
confirmed-факты переживают удаление резюме — repository.delete_resume
переносит resume_id=NULL ДО DELETE (Q-8, порядок операций, не схема).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.core.db import Base


def _uuid7() -> uuid.UUID:
    return uuid7()


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    career_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("career_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_key: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    status: Mapped[str] = mapped_column(
        ENUM(
            "uploaded",
            "parsing",
            "parsed",
            "failed",
            name="resume_status",
            create_type=False,
        ),
        nullable=False,
        server_default="uploaded",
    )  # native PG enum resume_status (миграция 0003)
    parse_error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ProfileFact(Base):
    __tablename__ = "profile_facts"
    __table_args__ = (
        CheckConstraint(
            "source <> 'llm_extraction' OR jsonb_array_length(evidence) > 0",
            name="ck_facts_evidence_required",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    career_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("career_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=True
    )
    fact_type: Mapped[str] = mapped_column(
        ENUM(
            "skill",
            "experience",
            "project",
            "education",
            name="fact_type",
            create_type=False,
        ),
        nullable=False,
    )  # native PG enum fact_type (создаёт миграция 0003)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    source: Mapped[str] = mapped_column(
        ENUM(
            "llm_extraction",
            "user_input",
            "merge",
            name="fact_source",
            create_type=False,
        ),
        nullable=False,
    )  # native PG enum fact_source (миграция 0003)
    status: Mapped[str] = mapped_column(
        ENUM(
            "draft",
            "confirmed",
            "rejected",
            "superseded",
            name="fact_status",
            create_type=False,
        ),
        nullable=False,
        server_default="draft",
    )  # native PG enum fact_status (миграция 0003)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # FK на profile_facts — см. миграцию
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

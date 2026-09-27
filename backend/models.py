"""SQLAlchemy ORM models.

CaseReview is the audit-of-record for every adjudication request: it always
stores the deterministic prototype's suggestion *and* keeps a separate,
initially-empty slot for the human reviewer's decision. Nothing in this
schema allows a prototype suggestion to silently become an authoritative
outcome — `human_decision` must be explicitly set by an authenticated
reviewer via the review-workflow endpoints.
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="reviewer")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    case_reviews_created: Mapped[list["CaseReview"]] = relationship(
        back_populates="created_by", foreign_keys="CaseReview.created_by_id"
    )
    case_reviews_decided: Mapped[list["CaseReview"]] = relationship(
        back_populates="reviewed_by", foreign_keys="CaseReview.reviewed_by_id"
    )


class CaseReview(Base):
    __tablename__ = "case_reviews"
    __table_args__ = (Index("ix_case_reviews_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # The submitted InferenceCase, stored verbatim for audit/reproducibility.
    inference_case: Mapped[dict] = mapped_column(JSON)

    # The deterministic prototype's suggestion — advisory only.
    suggested_decision: Mapped[str] = mapped_column(String(20))
    suggested_rationale: Mapped[str] = mapped_column(Text)
    suggested_confidence: Mapped[float] = mapped_column()
    findings: Mapped[list] = mapped_column(JSON)
    limitations: Mapped[list] = mapped_column(JSON)

    # Optional secondary signal from the LLM/RAG engine (backend/llm_engine.py).
    # Null when the engine was not configured at submission time. Always
    # advisory/comparison-only, like `suggested_decision` above.
    llm_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="pending_review")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Populated only once a human reviewer records a final decision.
    human_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    human_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_by: Mapped["User"] = relationship(
        back_populates="case_reviews_created", foreign_keys=[created_by_id]
    )
    reviewed_by: Mapped["User | None"] = relationship(
        back_populates="case_reviews_decided", foreign_keys=[reviewed_by_id]
    )


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    total_cases: Mapped[int] = mapped_column()
    result: Mapped[dict] = mapped_column(JSON)

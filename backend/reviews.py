"""Human-review workflow: persisted, audited case reviews.

Every adjudication run through this workflow is stored with the prototype's
suggestion kept separate from the authoritative human decision. A basic
maker-checker control is enforced: the reviewer who records the final
decision must be a different account than whoever created the case (unless
they are an admin), consistent with common segregation-of-duties controls
in claims-review environments. Decisions are immutable once recorded; an
admin can explicitly reopen a case if a correction is genuinely needed, and
this project keeps the reopened record's original decision fields
overwritten (a full change-history table is a natural next step, noted in
the README, but is out of scope for this pass).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agents import adjudicate
from backend.auth import get_current_user, require_role
from backend.db import get_db
from backend.llm_engine import (
    LLMNotConfigured,
    llm_configured,
    run_llm_rag_adjudication,
)
from backend.models import CaseReview, User
from backend.schemas import (
    CaseReviewDetail,
    CaseReviewListResponse,
    CaseReviewSummary,
    InferenceCase,
    LLMEngineResult,
    RecordDecisionRequest,
)

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])


def _to_summary(review: CaseReview) -> CaseReviewSummary:
    return CaseReviewSummary(
        id=review.id,
        case_id=review.case_id,
        suggested_decision=review.suggested_decision,
        suggested_confidence=review.suggested_confidence,
        llm_decision=(review.llm_result or {}).get("decision"),
        status=review.status,
        created_by=review.created_by.username,
        created_at=review.created_at,
        human_decision=review.human_decision,
        reviewed_by=review.reviewed_by.username if review.reviewed_by else None,
        reviewed_at=review.reviewed_at,
    )


def _to_detail(review: CaseReview) -> CaseReviewDetail:
    return CaseReviewDetail(
        **_to_summary(review).model_dump(),
        inference_case=InferenceCase(**review.inference_case),
        suggested_rationale=review.suggested_rationale,
        findings=review.findings,
        limitations=review.limitations,
        human_notes=review.human_notes,
        llm_result=LLMEngineResult(**review.llm_result) if review.llm_result else None,
    )


@router.post("", response_model=CaseReviewDetail, status_code=status.HTTP_201_CREATED)
async def create_review(
    case: InferenceCase,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CaseReviewDetail:
    result = adjudicate(case)
    llm_result: LLMEngineResult | None = None
    if llm_configured():
        try:
            llm_result = await run_llm_rag_adjudication(case)
        except LLMNotConfigured:
            llm_result = None
    review = CaseReview(
        case_id=case.case_id,
        inference_case=case.model_dump(),
        suggested_decision=result.decision,
        suggested_rationale=result.rationale,
        suggested_confidence=result.confidence,
        findings=[f.model_dump() for f in result.findings],
        limitations=result.limitations,
        llm_result=llm_result.model_dump() if llm_result else None,
        status="pending_review",
        created_by_id=user.id,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return _to_detail(review)


@router.get("", response_model=CaseReviewListResponse)
def list_reviews(
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CaseReviewListResponse:
    query = select(CaseReview)
    if status_filter:
        query = query.where(CaseReview.status == status_filter)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = (
        db.execute(
            query.order_by(CaseReview.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return CaseReviewListResponse(
        items=[_to_summary(r) for r in rows], total=total, page=page, page_size=page_size
    )


@router.get("/{review_id}", response_model=CaseReviewDetail)
def get_review(
    review_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CaseReviewDetail:
    review = db.get(CaseReview, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    return _to_detail(review)


@router.post("/{review_id}/decision", response_model=CaseReviewDetail)
def record_decision(
    review_id: int,
    request: RecordDecisionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CaseReviewDetail:
    review = db.get(CaseReview, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    if review.status == "reviewed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This case already has a recorded human decision. An admin can reopen it if a correction is required.",
        )
    if review.created_by_id == user.id and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Segregation of duties: the case creator cannot also record its human decision. Have another reviewer complete it.",
        )
    review.human_decision = request.human_decision
    review.human_notes = request.human_notes
    review.reviewed_by_id = user.id
    review.reviewed_at = datetime.now(timezone.utc)
    review.status = "reviewed"
    db.commit()
    db.refresh(review)
    return _to_detail(review)


@router.post("/{review_id}/reopen", response_model=CaseReviewDetail)
def reopen_review(
    review_id: int,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> CaseReviewDetail:
    review = db.get(CaseReview, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    review.status = "pending_review"
    review.human_decision = None
    review.human_notes = None
    review.reviewed_by_id = None
    review.reviewed_at = None
    db.commit()
    db.refresh(review)
    return _to_detail(review)

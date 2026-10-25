"""Review workflow: persisted, audited case reviews with STP routing.

Every adjudication submitted here runs through the five-agent pipeline and is
stored with the full agent trace. Agent 5 routes it:

* **Straight-Through Processing** - a draft APPROVE/DENY whose calibrated
  confidence meets its threshold is finalised immediately
  (``status="auto_decided"``, ``decision_source="stp"``), fully audited.
* **Human Review Queue** - everything else is stored as ``pending_review``
  for an authenticated reviewer.

A maker-checker control applies to human decisions: the reviewer who records
the decision must be a different account than the case creator (unless they
are an admin). Recorded and auto-decided outcomes are immutable; an admin can
explicitly reopen a case (which returns it to the human queue) if a correction
is genuinely needed.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.auth import get_current_user, require_role
from backend.config import get_settings
from backend.db import get_db
from backend.llm_engine import (
    LLMNotConfigured,
    llm_configured,
    run_llm_rag_adjudication,
)
from backend.models import CaseReview, User
from backend.pipeline.contracts import PipelineTrace
from backend.pipeline.orchestrator import adjudicate_pipeline_async
from backend.schemas import (
    CaseReviewDetail,
    CaseReviewListResponse,
    CaseReviewSummary,
    InferenceCase,
    LLMEngineResult,
    RecordDecisionRequest,
)

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])
_HUMAN_TO_FINAL = {"APPROVED": "APPROVE", "DENIED": "DENY", "ESCALATED": None}


def _to_summary(review: CaseReview) -> CaseReviewSummary:
    return CaseReviewSummary(
        id=review.id,
        case_id=review.case_id,
        suggested_decision=review.suggested_decision,
        suggested_confidence=review.suggested_confidence,
        llm_decision=(review.llm_result or {}).get("decision"),
        route=review.route,
        status=review.status,
        final_decision=review.final_decision,
        decision_source=review.decision_source,
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
        audit_brief=review.audit_brief,
        pipeline=PipelineTrace(**review.pipeline_trace) if review.pipeline_trace else None,
        llm_result=LLMEngineResult(**review.llm_result) if review.llm_result else None,
    )


@router.post("", response_model=CaseReviewDetail, status_code=status.HTTP_201_CREATED)
async def create_review(
    case: InferenceCase,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CaseReviewDetail:
    llm_enabled = llm_configured()
    result = await adjudicate_pipeline_async(case, llm_assist=llm_enabled and get_settings().pipeline_llm_assist)
    llm_result: LLMEngineResult | None = None
    if llm_enabled:
        try:
            llm_result = await run_llm_rag_adjudication(case)
        except LLMNotConfigured:
            llm_result = None
    straight_through = result.route == "STRAIGHT_THROUGH"
    review = CaseReview(
        case_id=case.case_id,
        inference_case=case.model_dump(),
        suggested_decision=result.draft_decision or result.decision,
        suggested_rationale=result.rationale,
        suggested_confidence=result.confidence,
        findings=[f.model_dump() for f in result.findings],
        limitations=result.limitations,
        llm_result=llm_result.model_dump() if llm_result else None,
        route=result.route,
        audit_brief=result.audit_brief,
        pipeline_trace=result.pipeline.model_dump(mode="json") if result.pipeline else None,
        final_decision=result.decision if straight_through else None,
        decision_source="stp" if straight_through else None,
        status="auto_decided" if straight_through else "pending_review",
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
            query.order_by(CaseReview.created_at.desc(), CaseReview.id.desc())
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
    if review.status == "auto_decided":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This case was finalised by straight-through processing. An admin can reopen it for human review.",
        )
    if review.created_by_id == user.id and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Segregation of duties: the case creator cannot also record its human decision. Have another reviewer complete it.",
        )
    review.human_decision = request.human_decision
    review.human_notes = request.human_notes
    review.final_decision = _HUMAN_TO_FINAL[request.human_decision]
    review.decision_source = "human"
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
    review.final_decision = None
    review.decision_source = None
    db.commit()
    db.refresh(review)
    return _to_detail(review)

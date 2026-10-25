import logging
from collections.abc import Callable
from time import perf_counter

from backend.schemas import (
    AdjudicationResult,
    ClassMetrics,
    Decision,
    EvaluationCaseResult,
    EvaluationComparisonResponse,
    EvaluationRequest,
    EvaluationResponse,
)

DECISIONS: tuple[Decision, ...] = ("APPROVE", "DENY", "HUMAN_REVIEW")
logger = logging.getLogger(__name__)


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def predictor_for(engine: str) -> Callable:
    if engine == "b1_lexical":
        from backend.agents import adjudicate

        return adjudicate
    if engine == "multi_agent_pipeline":
        from backend.pipeline.orchestrator import adjudicate_pipeline

        return adjudicate_pipeline
    raise ValueError(f"Engine {engine!r} has no synchronous predictor.")


def evaluate(
    request: EvaluationRequest,
    predictor: Callable | None = None,
    model_name: str | None = None,
) -> EvaluationResponse:
    predictor = predictor or predictor_for(request.engine)
    confusion = {actual: {predicted: 0 for predicted in DECISIONS} for actual in DECISIONS}
    ground_truth_support = {label: 0 for label in DECISIONS}
    errors: list[str] = []
    case_results: list[EvaluationCaseResult] = []
    latencies: list[float] = []
    evaluated = 0
    false_approvals = 0
    false_denials = 0
    review_count = 0
    stp_count = stp_correct = draft_correct = drafts = 0

    for index, item in enumerate(request.cases):
        ground_truth_support[item.ground_truth] += 1
        try:
            start = perf_counter()
            prediction: AdjudicationResult = predictor(item.inference_case)
            elapsed_ms = (perf_counter() - start) * 1000
            confusion[item.ground_truth][prediction.decision] += 1
            evaluated += 1
            latencies.append(elapsed_ms)
            review_count += prediction.decision == "HUMAN_REVIEW"
            false_approvals += prediction.decision == "APPROVE" and item.ground_truth != "APPROVE"
            false_denials += prediction.decision == "DENY" and item.ground_truth != "DENY"
            route = getattr(prediction, "route", None)
            draft = getattr(prediction, "draft_decision", None)
            if route == "STRAIGHT_THROUGH":
                stp_count += 1
                stp_correct += prediction.decision == item.ground_truth
            if draft is not None:
                drafts += 1
                draft_correct += draft == item.ground_truth
            case_results.append(EvaluationCaseResult(
                case_index=index + 1,
                case_id=item.inference_case.case_id,
                ground_truth=item.ground_truth,
                prediction=prediction.decision,
                status="evaluated",
                latency_ms=elapsed_ms,
                error=None,
                draft_decision=draft,
                route=route,
                confidence=prediction.confidence,
            ))
        except Exception as exc:  # noqa: BLE001 - keep model/plugin failures per-case, not run-fatal.
            error = f"Case {index + 1}: {type(exc).__name__}"
            logger.error("Evaluation case failed: index=%s error_type=%s", index + 1, type(exc).__name__)
            errors.append(error)
            case_results.append(EvaluationCaseResult(
                case_index=index + 1,
                case_id=item.inference_case.case_id,
                ground_truth=item.ground_truth,
                prediction=None,
                status="error",
                latency_ms=None,
                error=error,
            ))

    per_class: dict[str, ClassMetrics] = {}
    for label in DECISIONS:
        true_positive = confusion[label][label]
        predicted_positive = sum(confusion[actual][label] for actual in DECISIONS)
        support = ground_truth_support[label]
        precision = _ratio(true_positive, predicted_positive)
        recall = _ratio(true_positive, support)
        f1 = _ratio(2 * precision * recall, precision + recall)
        per_class[label] = ClassMetrics(
            precision=precision, recall=recall, f1=f1, support=support
        )

    correct = sum(confusion[label][label] for label in DECISIONS)
    total = len(request.cases)
    routed = drafts > 0
    return EvaluationResponse(
        model=model_name or request.engine,
        total_cases=total,
        evaluated_cases=evaluated,
        errored_cases=len(errors),
        accuracy=_ratio(correct, total),
        macro_precision=sum(m.precision for m in per_class.values()) / len(DECISIONS),
        macro_recall=sum(m.recall for m in per_class.values()) / len(DECISIONS),
        macro_f1=sum(m.f1 for m in per_class.values()) / len(DECISIONS),
        false_approval_rate=_ratio(
            false_approvals,
            sum(ground_truth_support[label] for label in DECISIONS if label != "APPROVE"),
        ),
        false_denial_rate=_ratio(
            false_denials,
            sum(ground_truth_support[label] for label in DECISIONS if label != "DENY"),
        ),
        human_review_rate=_ratio(review_count, evaluated),
        average_latency_ms=sum(latencies) / len(latencies) if latencies else 0.0,
        per_class=per_class,
        confusion_matrix=confusion,
        case_results=case_results,
        errors=errors,
        stp_rate=_ratio(stp_count, evaluated) if routed else None,
        stp_accuracy=_ratio(stp_correct, stp_count) if routed and stp_count else None,
        draft_accuracy=_ratio(draft_correct, total) if routed else None,
    )


async def evaluate_llm(request: EvaluationRequest) -> EvaluationResponse:
    """Evaluate the optional LLM/RAG engine (network calls, run sequentially)."""
    from backend.llm_engine import run_llm_rag_adjudication

    outcomes = []
    for item in request.cases:
        started = perf_counter()
        try:
            outcomes.append((await run_llm_rag_adjudication(item.inference_case), (perf_counter() - started) * 1000))
        except Exception as exc:  # noqa: BLE001 - disclosed per case by evaluate().
            outcomes.append((exc, 0.0))
    iterator = iter(outcomes)

    def predictor(case):
        outcome, latency = next(iterator)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome.decision is None:
            raise RuntimeError(outcome.error or "LLM engine returned no decision")
        return AdjudicationResult(
            case_id=case.case_id, decision=outcome.decision, rationale=outcome.rationale,
            confidence=outcome.confidence or 0.0, confidence_note="Self-reported LLM confidence.",
            findings=[], limitations=[], latency_ms=latency,
        )

    return evaluate(request, predictor=predictor, model_name="llm_rag")


def compare(responses: dict[str, EvaluationResponse]) -> EvaluationComparisonResponse:
    names = list(responses)
    disagreements = []
    if names:
        for index, base in enumerate(responses[names[0]].case_results):
            predictions = {name: responses[name].case_results[index].prediction for name in names}
            if len(set(predictions.values())) > 1:
                disagreements.append({
                    "case_index": base.case_index, "case_id": base.case_id,
                    "ground_truth": base.ground_truth, "predictions": predictions,
                })
    return EvaluationComparisonResponse(
        engines=responses,
        disagreements=disagreements,
        notes=[
            "All engines are scored against the same project-authored labels; see data/README.md.",
            ("multi_agent_pipeline accuracy counts calibrated abstentions (routed to human review) as HUMAN_REVIEW "
             "predictions; draft_accuracy scores Agent 4's draft before routing."),
        ],
    )

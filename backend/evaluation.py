import logging
from collections.abc import Callable
from time import perf_counter

from backend.agents import adjudicate
from backend.schemas import (
    ClassMetrics,
    Decision,
    EvaluationCaseResult,
    EvaluationRequest,
    EvaluationResponse,
)

DECISIONS: tuple[Decision, ...] = ("APPROVE", "DENY", "HUMAN_REVIEW")
logger = logging.getLogger(__name__)


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate(
    request: EvaluationRequest,
    predictor: Callable = adjudicate,
) -> EvaluationResponse:
    confusion = {actual: {predicted: 0 for predicted in DECISIONS} for actual in DECISIONS}
    ground_truth_support = {label: 0 for label in DECISIONS}
    errors: list[str] = []
    case_results: list[EvaluationCaseResult] = []
    latencies: list[float] = []
    evaluated = 0
    false_approvals = 0
    false_denials = 0
    review_count = 0

    for index, item in enumerate(request.cases):
        ground_truth_support[item.ground_truth] += 1
        try:
            start = perf_counter()
            prediction = predictor(item.inference_case)
            elapsed_ms = (perf_counter() - start) * 1000
            confusion[item.ground_truth][prediction.decision] += 1
            evaluated += 1
            latencies.append(elapsed_ms)
            review_count += prediction.decision == "HUMAN_REVIEW"
            false_approvals += (
                prediction.decision == "APPROVE" and item.ground_truth != "APPROVE"
            )
            false_denials += (
                prediction.decision == "DENY" and item.ground_truth != "DENY"
            )
            case_results.append(EvaluationCaseResult(
                case_index=index + 1,
                case_id=item.inference_case.case_id,
                ground_truth=item.ground_truth,
                prediction=prediction.decision,
                status="evaluated",
                latency_ms=elapsed_ms,
                error=None,
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
    macro_precision = sum(m.precision for m in per_class.values()) / len(DECISIONS)
    macro_recall = sum(m.recall for m in per_class.values()) / len(DECISIONS)
    macro_f1 = sum(m.f1 for m in per_class.values()) / len(DECISIONS)
    total = len(request.cases)
    return EvaluationResponse(
        model="evidence_reconciliation_prototype",
        total_cases=total,
        evaluated_cases=evaluated,
        errored_cases=len(errors),
        accuracy=_ratio(correct, total),
        macro_precision=macro_precision,
        macro_recall=macro_recall,
        macro_f1=macro_f1,
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
    )

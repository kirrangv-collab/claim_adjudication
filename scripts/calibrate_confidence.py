"""Fit Platt calibration for the pipeline's confidence score (Agent 5).

Runs the deterministic multi-agent pipeline on labelled cases, takes the
confidence model's raw logit for each decisive (APPROVE/DENY) draft and the
binary target "draft decision == ground truth", and fits
``p = sigmoid(a * logit + b)`` by
L2-regularised maximum likelihood shrunk towards the identity map (a=1, b=0),
because the labelled sample is tiny. Reports Brier score, expected
calibration error and leave-one-out log-loss before/after fitting, and writes
``data/calibration/pipeline_calibration.json``.

Run from the project root:
    python scripts/calibrate_confidence.py [--cases path.jsonl] [--l2 1.0]

CAVEAT: with ~20 project-labelled cases this is a demonstration of the
calibration mechanism, not a statistically meaningful calibration. The same
sample is also used for the in-app evaluation, which is disclosed in the
output file and via /api/v1/config.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.pipeline.confidence import CALIBRATION_PATH, confidence_logit
from backend.pipeline.orchestrator import Pipeline
from backend.schemas import EvaluationCase

DEFAULT_CASES = ROOT / "data" / "processed" / "ncd_eye_evaluation_cases.jsonl"
MIN_PER_CLASS = 3


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, x))))


def fit_platt(logits: list[float], targets: list[int], l2: float, iterations: int = 200) -> tuple[float, float]:
    """Newton's method on NLL + l2 * ((a - 1)^2 + b^2)."""
    a, b = 1.0, 0.0
    for _ in range(iterations):
        ga, gb = 2 * l2 * (a - 1), 2 * l2 * b
        haa, hab, hbb = 2 * l2, 0.0, 2 * l2
        for x, y in zip(logits, targets):
            p = _sigmoid(a * x + b)
            ga += (p - y) * x
            gb += p - y
            w = p * (1 - p)
            haa += w * x * x
            hab += w * x
            hbb += w
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) < 1e-9 and abs(db) < 1e-9:
            break
    return a, b


def brier(probs, targets):
    return sum((p - y) ** 2 for p, y in zip(probs, targets)) / len(targets)


def ece(probs, targets, bins: int = 5):
    total = 0.0
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        idx = [j for j, p in enumerate(probs) if lo <= p < hi or (i == bins - 1 and p == 1.0)]
        if idx:
            confidence = sum(probs[j] for j in idx) / len(idx)
            accuracy = sum(targets[j] for j in idx) / len(idx)
            total += len(idx) / len(probs) * abs(confidence - accuracy)
    return total


def log_loss(probs, targets):
    eps = 1e-6
    return -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps)) for p, y in zip(probs, targets)) / len(targets)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--l2", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cases = [EvaluationCase.model_validate_json(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    pipeline = Pipeline()
    logits, targets = [], []
    for item in cases:
        trace = pipeline.run(item.inference_case)
        # The score gates straight-through processing, so it is calibrated on
        # decisive drafts only (HUMAN_REVIEW drafts never go straight through).
        if trace.reconciliation.decision == "HUMAN_REVIEW":
            continue
        logit, _ = confidence_logit(trace.reconciliation)
        logits.append(logit)
        targets.append(int(trace.reconciliation.decision == item.ground_truth))

    a, b = fit_platt(logits, targets, args.l2)
    negatives = len(targets) - sum(targets)
    method = "platt"
    note = None
    if min(negatives, sum(targets)) < MIN_PER_CLASS:
        # Platt scaling is unidentifiable without both outcomes: with no
        # incorrect drafts it would merely flatten the scores. Keep identity.
        a, b, method = 1.0, 0.0, "identity"
        note = (f"Platt fit skipped: {negatives} incorrect and {sum(targets)} correct draft(s); at least "
                f"{MIN_PER_CLASS} of each are required. Raw model scores are reported unchanged.")
    raw = [_sigmoid(x) for x in logits]
    calibrated = [_sigmoid(a * x + b) for x in logits]
    loo = []
    for i in range(len(logits)):
        if method == "identity":
            loo.append(raw[i])
            continue
        la, lb = fit_platt(logits[:i] + logits[i + 1:], targets[:i] + targets[i + 1:], args.l2)
        loo.append(_sigmoid(la * logits[i] + lb))

    metrics = {
        "draft_accuracy": round(sum(targets) / len(targets), 4),
        "brier_raw": round(brier(raw, targets), 4),
        "brier_calibrated": round(brier(calibrated, targets), 4),
        "ece_raw": round(ece(raw, targets), 4),
        "ece_calibrated": round(ece(calibrated, targets), 4),
        "log_loss_raw": round(log_loss(raw, targets), 4),
        "log_loss_leave_one_out": round(log_loss(loo, targets), 4),
    }
    payload = {
        "method": method,
        "a": round(a, 6),
        "b": round(b, 6),
        "n": len(targets),
        "cases_scanned": len(cases),
        "positives": sum(targets),
        "negatives": negatives,
        "l2_towards_identity": args.l2,
        "target": "decisive (APPROVE/DENY) draft equals project-authored ground truth",
        "fitted_on": Path(args.cases).resolve().relative_to(ROOT).as_posix()
        if Path(args.cases).resolve().is_relative_to(ROOT) else str(args.cases),
        "fitted_at": datetime.now(timezone.utc).date().isoformat(),
        "metrics": metrics,
        "caveat": (
            (note + " " if note else "")
            + f"Evaluated on {len(cases)} project-labelled research cases (the same sample used by the in-app "
            "evaluation). Indicative only; not a statistically meaningful calibration."
        ),
    }
    print(json.dumps(payload, indent=2))
    if not args.dry_run:
        CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
        CALIBRATION_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {CALIBRATION_PATH}")


if __name__ == "__main__":
    main()

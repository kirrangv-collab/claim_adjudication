"""Run the guide test-case suite and print a results table.

Usage (from the project root):

    python scripts/run_test_cases.py                      # in-process pipeline (no server needed)
    python scripts/run_test_cases.py --api http://127.0.0.1:8000   # against a running API
    python scripts/run_test_cases.py --only D             # one category (prefix of the id)
    python scripts/run_test_cases.py --verbose            # show rationale + routing reasons
    python scripts/run_test_cases.py --export-eval out.jsonl   # Evaluation-tab JSONL to paste in the UI
    python scripts/run_test_cases.py --show A04           # print a case's inputs to type into the UI

Each result is classified as:

    PASS          final outcome == expected outcome
    SAFE-ABSTAIN  expected APPROVE/DENY; the draft was right but confidence was below
                  the STP threshold, so the case went to the human queue
    CONSERVATIVE  expected APPROVE/DENY; the pipeline escalated (draft HUMAN_REVIEW) —
                  a miss, but a safe one
    FAIL          a wrong automated APPROVE/DENY, or an automated decision where human
                  review was expected

Exit code is 1 if any case FAILs.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CASES_PATH = ROOT / "data" / "test_cases" / "guide_test_cases.jsonl"
MAX_RATE_LIMIT_RETRIES = 3


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def classify(expected: str, final: str, draft: str | None) -> str:
    if final == expected:
        return "PASS"
    if final != "HUMAN_REVIEW":
        return "FAIL"
    if draft == expected:
        return "SAFE-ABSTAIN"
    return "CONSERVATIVE"


def adjudicate_in_process(inference_case: dict) -> dict:
    from backend.pipeline.orchestrator import adjudicate_pipeline
    from backend.schemas import InferenceCase

    return adjudicate_pipeline(InferenceCase(**inference_case)).model_dump(mode="json")


def adjudicate_via_api(base_url: str, inference_case: dict) -> dict:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/v1/adjudications",
        data=json.dumps(inference_case).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Deployed sites rate-limit writes per IP (default 60/min): wait and retry.
            if exc.code != 429 or attempt == MAX_RATE_LIMIT_RETRIES:
                raise
            wait = int(exc.headers.get("Retry-After") or 60)
            print(f"  rate limited by the server; waiting {wait}s ...", file=sys.stderr, flush=True)
            time.sleep(wait)
    raise AssertionError("unreachable")


def run(cases: list[dict], api: str | None = None) -> list[dict]:
    rows = []
    for item in cases:
        result = (adjudicate_via_api(api, item["inference_case"]) if api
                  else adjudicate_in_process(item["inference_case"]))
        trace = result.get("pipeline") or {}
        rows.append({
            "id": item["id"],
            "category": item["category"],
            "title": item["title"],
            "expected": item["expected_decision"],
            "final": result["decision"],
            "draft": result.get("draft_decision"),
            "route": result.get("route"),
            "confidence": result["confidence"],
            "reason": (trace.get("reconciliation") or {}).get("reason_code"),
            "rationale": result["rationale"],
            "abstention_reasons": (trace.get("confidence") or {}).get("abstention_reasons", []),
            "baseline": (result.get("baseline") or {}).get("decision"),
            "outcome": classify(item["expected_decision"], result["decision"], result.get("draft_decision")),
        })
    return rows


def print_table(rows: list[dict], verbose: bool) -> None:
    current = None
    for row in rows:
        if row["category"] != current:
            current = row["category"]
            print(f"\n{current}")
        route = "STP" if row["route"] == "STRAIGHT_THROUGH" else "queue"
        print(f"  {row['outcome']:<12} {row['id']}  {row['title'][:46]:<46} expected={row['expected']:<12} "
              f"final={row['final']:<12} draft={row['draft'] or '-':<12} {route:<5} conf={row['confidence']:.3f}  "
              f"B1={row['baseline'] or '-'}")
        if verbose or row["outcome"] == "FAIL":
            print(f"               reason={row['reason']}: {row['rationale']}")
            for reason in row["abstention_reasons"]:
                print(f"               routing: {reason}")
    totals = {k: sum(r["outcome"] == k for r in rows) for k in ("PASS", "SAFE-ABSTAIN", "CONSERVATIVE", "FAIL")}
    baseline_correct = sum(r["baseline"] == r["expected"] for r in rows)
    automated = [r for r in rows if r["route"] == "STRAIGHT_THROUGH"]
    print(f"\n{len(rows)} cases: " + ", ".join(f"{k} {v}" for k, v in totals.items()))
    print(f"Straight-through: {len(automated)} ({sum(r['final'] == r['expected'] for r in automated)} correct); "
          f"B1 lexical baseline matches expected on {baseline_correct}/{len(rows)}.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the testing-guide case suite.")
    parser.add_argument("--api", help="Base URL of a running API (default: run the pipeline in-process)")
    parser.add_argument("--only", help="Only run cases whose id starts with this prefix (e.g. D or E1)")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", type=Path, help="Write full results as JSON to this path")
    parser.add_argument("--export-eval", type=Path,
                        help="Write Evaluation-tab JSONL (inference_case + ground_truth) to this path and exit")
    parser.add_argument("--show", metavar="ID", help="Print the inputs of one case (to type into the UI) and exit")
    args = parser.parse_args()
    # Category headings contain non-ASCII (e.g. "→"); keep output working when
    # piped or redirected on Windows consoles that default to cp1252.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    cases = load_cases()
    if args.show:
        item = next((c for c in cases if c["id"].upper() == args.show.upper()), None)
        if item is None:
            print(f"No case with id {args.show}")
            return 1
        ic = item["inference_case"]
        print(f"{item['id']} — {item['title']}\nChecks: {item['checks']}\nExpected outcome: {item['expected_decision']}\n")
        for label, key in (("Case reference", "case_id"), ("Diagnosis", "diagnosis"),
                           ("Requested service", "requested_service"), ("Evidence completeness", "evidence_state"),
                           ("Applicable policy language", "policy_text"), ("Clinical evidence summary", "clinical_evidence")):
            print(f"{label}:\n{ic[key]}\n")
        return 0
    if args.only:
        cases = [c for c in cases if c["id"].upper().startswith(args.only.upper())]
    if args.export_eval:
        args.export_eval.write_text("\n".join(
            json.dumps({"inference_case": c["inference_case"], "ground_truth": c["expected_decision"]}, ensure_ascii=False)
            for c in cases) + "\n", encoding="utf-8")
        print(f"Wrote {len(cases)} evaluation lines to {args.export_eval}")
        return 0
    rows = run(cases, args.api)
    print_table(rows, args.verbose)
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return 1 if any(r["outcome"] == "FAIL" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())

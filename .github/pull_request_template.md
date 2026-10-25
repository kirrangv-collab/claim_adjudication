## What and why

<!-- What does this change and why is it needed? Link issues with "Fixes #123". -->

## How it was tested

- [ ] `ruff check backend tests scripts`
- [ ] `python -m pytest`
- [ ] `python scripts/run_test_cases.py` reports 0 FAIL
- [ ] `cd frontend && npm run build` (if the UI changed)
- [ ] Re-ran `python scripts/calibrate_confidence.py` (if the pipeline or labelled sample changed)

## Checklist

- [ ] Synthetic data only; no secrets, credentials or real patient data
- [ ] Docs updated (README, testing_guide.md, deploy/README.md) where relevant

# Contributing to ClaimLab

Thanks for helping. ClaimLab is a research prototype for explainable,
evidence-reconciling claim adjudication. Bug reports, new test scenarios,
ontology additions, UI improvements and documentation fixes are all welcome.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
Report security problems privately as described in [SECURITY.md](SECURITY.md).

## Ground rules

- **Synthetic data only.** Never add real patient data, claims or anything
  covered by a CMS data-use agreement. Policy text must come from public
  sources, with its URL recorded (see [data/README.md](data/README.md)).
- **Safety first.** A change must never make the pipeline approve or deny a
  case it previously, and correctly, sent to human review, unless a test shows
  the new behaviour is right. The scenario suite enforces this (0 FAIL).
- **Small, focused pull requests** with tests are reviewed fastest.
- Contributions are licensed under the [Apache License 2.0](LICENSE)
  (inbound = outbound, see section 5 of the license).

## Development setup

Backend (Python 3.10+; CI uses 3.12):

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m alembic upgrade head
python scripts/create_user.py --username alice --role reviewer
python -m backend                  # http://127.0.0.1:8000
```

Frontend (Node 22):

```bash
cd frontend
npm ci
npm run dev                        # http://127.0.0.1:5173, proxies /api
```

Or run the whole production-like stack with Docker; see
[deploy/README.md](deploy/README.md) ("Test locally").

## Before you open a pull request

Run the same checks as CI:

```bash
ruff check backend tests scripts
python -m pytest
python scripts/run_test_cases.py      # scenario suite: must report 0 FAIL
cd frontend && npm run build          # type-check + production build
```

If you changed the pipeline or the labelled sample, also re-run
`python scripts/calibrate_confidence.py` and commit the updated
`data/calibration/` file.

### Adding a test scenario

Scenarios live in [scripts/build_test_cases.py](scripts/build_test_cases.py)
and are generated into `data/test_cases/guide_test_cases.jsonl`. Add the case
with its expected outcome and rationale, regenerate, and run the suite. See
[testing_guide.md](testing_guide.md) for the categories and outcome classes.

### Commit style

Use short imperative subjects ("Fix quantity parsing for 'or less'") and
explain *why* in the body when it is not obvious.

## Reporting bugs and requesting features

Use the issue forms (**Issues → New issue**). For adjudication bugs, include
the exact case JSON (diagnosis, requested service, policy text, clinical
evidence) and the decision you expected. `python scripts/run_test_cases.py
--show <ID>` prints a scenario in that format.

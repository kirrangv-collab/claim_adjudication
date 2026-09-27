# Claim Adjudication Research Prototype

This workspace contains a new, runnable demonstration scaffold based on the mentor-update PDF. The PDF's original source repository, frozen 24-case benchmark, and B1/B2/B3 implementations were not available in this workspace. Consequently, this application **does not reproduce the reported benchmark results**, and its deterministic lexical adjudicator is not the original research system.

This build adds a production-grade engineering layer — authentication, a database-backed audit trail, a maker-checker human-review workflow, rate limiting, and containerized deployment with migrations — on top of the research prototype. **The adjudication content itself remains unvalidated**: it is not clinically or legally reviewed, and it must never be the sole basis for a real coverage decision. Every case requires a different, authenticated human reviewer to record the actual decision; the deterministic output is only ever a labeled "suggestion."

## Scope and limitations

- React/TypeScript workbench with Case Review (stateless preview), Evaluation, **Review queue** (authenticated, persisted, audited), and Model & Data views; Python FastAPI API with JWT auth, PostgreSQL/SQLite persistence via SQLAlchemy + Alembic migrations, and per-IP rate limiting.
- The primary adjudication output uses deterministic lexical rules; no LLM controls or changes that result.
- **Optional second engine (LLM/RAG)**: when a server operator supplies a Google Gemini API key via `CAF_MODEL_API_KEY` (see below), every case is *also* run through a from-scratch LLM/RAG reasoning engine (`backend/llm_engine.py`, Gemini 2.5 Flash), and both results are shown side by side. This is a from-scratch reimplementation of the project brief's "B3: Single LLM/RAG" idea, not the original research code. It is a **secondary, comparison-only signal**: it never overrides the deterministic result, is never persisted as the authoritative decision, and a disagreement between the two engines is surfaced explicitly to the reviewer rather than silently resolved. See "The LLM/RAG comparison engine" below for the honest, measured behavior of this engine (including its failure modes).
- **Review queue**: submitting a case creates a permanent, audited `CaseReview` row (prototype suggestion + inputs, plus the LLM/RAG signal when configured). A human reviewer records the authoritative `APPROVED` / `DENIED` / `ESCALATED` decision separately. A basic maker-checker control blocks the case's own creator from also recording its decision (unless they are an admin); decisions are immutable once recorded, and only an admin can reopen a case. See "Authentication and the review workflow" below.
- Three deterministic processing stages (policy interpretation, evidence assessment, reconciliation) with a traceable output.
- Inference requests reject extra fields, including evaluation labels. Evaluation labels are kept in a separate wrapper and never passed into inference.
- Evaluation reports all three decisions, macro metrics, false approval/denial rates, human-review rate, latency, a confusion matrix, and per-case failures. Errors stay in accuracy and ground-truth class-recall denominators and are shown in an audit trail.
- B1, B2, benchmark import, and sensitivity analysis are **not implemented** because the source modules and benchmark are unavailable. B3 has a comparison-only reimplementation as described above.
- Still **not** implemented, even with the new engineering layer: clinical/legal validation of the adjudication logic, independent safety/bias/subgroup testing, formal data retention/deletion/backup/DR policy, and a security review. See the in-app "Readiness checklist" on the Model & Data page.
- Lexical matching is intentionally simple; it does not interpret complete policy language, clinical equivalence, or calibrated uncertainty. The LLM/RAG engine can also be wrong — see the measured comparison below. Outputs are not medical, legal, or coverage advice. Do not use with real claims or identifiable health information.

### Authentication and the review workflow

Accounts are provisioned out of band with `scripts/create_user.py` (self-registration is disabled by design):

```powershell
python scripts/create_user.py --username alice --role reviewer
python scripts/create_user.py --username admin --role admin
```

Sign in from the login screen (username + password) to reach Case Review, Evaluation, Review queue, and Model & Data. The frontend stores the returned JWT in `sessionStorage` (cleared when the tab closes) and attaches it as a `Bearer` token to authenticated requests. This is a common, well-understood SPA pattern, but it is JS-readable and therefore more exposed to token theft via XSS than an httpOnly cookie would be — a hardened deployment should consider httpOnly/secure cookies or a managed OIDC provider instead.

Review-queue endpoints (`/api/v1/reviews*`) all require a valid token. `POST /api/v1/reviews/{id}/decision` additionally enforces that the recording reviewer is not the case's creator (403 otherwise) unless they hold the `admin` role, and refuses to overwrite an already-recorded decision (409). `POST /api/v1/reviews/{id}/reopen` is `admin`-only and resets a case back to `pending_review`.

### The LLM/RAG comparison engine

Disabled by default. Set a Google Gemini API key on the backend process to enable it — one variable is all that's required, the rest default to Gemini:

```powershell
$env:CAF_MODEL_API_KEY = "..."   # Gemini API key; never logged, returned, or committed
python -m uvicorn backend.main:app
```

Optional overrides (the defaults are what you'd normally use):

| Variable | Default | Purpose |
| --- | --- | --- |
| `CAF_MODEL_API_KEY` | *(none)* | **Required.** Gemini API key, sent as the `x-goog-api-key` header. |
| `CAF_MODEL_PROVIDER` | `gemini` | Disclosure only; surfaced via `/api/v1/config`. |
| `CAF_MODEL_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta` | The model id is appended as `/models/{model}:generateContent`. |
| `CAF_MODEL_ANALYSIS` | `gemini-2.5-flash` | Model id to call. Must be a **2.5-series** Flash model — see the thinking note below. |

The engine calls Gemini's native `generateContent` REST endpoint directly over `httpx` (already a dependency), so no new packages and no SDK lock-in. Structured output is enforced with `responseMimeType="application/json"` plus a `responseSchema`, so the model returns schema-valid JSON rather than being merely *asked* for JSON.

Design and safety properties (see [`backend/llm_engine.py`](backend/llm_engine.py) for the implementation):

- **Bounded retries only** (2 attempts). If both fail — network error, HTTP error, a Gemini safety block, or a response that doesn't parse into the expected JSON shape — the engine returns `decision: null` with a disclosed `error` string. It never silently substitutes a specific decision for a failure.
- **Quote verification**: the model is asked to quote the exact policy sentence(s) supporting its answer. Each quote is checked against the submitted policy text and flagged `verified_in_policy_text: false` if it doesn't literally appear there, so a hallucinated quote is visible rather than silently trusted.
- **No retrieval/browsing**: the engine only ever sees the policy text and evidence submitted with that one case — nothing else. Gemini grounding/retrieval is deliberately not enabled.
- **Safety blocks and truncation are surfaced, never guessed at.** A `promptFeedback.blockReason`, an empty candidate list, or a `finishReason` other than `STOP` (notably `MAX_TOKENS`) is recorded as a disclosed engine error rather than silently read as a decision.
- **`temperature=0` only** is sent to reduce (not eliminate) run-to-run variance. Gemini's `generateContent` API has **no `seed` parameter**, so the fixed seed used in the previous vLLM configuration is gone; variance reduction is weaker than it was, and a different decision for an identical request between calls remains possible. That is disclosed rather than hidden, because it is itself a reason this must stay a secondary, human-reviewed signal rather than an authority.

**Thinking-budget note (why 2.5 and not 3.x):** Gemini 2.5 models think by default, and `maxOutputTokens` is a *combined* budget covering thought tokens **and** output tokens. Left alone, a reasoning trace can consume the whole budget, and the call returns `finishReason=MAX_TOKENS` with an empty body while still billing for the thought tokens. The engine pins `thinkingConfig.thinkingBudget` to `0` so the 1024-token cap applies to the answer only. Gemini 3.x Flash **cannot fully disable thinking at all**, so this is an intentional pin to the 2.5 series — moving to a 3.x model id requires reworking the budget (and Google's 3.x guidance is to leave `temperature` at its default rather than 0). This is the same class of failure the previous vLLM deployment hit, so the same guard applies.

**Measured comparison (real CMS policy dataset, 10 cases, live model calls):** run against the real-policy sample described below, the deterministic engine and the LLM/RAG engine both scored **7/10** against the project-authored `ground_truth` labels — but they did not make the *same* seven correct calls. The two engines disagreed on roughly half of the 10 cases, each catching mistakes the other made. This is exactly the argument for showing both signals and flagging disagreement rather than trusting either engine alone. **That measurement was taken against the previous vLLM/Nemotron deployment, not Gemini** — treat the 7/10 as a property of that earlier engine and re-run the comparison if you need a current number; see `tests/test_live_llm_engine.py` for a repeatable, opt-in, real-network check (skipped automatically unless `CAF_MODEL_API_KEY` is set, so CI never depends on network access or credentials).

## Run locally

### Backend

From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m alembic upgrade head   # creates/updates data/claimlab.db (SQLite by default)
python scripts/create_user.py --username alice --role reviewer
python -m backend
```

The API listens on `http://127.0.0.1:8000`. Interactive API documentation is at `http://127.0.0.1:8000/docs` (disabled automatically when `CLAIMLAB_ENVIRONMENT=production`).

Key environment variables (see [`backend/config.py`](backend/config.py) for the full list and defaults):

| Variable | Purpose |
| --- | --- |
| `CLAIMLAB_ENVIRONMENT` | `development` / `staging` / `production`. Production disables `/docs` and requires a non-default JWT secret. |
| `CLAIMLAB_DATABASE_URL` | SQLAlchemy URL. Defaults to a local SQLite file; use a `postgresql+psycopg://...` URL in any shared environment. |
| `CLAIMLAB_JWT_SECRET_KEY` | JWT signing secret. The startup default is deliberately obvious/insecure and the app **refuses to start** with it in production. |
| `CLAIMLAB_RATE_LIMIT_REQUESTS` / `CLAIMLAB_RATE_LIMIT_WINDOW_SECONDS` | Requests allowed per client IP per rolling window (default 60/60s). Per-process only — see limitation note in [`backend/rate_limit.py`](backend/rate_limit.py). |

### Frontend

In a second terminal:

```powershell
Set-Location frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to the local backend. To use a different API origin, set `VITE_API_URL` at frontend build/dev time and configure `CLAIMLAB_ALLOWED_ORIGINS` on the backend.

### Containerized deployment (PostgreSQL + migrations)

```powershell
$env:CLAIMLAB_DB_PASSWORD = "choose-a-strong-password"
$env:CLAIMLAB_JWT_SECRET_KEY = "choose-a-strong-unique-secret"
docker compose up --build
docker compose exec api python scripts/create_user.py --username alice --role reviewer
```

Compose refuses to start unless both secrets are set (no insecure defaults leak into containers) and runs `alembic upgrade head` automatically on API container startup via [`backend/docker-entrypoint.sh`](backend/docker-entrypoint.sh) before the server starts. Open `http://127.0.0.1:8080`; the API remains bound to localhost on port 8000. Containers run non-root with dropped Linux capabilities and read-only root filesystems; PostgreSQL data persists in the `claimlab-db-data` named volume. This is a solid local/staging baseline — it is still not a substitute for a real production rollout plan (network policy, secrets manager, backups/DR, monitoring/alerting, and the compliance review noted above).

## API

- `GET /api/v1/health` — health and benchmark availability.
- `GET /api/v1/config` — active model, environment, auth/audit status, LLM/RAG engine status (when configured), and unavailable-baseline notices.
- `GET /api/v1/sample-cases` — the real-CMS-policy research sample dataset with provenance and caveats (see Data section below).
- `POST /api/v1/adjudications` — adjudicate one inference-only case (stateless preview, no auth, not persisted). Response includes `llm_result` (null unless the LLM/RAG engine is configured).
- `POST /api/v1/evaluations` — evaluate 1–500 `{ "inference_case": ..., "ground_truth": ... }` items.
- `POST /api/v1/auth/login` — OAuth2 password flow; returns a bearer JWT. `GET /api/v1/auth/me` returns the current user.
- `POST /api/v1/reviews` (auth) — persist a case + its prototype suggestion (and LLM/RAG signal, if configured) for human review.
- `GET /api/v1/reviews` (auth) — paginated, status-filterable list of all reviews (shared organizational queue, not scoped to the caller).
- `GET /api/v1/reviews/{id}` (auth) — full detail for one review.
- `POST /api/v1/reviews/{id}/decision` (auth) — record the human decision; enforces maker-checker and immutability (see above).
- `POST /api/v1/reviews/{id}/reopen` (admin) — reset a decided case back to pending.

Inference case example:

```json
{
  "diagnosis": "glaucoma",
  "requested_service": "glaucoma imaging",
  "policy_text": "Glaucoma imaging is covered when medically necessary.",
  "clinical_evidence": "Documented glaucoma imaging is medically necessary.",
  "evidence_state": "documented"
}
```

The adjudicator is conservative when evidence is incomplete or conflicting. Its rule-based confidence is only a heuristic. The evaluation endpoint uses exact `APPROVE`, `DENY`, and `HUMAN_REVIEW` labels, with labels kept outside the inference schema.

Evaluation accuracy uses all submitted cases as its denominator, including errors. Per-class recall support includes errored cases, so an inference failure is not a correct prediction. False approval rate uses all non-APPROVE ground-truth cases, and false denial rate uses all non-DENY ground-truth cases; failed inferences remain in these denominators. Average latency and human-review rate use successfully evaluated cases only; errors are listed separately. Per-class precision uses successful predictions only. These definitions are descriptive prototype metrics, not an externally validated benchmark protocol.

## Tests

From the project root:

```powershell
python -m pytest
```

This runs the deterministic-adjudicator, security, rate-limit, auth/review-workflow, real-policy-dataset, and LLM/RAG-engine tests (46 tests, hermetic — no network calls) against an isolated, auto-created SQLite database (see `tests/conftest.py` — never the local `data/claimlab.db`). One additional live test in `tests/test_live_llm_engine.py` is skipped automatically unless `CAF_MODEL_API_KEY` is set — it makes one real network call to Gemini with synthetic text only, so CI never depends on network access or credentials.

## Next integration step

Provide the original `Claim_Adjudication_Pilot` source repository and frozen workbook. Then the scaffold can be adapted to the real shared schemas/evaluator, existing baselines, retry policy, benchmark leakage controls, and sensitivity-analysis workflow rather than approximating those unavailable components.

## Data

### Real, publicly downloaded policy corpus (available now)

[`scripts/extract_ncd_sections.py`](scripts/extract_ncd_sections.py) and [`scripts/build_evaluation_cases.py`](scripts/build_evaluation_cases.py) download-and-process, respectively, real official U.S. government policy text from the CMS **National Coverage Determinations (NCD) Manual** (Pub. 100-03, Chapter 1, Part 1, "80 – Eye" series) into:

- [`data/processed/ncd_eye_policy_sections.json`](data/processed/ncd_eye_policy_sections.json) — 13 unmodified real policy sections with full source/checksum provenance.
- [`data/processed/ncd_eye_evaluation_cases.jsonl`](data/processed/ncd_eye_evaluation_cases.jsonl) — 10 evaluation cases pairing that real policy text with **synthetic, non-patient** clinical vignettes and project-authored `ground_truth` labels (methodology and caveats are documented inline in `scripts/build_evaluation_cases.py`).

Running the deterministic adjudicator against this real dataset (`GET /api/v1/sample-cases` then `POST /api/v1/evaluations`, or the Evaluation tab's "Load real CMS policy sample" button) currently scores **70% accuracy**, including one deliberately retained, reproducible **false-approval case** (`ncd-80.2-opt-occult-large-lesion-no-progression`) where the lexical engine is misled by a "covered" keyword elsewhere in a long, multi-clause real policy section. This is intentional: it documents a genuine limitation and motivates the evidence-reconciliation research direction, rather than presenting an inflated accuracy number. See `tests/test_real_policy_data.py` for the reproducible assertions and `data/README.md` for full source citation and terms.

### Prior blocked attempt (unchanged)

See [`data/README.md`](data/README.md) and [`data/manifest.json`](data/manifest.json) for a separate CMS aggregate utilization dataset (Medicare Physician & Other Practitioners by Provider and Service) that returned HTTP 403 in this environment. That file, even if obtained, has no adjudication labels and cannot validate claim decisions — it is unrelated to the NCD policy corpus above.

### What is still not available

The original frozen 24-case benchmark and the B1/B2/B3 baseline implementations referenced in the mentor-update PDF remain unavailable in this workspace. Accuracy figures above describe only this project's own real-policy sample, not that frozen benchmark.

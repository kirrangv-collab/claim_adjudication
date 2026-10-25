# Claim Adjudication Research Prototype

A runnable research system for evidence-based health-insurance claim adjudication under policy and clinical ambiguity. Claims are adjudicated by a **five-agent evidence-reconciliation pipeline** (see [Architecture](#architecture)): policy clauses become logic trees, clinical notes become normalised ICD-10-CM concepts, a hybrid graph/vector knowledge layer links the two, a reconciliation agent evaluates every rule against the evidence, and a confidence agent either **finalises the case by straight-through processing (STP)** or **routes it to an audited human review queue**.

The engineering layer — JWT authentication, a database-backed audit trail, a maker-checker human-review workflow, rate limiting, and containerised deployment with migrations — is production-style. **The adjudication content itself remains unvalidated**: it is not clinically or legally reviewed and must never be the sole basis for a real coverage decision. The mentor-update PDF's original repository, frozen 24-case benchmark and original B1/B2/B3 code are not in this workspace, so its reported benchmark results are **not** reproduced here.

## Scope and limitations

- React/TypeScript workbench with Case Review (stateless preview with the full agent trace), Evaluation (single engine or side-by-side engine comparison), **Review queue** (authenticated, persisted, audited, STP-aware), and Model & Data views; Python FastAPI API with JWT auth, PostgreSQL/SQLite persistence via SQLAlchemy + Alembic migrations, and per-IP rate limiting.
- The primary engine is the deterministic five-agent pipeline in [`backend/pipeline/`](backend/pipeline). Optional **Gemini assistance** (Agent 1 clause parsing, Agent 5 audit-brief prose) is schema-validated, quote-checked and can never change the decision logic; any failure falls back to the deterministic output.
- **Straight-through processing**: a draft APPROVE whose calibrated confidence is ≥ 0.90, or a draft DENY ≥ 0.95, is finalised automatically and stored as `auto_decided` (fully audited; an admin can reopen it). Everything else — drafts of HUMAN_REVIEW, low confidence, incomplete or conflicting evidence, contradictions, and any denial that would override a satisfied coverage rule — goes to the human queue. Thresholds are configurable.
- **Baselines**: the original lexical engine is kept as **B1** ([`backend/agents.py`](backend/agents.py)); an optional, from-scratch **B3-style LLM/RAG** engine ([`backend/llm_engine.py`](backend/llm_engine.py)) runs as a side-by-side comparison signal when a Gemini key is configured. The original B2 (KAMEL-inspired) code is unavailable; KAMEL valid-ancestor linking is implemented inside Agent 3 instead.
- Inference requests reject extra fields, including evaluation labels. Evaluation labels are kept in a separate wrapper and never passed into inference.
- Evaluation reports all three decisions, macro metrics, false approval/denial rates, human-review rate, latency, a confusion matrix and per-case failures, plus — for the pipeline — **STP rate, STP accuracy and pre-routing draft accuracy**. `POST /api/v1/evaluations/compare` scores the pipeline, B1 and (optionally) the LLM engine on the same cases and lists disagreements.
- Still **not** implemented: clinical/legal validation, independent safety/bias/subgroup testing, formal retention/deletion/backup/DR policy, and a security review (see the in-app readiness checklist). The ontology and labelled sample are small, project-curated research artifacts. Outputs are not medical, legal or coverage advice. Do not use with real claims or identifiable health information.

## Architecture

```
[ Incoming Claim & Policy Documents ]
                  |
  Agent 1  Policy Logic            policy clauses -> ASTs (EXCEPT, UNLESS, ONLY IF, AND/OR/NOT)
                  |  structured policy rules
  Agent 2  Clinical Evidence       active-encounter isolation, MCR sliding window, SBioBERT ICD normalisation
                  |  clean codes & terms
  Agent 3  Knowledge & Evidence    hybrid Graph-RAG: Qdrant (vector) + Neo4j (graph), KAMEL valid ancestors
           Linker
                  |  graph paths & retrieved evidence
  Agent 4  Reconciliation &        3-valued rule evaluation, contradictions, missing evidence
           Adjudication
                  |  draft outcome & uncertainty metrics
  Agent 5  Confidence &            calibrated confidence, 1-page audit brief, routing
           Explainability
          /                 \
  confidence >= 0.90/0.95    otherwise
  Straight-Through            Human Review Queue
  Processing (APPROVE/DENY)   (calibrated abstention)
```

| Agent | Module | What it does |
| --- | --- | --- |
| 1 · Policy Logic | [`policy_logic.py`](backend/pipeline/policy_logic.py) | Splits CMS policy text into sections (titles, "Not Covered" banners), sentences and clauses; detects each clause's effect (COVERED / NOT_COVERED / MAC-DISCRETIONARY); extracts the subject with anaphora resolution ("It", "Such cases", "In this situation", "The above 2 indications … only when", "These include …"); and builds a condition AST. EXCEPT/UNLESS/ONLY IF bind to the clause they appear in; bullets, numbered lists (connector-aware), "such as"/"e.g." lists, quantity constraints ("4 disk areas or less") and in-policy definitions ("evidence of progression must be documented by …") are handled. Rules are tagged `sufficient`, `necessary` (only-if constraints) or `residual` (catch-alls). Low-confidence clauses can be re-parsed by Gemini; the tree is accepted only if every predicate is quoted from the clause. |
| 2 · Clinical Evidence | [`clinical_evidence.py`](backend/pipeline/clinical_evidence.py) | Separates the active encounter from noisy text (PMH, prior/dated encounters, family history, copy-forward duplicates, signatures/templates); runs **MCR** multi-scale sliding-window candidate recognition and normalises windows to the ontology by exact synonym match, a strict typo-tolerant spelling match for unknown single words ("glaucomma" → glaucoma), or embedding nearest-neighbour (SBioBERT when configured); assigns NegEx/ConText-style assertion status (present, negated, undocumented, historical, family, hypothetical); extracts quantities. |
| 3 · Knowledge & Evidence Linker | [`knowledge_linker.py`](backend/pipeline/knowledge_linker.py) | Links every predicate concept to evidence concepts through the ontology graph using **KAMEL valid ancestors** (specific evidence satisfies a general policy condition; chapter/block nodes are never valid ancestors; a negated general concept negates its descendants; `treats` edges link procedures to indications). Retrieves the most relevant submitted-policy passages and reference-corpus passages from the vector store, finds related corpus sections in the graph, and scores each rule's relevance to the requested service. |
| 4 · Reconciliation & Adjudication | [`reconciliation.py`](backend/pipeline/reconciliation.py) | Evaluates every relevant rule AST with Kleene three-valued logic. Absent concepts are FALSE only when the record is declared complete (`evidence_state=documented`, closed-world), otherwise UNKNOWN; "not documented", historical-only and hypothetical mentions are always UNKNOWN (never assumed absent); quantities are compared but never assumed; in "X due to Y" a documented cause Y implies the effect X; the requested service may satisfy a condition about itself but never counts as clinical evidence; administrative conditions are presumed and disclosed. It detects contradictions (conflicting evidence, a claimed diagnosis negated by the evidence, equally specific coverage/exclusion conflicts) and missing evidence, resolves conflicts by specificity (lex specialis), and emits a draft decision plus uncertainty metrics. |
| 5 · Confidence & Explainability | [`confidence.py`](backend/pipeline/confidence.py) | Scores the draft with a transparent logistic model over the uncertainty metrics, applies Platt calibration from [`data/calibration/pipeline_calibration.json`](data/calibration/pipeline_calibration.json), routes to STP or the human queue with explicit reasons, and writes a one-page audit brief (optionally with a Gemini-written summary that must state the same outcome and cite only codes in the trace). |

The orchestrator ([`orchestrator.py`](backend/pipeline/orchestrator.py)) runs the agents in order and returns every artifact as a typed `PipelineTrace` ([`contracts.py`](backend/pipeline/contracts.py)), which is persisted with each review.

### Knowledge layer

[`backend/knowledge/`](backend/knowledge) provides pluggable adapters selected by environment variables. Each external backend is optional: if it is not configured, not installed, or unreachable, the in-process implementation is used **and the fallback is disclosed** in `/api/v1/config` and `/api/v1/health`.

| Component | Default (no services needed) | Real backend | Settings |
| --- | --- | --- | --- |
| Embedder (Agent 2/3) | Hashing char-n-gram embedder (deterministic, spelling-level similarity only) | SBioBERT via sentence-transformers (`pritamdeka/S-BioBert-snli-multinli-stsb`) | `CLAIMLAB_EMBEDDER=sbiobert`, `CLAIMLAB_SBIOBERT_MODEL`; install `.[sbiobert]` |
| Vector store | In-memory (numpy) | Qdrant | `CLAIMLAB_VECTOR_BACKEND=qdrant`, `CLAIMLAB_QDRANT_URL`, `CLAIMLAB_QDRANT_API_KEY`; install `.[knowledge]` |
| Graph store | In-memory | Neo4j (`Concept` nodes, `IS_A` / `TREATS` / `MENTIONS` edges, Cypher traversal) | `CLAIMLAB_GRAPH_BACKEND=neo4j`, `CLAIMLAB_NEO4J_URI`, `CLAIMLAB_NEO4J_USER`, `CLAIMLAB_NEO4J_PASSWORD`; install `.[knowledge]` |

The ontology ([`data/ontology/eye_ontology.json`](data/ontology/eye_ontology.json)) is a curated subset of public-domain ICD-10-CM codes plus clearly labelled project-defined grouping/finding/procedure/device/drug concepts for the NCD 80.x eye corpus. Backends are seeded automatically on first use; `python scripts/load_knowledge_base.py` pre-seeds and verifies them (non-zero exit if a requested backend fell back).

### Pipeline results on the real-policy sample

On the 22-case real CMS policy sample (see [Data](#data)), measured with `POST /api/v1/evaluations/compare`:

| Engine | Accuracy | False approval rate | False denial rate | Human review rate | STP rate / STP accuracy |
| --- | --- | --- | --- | --- | --- |
| Five-agent pipeline | 100% (22/22) | 0% | 0% | 27.3% | 72.7% / 100% |
| B1 lexical baseline | 54.5% | 40.0% | 0% | 45.5% | — |

**Read this carefully.** The labels are project-authored, the original 10 cases were used while building the pipeline, and the 12 newer cases were labelled before the pipeline was run on them but still informed four general bug fixes (NegEx scope termination, prefix negation, historical-section assertions, and a confidence-metric bug). The calibration file records zero incorrect decisive drafts, so Platt scaling is deliberately **not** fitted (it needs both outcomes) and raw scores are used. These numbers demonstrate that the architecture fixes B1's documented failure modes — for example, `ncd-80.2-opt-occult-large-lesion-no-progression` (B1 false approval) and `ncd-80.7-cosmetic-keratotomy` (B1 over-escalation) — not real-world accuracy.

### How the LLM is used

Gemini is optional and never decides on its own:

1. **Agent 1 assist** — up to three low-confidence clauses per case are re-parsed into a flat node list; the result is rejected unless it is a valid, acyclic tree of allowed operators whose every predicate is quoted from the clause.
2. **Agent 5 assist** — the "Reviewer summary" paragraph of the audit brief may be rewritten from the structured brief; the rewrite is rejected unless it states the final outcome, names no other outcome, and cites only ICD codes present in the trace.
3. **Comparison engine (B3-style)** — described below; shown side by side, never authoritative.

Evaluation (`/api/v1/evaluations`) always runs the deterministic pipeline so results are reproducible.
### Authentication and the review workflow

Accounts are provisioned out of band with `scripts/create_user.py` (self-registration is disabled by design):

```powershell
python scripts/create_user.py --username alice --role reviewer
python scripts/create_user.py --username admin --role admin
```

Sign in from the login screen (username + password) to reach Case Review, Evaluation, Review queue, and Model & Data. The frontend stores the returned JWT in `sessionStorage` (cleared when the tab closes) and attaches it as a `Bearer` token to authenticated requests. This is a common, well-understood SPA pattern, but it is JS-readable and therefore more exposed to token theft via XSS than an httpOnly cookie would be — a hardened deployment should consider httpOnly/secure cookies or a managed OIDC provider instead.

Review-queue endpoints (`/api/v1/reviews*`) all require a valid token. Submitting a case runs the pipeline and stores the draft suggestion, calibrated confidence, route, audit brief and full agent trace:

- **STP route** — the case is stored as `auto_decided` with `final_decision` = the draft and `decision_source="stp"`. No human decision can be recorded on it (409); an admin can reopen it, which sends it to the human queue.
- **Human route** — the case is stored as `pending_review`. `POST /api/v1/reviews/{id}/decision` enforces that the recording reviewer is not the case's creator (403 otherwise) unless they hold the `admin` role, refuses to overwrite an already-recorded decision (409), and sets `final_decision` (`APPROVED`→APPROVE, `DENIED`→DENY, `ESCALATED`→none) with `decision_source="human"`.
- `POST /api/v1/reviews/{id}/reopen` is `admin`-only and resets any decided case back to `pending_review`.

### The LLM/RAG comparison engine and LLM assists

Disabled by default. Set a Google Gemini API key on the backend process to enable both the comparison engine and the pipeline's Agent 1/Agent 5 assists (set `CLAIMLAB_PIPELINE_LLM_ASSIST=false` to keep only the comparison engine) — one variable is all that's required, the rest default to Gemini:

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
| `CAF_MODEL_ANALYSIS` | `gemini-3.8-flash` | Model id to call. Must be a Flash model that accepts `thinkingBudget: 0` — see the thinking note below. |

Both uses go through one shared client, [`backend/llm/gemini_client.py`](backend/llm/gemini_client.py), which calls Gemini's native `generateContent` REST endpoint directly over `httpx`, so no new packages and no SDK lock-in. Structured output is enforced with `responseMimeType="application/json"` plus a `responseSchema`, so the model returns schema-valid JSON rather than being merely *asked* for JSON.

Design and safety properties (see [`backend/llm_engine.py`](backend/llm_engine.py) for the implementation):

- **Bounded retries only** (2 attempts). If both fail — network error, HTTP error, a Gemini safety block, or a response that doesn't parse into the expected JSON shape — the engine returns `decision: null` with a disclosed `error` string. It never silently substitutes a specific decision for a failure.
- **Quote verification**: the model is asked to quote the exact policy sentence(s) supporting its answer. Each quote is checked against the submitted policy text and flagged `verified_in_policy_text: false` if it doesn't literally appear there, so a hallucinated quote is visible rather than silently trusted.
- **No retrieval/browsing**: the engine only ever sees the policy text and evidence submitted with that one case — nothing else. Gemini grounding/retrieval is deliberately not enabled.
- **Safety blocks and truncation are surfaced, never guessed at.** A `promptFeedback.blockReason`, an empty candidate list, or a `finishReason` other than `STOP` (notably `MAX_TOKENS`) is recorded as a disclosed engine error rather than silently read as a decision.
- **`temperature=0` only** is sent to reduce (not eliminate) run-to-run variance. Gemini's `generateContent` API has **no `seed` parameter**, so the fixed seed used in the previous vLLM configuration is gone; variance reduction is weaker than it was, and a different decision for an identical request between calls remains possible. That is disclosed rather than hidden, because it is itself a reason this must stay a secondary, human-reviewed signal rather than an authority.

**Thinking-budget note (why the budget is pinned):** Gemini Flash models think by default, and `maxOutputTokens` is a *combined* budget covering thought tokens **and** output tokens. Left alone, a reasoning trace can consume the whole budget, and the call returns `finishReason=MAX_TOKENS` with an empty body while still billing for the thought tokens. The engine pins `thinkingConfig.thinkingBudget` to `0` so the 1024-token cap applies to the answer only. This is the same class of failure the previous vLLM deployment hit, so the same guard applies. Support for `thinkingBudget` is **per model id, not per series**: `gemini-3.8-flash` and `gemini-flash-latest` accept and honour it (verified: zero thought parts, `finishReason=STOP`), while `gemini-3.5-flash-lite` rejects it with HTTP 400. Re-verify this before changing the pinned id rather than assuming a whole series behaves uniformly.

**Model-retirement note:** the previous default `gemini-2.5-flash` now returns HTTP 404 (`"This model ... is no longer available to new users"`) for newly issued API keys, so it is no longer a usable default. Gemini retires model ids over time, so a pinned id can break again; the engine reports that as a disclosed HTTP error rather than guessing a decision. `gemini-flash-latest` tracks the current Flash release but changes behaviour when Google updates it, so the pinned version id remains the default for reproducibility.

**Historical measured comparison (real CMS policy dataset, 10 cases, live model calls):** run against the original 10-case real-policy sample, the B1 lexical engine and the LLM/RAG engine both scored **7/10** against the project-authored `ground_truth` labels — but they did not make the *same* seven correct calls. The two engines disagreed on roughly half of the 10 cases, each catching mistakes the other made. This is exactly the argument for showing both signals and flagging disagreement rather than trusting either engine alone. **That measurement was taken against the previous vLLM/Nemotron deployment, not Gemini** — treat the 7/10 as a property of that earlier engine and re-run the comparison if you need a current number; see `tests/test_live_llm_engine.py` for a repeatable, opt-in, real-network check (skipped automatically unless `CAF_MODEL_API_KEY` is set, so CI never depends on network access or credentials).

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
| `CLAIMLAB_STP_ENABLED` | `true` (default) lets Agent 5 finalise high-confidence drafts; `false` sends every case to the human queue. |
| `CLAIMLAB_STP_APPROVE_THRESHOLD` / `CLAIMLAB_STP_DENY_THRESHOLD` | Calibrated-confidence thresholds for straight-through APPROVE (default 0.90) and DENY (default 0.95). |
| `CLAIMLAB_PIPELINE_LLM_ASSIST` | `true` (default) lets Gemini assist Agents 1 and 5 when `CAF_MODEL_API_KEY` is set. |
| `CLAIMLAB_EMBEDDER`, `CLAIMLAB_VECTOR_BACKEND`, `CLAIMLAB_GRAPH_BACKEND` (+ connection settings) | Knowledge-layer backends; see [Knowledge layer](#knowledge-layer). |

Optional knowledge-layer backends: `python -m pip install -e ".[knowledge]"` (Qdrant + Neo4j clients) and `python -m pip install -e ".[sbiobert]"` (SBioBERT; pulls in PyTorch). Re-run `python scripts/calibrate_confidence.py` after changing the pipeline or the labelled sample.

### Frontend

In a second terminal:

```powershell
Set-Location frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to the local backend. To use a different API origin, set `VITE_API_URL` at frontend build/dev time and configure `CLAIMLAB_ALLOWED_ORIGINS` on the backend.

### Containerized deployment (PostgreSQL + Qdrant + Neo4j + migrations)

```powershell
$env:CLAIMLAB_DB_PASSWORD = "choose-a-strong-password"
$env:CLAIMLAB_JWT_SECRET_KEY = "choose-a-strong-unique-secret"
$env:CLAIMLAB_NEO4J_PASSWORD = "choose-a-strong-neo4j-password"
# Optional: bake SBioBERT into the API image and use it
# $env:CLAIMLAB_INSTALL_SBIOBERT = "true"; $env:CLAIMLAB_EMBEDDER = "sbiobert"
docker compose up --build
docker compose exec api python scripts/create_user.py --username alice --role reviewer
```

Compose starts PostgreSQL, **Qdrant** (vector store) and **Neo4j** (knowledge graph) alongside the API and web containers, and points the API at them (`CLAIMLAB_VECTOR_BACKEND=qdrant`, `CLAIMLAB_GRAPH_BACKEND=neo4j`); the API seeds both on first use and discloses any fallback. Compose refuses to start unless all three secrets are set (no insecure defaults leak into containers) and runs `alembic upgrade head` automatically on API container startup via [`backend/docker-entrypoint.sh`](backend/docker-entrypoint.sh) before the server starts. Open `http://127.0.0.1:8080`; the API remains bound to localhost on port 8000. Containers run non-root with dropped Linux capabilities and read-only root filesystems; PostgreSQL, Qdrant and Neo4j data persist in named volumes. This is a solid local/staging baseline — it is still not a substitute for a real production rollout plan (network policy, secrets manager, backups/DR, monitoring/alerting, and the compliance review noted above).

### Deploy as a public website (free)

[`deploy/`](deploy) contains a complete single-server kit: **Caddy** (automatic HTTPS) ? nginx web ? API ? PostgreSQL, with optional Qdrant + Neo4j. It also includes helper scripts for server setup, secret generation, backups, restore, updates and a nightly demo reset. The recommended $0 setup is an **Oracle Cloud Always Free** VM, a free **DuckDNS** domain and a Let's Encrypt certificate:

```bash
sudo bash deploy/setup-server.sh                            # on a fresh Ubuntu VM
bash deploy/configure.sh --domain your-name.duckdns.org     # writes deploy/.env with random secrets
cd deploy && docker compose up -d --build
```

By default the site runs in **public demo mode** (`CLAIMLAB_PUBLIC_DEMO=true`). The login page offers two shared reviewer accounts (`demo-maker`, `demo-checker`), so visitors can try the maker-checker workflow on synthetic data, and `deploy/reset-demo.sh` wipes it nightly. Use `--no-demo` for a private deployment. The step-by-step guide covers cloud firewall rules, cron jobs, pre-built multi-arch images from GHCR, local testing, troubleshooting and a pre-publication checklist: **[deploy/README.md](deploy/README.md)**.

## API

- `GET /api/v1/health` — health, benchmark availability and active knowledge backends.
- `GET /api/v1/config` — active pipeline (agents, version, STP thresholds, calibration, knowledge backends and fallbacks, LLM assist status), environment, auth/audit status, LLM/RAG engine status, available engines and unavailable-baseline notices.
- `GET /api/v1/sample-cases` — the real-CMS-policy research sample dataset with provenance and caveats (see Data section below).
- `POST /api/v1/adjudications` — run the five-agent pipeline on one inference-only case (stateless preview, no auth, not persisted). Returns the final outcome (`decision`), `draft_decision`, `route`, calibrated `confidence`, `audit_brief`, the full `pipeline` trace, the B1 `baseline` decision, and `llm_result` (null unless the LLM/RAG engine is configured).
- `POST /api/v1/evaluations` — evaluate 1–500 `{ "inference_case": ..., "ground_truth": ... }` items with `engine` = `multi_agent_pipeline` (default), `b1_lexical` or `llm_rag` (when configured).
- `POST /api/v1/evaluations/compare` — score the pipeline and B1 (plus the LLM engine with `include_llm: true`, when configured) on the same cases and list per-case disagreements.
- `POST /api/v1/auth/login` — OAuth2 password flow; returns a bearer JWT. `GET /api/v1/auth/me` returns the current user.
- `POST /api/v1/reviews` (auth) — run the pipeline and persist the case with its draft, route, audit brief and agent trace (and LLM/RAG signal, if configured); finalised immediately if routed straight-through.
- `GET /api/v1/reviews` (auth) — paginated list of all reviews filterable by `status` = `pending_review`, `auto_decided` or `reviewed` (shared organizational queue, not scoped to the caller).
- `GET /api/v1/reviews/{id}` (auth) — full detail for one review.
- `POST /api/v1/reviews/{id}/decision` (auth) — record the human decision; enforces maker-checker and immutability (see above).
- `POST /api/v1/reviews/{id}/reopen` (admin) — reset a human- or STP-decided case back to pending.

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

The pipeline is conservative when evidence is incomplete or conflicting: absence of evidence is only treated as negative when the record is declared complete, and any contradiction or undetermined exception routes to the human queue. The evaluation endpoint uses exact `APPROVE`, `DENY`, and `HUMAN_REVIEW` labels, with labels kept outside the inference schema.

Evaluation accuracy uses all submitted cases as its denominator, including errors. Per-class recall support includes errored cases, so an inference failure is not a correct prediction. False approval rate uses all non-APPROVE ground-truth cases, and false denial rate uses all non-DENY ground-truth cases; failed inferences remain in these denominators. Average latency and human-review rate use successfully evaluated cases only; errors are listed separately. Per-class precision uses successful predictions only. For the pipeline, `accuracy` scores the **routed** outcome (a correct draft that was routed to the human queue counts as HUMAN_REVIEW), `draft_accuracy` scores Agent 4's draft before routing, `stp_rate` is the share of evaluated cases finalised straight-through, and `stp_accuracy` is the accuracy of those automated outcomes. These definitions are descriptive prototype metrics, not an externally validated benchmark protocol.

## Tests

From the project root:

```powershell
python -m pytest
```

This runs the per-agent pipeline tests ([`tests/test_pipeline_agents.py`](tests/test_pipeline_agents.py): AST operators, assertion detection, encounter isolation, KAMEL valid ancestors, three-valued reconciliation, routing thresholds, LLM-assist validation and fallback), knowledge-layer adapter tests ([`tests/test_knowledge_layer.py`](tests/test_knowledge_layer.py): the real Qdrant client in `:memory:` mode, a fake-driver Neo4j test, fallback disclosure), API, security, rate-limit, STP/auth/review-workflow, real-policy-dataset (pipeline vs. B1) and LLM/RAG-engine tests (148 tests including the 51-scenario guide suite and public-demo-mode tests, hermetic — no network calls) against an isolated, auto-created SQLite database (see `tests/conftest.py` — never the local `data/claimlab.db`). One additional live test in `tests/test_live_llm_engine.py` is skipped automatically unless `CAF_MODEL_API_KEY` is set — it makes one real network call to Gemini with synthetic text only, so CI never depends on network access or credentials. `tests/test_knowledge_layer.py::test_live_neo4j_graph_store_traversal` is likewise skipped unless `CLAIMLAB_TEST_NEO4J_URI` (plus `CLAIMLAB_TEST_NEO4J_PASSWORD`) points at a Neo4j server.

### Scenario suite and testing guide

[`testing_guide.md`](testing_guide.md) is a complete, step-by-step guide for anyone testing the application: setup, a five-minute smoke test, a UI walkthrough with measured expected values, API and security checks, troubleshooting and known limitations. It is backed by a 51-scenario suite ([`data/test_cases/guide_test_cases.jsonl`](data/test_cases/guide_test_cases.jsonl), built by [`scripts/build_test_cases.py`](scripts/build_test_cases.py)) covering policy logic, quantities, evidence completeness, clinical assertions, real CMS policies, out-of-domain policies and robustness/XSS inputs:

```powershell
python scripts/run_test_cases.py                 # in-process
python scripts/run_test_cases.py --api http://127.0.0.1:8000   # against a running API
python scripts/run_test_cases.py --show A04      # inputs to type into the UI
```

Current result: 46 PASS, 5 SAFE-ABSTAIN (correct draft routed to a human), 0 FAIL; all 27 straight-through decisions correct (B1 matches 31/51).

## Next integration steps

- Provide the original `Claim_Adjudication_Pilot` repository and frozen 24-case workbook so the pipeline can be scored on the frozen benchmark against the original B1/B2/B3 baselines.
- Expand the labelled benchmark (especially with genuinely held-out cases and cases the pipeline gets wrong) so `scripts/calibrate_confidence.py` can fit Platt calibration, and run sensitivity analysis on the STP thresholds.
- Grow the ontology beyond the eye chapter (or connect a licensed terminology service) and evaluate SBioBERT vs. the hashing embedder on unseen terminology.

## Data

### Real, publicly downloaded policy corpus (available now)

[`scripts/extract_ncd_sections.py`](scripts/extract_ncd_sections.py) and [`scripts/build_evaluation_cases.py`](scripts/build_evaluation_cases.py) download-and-process, respectively, real official U.S. government policy text from the CMS **National Coverage Determinations (NCD) Manual** (Pub. 100-03, Chapter 1, Part 1, "80 – Eye" series) into:

- [`data/processed/ncd_eye_policy_sections.json`](data/processed/ncd_eye_policy_sections.json) — 13 unmodified real policy sections with full source/checksum provenance.
- [`data/processed/ncd_eye_evaluation_cases.jsonl`](data/processed/ncd_eye_evaluation_cases.jsonl) — 22 evaluation cases pairing that real policy text with **synthetic, non-patient** clinical vignettes and project-authored `ground_truth` labels (methodology and caveats are documented inline in `scripts/build_evaluation_cases.py`). The first 10 cases were used while developing the pipeline; the 12-case expansion set (including noisy multi-encounter notes that exercise Agent 2) was labelled before the pipeline was run on it.
- [`data/ontology/eye_ontology.json`](data/ontology/eye_ontology.json) — the curated knowledge graph used by Agents 2 and 3 (public-domain ICD-10-CM subset + labelled project-defined concepts).
- [`data/calibration/pipeline_calibration.json`](data/calibration/pipeline_calibration.json) — Agent 5 calibration parameters and diagnostics written by `scripts/calibrate_confidence.py`.

Run it with `GET /api/v1/sample-cases` then `POST /api/v1/evaluations` (or `/evaluations/compare`), or with the Evaluation tab's "Load real CMS policy sample" button. The B1 lexical baseline scores **54.5%** on this sample, including the deliberately retained, reproducible **false-approval case** `ncd-80.2-opt-occult-large-lesion-no-progression`, where it is misled by a "covered" keyword elsewhere in a long multi-clause policy section; the pipeline evaluates the narrow ONLY-IF exception in its own clause and denies it. See [Pipeline results](#pipeline-results-on-the-real-policy-sample) for the caveats, `tests/test_real_policy_data.py` for the reproducible assertions, and `data/README.md` for full source citation and terms.

### Prior blocked attempt (unchanged)

See [`data/README.md`](data/README.md) and [`data/manifest.json`](data/manifest.json) for a separate CMS aggregate utilization dataset (Medicare Physician & Other Practitioners by Provider and Service) that returned HTTP 403 in this environment. That file, even if obtained, has no adjudication labels and cannot validate claim decisions — it is unrelated to the NCD policy corpus above.

### What is still not available

The original frozen 24-case benchmark and the original B1/B2/B3 baseline implementations referenced in the mentor-update PDF remain unavailable in this workspace (this repo's B1 is its own lexical engine, and its B3-style engine is a reimplementation). Accuracy figures above describe only this project's own real-policy sample, not that frozen benchmark.

## Contributing, security and license

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and the checks CI runs, the [Code of Conduct](CODE_OF_CONDUCT.md), and [SECURITY.md](SECURITY.md) for reporting vulnerabilities privately.

Licensed under the [Apache License 2.0](LICENSE); see [NOTICE](NOTICE). CMS policy excerpts and ICD-10-CM titles are U.S. government works in the public domain. All clinical vignettes and labels are synthetic.

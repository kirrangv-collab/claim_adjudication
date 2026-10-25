# ClaimLab — Testing Guide

This guide lets **anyone** — developer, QA tester, reviewer or researcher —
install, run and test the Claim Adjudication Research Lab end to end:
automated tests, a 51-case scenario suite, a step-by-step UI walkthrough, API
checks, security/negative tests and troubleshooting. Every expected value in
this guide was measured on this build.

> **Research prototype.** Outputs are not medical, legal or coverage advice.
> Never enter real patient data. All clinical evidence in this guide is
> synthetic; policy text is real, unmodified CMS National Coverage
> Determination (NCD) text.

**Contents**

1. [What you are testing](#1-what-you-are-testing)
2. [Setup](#2-setup)
3. [Five-minute smoke test](#3-five-minute-smoke-test)
4. [Automated testing](#4-automated-testing)
5. [UI walkthrough](#5-ui-walkthrough)
6. [Test-case catalogue (51 scenarios)](#6-test-case-catalogue-51-scenarios)
7. [API testing](#7-api-testing)
8. [Security & negative tests](#8-security--negative-tests)
9. [Reporting a bug](#9-reporting-a-bug)
10. [Troubleshooting](#10-troubleshooting)
11. [Known limitations](#11-known-limitations)
12. [Summary checklist](#12-summary-checklist)

---

## 1. What you are testing

Every claim runs through a five-agent pipeline and is then routed:

```
Claim + policy ─► Agent 1 Policy Logic        policy clauses → logic trees (EXCEPT/UNLESS/ONLY IF/AND/OR/NOT)
               ─► Agent 2 Clinical Evidence   active-encounter isolation, concept & ICD-10-CM extraction, negation
               ─► Agent 3 Knowledge Linker    ontology paths (KAMEL valid ancestors) + passage retrieval
               ─► Agent 4 Reconciliation      evaluates every rule against the evidence → draft decision
               ─► Agent 5 Confidence          calibrated confidence + audit brief → route
                      ├─ confidence ≥ 0.90 (APPROVE) / ≥ 0.95 (DENY) ─► Straight-Through Processing (auto-finalised)
                      └─ otherwise                                     ─► Human Review Queue
```

Vocabulary used throughout this guide:

| Term | Meaning |
|---|---|
| **Draft** | Agent 4's decision before routing: APPROVE, DENY or HUMAN_REVIEW. |
| **Final outcome** | What the system records: the draft if it went straight through, otherwise HUMAN_REVIEW. |
| **Route** | `STRAIGHT_THROUGH` (STP, auto-finalised) or `HUMAN_REVIEW_QUEUE`. |
| **Confidence** | Agent 5's calibrated score. Only drafts at/above the threshold go straight through. |
| **B1 baseline** | The older keyword engine, shown for comparison only. |

Two safety rules are worth knowing before you test:

- The pipeline **escalates rather than guesses**: missing, contradictory,
  historical-only or hypothetical evidence goes to a human.
- A **denial is never auto-finalised** when it overrides a satisfied coverage rule.

---

## 2. Setup

### 2.1 Prerequisites

- Python 3.10+ and Node.js 18+ (tested with Python 3.13 and Node 24).
- Windows PowerShell commands are shown; on macOS/Linux use the equivalent
  (`export VAR=value` instead of `$env:VAR = "value"`).

### 2.2 Install (once)

From the project root:

```powershell
python -m pip install -r requirements.txt
Set-Location frontend; npm install; Set-Location ..
```

### 2.3 Start the backend (terminal 1)

```powershell
$env:CLAIMLAB_RATE_LIMIT_REQUESTS = "1000"   # test servers only — see 10.2
python -m alembic upgrade head                # creates/updates data/claimlab.db
python -m backend                             # API on http://127.0.0.1:8000
```

Optional: `$env:CAF_MODEL_API_KEY = "<Gemini key>"` before starting enables the
Gemini comparison engine and the Agent 1/Agent 5 assists. Everything in this
guide works without it; LLM-specific checks are marked **(LLM only)**.

> **Port 8000 already in use?** Run the API on another port and point the
> frontend at it: `$env:PORT = "8010"` before `python -m backend`, and
> `$env:VITE_API_URL = "http://127.0.0.1:8010"` before `npm run dev`.

### 2.4 Start the frontend (terminal 2)

```powershell
Set-Location frontend
npm run dev                                   # UI on http://127.0.0.1:5173
```

### 2.5 Create test accounts (once)

Self-registration is disabled by design. Create three accounts:

```powershell
python scripts/create_user.py --username alice --role reviewer
python scripts/create_user.py --username bob   --role reviewer
python scripts/create_user.py --username admin --role admin
```

The script prompts for a password. This guide assumes `TestPass1234!` for all
three. You need **two reviewers** (maker-checker) and **one admin** (reopen).

---

## 3. Five-minute smoke test

- [ ] `http://127.0.0.1:8000/api/v1/health` returns `"status":"ok"` and
      `"pipeline_ready":true`.
- [ ] Open `http://127.0.0.1:5173`, sign in as **alice** — the sidebar shows
      *API operational*.
- [ ] **Case review → Load example → Run adjudication pipeline.**
      **Expected:** green banner **STRAIGHT-THROUGH PROCESSING · Finalised:
      APPROVE**, calibrated confidence **0.987**, five agent tabs.
- [ ] **Evaluation → Load real CMS policy sample → Run evaluation.**
      **Expected:** 22 evaluated, 0 errors, accuracy **100.0%**, STP rate
      **72.7%**.
- [ ] `python scripts/run_test_cases.py` ends with
      `51 cases: PASS 46, SAFE-ABSTAIN 5, CONSERVATIVE 0, FAIL 0`.

If all five pass, the installation is healthy.

---

## 4. Automated testing

| Command (project root) | What it checks | Expected |
|---|---|---|
| `python -m pytest` | Unit, API, workflow, knowledge-layer, real-data and all 51 guide scenarios (hermetic, no network) | `148 passed, 2 skipped` (live Gemini/Neo4j tests skip without credentials) |
| `ruff check backend tests scripts` | Lint | `All checks passed!` |
| `python scripts/run_test_cases.py` | 51-scenario suite, in-process | `PASS 46, SAFE-ABSTAIN 5, CONSERVATIVE 0, FAIL 0`; `Straight-through: 27 (27 correct)` |
| `python scripts/run_test_cases.py --api http://127.0.0.1:8000` | Same suite over HTTP against a running server | Same numbers |
| `python scripts/load_knowledge_base.py` | Knowledge layer seeds and traverses | 482 term vectors, 105 corpus passages, exit code 0 |
| `Set-Location frontend; npm run build` | TypeScript type-check + production build | `✓ built` |

### 4.1 The scenario runner

```powershell
python scripts/run_test_cases.py                 # all 51 scenarios
python scripts/run_test_cases.py --only D        # one category (A–G) or id prefix
python scripts/run_test_cases.py --verbose       # rationale + routing reasons per case
python scripts/run_test_cases.py --show A04      # print a case's inputs to type into the UI
python scripts/run_test_cases.py --export-eval guide_eval.jsonl   # JSONL for the Evaluation tab
python scripts/run_test_cases.py --json results.json              # full machine-readable results
```

Each scenario is classified as:

| Result | Meaning | Acceptable? |
|---|---|---|
| **PASS** | Final outcome = expected outcome. | ✅ |
| **SAFE-ABSTAIN** | Expected APPROVE/DENY; the **draft was right** but confidence was below the STP threshold, so it went to a human. | ✅ (by design) |
| **CONSERVATIVE** | Expected APPROVE/DENY; the pipeline escalated with a HUMAN_REVIEW draft. Safe, but a miss. | ⚠️ investigate |
| **FAIL** | A wrong automated APPROVE/DENY, or an automated decision where a human was expected. | ❌ bug |

The runner exits with code 1 if any scenario FAILs, and
`tests/test_guide_cases.py` fails the build on FAIL or CONSERVATIVE.

### 4.2 Adding your own scenarios

Add a `case(...)` entry to [`scripts/build_test_cases.py`](scripts/build_test_cases.py)
(write the expected outcome **before** you run it), then:

```powershell
python scripts/build_test_cases.py     # regenerates data/test_cases/guide_test_cases.jsonl
python scripts/run_test_cases.py --only <your id> --verbose
```

---

### 4.3 Testing a deployed website

Everything above also works against a site deployed with [`deploy/`](deploy/README.md).
Point the runner at the site root; nginx forwards `/api/` to the API:

```powershell
python scripts/run_test_cases.py --api https://your-name.duckdns.org
```

Expected: the same numbers as in-process (46 PASS, 5 SAFE-ABSTAIN, 0 FAIL;
27/27 straight-through correct). Deployed sites keep the default limit of 60
write requests per minute per IP. If the server answers **429**, the runner
waits for the `Retry-After` period and continues.

Quick deployed-site checks:

| Check | How | Expected |
|---|---|---|
| HTTPS redirect | `curl -sI http://your-name.duckdns.org` | `308` to `https://?` |
| Security headers | `curl -sI https://your-name.duckdns.org/api/v1/health` | One each of `Strict-Transport-Security`, `Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` |
| Backends | `curl -s https://your-name.duckdns.org/api/v1/config` | `environment: production`; `knowledge_fallbacks: []`; `vector_store`/`graph_store` = `memory` (lite) or `qdrant`/`neo4j` (full) |
| Demo login (public demo mode) | Open the site | Login page shows *Use demo-maker* / *Use demo-checker*; a banner appears after sign-in |
| Maker-checker | Submit a case as demo-maker, then decide it as demo-maker and as demo-checker | demo-maker is blocked (403 *"Segregation of duties…"*); demo-checker can decide |
| Rate limit | More than 60 writes in a minute from one IP | `429` with `Retry-After` |

## 5. UI walkthrough

Sign in as **alice** unless a step says otherwise. Values below were measured
on this build; confidence values are deterministic (without Gemini).

### 5.1 Login

- [ ] **Wrong password** → red alert *"Incorrect username or password"*.
- [ ] **API stopped** (stop terminal 1, try to sign in) → *"Could not reach
      the API. Check that the backend is running…"*. Restart the API.
- [ ] **Correct login** → workspace with **Case review / Evaluation / Review
      queue / Model & data**; reload (F5) keeps you signed in.
- [ ] **Sign out** returns to the login screen.

### 5.2 How to read the Case review screen

After **Run adjudication pipeline**, the right panel shows, top to bottom:

1. **Route banner** — green/red *STRAIGHT-THROUGH PROCESSING · Finalised:
   APPROVE/DENY* or amber *HUMAN REVIEW QUEUE* with the reasons; the bar shows
   calibrated confidence and a tick at the threshold.
2. **Agent 4 draft decision** card with its rationale.
3. Confidence, processing time and **Case reference** (the optional field at
   the top of the form — it is printed on the audit brief).
4. **B1 lexical baseline** chip — purple when it disagrees with the pipeline.
5. **Agent trace** — five tabs:
   - *Policy Logic*: parsed rules, each expandable to its logic tree.
   - *Clinical Evidence*: active vs excluded sentences, and every concept with
     code and assertion (present / negated / historical / family /
     hypothetical / undocumented). Spelling-corrected words show a
     `spelling ≈` tag.
   - *Knowledge*: "Clinical evidence → policy condition" and "Claimed
     diagnosis ↔ policy indication" ontology paths, rule relevance, and
     retrieved passages.
   - *Reconciliation*: each rule's TRUE / FALSE / UNKNOWN with per-condition
     reasons.
   - *Confidence*: factor bars, calibration note and the **one-page audit brief**.

### 5.3 Hand-typed scenarios

Type each case into **Case review** (fields not listed: leave *Case
reference* empty or use the ID; *Evidence completeness* = Documented unless
stated). For any catalogue case not listed here, run
`python scripts/run_test_cases.py --show <ID>` to print its inputs.

**A01 — simple coverage, specific diagnosis**
- Diagnosis `primary open-angle glaucoma` · Service `optical coherence tomography`
- Policy `Optical coherence tomography is covered for patients with glaucoma.`
- Evidence `Primary open-angle glaucoma confirmed on exam; optical coherence tomography ordered.`
- [ ] **Expected:** STRAIGHT-THROUGH · **APPROVE** · 0.990. *Knowledge* tab:
      `Primary open-angle glaucoma → Open-angle glaucoma → Glaucoma (valid ancestor)`.

**A02 — unconditional exclusion**
- Diagnosis `myopia` · Service `eyeglasses` · Policy `Eyeglasses are not covered.`
- Evidence `Patient requests new eyeglasses to correct myopia.`
- [ ] **Expected:** STRAIGHT-THROUGH · **DENY** · 0.964.

**A04 — EXCEPT clause, exception not met**
- Diagnosis `myopia` · Service `contact lenses`
- Policy `Contact lenses are not covered except for patients with aphakia.`
- Evidence `Healthy eyes; contact lenses requested to correct myopia.`
- [ ] **Expected:** STRAIGHT-THROUGH · **DENY** · 0.960. *Policy Logic* tab:
      `EXCEPT(TRUE, aphakia)`. B1 chip: *HUMAN REVIEW — differs*.
- [ ] Change the evidence to `Patient is aphakic after cataract extraction;
      contact lenses prescribed.` and the diagnosis to `aphakia` (this is A03).
      **Expected:** HUMAN REVIEW QUEUE (*no rule satisfied*) — the exception
      lifts the exclusion but nothing explicitly grants coverage, so the
      pipeline escalates rather than approves.

**A07 / A08 — ONLY IF met / not met**
- Service `corneal pachymetry` · Policy `Corneal pachymetry is covered only when glaucoma is documented.`
- [ ] A07: Diagnosis `glaucoma`, Evidence `Open-angle glaucoma documented by the
      treating ophthalmologist; corneal pachymetry performed.` → STRAIGHT-THROUGH
      · **APPROVE** · 0.989.
- [ ] A08: Diagnosis `routine eye examination`, Evidence `Routine eye
      examination; no glaucoma; corneal pachymetry requested.` → STRAIGHT-THROUGH
      · **DENY** · 0.962, reason *only-if condition failed* (B1 wrongly approves).

**B01 / B02 / B03 — quantities**
- Diagnosis `neovascular age-related macular degeneration` · Service `photodynamic therapy`
- Policy `Photodynamic therapy is covered only when the lesion is 4 disk areas or less in size.`
- [ ] B01: Evidence `…; the lesion measures 3 disk areas.` → **APPROVE** ·
      straight-through · 0.964. *Policy Logic* shows a `<= 4 disk area` badge.
- [ ] B02: `…; the lesion measures 5.5 disk areas.` → **DENY** · straight-through;
      *Reconciliation*: *"Documented 5.5 disk area violates <= 4."*
- [ ] B03: `…; lesion size is not documented.` → HUMAN REVIEW QUEUE
      (*missing evidence*) — measurements are never assumed.

(Use the full sentence `Neovascular age-related macular degeneration; the lesion measures 3 disk areas.` etc.)

**C01 / C02 / C03 — evidence completeness** (policy = NCD 80.9, run `--show C01` to copy it)
- Diagnosis `glaucoma` · Service `computer enhanced perimetry`
- Evidence `Glaucoma documented; computer enhanced perimetry ordered to assess the visual fields.`
- [ ] C01 Documented → STRAIGHT-THROUGH · **APPROVE** · 0.986.
- [ ] C02 *Incomplete* → HUMAN REVIEW QUEUE, **draft APPROVE**, 0.867, reasons:
      *below the APPROVE STP threshold* and *Clinical evidence was not declared complete*.
- [ ] C03 *Conflicting* → HUMAN REVIEW QUEUE, draft HUMAN_REVIEW (*contradiction*), 0.182.

**D-series — clinical assertions** (same NCD 80.9 policy and service as C01)
- [ ] D01 Evidence `No evidence of glaucoma or other neuropathologic defect on examination.`
      → queue, *contradiction* (claimed diagnosis negated).
- [ ] D02 `Exam: optic nerves healthy, intraocular pressure normal. Family history: father has glaucoma.`
      → queue; *Clinical Evidence* tab shows the family-history sentence as excluded.
- [ ] D03 `Rule out glaucoma; perimetry requested.` → queue; glaucoma marked **hypothetical**.
- [ ] D07 `Advanced glaucomma confirmed by the treating ophthalmologist; perimetry ordered.`
      → STRAIGHT-THROUGH · **APPROVE**; *Clinical Evidence* shows `"glaucomma"`
      → Glaucoma H40 with a **spelling ≈ 0.94** tag.

**G01 / G04 — robustness**
- [ ] G01: all inputs in CAPITALS (`GLAUCOMA`, `GLAUCOMA IMAGING`,
      `GLAUCOMA IMAGING IS COVERED WHEN MEDICALLY NECESSARY.`,
      `DOCUMENTED GLAUCOMA; GLAUCOMA IMAGING IS MEDICALLY NECESSARY.`) → **APPROVE** straight-through.
- [ ] G04: Diagnosis `<script>alert(1)</script> & "quoted"`, Service
      `O'Brien's <b>procedure</b>`, Policy
      `Coverage is <i>covered</i> when "medically necessary"; see § 80.1 — <script>evil()</script>.`,
      Evidence `Evidence includes <img src=x onerror=alert(1)> and unicode: café, 日本語, emoji 🎉.`
      → **no alert box appears**; tags render as plain text; HUMAN REVIEW QUEUE (*no applicable rule*).
- [ ] Empty required fields → the browser blocks submission (no request sent).

### 5.4 Real CMS policy: the false-approval case

- [ ] **Evaluation → Load real CMS policy sample** fills 22 cases. To run one
      on Case review, copy the `ncd-80.2-opt-occult-large-lesion-no-progression`
      line's fields into the form (or use the API, section 7).
- [ ] **Expected:** STRAIGHT-THROUGH · **DENY** · 0.990, B1 chip *APPROVE —
      differs*. *Policy Logic*: 13 rules; expand **R11** to see `ONLY IF` →
      `AND(lesions are small (<= 4 disk area), OR(deterioration of visual
      acuity, lesion growth, appearance of blood…))`. *Reconciliation*: R11
      **ONLY-IF failed** because 6 disk areas violates ≤ 4.

### 5.5 Evaluation workbench

- [ ] **Load real CMS policy sample → Engine: Five-agent pipeline → Run evaluation.**
      22 evaluated · accuracy **100.0%** · false approval **0.0%** · human
      review **27.3%** · STP rate **72.7%** / STP accuracy **100.0%**.
- [ ] **Engine: B1 lexical baseline → Run evaluation.** Accuracy **54.5%**,
      false approval rate **40.0%**, no STP metrics.
- [ ] **Engine: Compare engines → Run comparison.** Table with both engines
      and **10 disagreements** (e.g. `ncd-80.7-cosmetic-keratotomy`: pipeline
      DENY · b1 HUMAN REVIEW).
- [ ] **Full 51-scenario suite in the UI:** run
      `python scripts/run_test_cases.py --export-eval guide_eval.jsonl`, open
      the file, copy everything, paste it into the JSONL box.
  - Five-agent pipeline: 51 evaluated · accuracy **90.2%** · macro F1
    **89.6%** · false approval **0.0%** · false denial **0.0%** · human review
    **47.1%** · STP rate **52.9%** / STP accuracy **100.0%** · draft accuracy
    **98.0%**. (Accuracy counts the 5 safe abstentions as HUMAN_REVIEW; draft
    accuracy's one miss is C02, whose correct APPROVE draft was routed to a
    human because evidence was declared incomplete.)
  - Compare engines: pipeline **90.2%** vs B1 **60.8%**; false approvals
    **0.0%** vs **42.9%**; **20 disagreements**.
- [ ] **Malformed JSONL** (add a line `{not json}`) → red alert *"Line N is
      not valid JSON…"*; nothing is sent.

### 5.6 Review queue — routing, maker-checker, reopen

1. [ ] **STP case.** As **alice**: *Review queue → Submit case for review*,
   enter C01 (documented) → status **Auto-decided (STP)**, green banner
   *Finalised: APPROVE*, blue card *Finalised by straight-through processing*,
   **no decision form**. Expand *Agent trace & audit brief*.
2. [ ] **Human-routed case.** Submit C01 again with *Incomplete* → status
   **Pending review**, amber banner (0.867) with the routing reasons, card
   *PIPELINE DRAFT — A REVIEWER RECORDS THE DECISION: APPROVE*.
3. [ ] **Segregation of duties.** Still as alice, open that pending case →
   *"Segregation of duties … Another reviewer must complete this step."* — no form.
4. [ ] **Second reviewer.** Sign in as **bob** → *Human queue* filter → open the
   case → choose *Escalated for further review*, add a note → **Record final
   decision** → *Human decision recorded: ESCALATED by bob*.
5. [ ] **Immutability.** The form is gone; a second decision via the API
   returns **409** (section 7).
6. [ ] **Reopen requires admin.** As bob, *Reopen (admin only)* → *"Requires
   one of roles: admin"*.
7. [ ] **Admin reopens an STP case.** As **admin**, *Straight-through* filter →
   open the auto-decided case → *Reopen for human review* → status **Pending
   review**, banner **REOPENED BY ADMIN · ORIGINALLY STRAIGHT-THROUGH**, and
   the decision form appears.
8. [ ] **Admin self-review.** As admin, submit a case and open it — admins are
   exempt from maker-checker, so the **decision form is shown**.
9. [ ] **Filters.** *All*, *Human queue* (pending only), *Straight-through*
   (auto-decided, final decision `… (STP)`), *Reviewed* (human decisions,
   e.g. `APPROVE (human)`). *Refresh* re-fetches without errors.

### 5.7 Model & data

- [ ] **Adjudication architecture** diagram: input → Agents 1–5 → STP
      (APPROVE ≥ 0.9 · DENY ≥ 0.95) and Human Review Queue.
- [ ] **Inference configuration:** `multi_agent_pipeline` (5 AGENTS), STP
      thresholds (ENABLED), calibration `identity (n=16)`, embedder
      `hashing-char-ngram-v1`, vector store / graph store `memory` (or
      `qdrant:…` / `neo4j:…` when configured), ontology
      `claimlab-eye-ontology@1.0.0 (133 concepts)`, Gemini *NO LLM* or
      *ASSIST ON*.
- [ ] **Dataset availability:** 22 real-policy research cases.
- [ ] **Engines & baselines:** pipeline Active, B1 Available, B2 Unavailable,
      B3 Available only with a Gemini key.
- [ ] **Readiness checklist:** 2 items Met, 4 Not met.

### 5.8 Layout

- [ ] Resize the browser to ~800 px wide: no horizontal scrollbar on any
      page, the sidebar never overlaps content, and the Evaluation controls
      wrap under the textbox.
- [ ] Below 760 px the sidebar becomes a top bar.

---

## 6. Test-case catalogue (51 scenarios)

Source: [`data/test_cases/guide_test_cases.jsonl`](data/test_cases/guide_test_cases.jsonl)
(generated by [`scripts/build_test_cases.py`](scripts/build_test_cases.py)).
"This build" shows final outcome · route · calibrated confidence. Print any
case's inputs with `python scripts/run_test_cases.py --show <ID>`.

Expected outcomes reflect a careful reading of the policy text **and** the
system's deliberately conservative design (for example, a triggered
UNLESS/EXCEPT escalates rather than denies). They were written before the
suite was first run. The first run found 9 conservative misses, which led to
four general fixes (quantity phrases like "4 disk areas or less", strict
spelling correction, "X due to Y" reasoning, and the diagnosis-link guard)
and one ontology addition. It also surfaced two unsafe regressions during
that work, which were fixed by never treating historical or hypothetical
findings as absent and never counting the requested service as clinical
evidence. The suite is therefore not a held-out benchmark.

#### A · Policy logic (Agent 1)

| ID | Scenario | What it checks | Expected | This build |
|---|---|---|---|---|
| A01 | Simple coverage + specific diagnosis | COVERED rule; H40.11 satisfies 'glaucoma' via KAMEL valid ancestor | APPROVE | APPROVE · straight-through · 0.99 |
| A02 | Unconditional exclusion | NOT_COVERED rule with TRUE condition | DENY | DENY · straight-through · 0.96 |
| A03 | EXCEPT clause — exception met | Exception lifts the exclusion but nothing grants coverage → conservative escalation | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.98 |
| A04 | EXCEPT clause — exception not met | EXCEPT(TRUE, aphakia) with aphakia absent from a complete record → exclusion applies | DENY | DENY · straight-through · 0.96 |
| A05 | UNLESS clause — not triggered | UNLESS(TRUE, enucleated) with no enucleation documented → coverage | APPROVE | APPROVE · straight-through · 0.99 |
| A06 | UNLESS clause — triggered | Coverage condition fails; no explicit exclusion → conservative escalation | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.94 |
| A07 | ONLY IF — condition met | Only-if rule is the whole coverage criterion | APPROVE | APPROVE · straight-through · 0.99 |
| A08 | ONLY IF — condition not met | Only-if condition fails → denial | DENY | DENY · straight-through · 0.96 |
| A09 | AND — both conditions met | AND(corneal edema, about to undergo cataract surgery) | APPROVE | APPROVE · straight-through · 0.99 |
| A10 | AND — one condition negated | One AND branch FALSE → no rule satisfied | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.98 |
| A11 | OR list via ontology | E11.35 proliferative diabetic retinopathy is-a diabetic retinopathy | APPROVE | APPROVE · straight-through · 0.99 |
| A12 | Specific exclusion vs general coverage | Exclusion satisfied, coverage condition absent → denial | DENY | DENY · straight-through · 0.99 |

#### B · Quantities (Agents 1/2/4)

| ID | Scenario | What it checks | Expected | This build |
|---|---|---|---|---|
| B01 | Quantity within limit | ≤ 4 disk areas satisfied by 3 | APPROVE | APPROVE · straight-through · 0.96 |
| B02 | Quantity over limit | ≤ 4 disk areas violated by 5.5 → only-if fails | DENY | DENY · straight-through · 0.96 |
| B03 | Quantity not documented | Measurements are never assumed (no closed-world for quantities) | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.82 |
| B04 | Percentage threshold met | ≤ 88 % satisfied by 85 % | APPROVE | HUMAN REVIEW · human queue · 0.81 · **SAFE-ABSTAIN** (draft APPROVE) |
| B05 | Percentage threshold not met | ≤ 88 % violated by 93 % | DENY | HUMAN REVIEW · human queue · 0.81 · **SAFE-ABSTAIN** (draft DENY) |
| B06 | Duration threshold | 'at least 3 months' satisfied by 4 months | APPROVE | HUMAN REVIEW · human queue · 0.81 · **SAFE-ABSTAIN** (draft APPROVE) |

#### C · Evidence completeness (Agents 4/5)

| ID | Scenario | What it checks | Expected | This build |
|---|---|---|---|---|
| C01 | Complete evidence → straight-through | Baseline for C02/C03 | APPROVE | APPROVE · straight-through · 0.99 |
| C02 | Same evidence declared incomplete | Draft APPROVE but routed to the human queue | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.87 |
| C03 | Same evidence declared conflicting | Contradiction → human queue | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.18 |

#### D · Clinical assertions & ontology (Agents 2/3)

| ID | Scenario | What it checks | Expected | This build |
|---|---|---|---|---|
| D01 | Negated diagnosis | Claimed diagnosis negated by evidence → contradiction | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.43 |
| D02 | Family history only | Family-history section is excluded from the active encounter | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.89 |
| D03 | Hypothetical diagnosis | 'Rule out glaucoma' is hypothetical, not a diagnosis | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.89 |
| D04 | Explicitly undocumented | 'not documented' → UNKNOWN, missing evidence | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.89 |
| D05 | Past history only | PMH section is historical, not the active encounter | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.89 |
| D06 | Specific subtype via valid ancestor | H40.2 angle-closure glaucoma is-a H40 glaucoma | APPROVE | APPROVE · straight-through · 0.99 |
| D07 | Misspelled term | Strict spelling-variant match of 'glaucomma' to glaucoma (H40) | APPROVE | APPROVE · straight-through · 0.99 |
| D08 | Other neuropathologic defect | Multiple sclerosis is-a neuropathologic defect | APPROVE | APPROVE · straight-through · 0.99 |

#### E · Real CMS NCD policies

| ID | Scenario | What it checks | Expected | This build |
|---|---|---|---|---|
| E01 | 80.5 scleral shell for dry eye | 'covered as a prosthetic device … treatment of dry eye' | APPROVE | APPROVE · straight-through · 0.98 |
| E02 | 80.6 photography for macular degeneration | Listed condition | APPROVE | APPROVE · straight-through · 0.98 |
| E03 | 80.6 photography with no listed condition | Closed-world: no listed condition present | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.54 |
| E04 | 80.8 endothelial photography for cornea guttata | Bulleted criteria list | APPROVE | APPROVE · straight-through · 0.96 |
| E05 | 80.11 vitrectomy for proliferative diabetic retinopathy | E11.35 is-a proliferative retinopathy | APPROVE | APPROVE · straight-through · 0.98 |
| E06 | 80.11 vitrectomy for vitreous hemorrhage | 'vitreous opacities due to vitreous hemorrhage': documented cause implies the effect | APPROVE | APPROVE · straight-through · 0.98 |
| E07 | 80.4 contact lens for keratoconus | Neither the exclusion nor the aphakia coverage applies | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.97 |
| E08 | 80.3 verteporfin, predominantly classic CNV | Covered indication | APPROVE | APPROVE · straight-through · 0.97 |
| E09 | 80.3 verteporfin for pathologic myopia | Contractor (MAC) discretion → human review | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.96 |
| E10 | 80.2 juxtafoveal CNV | Nationally non-covered indication | DENY | DENY · straight-through · 0.98 |
| E11 | 80.7 LASIK for myopia | LASIK is-a keratomileusis is-a refractive keratoplasty (Not Covered banner) | DENY | DENY · straight-through · 0.99 |
| E12 | 80.10 phaco without documented cataract | Coverage requires cataract extraction context | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.94 |
| E13 | 80.1 bandage lens for corneal erosion | Listed corneal pathology | APPROVE | APPROVE · straight-through · 0.98 |
| E14 | 80.12 IOL after cataract surgery | Covered if reasonable and necessary | APPROVE | APPROVE · straight-through · 0.98 |

#### F · Outside the eye ontology

| ID | Scenario | What it checks | Expected | This build |
|---|---|---|---|---|
| F01 | Non-eye coverage (lexical fallback) | No ontology concepts; lexical matching of conditions | APPROVE | HUMAN REVIEW · human queue · 0.81 · **SAFE-ABSTAIN** (draft APPROVE) |
| F02 | Non-eye cosmetic exclusion | Unconditional exclusion of a cosmetic service | DENY | HUMAN REVIEW · human queue · 0.81 · **SAFE-ABSTAIN** (draft DENY) |
| F03 | Irrelevant policy | No rule concerns the requested service | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.82 |

#### G · Robustness & safety

| ID | Scenario | What it checks | Expected | This build |
|---|---|---|---|---|
| G01 | All-caps input | Case-insensitive parsing and matching | APPROVE | APPROVE · straight-through · 0.99 |
| G02 | Noisy multi-section note | Template, prior encounter, family history, signature and copy-forward are excluded | APPROVE | APPROVE · straight-through · 0.99 |
| G03 | Contradiction inside the active note | Concept both affirmed and negated → contradiction | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.25 |
| G04 | HTML / script / unicode input | Rendered as text, never executed; no crash | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.43 |
| G05 | Minimal evidence | Nearly empty evidence never auto-approves | HUMAN REVIEW | HUMAN REVIEW · human queue · 0.89 |

**Totals:** 46 PASS · 5 SAFE-ABSTAIN · 0 CONSERVATIVE · 0 FAIL. All 27
straight-through decisions are correct. The B1 baseline matches the expected
outcome on 31/51.

---

## 7. API testing

Interactive docs: `http://127.0.0.1:8000/docs` (disabled in production).
PowerShell examples (`$API = "http://127.0.0.1:8000"`):

```powershell
# Health and configuration
Invoke-RestMethod "$API/api/v1/health"
(Invoke-RestMethod "$API/api/v1/config").pipeline

# Stateless adjudication (no login)
$case = @{ case_id = "api-demo"; diagnosis = "glaucoma"; requested_service = "glaucoma imaging"
           policy_text = "Glaucoma imaging is covered when medically necessary."
           clinical_evidence = "Documented glaucoma imaging is medically necessary."
           evidence_state = "documented" } | ConvertTo-Json
$r = Invoke-RestMethod -Method Post "$API/api/v1/adjudications" -ContentType "application/json" -Body $case
$r.decision; $r.route; $r.confidence; $r.baseline.decision; $r.audit_brief
# → APPROVE, STRAIGHT_THROUGH, ~0.987, APPROVE, "# Claim adjudication audit brief…"

# Login and create a persisted review
$tok = (Invoke-RestMethod -Method Post "$API/api/v1/auth/login" -Body @{ username = "alice"; password = "TestPass1234!" }).access_token
$auth = @{ Authorization = "Bearer $tok" }
$rev = Invoke-RestMethod -Method Post "$API/api/v1/reviews" -Headers $auth -ContentType "application/json" -Body $case
$rev.status; $rev.final_decision; $rev.decision_source       # → auto_decided, APPROVE, stp
Invoke-RestMethod "$API/api/v1/reviews?status=auto_decided" -Headers $auth

# Bulk evaluation / engine comparison from a JSONL file
python scripts/run_test_cases.py --export-eval guide_eval.jsonl
$cases = Get-Content guide_eval.jsonl -Encoding utf8 | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json }
$body = [Text.Encoding]::UTF8.GetBytes((@{ cases = @($cases) } | ConvertTo-Json -Depth 10))
$cmp = Invoke-RestMethod -Method Post "$API/api/v1/evaluations/compare" -ContentType "application/json; charset=utf-8" -Body $body
$cmp.engines.multi_agent_pipeline.accuracy; $cmp.engines.b1_lexical.accuracy; $cmp.disagreements.Count
# → 0.902, 0.608, 20
```

Use `"engine": "b1_lexical"` (or `"llm_rag"` with a Gemini key) in a
`POST /api/v1/evaluations` body to score a single engine.

---

## 8. Security & negative tests

| # | Test | How | Expected |
|---|---|---|---|
| 8.1 | Labels can't leak into inference | POST `/api/v1/adjudications` with an extra `"ground_truth":"DENY"` field | **422** |
| 8.2 | Protected endpoints need a token | `Invoke-RestMethod "$API/api/v1/reviews"` without a header | **401** |
| 8.3 | No decisions on STP cases | POST `/reviews/{id}/decision` on an `auto_decided` case | **409** *"…finalised by straight-through processing…"* |
| 8.4 | No double decisions | Record a decision twice on a human-reviewed case | **409** |
| 8.5 | Maker-checker | Creator (non-admin) records a decision on their own case | **403** *"Segregation of duties…"* |
| 8.6 | Admin-only reopen | Reviewer calls `/reviews/{id}/reopen` | **403** |
| 8.7 | Request size limit | POST a 5 MB body to `/api/v1/adjudications` | **413** |
| 8.8 | Rate limiting | >60 write requests/minute from one IP at default settings | **429** *"Rate limit exceeded"* |
| 8.9 | XSS | Scenario G04 in the UI | No script runs; text shown literally |
| 8.10 | Security headers | `curl.exe -i $API/api/v1/health` | `x-content-type-options: nosniff`, `x-frame-options: DENY`, `x-request-id` |
| 8.11 | Production hardening | `$env:CLAIMLAB_ENVIRONMENT="production"` with the default JWT secret | API refuses to start; `/docs` disabled when a real secret is set |

---

## 9. Reporting a bug

Please include:

1. **Scenario** — catalogue ID (or the exact inputs; use `--show <ID>`).
2. **Expected vs actual** — final outcome, draft, route and confidence.
3. **Agent trace** — the tab where it goes wrong (e.g. *Reconciliation: R2
   evaluated FALSE but the evidence documents X*). The *Confidence* tab's
   **audit brief** is a convenient copy-paste summary.
4. **Environment** — `/api/v1/config` → `pipeline.knowledge_backends`,
   `llm_enabled`, browser and window width (for layout issues).
5. Output of `python scripts/run_test_cases.py --only <ID> --verbose`.

Treat any **FAIL** (wrong automated decision) as high priority.

---

## 10. Troubleshooting

| # | Symptom | Cause / fix |
|---|---|---|
| 10.1 | UI says *API unavailable* / login says *Could not reach the API* | Backend not running, or the frontend points at another port: set `VITE_API_URL` and restart `npm run dev`. |
| 10.2 | **429 Rate limit exceeded** after running the scenario suite with `--api` | Expected at default settings (60 writes/min per IP). For test servers start the API with `$env:CLAIMLAB_RATE_LIMIT_REQUESTS = "1000"`, or wait a minute. |
| 10.3 | `error while attempting to bind … 8000` | Port in use by another app — use `$env:PORT` (see 2.3). Just after stopping the API, wait a few seconds for the port to free. |
| 10.4 | CORS errors in the browser console | Add your UI origin to `CLAIMLAB_ALLOWED_ORIGINS` on the backend. |
| 10.5 | Vite crashes with `EBUSY … watch` | Windows file lock on a file being copied into `frontend/`; restart `npm run dev`. |
| 10.6 | *Model & data* shows a *Knowledge backend fallback* notice | Qdrant/Neo4j/SBioBERT was configured but unreachable or not installed; the in-memory backend is used. Results are unaffected; see README "Knowledge layer". |
| 10.7 | **(LLM only)** Gemini card shows *certificate verify failed* | A TLS-inspecting proxy's root CA is missing from Python's trust store; set `SSL_CERT_FILE` to a bundle including it. |
| 10.8 | **(LLM only)** Gemini card shows an HTTP 404/400 error | The model id was retired or rejects `thinkingBudget: 0`; set `CAF_MODEL_ANALYSIS` (see README). The pipeline itself is unaffected. |
| 10.9 | Session ends unexpectedly | JWT expired (default 30 min, `CLAIMLAB_JWT_ACCESS_TOKEN_MINUTES`); sign in again. |

---

## 11. Known limitations

- **Domain:** the ontology covers the CMS NCD 80.x eye chapter. Outside it
  (F-series), Agents 3–4 fall back to lexical matching and decisions are
  routed to a human (SAFE-ABSTAIN) unless the claimed diagnosis can be
  linked to the policy.
- **Conservative escalation:** a triggered UNLESS/EXCEPT, an exception that
  merely lifts an exclusion, MAC-discretion rules, historical-only or
  hypothetical findings, and contradictions all go to a human rather than
  being decided automatically.
- **Calibration:** confidence uses the identity map because the labelled sample
  has no incorrect decisive drafts to fit against (see
  `data/calibration/pipeline_calibration.json`); treat it as indicative.
- **Labels:** all expected outcomes are project-authored research
  constructs, not certified coverage determinations.
- **LLM:** Gemini output varies between runs (no seed parameter); the
  pipeline's own results are deterministic.

---

## 12. Summary checklist

| # | Area | Scenarios |
|---|---|---|
| 3 | Smoke test | health, login, example STP approve, CMS sample, scenario suite |
| 4 | Automated | pytest (148), lint, scenario runner in-process and over HTTP, knowledge seeding, frontend build |
| 5.1 | Login | wrong password, API down, login, persistence, sign out |
| 5.3 | Case review | coverage, exclusion, EXCEPT, ONLY IF, quantities, evidence completeness, assertions, spelling, caps, XSS, validation |
| 5.4 | Real policy | NCD 80.2 large-lesion denial vs B1 false approval |
| 5.5 | Evaluation | CMS sample (pipeline 100% / B1 54.5%), compare, full suite (90.2% vs 60.8%), malformed JSONL |
| 5.6 | Review queue | STP auto-decision, human queue, maker-checker, second reviewer, immutability, reopen, admin self-review, filters |
| 5.7–5.8 | Model & data, layout | architecture, configuration, datasets, engines, readiness, narrow-window layout |
| 6 | Catalogue | 51 scenarios A–G: 46 PASS, 5 SAFE-ABSTAIN, 0 FAIL |
| 7–8 | API & security | REST calls, bulk compare, 401/403/409/413/422/429, XSS, headers, production guard |

When every box is ticked and the scenario runner reports **0 FAIL**, the
application is working end to end.

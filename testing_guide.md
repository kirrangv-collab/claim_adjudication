# ClaimLab — End-to-End UI Testing Guide

This guide walks through **every workflow** in the Claim Adjudication Research
Lab UI, step by step, with exact inputs to type and the exact result you
should see. Follow it top to bottom in a browser at **http://127.0.0.1:5173/**.

Each test has a checkbox. Tick it off once the actual result matches the
expected result.

---

## 0. Prerequisites — start the servers

Open two terminals from the project root.

**Terminal 1 — backend (FastAPI):**
```powershell
$env:CAF_MODEL_API_KEY = "<your Gemini API key>"   # omit to test with LLM engine disabled
python -m alembic upgrade head                      # one-time / after pulling migrations
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

**Terminal 2 — frontend (Vite):**
```powershell
cd frontend
npm install     # first time only
npm run dev
```

Open **http://127.0.0.1:5173/** in your browser.

> **Corporate proxy / SSL note:** if the LLM signal shows "certificate verify
> failed", your machine's TLS-inspecting proxy root CA isn't in Python's
> `certifi` bundle. Build a combined CA bundle from the Windows trust store
> and set `SSL_CERT_FILE` to it before starting uvicorn (see chat history for
> the exact script) — this is an environment fix, not a code change.

### Test accounts

Accounts are provisioned out-of-band (self-registration is disabled). If they
don't already exist, create them:
```powershell
python scripts/create_user.py --username alice --role reviewer
python scripts/create_user.py --username bob   --role reviewer
python scripts/create_user.py --username admin --role admin
```
This guide assumes:
| Username | Role | Password |
|---|---|---|
| `alice` | reviewer | `TestPass1234!` |
| `bob` | reviewer | `TestPass1234!` |
| `admin` | admin | `TestPass1234!` |

You need **two different reviewer accounts** (`alice`, `bob`) to test the
maker-checker workflow, plus `admin` for admin-only actions.

---

## 1. Login screen

- [ ] **1.1 — Invalid credentials.** Go to `/`. Enter username `alice`,
  password `wrongpassword`. Click **Sign in**.
  **Expected:** red alert banner "Incorrect username or password". Page stays
  on login screen.

- [ ] **1.2 — Valid login.** Enter username `alice`, password
  `TestPass1234!`. Click **Sign in**.
  **Expected:** redirected to the workspace; sidebar/topbar shows nav items
  **Case review / Evaluation / Review queue / Model & data** and a
  **Sign out (alice)** button.

- [ ] **1.3 — Session persists on reload.** Reload the page (F5).
  **Expected:** still signed in as alice (no redirect to login).

- [ ] **1.4 — Sign out.** Click **Sign out (alice)**.
  **Expected:** returns to the login screen.

Log back in as **alice** before continuing to Section 2.

---

## 2. Case review — deterministic rules + Gemini LLM/RAG signal

Go to the **Case review** tab. This screen is stateless (nothing is saved) —
it's for exploring how the rule engine and the optional LLM engine respond to
different inputs.

For every case below:
1. Clear each field and type the exact values given (clearing is important —
   leftover text from the previous case will contaminate the result).
2. Set **Evidence completeness** as specified.
3. Click **Run research review**.
4. Compare the **RULE-BASED RESEARCH OUTPUT** card and, if an LLM key is
   configured, the **LLM + RAG COMPARISON SIGNAL** card against the expected
   result.

> **Note on the LLM column:** the rule-based decision, heuristic score, and
> rationale are 100% deterministic and will match exactly every time. The
> Gemini wording and exact confidence number can vary slightly between runs
> (there's no `seed` parameter on Gemini's API — see README), so judge the
> LLM card by its **direction** (approve/deny/human-review lean) and the
> **quoted policy sentence**, not by matching its sentence word-for-word.

### 2.1 — Clean APPROVE (coverage marker + matching documented evidence)
- [ ] Diagnosis: `glaucoma`
- [ ] Requested service: `glaucoma imaging`
- [ ] Policy language: `Glaucoma imaging is covered when medically necessary.`
- [ ] Clinical evidence: `Documented glaucoma imaging is medically necessary.`
- [ ] Evidence completeness: `Documented`
- [ ] **Expected rule-based result:** `APPROVE` ("Coverage-supporting signal
  detected"), heuristic score **75/100**.
- [ ] **Expected LLM result (if enabled):** leans toward **approval**, with a
  quoted-policy match and a green "verified" check mark next to the quote.

*(Tip: the "Load example" button fills exactly this case for you.)*

### 2.2 — Explicit exclusion → DENY
- [ ] Diagnosis: `myopia`
- [ ] Requested service: `radial keratotomy`
- [ ] Policy language: `Radial keratotomy for correction of refractive errors such as myopia is a cosmetic procedure and is specifically excluded from coverage under this policy.`
- [ ] Clinical evidence: `Patient requests radial keratotomy to correct myopia for cosmetic reasons; no medical necessity documented.`
- [ ] Evidence completeness: `Documented`
- [ ] **Expected rule-based result:** `HUMAN_REVIEW` at 65/100 — the lexical
  engine does **not** reliably catch this exclusion wording (this is a
  documented limitation, not a bug).
- [ ] **Expected LLM result:** leans toward **denial**, quoting the exclusion
  sentence verbatim.
- [ ] **Expected banner:** an amber **"Signals disagree"** notice appears
  between the two cards — this is the intended behavior when the two engines
  reach different conclusions.

### 2.3 — Conflicting evidence → HUMAN_REVIEW (both engines agree)
- [ ] Diagnosis: `chronic lower back pain`
- [ ] Requested service: `lumbar spinal fusion surgery`
- [ ] Policy language: `Lumbar spinal fusion surgery is covered only when conservative therapy for at least 6 months has failed and imaging confirms spinal instability.`
- [ ] Clinical evidence: `One radiologist report states spinal instability is present; a second radiologist report for the same imaging study states no instability is seen. Duration of conservative therapy is not clearly documented.`
- [ ] Evidence completeness: `Conflicting evidence`
- [ ] **Expected rule-based result:** `HUMAN_REVIEW`, 90/100, "Conflicting
  evidence requires human reconciliation."
- [ ] **Expected LLM result:** also recommends human review, citing the same
  conflict.
- [ ] **Expected banner:** no disagreement banner (both agree).

### 2.4 — Incomplete evidence → HUMAN_REVIEW
- [ ] Diagnosis: `rheumatoid arthritis`
- [ ] Requested service: `biologic infusion therapy`
- [ ] Policy language: `Biologic infusion therapy is covered when there is documented failure of at least two conventional disease-modifying antirheumatic drugs (DMARDs).`
- [ ] Clinical evidence: `Patient has rheumatoid arthritis. Prior DMARD treatment history is not fully documented in the chart.`
- [ ] Evidence completeness: `Incomplete / missing information`
- [ ] **Expected rule-based result:** `HUMAN_REVIEW`, 65/100 ("Sufficient
  matching evidence was not established").
- [ ] **Expected LLM result:** also recommends human review, citing the
  missing DMARD history.

### 2.5 — Policy qualifier is escalated, not auto-approved
- [ ] Diagnosis: `glaucoma`
- [ ] Requested service: `glaucoma imaging`
- [ ] Policy language: `Glaucoma imaging is covered unless the patient has glaucoma.`
- [ ] Clinical evidence: `Documented glaucoma imaging is medically necessary.`
- [ ] Evidence completeness: `Documented`
- [ ] **Expected rule-based result:** `HUMAN_REVIEW`, 90/100 ("Policy
  conditions or conflicting coverage/exclusion language require full-context
  human review.") — the word "unless" triggers a policy-qualifier check that
  prevents a naive keyword match from auto-approving a self-contradictory
  policy sentence.
- [ ] **Expected LLM result:** leans toward **denial** (it reasons the
  "unless" exception clause applies). **Expected banner:** "Signals
  disagree" appears, since the rule engine says HUMAN_REVIEW and the LLM
  leans DENY.

### 2.6 — Negated clinical evidence is not treated as support
- [ ] Diagnosis: `glaucoma`
- [ ] Requested service: `glaucoma imaging`
- [ ] Policy language: `Glaucoma imaging is covered when medically necessary.`
- [ ] Clinical evidence: `No evidence of glaucoma imaging is documented.`
- [ ] Evidence completeness: `Documented`
- [ ] **Expected rule-based result:** `HUMAN_REVIEW`, 90/100 ("Evidence
  contains negation or absence language that requires human review.") —
  negation language ("no evidence…") blocks the engine from treating this as
  supporting evidence even though the words "glaucoma imaging" appear.
- [ ] **Expected LLM result:** also recommends human review (no disagreement
  banner expected here).

### 2.7 — Required-field validation
- [ ] Clear **all four** text fields (Diagnosis, Requested service, Policy
  language, Clinical evidence) so they're empty.
- [ ] Click **Run research review**.
  **Expected:** the browser blocks submission and focuses the first empty
  required field (native HTML validation) — no request is sent, no crash.

### 2.8 — Special-character / injection safety
- [ ] Diagnosis: `<script>alert(1)</script> & "quoted" diagnosis`
- [ ] Requested service: `O'Brien's <b>procedure</b> & test`
- [ ] Policy language: `Coverage is <i>covered</i> when "medically necessary" & documented; see § 80.1 — <script>evil()</script>.`
- [ ] Clinical evidence: `Evidence includes <img src=x onerror=alert(1)> and unicode: café, 日本語, emoji 🎉.`
- [ ] Evidence completeness: `Documented`
- [ ] Click **Run research review**.
  **Expected:** **no JavaScript alert pops up.** The findings and the
  "Quoted policy support" block render the angle brackets and script tags as
  plain visible text, not executed markup.

---

## 3. Evaluation workbench

Go to the **Evaluation** tab.

### 3.1 — Load the real CMS policy sample
- [ ] Click **Load real CMS policy sample**.
  **Expected:** a green provenance banner appears citing the CMS NCD Manual,
  and the JSONL textbox fills with 10 real-policy cases.
- [ ] Click **Run evaluation**.
  **Expected result summary:**
  - 10 evaluated, 0 errors
  - **Accuracy: 70.0%**
  - **Macro F1: 51.7%**
  - False approval rate: 20.0% / False denial rate: 0.0%
  - Human review rate: 50.0%
  - A confusion matrix and a 10-row case-level audit list below it.

### 3.2 — Malformed JSONL is rejected client-side
- [ ] Clear the textbox and paste this (note the last line is broken JSON):
  ```
  {"inference_case":{"diagnosis":"hypertension","requested_service":"home blood pressure monitor","policy_text":"Home blood pressure monitors are covered when hypertension is documented and medically necessary.","clinical_evidence":"Hypertension documented; monitor medically necessary per physician note.","evidence_state":"documented"},"ground_truth":"APPROVE"}
  {this is not valid json}
  ```
- [ ] Click **Run evaluation**.
  **Expected:** a red alert: *"Line 2 is not valid JSON. Each line must be a
  single, complete JSON object — check for missing quotes, commas, or
  braces."* No results are computed.

### 3.3 — Custom 3-class evaluation set
- [ ] Replace the textbox content with (remove the broken line, one JSON
  object per line, three lines total):
  ```
  {"inference_case":{"diagnosis":"hypertension","requested_service":"home blood pressure monitor","policy_text":"Home blood pressure monitors are covered when hypertension is documented and medically necessary.","clinical_evidence":"Hypertension documented; monitor medically necessary per physician note.","evidence_state":"documented"},"ground_truth":"APPROVE"}
  {"inference_case":{"diagnosis":"common cold","requested_service":"full body MRI","policy_text":"Full body MRI for evaluation of the common cold is not covered and is excluded from benefits.","clinical_evidence":"Patient has a common cold; no other symptoms.","evidence_state":"documented"},"ground_truth":"DENY"}
  {"inference_case":{"diagnosis":"unspecified chest pain","requested_service":"cardiac catheterization","policy_text":"Cardiac catheterization is covered when ischemia is documented by stress testing.","clinical_evidence":"Stress test results pending; not yet available.","evidence_state":"incomplete"},"ground_truth":"HUMAN_REVIEW"}
  ```
- [ ] Click **Run evaluation**.
  **Expected:** 3 evaluated, 0 errors, **Accuracy 100.0%**, **Macro F1
  100.0%**, confusion matrix with exactly one case on each diagonal cell
  (APPROVE/APPROVE, DENY/DENY, HUMAN_REVIEW/HUMAN_REVIEW).

---

## 4. Review queue — maker-checker human-review workflow

This is the persisted, audited workflow. You need **two different reviewer
logins** to fully exercise it.

### 4.1 — Submit a case as alice (the "maker")
Make sure you're logged in as **alice**. Go to **Review queue** →
**Submit case for review**.
- [ ] Diagnosis: `diabetic retinopathy`
- [ ] Requested service: `retinal laser photocoagulation`
- [ ] Policy language: `Retinal laser photocoagulation is covered for proliferative diabetic retinopathy when medically necessary and documented by an ophthalmologist.`
- [ ] Clinical evidence: `Ophthalmologist documented proliferative diabetic retinopathy requiring retinal laser photocoagulation as medically necessary.`
- [ ] Evidence completeness: `Documented`
- [ ] Click **Submit for review**.
  **Expected:** a new case appears (e.g. "Case #N") with:
  - Status badge: **Pending review**
  - A green "PROTOTYPE SUGGESTION — NOT A DECISION" card: `APPROVE`
  - A green "LLM + RAG COMPARISON SIGNAL — SECONDARY ONLY" card (if LLM
    enabled): also `APPROVE`
  - A blue **"Segregation of duties"** notice: *"You submitted this case, so
    you cannot also record its human decision. Another reviewer must
    complete this step."* — and **no decision form is shown** to alice.

### 4.2 — Segregation of duties is enforced server-side too
- [ ] While still logged in as alice, note the case number from 4.1 (call it
  `#N`). This step just confirms the UI never even offers the decision form
  to the creator — there is nothing further to click here, the absence of
  the form **is** the test.

### 4.3 — A different reviewer (bob) can record the decision
- [ ] Sign out. Sign in as **bob** / `TestPass1234!`.
- [ ] Go to **Review queue**, click the **All** filter, and open case `#N`.
  **Expected:** this time the **"Record the human review decision"** form
  **is** shown (bob didn't create this case).
- [ ] Select Decision: **Approved**.
- [ ] Notes: `Confirmed ophthalmologist documentation matches policy criteria; both prototype and LLM signals agree.`
- [ ] Click **Record final decision**.
  **Expected:** the card changes to a green **"Human decision recorded"**
  panel reading *"APPROVED by bob on <date/time>"* with your note quoted
  underneath, status badge changes to **Reviewed**, and a **"Reopen (admin
  only)"** button appears.

### 4.4 — Decisions are immutable (non-admin can't change them, can't re-decide)
- [ ] Still as bob, note that the decision form is gone — there is no way to
  submit a second decision through the UI for this case anymore. (The
  backend additionally returns `409 Conflict` if the same decision endpoint
  is called again — this is enforced even if you were to bypass the UI.)

### 4.5 — Reopening a decided case requires admin
- [ ] Still as bob (role = reviewer), click **Reopen (admin only)** on case
  `#N`.
  **Expected:** a red alert: *"Requires one of roles: admin"*. The case stays
  **Reviewed**.
- [ ] Sign out. Sign in as **admin** / `TestPass1234!`.
- [ ] Open case `#N` (filter **All**), click **Reopen (admin only)**.
  **Expected:** succeeds — status badge returns to **Pending review**, and
  the decision form reappears (now re-decidable by any eligible reviewer,
  including admin).

### 4.6 — Admin self-review: backend allows it, the UI currently does not expose it
- [ ] While logged in as **admin**, submit a new case for review (any inputs
  — e.g. reuse the DMARD example from 2.4, Evidence completeness
  `Incomplete / missing information`).
  **Expected:** case created, suggestion = `HUMAN_REVIEW`.
- [ ] On that same case (still logged in as admin), look at what's shown.
  **Expected (actual UI behavior):** the UI shows the same **"Segregation of
  duties"** notice as it would for a regular reviewer — admin does **not**
  get the decision form here, even though the backend API itself *does*
  permit an admin to record a decision on their own case (confirmed via a
  direct API call: `POST /api/v1/reviews/{id}/decision` as the admin who
  created the case returns `200`, not `403`). This is a known **frontend
  gap, not a backend bug**: [`ReviewQueue.tsx`](frontend/src/ReviewQueue.tsx)
  computes `isOwnCase` from username only (`detail.created_by ===
  currentUsername`) and never checks `role`, so the admin exemption that
  exists server-side is invisible in the UI. Don't expect to see a decision
  form here until this is fixed — if you do see one, something changed.

### 4.7 — Escalation path + queue filters
- [ ] Go back to the **Review queue** list (not the detail view). Click
  **Pending**, **Reviewed**, and **All** tabs in turn.
  **Expected:**
  - **Pending** shows only cases with status `Pending review`.
  - **Reviewed** shows only decided cases, each with its decision chip
    (`APPROVED` / `DENIED` / `ESCALATED`) visible in the list row.
  - **All** shows every case regardless of status.
- [ ] Click **Refresh**.
  **Expected:** list re-fetches without errors (button briefly disables,
  then re-enables).

### 4.8 — Unauthenticated access is blocked
- [ ] Sign out completely (back to the login screen).
- [ ] Try to reload the page while on the Review queue URL / state (or just
  reload at `/` after signing out).
  **Expected:** you land on the login screen — the review queue is never
  rendered without a valid session.

---

## 5. Model & Data (transparency page)

Log back in (any account) and go to **Model & data**.

- [ ] **5.1 — Inference configuration card.**
  **Expected fields:** `Inference engine: evidence_reconciliation_prototype`,
  `Implementation: Deterministic lexical rules`, `Deployment environment:
  development`, `Authentication: Required (JWT bearer)` (ENFORCED),
  `Audit trail: ENABLED`, `LLM / RAG comparison engine: gemini-3.8-flash
  (secondary signal)` if a Gemini key is configured (otherwise shows
  disabled/not configured), `Clinically/legally validated: No` (NOT
  VALIDATED), `Autonomous claim decisions: Not permitted`.

- [ ] **5.2 — Dataset availability card.**
  **Expected:** "10 real-policy research cases available" with a link to the
  CMS NCD Manual PDF; "Frozen 24-case benchmark not available"; "CMS
  provider/service utilization file not downloaded" (HTTP 403 note).

- [ ] **5.3 — Research baselines card.**
  **Expected:** `B1 · Rule-based (original)` → Unavailable; `B2 ·
  KAMEL-inspired` → Unavailable; `B3 · Single LLM / RAG` → **Available
  (comparison engine)**.

- [ ] **5.4 — Readiness checklist.**
  **Expected 5 items**, all **"Not met"** except "Authentication,
  role-based access, and audited human review" which is **"Met"**.

---

## 6. Session expiry (optional, slow test)

- [ ] Log in and leave the tab idle for the configured JWT lifetime (default
  30 minutes — check `CLAIMLAB_JWT_ACCESS_TOKEN_MINUTES` if changed). Then
  click any nav tab or action that calls the API.
  **Expected:** you're redirected to the login screen instead of seeing a
  raw error — expired tokens are handled gracefully.

---

## Summary checklist

| # | Area | Scenarios |
|---|---|---|
| 1 | Login | bad creds, good creds, session persistence, sign out |
| 2 | Case review | APPROVE, DENY-missed-by-rules, conflicting evidence, incomplete evidence, policy qualifier, negated evidence, empty-field validation, XSS safety, LLM agreement/disagreement |
| 3 | Evaluation | CMS sample (70% acc.), malformed JSONL rejection, custom 3-class set (100% acc.) |
| 4 | Review queue | submit, segregation of duties, cross-reviewer decision, immutability, admin-only reopen, admin self-review (backend allows, UI currently blocks — known gap), escalation, filters, auth gate |
| 5 | Model & data | config card, dataset card, baselines card, readiness checklist |
| 6 | Session | expiry redirect |

If every box above is checked and matches the expected result, the full
application — login, both adjudication engines, evaluation metrics, the
audited human-review workflow, and the transparency page — is working
end-to-end.

import { useEffect, useState } from "react";
import {
  Activity, ArrowRight, Bot, BookOpen, Check, ChevronRight, CircleAlert,
  ClipboardCheck, Database, FileCheck2, FileText, FlaskConical, Gauge,
  HeartPulse, Info, Layers3, LoaderCircle, LogOut, RefreshCw, ShieldAlert,
  ShieldCheck, Sparkles, Workflow,
} from "lucide-react";
import { useAuth } from "./auth";
import { LoginScreen } from "./LoginScreen";
import { ReviewQueue } from "./ReviewQueue";

const API = import.meta.env.VITE_API_URL ?? "";
type Decision = "APPROVE" | "DENY" | "HUMAN_REVIEW";
type Finding = {
  agent: string;
  status: string;
  summary: string;
  evidence: string[];
};
type VerifiedQuote = { quote: string; verified_in_policy_text: boolean };
type LLMResult = {
  engine: "llm_rag";
  model: string;
  decision: Decision | null;
  rationale: string;
  confidence: number | null;
  supporting_quotes: VerifiedQuote[];
  attempts: number;
  latency_ms: number;
  error: string | null;
};
type Result = {
  case_id: string | null;
  decision: Decision;
  rationale: string;
  confidence: number;
  confidence_note: string;
  findings: Finding[];
  limitations: string[];
  latency_ms: number;
  llm_result: LLMResult | null;
};
type ClassMetric = { precision: number; recall: number; f1: number; support: number };
type CaseAudit = {
  case_index: number;
  case_id: string | null;
  ground_truth: Decision;
  prediction: Decision | null;
  status: "evaluated" | "error";
  latency_ms: number | null;
  error: string | null;
};
type Metrics = {
  total_cases: number;
  evaluated_cases: number;
  errored_cases: number;
  accuracy: number;
  macro_f1: number;
  false_approval_rate: number;
  false_denial_rate: number;
  human_review_rate: number;
  average_latency_ms: number;
  per_class: Record<string, ClassMetric>;
  confusion_matrix: Record<string, Record<string, number>>;
  case_results: CaseAudit[];
  errors: string[];
};
type Config = {
  model: string;
  inference_type: "deterministic_rules";
  llm_enabled: boolean;
  llm_provider: string | null;
  llm_analysis_model: string | null;
  llm_endpoint_host: string | null;
  llm_role: string | null;
  environment: string;
  auth_required: true;
  audit_trail_enabled: true;
  production_ready: false;
  clinically_validated: false;
  unavailable_baselines: Record<string, string>;
  benchmark_available: false;
};
type CaseInput = {
  case_id?: string;
  diagnosis: string;
  requested_service: string;
  policy_text: string;
  clinical_evidence: string;
  evidence_state: "documented" | "incomplete" | "conflicting";
};
type SampleCasesResponse = {
  cases: Array<{ inference_case: CaseInput; ground_truth: Decision }>;
  provenance: {
    source_title?: string;
    publisher?: string;
    source_url?: string;
    retrieved_at?: string;
  } | null;
  is_real_world_policy_text: true;
  is_synthetic_clinical_evidence: true;
  ground_truth_caveat: string;
};
type View = "adjudication" | "evaluation" | "review" | "research";

const emptyCase: CaseInput = {
  diagnosis: "",
  requested_service: "",
  policy_text: "",
  clinical_evidence: "",
  evidence_state: "documented",
};
const demoCase: CaseInput = {
  case_id: "demo-glaucoma",
  diagnosis: "glaucoma",
  requested_service: "glaucoma imaging",
  policy_text: "Glaucoma imaging is covered when medically necessary.",
  clinical_evidence: "Documented glaucoma imaging is medically necessary.",
  evidence_state: "documented",
};

type ValidationErrorItem = { loc?: Array<string | number>; msg?: string };

function formatValidationErrors(items: ValidationErrorItem[]): string {
  const messages = items.slice(0, 5).map((item) => {
    const path = Array.isArray(item.loc) ? item.loc.filter((p) => p !== "body").join(".") : "";
    return path ? `${path}: ${item.msg ?? "invalid value"}` : (item.msg ?? "invalid value");
  });
  const suffix = items.length > 5 ? ` (and ${items.length - 5} more)` : "";
  return `Validation error — ${messages.join("; ")}${suffix}`;
}

async function readError(response: Response) {
  const body = await response.json().catch(() => null);
  if (Array.isArray(body?.detail) && body.detail.every((item: unknown) => typeof item === "object")) {
    return formatValidationErrors(body.detail as ValidationErrorItem[]);
  }
  if (body?.detail) {
    return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  }
  return `Request failed (${response.status})`;
}

function friendlyErrorMessage(cause: unknown, fallback: string): string {
  if (cause instanceof TypeError) {
    return "Could not reach the API. Check that the backend is running and reachable, then try again.";
  }
  return cause instanceof Error ? cause.message : fallback;
}

function App() {
  const { session, login, logout, authFetch } = useAuth();
  const [view, setView] = useState<View>("adjudication");
  const [form, setForm] = useState<CaseInput>(emptyCase);
  const [result, setResult] = useState<Result | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [labelsText, setLabelsText] = useState("");
  const [busy, setBusy] = useState<"case" | "eval" | "sample" | null>(null);
  const [error, setError] = useState("");
  const [apiReady, setApiReady] = useState<boolean | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [sampleProvenance, setSampleProvenance] = useState<SampleCasesResponse | null>(null);
  const [sampleMeta, setSampleMeta] = useState<{ count: number; provenance: SampleCasesResponse["provenance"] } | null>(null);

  async function checkConnection() {
    try {
      const [healthResponse, configResponse, sampleResponse] = await Promise.all([
        fetch(`${API}/api/v1/health`),
        fetch(`${API}/api/v1/config`),
        fetch(`${API}/api/v1/sample-cases`),
      ]);
      if (!healthResponse.ok) throw new Error(await readError(healthResponse));
      if (!configResponse.ok) throw new Error(await readError(configResponse));
      setConfig(await configResponse.json());
      if (sampleResponse.ok) {
        const body: SampleCasesResponse = await sampleResponse.json();
        setSampleMeta({ count: body.cases.length, provenance: body.provenance });
      } else {
        setSampleMeta(null);
      }
      setApiReady(true);
    } catch {
      setApiReady(false);
      setConfig(null);
      setSampleMeta(null);
    }
  }

  useEffect(() => {
    void checkConnection();
  }, []);

  function update<K extends keyof CaseInput>(key: K, value: CaseInput[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function submitCase(event: React.FormEvent) {
    event.preventDefault();
    setBusy("case");
    setError("");
    setResult(null);
    try {
      const response = await fetch(`${API}/api/v1/adjudications`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!response.ok) throw new Error(await readError(response));
      setResult(await response.json());
    } catch (cause) {
      setError(friendlyErrorMessage(cause, "Unable to adjudicate case."));
    } finally {
      setBusy(null);
    }
  }

  async function runEvaluation() {
    setBusy("eval");
    setError("");
    setMetrics(null);
    try {
      const rows = labelsText.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
      if (rows.length === 0) {
        throw new Error("Enter at least one evaluation case, one JSON object per line.");
      }
      if (rows.length > 500) {
        throw new Error(`Too many cases (${rows.length}). The evaluation endpoint accepts at most 500 per request.`);
      }
      const cases = rows.map((line, index) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(line);
        } catch {
          throw new Error(
            `Line ${index + 1} is not valid JSON. Each line must be a single, complete JSON object — check for missing quotes, commas, or braces.`
          );
        }
        if (
          !parsed || typeof parsed !== "object" || Array.isArray(parsed)
          || !("inference_case" in parsed) || !("ground_truth" in parsed)
        ) {
          throw new Error(`Line ${index + 1} must be an object with "inference_case" and "ground_truth" fields.`);
        }
        return parsed;
      });
      const response = await fetch(`${API}/api/v1/evaluations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cases }),
      });
      if (!response.ok) throw new Error(await readError(response));
      setMetrics(await response.json());
    } catch (cause) {
      setError(friendlyErrorMessage(cause, "Unable to evaluate cases."));
    } finally {
      setBusy(null);
    }
  }

  async function loadRealSample() {
    setBusy("sample");
    setError("");
    try {
      const response = await fetch(`${API}/api/v1/sample-cases`);
      if (!response.ok) throw new Error(await readError(response));
      const body: SampleCasesResponse = await response.json();
      setSampleProvenance(body);
      const lines = body.cases.map((entry) => JSON.stringify(entry));
      setLabelsText(lines.join("\n"));
    } catch (cause) {
      setError(friendlyErrorMessage(cause, "Unable to load the sample dataset."));
    } finally {
      setBusy(null);
    }
  }

  function loadDemo() {
    setForm(demoCase);
    setResult(null);
    setError("");
    setView("adjudication");
  }

  const viewTitle: Record<View, string> = {
    adjudication: "Case review",
    evaluation: "Evaluation",
    review: "Review queue",
    research: "Model & data",
  };

  if (!session) {
    return <LoginScreen onLogin={login} />;
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <aside className="sidebar">
        <a className="brand" href="#main-content" aria-label="ClaimLab home">
          <span className="brand-icon"><HeartPulse size={20} strokeWidth={2.2} /></span>
          <span className="brand-copy"><strong>claim<span>lab</span></strong><small>RESEARCH WORKSPACE</small></span>
        </a>

        <div className="workspace-label">WORKSPACE</div>
        <nav aria-label="Primary navigation" className="side-nav">
          <button className={`nav-item ${view === "adjudication" ? "active" : ""}`} onClick={() => setView("adjudication")} aria-current={view === "adjudication" ? "page" : undefined}>
            <ClipboardCheck size={17} /><span>Case review</span><ChevronRight size={14} className="nav-chevron" />
          </button>
          <button className={`nav-item ${view === "evaluation" ? "active" : ""}`} onClick={() => setView("evaluation")} aria-current={view === "evaluation" ? "page" : undefined}>
            <Gauge size={17} /><span>Evaluation</span><ChevronRight size={14} className="nav-chevron" />
          </button>
          <button className={`nav-item ${view === "review" ? "active" : ""}`} onClick={() => setView("review")} aria-current={view === "review" ? "page" : undefined}>
            <ShieldCheck size={17} /><span>Review queue</span><ChevronRight size={14} className="nav-chevron" />
          </button>
          <button className={`nav-item ${view === "research" ? "active" : ""}`} onClick={() => setView("research")} aria-current={view === "research" ? "page" : undefined}>
            <Layers3 size={17} /><span>Model & data</span><ChevronRight size={14} className="nav-chevron" />
          </button>
        </nav>

        <button className="mobile-signout-btn" onClick={logout} aria-label={`Sign out (${session.username})`}>
          <LogOut size={16} />
        </button>

        <div className="sidebar-bottom">
          <div className="user-chip">
            <span className="user-avatar">{session.username.slice(0, 2).toUpperCase()}</span>
            <span><strong>{session.username}</strong><small>{session.role}</small></span>
            <button className="icon-button" onClick={logout} aria-label="Sign out"><LogOut size={14} /></button>
          </div>
          <div className="side-status">
            <span className={`status-indicator ${apiReady ? "online" : apiReady === false ? "offline" : ""}`} />
            <span><strong>{apiReady ? "API operational" : apiReady === false ? "API unavailable" : "Checking API"}</strong><small>{apiReady ? "Local research service" : "Connection status"}</small></span>
            <button className="icon-button" onClick={() => void checkConnection()} aria-label="Refresh API status"><RefreshCw size={14} /></button>
          </div>
          <div className="safety-note"><ShieldCheck size={15} /><p>Research prototype only.<br />Every decision requires human sign-off.</p></div>
          <div className="sidebar-version">CLAIMLAB <span>·</span> v0.2 production-layer build</div>
        </div>
      </aside>

      <main className="main-content" id="main-content">
        <header className="topbar">
          <div className="breadcrumbs"><span>Workspace</span><ChevronRight size={13} /><strong>{viewTitle[view]}</strong></div>
          <div className="top-actions">
            <div className="model-chip"><span className="model-dot" /><span>{config?.model ?? "Model status unavailable"}</span></div>
            <span className="env-pill"><FlaskConical size={13} /> Research environment</span>
          </div>
        </header>

        <div className="page">
          {error && <div className="error-banner" role="alert"><CircleAlert size={17} /><span>{error}</span></div>}

          {apiReady === false && (
            <div className="error-banner" role="alert">
              <CircleAlert size={17} />
              <span>Backend is not reachable at the configured API URL. Start the API (see README.md), then select "Refresh API status" in the sidebar.</span>
            </div>
          )}

          {view === "adjudication" && (
            <>
              <PageHeading eyebrow="EVIDENCE RECONCILIATION" title="Case review" subtitle="Inspect policy and evidence signals. The prototype does not make coverage decisions." action={<button className="button secondary" onClick={loadDemo}><BookOpen size={15} /> Load example</button>} />
              <div className="workflow-strip" aria-label="Review workflow">
                <WorkflowStep number="1" title="Case inputs" active={!result} complete={Boolean(result)} />
                <span className="workflow-connector" />
                <WorkflowStep number="2" title="Evidence review" active={Boolean(result)} complete={false} />
                <span className="workflow-connector" />
                <WorkflowStep number="3" title="Human decision" active={false} complete={false} />
                <span className="workflow-caption"><ShieldCheck size={14} /> Human sign-off required</span>
              </div>
              <section className="workspace-grid" aria-label="Case review workspace">
                <form className="panel case-card" onSubmit={submitCase}>
                  <PanelHeading icon={<FileText size={17} />} tone="blue" title="Case information" subtitle="Inference-only fields. Do not enter real patient data." step="INPUT" />
                  <div className="field-row">
                    <Field label="Diagnosis" required hint="Clinical indication">
                      <input required maxLength={500} value={form.diagnosis} onChange={(e) => update("diagnosis", e.target.value)} placeholder="e.g. glaucoma" autoComplete="off" />
                    </Field>
                    <Field label="Requested service" required hint="Procedure or service">
                      <input required maxLength={500} value={form.requested_service} onChange={(e) => update("requested_service", e.target.value)} placeholder="e.g. diagnostic imaging" autoComplete="off" />
                    </Field>
                  </div>
                  <Field label="Applicable policy language" required hint="Use a relevant excerpt; scope is limited to the text entered.">
                    <textarea required maxLength={20000} rows={5} value={form.policy_text} onChange={(e) => update("policy_text", e.target.value)} placeholder="Enter the relevant policy text…" />
                  </Field>
                  <Field label="Clinical evidence summary" required hint="Do not include names, identifiers, or sensitive personal information.">
                    <textarea required maxLength={10000} rows={5} value={form.clinical_evidence} onChange={(e) => update("clinical_evidence", e.target.value)} placeholder="Summarize the available evidence…" />
                  </Field>
                  <Field label="Evidence completeness" hint="Select conflicting if the provided documentation disagrees.">
                    <select value={form.evidence_state} onChange={(e) => update("evidence_state", e.target.value as CaseInput["evidence_state"])}>
                      <option value="documented">Documented</option>
                      <option value="incomplete">Incomplete / missing information</option>
                      <option value="conflicting">Conflicting evidence</option>
                    </select>
                  </Field>
                  <div className="form-foot">
                    <span className="privacy-inline"><ShieldCheck size={14} /> Inference accepts no ground-truth field</span>
                    <button className="button primary" type="submit" disabled={busy !== null || !apiReady}>
                      {busy === "case" ? <LoaderCircle className="spin" size={16} /> : <Activity size={16} />}
                      {busy === "case" ? "Reviewing…" : "Run research review"} <ArrowRight size={15} />
                    </button>
                  </div>
                </form>

                <section className="panel findings-panel" aria-live="polite" aria-busy={busy === "case"}>
                  <PanelHeading icon={<Sparkles size={17} />} tone="violet" title="Review findings" subtitle="Signals from the deterministic prototype" />
                  {busy === "case" ? <LoadingState /> : result ? <ResultView result={result} /> : <EmptyState />}
                </section>
              </section>
              <section className="quality-row" aria-label="System limitations">
                <QualityItem icon={<ShieldCheck size={16} />} title="Human oversight" detail="Every output remains subject to independent review." />
                <QualityItem icon={<FileCheck2 size={16} />} title="Traceable signals" detail="Findings identify the terms that triggered a rule." />
                <QualityItem icon={<Info size={16} />} title="Heuristic only" detail="Not calibrated, clinically validated, or production-ready." />
              </section>
            </>
          )}

          {view === "evaluation" && (
            <>
              <PageHeading eyebrow="RESEARCH & VALIDATION" title="Evaluation workbench" subtitle="Run metrics only on data you are authorized to use, with adjudication labels kept outside inference inputs." action={<button className="button secondary" disabled={busy !== null || !apiReady} onClick={() => void loadRealSample()}><Database size={15} /> {busy === "sample" ? "Loading…" : "Load real CMS policy sample"}</button>} />
              {sampleProvenance && (
                <div className="callout provenance-callout">
                  <Database size={16} />
                  <p>
                    <strong>Real-data sample loaded.</strong> Policy text is an unmodified excerpt from{" "}
                    <a href={sampleProvenance.provenance?.source_url} target="_blank" rel="noreferrer">{sampleProvenance.provenance?.source_title ?? "the CMS NCD Manual"}</a>{" "}
                    ({sampleProvenance.provenance?.publisher}). Clinical evidence is synthetic. {sampleProvenance.ground_truth_caveat}
                  </p>
                </div>
              )}
              <section className="panel evaluation-panel">
                <PanelHeading icon={<Gauge size={17} />} tone="green" title="Labeled evaluation set" subtitle="One JSON object per line. Labels are sent only to the evaluation endpoint." step="OFFLINE EVAL" />
                <div className="eval-input-grid">
                  <div>
                    <Field label="Evaluation cases (JSONL)" required hint="Each line: { inference_case: {...}, ground_truth: APPROVE | DENY | HUMAN_REVIEW }">
                      <textarea className="jsonl" rows={12} value={labelsText} onChange={(e) => setLabelsText(e.target.value)} placeholder={'{"inference_case":{"diagnosis":"…","requested_service":"…","policy_text":"…","clinical_evidence":"…","evidence_state":"documented"},"ground_truth":"APPROVE"}'} />
                    </Field>
                    <div className="eval-controls">
                      <span><ShieldCheck size={14} /> Ground truth is not passed to adjudication</span>
                      <button className="button primary" disabled={busy !== null || !apiReady || !labelsText.trim()} onClick={() => void runEvaluation()}>
                        {busy === "eval" ? <LoaderCircle className="spin" size={16} /> : <Gauge size={16} />}
                        {busy === "eval" ? "Evaluating…" : "Run evaluation"}
                      </button>
                    </div>
                  </div>
                  {metrics ? <MetricsView metrics={metrics} /> : <div className="metrics-placeholder"><div className="placeholder-icon"><Gauge size={21} /></div><strong>Evaluation results will appear here</strong><span>Accuracy, per-class metrics, false decision rates, confusion matrix, and case-level audit rows.</span></div>}
                </div>
              </section>
              <div className="callout info-callout"><Info size={17} /><p><strong>Interpret metrics carefully.</strong> No frozen benchmark is loaded. A small or non-representative sample cannot establish generalization, safety, or production readiness.</p></div>
            </>
          )}

          {view === "review" && (
            <>
              <PageHeading eyebrow="AUDITED HUMAN REVIEW" title="Review queue" subtitle="Every submitted case is persisted with an audit trail. A different reviewer must record the final decision before it counts as anything but a suggestion." />
              <ReviewQueue authFetch={authFetch} currentUsername={session.username} currentRole={session.role} />
            </>
          )}

          {view === "research" && (
            <>
              <PageHeading eyebrow="SYSTEM TRANSPARENCY" title="Model & data" subtitle="Know what is running—and what evidence is not available—before interpreting a result." />
              <div className="research-grid">
                <section className="panel">
                  <PanelHeading icon={<Workflow size={17} />} tone="blue" title="Inference configuration" subtitle="Reported by the backend at runtime" />
                  <div className="detail-list">
                    <DetailRow label="Inference engine" value={config?.model ?? "Unavailable"} />
                    <DetailRow label="Implementation" value="Deterministic lexical rules" />
                    <DetailRow label="Deployment environment" value={config?.environment ?? "Unknown"} badge={config?.environment === "production" ? "PRODUCTION ENV" : (config?.environment ?? "").toUpperCase() || undefined} />
                    <DetailRow label="Authentication" value="Required (JWT bearer)" badge="ENFORCED" />
                    <DetailRow label="Audit trail" value="Every review persisted with reviewer identity" badge="ENABLED" />
                    <DetailRow
                      label="LLM / RAG comparison engine"
                      value={config?.llm_enabled ? `${config.llm_analysis_model ?? "configured model"} (secondary signal)` : "Not configured"}
                      badge={config?.llm_enabled ? "SECONDARY SIGNAL" : "NO LLM"}
                    />
                    <DetailRow label="Clinically/legally validated" value="No" badge="NOT VALIDATED" />
                    <DetailRow label="Autonomous claim decisions" value="Not permitted — human sign-off required" badge="DECISION SUPPORT ONLY" />
                  </div>
                </section>
                <section className="panel">
                  <PanelHeading icon={<Database size={17} />} tone="amber" title="Dataset availability" subtitle="One real dataset is available; the original benchmark is not" />
                  {sampleMeta && sampleMeta.count > 0 ? (
                    <div className="data-status success-status">
                      <span className="data-status-icon success"><FileCheck2 size={19} /></span>
                      <div>
                        <strong>{sampleMeta.count} real-policy research cases available</strong>
                        <p>
                          Policy text is an unmodified excerpt from{" "}
                          {sampleMeta.provenance?.source_url ? (
                            <a href={sampleMeta.provenance.source_url} target="_blank" rel="noreferrer">{sampleMeta.provenance.source_title ?? "the CMS NCD Manual"}</a>
                          ) : (sampleMeta.provenance?.source_title ?? "the CMS NCD Manual")}. Clinical evidence is synthetic and ground_truth labels are project-authored, not clinically validated. Open the Evaluation tab to load and run it.
                        </p>
                      </div>
                    </div>
                  ) : (
                    <div className="data-status"><span className="data-status-icon"><CircleAlert size={19} /></span><div><strong>Real-policy sample dataset unavailable</strong><p>The backend could not serve data/processed/ncd_eye_evaluation_cases.jsonl. Run the extraction scripts in scripts/ from the project root.</p></div></div>
                  )}
                  <div className="data-status"><span className="data-status-icon"><CircleAlert size={19} /></span><div><strong>Frozen 24-case benchmark not available</strong><p>It was referenced in the project brief but not provided in this workspace. Do not infer research performance from example cases.</p></div></div>
                  <div className="data-status secondary-status"><span className="data-status-icon"><Database size={19} /></span><div><strong>CMS provider/service utilization file not downloaded</strong><p>A separate CMS aggregate utilization dataset (distinct from the policy corpus above) has no adjudication labels and returned HTTP 403 in this environment.</p></div></div>
                  <a className="text-link" href="https://catalog.data.gov/dataset/medicare-physician-other-practitioners-by-provider-and-service" target="_blank" rel="noreferrer">Open CMS catalog record <ArrowRight size={14} /></a>
                </section>
                <section className="panel baseline-panel">
                  <PanelHeading icon={<Layers3 size={17} />} tone="violet" title="Research baselines" subtitle="Original B1/B2 implementations are not installed" />
                  <div className="baseline-list">
                    <div className="baseline-row"><span>B1 · Rule-based (original)</span><span className="availability unavailable">Unavailable</span></div>
                    <div className="baseline-row"><span>B2 · KAMEL-inspired</span><span className="availability unavailable">Unavailable</span></div>
                    <div className="baseline-row">
                      <span>B3 · Single LLM / RAG</span>
                      <span className={`availability ${config?.llm_enabled ? "available" : "unavailable"}`}>
                        {config?.llm_enabled ? "Available (comparison engine)" : "Unavailable"}
                      </span>
                    </div>
                  </div>
                  <div className="callout"><Info size={16} /><p>{config?.llm_enabled ? "The B3-style LLM/RAG engine is a from-scratch reimplementation, not the original research code, and is a secondary comparison signal only — see Case review." : "Provide the original repository and frozen benchmark to enable valid reproduction and comparison of B1/B2. Configure CAF_MODEL_API_KEY (a Google Gemini API key) to enable a comparison-only B3-style LLM/RAG engine."}</p></div>
                </section>
                <section className="panel">
                  <PanelHeading icon={<ShieldCheck size={17} />} tone="green" title="Readiness checklist" subtitle="Requirements before any operational pilot" />
                  <ul className="readiness-list">
                    <li><Check size={15} className="readiness-met" /><span>Authentication, role-based access, and audited human review</span><b className="met">Met</b></li>
                    <li><CircleAlert size={15} /><span>Approved, representative, labeled real-claim data</span><b>Not met</b></li>
                    <li><CircleAlert size={15} /><span>Independent safety and subgroup validation</span><b>Not met</b></li>
                    <li><CircleAlert size={15} /><span>Data retention/deletion policy, backups, and DR plan</span><b>Not met</b></li>
                    <li><CircleAlert size={15} /><span>Clinical, legal, privacy, and security review</span><b>Not met</b></li>
                  </ul>
                </section>
              </div>
            </>
          )}

          <footer className="page-footer">
            <span><ShieldCheck size={14} /> No app-side case storage on this screen · decisions require independent human sign-off</span>
            <span>For research exploration only</span>
          </footer>
        </div>
      </main>
    </div>
  );
}

function PageHeading({ eyebrow, title, subtitle, action }: { eyebrow: string; title: string; subtitle: string; action?: React.ReactNode }) {
  return <header className="page-heading"><div><div className="eyebrow"><Sparkles size={13} /> {eyebrow}</div><h1>{title}</h1><p>{subtitle}</p></div>{action}</header>;
}

function PanelHeading({ icon, tone, title, subtitle, step }: { icon: React.ReactNode; tone: string; title: string; subtitle: string; step?: string }) {
  return <div className="panel-heading"><span className={`section-icon ${tone}`}>{icon}</span><div className="panel-heading-copy"><h2>{title}</h2><p>{subtitle}</p></div>{step && <span className="panel-step">{step}</span>}</div>;
}

function WorkflowStep({ number, title, active, complete }: { number: string; title: string; active: boolean; complete: boolean }) {
  return <div className={`workflow-step ${active ? "current" : ""} ${complete ? "complete" : ""}`}><span>{complete ? <Check size={13} /> : number}</span><strong>{title}</strong></div>;
}

function Field({ label, required, hint, children }: { label: string; required?: boolean; hint?: string; children: React.ReactNode }) {
  return <label className="field"><span className="field-label">{label}{required && <i aria-label="required">Required</i>}</span>{children}{hint && <small>{hint}</small>}</label>;
}

function EmptyState() {
  return <div className="empty-state"><div className="empty-illustration"><Activity size={24} /></div><strong>Ready for a research review</strong><p>Submit inference-only information to view the prototype's policy and evidence signals.</p><div className="empty-caption"><ShieldCheck size={14} /> No determination is made on this screen</div></div>;
}

function LoadingState() {
  return <div className="empty-state loading-state"><LoaderCircle className="spin" size={27} /><strong>Reviewing the entered text</strong><p>Applying limited deterministic rules. This does not replace a qualified reviewer.</p></div>;
}

function ResultView({ result }: { result: Result }) {
  const title = result.decision === "HUMAN_REVIEW" ? "Human review required" : result.decision === "APPROVE" ? "Coverage-supporting signal detected" : "Potential exclusion signal detected";
  const signalLabel = result.decision === "HUMAN_REVIEW" ? "REVIEW SIGNAL" : result.decision === "APPROVE" ? "SUPPORT SIGNAL" : "EXCLUSION SIGNAL";
  return (
    <div className="result-content">
      <div className={`decision ${result.decision.toLowerCase()}`}>
        <div className="decision-overline"><span>RULE-BASED RESEARCH OUTPUT</span><span className="decision-status">{signalLabel}</span></div>
        <strong>{title}</strong><p>{result.rationale}</p>
      </div>
      <div className="result-metadata"><div><span>Heuristic score</span><strong>{Math.round(result.confidence * 100)}<small>/100</small></strong></div><div><span>Processing time</span><strong>{result.latency_ms.toFixed(1)}<small> ms</small></strong></div><div><span>Case reference</span><strong className="case-reference">{result.case_id ?? "Not provided"}</strong></div></div>
      <div className="score-warning"><Info size={14} /><span>{result.confidence_note} Do not interpret as a probability of correctness.</span></div>
      <div className="section-divider"><span>Evidence signal trace</span><span>3 stages</span></div>
      <div className="trace-list">{result.findings.map((finding, index) => <div className="trace-item" key={finding.agent}><div className={`trace-index ${finding.status}`}>{finding.status === "supports" ? <Check size={12} /> : index + 1}</div><div className="trace-main"><strong>{finding.agent.replaceAll("_", " ")}</strong><p>{finding.summary}</p>{finding.evidence.length > 0 && <div className="evidence-tags">{finding.evidence.map((term) => <span key={term}>{term}</span>)}</div>}</div><span className={`status ${finding.status}`}>{finding.status}</span></div>)}</div>
      <div className="review-action"><ShieldAlert size={17} /><div><strong>Independent human review required</strong><span>This output is not a final decision and does not authorize payment or denial.</span></div></div>
      {result.llm_result && (
        <LLMComparisonPanel llm={result.llm_result} ruleDecision={result.decision} />
      )}
      <details className="limitations"><summary>Limitations and assumptions</summary><ul>{result.limitations.map((item) => <li key={item}>{item}</li>)}</ul></details>
    </div>
  );
}

function LLMComparisonPanel({ llm, ruleDecision }: { llm: LLMResult; ruleDecision: Decision }) {
  const disagrees = llm.decision !== null && llm.decision !== ruleDecision;
  const decisionClass = (llm.decision ?? "unavailable").toLowerCase();
  const title = llm.decision === null
    ? "LLM engine unavailable for this request"
    : llm.decision === "HUMAN_REVIEW" ? "Model also recommends human review"
    : llm.decision === "APPROVE" ? "Model leans toward approval"
    : "Model leans toward denial";
  return (
    <>
      {disagrees && (
        <div className="disagreement-banner">
          <CircleAlert size={16} />
          <div>
            <strong>Signals disagree</strong>
            <span>The rule-based and LLM/RAG engines reached different conclusions for this case. Review both carefully — neither is authoritative.</span>
          </div>
        </div>
      )}
      <div className={`decision llm-decision ${decisionClass}`}>
        <div className="decision-overline"><span><Bot size={11} style={{ display: "inline", verticalAlign: "-2px", marginRight: 4 }} />LLM + RAG COMPARISON SIGNAL</span><span className="decision-status">{llm.decision ? "SECONDARY SIGNAL" : "UNAVAILABLE"}</span></div>
        <strong>{title}</strong>
        <p>{llm.decision ? llm.rationale : (llm.error ?? "The model did not return a usable result after retrying.")}</p>
      </div>
      {llm.decision && (
        <>
          <div className="result-metadata">
            <div><span>Model confidence</span><strong>{llm.confidence != null ? Math.round(llm.confidence * 100) : "—"}<small>/100</small></strong></div>
            <div><span>Processing time</span><strong>{llm.latency_ms.toFixed(0)}<small> ms</small></strong></div>
            <div><span>Model</span><strong className="case-reference">{llm.model}</strong></div>
          </div>
          {llm.supporting_quotes.length > 0 && (
            <div className="llm-quotes">
              <span className="llm-quotes-label">Quoted policy support</span>
              <ul>
                {llm.supporting_quotes.map((q, i) => (
                  <li key={i} className={q.verified_in_policy_text ? "quote-verified" : "quote-unverified"}>
                    {q.verified_in_policy_text ? <Check size={12} /> : <CircleAlert size={12} />}
                    <div>
                      <span>&ldquo;{q.quote}&rdquo;</span>
                      {!q.verified_in_policy_text && <i>Not found verbatim in the submitted policy text — possible hallucination.</i>}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      <div className="score-warning"><Info size={14} /><span>Secondary comparison signal only, produced by a separately configured model. It never overrides the rule-based result above and is not itself a decision. The two engines can and do disagree (see README) — that disagreement, when it happens, is itself useful information for the human reviewer.</span></div>
    </>
  );
}

function QualityItem({ icon, title, detail }: { icon: React.ReactNode; title: string; detail: string }) {
  return <div className="quality-item"><span>{icon}</span><div><strong>{title}</strong><small>{detail}</small></div></div>;
}

function DetailRow({ label, value, badge }: { label: string; value: string; badge?: string }) {
  return <div className="detail-row"><span>{label}</span><strong>{value}</strong>{badge && <i>{badge}</i>}</div>;
}

function MetricsView({ metrics }: { metrics: Metrics }) {
  return (
    <div className="metrics-results">
      <div className="metrics-summary"><span>RUN SUMMARY</span><strong>{metrics.evaluated_cases} <small>evaluated</small></strong><i>{metrics.errored_cases} errors / {metrics.total_cases} total</i></div>
      <div className="metric-grid">
        <Metric label="Accuracy" value={metrics.accuracy} />
        <Metric label="Macro F1" value={metrics.macro_f1} />
        <Metric label="False approval rate" value={metrics.false_approval_rate} />
        <Metric label="False denial rate" value={metrics.false_denial_rate} />
        <Metric label="Human review rate" value={metrics.human_review_rate} />
        <Metric label="Mean latency" value={`${metrics.average_latency_ms.toFixed(1)} ms`} />
      </div>
      <div className="class-table"><div className="table-row table-head"><span>Outcome</span><span>Precision</span><span>Recall</span><span>F1 · n</span></div>{Object.entries(metrics.per_class).map(([label, value]) => <div className="table-row" key={label}><span>{label.replace("_", " ")}</span><span>{value.precision.toFixed(2)}</span><span>{value.recall.toFixed(2)}</span><span>{value.f1.toFixed(2)} · {value.support}</span></div>)}</div>
      <div className="class-table"><div className="table-row table-head"><span>Actual \ Predicted</span><span>Approve</span><span>Deny</span><span>Review</span></div>{Object.entries(metrics.confusion_matrix).map(([actual, predicted]) => <div className="table-row" key={actual}><span>{actual.replace("_", " ")}</span><span>{predicted.APPROVE}</span><span>{predicted.DENY}</span><span>{predicted.HUMAN_REVIEW}</span></div>)}</div>
      <div className="case-results"><strong>Case-level audit</strong>{metrics.case_results.map((item) => <div className="case-result" key={item.case_index}><span>#{item.case_index}{item.case_id ? ` · ${item.case_id}` : ""}</span><span>Actual: {item.ground_truth.replace("_", " ")}</span><span>{item.status === "error" ? item.error : `Predicted: ${item.prediction?.replace("_", " ")}`}</span></div>)}</div>
      <p className="metric-footnote">Errors remain in accuracy and recall support. Precision uses successful predictions only. See README for metric definitions.</p>
      {metrics.errors.length > 0 && <div className="eval-errors">{metrics.errors.map((item) => <p key={item}>{item}</p>)}</div>}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  const display = typeof value === "number" ? `${(value * 100).toFixed(1)}%` : value;
  return <div className="metric"><span>{label}</span><strong>{display}</strong></div>;
}

export default App;

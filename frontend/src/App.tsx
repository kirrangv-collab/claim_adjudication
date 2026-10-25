import { useEffect, useState } from "react";
import {
  Activity, ArrowRight, Bot, BookOpen, Check, ChevronRight, CircleAlert,
  ClipboardCheck, Database, FileCheck2, FileText, FlaskConical, Gauge, GitCompare,
  HeartPulse, Info, Layers3, LoaderCircle, LogOut, Network, RefreshCw, ShieldAlert,
  ShieldCheck, Sparkles, Workflow, Zap,
} from "lucide-react";
import { useAuth } from "./auth";
import { LoginScreen, type PublicDemo } from "./LoginScreen";
import { ArchitectureDiagram, PipelineTraceView, RouteBanner } from "./PipelineView";
import { ReviewQueue } from "./ReviewQueue";
import type { Decision, PipelineTrace, Route } from "./pipeline";

const API = import.meta.env.VITE_API_URL ?? "";
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
  draft_decision: Decision | null;
  route: Route | null;
  rationale: string;
  confidence: number;
  confidence_note: string;
  findings: Finding[];
  limitations: string[];
  latency_ms: number;
  audit_brief: string | null;
  pipeline: PipelineTrace | null;
  baseline: { model: string; decision: Decision; rationale: string } | null;
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
  draft_decision: Decision | null;
  route: Route | null;
  confidence: number | null;
};
type Metrics = {
  model: string;
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
  stp_rate: number | null;
  stp_accuracy: number | null;
  draft_accuracy: number | null;
};
type Comparison = {
  engines: Record<string, Metrics>;
  disagreements: { case_index: number; case_id: string | null; ground_truth: Decision; predictions: Record<string, Decision | null> }[];
  notes: string[];
};
type Engine = "multi_agent_pipeline" | "b1_lexical" | "llm_rag" | "compare";
type Config = {
  model: string;
  inference_type: "multi_agent_pipeline";
  pipeline: {
    version: string;
    agents: { id: string; name: string }[];
    stp_enabled: boolean;
    stp_thresholds: Record<string, number>;
    llm_assist_enabled: boolean;
    knowledge_backends: Record<string, string>;
    knowledge_fallbacks: string[];
    calibration: Record<string, unknown>;
  };
  public_demo: PublicDemo | null;
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
  available_models: string[];
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
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [engine, setEngine] = useState<Engine>("multi_agent_pipeline");
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
    setComparison(null);
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
      const isCompare = engine === "compare";
      const response = await fetch(`${API}/api/v1/evaluations${isCompare ? "/compare" : ""}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(isCompare ? { cases, include_llm: Boolean(config?.llm_enabled) } : { cases, engine }),
      });
      if (!response.ok) throw new Error(await readError(response));
      if (isCompare) setComparison(await response.json());
      else setMetrics(await response.json());
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
    return <LoginScreen onLogin={login} demo={config?.public_demo} />;
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
          <div className="safety-note"><ShieldCheck size={15} /><p>Research prototype only.<br />Only high-confidence cases are auto-finalised; all others need human sign-off.</p></div>
          <div className="sidebar-version">CLAIMLAB <span>·</span> v0.3 five-agent pipeline</div>
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
          {config?.public_demo && (
            <div className="demo-banner" role="note"><FlaskConical size={15} /><span><strong>Public demo.</strong> {config.public_demo.notice} Never enter real patient data.</span></div>
          )}
          {error && <div className="error-banner" role="alert"><CircleAlert size={17} /><span>{error}</span></div>}

          {apiReady === false && (
            <div className="error-banner" role="alert">
              <CircleAlert size={17} />
              <span>Backend is not reachable at the configured API URL. Start the API (see README.md), then select "Refresh API status" in the sidebar.</span>
            </div>
          )}

          {view === "adjudication" && (
            <>
              <PageHeading eyebrow="FIVE-AGENT EVIDENCE RECONCILIATION" title="Case review" subtitle="Policy logic, clinical evidence, knowledge linking, reconciliation and calibrated confidence — then straight-through processing or human review." action={<button className="button secondary" onClick={loadDemo}><BookOpen size={15} /> Load example</button>} />
              <div className="workflow-strip" aria-label="Review workflow">
                <WorkflowStep number="1" title="Case inputs" active={!result} complete={Boolean(result)} />
                <span className="workflow-connector" />
                <WorkflowStep number="2" title="Agents 1–5" active={false} complete={Boolean(result)} />
                <span className="workflow-connector" />
                <WorkflowStep number="3" title={result?.route === "STRAIGHT_THROUGH" ? "Straight-through" : result ? "Human review" : "Route"} active={Boolean(result)} complete={result?.route === "STRAIGHT_THROUGH"} />
                <span className="workflow-caption"><ShieldCheck size={14} /> STP: APPROVE ≥ {config?.pipeline.stp_thresholds.APPROVE ?? 0.9} · DENY ≥ {config?.pipeline.stp_thresholds.DENY ?? 0.95}</span>
              </div>
              <section className="workspace-grid" aria-label="Case review workspace">
                <form className="panel case-card" onSubmit={submitCase}>
                  <PanelHeading icon={<FileText size={17} />} tone="blue" title="Case information" subtitle="Inference-only fields. Do not enter real patient data." step="INPUT" />
                  <div className="field-row">
                    <Field label="Case reference" hint="Optional identifier shown in the audit brief">
                      <input maxLength={100} value={form.case_id ?? ""} onChange={(e) => update("case_id", e.target.value || undefined)} placeholder="e.g. claim-2026-001" autoComplete="off" />
                    </Field>
                  </div>
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
                      {busy === "case" ? "Running agents…" : "Run adjudication pipeline"} <ArrowRight size={15} />
                    </button>
                  </div>
                </form>

                <section className="panel findings-panel" aria-live="polite" aria-busy={busy === "case"}>
                  <PanelHeading icon={<Sparkles size={17} />} tone="violet" title="Pipeline outcome" subtitle="Routing, agent trace and audit brief (stateless preview — not persisted)" />
                  {busy === "case" ? <LoadingState /> : result ? <ResultView result={result} /> : <EmptyState />}
                </section>
              </section>
              <section className="quality-row" aria-label="System limitations">
                <QualityItem icon={<Zap size={16} />} title="Calibrated routing" detail="Only drafts above the decision-specific threshold go straight through." />
                <QualityItem icon={<FileCheck2 size={16} />} title="Traceable reasoning" detail="Every rule, predicate, ontology path and confidence factor is shown." />
                <QualityItem icon={<Info size={16} />} title="Research only" detail="Not clinically or legally validated, and not production-ready." />
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
                      <label className="engine-select">
                        <span>Engine</span>
                        <select value={engine} onChange={(e) => setEngine(e.target.value as Engine)}>
                          <option value="multi_agent_pipeline">Five-agent pipeline</option>
                          <option value="b1_lexical">B1 lexical baseline</option>
                          {config?.llm_enabled && <option value="llm_rag">LLM / RAG (network)</option>}
                          <option value="compare">Compare engines</option>
                        </select>
                      </label>
                      <button className="button primary" disabled={busy !== null || !apiReady || !labelsText.trim()} onClick={() => void runEvaluation()}>
                        {busy === "eval" ? <LoaderCircle className="spin" size={16} /> : engine === "compare" ? <GitCompare size={16} /> : <Gauge size={16} />}
                        {busy === "eval" ? "Evaluating…" : engine === "compare" ? "Run comparison" : "Run evaluation"}
                      </button>
                    </div>
                  </div>
                  {comparison ? <ComparisonView comparison={comparison} /> : metrics ? <MetricsView metrics={metrics} /> : <div className="metrics-placeholder"><div className="placeholder-icon"><Gauge size={21} /></div><strong>Evaluation results will appear here</strong><span>Accuracy, STP rate and accuracy, false decision rates, confusion matrix, and case-level audit rows — or a side-by-side engine comparison.</span></div>}
                </div>
              </section>
              <div className="callout info-callout"><Info size={17} /><p><strong>Interpret metrics carefully.</strong> No frozen benchmark is loaded. The sample is small and project-labelled, and was partly used while developing the pipeline (see data/README.md). It cannot establish generalization, safety, or production readiness.</p></div>
            </>
          )}

          {view === "review" && (
            <>
              <PageHeading eyebrow="AUDITED ROUTING & HUMAN REVIEW" title="Review queue" subtitle="Every submitted case is persisted with its full agent trace. High-confidence drafts are finalised by straight-through processing; everything else waits for a different reviewer." />
              <ReviewQueue authFetch={authFetch} currentUsername={session.username} currentRole={session.role} />
            </>
          )}

          {view === "research" && (
            <>
              <PageHeading eyebrow="SYSTEM TRANSPARENCY" title="Model & data" subtitle="Know what is running—and what evidence is not available—before interpreting a result." />
              <section className="panel architecture-panel">
                <PanelHeading icon={<Network size={17} />} tone="violet" title="Adjudication architecture" subtitle={`Five-agent evidence-reconciliation pipeline v${config?.pipeline.version ?? "?"}`} />
                <ArchitectureDiagram thresholds={config?.pipeline.stp_thresholds} />
              </section>
              <div className="research-grid">
                <section className="panel">
                  <PanelHeading icon={<Workflow size={17} />} tone="blue" title="Inference configuration" subtitle="Reported by the backend at runtime" />
                  <div className="detail-list">
                    <DetailRow label="Primary engine" value={config?.model ?? "Unavailable"} badge="5 AGENTS" />
                    <DetailRow label="Straight-through processing" value={config ? (config.pipeline.stp_enabled ? `APPROVE ≥ ${config.pipeline.stp_thresholds.APPROVE} · DENY ≥ ${config.pipeline.stp_thresholds.DENY}` : "Disabled") : "Unknown"} badge={config?.pipeline.stp_enabled ? "ENABLED" : "OFF"} />
                    <DetailRow label="Confidence calibration" value={config ? `${String(config.pipeline.calibration.method ?? "identity")} (n=${String(config.pipeline.calibration.n ?? 0)})` : "Unknown"} />
                    <DetailRow label="Embedder (Agent 2)" value={config?.pipeline.knowledge_backends.embedder ?? "Unknown"} />
                    <DetailRow label="Vector store (Agent 3)" value={config?.pipeline.knowledge_backends.vector_store ?? "Unknown"} />
                    <DetailRow label="Graph store (Agent 3)" value={config?.pipeline.knowledge_backends.graph_store ?? "Unknown"} />
                    <DetailRow label="Ontology" value={config?.pipeline.knowledge_backends.ontology ?? "Unknown"} />
                    <DetailRow label="Deployment environment" value={config?.environment ?? "Unknown"} badge={config?.environment === "production" ? "PRODUCTION ENV" : (config?.environment ?? "").toUpperCase() || undefined} />
                    <DetailRow label="Authentication" value="Required (JWT bearer)" badge="ENFORCED" />
                    <DetailRow label="Audit trail" value="Every case persisted with its full agent trace" badge="ENABLED" />
                    <DetailRow
                      label="Gemini (LLM)"
                      value={config?.llm_enabled ? `${config.llm_analysis_model ?? "configured model"} — assists Agents 1 & 5, plus comparison engine` : "Not configured"}
                      badge={config?.llm_enabled ? (config.pipeline.llm_assist_enabled ? "ASSIST ON" : "COMPARISON ONLY") : "NO LLM"}
                    />
                    <DetailRow label="Clinically/legally validated" value="No" badge="NOT VALIDATED" />
                  </div>
                  {config && config.pipeline.knowledge_fallbacks.length > 0 && (
                    <div className="callout warn-callout"><CircleAlert size={14} /><p><strong>Knowledge backend fallback:</strong> {config.pipeline.knowledge_fallbacks.join(" ")}</p></div>
                  )}
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
                  <PanelHeading icon={<Layers3 size={17} />} tone="violet" title="Engines & baselines" subtitle="Compare them side by side on the Evaluation page" />
                  <div className="baseline-list">
                    <div className="baseline-row"><span>Five-agent pipeline (primary)</span><span className="availability available">Active</span></div>
                    <div className="baseline-row"><span>B1 · Lexical rule baseline (this repo's original engine)</span><span className="availability available">Available</span></div>
                    <div className="baseline-row"><span>B2 · KAMEL-inspired (original)</span><span className="availability unavailable">Unavailable · KAMEL VAs live in Agent 3</span></div>
                    <div className="baseline-row">
                      <span>B3 · Single LLM / RAG</span>
                      <span className={`availability ${config?.llm_enabled ? "available" : "unavailable"}`}>
                        {config?.llm_enabled ? "Available (comparison engine)" : "Unavailable"}
                      </span>
                    </div>
                  </div>
                  <div className="callout"><Info size={16} /><p>{config?.llm_enabled ? "The B3-style LLM/RAG engine is a from-scratch reimplementation, not the original research code. Gemini also assists Agent 1 (clause parsing) and Agent 5 (brief prose) with validated output." : "Configure CAF_MODEL_API_KEY (a Google Gemini API key) to enable LLM assistance for Agents 1 and 5 and a comparison-only B3-style LLM/RAG engine. The original B1/B2 research code and frozen benchmark are not in this workspace."}</p></div>
                </section>
                <section className="panel">
                  <PanelHeading icon={<ShieldCheck size={17} />} tone="green" title="Readiness checklist" subtitle="Requirements before any operational pilot" />
                  <ul className="readiness-list">
                    <li><Check size={15} className="readiness-met" /><span>Authentication, role-based access, and audited human review</span><b className="met">Met</b></li>
                    <li><Check size={15} className="readiness-met" /><span>Traceable multi-agent reasoning with calibrated STP routing</span><b className="met">Met</b></li>
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
            <span><ShieldCheck size={14} /> Case review is a stateless preview · persisted, routed cases live in the Review queue</span>
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
  return <div className="empty-state"><div className="empty-illustration"><Activity size={24} /></div><strong>Ready to adjudicate</strong><p>Submit inference-only information to run the five agents and see how the case would be routed.</p><div className="empty-caption"><ShieldCheck size={14} /> Preview only — nothing is persisted from this screen</div></div>;
}

function LoadingState() {
  return <div className="empty-state loading-state"><LoaderCircle className="spin" size={27} /><strong>Running the five-agent pipeline</strong><p>Parsing policy logic, extracting clinical evidence, linking knowledge, reconciling and calibrating confidence.</p></div>;
}

function ResultView({ result }: { result: Result }) {
  const trace = result.pipeline;
  const draft = result.draft_decision ?? result.decision;
  return (
    <div className="result-content">
      {trace && result.route ? (
        <RouteBanner
          decision={result.decision}
          draft={draft}
          route={result.route}
          confidence={result.confidence}
          thresholds={trace.confidence.thresholds}
          reasons={trace.confidence.abstention_reasons}
        />
      ) : (
        <div className={`decision ${result.decision.toLowerCase()}`}><strong>{result.decision.replace("_", " ")}</strong><p>{result.rationale}</p></div>
      )}
      <div className={`decision ${draft.toLowerCase()}`}>
        <div className="decision-overline"><span>AGENT 4 DRAFT DECISION</span><span className="decision-status">{draft.replace("_", " ")}</span></div>
        <p>{result.rationale}</p>
      </div>
      <div className="result-metadata">
        <div><span>Calibrated confidence</span><strong>{Math.round(result.confidence * 100)}<small>/100</small></strong></div>
        <div><span>Processing time</span><strong>{result.latency_ms.toFixed(1)}<small> ms</small></strong></div>
        <div><span>Case reference</span><strong className="case-reference">{result.case_id ?? "Not provided"}</strong></div>
      </div>
      <div className="score-warning"><Info size={14} /><span>{result.confidence_note}</span></div>
      {result.baseline && (
        <div className={`baseline-compare ${result.baseline.decision !== draft ? "differs" : ""}`}>
          <Layers3 size={13} />
          <span>B1 lexical baseline: <b>{result.baseline.decision.replace("_", " ")}</b>{result.baseline.decision !== draft ? " — differs from the pipeline draft" : " — agrees with the pipeline draft"}</span>
        </div>
      )}
      <div className="section-divider"><span>Agent trace</span><span>5 agents</span></div>
      {trace ? <PipelineTraceView trace={trace} /> : (
        <div className="trace-list">{result.findings.map((finding, index) => <div className="trace-item" key={finding.agent}><div className={`trace-index ${finding.status}`}>{index + 1}</div><div className="trace-main"><strong>{finding.agent.replaceAll("_", " ")}</strong><p>{finding.summary}</p></div><span className={`status ${finding.status}`}>{finding.status}</span></div>)}</div>
      )}
      {result.route !== "STRAIGHT_THROUGH" && (
        <div className="review-action"><ShieldAlert size={17} /><div><strong>Human review required</strong><span>Submit this case through the Review queue to record an audited decision.</span></div></div>
      )}
      {result.llm_result && (
        <LLMComparisonPanel llm={result.llm_result} ruleDecision={draft} />
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
            <span>The pipeline draft and the LLM/RAG comparison engine reached different conclusions for this case. Review both carefully.</span>
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
      <div className="score-warning"><Info size={14} /><span>Secondary comparison signal only, produced by a separately configured model. It never overrides the pipeline's draft or routing and is not itself a decision. Disagreement, when it happens, is useful information for the human reviewer.</span></div>
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
  const routed = metrics.stp_rate !== null;
  return (
    <div className="metrics-results">
      <div className="metrics-summary"><span>RUN SUMMARY · {metrics.model.replaceAll("_", " ").toUpperCase()}</span><strong>{metrics.evaluated_cases} <small>evaluated</small></strong><i>{metrics.errored_cases} errors / {metrics.total_cases} total</i></div>
      <div className="metric-grid">
        <Metric label="Accuracy (final outcome)" value={metrics.accuracy} />
        <Metric label="Macro F1" value={metrics.macro_f1} />
        <Metric label="False approval rate" value={metrics.false_approval_rate} />
        <Metric label="False denial rate" value={metrics.false_denial_rate} />
        <Metric label="Human review rate" value={metrics.human_review_rate} />
        <Metric label="Mean latency" value={`${metrics.average_latency_ms.toFixed(1)} ms`} />
        {routed && <Metric label="STP rate" value={metrics.stp_rate ?? 0} />}
        {routed && <Metric label="STP accuracy" value={metrics.stp_accuracy ?? "—"} />}
        {routed && <Metric label="Draft accuracy (pre-routing)" value={metrics.draft_accuracy ?? 0} />}
      </div>
      <div className="class-table"><div className="table-row table-head"><span>Outcome</span><span>Precision</span><span>Recall</span><span>F1 · n</span></div>{Object.entries(metrics.per_class).map(([label, value]) => <div className="table-row" key={label}><span>{label.replace("_", " ")}</span><span>{value.precision.toFixed(2)}</span><span>{value.recall.toFixed(2)}</span><span>{value.f1.toFixed(2)} · {value.support}</span></div>)}</div>
      <div className="class-table"><div className="table-row table-head"><span>Actual \ Predicted</span><span>Approve</span><span>Deny</span><span>Review</span></div>{Object.entries(metrics.confusion_matrix).map(([actual, predicted]) => <div className="table-row" key={actual}><span>{actual.replace("_", " ")}</span><span>{predicted.APPROVE}</span><span>{predicted.DENY}</span><span>{predicted.HUMAN_REVIEW}</span></div>)}</div>
      <div className="case-results"><strong>Case-level audit</strong>{metrics.case_results.map((item) => (
        <div className={`case-result ${item.prediction && item.prediction !== item.ground_truth ? "mismatch" : ""}`} key={item.case_index}>
          <span>#{item.case_index}{item.case_id ? ` · ${item.case_id}` : ""}</span>
          <span>Actual: {item.ground_truth.replace("_", " ")}</span>
          <span>{item.status === "error" ? item.error : `Predicted: ${item.prediction?.replace("_", " ")}`}{item.route && <> · {item.route === "STRAIGHT_THROUGH" ? "STP" : `queue (draft ${item.draft_decision?.replace("_", " ")})`} · {item.confidence?.toFixed(2)}</>}</span>
        </div>
      ))}</div>
      <p className="metric-footnote">Errors remain in accuracy and recall support. Precision uses successful predictions only. Pipeline accuracy scores the routed outcome: abstentions count as HUMAN REVIEW. See README for metric definitions.</p>
      {metrics.errors.length > 0 && <div className="eval-errors">{metrics.errors.map((item) => <p key={item}>{item}</p>)}</div>}
    </div>
  );
}

function ComparisonView({ comparison }: { comparison: Comparison }) {
  const engines = Object.entries(comparison.engines);
  const rows: Array<[string, (m: Metrics) => string]> = [
    ["Accuracy", (m) => `${(m.accuracy * 100).toFixed(1)}%`],
    ["Macro F1", (m) => `${(m.macro_f1 * 100).toFixed(1)}%`],
    ["False approval rate", (m) => `${(m.false_approval_rate * 100).toFixed(1)}%`],
    ["False denial rate", (m) => `${(m.false_denial_rate * 100).toFixed(1)}%`],
    ["Human review rate", (m) => `${(m.human_review_rate * 100).toFixed(1)}%`],
    ["STP rate / accuracy", (m) => m.stp_rate === null ? "—" : `${(m.stp_rate * 100).toFixed(0)}% / ${m.stp_accuracy === null ? "—" : `${(m.stp_accuracy * 100).toFixed(0)}%`}`],
    ["Errors", (m) => String(m.errored_cases)],
    ["Mean latency", (m) => `${m.average_latency_ms.toFixed(1)} ms`],
  ];
  return (
    <div className="metrics-results comparison">
      <div className="metrics-summary"><span>ENGINE COMPARISON</span><strong>{engines.length} <small>engines</small></strong><i>{comparison.disagreements.length} disagreement(s)</i></div>
      <div className="class-table compare-table" style={{ ["--cols" as string]: engines.length }}>
        <div className="table-row table-head"><span>Metric</span>{engines.map(([name]) => <span key={name}>{name.replaceAll("_", " ")}</span>)}</div>
        {rows.map(([title, fmt]) => <div className="table-row" key={title}><span>{title}</span>{engines.map(([name, m]) => <span key={name}>{fmt(m)}</span>)}</div>)}
      </div>
      <div className="case-results"><strong>Disagreements</strong>
        {comparison.disagreements.length === 0 && <p className="metric-footnote">All engines agree on every case.</p>}
        {comparison.disagreements.map((d) => (
          <div className="case-result" key={d.case_index}>
            <span>#{d.case_index}{d.case_id ? ` · ${d.case_id}` : ""}</span>
            <span>Actual: {d.ground_truth.replace("_", " ")}</span>
            <span>{Object.entries(d.predictions).map(([name, p]) => `${name.replaceAll("_", " ")}: ${p ? p.replace("_", " ") : "error"}`).join(" · ")}</span>
          </div>
        ))}
      </div>
      {comparison.notes.map((n) => <p className="metric-footnote" key={n}>{n}</p>)}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  const display = typeof value === "number" ? `${(value * 100).toFixed(1)}%` : value;
  return <div className="metric"><span>{label}</span><strong>{display}</strong></div>;
}

export default App;

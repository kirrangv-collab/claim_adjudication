import { useEffect, useState } from "react";
import {
  ArrowLeft, CircleAlert, ClipboardCheck, FilePlus2, LoaderCircle,
  Lock, RefreshCw, ShieldAlert, ShieldCheck, UserCheck,
} from "lucide-react";

type Decision = "APPROVE" | "DENY" | "HUMAN_REVIEW";
type HumanDecision = "APPROVED" | "DENIED" | "ESCALATED";

type CaseInput = {
  case_id?: string;
  diagnosis: string;
  requested_service: string;
  policy_text: string;
  clinical_evidence: string;
  evidence_state: "documented" | "incomplete" | "conflicting";
};

type Finding = { agent: string; status: string; summary: string; evidence: string[] };

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

type ReviewSummary = {
  id: number;
  case_id: string | null;
  suggested_decision: Decision;
  suggested_confidence: number;
  llm_decision: Decision | null;
  status: "pending_review" | "reviewed";
  created_by: string;
  created_at: string;
  human_decision: HumanDecision | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
};

type ReviewDetail = ReviewSummary & {
  inference_case: CaseInput;
  suggested_rationale: string;
  findings: Finding[];
  limitations: string[];
  human_notes: string | null;
  llm_result: LLMResult | null;
};

type ListResponse = { items: ReviewSummary[]; total: number; page: number; page_size: number };

const emptyCase: CaseInput = {
  diagnosis: "",
  requested_service: "",
  policy_text: "",
  clinical_evidence: "",
  evidence_state: "documented",
};

async function readError(response: Response) {
  const body = await response.json().catch(() => null);
  return typeof body?.detail === "string" ? body.detail : `Request failed (${response.status})`;
}

export function ReviewQueue({
  authFetch,
  currentUsername,
}: {
  authFetch: (path: string, init?: RequestInit) => Promise<Response>;
  currentUsername: string;
}) {
  const [mode, setMode] = useState<"list" | "new" | "detail">("list");
  const [statusFilter, setStatusFilter] = useState<"" | "pending_review" | "reviewed">("");
  const [list, setList] = useState<ListResponse | null>(null);
  const [detail, setDetail] = useState<ReviewDetail | null>(null);
  const [form, setForm] = useState<CaseInput>(emptyCase);
  const [decision, setDecision] = useState<HumanDecision>("APPROVED");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState<"list" | "create" | "decide" | "reopen" | null>(null);
  const [error, setError] = useState("");

  async function loadList() {
    setBusy("list");
    setError("");
    try {
      const params = new URLSearchParams({ page: "1", page_size: "20" });
      if (statusFilter) params.set("status", statusFilter);
      const response = await authFetch(`/api/v1/reviews?${params}`);
      if (!response.ok) throw new Error(await readError(response));
      setList(await response.json());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to load the review queue.");
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    if (mode === "list") void loadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, statusFilter]);

  async function openDetail(id: number) {
    setError("");
    try {
      const response = await authFetch(`/api/v1/reviews/${id}`);
      if (!response.ok) throw new Error(await readError(response));
      const body: ReviewDetail = await response.json();
      setDetail(body);
      setDecision("APPROVED");
      setNotes("");
      setMode("detail");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to load this case.");
    }
  }

  async function submitNewCase(event: React.FormEvent) {
    event.preventDefault();
    setBusy("create");
    setError("");
    try {
      const response = await authFetch("/api/v1/reviews", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!response.ok) throw new Error(await readError(response));
      const body: ReviewDetail = await response.json();
      setForm(emptyCase);
      setDetail(body);
      setDecision("APPROVED");
      setNotes("");
      setMode("detail");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to submit this case.");
    } finally {
      setBusy(null);
    }
  }

  async function recordDecision() {
    if (!detail) return;
    setBusy("decide");
    setError("");
    try {
      const response = await authFetch(`/api/v1/reviews/${detail.id}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ human_decision: decision, human_notes: notes }),
      });
      if (!response.ok) throw new Error(await readError(response));
      setDetail(await response.json());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to record this decision.");
    } finally {
      setBusy(null);
    }
  }

  async function reopenCase() {
    if (!detail) return;
    setBusy("reopen");
    setError("");
    try {
      const response = await authFetch(`/api/v1/reviews/${detail.id}/reopen`, { method: "POST" });
      if (!response.ok) throw new Error(await readError(response));
      setDetail(await response.json());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to reopen this case.");
    } finally {
      setBusy(null);
    }
  }

  const isOwnCase = detail?.created_by === currentUsername;

  return (
    <section className="review-queue">
      {error && <div className="error-banner" role="alert"><CircleAlert size={17} /><span>{error}</span></div>}

      {mode !== "list" && (
        <button className="button secondary back-button" onClick={() => setMode("list")}>
          <ArrowLeft size={15} /> Back to queue
        </button>
      )}

      {mode === "list" && (
        <>
          <div className="queue-toolbar">
            <div className="queue-filters">
              {(["", "pending_review", "reviewed"] as const).map((value) => (
                <button
                  key={value || "all"}
                  className={`filter-chip ${statusFilter === value ? "active" : ""}`}
                  onClick={() => setStatusFilter(value)}
                >
                  {value === "" ? "All" : value === "pending_review" ? "Pending" : "Reviewed"}
                </button>
              ))}
            </div>
            <div className="queue-actions">
              <button className="button secondary" onClick={() => void loadList()} disabled={busy !== null}>
                <RefreshCw size={14} className={busy === "list" ? "spin" : ""} /> Refresh
              </button>
              <button className="button primary" onClick={() => { setForm(emptyCase); setMode("new"); }}>
                <FilePlus2 size={15} /> Submit case for review
              </button>
            </div>
          </div>

          {busy === "list" && !list ? (
            <div className="empty-state"><LoaderCircle className="spin" size={24} /><strong>Loading review queue…</strong></div>
          ) : !list || list.items.length === 0 ? (
            <div className="empty-state">
              <div className="empty-illustration"><ClipboardCheck size={24} /></div>
              <strong>No cases in this view</strong>
              <p>Submit a case for review, or change the filter above.</p>
            </div>
          ) : (
            <div className="queue-table">
              <div className="queue-row queue-head">
                <span>Case</span><span>Suggested</span><span>LLM signal</span><span>Status</span><span>Created by</span><span>Decision</span>
              </div>
              {list.items.map((item) => (
                <button className="queue-row queue-item" key={item.id} onClick={() => void openDetail(item.id)}>
                  <span>#{item.id}{item.case_id ? ` · ${item.case_id}` : ""}</span>
                  <span className={`pill signal-${item.suggested_decision.toLowerCase()}`}>{item.suggested_decision.replace("_", " ")}</span>
                  <span className={item.llm_decision ? `pill signal-${item.llm_decision.toLowerCase()} ${item.llm_decision !== item.suggested_decision ? "signal-disagree" : ""}` : "pill signal-none"}>
                    {item.llm_decision ? item.llm_decision.replace("_", " ") : "—"}
                  </span>
                  <span className={`pill status-${item.status}`}>{item.status === "pending_review" ? "Pending" : "Reviewed"}</span>
                  <span>{item.created_by}</span>
                  <span>{item.human_decision ?? "—"}</span>
                </button>
              ))}
            </div>
          )}
        </>
      )}

      {mode === "new" && (
        <form className="panel case-card" onSubmit={submitNewCase}>
          <div className="panel-heading">
            <span className="section-icon blue"><FilePlus2 size={17} /></span>
            <div className="panel-heading-copy"><h2>Submit a case for review</h2><p>This creates a permanent, audited record. It is not a final decision until a different reviewer signs off.</p></div>
          </div>
          <div className="field-row">
            <label className="field"><span className="field-label">Diagnosis<i>Required</i></span>
              <input required maxLength={500} value={form.diagnosis} onChange={(e) => setForm((f) => ({ ...f, diagnosis: e.target.value }))} />
            </label>
            <label className="field"><span className="field-label">Requested service<i>Required</i></span>
              <input required maxLength={500} value={form.requested_service} onChange={(e) => setForm((f) => ({ ...f, requested_service: e.target.value }))} />
            </label>
          </div>
          <label className="field"><span className="field-label">Applicable policy language<i>Required</i></span>
            <textarea required rows={4} maxLength={20000} value={form.policy_text} onChange={(e) => setForm((f) => ({ ...f, policy_text: e.target.value }))} />
          </label>
          <label className="field"><span className="field-label">Clinical evidence summary<i>Required</i></span>
            <textarea required rows={4} maxLength={10000} value={form.clinical_evidence} onChange={(e) => setForm((f) => ({ ...f, clinical_evidence: e.target.value }))} />
          </label>
          <label className="field"><span className="field-label">Evidence completeness</span>
            <select value={form.evidence_state} onChange={(e) => setForm((f) => ({ ...f, evidence_state: e.target.value as CaseInput["evidence_state"] }))}>
              <option value="documented">Documented</option>
              <option value="incomplete">Incomplete / missing information</option>
              <option value="conflicting">Conflicting evidence</option>
            </select>
          </label>
          <button className="button primary" type="submit" disabled={busy !== null}>
            {busy === "create" ? <LoaderCircle className="spin" size={16} /> : <FilePlus2 size={16} />}
            {busy === "create" ? "Submitting…" : "Submit for review"}
          </button>
        </form>
      )}

      {mode === "detail" && detail && (
        <div className="panel case-card review-detail">
          <div className="panel-heading">
            <span className="section-icon violet"><ClipboardCheck size={17} /></span>
            <div className="panel-heading-copy"><h2>Case #{detail.id}{detail.case_id ? ` · ${detail.case_id}` : ""}</h2><p>Created by {detail.created_by}</p></div>
            <span className={`pill status-${detail.status}`}>{detail.status === "pending_review" ? "Pending review" : "Reviewed"}</span>
          </div>

          <div className={`decision ${detail.suggested_decision.toLowerCase()}`}>
            <span className="decision-kicker">PROTOTYPE SUGGESTION — NOT A DECISION</span>
            <strong>{detail.suggested_decision.replace("_", " ")}</strong>
            <p>{detail.suggested_rationale}</p>
          </div>

          {detail.llm_result && (
            <div className={`decision llm-decision ${(detail.llm_result.decision ?? "unavailable").toLowerCase()}`}>
              <span className="decision-kicker">LLM + RAG COMPARISON SIGNAL — SECONDARY ONLY</span>
              <strong>{detail.llm_result.decision ? detail.llm_result.decision.replace("_", " ") : "Unavailable"}</strong>
              <p>{detail.llm_result.decision ? detail.llm_result.rationale : (detail.llm_result.error ?? "The model did not return a usable result.")}</p>
              {detail.llm_result.decision && detail.llm_result.decision !== detail.suggested_decision && (
                <p className="signal-disagree-note"><CircleAlert size={12} /> Disagrees with the rule-based suggestion above.</p>
              )}
            </div>
          )}

          <div className="detail-grid">
            <div><strong>Diagnosis</strong><span>{detail.inference_case.diagnosis}</span></div>
            <div><strong>Requested service</strong><span>{detail.inference_case.requested_service}</span></div>
          </div>
          <div className="detail-block"><strong>Policy text</strong><p>{detail.inference_case.policy_text}</p></div>
          <div className="detail-block"><strong>Clinical evidence</strong><p>{detail.inference_case.clinical_evidence}</p></div>

          {detail.status === "reviewed" ? (
            <div className="human-decision-card">
              <div className="human-decision-heading"><UserCheck size={17} /><strong>Human decision recorded</strong></div>
              <p><b>{detail.human_decision}</b> by {detail.reviewed_by} on {detail.reviewed_at ? new Date(detail.reviewed_at).toLocaleString() : ""}</p>
              {detail.human_notes && <p className="human-decision-notes">"{detail.human_notes}"</p>}
              <button className="button secondary" onClick={() => void reopenCase()} disabled={busy !== null}>
                {busy === "reopen" ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />} Reopen (admin only)
              </button>
            </div>
          ) : isOwnCase ? (
            <div className="maker-checker-notice">
              <Lock size={16} />
              <div><strong>Segregation of duties</strong><p>You submitted this case, so you cannot also record its human decision. Another reviewer must complete this step.</p></div>
            </div>
          ) : (
            <div className="decision-form">
              <div className="decision-form-heading"><ShieldAlert size={16} /> Record the human review decision</div>
              <label className="field"><span className="field-label">Decision</span>
                <select value={decision} onChange={(e) => setDecision(e.target.value as HumanDecision)}>
                  <option value="APPROVED">Approved</option>
                  <option value="DENIED">Denied</option>
                  <option value="ESCALATED">Escalated for further review</option>
                </select>
              </label>
              <label className="field"><span className="field-label">Notes</span>
                <textarea rows={3} maxLength={4000} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Document the basis for this decision…" />
              </label>
              <button className="button primary" onClick={() => void recordDecision()} disabled={busy !== null}>
                {busy === "decide" ? <LoaderCircle className="spin" size={16} /> : <ShieldCheck size={16} />}
                {busy === "decide" ? "Recording…" : "Record final decision"}
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

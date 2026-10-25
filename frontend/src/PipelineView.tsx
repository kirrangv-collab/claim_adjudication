import { useState } from "react";
import {
  ArrowRight, BookOpenCheck, Brain, Check, CircleAlert, FileSearch, GitBranch, Network,
  Route as RouteIcon, Scale, ShieldAlert, ShieldCheck, UserCheck, Zap,
} from "lucide-react";
import {
  AGENT_LABELS, ASTNode, Decision, GraphPath, PipelineTrace, Route, label,
} from "./pipeline";

const AGENT_ICONS: Record<string, React.ReactNode> = {
  policy_logic: <BookOpenCheck size={14} />,
  clinical_evidence: <FileSearch size={14} />,
  knowledge_linker: <Network size={14} />,
  reconciliation: <Scale size={14} />,
  confidence_explainability: <Brain size={14} />,
};

export function RouteBanner({ decision, draft, route, confidence, thresholds, reasons, reopened = false }: {
  decision: Decision;
  draft: Decision;
  route: Route;
  confidence: number;
  thresholds: Record<string, number>;
  reasons: string[];
  reopened?: boolean;
}) {
  const stp = route === "STRAIGHT_THROUGH" && !reopened;
  const threshold = thresholds[draft];
  const kicker = reopened
    ? "REOPENED BY ADMIN · ORIGINALLY STRAIGHT-THROUGH"
    : stp ? "STRAIGHT-THROUGH PROCESSING" : "HUMAN REVIEW QUEUE · CALIBRATED ABSTENTION";
  const title = reopened
    ? `Back in the human queue (draft: ${label(draft)})`
    : stp ? `Finalised: ${label(decision)}` : `Routed to human review (draft: ${label(draft)})`;
  return (
    <div className={`route-banner ${stp ? "stp" : "queue"} ${decision.toLowerCase()}`}>
      <div className="route-banner-head">
        <span className="route-icon">{stp ? <Zap size={16} /> : <UserCheck size={16} />}</span>
        <div>
          <span className="route-kicker">{kicker}</span>
          <strong>{title}</strong>
        </div>
      </div>
      <div className="confidence-meter" aria-label={`Calibrated confidence ${Math.round(confidence * 100)} percent`}>
        <div className="confidence-track">
          <div className="confidence-fill" style={{ width: `${Math.round(confidence * 100)}%` }} />
          {threshold !== undefined && <div className="confidence-threshold" style={{ left: `${threshold * 100}%` }} title={`${draft} STP threshold ${threshold}`} />}
        </div>
        <span>Calibrated confidence <b>{confidence.toFixed(3)}</b>{threshold !== undefined && <> · {label(draft)} threshold {threshold.toFixed(2)}</>}</span>
      </div>
      {reasons.length > 0 && <ul className="route-reasons">{reasons.map((r) => <li key={r}><ShieldAlert size={12} />{r}</li>)}</ul>}
    </div>
  );
}

export function PipelineFlow({ trace, active, onSelect }: { trace: PipelineTrace; active: string; onSelect: (agent: string) => void }) {
  const statuses: Record<string, string> = {
    policy_logic: `${trace.policy_logic.rules.length} rules`,
    clinical_evidence: `${trace.clinical_evidence.icd_codes.length} ICD codes`,
    knowledge_linker: trace.knowledge_links.diagnosis_linked_to_policy ? "diagnosis linked" : "not linked",
    reconciliation: label(trace.reconciliation.decision),
    confidence_explainability: trace.confidence.route === "STRAIGHT_THROUGH" ? "STP" : "human queue",
  };
  return (
    <div className="pipeline-flow" role="tablist" aria-label="Agent pipeline">
      {Object.keys(AGENT_LABELS).map((agent, index) => (
        <button
          key={agent}
          role="tab"
          aria-selected={active === agent}
          className={`pipeline-step ${active === agent ? "active" : ""}`}
          onClick={() => onSelect(agent)}
        >
          <span className="pipeline-step-index">{AGENT_ICONS[agent]}</span>
          <span className="pipeline-step-copy">
            <strong>{index + 1}. {AGENT_LABELS[agent].split(" · ")[1]}</strong>
            <small>{statuses[agent]} · {(trace.agent_latency_ms[agent] ?? 0).toFixed(1)} ms</small>
          </span>
        </button>
      ))}
    </div>
  );
}

export function PipelineTraceView({ trace }: { trace: PipelineTrace }) {
  const [active, setActive] = useState("reconciliation");
  return (
    <div className="pipeline-trace">
      <PipelineFlow trace={trace} active={active} onSelect={setActive} />
      <div className="agent-panel" role="tabpanel">
        {active === "policy_logic" && <PolicyLogicPanel trace={trace} />}
        {active === "clinical_evidence" && <ClinicalEvidencePanel trace={trace} />}
        {active === "knowledge_linker" && <KnowledgePanel trace={trace} />}
        {active === "reconciliation" && <ReconciliationPanel trace={trace} />}
        {active === "confidence_explainability" && <ConfidencePanel trace={trace} />}
      </div>
    </div>
  );
}

export function AstTree({ node }: { node: ASTNode }) {
  if (node.op === "PRED") {
    return (
      <div className="ast-leaf">
        <span className="ast-op pred">PRED</span>
        <span>{node.text}</span>
        {node.quantity && <span className="ast-quantity">{node.quantity.op} {node.quantity.value} {node.quantity.unit}</span>}
      </div>
    );
  }
  if (node.op === "TRUE") return <div className="ast-leaf"><span className="ast-op">TRUE</span><span>unconditional</span></div>;
  return (
    <div className="ast-node">
      <span className={`ast-op ${node.op.toLowerCase()}`}>{node.op.replace("_", " ")}</span>
      <div className="ast-children">{node.children.map((child) => <AstTree key={child.id} node={child} />)}</div>
    </div>
  );
}

function PolicyLogicPanel({ trace }: { trace: PipelineTrace }) {
  const policy = trace.policy_logic;
  const relevance = Object.fromEntries(trace.knowledge_links.rule_relevance.map((r) => [r.rule_id, r.score]));
  return (
    <div className="agent-section">
      <p className="agent-lede">Clause-level policy rules parsed into abstract syntax trees. EXCEPT / UNLESS / ONLY IF qualifiers stay bound to the clause they appear in.</p>
      {Object.keys(policy.abbreviations).length > 0 && (
        <div className="evidence-tags">{Object.entries(policy.abbreviations).map(([abbr, long]) => <span key={abbr}>{abbr} = {long}</span>)}</div>
      )}
      <div className="rule-list">
        {policy.rules.map((rule) => (
          <details className="rule-card" key={rule.id} open={(relevance[rule.id] ?? 0) >= 0.5 && policy.rules.length <= 4}>
            <summary>
              <span className="rule-id">{rule.id}{rule.section ? ` · ${rule.section}` : ""}</span>
              <span className={`effect-pill ${rule.effect.toLowerCase()}`}>{label(rule.effect)}</span>
              {rule.modality !== "sufficient" && <span className="tag">{rule.modality}</span>}
              {rule.parse_method === "llm_assisted" && <span className="tag llm">LLM-assisted</span>}
              <span className="rule-subject">{rule.subject || "—"}</span>
              <span className="rule-meta">relevance {(relevance[rule.id] ?? 0).toFixed(2)} · parse {rule.parse_confidence.toFixed(2)}</span>
            </summary>
            <blockquote>{rule.source_text}</blockquote>
            <AstTree node={rule.condition} />
          </details>
        ))}
      </div>
      {policy.definitions.length > 0 && (
        <div className="agent-subsection"><strong>Definitions expanded in place</strong>
          {policy.definitions.map((d) => <div key={d.term} className="definition"><b>{d.term}</b><AstTree node={d.expansion} /></div>)}
        </div>
      )}
      {policy.notes.length > 0 && <p className="agent-note">{policy.notes.join(" ")}</p>}
    </div>
  );
}

function ClinicalEvidencePanel({ trace }: { trace: PipelineTrace }) {
  const ev = trace.clinical_evidence;
  return (
    <div className="agent-section">
      <p className="agent-lede">Active-encounter isolation, MCR sliding-window concept recognition ({ev.mcr_windows_scanned} windows) and {ev.embedder} ICD-10-CM normalisation with assertion status.</p>
      <div className="segment-list">
        {ev.active_segments.map((s, i) => <div key={`a${i}`} className="segment active"><span className="tag">{s.label}</span>{s.text}</div>)}
        {ev.excluded_segments.map((s, i) => <div key={`x${i}`} className={`segment excluded ${s.segment_type}`}><span className="tag">{s.segment_type}</span><s>{s.text}</s><small>{s.reason}</small></div>)}
      </div>
      <div className="mention-table">
        <div className="mention-row head"><span>Mention</span><span>Concept</span><span>Code</span><span>Assertion</span><span>Source</span></div>
        {ev.mentions.map((m, i) => (
          <div className="mention-row" key={`${m.concept_id}-${i}`}>
            <span>“{m.text}”{m.match === "embedding" && <i className="tag">≈ {m.score.toFixed(2)}</i>}{m.match === "fuzzy" && <i className="tag">spelling ≈ {m.score.toFixed(2)}</i>}</span>
            <span>{m.label}</span>
            <span>{m.code ?? "—"}</span>
            <span className={`assertion ${m.assertion}`}>{m.assertion}</span>
            <span>{m.source}</span>
          </div>
        ))}
      </div>
      {ev.quantities.length > 0 && <div className="evidence-tags">{ev.quantities.map((q, i) => <span key={i}>{q.value} {q.unit} ({q.assertion})</span>)}</div>}
    </div>
  );
}

function PathLine({ labels, relation }: { labels: string[]; relation: string }) {
  return (
    <div className="graph-path">
      {labels.map((l, i) => <span key={`${l}-${i}`}>{i > 0 && <ArrowRight size={11} />}{l}</span>)}
      <i className={`tag rel-${relation}`}>{label(relation)}</i>
    </div>
  );
}

function KnowledgePanel({ trace }: { trace: PipelineTrace }) {
  const k = trace.knowledge_links;
  const unique = (items: GraphPath[]) => {
    const seen = new Set<string>();
    return items.filter((p) => p.relation !== "exact").filter((p) => {
      const key = `${p.relation}:${p.labels.join(">")}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  };
  const evidencePaths = unique(k.predicate_links.flatMap((l) => l.evidence_paths));
  const diagnosisPaths = unique(k.diagnosis_paths);
  return (
    <div className="agent-section">
      <p className="agent-lede">Hybrid Graph-RAG: {k.backends.graph_store} graph traversal with KAMEL valid ancestors + {k.backends.vector_store} vector retrieval ({k.backends.embedder}).</p>
      <div className="agent-subsection"><strong><GitBranch size={12} /> Clinical evidence → policy condition</strong>
        {evidencePaths.length === 0 && <p className="agent-note">Only exact concept matches were needed.</p>}
        {evidencePaths.slice(0, 8).map((p, i) => <PathLine key={i} labels={p.labels} relation={p.relation} />)}
      </div>
      <div className="agent-subsection"><strong><GitBranch size={12} /> Claimed diagnosis ↔ policy indication</strong>
        {diagnosisPaths.length === 0 && <p className="agent-note">{k.diagnosis_linked_to_policy ? "Linked by an exact concept or term match." : "No ontology link found."}</p>}
        {diagnosisPaths.slice(0, 6).map((p, i) => <PathLine key={i} labels={p.labels} relation={p.relation} />)}
        <p className="agent-note">Diagnosis linked to a policy indication: <b>{k.diagnosis_linked_to_policy ? "yes" : "no"}</b>{k.related_sections.length > 0 && <> · related reference sections: {k.related_sections.join(", ")}</>}</p>
      </div>
      <div className="agent-subsection"><strong>Rule relevance to the requested service</strong>
        {k.rule_relevance.map((r) => <div key={r.rule_id} className="relevance-row"><span className="rule-id">{r.rule_id}</span><span className={`relevance ${r.score >= 0.5 ? "on" : "off"}`}>{r.score.toFixed(2)}</span><span>{r.basis}</span></div>)}
      </div>
      <div className="agent-subsection"><strong>Retrieved policy passages</strong>
        {k.retrieved_passages.slice(0, 3).map((p, i) => <blockquote key={i}><b>{p.score.toFixed(2)}</b> {p.text}</blockquote>)}
        {k.reference_passages.slice(0, 2).map((p, i) => <blockquote key={`r${i}`} className="reference"><b>{p.section} · {p.score.toFixed(2)}</b> {p.text}</blockquote>)}
      </div>
    </div>
  );
}

function ReconciliationPanel({ trace }: { trace: PipelineTrace }) {
  const d = trace.reconciliation;
  return (
    <div className="agent-section">
      <p className="agent-lede"><b>{label(d.decision)}</b> · {label(d.reason_code)} — {d.rationale}</p>
      {d.contradictions.length > 0 && <div className="callout warn-callout"><CircleAlert size={14} /><p>{d.contradictions.map((c) => c.description).join(" ")}</p></div>}
      {d.missing_evidence.length > 0 && <div className="evidence-tags missing">{d.missing_evidence.map((m) => <span key={m}>missing: {m}</span>)}</div>}
      <div className="rule-list">
        {d.rule_evaluations.map((e) => (
          <details className="rule-card" key={e.rule_id} open={e.status === "TRUE" || e.only_if_failed}>
            <summary>
              <span className="rule-id">{e.rule_id}</span>
              <span className={`effect-pill ${e.effect.toLowerCase()}`}>{label(e.effect)}</span>
              <span className={`truth ${e.status.toLowerCase()}`}>{e.status}</span>
              {e.only_if_failed && <span className="tag">ONLY-IF failed</span>}
              <span className="rule-subject">{e.source_text.slice(0, 90)}{e.source_text.length > 90 ? "…" : ""}</span>
            </summary>
            {e.predicates.map((p) => (
              <div className="predicate-row" key={p.predicate_id}>
                <span className={`truth ${p.status.toLowerCase()}`}>{p.status}</span>
                <div><strong>{p.text}</strong><small>{p.basis}{p.presumed ? " · presumed" : ""}{p.closed_world ? " · closed-world" : ""}</small></div>
              </div>
            ))}
          </details>
        ))}
      </div>
    </div>
  );
}

function ConfidencePanel({ trace }: { trace: PipelineTrace }) {
  const c = trace.confidence;
  const max = Math.max(...c.factors.map((f) => Math.abs(f.contribution)), 1);
  return (
    <div className="agent-section">
      <p className="agent-lede">Calibrated confidence {c.confidence.toFixed(3)} (raw {c.raw_confidence.toFixed(3)}, calibration: {String(c.calibration.method ?? "identity")}, n={String(c.calibration.n ?? 0)}).</p>
      <div className="factor-list">
        {c.factors.filter((f) => f.contribution !== 0).sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution)).map((f) => (
          <div className="factor-row" key={f.name}>
            <span>{label(f.name)}</span>
            <div className="factor-bar"><div className={f.contribution >= 0 ? "pos" : "neg"} style={{ width: `${(Math.abs(f.contribution) / max) * 100}%` }} /></div>
            <b>{f.contribution > 0 ? "+" : ""}{f.contribution.toFixed(2)}</b>
          </div>
        ))}
      </div>
      {typeof c.calibration.caveat === "string" && <p className="agent-note">{c.calibration.caveat}</p>}
      <AuditBrief text={c.audit_brief} source={c.audit_brief_source} />
    </div>
  );
}

export function AuditBrief({ text, source }: { text: string; source: string }) {
  return (
    <details className="audit-brief" open>
      <summary><ShieldCheck size={13} /> One-page audit brief {source === "llm_assisted" && <i className="tag llm">LLM-drafted summary · validated</i>}</summary>
      <div className="audit-brief-body">
        {text.split("\n").map((line, i) => {
          if (line.startsWith("# ")) return <h3 key={i}>{line.slice(2)}</h3>;
          if (line.startsWith("## ")) return <h4 key={i}>{line.slice(3)}</h4>;
          if (line.startsWith("- ")) return <p key={i} className="brief-bullet">• {line.slice(2)}</p>;
          if (!line.trim()) return null;
          return <p key={i}>{line}</p>;
        })}
      </div>
    </details>
  );
}

export function ArchitectureDiagram({ thresholds }: { thresholds?: Record<string, number> }) {
  const steps = [
    { id: "policy_logic", detail: "Clauses → ASTs (EXCEPT, UNLESS, ONLY IF, AND/OR/NOT)", out: "structured policy rules" },
    { id: "clinical_evidence", detail: "Active-encounter isolation · MCR sliding window · SBioBERT ICD normalisation", out: "clean codes & terms" },
    { id: "knowledge_linker", detail: "Hybrid Graph-RAG: Qdrant + Neo4j · KAMEL valid ancestors", out: "graph paths & evidence" },
    { id: "reconciliation", detail: "Reconciles evidence against AST rules · contradictions & missing evidence", out: "draft outcome & uncertainty" },
    { id: "confidence_explainability", detail: "Calibrated confidence · 1-page audit brief", out: "route" },
  ];
  return (
    <div className="architecture">
      <div className="arch-input">Incoming claim & policy documents</div>
      {steps.map((step) => (
        <div key={step.id} className="arch-step-wrap">
          <div className="arch-step"><span>{AGENT_ICONS[step.id]}</span><div><strong>{AGENT_LABELS[step.id]}</strong><small>{step.detail}</small></div></div>
          <div className="arch-arrow"><RouteIcon size={11} /> {step.out}</div>
        </div>
      ))}
      <div className="arch-split">
        <div className="arch-out stp"><Zap size={14} /><strong>Straight-Through Processing</strong><small>APPROVE ≥ {thresholds?.APPROVE ?? 0.9} · DENY ≥ {thresholds?.DENY ?? 0.95}</small></div>
        <div className="arch-out queue"><UserCheck size={14} /><strong>Human Review Queue</strong><small>Calibrated abstention</small></div>
      </div>
      <p className="agent-note"><Check size={11} /> Safety rule: a denial that overrides a satisfied coverage provision is never auto-finalised.</p>
    </div>
  );
}

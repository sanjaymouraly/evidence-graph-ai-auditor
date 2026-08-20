"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api, detailText, download } from "@/lib/api";
import type {
  Analysis,
  AuditEvent,
  Checklist,
  DecisionDetail,
  GateCheck,
  ReportRecord,
} from "@/lib/types";
import { humanDate, humanDateTime, money, percent, relativeTime, shortId, titleCase } from "@/lib/format";
import { useQueryParam } from "@/lib/useQuery";
import { can, useAuth } from "@/lib/auth";
import { Badge, Card, CopyButton, Empty, ErrorBox, Icon, Loading, Meter, PageHeader, SectionTitle, StatusPill, Tabs } from "@/components/ui";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "checklist", label: "Sufficiency checklist" },
  { id: "evidence", label: "Evidence" },
  { id: "alternatives", label: "Alternatives" },
  { id: "analysis", label: "AI analysis" },
  { id: "review", label: "Review & approval" },
  { id: "reports", label: "Reports" },
  { id: "audit", label: "Audit" },
];

export default function DecisionViewPage() {
  const decisionId = useQueryParam("id");
  const { user } = useAuth();
  const [decision, setDecision] = useState<DecisionDetail | null>(null);
  const [checklist, setChecklist] = useState<Checklist | null>(null);
  const [reports, setReports] = useState<ReportRecord[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [tab, setTab] = useState("overview");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const [exceptionReason, setExceptionReason] = useState("");
  const [ask, setAsk] = useState("");
  const [answer, setAnswer] = useState<{ answer: string; citations: { document_title: string; locator: string; quote: string }[]; insufficient_evidence: boolean } | null>(null);

  const load = useCallback(async () => {
    if (!decisionId) return;
    setLoading(true);
    setError(null);
    try {
      const [detail, checklistPayload, reportPayload, auditPayload] = await Promise.all([
        api<DecisionDetail>(`/api/decisions/${decisionId}`),
        api<Checklist>(`/api/decisions/${decisionId}/checklist`),
        api<{ items: ReportRecord[] }>(`/api/decisions/${decisionId}/reports`),
        api<{ events: AuditEvent[] }>(`/api/decisions/${decisionId}/audit`).catch(() => ({ events: [] })),
      ]);
      setDecision(detail);
      setChecklist(checklistPayload);
      setReports(reportPayload.items);
      setAudit(auditPayload.events ?? []);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to load decision");
    } finally {
      setLoading(false);
    }
  }, [decisionId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(path: string, body: unknown, label: string, reload = true) {
    if (!decisionId) return;
    setBusy(label);
    setActionError(null);
    try {
      await api(`/api/decisions/${decisionId}${path}`, { method: "POST", body });
      setComment("");
      setExceptionReason("");
      if (reload) await load();
    } catch (exc) {
      setActionError(exc instanceof ApiError ? detailText(exc.detail) : "Action failed");
    } finally {
      setBusy(null);
    }
  }

  async function createReport(kind: "pdf" | "json" | "csv" | "manifest") {
    if (!decisionId) return;
    setBusy(`report-${kind}`);
    setActionError(null);
    try {
      await api(`/api/decisions/${decisionId}/reports`, { method: "POST", body: { decision_id: decisionId, kind } });
      await load();
    } catch (exc) {
      setActionError(exc instanceof ApiError ? detailText(exc.detail) : "Report failed");
    } finally {
      setBusy(null);
    }
  }

  async function runAnalysis() {
    if (!decisionId) return;
    setBusy("analysis");
    setActionError(null);
    try {
      await api(`/api/decisions/${decisionId}/analysis`, { method: "POST", body: { use_llm: true } });
      await load();
    } catch (exc) {
      setActionError(exc instanceof ApiError ? detailText(exc.detail) : "Analysis failed");
    } finally {
      setBusy(null);
    }
  }

  async function askQuestion(event: React.FormEvent) {
    event.preventDefault();
    if (!decisionId || ask.trim().length < 3) return;
    setBusy("ask");
    setActionError(null);
    try {
      const response = await api<{ answer: string; citations: { document_title: string; locator: string; quote: string }[]; insufficient_evidence: boolean }>(
        `/api/decisions/${decisionId}/ask`,
        { method: "POST", body: { question: ask.trim(), use_llm: true } },
      );
      setAnswer(response);
    } catch (exc) {
      setActionError(exc instanceof ApiError ? detailText(exc.detail) : "Question failed");
    } finally {
      setBusy(null);
    }
  }

  const mcda = decision?.gate_result?.mcda;
  const latestAnalysis: Analysis | undefined = decision?.analyses?.[0];

  const requirementRows = useMemo(() => checklist?.requirements ?? [], [checklist]);
  const gateChecks = checklist?.checks ?? decision?.gate_checks ?? [];
  const passed = gateChecks.filter((check) => check.status === "pass").length;
  const failed = gateChecks.filter((check) => check.status === "fail").length;

  if (!decisionId) {
    return <Empty title="No decision selected" hint="Open a case from the decisions list." action={<Link href="/decisions" className="btn-primary">Decisions</Link>} />;
  }

  return (
    <div className="fade-in">
      <PageHeader
        title={decision?.title ?? "Decision"}
        subtitle={decision ? `${titleCase(decision.decision_type)} · as of ${humanDate(decision.as_of_date)} · created ${relativeTime(decision.created_at)}` : undefined}
        actions={
          decision ? (
            <>
              <button className="btn-ghost" onClick={() => void load()} disabled={loading}><Icon name="refresh" className="h-3.5 w-3.5" /> Refresh</button>
              <button className="btn-ghost" onClick={() => void act("/evaluate", {}, "evaluate")} disabled={busy !== null || !can(user, "decision:submit")}>
                {busy === "evaluate" ? "Evaluating…" : "Re-evaluate gate"}
              </button>
              <Link href="/decisions" className="btn-ghost">All decisions</Link>
            </>
          ) : null
        }
      />

      {error ? <div className="mb-4"><ErrorBox message={error} onRetry={load} /></div> : null}
      {!decision && loading ? <Loading rows={8} /> : null}

      {decision ? (
        <>
          <div className="mb-4 grid gap-3 lg:grid-cols-[1fr_auto]">
            <Card className="flex flex-wrap items-center gap-x-6 gap-y-3">
              <div className="flex items-center gap-2">
                <span className="label">Gate</span>
                <StatusPill status={decision.gate_status} />
              </div>
              <div className="flex items-center gap-2">
                <span className="label">Workflow</span>
                <StatusPill status={decision.status} />
              </div>
              <div className="label normal-case tracking-normal text-slate-400">
                {passed} passed · {failed} failed · {gateChecks.length} checks
              </div>
              {checklist?.eligible_for_approval ? <Badge tone="ok">eligible for approval</Badge> : <Badge tone="warn">approval not permitted</Badge>}
              {decision.exception_recorded ? <Badge tone="warn">policy exception recorded</Badge> : null}
              {decision.gate_evaluated_at ? <span className="text-[11px] text-slate-500">evaluated {relativeTime(decision.gate_evaluated_at)}</span> : null}
            </Card>
            <Card className="lg:w-[280px]">
              <div className="flex items-center justify-between text-[11.5px] text-slate-400">
                <span>Gate result hash</span>
                <CopyButton value={String(decision.gate_result?.result_hash ?? "")} />
              </div>
              <code className="mono mt-1 block break-all text-[10.5px] text-slate-500">{decision.gate_result?.result_hash ?? "not evaluated"}</code>
            </Card>
          </div>

          {actionError ? <div className="mb-4"><ErrorBox message={actionError} /></div> : null}

          <div className="mb-4">
            <Tabs tabs={TABS} active={tab} onChange={setTab} />
          </div>

          {tab === "overview" ? (
            <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
              <div className="space-y-4">
                <Card>
                  <SectionTitle title="Case" />
                  <p className="text-[13px] leading-relaxed text-slate-300">{decision.description || "No description provided."}</p>
                  <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    <div>
                      <div className="label mb-1">Options</div>
                      <ul className="space-y-1">
                        {decision.options.map((option) => (
                          <li key={option.name} className="flex items-center justify-between rounded-md border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5 text-[12px]">
                            <span className="text-slate-200">{option.name}</span>
                            {decision.selected_option === option.name ? <Badge tone="ok">selected</Badge> : null}
                          </li>
                        ))}
                        {!decision.options.length ? <li className="text-[12px] text-slate-500">No options recorded.</li> : null}
                      </ul>
                    </div>
                    <div>
                      <div className="label mb-1">Blocking failures</div>
                      {decision.blocking_failures.length ? (
                        <ul className="space-y-1">
                          {decision.blocking_failures.map((failure) => {
                            const label = typeof failure === "string" ? failure : failure.label ?? failure.code;
                            const code = typeof failure === "string" ? failure : failure.code;
                            return (
                              <li key={code} className="rounded-md border border-bad/25 bg-bad/[0.07] px-2.5 py-1.5 text-[12px] text-red-200">
                                {label}
                              </li>
                            );
                          })}
                        </ul>
                      ) : (
                        <div className="rounded-md border border-ok/25 bg-ok/[0.06] px-2.5 py-1.5 text-[12px] text-emerald-200">No blocking failures.</div>
                      )}
                      {decision.warnings.length ? (
                        <ul className="mt-2 space-y-1">
                          {decision.warnings.map((warning) => (
                            <li key={typeof warning === "string" ? warning : warning.code} className="rounded-md border border-warn/25 bg-warn/[0.07] px-2.5 py-1.5 text-[12px] text-amber-200">
                              {typeof warning === "string" ? warning : warning.label ?? warning.code}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  </div>
                </Card>

                <Card>
                  <SectionTitle title="Gate checks" hint="Deterministic checks re-computed from stored evidence." actions={<button className="link text-[12px]" onClick={() => setTab("checklist")}>Full checklist</button>} />
                  <ul className="space-y-1.5">
                    {gateChecks.slice(0, 6).map((check) => (
                      <li key={check.code} className="flex items-start gap-3 rounded-lg border border-white/[0.05] bg-white/[0.02] px-3 py-2">
                        <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${check.status === "pass" ? "bg-ok" : check.status === "warn" ? "bg-warn" : "bg-bad"}`} />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-[12.5px] text-slate-200">{check.label}</span>
                            {check.blocking ? <Badge tone="bad">blocking</Badge> : null}
                          </div>
                          <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-500">{check.detail}</p>
                        </div>
                      </li>
                    ))}
                  </ul>
                </Card>
              </div>

              <div className="space-y-4">
                <Card>
                  <SectionTitle title="Attached evidence" actions={<span className="text-[11.5px] text-slate-500">{decision.documents.length} documents</span>} />
                  <ul className="space-y-1.5">
                    {decision.documents.map((doc) => (
                      <li key={doc.id} className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 hover:bg-white/[0.03]">
                        <Link href={`/documents/view?id=${doc.id}`} className="min-w-0">
                          <span className="block truncate text-[12.5px] text-slate-200">{doc.title}</span>
                          <span className="block text-[10.5px] text-slate-500">{titleCase(doc.doc_type)} · v{doc.current_version?.version_number ?? 1} · {doc.claim_count ?? "—"} claims</span>
                        </Link>
                        <Icon name="chevron" className="h-3.5 w-3.5 shrink-0 text-slate-600" />
                      </li>
                    ))}
                    {!decision.documents.length ? <li className="text-[12px] text-slate-500">No documents attached.</li> : null}
                  </ul>
                </Card>

                {mcda?.applicable ? (
                  <Card>
                    <SectionTitle title="Weighted alternatives" hint="Derived from stored evidence; min–max normalised." actions={<button className="link text-[12px]" onClick={() => setTab("alternatives")}>Details</button>} />
                    <ul className="space-y-2">
                      {(mcda.ranking ?? []).map((entry, index) => (
                        <li key={entry.option}>
                          <div className="flex items-center justify-between text-[12px]">
                            <span className="text-slate-200">{index + 1}. {entry.option}</span>
                            <span className="tabular-nums text-slate-300">{(entry.score ?? 0).toFixed(3)}</span>
                          </div>
                          <Meter value={entry.score ?? 0} tone={index === 0 ? "ok" : "info"} />
                        </li>
                      ))}
                    </ul>
                  </Card>
                ) : null}
              </div>
            </div>
          ) : null}

          {tab === "checklist" ? (
            <div className="space-y-4">
              <Card>
                <SectionTitle
                  title="Evidence sufficiency gate"
                  hint={`${checklist?.gate_version ?? "gate"} · ${checklist?.policy_version ?? "policy"} · evaluated ${humanDateTime(checklist?.evaluated_at)}`}
                  actions={<Badge tone={checklist?.eligible_for_approval ? "ok" : "bad"}>{checklist?.eligible_for_approval ? "eligible for approval" : "not eligible"}</Badge>}
                />
                <ul className="space-y-2">
                  {gateChecks.map((check: GateCheck) => (
                    <li key={check.code} className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className={`h-2 w-2 rounded-full ${check.status === "pass" ? "bg-ok" : check.status === "warn" ? "bg-warn" : "bg-bad"}`} />
                          <span className="text-[12.5px] font-medium text-slate-100">{check.label}</span>
                          <span className="mono text-[10.5px] text-slate-600">{check.code}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          {check.blocking && check.status !== "pass" ? <Badge tone="bad">blocking</Badge> : null}
                          <Badge tone={check.status === "pass" ? "ok" : check.status === "warn" ? "warn" : "bad"}>{check.status}</Badge>
                        </div>
                      </div>
                      <p className="mt-1.5 text-[11.5px] leading-relaxed text-slate-400">{check.detail}</p>
                    </li>
                  ))}
                </ul>
              </Card>

              {requirementRows.length ? (
                <Card>
                  <SectionTitle title="Evidence requirements" hint="Per-option claim coverage and required source types." />
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[560px]">
                      <thead>
                        <tr><th className="th">Requirement</th><th className="th">Status</th><th className="th">Detail</th></tr>
                      </thead>
                      <tbody>
                        {requirementRows.map((requirement, index) => (
                          <tr key={`${requirement.key ?? requirement.label}-${index}`} className="border-t border-white/[0.05]">
                            <td className="td">{requirement.label}</td>
                            <td className="td"><Badge tone={requirement.pass ? "ok" : "bad"}>{requirement.pass ? "covered" : "gap"}</Badge></td>
                            <td className="td text-[11.5px] text-slate-400">
                              {requirement.missing_doc_types?.length ? `Missing sources: ${requirement.missing_doc_types.join(", ")}` : null}
                              {requirement.options?.length ? (
                                <ul className="mt-1 space-y-0.5">
                                  {requirement.options.map((option) => (
                                    <li key={option.option} className={option.pass ? "text-slate-400" : "text-red-300"}>
                                      {option.option}: {option.pass ? "covered" : option.detail ?? "missing evidence"}
                                    </li>
                                  ))}
                                </ul>
                              ) : null}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
              ) : null}

              {decision.exception_recorded ? (
                <Card>
                  <SectionTitle title="Recorded policy exception" hint="Exceptions do not change gate results; they are recorded alongside them." />
                  <p className="rounded-lg border border-warn/25 bg-warn/[0.06] p-3 text-[12px] leading-relaxed text-amber-100">{decision.exception_reason}</p>
                </Card>
              ) : null}
            </div>
          ) : null}

          {tab === "evidence" ? (
            <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
              <Card>
                <SectionTitle title="Documents in scope" hint="Current versions only; superseded versions remain in the audit record." />
                <ul className="divide-y divide-white/[0.05]">
                  {decision.documents.map((doc) => (
                    <li key={doc.id} className="py-2.5">
                      <div className="flex items-start justify-between gap-2">
                        <Link href={`/documents/view?id=${doc.id}`} className="min-w-0 text-[12.5px] text-slate-200 hover:text-accent">{doc.title}</Link>
                        <Badge tone="neutral">{titleCase(doc.doc_type)}</Badge>
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-[10.5px] text-slate-500">
                        <span>v{doc.current_version?.version_number ?? 1}</span>
                        <span>· {doc.claim_count ?? "—"} claims</span>
                        {doc.current_version?.valid_until ? <span>· valid to {humanDate(doc.current_version.valid_until)}</span> : null}
                        {doc.current_version?.injection_flags?.length ? <Badge tone="bad">injection screened</Badge> : null}
                      </div>
                    </li>
                  ))}
                </ul>
              </Card>

              <Card>
                <SectionTitle title="Ask about this evidence" hint="Answers are constrained to stored claims; insufficient evidence is stated explicitly." />
                <form onSubmit={askQuestion} className="flex gap-2">
                  <input className="input" value={ask} onChange={(event) => setAsk(event.target.value)} placeholder="What are the payment terms offered by each supplier?" />
                  <button className="btn-primary shrink-0" disabled={busy === "ask" || ask.trim().length < 3}>{busy === "ask" ? "Searching…" : "Ask"}</button>
                </form>
                {answer ? (
                  <div className="mt-3 rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
                    <div className="flex items-center justify-between">
                      <span className="label">Answer</span>
                      <Badge tone={answer.insufficient_evidence ? "warn" : "ok"}>{answer.insufficient_evidence ? "insufficient evidence" : "grounded"}</Badge>
                    </div>
                    <p className="mt-2 whitespace-pre-wrap text-[12.5px] leading-relaxed text-slate-200">{answer.answer}</p>
                    {answer.citations?.length ? (
                      <ol className="mt-3 space-y-1.5">
                        {answer.citations.map((citation, index) => (
                          <li key={`${citation.document_title}-${index}`} className="rounded-md border border-white/[0.06] p-2 text-[11px]">
                            <span className="text-slate-400">[{index + 1}] {citation.document_title} · {citation.locator}</span>
                            <div className="mt-0.5 italic text-slate-500">“{citation.quote}”</div>
                          </li>
                        ))}
                      </ol>
                    ) : null}
                  </div>
                ) : null}
              </Card>
            </div>
          ) : null}

          {tab === "alternatives" ? (
            <div className="space-y-4">
              <Card>
                <SectionTitle title="Criteria and weights" hint="Weights are part of the case definition and versioned with the gate policy." />
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
                  {(mcda?.criteria ?? decision.criteria).map((criterion) => (
                    <div key={criterion.code} className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5">
                      <div className="text-[11.5px] text-slate-300">{criterion.label}</div>
                      <div className="mt-1 flex items-center justify-between text-[10.5px] text-slate-500">
                        <span>weight {criterion.weight}</span>
                        <span>{criterion.direction}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </Card>

              {mcda?.metrics?.length ? (
                <Card pad={false}>
                  <div className="px-5 pt-4"><SectionTitle title="Option comparison" hint="Raw evidence values and normalised scores (0–1)." /></div>
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[720px]">
                      <thead>
                        <tr>
                          <th className="th">Option</th>
                          <th className="th">Price</th>
                          <th className="th">Delivery (d)</th>
                          <th className="th">Warranty (d)</th>
                          <th className="th">Compliance</th>
                          <th className="th">Risk</th>
                          <th className="th">Weighted</th>
                        </tr>
                      </thead>
                      <tbody>
                        {mcda.metrics.map((metric) => {
                          const ranking = mcda.ranking?.find((entry) => entry.option === metric.option);
                          return (
                            <tr key={metric.option} className="border-t border-white/[0.05]">
                              <td className="td font-medium text-slate-100">{metric.option}</td>
                              <td className="td">{metric.price !== undefined ? money(metric.price) : "—"}</td>
                              <td className="td">{metric.delivery ?? "—"}</td>
                              <td className="td">{metric.warranty ?? "—"}</td>
                              <td className="td">{metric.compliance ?? "—"}</td>
                              <td className="td">{metric.risk ?? "—"}</td>
                              <td className="td tabular-nums text-slate-100">{ranking?.score?.toFixed(3) ?? "—"}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  {mcda.sensitivity?.length ? (
                    <div className="border-t border-white/[0.06] px-5 py-3">
                      <div className="label mb-1">Sensitivity (−0.10 weight perturbation)</div>
                      <ul className="grid gap-1 sm:grid-cols-2">
                        {mcda.sensitivity.map((row, index) => (
                          <li key={`${row.criterion ?? row.option ?? "criterion"}-${index}`} className="flex flex-wrap justify-between gap-2 text-[11.5px] text-slate-400">
                            <span>{row.criterion ?? row.option ?? "criterion"}</span>
                            <span className="flex items-center gap-2">
                              {row.ranking?.length ? <span className="text-slate-500">rank: {row.ranking.join(" › ")}</span> : null}
                              <Badge tone={row.top_option_changed ? "warn" : "ok"}>{row.top_option_changed ? "top option changes" : "stable"}</Badge>
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </Card>
              ) : (
                <Empty title="No comparable options" hint="This case has a single option or missing numeric evidence, so no ranking is produced." />
              )}
            </div>
          ) : null}

          {tab === "analysis" ? (
            <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
              <Card>
                <SectionTitle
                  title="Evidence-constrained analysis"
                  hint="Every statement must cite stored claims. Ungrounded statements are flagged and excluded."
                  actions={<button className="btn-primary px-2.5 py-1.5 text-[12px]" onClick={() => void runAnalysis()} disabled={busy === "analysis"}>{busy === "analysis" ? "Running…" : "Run analysis"}</button>}
                />
                {!latestAnalysis ? (
                  <Empty title="No analysis yet" hint="Run the offline deterministic analysis or configure a generation provider for narrative output." />
                ) : (
                  <div>
                    <div className="flex flex-wrap items-center gap-2 text-[11.5px]">
                      <Badge tone="neutral">{latestAnalysis.provider_label || "offline"}</Badge>
                      <Badge tone="neutral">{latestAnalysis.status}</Badge>
                      <span className="text-slate-500">{humanDateTime(latestAnalysis.created_at)}</span>
                      <span className="text-slate-500">{(latestAnalysis.statements ?? []).length} statements · {(latestAnalysis.statements ?? []).filter((statement) => !statement.supported).length} unsupported</span>
                    </div>
                    <p className="mt-3 text-[13px] leading-relaxed text-slate-300">{latestAnalysis.summary}</p>
                    {latestAnalysis.recommendation ? (
                      <div className="mt-3 rounded-lg border border-accent/25 bg-accent/[0.06] p-3 text-[12.5px] leading-relaxed text-amber-100">
                        <span className="text-[10.5px] uppercase tracking-wide text-amber-200/70">Advisory, not a decision</span>
                        <p className="mt-1">{latestAnalysis.recommendation}</p>
                      </div>
                    ) : null}
                    <ul className="mt-4 space-y-2">
                      {(latestAnalysis.statements ?? []).map((statement) => (
                        <li key={statement.id} className={`rounded-lg border p-3 ${statement.supported ? "border-white/[0.07] bg-white/[0.02]" : "border-bad/30 bg-bad/[0.07]"}`}>
                          <div className="flex items-start justify-between gap-3">
                            <p className="text-[12.5px] leading-relaxed text-slate-200">{statement.text}</p>
                            <Badge tone={statement.supported ? "ok" : "bad"}>{statement.supported ? "grounded" : "excluded"}</Badge>
                          </div>
                          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[10.5px] text-slate-500">
                            <span>{statement.kind}</span>
                            {statement.claim_ids?.length ? <span>· {statement.claim_ids.length} citation(s)</span> : <span>· structural</span>}
                            {statement.validation_notes?.length ? <span className="text-red-300">· {statement.validation_notes.join("; ")}</span> : null}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </Card>

              <div className="space-y-4">
                <Card>
                  <SectionTitle title="Constraints" />
                  <ul className="space-y-2 text-[11.5px] leading-relaxed text-slate-400">
                    <li className="flex gap-2"><Icon name="lock" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" /> The model cannot change gate results or approval state.</li>
                    <li className="flex gap-2"><Icon name="check" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ok" /> Citations are validated against stored claims; unknown ids are rejected.</li>
                    <li className="flex gap-2"><Icon name="alert" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-bad" /> Numbers in statements must appear in the cited evidence.</li>
                    <li className="flex gap-2"><Icon name="shield" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-info" /> Document text is treated as untrusted data, never as instructions.</li>
                  </ul>
                </Card>
                <Card>
                  <SectionTitle title="History" />
                  <ul className="space-y-1.5">
                    {decision.analyses.map((analysis) => (
                      <li key={analysis.id} className="flex items-center justify-between text-[11.5px]">
                        <span className="text-slate-400">{humanDateTime(analysis.created_at)}</span>
                        <span className="text-slate-500">{(analysis.statements ?? []).length} stmts</span>
                      </li>
                    ))}
                  </ul>
                </Card>
              </div>
            </div>
          ) : null}

          {tab === "review" ? (
            <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
              <Card>
                <SectionTitle title="Review actions" hint="Reviewer attestation is required before final approval. A creator cannot approve their own case." />
                <textarea className="input min-h-[76px]" placeholder="Review comment (recorded in the audit trail)" value={comment} onChange={(event) => setComment(event.target.value)} />
                <div className="mt-3 flex flex-wrap gap-2">
                  <button className="btn-ghost" disabled={busy !== null || !can(user, "decision:approve")} onClick={() => void act("/attest", { comment }, "attest")}>
                    {busy === "attest" ? "Recording…" : "Attest evidence"}
                  </button>
                  <button className="btn-primary" disabled={busy !== null || !can(user, "decision:approve") || !checklist?.eligible_for_approval} onClick={() => void act("/approve", { comment }, "approve")}>
                    {busy === "approve" ? "Approving…" : "Approve decision"}
                  </button>
                  <button className="btn-ghost" disabled={busy !== null || !can(user, "decision:approve")} onClick={() => void act("/request-changes", { comment }, "changes")}>Request changes</button>
                  <button className="btn-danger" disabled={busy !== null || !can(user, "decision:approve")} onClick={() => void act("/reject", { comment }, "reject")}>Reject</button>
                </div>
                {!checklist?.eligible_for_approval ? (
                  <p className="mt-2 text-[11.5px] leading-relaxed text-amber-200/80">
                    Approval is disabled because the gate is {checklist?.gate_status ?? "not evaluated"}. Resolve the blocking checks first; recording an exception does not change this result.
                  </p>
                ) : null}

                <div className="mt-5 border-t border-white/[0.07] pt-3">
                  <div className="label mb-1.5">Admin: record policy exception</div>
                  <textarea className="input min-h-[60px]" placeholder="Reason (min 20 characters) — stored verbatim and never alters gate results" value={exceptionReason} onChange={(event) => setExceptionReason(event.target.value)} />
                  <button
                    className="btn-ghost mt-2"
                    disabled={busy !== null || !can(user, "decision:approve") || exceptionReason.trim().length < 20 || user?.role !== "admin"}
                    onClick={() => void act("/exception", { reason: exceptionReason }, "exception")}
                  >
                    {user?.role === "admin" ? "Record exception" : "Admin only"}
                  </button>
                </div>
              </Card>

              <Card>
                <SectionTitle title="Approval history" hint="Each entry stores the gate snapshot hash at the moment of the action." />
                <ol className="space-y-2.5">
                  {decision.approvals.map((approval) => (
                    <li key={approval.id} className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[12.5px] font-medium text-slate-100">{titleCase(approval.action)}</span>
                        <span className="text-[10.5px] text-slate-500">{humanDateTime(approval.created_at)}</span>
                      </div>
                      <div className="mt-0.5 text-[11px] text-slate-500">{approval.user?.name ?? "system"} · {approval.user?.role ?? ""}</div>
                      {approval.comment ? <p className="mt-1.5 text-[11.5px] leading-relaxed text-slate-300">{approval.comment}</p> : null}
                      {approval.gate_snapshot_hash ? <div className="mono mt-1 break-all text-[10px] text-slate-600">gate {approval.gate_snapshot_hash.slice(0, 32)}…</div> : null}
                    </li>
                  ))}
                  {!decision.approvals.length ? <li className="text-[12px] text-slate-500">No review actions yet.</li> : null}
                </ol>
              </Card>
            </div>
          ) : null}

          {tab === "reports" ? (
            <Card>
              <SectionTitle
                title="Reports and snapshots"
                hint="A report binds a frozen snapshot: document hashes, claim set, gate result and audit chain head."
                actions={
                  <>
                    <button className="btn-ghost px-2.5 py-1.5 text-[12px]" onClick={() => void createReport("pdf")} disabled={busy !== null}>PDF</button>
                    <button className="btn-ghost px-2.5 py-1.5 text-[12px]" onClick={() => void createReport("json")} disabled={busy !== null}>JSON</button>
                    <button className="btn-ghost px-2.5 py-1.5 text-[12px]" onClick={() => void createReport("csv")} disabled={busy !== null}>CSV</button>
                    <button className="btn-ghost px-2.5 py-1.5 text-[12px]" onClick={() => void createReport("manifest")} disabled={busy !== null}>Manifest</button>
                  </>
                }
              />
              {reports.length === 0 ? (
                <Empty title="No reports generated" hint="Generate a PDF or JSON report to freeze the evidence state." />
              ) : (
                <ul className="divide-y divide-white/[0.05]">
                  {reports.map((report) => (
                    <li key={report.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5">
                      <div>
                        <div className="text-[12.5px] text-slate-200">{report.kind.toUpperCase()} · {report.filename}</div>
                        <div className="mono text-[10.5px] text-slate-500">sha256 {report.sha256.slice(0, 24)}…</div>
                      </div>
                      <div className="flex items-center gap-2">
                        {report.snapshot_verification ? <Badge tone={report.snapshot_verification.hash_match ? "ok" : "bad"}>{report.snapshot_verification.hash_match ? "snapshot verified" : "hash mismatch"}</Badge> : null}
                        <span className="text-[10.5px] text-slate-500">{humanDateTime(report.created_at)}</span>
                        <button className="btn-ghost px-2 py-1 text-[11.5px]" onClick={() => void download(`/api/reports/${report.id}/download`, report.filename)}>
                          <Icon name="download" className="h-3 w-3" /> Download
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          ) : null}

          {tab === "audit" ? (
            <Card pad={false}>
              <div className="px-5 pt-4"><SectionTitle title="Decision audit trail" hint="Hash-chained events scoped to this case and its evidence." /></div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px]">
                  <thead>
                    <tr><th className="th">When</th><th className="th">Action</th><th className="th">Actor</th><th className="th">Object</th><th className="th">Event hash</th></tr>
                  </thead>
                  <tbody>
                    {audit.map((event) => (
                      <tr key={event.id} className="border-t border-white/[0.05]">
                        <td className="td whitespace-nowrap text-[11.5px]">{humanDateTime(event.created_at)}</td>
                        <td className="td text-[12px] text-slate-200">{titleCase(event.action.replace(/[._]/g, " "))}</td>
                        <td className="td text-[11.5px] text-slate-400">{event.actor ?? "system"}</td>
                        <td className="td mono text-[10.5px] text-slate-500">{event.object_type}:{shortId(event.object_id)}</td>
                        <td className="td mono text-[10.5px] text-slate-500">{shortId(event.event_hash, 16)}…</td>
                      </tr>
                    ))}
                    {!audit.length ? <tr><td className="td text-slate-500" colSpan={5}>No audit events for this case.</td></tr> : null}
                  </tbody>
                </table>
              </div>
            </Card>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

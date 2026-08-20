"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Contradiction, DashboardPayload } from "@/lib/types";
import { humanDateTime, percent, relativeTime, titleCase } from "@/lib/format";
import { Badge, Card, Donut, Empty, ErrorBox, Icon, Loading, PageHeader, SectionTitle, Stat, StatusPill } from "@/components/ui";

const PIPELINE = ["DRAFT", "EVIDENCE_INCOMPLETE", "CONTRADICTED", "REVIEW_REQUIRED", "SUFFICIENTLY_SUPPORTED", "APPROVED", "REJECTED"];

export default function DashboardPage() {
  const [data, setData] = useState<DashboardPayload | null>(null);
  const [contradictions, setContradictions] = useState<Contradiction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [dashboard, contradictionsPage] = await Promise.all([
        api<DashboardPayload>("/api/dashboard"),
        api<{ items: Contradiction[] }>("/api/contradictions?page_size=4"),
      ]);
      setData(dashboard);
      setContradictions(contradictionsPage.items);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const totalClaims = data?.claims.total ?? 0;
  const verified = data?.claims.by_status?.verified ?? 0;
  const health = data?.evidence_health_score ?? 0;

  return (
    <div className="fade-in">
      <PageHeader
        title="Dashboard"
        subtitle="Live state of the evidence base, contradiction backlog and decision pipeline. All figures are read from the workspace database."
        actions={
          <>
            <Link href="/ingestion" className="btn-ghost">
              <Icon name="upload" className="h-3.5 w-3.5" /> Import documents
            </Link>
            <Link href="/decisions/new" className="btn-primary">
              <Icon name="plus" className="h-3.5 w-3.5" /> New decision
            </Link>
          </>
        }
      />

      {error ? <div className="mb-4"><ErrorBox message={error} onRetry={load} /></div> : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Documents"
          value={loading ? "—" : data?.documents.total ?? 0}
          hint={loading ? " " : `${data?.documents.versions ?? 0} versions · ${data?.documents.expired ?? 0} expired`}
          tone="info"
          href="/documents"
        />
        <Stat
          label="Claims extracted"
          value={loading ? "—" : totalClaims}
          hint={loading ? " " : `${verified} verified · ${data?.claims.duplicates ?? 0} duplicates`}
          tone="ok"
          href="/claims"
        />
        <Stat
          label="Blocking contradictions"
          value={loading ? "—" : data?.contradictions.blocking ?? 0}
          hint={loading ? " " : `${data?.contradictions.confirmed ?? 0} confirmed of ${data?.contradictions.total ?? 0}`}
          tone={data && (data.contradictions.blocking ?? 0) > 0 ? "bad" : "ok"}
          href="/contradictions"
        />
        <Stat
          label="Awaiting review"
          value={loading ? "—" : data?.decisions.awaiting_review ?? 0}
          hint={loading ? " " : `${data?.decisions.approved ?? 0} approved · ${data?.decisions.total ?? 0} total`}
          tone="warn"
          href="/reviews"
        />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[1.05fr_1.35fr_1fr]">
        <Card>
          <SectionTitle title="Evidence health" hint="Composite of source freshness, verification status and contradiction load." />
          {loading ? (
            <Loading rows={4} />
          ) : (
            <div className="flex items-center gap-6">
              <Donut
                segments={[
                  { label: "verified", value: verified, color: "#43c59e" },
                  { label: "disputed", value: data?.claims.by_status?.disputed ?? 0, color: "#e2a33c" },
                  { label: "unsupported", value: data?.claims.by_status?.unsupported ?? 0, color: "#ef6461" },
                  { label: "other", value: Math.max(0, totalClaims - verified - (data?.claims.by_status?.disputed ?? 0) - (data?.claims.by_status?.unsupported ?? 0)), color: "#3c4c68" },
                ]}
                center={
                  <>
                    <span className="text-2xl font-semibold text-slate-50">{health.toFixed(1)}</span>
                    <span className="text-[10px] uppercase tracking-wide text-slate-500">of 100</span>
                  </>
                }
              />
              <div className="flex-1 space-y-2.5 text-[12.5px]">
                {[
                  ["Verified claims", verified, "text-emerald-300"],
                  ["Disputed", data?.claims.by_status?.disputed ?? 0, "text-amber-300"],
                  ["Unsupported / expired", data?.claims.by_status?.unsupported ?? 0, "text-red-300"],
                  ["Expired documents", data?.documents.expired ?? 0, "text-slate-300"],
                ].map(([label, value, klass]) => (
                  <div key={String(label)} className="flex items-center justify-between border-b border-white/[0.05] pb-1.5">
                    <span className="text-slate-400">{label}</span>
                    <span className={String(klass)}>{String(value)}</span>
                  </div>
                ))}
                <div className="flex items-center justify-between pt-0.5">
                  <span className="text-slate-500">Verification rate</span>
                  <span className="text-slate-200">{percent(totalClaims ? verified / totalClaims : 0, 0)}</span>
                </div>
              </div>
            </div>
          )}
        </Card>

        <Card>
          <SectionTitle title="Decision pipeline" hint="Current workflow state for every case in the workspace." actions={<Link href="/decisions" className="link text-[12px]">View all</Link>} />
          {loading ? (
            <Loading rows={5} />
          ) : (
            <div className="space-y-2.5">
              {PIPELINE.map((stage) => {
                const value = data?.decisions.by_status?.[stage] ?? 0;
                const total = data?.decisions.total || 1;
                const tone =
                  stage === "APPROVED" || stage === "SUFFICIENTLY_SUPPORTED" ? "bg-ok" :
                  stage === "CONTRADICTED" || stage === "REJECTED" || stage === "EVIDENCE_INCOMPLETE" ? "bg-bad" :
                  stage === "REVIEW_REQUIRED" ? "bg-warn" : "bg-info";
                return (
                  <div key={stage} className="grid grid-cols-[160px_1fr_24px] items-center gap-3">
                    <span className="truncate text-[11.5px] text-slate-400">{titleCase(stage)}</span>
                    <div className="h-2 overflow-hidden rounded-full bg-white/[0.05]">
                      <div className={`h-full rounded-full ${tone}`} style={{ width: `${Math.max(value ? 6 : 0, (value / total) * 100)}%` }} />
                    </div>
                    <span className="text-right text-[12px] tabular-nums text-slate-300">{value}</span>
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        <Card>
          <SectionTitle title="Contradictions" hint="Newest first; blocking items stop approval." actions={<Link href="/contradictions" className="link text-[12px]">All</Link>} />
          {loading ? (
            <Loading rows={4} />
          ) : contradictions.length === 0 ? (
            <Empty title="No contradictions recorded" hint="Cross-source checks found no conflicts in the current corpus." />
          ) : (
            <ul className="space-y-2.5">
              {contradictions.slice(0, 4).map((item) => (
                <li key={item.id} className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <Badge tone={item.blocking ? "bad" : "warn"}>{titleCase(item.kind)}</Badge>
                    <span className="text-[10.5px] text-slate-500">{relativeTime(item.created_at)}</span>
                  </div>
                  <p className="mt-1.5 line-clamp-2 text-[11.5px] leading-relaxed text-slate-400">
                    {item.claim_a.document_title ?? "Unknown source"} vs {item.claim_b.document_title ?? "Unknown source"}
                  </p>
                  <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-500">
                    <span className={item.claim_a.verification_status === "disputed" ? "text-amber-300" : ""}>{item.claim_a.object ?? "—"}</span>
                    <span>↔</span>
                    <span className={item.claim_b.verification_status === "disputed" ? "text-amber-300" : ""}>{item.claim_b.object ?? "—"}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[1.55fr_1fr]">
        <Card pad={false}>
          <div className="flex items-center justify-between px-5 pt-5">
            <SectionTitle title="Recent decisions" hint="Gate outcome is computed; workflow status reflects human actions." />
          </div>
          <div className="overflow-x-auto">
            {loading ? (
              <div className="px-5 pb-5"><Loading rows={4} /></div>
            ) : (
              <table className="w-full min-w-[640px] border-t border-white/[0.06]">
                <thead>
                  <tr>
                    <th className="th">Decision</th>
                    <th className="th">Gate</th>
                    <th className="th">Status</th>
                    <th className="th">Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.recent_decisions ?? []).slice(0, 7).map((decision) => (
                    <tr key={decision.id} className="row-hover border-t border-white/[0.05]">
                      <td className="td">
                        <Link href={`/decisions/view?id=${decision.id}`} className="font-medium text-slate-100 hover:text-accent">
                          {decision.title}
                        </Link>
                        <div className="mt-0.5 text-[11px] text-slate-500">
                          {titleCase(decision.decision_type)} · {decision.documents_count} documents · as of {decision.as_of_date ?? "—"}
                        </div>
                      </td>
                      <td className="td"><StatusPill status={decision.gate_status} /></td>
                      <td className="td"><StatusPill status={decision.status} /></td>
                      <td className="td whitespace-nowrap text-[12px] text-slate-400">{relativeTime(decision.updated_at)}</td>
                    </tr>
                  ))}
                  {!data?.recent_decisions?.length ? (
                    <tr><td className="td text-slate-500" colSpan={4}>No decisions yet.</td></tr>
                  ) : null}
                </tbody>
              </table>
            )}
          </div>
        </Card>

        <Card>
          <SectionTitle title="Audit activity" hint="Hash-chained events, newest first." actions={<Link href="/audit" className="link text-[12px]">Trail</Link>} />
          {loading ? (
            <Loading rows={5} />
          ) : (
            <ol className="relative space-y-3 pl-4">
              <span className="absolute left-[5px] top-1 h-[calc(100%-8px)] w-px bg-white/[0.08]" aria-hidden />
              {(data?.audit_tail ?? []).slice(0, 6).map((event) => (
                <li key={event.id} className="relative">
                  <span className="absolute -left-4 top-1.5 h-2 w-2 rounded-full border border-ink-900 bg-accent/80" aria-hidden />
                  <div className="text-[12px] text-slate-200">{titleCase(event.action.replace(/[._]/g, " "))}</div>
                  <div className="text-[11px] text-slate-500">
                    {event.actor ?? "system"} · {humanDateTime(event.created_at)}
                  </div>
                </li>
              ))}
              {!data?.audit_tail?.length ? <li className="text-[12px] text-slate-500">No events recorded.</li> : null}
            </ol>
          )}
        </Card>
      </div>
    </div>
  );
}

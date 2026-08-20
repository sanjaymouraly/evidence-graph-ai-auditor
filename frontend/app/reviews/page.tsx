"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Claim, Contradiction, DecisionSummary, Paged } from "@/lib/types";
import { humanDate, relativeTime, titleCase } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, Loading, PageHeader, Stat, StatusPill, Tabs } from "@/components/ui";

export default function ReviewsPage() {
  const [decisions, setDecisions] = useState<DecisionSummary[]>([]);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [contradictions, setContradictions] = useState<Contradiction[]>([]);
  const [tab, setTab] = useState("decisions");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const [decisionPage, claimPage, contradictionPage] = await Promise.all([
          api<Paged<DecisionSummary>>("/api/decisions?page_size=50"),
          api<Paged<Claim>>("/api/claims?verification_status=disputed&page_size=50"),
          api<Paged<Contradiction>>("/api/contradictions?state=possible&page_size=50"),
        ]);
        if (cancelled) return;
        setDecisions(decisionPage.items);
        setClaims(claimPage.items);
        setContradictions(contradictionPage.items);
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : "Failed to load review queue");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const awaiting = useMemo(
    () => decisions.filter((decision) => ["DRAFT", "EVIDENCE_INCOMPLETE", "REVIEW_REQUIRED", "SUFFICIENTLY_SUPPORTED"].includes(decision.status)),
    [decisions],
  );
  const blocked = useMemo(() => decisions.filter((decision) => decision.status === "CONTRADICTED"), [decisions]);

  return (
    <div className="fade-in">
      <PageHeader
        title="Review queue"
        subtitle="Everything awaiting a human decision: cases that can be approved, blocked cases needing remediation, disputed claims and unresolved contradictions."
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Cases in review" value={awaiting.length} tone="warn" hint="Draft through sufficiently supported" />
        <Stat label="Blocked cases" value={blocked.length} tone="bad" hint="Contradictions or missing evidence" />
        <Stat label="Disputed claims" value={claims.length} tone="warn" hint="Flagged during extraction or review" />
        <Stat label="Open contradictions" value={contradictions.length} tone="info" hint="State = possible, awaiting confirmation" />
      </div>

      {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}

      <Tabs
        tabs={[
          { id: "decisions", label: "Cases", count: awaiting.length + blocked.length },
          { id: "claims", label: "Disputed claims", count: claims.length },
          { id: "contradictions", label: "Contradictions", count: contradictions.length },
        ]}
        active={tab}
        onChange={setTab}
      />

      <div className="mt-4">
        {loading ? <Loading rows={6} /> : null}

        {!loading && tab === "decisions" ? (
          [...blocked, ...awaiting].length === 0 ? (
            <Empty title="Nothing awaiting review" hint="All cases are approved or rejected." />
          ) : (
            <Card pad={false}>
              <ul className="divide-y divide-white/[0.05]">
                {[...blocked, ...awaiting].map((decision) => (
                  <li key={decision.id} className="flex flex-wrap items-center gap-4 px-4 py-3.5">
                    <div className="min-w-0 flex-1">
                      <Link href={`/decisions/view?id=${decision.id}`} className="text-[13px] font-medium text-slate-100 hover:text-accent">
                        {decision.title}
                      </Link>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                        <span>{titleCase(decision.decision_type)}</span>
                        <span>·</span>
                        <span>{decision.documents_count} documents</span>
                        <span>·</span>
                        <span>as of {humanDate(decision.as_of_date)}</span>
                        <span>·</span>
                        <span>updated {relativeTime(decision.updated_at)}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {decision.blocking_failures.length ? <Badge tone="bad">{decision.blocking_failures.length} blocking</Badge> : <Badge tone="ok">no blockers</Badge>}
                      <StatusPill status={decision.gate_status} />
                      <Link href={`/decisions/view?id=${decision.id}`} className="btn-ghost px-2.5 py-1.5 text-[12px]">
                        {decision.gate_status === "SUFFICIENTLY_SUPPORTED" ? "Review & approve" : "Inspect gaps"}
                      </Link>
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          )
        ) : null}

        {!loading && tab === "claims" ? (
          claims.length === 0 ? (
            <Empty title="No disputed claims" hint="Claims flagged during extraction or explicitly disputed appear here." />
          ) : (
            <Card pad={false}>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px]">
                  <thead><tr><th className="th">Claim</th><th className="th">Source</th><th className="th">Status</th><th className="th" /></tr></thead>
                  <tbody>
                    {claims.map((claim) => (
                      <tr key={claim.id} className="border-t border-white/[0.05]">
                        <td className="td">
                          <div className="text-[12.5px] text-slate-100">{titleCase(claim.predicate)}: {claim.object}</div>
                          <div className="text-[11px] italic text-slate-500">“{claim.quoted_span.slice(0, 130)}”</div>
                        </td>
                        <td className="td text-[12px]">{claim.document?.title ?? "—"}<div className="text-[10.5px] text-slate-500">v{claim.version?.version_number ?? 1}</div></td>
                        <td className="td"><StatusPill status={claim.verification_status} /></td>
                        <td className="td text-right"><Link href={`/claims?q=${encodeURIComponent(claim.object.slice(0, 40))}`} className="link text-[11.5px]">Review</Link></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )
        ) : null}

        {!loading && tab === "contradictions" ? (
          contradictions.length === 0 ? (
            <Empty title="No open contradictions" hint="All detected contradictions have been reviewed." />
          ) : (
            <Card pad={false}>
              <ul className="divide-y divide-white/[0.05]">
                {contradictions.map((item) => (
                  <li key={item.id} className="px-4 py-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[12.5px] font-medium text-slate-100">{titleCase(item.kind)}</span>
                      <Badge tone={item.blocking ? "bad" : "warn"}>{item.blocking ? "blocking" : item.state}</Badge>
                    </div>
                    <div className="mt-1 text-[11.5px] text-slate-400">
                      {item.claim_a.document_title} · {item.claim_a.object} ↔ {item.claim_b.document_title} · {item.claim_b.object}
                    </div>
                    <Link href="/contradictions" className="link mt-1 inline-block text-[11.5px]">Open in contradictions</Link>
                  </li>
                ))}
              </ul>
            </Card>
          )
        ) : null}
      </div>
    </div>
  );
}

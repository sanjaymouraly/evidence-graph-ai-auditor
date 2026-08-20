"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { DecisionSummary, Paged } from "@/lib/types";
import { humanDate, relativeTime, titleCase } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, Icon, Loading, PageHeader, StatusPill, useDebounced } from "@/components/ui";

const STAGES = ["", "DRAFT", "EVIDENCE_INCOMPLETE", "CONTRADICTED", "REVIEW_REQUIRED", "SUFFICIENTLY_SUPPORTED", "APPROVED", "REJECTED"];
const TYPES = ["", "procurement", "engineering_change", "project_approval", "policy_compliance"];

export default function DecisionsPage() {
  const [items, setItems] = useState<DecisionSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("");
  const [decisionType, setDecisionType] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const debounced = useDebounced(query, 300);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({ page_size: "50" });
        if (status) params.set("status", status);
        if (decisionType) params.set("decision_type", decisionType);
        if (debounced) params.set("q", debounced);
        const response = await api<Paged<DecisionSummary>>(`/api/decisions?${params.toString()}`);
        if (!cancelled) {
          setItems(response.items);
          setTotal(response.total);
        }
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : "Failed to load decisions");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [status, decisionType, debounced]);

  const counts = useMemo(() => {
    const map: Record<string, number> = {};
    items.forEach((item) => {
      map[item.status] = (map[item.status] ?? 0) + 1;
    });
    return map;
  }, [items]);

  return (
    <div className="fade-in">
      <PageHeader
        title="Decisions"
        subtitle="Each case binds a document set to a deterministic evidence gate. Status changes only through explicit, audited transitions."
        actions={<Link href="/decisions/new" className="btn-primary"><Icon name="plus" className="h-3.5 w-3.5" /> New decision</Link>}
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          { label: "Total cases", value: total, tone: "info" as const },
          { label: "Approved", value: counts.APPROVED ?? 0, tone: "ok" as const },
          { label: "Blocked", value: (counts.CONTRADICTED ?? 0) + (counts.EVIDENCE_INCOMPLETE ?? 0), tone: "bad" as const },
          { label: "Draft / review", value: (counts.DRAFT ?? 0) + (counts.REVIEW_REQUIRED ?? 0), tone: "warn" as const },
        ].map((stat) => (
          <Card key={stat.label} className="flex items-center justify-between">
            <div>
              <div className="label">{stat.label}</div>
              <div className="mt-1.5 text-xl font-semibold text-slate-50">{stat.value}</div>
            </div>
            <span className={`h-2 w-2 rounded-full ${stat.tone === "ok" ? "bg-ok" : stat.tone === "bad" ? "bg-bad" : stat.tone === "warn" ? "bg-warn" : "bg-info"}`} />
          </Card>
        ))}
      </div>

      <Card pad={false}>
        <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.06] px-4 py-3">
          <div className="relative min-w-[220px] flex-1">
            <Icon name="search" className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-500" />
            <input className="input pl-9" placeholder="Search decisions…" value={query} onChange={(event) => setQuery(event.target.value)} />
          </div>
          <select className="input w-auto" value={status} onChange={(event) => setStatus(event.target.value)}>
            {STAGES.map((stage) => <option key={stage} value={stage}>{stage ? titleCase(stage) : "All statuses"}</option>)}
          </select>
          <select className="input w-auto" value={decisionType} onChange={(event) => setDecisionType(event.target.value)}>
            {TYPES.map((type) => <option key={type} value={type}>{type ? titleCase(type) : "All types"}</option>)}
          </select>
        </div>

        {error ? <div className="p-4"><ErrorBox message={error} /></div> : null}
        {loading ? <div className="p-4"><Loading rows={6} /></div> : items.length === 0 ? (
          <div className="p-4"><Empty title="No decisions match" hint="Create a case or clear the filters." action={<Link href="/decisions/new" className="btn-primary">New decision</Link>} /></div>
        ) : (
          <ul className="divide-y divide-white/[0.05]">
            {items.map((decision) => {
              const blocking = decision.blocking_failures.length;
              return (
                <li key={decision.id}>
                  <Link href={`/decisions/view?id=${decision.id}`} className="flex flex-wrap items-start gap-4 px-4 py-4 transition-colors hover:bg-white/[0.025]">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[13.5px] font-medium text-slate-100">{decision.title}</span>
                        {decision.exception_recorded ? <Badge tone="warn">exception</Badge> : null}
                      </div>
                      <p className="mt-1 line-clamp-2 max-w-3xl text-[12px] leading-relaxed text-slate-500">{decision.description}</p>
                      <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-slate-500">
                        <span>{titleCase(decision.decision_type)}</span>
                        <span>·</span>
                        <span>{decision.documents_count} documents</span>
                        <span>·</span>
                        <span>as of {humanDate(decision.as_of_date)}</span>
                        <span>·</span>
                        <span>updated {relativeTime(decision.updated_at)}</span>
                        {blocking ? <><span>·</span><span className="text-red-300">{blocking} blocking check(s)</span></> : null}
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1.5">
                      <StatusPill status={decision.status} />
                      <span className="text-[11px] text-slate-500">
                        gate: {titleCase(decision.gate_status ?? "not evaluated")}
                      </span>
                      <span className="text-[11px] text-slate-500">
                        {decision.summary?.passed ?? 0}/{decision.summary?.checks ?? 0} checks passed
                      </span>
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}

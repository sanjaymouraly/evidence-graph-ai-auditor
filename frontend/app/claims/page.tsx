"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, detailText, ApiError } from "@/lib/api";
import type { Claim, Paged } from "@/lib/types";
import { humanDate, locatorText, money, percent, relativeTime, titleCase } from "@/lib/format";
import { useQueryParam } from "@/lib/useQuery";
import { Badge, Card, Empty, ErrorBox, Icon, Loading, PageHeader, StatusPill, useDebounced } from "@/components/ui";

type Facets = { predicates: Record<string, number>; statuses: Record<string, number> };

type Trace = {
  claim: { id: string; verification_status: string; confidence: number; quoted_span: string };
  span: { id: string; page?: number | null; sheet?: string | null; cell_range?: string | null; paragraph_index?: number | null; char_start?: number | null; text: string } | null;
  version: { id: string; number: number; filename: string; content_hash: string; is_current: boolean } | null;
  document: { id: string; title: string; doc_type: string } | null;
  contradictions: { id: string; kind: string; state: string; severity: string }[];
  decisions: { id: string; title: string; status: string }[];
};

export default function ClaimsPage() {
  const initialDocument = useQueryParam("document_id");
  const initialQuery = useQueryParam("q");
  const [items, setItems] = useState<Claim[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [query, setQuery] = useState("");
  const [predicate, setPredicate] = useState("");
  const [status, setStatus] = useState("");
  const [facets, setFacets] = useState<Facets | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Claim | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [reviewNote, setReviewNote] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const debounced = useDebounced(query, 320);

  useEffect(() => {
    if (initialDocument) setQuery("");
    if (initialQuery) setQuery(initialQuery);
  }, [initialDocument, initialQuery]);

  useEffect(() => {
    api<Facets>("/api/claims/facets").then(setFacets).catch(() => setFacets(null));
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({ page: String(page), page_size: "25" });
        if (debounced) params.set("q", debounced);
        if (predicate) params.set("predicate", predicate);
        if (status) params.set("verification_status", status);
        if (initialDocument) params.set("document_id", initialDocument);
        const response = await api<Paged<Claim>>(`/api/claims?${params.toString()}`);
        if (cancelled) return;
        setItems(response.items);
        setTotal(response.total);
        setPages(response.pages);
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : "Failed to load claims");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [page, debounced, predicate, status, initialDocument]);

  async function openClaim(claim: Claim) {
    setSelected(claim);
    setTrace(null);
    setReviewNote("");
    setActionError(null);
    try {
      const payload = await api<Trace>(`/api/claims/${claim.id}/trace`);
      setTrace(payload);
    } catch {
      setTrace(null);
    }
  }

  async function review(action: "verify" | "flag" | "reject" | "reset") {
    if (!selected) return;
    try {
      const updated = await api<Claim>(`/api/claims/${selected.id}/review`, {
        method: "POST",
        body: { action, note: reviewNote },
      });
      setSelected(updated);
      setItems((previous) => previous.map((item) => (item.id === updated.id ? updated : item)));
      setActionError(null);
    } catch (exc) {
      setActionError(exc instanceof ApiError ? detailText(exc.detail) : "Review failed");
    }
  }

  return (
    <div className="fade-in">
      <PageHeader
        title="Claims"
        subtitle="Typed assertions with normalized values, validity windows and a verified quote. Review actions are recorded in the audit chain."
        actions={<button className="btn-ghost" onClick={() => window.open("/api/claims/export.csv")}><Icon name="download" className="h-3.5 w-3.5" /> Export CSV</button>}
      />

      <div className="grid gap-4 2xl:grid-cols-[minmax(0,1fr)_420px]">
        <Card pad={false}>
          <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.06] px-4 py-3">
            <div className="relative min-w-[220px] flex-1">
              <Icon name="search" className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-500" />
              <input className="input pl-9" placeholder="Search subject, value or quote…" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} />
            </div>
            <select className="input w-auto" value={predicate} onChange={(event) => { setPredicate(event.target.value); setPage(1); }}>
              <option value="">All predicates</option>
              {Object.entries(facets?.predicates ?? {}).sort((a, b) => b[1] - a[1]).map(([key, count]) => (
                <option key={key} value={key}>{titleCase(key)} ({count})</option>
              ))}
            </select>
            <select className="input w-auto" value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}>
              <option value="">All statuses</option>
              {Object.keys(facets?.statuses ?? {}).map((key) => <option key={key} value={key}>{titleCase(key)} ({facets?.statuses[key] ?? 0})</option>)}
            </select>
            <span className="text-[12px] text-slate-500">{total} claims</span>
          </div>

          {error ? <div className="p-4"><ErrorBox message={error} /></div> : null}
          {loading ? <div className="p-4"><Loading rows={8} /></div> : items.length === 0 ? (
            <div className="p-4"><Empty title="No claims match" hint="Try a different predicate or search term." /></div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px]">
                <thead>
                  <tr>
                    <th className="th">Claim</th>
                    <th className="th">Value</th>
                    <th className="th">Source</th>
                    <th className="th">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((claim) => (
                    <tr key={claim.id} className="row-hover cursor-pointer border-t border-white/[0.05]" onClick={() => void openClaim(claim)}>
                      <td className="td">
                        <div className="text-[12.5px] font-medium text-slate-100">{titleCase(claim.predicate)}</div>
                        <div className="text-[11px] text-slate-500">{claim.subject || "unattributed"} · conf {percent(claim.extraction_confidence, 0)}</div>
                      </td>
                      <td className="td">
                        <div className="text-[12.5px] text-slate-200">
                          {claim.value_numeric !== null && claim.value_numeric !== undefined ? money(claim.value_numeric, claim.currency) : claim.object}
                        </div>
                        {claim.valid_until ? <div className="text-[10.5px] text-slate-500">valid to {humanDate(claim.valid_until)}</div> : null}
                      </td>
                      <td className="td">
                        <Link href={`/documents/view?id=${claim.document?.id ?? ""}`} className="text-[12px] text-slate-300 hover:text-accent" onClick={(event) => event.stopPropagation()}>
                          {claim.document?.title ?? "—"}
                        </Link>
                        <div className="text-[10.5px] text-slate-500">v{claim.version?.version_number ?? 1} · {locatorText(claim.span)}</div>
                      </td>
                      <td className="td"><StatusPill status={claim.verification_status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="flex items-center justify-between border-t border-white/[0.06] px-4 py-2.5">
            <span className="text-[11.5px] text-slate-500">Page {page} of {pages}</span>
            <div className="flex gap-2">
              <button className="btn-ghost px-2 py-1 text-[12px]" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>Previous</button>
              <button className="btn-ghost px-2 py-1 text-[12px]" disabled={page >= pages} onClick={() => setPage((value) => Math.min(pages, value + 1))}>Next</button>
            </div>
          </div>
        </Card>

        <Card className="h-fit">
          {!selected ? (
            <Empty title="Select a claim" hint="Inspect the exact quote, its span locator and every dependent decision." />
          ) : (
            <div>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-sm font-semibold text-slate-100">{titleCase(selected.predicate)}</div>
                  <div className="text-[11.5px] text-slate-500">{selected.subject}</div>
                </div>
                <button className="rounded p-1 text-slate-500 hover:bg-white/[0.05] hover:text-slate-300" onClick={() => setSelected(null)} aria-label="Close"><Icon name="x" className="h-4 w-4" /></button>
              </div>

              <div className="mt-3 rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
                <div className="text-[13px] text-slate-100">{selected.object}</div>
                <div className="mt-2 border-l-2 border-accent/40 pl-2.5 text-[11.5px] italic leading-relaxed text-slate-400">“{selected.quoted_span}”</div>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <Badge tone={selected.citation?.resolves ? "ok" : "bad"}>{selected.citation?.resolves ? "quote resolves" : "quote unresolved"}</Badge>
                  <Badge tone="neutral">{locatorText(selected.span)}</Badge>
                </div>
              </div>

              <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11.5px]">
                {[
                  ["Verification", <StatusPill key="v" status={selected.verification_status} />],
                  ["Review state", selected.review_status],
                  ["Confidence", percent(selected.extraction_confidence, 0)],
                  ["Extraction", selected.extraction_method],
                  ["Effective", humanDate(selected.effective_date)],
                  ["Valid until", humanDate(selected.valid_until)],
                  ["Document", selected.document?.title ?? "—"],
                  ["Version", `v${selected.version?.version_number ?? 1}`],
                ].map(([label, value]) => (
                  <div key={String(label)} className="flex flex-col">
                    <dt className="text-[10px] uppercase tracking-wide text-slate-500">{label}</dt>
                    <dd className="text-slate-300">{value as React.ReactNode}</dd>
                  </div>
                ))}
              </dl>

              {trace?.contradictions?.length ? (
                <div className="mt-3 rounded-lg border border-bad/25 bg-bad/[0.06] p-2.5">
                  <div className="text-[11.5px] font-medium text-red-200">{trace.contradictions.length} contradiction(s) reference this claim</div>
                  <ul className="mt-1 space-y-1">
                    {trace.contradictions.map((item) => (
                      <li key={item.id} className="flex items-center justify-between text-[11px] text-red-200/80">
                        <span>{titleCase(item.kind)}</span>
                        <span>{item.state} · {item.severity}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {trace?.decisions?.length ? (
                <div className="mt-3">
                  <div className="label mb-1">Used by decisions</div>
                  <ul className="space-y-1">
                    {trace.decisions.map((decision) => (
                      <li key={decision.id}>
                        <Link href={`/decisions/view?id=${decision.id}`} className="link text-[11.5px]">{decision.title}</Link>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div className="mt-4 border-t border-white/[0.07] pt-3">
                <div className="label mb-1.5">Review</div>
                <textarea className="input min-h-[60px]" placeholder="Optional note (audit recorded)" value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} />
                {actionError ? <div className="mt-2 text-[11.5px] text-red-300">{actionError}</div> : null}
                <div className="mt-2 flex flex-wrap gap-2">
                  <button className="btn-primary px-2.5 py-1.5 text-[12px]" onClick={() => void review("verify")}>Verify</button>
                  <button className="btn-ghost px-2.5 py-1.5 text-[12px]" onClick={() => void review("flag")}>Dispute</button>
                  <button className="btn-danger px-2.5 py-1.5 text-[12px]" onClick={() => void review("reject")}>Reject</button>
                  <button className="btn-ghost px-2.5 py-1.5 text-[12px]" onClick={() => void review("reset")}>Reset</button>
                </div>
                <p className="mt-2 text-[10.5px] leading-relaxed text-slate-500">
                  Rejecting a claim excludes it from gate coverage. Disputed claims raise data-quality warnings.
                </p>
              </div>
            </div>
          )}
        </Card>
      </div>

    </div>
  );
}

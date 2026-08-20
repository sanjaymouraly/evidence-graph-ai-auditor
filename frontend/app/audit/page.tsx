"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, download } from "@/lib/api";
import type { AuditEvent, Paged } from "@/lib/types";
import { humanDateTime, shortId, titleCase } from "@/lib/format";
import { Badge, Card, CopyButton, Empty, ErrorBox, Icon, Loading, PageHeader, Stat } from "@/components/ui";

type VerifyResult = { valid: boolean; events: number; head_hash?: string; errors?: { error: string; event_id?: string }[] };

export default function AuditPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [action, setAction] = useState("");
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({ page: String(page), page_size: "40" });
        if (action) params.set("action", action);
        const response = await api<Paged<AuditEvent>>(`/api/audit?${params.toString()}`);
        if (!cancelled) {
          setEvents(response.items);
          setTotal(response.total);
          setPages(response.pages);
        }
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : "Failed to load audit trail");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [page, action]);

  const runVerify = useCallback(async () => {
    setVerifying(true);
    setError(null);
    try {
      const result = await api<VerifyResult>("/api/audit/verify");
      setVerify(result);
    } catch (exc) {
      setVerify({ valid: false, events: 0, errors: [{ error: exc instanceof Error ? exc.message : "Verification failed" }] });
    } finally {
      setVerifying(false);
    }
  }, []);

  useEffect(() => {
    void runVerify();
  }, [runVerify]);

  const actions = useMemo(() => {
    const counts = new Map<string, number>();
    events.forEach((event) => counts.set(event.action, (counts.get(event.action) ?? 0) + 1));
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 10);
  }, [events]);

  return (
    <div className="fade-in">
      <PageHeader
        title="Audit trail"
        subtitle="Append-only, hash-chained record of every material action. Each event seals the previous hash, so any edit or deletion breaks verification."
        actions={
          <>
            <button className="btn-ghost" onClick={() => void runVerify()} disabled={verifying}>
              <Icon name="shield" className="h-3.5 w-3.5" /> {verifying ? "Verifying…" : "Verify chain"}
            </button>
            <button className="btn-ghost" onClick={() => void download("/api/audit/export.csv", "audit-trail.csv")}>
              <Icon name="download" className="h-3.5 w-3.5" /> Export CSV
            </button>
          </>
        }
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Events" value={total} tone="info" hint="Workspace-scoped" />
        <Stat
          label="Chain integrity"
          value={verify ? (verify.valid ? "Valid" : "Broken") : "—"}
          tone={verify?.valid ? "ok" : "bad"}
          hint={verify ? `${verify.events} events verified` : "not yet verified"}
        />
        <Stat label="Head hash" value={<span className="mono text-[13px]">{verify?.head_hash ? `${verify.head_hash.slice(0, 14)}…` : "—"}</span>} tone="neutral" hint="Latest event hash" />
        <Stat label="Errors" value={verify?.errors?.length ?? 0} tone={verify?.errors?.length ? "bad" : "ok"} hint="Hash mismatches or broken links" />
      </div>

      {verify && !verify.valid ? (
        <div className="mb-4 rounded-lg border border-bad/30 bg-bad/[0.08] p-4">
          <div className="text-[13px] font-medium text-red-200">Audit chain verification failed</div>
          <ul className="mt-2 space-y-1 text-[11.5px] text-red-200/80">
            {(verify.errors ?? []).slice(0, 8).map((item, index) => (
              <li key={index}>• {item.error}{item.event_id ? ` (event ${shortId(item.event_id)})` : ""}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
        <Card pad={false}>
          <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.06] px-4 py-3">
            <select className="input w-auto" value={action} onChange={(event) => { setAction(event.target.value); setPage(1); }}>
              <option value="">All actions</option>
              {actions.map(([name, count]) => <option key={name} value={name}>{titleCase(name.replace(/[._]/g, " "))} ({count})</option>)}
            </select>
            <span className="text-[12px] text-slate-500">{total} events · page {page} of {pages}</span>
          </div>

          {loading ? <div className="p-4"><Loading rows={8} /></div> : events.length === 0 ? (
            <div className="p-4"><Empty title="No audit events" hint="Actions on documents, claims and decisions appear here." /></div>
          ) : (
            <ul className="divide-y divide-white/[0.05]">
              {events.map((event) => (
                <li key={event.id}>
                  <button
                    type="button"
                    className="flex w-full items-start gap-3 px-4 py-2.5 text-left hover:bg-white/[0.02]"
                    onClick={() => setExpandedId((current) => (current === event.id ? null : event.id))}
                  >
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent/70" />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="text-[12.5px] text-slate-200">{titleCase(event.action.replace(/[._]/g, " "))}</span>
                        <Badge tone="neutral">{event.object_type}</Badge>
                      </span>
                      <span className="mt-0.5 block text-[11px] text-slate-500">
                        {event.actor ?? "system"} · {humanDateTime(event.created_at)} · {shortId(event.object_id)}
                      </span>
                    </span>
                    <span className="mono hidden shrink-0 text-[10px] text-slate-600 sm:block">{shortId(event.event_hash, 12)}…</span>
                  </button>
                  {expandedId === event.id ? (
                    <div className="border-t border-white/[0.05] bg-ink-950/40 px-4 py-3">
                      <div className="grid gap-3 sm:grid-cols-2">
                        <div>
                          <div className="label mb-1">Event</div>
                          <div className="space-y-1 text-[11px] text-slate-400">
                            <div className="flex justify-between gap-2"><span className="text-slate-500">id</span><span className="mono">{event.id}</span></div>
                            <div className="flex justify-between gap-2"><span className="text-slate-500">object</span><span className="mono">{event.object_type}:{event.object_id}</span></div>
                            <div className="flex justify-between gap-2"><span className="text-slate-500">correlation</span><span className="mono">{event.correlation_id ?? "—"}</span></div>
                          </div>
                        </div>
                        <div>
                          <div className="label mb-1">Chain</div>
                          <div className="space-y-1 text-[11px]">
                            <div className="flex items-start justify-between gap-2">
                              <span className="text-slate-500">prev</span>
                              <span className="mono break-all text-slate-400">{event.prev_hash ?? "—"}</span>
                            </div>
                            <div className="flex items-start justify-between gap-2">
                              <span className="text-slate-500">event</span>
                              <span className="mono break-all text-slate-400">{event.event_hash ?? "—"}</span>
                            </div>
                            {event.event_hash ? <CopyButton value={event.event_hash} label="Copy event hash" /> : null}
                          </div>
                        </div>
                      </div>
                      <div className="mt-3">
                        <div className="label mb-1">Payload</div>
                        <pre className="mono max-h-56 overflow-auto rounded-md border border-white/[0.06] bg-ink-950/70 p-2 text-[10.5px] leading-relaxed text-slate-400">
                          {JSON.stringify(event.payload ?? {}, null, 2)}
                        </pre>
                      </div>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}

          <div className="flex items-center justify-between border-t border-white/[0.06] px-4 py-2.5">
            <span className="text-[11.5px] text-slate-500">Page {page} of {pages}</span>
            <div className="flex gap-2">
              <button className="btn-ghost px-2 py-1 text-[12px]" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>Previous</button>
              <button className="btn-ghost px-2 py-1 text-[12px]" disabled={page >= pages} onClick={() => setPage((value) => Math.min(pages, value + 1))}>Next</button>
            </div>
          </div>
        </Card>

        <div className="space-y-4">
          <Card>
            <div className="label mb-2">How verification works</div>
            <ol className="space-y-2 text-[11.5px] leading-relaxed text-slate-400">
              <li>1. Each event hashes its canonical payload plus the previous event hash.</li>
              <li>2. Verification replays the chain in order and recomputes every hash.</li>
              <li>3. Any modified field, reordered event or deletion produces a mismatch at that event.</li>
            </ol>
          </Card>
          <Card>
            <div className="label mb-2">Top actions (this page)</div>
            <ul className="space-y-1">
              {actions.map(([name, count]) => (
                <li key={name} className="flex items-center justify-between text-[11.5px]">
                  <span className="truncate text-slate-400">{titleCase(name.replace(/[._]/g, " "))}</span>
                  <span className="tabular-nums text-slate-300">{count}</span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}

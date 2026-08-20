"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ApiError, api, detailText } from "@/lib/api";
import type { Contradiction, Paged } from "@/lib/types";
import { humanDateTime, locatorText, percent, titleCase } from "@/lib/format";
import { useAuth, can } from "@/lib/auth";
import { Badge, Card, Empty, ErrorBox, Icon, Loading, PageHeader, SeverityDot, StatusPill } from "@/components/ui";

export default function ContradictionsPage() {
  const { user } = useAuth();
  const [items, setItems] = useState<Contradiction[]>([]);
  const [total, setTotal] = useState(0);
  const [state, setState] = useState("all");
  const [kind, setKind] = useState("");
  const [blockingOnly, setBlockingOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ page_size: "50" });
      if (state !== "all") params.set("state", state);
      if (kind) params.set("kind", kind);
      if (blockingOnly) params.set("blocking", "true");
      const response = await api<Paged<Contradiction>>(`/api/contradictions?${params.toString()}`);
      setItems(response.items);
      setTotal(response.total);
      setActiveId((current) => current ?? response.items[0]?.id ?? null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to load contradictions");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, kind, blockingOnly]);

  const active = useMemo(() => items.find((item) => item.id === activeId) ?? items[0] ?? null, [items, activeId]);
  const kinds = useMemo(() => Array.from(new Set(items.map((item) => item.kind))).sort(), [items]);
  const stats = useMemo(() => ({
    blocking: items.filter((item) => item.blocking).length,
    confirmed: items.filter((item) => item.state === "confirmed").length,
    possible: items.filter((item) => item.state === "possible").length,
  }), [items]);

  async function act(action: "confirm" | "dismiss" | "resolve") {
    if (!active) return;
    setBusy(true);
    setActionError(null);
    try {
      const updated = await api<Contradiction>(`/api/contradictions/${active.id}/${action}`, {
        method: "POST",
        body: { note },
      });
      setItems((previous) => previous.map((item) => (item.id === updated.id ? updated : item)));
      setNote("");
    } catch (exc) {
      setActionError(exc instanceof ApiError ? detailText(exc.detail) : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  async function redetect() {
    setBusy(true);
    try {
      await api("/api/contradictions/detect", { method: "POST", body: {} });
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Detection failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fade-in">
      <PageHeader
        title="Contradictions"
        subtitle="Cross-source conflicts detected by deterministic rules. Semantically different contexts (period, scope, entity, currency) are never treated as conflicts."
        actions={
          <>
            <button className="btn-ghost" onClick={() => void redetect()} disabled={busy}><Icon name="refresh" className="h-3.5 w-3.5" /> Re-run detection</button>
            <button className="btn-ghost" onClick={() => void load()}><Icon name="refresh" className="h-3.5 w-3.5" /> Refresh</button>
          </>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className="input w-auto" value={state} onChange={(event) => setState(event.target.value)}>
          <option value="all">All states</option>
          <option value="confirmed">Confirmed ({stats.confirmed})</option>
          <option value="possible">Possible ({stats.possible})</option>
          <option value="dismissed">Dismissed</option>
          <option value="resolved">Resolved</option>
        </select>
        <select className="input w-auto" value={kind} onChange={(event) => setKind(event.target.value)}>
          <option value="">All kinds</option>
          {kinds.map((item) => <option key={item} value={item}>{titleCase(item)}</option>)}
        </select>
        <label className="flex items-center gap-2 text-[12px] text-slate-300">
          <input type="checkbox" checked={blockingOnly} onChange={(event) => setBlockingOnly(event.target.checked)} className="h-3.5 w-3.5 rounded border-white/20 bg-ink-900" />
          Blocking only ({stats.blocking})
        </label>
        <span className="ml-auto text-[12px] text-slate-500">{total} records</span>
      </div>

      {error ? <div className="mb-4"><ErrorBox message={error} onRetry={load} /></div> : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
        <Card pad={false} className="overflow-hidden">
          <div className="border-b border-white/[0.06] px-4 py-2.5 text-[12.5px] text-slate-300">Detected conflicts</div>
          {loading ? <div className="p-4"><Loading rows={6} /></div> : items.length === 0 ? (
            <div className="p-4"><Empty title="No contradictions" hint="No cross-source conflicts match the filters." /></div>
          ) : (
            <ul className="max-h-[620px] divide-y divide-white/[0.05] overflow-y-auto">
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => setActiveId(item.id)}
                    className={`w-full px-4 py-3 text-left transition-colors ${active?.id === item.id ? "bg-white/[0.04]" : "hover:bg-white/[0.02]"}`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-1.5 text-[12.5px] font-medium text-slate-100">
                        <SeverityDot severity={item.severity} /> {titleCase(item.kind)}
                      </span>
                      {item.blocking ? <Badge tone="bad">blocking</Badge> : <Badge tone="warn">{item.state}</Badge>}
                    </div>
                    <div className="mt-1 truncate text-[11.5px] text-slate-400">
                      {item.claim_a.document_title ?? "?"}
                      <span className="text-slate-600"> v{item.claim_a.version_number ?? 1}</span> ↔{" "}
                      {item.claim_b.document_title ?? "?"}
                      <span className="text-slate-600"> v{item.claim_b.version_number ?? 1}</span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-2 text-[10.5px] text-slate-500">
                      <span>{item.claim_a.object ?? "—"}</span>
                      <span>vs</span>
                      <span>{item.claim_b.object ?? "—"}</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {active ? (
          <Card>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-semibold text-slate-100">{titleCase(active.kind)}</h2>
                  <Badge tone={active.blocking ? "bad" : "warn"}>{active.blocking ? "blocks approval" : "non-blocking"}</Badge>
                  <StatusPill status={active.state} />
                </div>
                <p className="mt-1 text-[11.5px] text-slate-500">
                  {active.detected_by} · confidence {percent(active.confidence, 0)} · detected {humanDateTime(active.created_at)} · severity {active.severity}
                </p>
              </div>
              <div className="flex gap-2">
                {can(user, "contradiction:review") ? (
                  <>
                    <button className="btn-ghost px-2.5 py-1.5 text-[12px]" disabled={busy || active.state === "dismissed"} onClick={() => void act("dismiss")}>Dismiss</button>
                    <button className="btn-ghost px-2.5 py-1.5 text-[12px]" disabled={busy || active.state === "resolved"} onClick={() => void act("resolve")}>Resolve</button>
                    <button className="btn-primary px-2.5 py-1.5 text-[12px]" disabled={busy || active.state === "confirmed"} onClick={() => void act("confirm")}>Confirm</button>
                  </>
                ) : null}
              </div>
            </div>

            <div className="mt-4 grid gap-3 md:grid-cols-2">
              {([["A", active.claim_a], ["B", active.claim_b]] as const).map(([side, claim]) => (
                <div key={side} className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="label">Claim {side}</span>
                    <StatusPill status={claim.verification_status} />
                  </div>
                  <div className="mt-2 text-[12.5px] text-slate-100">{claim.object ?? "—"}</div>
                  <div className="mt-1 text-[11.5px] text-slate-500">{claim.subject ?? "unattributed"} · {titleCase(claim.predicate ?? "")}</div>
                  <div className="mt-2 border-l-2 border-white/15 pl-2 text-[11px] italic leading-relaxed text-slate-400">
                    “{claim.quoted_span ?? "—"}”
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                    {claim.document_id ? <Link href={`/documents/view?id=${claim.document_id}`} className="link">{claim.document_title}</Link> : null}
                    <span className="text-slate-500">v{claim.version_number ?? 1}</span>
                    <Badge tone="neutral">{locatorText(claim.locator)}</Badge>
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-4 rounded-lg border border-white/[0.06] bg-white/[0.02] p-3">
              <div className="label mb-1">Why this was flagged</div>
              <p className="text-[11.5px] leading-relaxed text-slate-400">{active.reasoning}</p>
            </div>

            <div className="mt-4">
              <div className="label mb-1.5">Review note (required to dismiss)</div>
              <textarea className="input min-h-[62px]" value={note} onChange={(event) => setNote(event.target.value)} placeholder="Explain the evidence basis for your decision…" />
              {actionError ? <div className="mt-2 text-[11.5px] text-red-300">{actionError}</div> : null}
              {active.review_note ? (
                <div className="mt-2 rounded-md border border-white/[0.06] p-2 text-[11.5px] text-slate-400">
                  <span className="text-slate-500">Last review:</span> {active.review_note}
                </div>
              ) : null}
              <p className="mt-2 text-[10.5px] leading-relaxed text-slate-500">
                Confirming a contradiction keeps it blocking. Dismissing requires a written justification and is recorded in the audit chain.
              </p>
            </div>
          </Card>
        ) : (
          <Card><Empty title="Nothing selected" hint="Pick a contradiction from the list." /></Card>
        )}
      </div>
    </div>
  );
}

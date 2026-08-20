"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api, detailText, download } from "@/lib/api";
import type { DecisionSummary, Paged, ReportRecord } from "@/lib/types";
import { humanDateTime, relativeTime } from "@/lib/format";
import { can, useAuth } from "@/lib/auth";
import { Badge, Card, Empty, ErrorBox, Icon, Loading, PageHeader, useDebounced } from "@/components/ui";

type SnapshotVerification = { hash_match: boolean; errors?: string[]; snapshot_hash?: string };

export default function ReportsPage() {
  const { user } = useAuth();
  const [reports, setReports] = useState<ReportRecord[]>([]);
  const [decisions, setDecisions] = useState<DecisionSummary[]>([]);
  const [query, setQuery] = useState("");
  const [selectedDecision, setSelectedDecision] = useState("");
  const [kind, setKind] = useState<"pdf" | "json" | "csv" | "manifest">("pdf");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [verification, setVerification] = useState<Record<string, SnapshotVerification>>({});
  const debounced = useDebounced(query, 280);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ page_size: "60" });
      if (debounced) params.set("q", debounced);
      const [reportPage, decisionPage] = await Promise.all([
        api<Paged<ReportRecord>>(`/api/reports?${params.toString()}`),
        api<Paged<DecisionSummary>>("/api/decisions?page_size=50"),
      ]);
      setReports(reportPage.items);
      setDecisions(decisionPage.items);
      setSelectedDecision((current) => current || decisionPage.items[0]?.id || "");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to load reports");
    } finally {
      setLoading(false);
    }
  }, [debounced]);

  useEffect(() => {
    void load();
  }, [load]);

  async function generate() {
    if (!selectedDecision) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const created = await api<ReportRecord>(`/api/decisions/${selectedDecision}/reports`, {
        method: "POST",
        body: { decision_id: selectedDecision, kind },
      });
      setNote(`${kind.toUpperCase()} report generated with snapshot ${created.snapshot_id.slice(0, 10)}…`);
      await load();
    } catch (exc) {
      setError(exc instanceof ApiError ? detailText(exc.detail) : "Report generation failed");
    } finally {
      setBusy(false);
    }
  }

  async function verify(report: ReportRecord) {
    try {
      const result = await api<SnapshotVerification>(`/api/snapshots/${report.snapshot_id}/verify`);
      setVerification((previous) => ({ ...previous, [report.id]: result }));
    } catch (exc) {
      setVerification((previous) => ({ ...previous, [report.id]: { hash_match: false, errors: [String(exc)] } }));
    }
  }

  async function regenerate(report: ReportRecord) {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/reports/${report.id}/regenerate`, { method: "POST" });
      await load();
      setNote("Report regenerated from the frozen snapshot; hashes still verify.");
    } catch (exc) {
      setError(exc instanceof ApiError ? detailText(exc.detail) : "Regeneration failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fade-in">
      <PageHeader
        title="Reports & evidence manifests"
        subtitle="Each report freezes a snapshot — document hashes, claim set, gate result, audit head — so a third party can re-verify the decision later."
      />

      <Card className="mb-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-[280px] flex-1">
            <span className="mb-1.5 block text-[12px] font-medium text-slate-300">Decision</span>
            <select className="input" value={selectedDecision} onChange={(event) => setSelectedDecision(event.target.value)}>
              {decisions.map((decision) => <option key={decision.id} value={decision.id}>{decision.title}</option>)}
            </select>
          </label>
          <label>
            <span className="mb-1.5 block text-[12px] font-medium text-slate-300">Format</span>
            <select className="input" value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}>
              <option value="pdf">PDF</option>
              <option value="json">JSON</option>
              <option value="csv">CSV</option>
              <option value="manifest">Manifest</option>
            </select>
          </label>
          <button className="btn-primary" onClick={() => void generate()} disabled={busy || !selectedDecision || !can(user, "report:generate")}>
            {busy ? "Generating…" : "Generate report"}
          </button>
        </div>
        {note ? <p className="mt-3 text-[12px] text-emerald-300">{note}</p> : null}
        {error ? <div className="mt-3"><ErrorBox message={error} /></div> : null}
      </Card>

      <Card pad={false}>
        <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.06] px-4 py-3">
          <div className="relative min-w-[220px] flex-1">
            <Icon name="search" className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-500" />
            <input className="input pl-9" placeholder="Filter by decision title…" value={query} onChange={(event) => setQuery(event.target.value)} />
          </div>
          <span className="text-[12px] text-slate-500">{reports.length} reports</span>
        </div>

        {loading ? <div className="p-4"><Loading rows={5} /></div> : reports.length === 0 ? (
          <div className="p-4"><Empty title="No reports yet" hint="Generate a PDF or JSON report for a decision to freeze its evidence state." /></div>
        ) : (
          <ul className="divide-y divide-white/[0.05]">
            {reports.map((report) => {
              const check = verification[report.id];
              return (
                <li key={report.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone="neutral">{report.kind.toUpperCase()}</Badge>
                        <Link href={`/decisions/view?id=${report.decision_id}`} className="text-[12.5px] text-slate-200 hover:text-accent">
                          {report.decision_title ?? report.filename}
                        </Link>
                        {check ? <Badge tone={check.hash_match ? "ok" : "bad"}>{check.hash_match ? "snapshot verified" : "hash mismatch"}</Badge> : null}
                      </div>
                      <div className="mt-1 text-[11px] text-slate-500">
                        {report.filename} · {humanDateTime(report.created_at)} · {relativeTime(report.created_at)}
                      </div>
                      <div className="mono mt-1 break-all text-[10.5px] text-slate-600">sha256 {report.sha256}</div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <button className="btn-ghost px-2 py-1 text-[11.5px]" onClick={() => void verify(report)}>Verify</button>
                      <button className="btn-ghost px-2 py-1 text-[11.5px]" onClick={() => void download(`/api/reports/${report.id}/download`, report.filename)}>
                        <Icon name="download" className="h-3 w-3" /> Download
                      </button>
                      <button className="btn-ghost px-2 py-1 text-[11.5px]" onClick={() => void download(`/api/snapshots/${report.snapshot_id}/download`, `${report.snapshot_id}.snapshot.json`)}>Snapshot</button>
                      {can(user, "report:generate") ? <button className="btn-ghost px-2 py-1 text-[11.5px]" onClick={() => void regenerate(report)} disabled={busy}>Regenerate</button> : null}
                    </div>
                  </div>
                  {check && !check.hash_match ? (
                    <div className="mt-2 rounded-md border border-bad/30 bg-bad/[0.07] p-2 text-[11px] text-red-200">
                      {check.errors?.join("; ") ?? "Stored snapshot no longer matches its recorded hash."}
                    </div>
                  ) : null}
                  {check?.snapshot_hash ? <div className="mono mt-1 text-[10px] text-slate-600">snapshot {check.snapshot_hash.slice(0, 32)}…</div> : null}
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <p className="mt-4 max-w-3xl text-[11.5px] leading-relaxed text-slate-500">
        Verification recomputes the canonical hash of the stored snapshot payload and compares it with the hash recorded at
        snapshot creation. If any stored claim, document hash, gate result or audit head differs, verification fails loudly.
      </p>
    </div>
  );
}

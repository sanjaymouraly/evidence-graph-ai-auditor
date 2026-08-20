"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { DocumentSummary, Paged } from "@/lib/types";
import { humanDate, relativeTime, titleCase } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, Icon, Loading, PageHeader, useDebounced } from "@/components/ui";

const DOC_TYPES = ["", "quote", "contract", "certificate", "invoice", "spec", "requirements", "schedule",
  "technical_assessment", "policy", "risk_register", "email", "decision_record", "spreadsheet", "report", "generic"];

export default function DocumentsPage() {
  const [items, setItems] = useState<DocumentSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [query, setQuery] = useState("");
  const [docType, setDocType] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const debounced = useDebounced(query, 320);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({ page: String(page), page_size: "20" });
        if (debounced) params.set("q", debounced);
        if (docType) params.set("doc_type", docType);
        const response = await api<Paged<DocumentSummary>>(`/api/documents?${params.toString()}`);
        if (cancelled) return;
        setItems(response.items);
        setTotal(response.total);
        setPages(response.pages);
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : "Failed to load documents");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [page, debounced, docType]);

  const expiring = useMemo(() => items.filter((doc) => {
    const valid = doc.current_version?.valid_until;
    if (!valid) return false;
    const until = new Date(valid).getTime();
    return until < Date.now();
  }).length, [items]);

  return (
    <div className="fade-in">
      <PageHeader
        title="Documents"
        subtitle="Every source is an immutable version with a content hash and typed locators. Superseded versions remain available for audit."
        actions={<Link href="/ingestion" className="btn-primary"><Icon name="upload" className="h-3.5 w-3.5" /> Import</Link>}
      />

      <Card className="mb-4" pad={false}>
        <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.06] px-4 py-3">
          <div className="relative min-w-[220px] flex-1">
            <Icon name="search" className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-500" />
            <input
              className="input pl-9"
              placeholder="Search title, key or filename…"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(1);
              }}
            />
          </div>
          <select
            className="input w-auto"
            value={docType}
            onChange={(event) => {
              setDocType(event.target.value);
              setPage(1);
            }}
          >
            {DOC_TYPES.map((type) => <option key={type} value={type}>{type ? titleCase(type) : "All types"}</option>)}
          </select>
          <span className="text-[12px] text-slate-500">{total} documents · {expiring} expired on this page</span>
        </div>

        {error ? <div className="p-4"><ErrorBox message={error} /></div> : null}
        {loading ? (
          <div className="p-4"><Loading rows={6} /></div>
        ) : items.length === 0 ? (
          <div className="p-4"><Empty title="No documents match" hint="Adjust filters or import a new source." action={<Link href="/ingestion" className="btn-primary">Import documents</Link>} /></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px]">
              <thead>
                <tr>
                  <th className="th">Document</th>
                  <th className="th">Type</th>
                  <th className="th">Version</th>
                  <th className="th">Claims</th>
                  <th className="th">Validity</th>
                  <th className="th">Updated</th>
                </tr>
              </thead>
              <tbody>
                {items.map((doc) => {
                  const version = doc.current_version;
                  const expired = version?.valid_until ? new Date(version.valid_until).getTime() < Date.now() : false;
                  return (
                    <tr key={doc.id} className="row-hover border-t border-white/[0.05]">
                      <td className="td">
                        <Link href={`/documents/view?id=${doc.id}`} className="font-medium text-slate-100 hover:text-accent">{doc.title}</Link>
                        <div className="mt-0.5 flex items-center gap-2 text-[11px] text-slate-500">
                          <span className="mono">{doc.doc_key}</span>
                          {version?.injection_flags?.length ? <Badge tone="bad">injection screened</Badge> : null}
                        </div>
                      </td>
                      <td className="td"><Badge tone="neutral">{titleCase(doc.doc_type)}</Badge></td>
                      <td className="td text-[12px]">v{version?.version_number ?? 1}{doc.version_count > 1 ? <span className="ml-1 text-slate-500">/ {doc.version_count}</span> : null}</td>
                      <td className="td text-[12px] tabular-nums">{doc.claim_count}</td>
                      <td className="td text-[12px]">
                        <div className={expired ? "text-red-300" : "text-slate-300"}>{version?.valid_until ? humanDate(version.valid_until) : "open ended"}</div>
                        <div className="text-[11px] text-slate-500">from {humanDate(version?.effective_date)}</div>
                      </td>
                      <td className="td text-[12px] text-slate-400">{relativeTime(version?.processed_at ?? doc.created_at)}</td>
                    </tr>
                  );
                })}
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
    </div>
  );
}

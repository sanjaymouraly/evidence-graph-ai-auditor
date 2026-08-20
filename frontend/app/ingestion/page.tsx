"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ApiError, api, detailText } from "@/lib/api";
import type { DocumentSummary, Paged } from "@/lib/types";
import { bytes, humanDateTime, relativeTime, titleCase } from "@/lib/format";
import { Badge, Card, ErrorBox, Field, Icon, PageHeader, SectionTitle } from "@/components/ui";

type UploadResult = {
  document_id: string;
  version_id: string;
  version_number: number;
  duplicate?: boolean;
  message?: string;
  content_hash?: string;
  processing?: { status?: string; claims?: number; spans?: number; warnings?: string[]; error?: string };
};

const DOC_TYPES = ["auto", "quote", "contract", "certificate", "invoice", "spec", "requirements", "schedule",
  "technical_assessment", "policy", "risk_register", "email", "decision_record", "spreadsheet", "report", "generic"];

const ACCEPT = ".pdf,.docx,.xlsx,.csv,.txt,.md,.json,.eml";

export default function IngestionPage() {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState("auto");
  const [docKey, setDocKey] = useState("");
  const [entity, setEntity] = useState("");
  const [effective, setEffective] = useState("");
  const [validUntil, setValidUntil] = useState("");
  const [trust, setTrust] = useState("0.7");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [recent, setRecent] = useState<DocumentSummary[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function loadRecent() {
    try {
      const page = await api<Paged<DocumentSummary>>("/api/documents?page_size=6");
      setRecent(page.items);
    } catch {
      /* recent list is non-critical */
    }
  }

  useEffect(() => {
    void loadRecent();
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!file) {
      setError("Choose a document to import.");
      return;
    }
    setBusy(true);
    setError(null);
    setResult(null);
    const form = new FormData();
    form.append("file", file);
    if (title) form.append("title", title);
    form.append("doc_type", docType);
    if (docKey) form.append("doc_key", docKey);
    if (entity) form.append("primary_entity", entity);
    if (effective) form.append("effective_date", effective);
    if (validUntil) form.append("valid_until", validUntil);
    form.append("trust_level", trust);
    try {
      const response = await api<UploadResult>("/api/documents", { method: "POST", form });
      setResult(response);
      setFile(null);
      setTitle("");
      setDocKey("");
      setEntity("");
      setEffective("");
      setValidUntil("");
      if (inputRef.current) inputRef.current.value = "";
      await loadRecent();
    } catch (exc) {
      setError(exc instanceof ApiError ? detailText(exc.detail) : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fade-in">
      <PageHeader
        title="Document ingestion"
        subtitle="Uploads are content-hashed, versioned and parsed into locatable spans. Identical content is detected and never silently overwritten."
        actions={<Link href="/documents" className="btn-ghost"><Icon name="document" className="h-3.5 w-3.5" /> Browse documents</Link>}
      />

      <div className="grid gap-4 lg:grid-cols-[1.25fr_1fr]">
        <Card>
          <SectionTitle title="Import a document" hint="PDF, DOCX, XLSX, CSV, TXT, Markdown, JSON or EML." />
          <form onSubmit={submit} className="space-y-4">
            <div
              className={`flex flex-col items-center justify-center rounded-xl border border-dashed px-6 py-8 text-center transition-colors ${
                dragging ? "border-accent/60 bg-accent/5" : "border-white/12 bg-white/[0.02]"
              }`}
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                const dropped = event.dataTransfer.files?.[0];
                if (dropped) setFile(dropped);
              }}
            >
              <Icon name="upload" className="mb-2 h-6 w-6 text-slate-400" />
              <p className="text-[13px] text-slate-200">{file ? file.name : "Drop a file here, or choose one"}</p>
              <p className="mt-1 text-[11.5px] text-slate-500">{file ? bytes(file.size) : ACCEPT.replaceAll(".", "").replaceAll(",", " · ")}</p>
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPT}
                className="hidden"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
              <button type="button" className="btn-ghost mt-4" onClick={() => inputRef.current?.click()}>
                Choose file
              </button>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Title (optional)">
                <input className="input" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Derived from filename when empty" />
              </Field>
              <Field label="Document type">
                <select className="input" value={docType} onChange={(event) => setDocType(event.target.value)}>
                  {DOC_TYPES.map((type) => <option key={type} value={type}>{titleCase(type === "auto" ? "auto-detect" : type)}</option>)}
                </select>
              </Field>
              <Field label="Document key" hint="Reuse a key to add a new version of the same source.">
                <input className="input" value={docKey} onChange={(event) => setDocKey(event.target.value)} placeholder="e.g. apex-quote" />
              </Field>
              <Field label="Primary entity">
                <input className="input" value={entity} onChange={(event) => setEntity(event.target.value)} placeholder="e.g. Apex Systems Pvt Ltd" />
              </Field>
              <Field label="Effective date">
                <input className="input" type="date" value={effective} onChange={(event) => setEffective(event.target.value)} />
              </Field>
              <Field label="Valid until">
                <input className="input" type="date" value={validUntil} onChange={(event) => setValidUntil(event.target.value)} />
              </Field>
              <Field label="Trust level" hint="0.0 – 1.0; used by data-quality checks.">
                <input className="input" type="number" min="0" max="1" step="0.05" value={trust} onChange={(event) => setTrust(event.target.value)} />
              </Field>
            </div>

            {error ? <ErrorBox message={error} /> : null}
            <div className="flex items-center gap-2">
              <button type="submit" className="btn-primary" disabled={busy || !file}>
                {busy ? "Ingesting & extracting…" : "Ingest document"}
              </button>
              {busy ? <span className="text-[11.5px] text-slate-500">Parsing spans, extracting claims and updating the graph.</span> : null}
            </div>
          </form>

          {result ? (
            <div className="mt-5 rounded-xl border border-ok/25 bg-ok/[0.06] p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-[13px] font-medium text-emerald-100">
                    {result.duplicate ? "Identical content already ingested" : `Ingested as version ${result.version_number}`}
                  </div>
                  <p className="mt-1 text-[11.5px] text-emerald-200/70">{result.message ?? "Document accepted and processed."}</p>
                </div>
                <Link href={`/documents/view?id=${result.document_id}`} className="btn-ghost shrink-0">Open viewer</Link>
              </div>
              <div className="mt-3 flex flex-wrap gap-2 text-[11.5px] text-slate-300">
                <Badge tone="ok">{result.processing?.claims ?? 0} claims</Badge>
                <Badge tone="info">{result.processing?.spans ?? 0} spans</Badge>
                <Badge tone="neutral">{result.processing?.status ?? (result.duplicate ? "duplicate" : "processed")}</Badge>
                {result.content_hash ? <span className="mono text-slate-500">sha256 {result.content_hash.slice(0, 16)}…</span> : null}
              </div>
              {result.processing?.warnings?.length ? (
                <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[11.5px] text-amber-200/80">
                  {result.processing.warnings.map((warning) => <li key={warning}>{warning}</li>)}
                </ul>
              ) : null}
              {result.processing?.error ? <p className="mt-2 text-[11.5px] text-red-300">{result.processing.error}</p> : null}
            </div>
          ) : null}
        </Card>

        <div className="space-y-4">
          <Card>
            <SectionTitle title="What happens on ingest" hint="The same pipeline runs for every source type." />
            <ol className="space-y-3">
              {[
                ["SHA-256 content hash", "Identical bytes are detected; the original version is preserved and returned."],
                ["Immutable version", "New content under an existing key becomes version N+1 and supersedes the previous version."],
                ["Typed spans", "Pages with bounding boxes, DOCX paragraphs, spreadsheet cells, email sections and character offsets."],
                ["Rule-based claims", "Numbers, currencies, dates and durations are normalized; every claim keeps its exact quote."],
                ["Prompt-injection screening", "Instruction-like text is flagged and excluded from extraction, never executed."],
                ["Graph + audit", "Entities, claims and relations are linked; the action is appended to the hash-chained audit log."],
              ].map(([titleText, body], index) => (
                <li key={titleText} className="flex gap-3">
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-white/10 bg-white/[0.04] text-[10.5px] text-slate-400">
                    {index + 1}
                  </span>
                  <div>
                    <div className="text-[12.5px] font-medium text-slate-200">{titleText}</div>
                    <div className="text-[11.5px] leading-relaxed text-slate-500">{body}</div>
                  </div>
                </li>
              ))}
            </ol>
          </Card>

          <Card>
            <SectionTitle title="Recently ingested" actions={<Link href="/documents" className="link text-[12px]">All</Link>} />
            <ul className="divide-y divide-white/[0.05]">
              {recent.map((doc) => (
                <li key={doc.id} className="flex items-center justify-between gap-3 py-2.5">
                  <div className="min-w-0">
                    <Link href={`/documents/view?id=${doc.id}`} className="block truncate text-[12.5px] text-slate-200 hover:text-accent">{doc.title}</Link>
                    <div className="text-[11px] text-slate-500">
                      {titleCase(doc.doc_type)} · v{doc.current_version?.version_number ?? 1} · {doc.claim_count} claims
                    </div>
                  </div>
                  <span className="shrink-0 text-[11px] text-slate-500">{relativeTime(doc.current_version?.processed_at ?? doc.created_at)}</span>
                </li>
              ))}
              {!recent.length ? <li className="py-3 text-[12px] text-slate-500">No documents yet.</li> : null}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}

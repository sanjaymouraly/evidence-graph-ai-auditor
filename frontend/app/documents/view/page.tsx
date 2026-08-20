"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api, download, fileObjectUrl } from "@/lib/api";
import type { DocumentDetail, Version } from "@/lib/types";
import { bytes, humanDate, humanDateTime, titleCase } from "@/lib/format";
import { useQueryParam } from "@/lib/useQuery";
import { Badge, Card, CopyButton, Empty, ErrorBox, Icon, Loading, PageHeader, StatusPill } from "@/components/ui";

type SpanClaim = { id: string; predicate: string; object: string; verification_status: string; citation_ok: boolean };
type SpanRow = {
  id: string;
  ordinal: number;
  locator_type: string;
  page?: number | null;
  paragraph_index?: number | null;
  sheet?: string | null;
  cell_range?: string | null;
  char_start?: number | null;
  char_end?: number | null;
  section_label?: string | null;
  text: string;
  bbox?: number[] | null;
  claims: SpanClaim[];
};

type SpanPayload = {
  document: { id: string; title: string; doc_type: string };
  version: Version;
  spans: SpanRow[];
  claim_count: number;
  sheets?: Record<string, unknown>;
};

export default function DocumentViewerPage() {
  const documentId = useQueryParam("id");
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [spans, setSpans] = useState<SpanPayload | null>(null);
  const [versionId, setVersionId] = useState<string | null>(null);
  const [activeSpan, setActiveSpan] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [viewMode, setViewMode] = useState<"parsed" | "original">("parsed");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  useEffect(() => {
    if (!documentId) return;
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const detail = await api<DocumentDetail>(`/api/documents/${documentId}`);
        if (cancelled) return;
        setDoc(detail);
        const current = detail.versions?.find((version) => version.is_current) ?? detail.versions?.[0];
        if (current) setVersionId(current.id);
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : "Failed to load document");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  useEffect(() => {
    if (!documentId || !versionId) return;
    let cancelled = false;
    api<SpanPayload>(`/api/documents/${documentId}/versions/${versionId}/spans`)
      .then((payload) => {
        if (!cancelled) {
          setSpans(payload);
          setActiveSpan(payload.spans[0]?.id ?? null);
        }
      })
      .catch((exc: Error) => {
        if (!cancelled) setError(exc.message);
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, versionId]);

  useEffect(() => {
    setViewMode("parsed");
  }, [versionId]);

  useEffect(() => {
    if (viewMode !== "original" || !doc || !versionId) return;
    const version = doc.versions?.find((item) => item.id === versionId);
    const mime = version?.mime_type ?? "";
    if (!mime.includes("pdf") && !mime.startsWith("image/")) return;
    let cancelled = false;
    let objectUrl: string | null = null;
    setPreviewError(null);
    setPreviewUrl(null);
    fileObjectUrl(`/api/documents/${doc.id}/versions/${versionId}/file`)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        objectUrl = url;
        setPreviewUrl(url);
      })
      .catch((exc: Error) => {
        if (!cancelled) setPreviewError(exc.message || "Preview unavailable");
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [viewMode, doc, versionId]);

  const selectedVersion = useMemo(() => doc?.versions?.find((version) => version.id === versionId) ?? null, [doc, versionId]);
  const previewMime = selectedVersion?.mime_type ?? "";
  const canPreview = previewMime.includes("pdf") || previewMime.startsWith("image/");
  const isImagePreview = previewMime.startsWith("image/");
  const active = spans?.spans.find((span) => span.id === activeSpan) ?? null;
  const allClaims = useMemo(() => (spans?.spans ?? []).flatMap((span) => span.claims.map((claim) => ({ ...claim, spanId: span.id, locator: span }))), [spans]);

  if (!documentId) {
    return <Empty title="No document selected" hint="Pick a document from the library." action={<Link href="/documents" className="btn-primary">Browse documents</Link>} />;
  }

  return (
    <div className="fade-in">
      <PageHeader
        title={doc?.title ?? "Document"}
        subtitle={doc ? `${titleCase(doc.doc_type)} · ${doc.doc_key} · ${doc.versions?.length ?? 1} version(s)` : undefined}
        actions={
          doc && selectedVersion ? (
            <>
              <button className="btn-ghost" onClick={() => download(`/api/documents/${doc.id}/versions/${selectedVersion.id}/file`, selectedVersion.filename)}>
                <Icon name="download" className="h-3.5 w-3.5" /> Original file
              </button>
              <Link href="/documents" className="btn-ghost">Back</Link>
            </>
          ) : null
        }
      />

      {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}
      {loading ? <Loading rows={8} /> : null}

      {doc ? (
        <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)_340px]">
          <div className="space-y-4">
            <Card>
              <div className="label mb-2">Source</div>
              <div className="space-y-2 text-[12.5px]">
                <div className="flex justify-between gap-2"><span className="text-slate-500">Type</span><Badge tone="neutral">{titleCase(doc.doc_type)}</Badge></div>
                <div className="flex justify-between gap-2"><span className="text-slate-500">Key</span><span className="mono text-slate-300">{doc.doc_key}</span></div>
                <div className="flex justify-between gap-2"><span className="text-slate-500">Versions</span><span className="text-slate-300">{doc.versions?.length ?? 0}</span></div>
                <div className="flex justify-between gap-2"><span className="text-slate-500">Claims</span><span className="text-slate-300">{spans?.claim_count ?? doc.claim_count ?? 0}</span></div>
                <div className="flex justify-between gap-2"><span className="text-slate-500">Trust</span><span className="text-slate-300">{selectedVersion?.trust_level ?? "—"}</span></div>
              </div>
              <div className="mt-3 border-t border-white/[0.06] pt-3">
                <div className="label mb-1.5">Versions</div>
                <ul className="space-y-1">
                  {(doc.versions ?? []).map((version) => (
                    <li key={version.id}>
                      <button
                        type="button"
                        onClick={() => setVersionId(version.id)}
                        className={`w-full rounded-md px-2 py-1.5 text-left text-[12px] ${version.id === versionId ? "bg-accent/10 text-amber-100" : "text-slate-400 hover:bg-white/[0.04]"}`}
                      >
                        <span className="flex items-center justify-between gap-2">
                          <span>v{version.version_number} · {version.filename}</span>
                          {version.is_current ? <Badge tone="ok">current</Badge> : null}
                        </span>
                        <span className="mt-0.5 block text-[10.5px] text-slate-500">
                          {humanDate(version.effective_date)} → {version.valid_until ? humanDate(version.valid_until) : "open"} · {bytes(version.size_bytes)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
              {selectedVersion?.content_hash ? (
                <div className="mt-3 border-t border-white/[0.06] pt-3">
                  <div className="label mb-1">Content hash</div>
                  <div className="flex items-start justify-between gap-2">
                    <code className="mono break-all text-slate-400">{selectedVersion.content_hash}</code>
                    <CopyButton value={selectedVersion.content_hash} label="Copy" />
                  </div>
                </div>
              ) : null}
              {selectedVersion?.injection_flags?.length ? (
                <div className="mt-3 rounded-lg border border-bad/30 bg-bad/10 p-2.5">
                  <div className="flex items-center gap-1.5 text-[11.5px] font-medium text-red-200"><Icon name="alert" className="h-3.5 w-3.5" /> Prompt-injection screened</div>
                  {selectedVersion.injection_flags.map((flag, index) => (
                    <div key={index} className="mt-1.5 text-[11px] leading-relaxed text-red-200/80">
                      <div className="font-medium">Span {flag.span}: {flag.flags.join(", ")}</div>
                      {flag.excerpt ? <p className="mt-0.5 line-clamp-3 italic">“{flag.excerpt}”</p> : null}
                    </div>
                  ))}
                </div>
              ) : null}
              {selectedVersion?.warnings?.length ? (
                <ul className="mt-3 space-y-1 text-[11px] text-amber-200/80">
                  {selectedVersion.warnings.map((warning) => <li key={warning}>• {warning}</li>)}
                </ul>
              ) : null}
            </Card>

            <Card>
              <div className="label mb-2">Ingestion</div>
              <div className="space-y-1.5 text-[11.5px] text-slate-400">
                <div className="flex justify-between"><span>Status</span><StatusPill status={selectedVersion?.status} /></div>
                <div className="flex justify-between"><span>Uploaded</span><span>{humanDateTime(selectedVersion?.uploaded_at)}</span></div>
                <div className="flex justify-between"><span>Processed</span><span>{humanDateTime(selectedVersion?.processed_at)}</span></div>
                <div className="flex justify-between"><span>Locator types</span><span>{Array.from(new Set((spans?.spans ?? []).map((span) => span.locator_type))).join(", ") || "—"}</span></div>
              </div>
            </Card>
          </div>

          <Card pad={false} className="overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.06] px-4 py-2.5">
              <div className="text-[12.5px] font-medium text-slate-200">{viewMode === "original" ? "Original file" : "Parsed content"}</div>
              <div className="flex items-center gap-3">
                {canPreview ? (
                  <div className="flex rounded-md border border-white/[0.08] p-0.5">
                    <button
                      type="button"
                      onClick={() => setViewMode("parsed")}
                      className={`rounded px-2 py-0.5 text-[11px] ${viewMode === "parsed" ? "bg-white/[0.08] text-slate-200" : "text-slate-500 hover:text-slate-300"}`}
                    >
                      Parsed
                    </button>
                    <button
                      type="button"
                      onClick={() => setViewMode("original")}
                      className={`rounded px-2 py-0.5 text-[11px] ${viewMode === "original" ? "bg-white/[0.08] text-slate-200" : "text-slate-500 hover:text-slate-300"}`}
                    >
                      Original
                    </button>
                  </div>
                ) : null}
                <div className="text-[11px] text-slate-500">
                  {viewMode === "original" ? "Authenticated preview of the stored source file" : `${spans?.spans.length ?? 0} spans · click a span to inspect claims`}
                </div>
              </div>
            </div>
            <div className="max-h-[calc(100vh-230px)] overflow-y-auto p-4">
              {viewMode === "original" ? (
                previewError ? (
                  <ErrorBox message={`Preview unavailable: ${previewError}. Use “Original file” to download it.`} />
                ) : !previewUrl ? (
                  <Loading rows={6} />
                ) : isImagePreview ? (
                  <img src={previewUrl} alt={selectedVersion?.filename ?? "Source image"} className="mx-auto max-h-[calc(100vh-310px)] rounded-lg border border-white/[0.08]" />
                ) : (
                  <iframe
                    src={previewUrl}
                    title={`Original PDF: ${selectedVersion?.filename ?? "source"}`}
                    className="h-[calc(100vh-310px)] min-h-[520px] w-full rounded-lg border border-white/[0.08] bg-white"
                  />
                )
              ) : !spans ? <Loading rows={6} /> : spans.spans.length === 0 ? <Empty title="No spans extracted" hint="The parser produced no text for this version." /> : (
                <div className="space-y-1.5">
                  {spans.spans.map((span) => {
                    const selected = span.id === activeSpan;
                    return (
                      <button
                        key={span.id}
                        type="button"
                        onClick={() => setActiveSpan(span.id)}
                        className={`group block w-full rounded-lg border px-3 py-2 text-left transition-colors ${
                          selected ? "border-accent/40 bg-accent/[0.07]" : "border-transparent hover:border-white/10 hover:bg-white/[0.03]"
                        }`}
                      >
                        <div className="mb-1 flex items-center justify-between gap-2">
                          <span className="mono text-[10.5px] uppercase tracking-wide text-slate-500">
                            {span.page ? `page ${span.page}` : span.sheet ? `${span.sheet} ${span.cell_range ?? ""}` : span.paragraph_index !== null && span.paragraph_index !== undefined ? `¶ ${span.paragraph_index + 1}` : span.section_label ?? "span"}
                          </span>
                          {span.claims.length ? <span className="rounded bg-white/[0.06] px-1.5 py-0.5 text-[10px] text-slate-400">{span.claims.length} claim{span.claims.length === 1 ? "" : "s"}</span> : null}
                        </div>
                        <p className={`whitespace-pre-wrap text-[12.5px] leading-relaxed ${selected ? "text-slate-100" : "text-slate-300"}`}>
                          {span.text.length > 900 ? `${span.text.slice(0, 900)}…` : span.text}
                        </p>
                        {span.claims.length ? (
                          <div className="mt-1.5 flex flex-wrap gap-1">
                            {span.claims.map((claim) => (
                              <span key={claim.id} className={`rounded border px-1.5 py-0.5 text-[10px] ${
                                claim.citation_ok ? "border-ok/25 bg-ok/10 text-emerald-200" : "border-bad/30 bg-bad/10 text-red-200"
                              }`}>
                                {claim.predicate}: {String(claim.object).slice(0, 34)}
                              </span>
                            ))}
                          </div>
                        ) : null}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </Card>

          <div className="space-y-4">
            <Card>
              <div className="label mb-2">Claims in this span</div>
              {!active ? <p className="text-[12px] text-slate-500">Select a span.</p> : active.claims.length === 0 ? (
                <p className="text-[12px] text-slate-500">No claims extracted from this span.</p>
              ) : (
                <ul className="space-y-2">
                  {active.claims.map((claim) => (
                    <li key={claim.id} className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-2.5">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[11.5px] font-medium text-slate-200">{titleCase(claim.predicate)}</span>
                        <Badge tone={claim.citation_ok ? "ok" : "bad"}>{claim.citation_ok ? "quote verified" : "quote unresolved"}</Badge>
                      </div>
                      <p className="mt-1 text-[12px] text-slate-300">{claim.object}</p>
                      <div className="mt-1 flex items-center justify-between">
                        <StatusPill status={claim.verification_status} />
                        <Link className="link text-[11px]" href={`/claims?q=${encodeURIComponent(claim.object)}`}>Inspect</Link>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card>
              <div className="flex items-center justify-between">
                <div className="label">All claims ({allClaims.length})</div>
                <Link href={`/claims?document_id=${doc.id}`} className="link text-[11px]">Open in claims</Link>
              </div>
              <ul className="mt-2 max-h-[320px] space-y-1.5 overflow-y-auto pr-1">
                {allClaims.map((claim) => (
                  <li key={claim.id}>
                    <button
                      type="button"
                      onClick={() => setActiveSpan(claim.spanId)}
                      className="flex w-full items-start justify-between gap-2 rounded-md px-1.5 py-1 text-left hover:bg-white/[0.04]"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-[11.5px] text-slate-300">{claim.object}</span>
                        <span className="block text-[10.5px] text-slate-500">{titleCase(claim.predicate)}</span>
                      </span>
                      <Badge tone={claim.citation_ok ? "ok" : "bad"}>{claim.citation_ok ? "✓" : "!"}</Badge>
                    </button>
                  </li>
                ))}
                {!allClaims.length ? <li className="text-[12px] text-slate-500">No claims.</li> : null}
              </ul>
            </Card>
          </div>
        </div>
      ) : null}
    </div>
  );
}

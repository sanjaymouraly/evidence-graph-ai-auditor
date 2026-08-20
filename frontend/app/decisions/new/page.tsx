"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, api, detailText } from "@/lib/api";
import type { DecisionDetail, DocumentSummary, Paged } from "@/lib/types";
import { humanDate, titleCase } from "@/lib/format";
import { useAuth, can } from "@/lib/auth";
import { Badge, Card, ErrorBox, Field, Icon, PageHeader, useDebounced } from "@/components/ui";

const TYPES = [
  { code: "procurement", label: "Supplier selection", hint: "Quotes, contracts, certificates and risk registers for competing suppliers." },
  { code: "engineering_change", label: "Engineering change", hint: "Change specification, technical assessment, validation and rollback evidence." },
  { code: "project_approval", label: "Project / milestone approval", hint: "Schedule, status, budget and project risk register for a gate release." },
  { code: "policy_compliance", label: "Policy compliance", hint: "Policy text, attestations and review records for a compliance determination." },
];

export default function NewDecisionPage() {
  const router = useRouter();
  const { user } = useAuth();
  const [title, setTitle] = useState("");
  const [decisionType, setDecisionType] = useState("procurement");
  const [description, setDescription] = useState("");
  const [asOf, setAsOf] = useState(new Date().toISOString().slice(0, 10));
  const [dueDate, setDueDate] = useState("");
  const [options, setOptions] = useState<string[]>(["", ""]);
  const [selectedDocs, setSelectedDocs] = useState<string[]>([]);
  const [docQuery, setDocQuery] = useState("");
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const debounced = useDebounced(docQuery, 280);

  useEffect(() => {
    const params = new URLSearchParams({ page_size: "60" });
    if (debounced) params.set("q", debounced);
    api<Paged<DocumentSummary>>(`/api/documents?${params.toString()}`)
      .then((page) => setDocuments(page.items))
      .catch(() => setDocuments([]));
  }, [debounced]);

  const canCreate = can(user, "decision:write");
  const cleanedOptions = useMemo(() => options.map((option) => option.trim()).filter(Boolean), [options]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api<{ id: string }>("/api/decisions", {
        method: "POST",
        body: {
          title: title.trim(),
          decision_type: decisionType,
          description: description.trim(),
          options: cleanedOptions,
          as_of_date: asOf || null,
          due_date: dueDate || null,
          document_ids: selectedDocs,
        },
      });
      await api<DecisionDetail>(`/api/decisions/${created.id}/evaluate`, { method: "POST", body: {} }).catch(() => null);
      router.push(`/decisions/view?id=${created.id}`);
    } catch (exc) {
      setError(exc instanceof ApiError ? detailText(exc.detail) : "Could not create decision");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fade-in">
      <PageHeader
        title="New decision case"
        subtitle="The case binds evidence documents to a gate policy. On creation the gate is evaluated immediately so gaps are visible before review."
        actions={<Link href="/decisions" className="btn-ghost">Cancel</Link>}
      />

      {!canCreate ? <div className="mb-4"><ErrorBox message="Your role cannot create decisions. Ask an administrator for auditor or reviewer access." /></div> : null}

      <form onSubmit={submit} className="grid gap-4 lg:grid-cols-[1.35fr_1fr]">
        <div className="space-y-4">
          <Card>
            <div className="label mb-3">Case definition</div>
            <div className="space-y-3">
              <Field label="Title">
                <input className="input" required minLength={3} value={title} onChange={(event) => setTitle(event.target.value)} placeholder="e.g. Approve Northstar Compressors as compressor supplier — Chennai Phase 2" />
              </Field>
              <Field label="Description" hint="State the decision, the options under consideration and why evidence is required.">
                <textarea className="input min-h-[86px]" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Select a supplier for 25 air compressors…" />
              </Field>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="As-of date" hint="Evidence validity is evaluated against this date.">
                  <input className="input" type="date" value={asOf} onChange={(event) => setAsOf(event.target.value)} />
                </Field>
                <Field label="Due date (optional)">
                  <input className="input" type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} />
                </Field>
              </div>
            </div>
          </Card>

          <Card>
            <div className="label mb-3">Decision type</div>
            <div className="grid gap-2 sm:grid-cols-2">
              {TYPES.map((type) => (
                <button
                  key={type.code}
                  type="button"
                  onClick={() => setDecisionType(type.code)}
                  className={`rounded-lg border p-3 text-left transition-colors ${
                    decisionType === type.code ? "border-accent/45 bg-accent/[0.07]" : "border-white/[0.07] bg-white/[0.02] hover:border-white/15"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[12.5px] font-medium text-slate-100">{type.label}</span>
                    {decisionType === type.code ? <Icon name="check" className="h-3.5 w-3.5 text-accent" /> : null}
                  </div>
                  <p className="mt-1 text-[11.5px] leading-relaxed text-slate-500">{type.hint}</p>
                </button>
              ))}
            </div>
          </Card>

          <Card>
            <div className="mb-3 flex items-center justify-between">
              <div>
                <div className="label">Options considered</div>
                <p className="mt-1 text-[11.5px] text-slate-500">Procurement cases need at least two options; the gate checks coverage per option.</p>
              </div>
              <button type="button" className="btn-ghost px-2 py-1 text-[12px]" onClick={() => setOptions((previous) => [...previous, ""])}>
                <Icon name="plus" className="h-3.5 w-3.5" /> Add option
              </button>
            </div>
            <div className="space-y-2">
              {options.map((option, index) => (
                <div key={index} className="flex items-center gap-2">
                  <span className="w-6 text-[11px] text-slate-500">{index + 1}.</span>
                  <input
                    className="input"
                    value={option}
                    onChange={(event) => setOptions((previous) => previous.map((item, position) => (position === index ? event.target.value : item)))}
                    placeholder={index === 0 ? "Apex Systems Pvt Ltd" : "Borealis Industrial GmbH"}
                  />
                  <button
                    type="button"
                    className="rounded p-1.5 text-slate-500 hover:bg-white/[0.06] hover:text-slate-300"
                    onClick={() => setOptions((previous) => (previous.length > 1 ? previous.filter((_, position) => position !== index) : previous))}
                    aria-label="Remove option"
                  >
                    <Icon name="x" className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          </Card>
        </div>

        <div className="space-y-4">
          <Card pad={false} className="flex h-full flex-col">
            <div className="border-b border-white/[0.06] px-4 py-3">
              <div className="label">Evidence documents</div>
              <div className="relative mt-2">
                <Icon name="search" className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-500" />
                <input className="input pl-9" placeholder="Search the library…" value={docQuery} onChange={(event) => setDocQuery(event.target.value)} />
              </div>
              <p className="mt-2 text-[11.5px] text-slate-500">{selectedDocs.length} selected</p>
            </div>
            <ul className="max-h-[420px] flex-1 divide-y divide-white/[0.05] overflow-y-auto">
              {documents.map((doc) => {
                const checked = selectedDocs.includes(doc.id);
                return (
                  <li key={doc.id}>
                    <label className="flex cursor-pointer items-start gap-3 px-4 py-2.5 hover:bg-white/[0.025]">
                      <input
                        type="checkbox"
                        className="mt-0.5 h-3.5 w-3.5 rounded border-white/20 bg-ink-900"
                        checked={checked}
                        onChange={(event) => setSelectedDocs((previous) => (event.target.checked ? [...previous, doc.id] : previous.filter((id) => id !== doc.id)))}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[12.5px] text-slate-200">{doc.title}</span>
                        <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10.5px] text-slate-500">
                          <Badge tone="neutral">{titleCase(doc.doc_type)}</Badge>
                          {doc.current_version?.valid_until ? <span>valid to {humanDate(doc.current_version.valid_until)}</span> : null}
                          <span>· {doc.claim_count} claims</span>
                        </span>
                      </span>
                    </label>
                  </li>
                );
              })}
              {!documents.length ? <li className="px-4 py-6 text-center text-[12px] text-slate-500">No documents found.</li> : null}
            </ul>
            <div className="border-t border-white/[0.06] p-4">
              {error ? <div className="mb-3"><ErrorBox message={error} /></div> : null}
              <button type="submit" className="btn-primary w-full" disabled={busy || !canCreate || title.trim().length < 3}>
                {busy ? "Creating & evaluating…" : "Create case and evaluate gate"}
              </button>
              <p className="mt-2 text-[10.5px] leading-relaxed text-slate-500">
                The gate re-runs on every evaluation and after any evidence change. Approval is only possible from a
                SUFFICIENTLY_SUPPORTED gate result by a reviewer who is not the case creator.
              </p>
            </div>
          </Card>
        </div>
      </form>
    </div>
  );
}

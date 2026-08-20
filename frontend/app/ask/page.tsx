"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ApiError, api, detailText } from "@/lib/api";
import type { DecisionSummary, GroundedAnswer, Paged } from "@/lib/types";
import { relativeTime, titleCase } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, Icon, PageHeader, Toggle } from "@/components/ui";

type Turn = { id: number; question: string; answer: GroundedAnswer | null; error?: string; at: string };

const SUGGESTIONS = [
  "What are the payment terms for each supplier?",
  "Which certificates have expired before the decision date?",
  "Summarise the delivery commitments in the attached contracts.",
  "Is there a contradiction on compressor price?",
];

export default function AskPage() {
  const [question, setQuestion] = useState("");
  const [decisionId, setDecisionId] = useState("");
  const [decisions, setDecisions] = useState<DecisionSummary[]>([]);
  const [useLlm, setUseLlm] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [provider, setProvider] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<Paged<DecisionSummary>>("/api/decisions?page_size=50").then((page) => setDecisions(page.items)).catch(() => setDecisions([]));
    api<{ items: { enabled: boolean; is_default_generation?: boolean; name: string }[] }>("/api/providers")
      .then((payload) => {
        const active = payload.items.find((item) => item.enabled && item.is_default_generation) ?? payload.items.find((item) => item.enabled);
        setProvider(active?.name ?? null);
      })
      .catch(() => setProvider(null));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  async function ask(text: string) {
    const trimmed = text.trim();
    if (trimmed.length < 3 || busy) return;
    setBusy(true);
    setQuestion("");
    const id = Date.now();
    const pending: Turn = { id, question: trimmed, answer: null, at: new Date().toISOString() };
    setTurns((previous) => [...previous, pending]);
    try {
      const response = await api<GroundedAnswer>("/api/analysis/ask", {
        method: "POST",
        body: { question: trimmed, decision_id: decisionId || null, use_llm: useLlm, top_k: 8 },
      });
      setTurns((previous) => previous.map((turn) => (turn.id === id ? { ...turn, answer: response } : turn)));
    } catch (exc) {
      setTurns((previous) => previous.map((turn) => (turn.id === id ? { ...turn, error: exc instanceof ApiError ? detailText(exc.detail) : "Request failed" } : turn)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fade-in">
      <PageHeader
        title="Grounded Q&A"
        subtitle="Retrieval-first answers over stored claims only. Every statement cites a claim; when evidence is missing the answer says so instead of guessing."
        actions={<Badge tone={provider ? "ok" : "neutral"}>{provider ? `generation: ${provider}` : "offline retrieval mode"}</Badge>}
      />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
        <Card pad={false} className="flex min-h-[560px] flex-col">
          <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.06] px-4 py-3">
            <select className="input w-auto min-w-[240px]" value={decisionId} onChange={(event) => setDecisionId(event.target.value)}>
              <option value="">Whole workspace</option>
              {decisions.map((decision) => <option key={decision.id} value={decision.id}>{decision.title}</option>)}
            </select>
            <Toggle checked={useLlm} onChange={setUseLlm} label="Use generation provider when available" />
          </div>

          <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
            {turns.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center py-16 text-center">
                <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full border border-white/10 bg-white/[0.03] text-slate-400">
                  <Icon name="ask" className="h-5 w-5" />
                </div>
                <p className="text-sm font-medium text-slate-200">Ask a question about the evidence</p>
                <p className="mt-1 max-w-md text-[12.5px] leading-relaxed text-slate-500">
                  Answers are grounded in claims extracted from your documents. Contradictions are surfaced, never hidden.
                </p>
                <div className="mt-5 grid w-full max-w-lg gap-2">
                  {SUGGESTIONS.map((suggestion) => (
                    <button key={suggestion} className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2 text-left text-[12.5px] text-slate-300 hover:border-accent/30 hover:bg-accent/5" onClick={() => void ask(suggestion)}>
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}

            {turns.map((turn) => (
              <div key={turn.id} className="space-y-2">
                <div className="flex justify-end">
                  <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-accent/15 px-3.5 py-2 text-[12.5px] text-amber-100">{turn.question}</div>
                </div>
                {turn.error ? (
                  <ErrorBox message={turn.error} />
                ) : !turn.answer ? (
                  <div className="max-w-[85%] rounded-2xl rounded-bl-sm border border-white/[0.07] bg-white/[0.02] px-3.5 py-2.5">
                    <div className="flex items-center gap-2 text-[12px] text-slate-400">
                      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" /> Retrieving evidence and validating citations…
                    </div>
                  </div>
                ) : (
                  <div className="max-w-[92%] rounded-2xl rounded-bl-sm border border-white/[0.07] bg-white/[0.02] px-3.5 py-3">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <Badge tone={turn.answer.insufficient_evidence ? "warn" : "ok"}>
                        {turn.answer.insufficient_evidence ? "insufficient evidence" : "evidence-grounded"}
                      </Badge>
                      <span className="text-[10.5px] text-slate-500">{turn.answer.search_method}</span>
                      {turn.answer.provider ? <span className="text-[10.5px] text-slate-500">· {turn.answer.provider}</span> : null}
                    </div>
                    <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-slate-200">{turn.answer.answer}</p>
                    {turn.answer.citations?.length ? (
                      <ol className="mt-3 space-y-1.5">
                        {turn.answer.citations.map((citation, index) => (
                          <li key={`${turn.id}-${citation.claim_id}`} className="rounded-lg border border-white/[0.06] bg-ink-900/50 p-2">
                            <div className="flex items-center justify-between gap-2 text-[11px]">
                              <span className="text-slate-300">[{index + 1}] {citation.document_title}</span>
                              <span className="text-slate-500">{citation.locator}</span>
                            </div>
                            <div className="mt-0.5 text-[11px] italic text-slate-500">“{citation.quote}”</div>
                            <div className="mt-1 flex items-center gap-2 text-[10px] text-slate-600">
                              <span>{titleCase(citation.predicate)}</span>
                              <span>· {citation.verification_status}</span>
                              <Link href={`/claims?q=${encodeURIComponent(citation.value.slice(0, 30))}`} className="link">open claim</Link>
                            </div>
                          </li>
                        ))}
                      </ol>
                    ) : null}
                    {turn.answer.invalid_citations?.length ? (
                      <p className="mt-2 text-[11px] text-red-300">
                        {turn.answer.invalid_citations.length} citation(s) failed resolution and were excluded.
                      </p>
                    ) : null}
                  </div>
                )}
              </div>
            ))}
            <div ref={endRef} />
          </div>

          <form
            className="flex items-center gap-2 border-t border-white/[0.06] px-4 py-3"
            onSubmit={(event) => {
              event.preventDefault();
              void ask(question);
            }}
          >
            <input
              className="input"
              placeholder="Ask about price, delivery, certifications, milestones…"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              disabled={busy}
            />
            <button type="submit" className="btn-primary shrink-0" disabled={busy || question.trim().length < 3}>
              {busy ? "Working…" : "Send"}
            </button>
          </form>
        </Card>

        <div className="space-y-4">
          <Card>
            <div className="label mb-2">Answer policy</div>
            <ul className="space-y-2 text-[11.5px] leading-relaxed text-slate-400">
              <li>• Retrieval runs over claims, not raw text, so answers inherit provenance.</li>
              <li>• A statement without a resolvable citation is excluded and shown as such.</li>
              <li>• If nothing exceeds the relevance floor, the answer is “insufficient evidence”.</li>
              <li>• The model may not assert approvals or gate outcomes.</li>
            </ul>
          </Card>
          <Card>
            <div className="label mb-2">Scope</div>
            {decisionId ? (
              <div className="text-[11.5px] text-slate-400">
                Restricted to: <span className="text-slate-200">{decisions.find((item) => item.id === decisionId)?.title}</span>
              </div>
            ) : (
              <div className="text-[11.5px] text-slate-400">Searching all current evidence in the workspace.</div>
            )}
            <p className="mt-2 text-[10.5px] text-slate-500">Superseded versions and rejected claims are excluded from retrieval.</p>
          </Card>
          {turns.length ? (
            <button className="btn-ghost w-full" onClick={() => setTurns([])}>Clear conversation</button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

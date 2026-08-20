"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, api, detailText } from "@/lib/api";
import type { BenchmarkResult, BenchmarkRun } from "@/lib/types";
import { humanDateTime, percent, relativeTime, titleCase } from "@/lib/format";
import { can, useAuth } from "@/lib/auth";
import { Badge, Card, Empty, ErrorBox, Icon, Loading, PageHeader, SectionTitle, Stat, Tabs } from "@/components/ui";

type RunList = { items: BenchmarkRun[]; total: number; targets: Record<string, number> };

function metricName(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export default function BenchmarksPage() {
  const { user } = useAuth();
  const [runs, setRuns] = useState<RunList | null>(null);
  const [selected, setSelected] = useState<BenchmarkResult | null>(null);
  const [tab, setTab] = useState("dev");
  const [dataset, setDataset] = useState("dev");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await api<RunList>("/api/benchmarks");
      setRuns(list);
      if (list.items.length) {
        const latest = await api<BenchmarkResult>(`/api/benchmarks/${list.items[0].id}`);
        setSelected(latest);
        setDataset(latest.dataset);
        setTab("metrics");
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to load benchmarks");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function runBenchmark() {
    setRunning(true);
    setError(null);
    setNote(null);
    try {
      const result = await api<BenchmarkResult>("/api/benchmarks/run", { method: "POST", body: { dataset, include_llm: false } });
      setSelected(result);
      setDataset(result.dataset);
      setTab("metrics");
      setNote(`Run completed in ${result.duration_seconds.toFixed(2)}s with ${result.errors.length} error(s).`);
      await load();
    } catch (exc) {
      setError(exc instanceof ApiError ? detailText(exc.detail) : "Benchmark failed to run");
    } finally {
      setRunning(false);
    }
  }

  const metrics = selected?.metrics ?? {};
  const targets = selected?.targets ?? {};
  const numericMetrics = Object.entries(metrics).filter(([, value]) => isNumber(value)) as [string, number][];
  const gateConfusion = (selected?.confusion?.gate ?? {}) as Record<string, Record<string, number>>;
  const expectedLabels = Array.from(new Set([...Object.keys(gateConfusion), "CONTRADICTED", "EVIDENCE_INCOMPLETE", "REVIEW_REQUIRED", "SUFFICIENTLY_SUPPORTED"])).sort();
  const actualLabels = Array.from(new Set([...Object.values(gateConfusion).flatMap((row) => Object.keys(row)), ...expectedLabels])).sort();
  const decisions = (selected?.details?.decisions ?? []) as { title: string; expected?: string; actual?: string; pass?: boolean; gate_match?: boolean; final_match?: boolean }[];
  const analysisDetails = (selected?.details?.analysis ?? []) as { decision: string; statements: number; unsupported: number }[];
  const retrievalDetails = (selected?.details?.retrieval ?? []) as { decision: string; recall_at_5: number; mrr: number; retrieved_doc_keys: string[]; relevant_doc_keys: string[] }[];
  const claimDetails = (selected?.details?.claims ?? {}) as { missing_labels?: unknown[]; unexpected_claims?: unknown[]; matched?: number; in_scope?: number };

  return (
    <div className="fade-in">
      <PageHeader
        title="Benchmark dashboard"
        subtitle="Synthetic labelled corpora with a separate dev and held-out split. Runs execute against a throwaway database, so application data is untouched."
        actions={
          <>
            <span className="text-[11px] uppercase tracking-[0.08em] text-slate-500">Next run</span>
            <select
              className="input w-auto"
              aria-label="Dataset for the next benchmark run"
              value={dataset}
              onChange={(event) => setDataset(event.target.value)}
            >
              <option value="dev">dev split</option>
              <option value="heldout">held-out split</option>
            </select>
            <button className="btn-primary" onClick={() => void runBenchmark()} disabled={running || !can(user, "benchmark:run")}>
              <Icon name="chart" className="h-3.5 w-3.5" /> {running ? "Running…" : "Run benchmark"}
            </button>
          </>
        }
      />

      {error ? <div className="mb-4"><ErrorBox message={error} onRetry={load} /></div> : null}
      {note ? <div className="mb-4 rounded-lg border border-ok/25 bg-ok/[0.07] px-4 py-2.5 text-[12.5px] text-emerald-200">{note}</div> : null}
      {loading && !selected ? <Loading rows={8} /> : null}

      {selected ? (
        <>
          <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {Object.entries(targets).map(([name, target]) => (
              <Card key={name}>
                <div className="flex items-center justify-between">
                  <span className="label">{metricName(name)}</span>
                  <Badge tone={target.pass ? "ok" : "bad"}>{target.pass ? "target met" : "below target"}</Badge>
                </div>
                <div className="mt-2 text-xl font-semibold text-slate-50">{target.actual === null ? "—" : percent(target.actual)}</div>
                <div className="mt-1 text-[11px] text-slate-500">target ≥ {percent(target.target, 0)}</div>
              </Card>
            ))}
            <Card>
              <div className="flex items-center justify-between">
                <span className="label">Run</span>
                <Badge tone={selected.errors.length ? "warn" : "ok"}>{selected.errors.length ? `${selected.errors.length} errors` : "clean"}</Badge>
              </div>
              <div className="mt-2 text-xl font-semibold text-slate-50">{selected.duration_seconds.toFixed(2)}s</div>
              <div className="mt-1 text-[11px] text-slate-500">{selected.dataset} · {humanDateTime(selected.started_at)}</div>
            </Card>
          </div>

          <div className="mb-4">
            <Tabs
              tabs={[
                { id: "metrics", label: "Metrics" },
                { id: "confusion", label: "Confusion" },
                { id: "cases", label: "Cases", count: decisions.length },
                { id: "retrieval", label: "Retrieval" },
                { id: "history", label: "History", count: runs?.items.length ?? 0 },
              ]}
              active={tab}
              onChange={setTab}
            />
          </div>

          {tab === "metrics" ? (
            <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
              <Card>
                <SectionTitle title="All metrics" hint="Computed from stored claims, contradictions and gate results against the labelled ground truth." />
                <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
                  {numericMetrics.map(([name, value]) => (
                    <div key={name} className="flex items-center justify-between border-b border-white/[0.05] py-1.5">
                      <span className="text-[12px] text-slate-400">{metricName(name)}</span>
                      <span className="tabular-nums text-[12.5px] text-slate-100">
                        {name.startsWith("retrieval") || name.includes("rate") || name.includes("accuracy") || name.includes("precision") || name.includes("recall") || name.includes("f1") || name.includes("validity") || name.includes("containment")
                          ? percent(value)
                          : value.toFixed(3)}
                      </span>
                    </div>
                  ))}
                </div>
              </Card>
              <div className="space-y-4">
                <Card>
                  <SectionTitle title="Corpus" />
                  <ul className="space-y-1.5">
                    {Object.entries((metrics.counts as Record<string, number>) ?? {}).map(([key, value]) => (
                      <li key={key} className="flex items-center justify-between text-[12px]">
                        <span className="text-slate-400">{metricName(key)}</span>
                        <span className="tabular-nums text-slate-200">{value}</span>
                      </li>
                    ))}
                  </ul>
                </Card>
                <Card>
                  <SectionTitle title="Label gaps" hint="Unmatched labels and unexpected extractions — reported, never hidden." />
                  <div className="text-[12px] text-slate-400">
                    <div>Missing labels: {claimDetails.missing_labels?.length ?? 0}</div>
                    <div>Unexpected extractions: {claimDetails.unexpected_claims?.length ?? 0}</div>
                  </div>
                  {claimDetails.missing_labels?.length ? (
                    <pre className="mono mt-2 max-h-40 overflow-auto rounded-md bg-ink-950/60 p-2 text-[10.5px] text-slate-400">{JSON.stringify(claimDetails.missing_labels, null, 1)}</pre>
                  ) : null}
                </Card>
              </div>
            </div>
          ) : null}

          {tab === "confusion" ? (
            <Card pad={false}>
              <div className="px-5 pt-4"><SectionTitle title="Gate confusion matrix" hint="Expected (rows) vs actual (columns) gate status per decision." /></div>
              <div className="overflow-x-auto px-5 pb-5">
                <table className="mt-2 w-full text-[11.5px]">
                  <thead>
                    <tr>
                      <th className="th">expected ↓ / actual →</th>
                      {actualLabels.map((label) => <th key={label} className="th text-center">{titleCase(label)}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {expectedLabels.map((expected) => (
                      <tr key={expected} className="border-t border-white/[0.05]">
                        <td className="td whitespace-nowrap text-slate-300">{titleCase(expected)}</td>
                        {actualLabels.map((actual) => {
                          const value = gateConfusion[expected]?.[actual] ?? 0;
                          return (
                            <td key={actual} className={`td text-center tabular-nums ${expected === actual && value ? "text-emerald-300" : value ? "text-red-300" : "text-slate-600"}`}>
                              {value || "·"}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ) : null}

          {tab === "cases" ? (
            <div className="grid gap-4 xl:grid-cols-[1.2fr_1fr]">
              <Card pad={false}>
                <div className="px-5 pt-4"><SectionTitle title="Per-decision outcomes" /></div>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[520px]">
                    <thead><tr><th className="th">Decision</th><th className="th">Expected</th><th className="th">Actual</th></tr></thead>
                    <tbody>
                      {decisions.map((decision) => (
                        <tr key={decision.title} className="border-t border-white/[0.05]">
                          <td className="td text-[12px] text-slate-200">{decision.title}</td>
                          <td className="td text-[11.5px] text-slate-400">{titleCase(String(decision.expected ?? "—"))}</td>
                          <td className="td">
                            <Badge tone={decision.expected === decision.actual ? "ok" : "bad"}>{titleCase(String(decision.actual ?? "—"))}</Badge>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
              <Card>
                <SectionTitle title="Analysis grounding" hint="Statements produced per case and how many failed citation/number checks." />
                <ul className="space-y-2">
                  {analysisDetails.map((row) => (
                    <li key={row.decision} className="flex items-center justify-between gap-3 text-[12px]">
                      <span className="min-w-0 flex-1 truncate text-slate-300">{row.decision}</span>
                      <span className="text-slate-500">{row.statements} stmts</span>
                      <Badge tone={row.unsupported === 0 ? "ok" : "warn"}>{row.unsupported} unsupported</Badge>
                    </li>
                  ))}
                </ul>
              </Card>
            </div>
          ) : null}

          {tab === "retrieval" ? (
            <Card pad={false}>
              <div className="px-5 pt-4"><SectionTitle title="Retrieval quality" hint="Source-level recall@5 and MRR over each decision's labelled relevant documents." /></div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px]">
                  <thead><tr><th className="th">Decision</th><th className="th">Recall@5</th><th className="th">MRR</th><th className="th">Top retrieved sources</th></tr></thead>
                  <tbody>
                    {retrievalDetails.map((row) => (
                      <tr key={row.decision} className="border-t border-white/[0.05]">
                        <td className="td text-[12px] text-slate-200">{row.decision}</td>
                        <td className="td tabular-nums">{percent(row.recall_at_5, 0)}</td>
                        <td className="td tabular-nums">{row.mrr.toFixed(3)}</td>
                        <td className="td text-[11px] text-slate-400">{row.retrieved_doc_keys.join(", ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="border-t border-white/[0.06] px-5 py-3 text-[11px] leading-relaxed text-slate-500">
                The embedder is a deterministic local feature-hashing model (no external API). Semantic recall is therefore
                weaker than a transformer embedding model would be; this is reported as a limitation rather than tuned against the split.
              </p>
            </Card>
          ) : null}

          {tab === "history" ? (
            <Card pad={false}>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px]">
                  <thead><tr><th className="th">Dataset</th><th className="th">Started</th><th className="th">Duration</th><th className="th">Status</th><th className="th" /></tr></thead>
                  <tbody>
                    {(runs?.items ?? []).map((run) => (
                      <tr key={run.id} className="border-t border-white/[0.05]">
                        <td className="td capitalize text-slate-200">{run.dataset}</td>
                        <td className="td text-[11.5px] text-slate-400">{humanDateTime(run.created_at)} · {relativeTime(run.created_at)}</td>
                        <td className="td tabular-nums">{run.duration_seconds?.toFixed(2)}s</td>
                        <td className="td"><Badge tone={run.status === "completed" ? "ok" : "warn"}>{run.status}</Badge></td>
                        <td className="td text-right">
                          <button
                            className="link text-[11.5px]"
                            onClick={async () => {
                              const result = await api<BenchmarkResult>(`/api/benchmarks/${run.id}`);
                              setSelected(result);
                              setDataset(result.dataset);
                              setTab("metrics");
                            }}
                          >
                            View
                          </button>
                        </td>
                      </tr>
                    ))}
                    {!runs?.items.length ? <tr><td className="td text-slate-500" colSpan={5}>No stored runs yet.</td></tr> : null}
                  </tbody>
                </table>
              </div>
            </Card>
          ) : null}
        </>
      ) : !loading ? (
        <Empty
          title="No benchmark runs"
          hint="Run the dev or held-out benchmark to populate this dashboard. Results are stored with the run and are reproducible."
          action={<button className="btn-primary" onClick={() => void runBenchmark()} disabled={running}>Run benchmark</button>}
        />
      ) : null}
    </div>
  );
}

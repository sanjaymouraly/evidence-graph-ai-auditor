"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { DecisionSummary, GraphEdge, GraphNode, GraphPayload, Paged } from "@/lib/types";
import { titleCase } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, Icon, Loading, PageHeader, Toggle } from "@/components/ui";

const TYPE_STYLE: Record<string, { fill: string; stroke: string; radius: number; label: string }> = {
  Document: { fill: "#1c2a44", stroke: "#6aa7f7", radius: 7, label: "Document" },
  DocumentVersion: { fill: "#172238", stroke: "#4f6e9e", radius: 5, label: "Version" },
  Claim: { fill: "#1f2a1f", stroke: "#43c59e", radius: 5, label: "Claim" },
  Entity: { fill: "#2a2338", stroke: "#a78bfa", radius: 8, label: "Entity" },
  Contradiction: { fill: "#2f1d1d", stroke: "#ef6461", radius: 8, label: "Contradiction" },
  Decision: { fill: "#2d2618", stroke: "#e2a33c", radius: 9, label: "Decision" },
  Risk: { fill: "#2c2418", stroke: "#f59e0b", radius: 6, label: "Risk" },
  Requirement: { fill: "#1b2836", stroke: "#38bdf8", radius: 6, label: "Requirement" },
  Reviewer: { fill: "#23202c", stroke: "#c4b5fd", radius: 6, label: "Reviewer" },
  SourceSpan: { fill: "#182030", stroke: "#64748b", radius: 4, label: "Span" },
};

const FALLBACK = { fill: "#1b2434", stroke: "#64748b", radius: 5, label: "Node" };

type Positioned = GraphNode & { x: number; y: number };

function seed(key: string): number {
  let hash = 2166136261;
  for (let index = 0; index < key.length; index += 1) {
    hash ^= key.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return Math.abs(hash % 1000) / 1000;
}

function layout(nodes: GraphNode[]): Record<string, { x: number; y: number }> {
  const width = 900;
  const height = 620;
  const positions: Record<string, { x: number; y: number }> = {};
  const groups: Record<string, GraphNode[]> = {};
  nodes.forEach((node) => {
    const group = node.type ?? "Node";
    (groups[group] ??= []).push(node);
  });
  const order = Object.keys(groups).sort();
  order.forEach((group, groupIndex) => {
    const radius = 90 + (groupIndex * 190) / Math.max(order.length, 1);
    groups[group].forEach((node, index) => {
      const angle = (index / Math.max(groups[group].length, 1)) * Math.PI * 2 + seed(node.key ?? node.id) * 0.55 + groupIndex * 0.6;
      const jitter = 0.72 + seed((node.key ?? node.id) + "r") * 0.5;
      positions[node.key ?? node.id] = {
        x: width / 2 + Math.cos(angle) * radius * jitter,
        y: height / 2 + Math.sin(angle) * radius * jitter,
      };
    });
  });
  // two relaxation passes to spread dense clusters
  for (let pass = 0; pass < 2; pass += 1) {
    nodes.forEach((a) => {
      const pa = positions[a.key ?? a.id];
      let dx = 0;
      let dy = 0;
      nodes.forEach((b) => {
        if (a === b) return;
        const pb = positions[b.key ?? b.id];
        const ddx = pa.x - pb.x;
        const ddy = pa.y - pb.y;
        const dist = Math.hypot(ddx, ddy) || 1;
        if (dist < 34) {
          dx += (ddx / dist) * (34 - dist) * 0.35;
          dy += (ddy / dist) * (34 - dist) * 0.35;
        }
      });
      pa.x = Math.max(30, Math.min(width - 30, pa.x + dx));
      pa.y = Math.max(30, Math.min(height - 30, pa.y + dy));
    });
  }
  // Normalise the bounding box to the viewport. Cluster radii alone leave the drawing
  // bunched in the middle; stretching (independently per axis) uses the full canvas and
  // keeps node labels apart.
  const items = Object.values(positions);
  if (items.length > 1) {
    const minX = Math.min(...items.map((item) => item.x));
    const maxX = Math.max(...items.map((item) => item.x));
    const minY = Math.min(...items.map((item) => item.y));
    const maxY = Math.max(...items.map((item) => item.y));
    const spanX = Math.max(maxX - minX, 1);
    const spanY = Math.max(maxY - minY, 1);
    const targetW = width - 120;
    const targetH = height - 90;
    const scaleX = Math.max(1, Math.min(2.4, targetW / spanX));
    const scaleY = Math.max(1, Math.min(2.4, targetH / spanY));
    items.forEach((item) => {
      item.x = width / 2 + (item.x - (minX + maxX) / 2) * scaleX;
      item.y = height / 2 + (item.y - (minY + maxY) / 2) * scaleY;
    });
  }
  return positions;
}

export default function GraphPage() {
  const [decisions, setDecisions] = useState<DecisionSummary[]>([]);
  const [decisionId, setDecisionId] = useState("");
  const [payload, setPayload] = useState<GraphPayload | null>(null);
  const [hidden, setHidden] = useState<string[]>(["SourceSpan", "DocumentVersion"]);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Paged<DecisionSummary>>("/api/decisions?page_size=50")
      .then((page) => setDecisions(page.items))
      .catch(() => setDecisions([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({ limit: "260" });
        if (decisionId) params.set("decision_id", decisionId);
        const response = await api<GraphPayload>(`/api/graph?${params.toString()}`);
        if (!cancelled) {
          setPayload(response);
          setSelected(null);
          // A whole-workspace graph with every claim drawn is a hairball. Collapse claims by
          // default above a readable threshold; the type toggle below restores them.
          if (response.nodes.length > 200 && !decisionId) {
            setHidden((prev) => (prev.includes("Claim") ? prev : [...prev, "Claim"]));
          }
        }
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : "Failed to load graph");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [decisionId]);

  const visibleNodes = useMemo(() => (payload?.nodes ?? []).filter((node) => !hidden.includes(node.type)), [payload, hidden]);
  const nodeKeys = useMemo(() => new Set(visibleNodes.map((node) => node.key ?? node.id)), [visibleNodes]);
  const visibleEdges = useMemo(() => (payload?.edges ?? []).filter((edge) => nodeKeys.has(edge.source) && nodeKeys.has(edge.target)), [payload, nodeKeys]);
  // Node ids are only unique per type (claim ids, document ids etc. can collide), so the
  // layout is keyed on type+id just like the persisted graph nodes are.
  const positions = useMemo(() => layout(visibleNodes), [visibleNodes]);
  const typeCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    (payload?.nodes ?? []).forEach((node) => {
      counts[node.type] = (counts[node.type] ?? 0) + 1;
    });
    return counts;
  }, [payload]);

  const neighbours = useMemo(() => {
    if (!selected || !payload) return [];
    const key = selected.key ?? selected.id;
    const result: { edge: GraphEdge; node: GraphNode | null; direction: "out" | "in" }[] = [];
    payload.edges.forEach((edge) => {
      if (edge.source === key) result.push({ edge, node: payload.nodes.find((node) => (node.key ?? node.id) === edge.target) ?? null, direction: "out" });
      else if (edge.target === key) result.push({ edge, node: payload.nodes.find((node) => (node.key ?? node.id) === edge.source) ?? null, direction: "in" });
    });
    return result.slice(0, 26);
  }, [selected, payload]);

  return (
    <div className="fade-in">
      <PageHeader
        title="Evidence graph"
        subtitle="Persisted nodes and typed relations: claims, sources, entities, contradictions and decisions. Edges are recorded, not inferred at render time."
      />

      <Card className="mb-4">
        <div className="flex flex-wrap items-center gap-3">
          <select className="input w-auto min-w-[260px]" value={decisionId} onChange={(event) => setDecisionId(event.target.value)}>
            <option value="">Whole workspace</option>
            {decisions.map((decision) => <option key={decision.id} value={decision.id}>{decision.title}</option>)}
          </select>
          <span className="text-[12px] text-slate-500">{payload?.counts.nodes ?? 0} nodes · {payload?.counts.edges ?? 0} edges{payload?.truncated ? " · truncated" : ""}</span>
          <span className="text-[11.5px] text-slate-500">{visibleNodes.length} shown{hidden.length ? ` · ${hidden.length} type(s) hidden` : ""}</span>
          <div className="flex-1" />
          <div className="flex flex-wrap items-center gap-3">
            {Object.keys(TYPE_STYLE).filter((type) => typeCounts[type]).map((type) => (
              <Toggle
                key={type}
                label={`${TYPE_STYLE[type].label} (${typeCounts[type]})`}
                checked={!hidden.includes(type)}
                onChange={(value) => setHidden((previous) => (value ? previous.filter((item) => item !== type) : [...previous, type]))}
              />
            ))}
          </div>
        </div>
      </Card>

      {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Card pad={false} className="overflow-hidden">
          <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-2.5">
            <span className="text-[12.5px] text-slate-300">{decisionId ? "Decision subgraph" : "Workspace graph"}</span>
            <span className="text-[11px] text-slate-500">click a node to inspect</span>
          </div>
          {loading ? <div className="p-4"><Loading rows={8} /></div> : visibleNodes.length === 0 ? (
            <div className="p-4"><Empty title="No nodes to display" hint="Enable more node types or select another decision." /></div>
          ) : (
            <div className="grid-lines" style={{ background: "radial-gradient(circle at 50% 40%, rgba(226,163,60,0.05), transparent 60%)" }}>
              <svg viewBox="0 0 900 620" className="h-[560px] w-full">
                <g>
                  {visibleEdges.map((edge, index) => {
                    const from = positions[edge.source];
                    const to = positions[edge.target];
                    if (!from || !to) return null;
                    if (!from || !to) return null;
                    const highlighted = selected && (edge.source === (selected.key ?? selected.id) || edge.target === (selected.key ?? selected.id));
                    const color = edge.relation === "CONTRADICTS" ? "rgba(239,100,97,0.55)" : edge.relation === "SUPPORTS" ? "rgba(67,197,158,0.45)" : "rgba(148,163,184,0.22)";
                    return (
                      <line
                        key={edge.key ?? `${edge.source}-${edge.target}-${index}`}
                        x1={from.x}
                        y1={from.y}
                        x2={to.x}
                        y2={to.y}
                        stroke={highlighted ? "rgba(226,163,60,0.7)" : color}
                        strokeWidth={highlighted ? 1.6 : 0.8}
                      />
                    );
                  })}
                </g>
                <g>
                  {visibleNodes.map((node) => {
                    const position = positions[node.key ?? node.id];
                    const style = TYPE_STYLE[node.type] ?? FALLBACK;
                    const isSelected = selected?.key === node.key && selected?.type === node.type;
                    return (
                      <g key={node.key ?? node.id} transform={`translate(${position.x},${position.y})`} className="cursor-pointer" onClick={() => setSelected(node)}>
                        <title>{`${node.type}: ${node.label}`}</title>
                        <circle r={isSelected ? style.radius + 3 : style.radius} fill={style.fill} stroke={isSelected ? "#f3c579" : style.stroke} strokeWidth={isSelected ? 2 : 1.1} />
                        {isSelected || node.type === "Decision" || node.type === "Contradiction" ? (
                          <text x={style.radius + 4} y={3.5} fontSize="9.5" fill={isSelected ? "#f8fafc" : "rgba(226,232,240,0.8)"}>
                            {String(node.label).slice(0, 22)}
                          </text>
                        ) : null}
                      </g>
                    );
                  })}
                </g>
              </svg>
            </div>
          )}
        </Card>

        <div className="space-y-4">
          <Card>
            <div className="label mb-2">Selection</div>
            {!selected ? (
              <p className="text-[12px] text-slate-500">Select a node to see its properties and typed relations.</p>
            ) : (
              <div>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[13px] font-medium text-slate-100">{String(selected.label)}</span>
                  <Badge tone="neutral">{selected.type}</Badge>
                </div>
                <div className="mono mt-1 break-all text-[10.5px] text-slate-500">{selected.key ?? selected.id}</div>
                {selected.status ? <div className="mt-2 text-[11.5px] text-slate-400">status: {String(selected.status)}</div> : null}
                {selected.meta && Object.keys(selected.meta).length ? (
                  <div className="mt-2 rounded-md border border-white/[0.06] bg-white/[0.02] p-2 text-[11px] text-slate-400">
                    {Object.entries(selected.meta).slice(0, 8).map(([key, value]) => (
                      <div key={key} className="flex justify-between gap-2"><span className="text-slate-500">{key}</span><span className="truncate text-slate-300">{String(value)}</span></div>
                    ))}
                  </div>
                ) : null}
                {selected.type === "Decision" ? <Link href={`/decisions/view?id=${selected.id}`} className="link mt-2 inline-block text-[11.5px]">Open decision</Link> : null}
                {selected.type === "Document" ? <Link href={`/documents/view?id=${selected.id}`} className="link mt-2 inline-block text-[11.5px]">Open source</Link> : null}
              </div>
            )}
          </Card>

          <Card>
            <div className="label mb-2">Relations {selected ? `(${neighbours.length})` : ""}</div>
            {!selected ? (
              <p className="text-[12px] text-slate-500">No node selected.</p>
            ) : (
              <ul className="max-h-[360px] space-y-1.5 overflow-y-auto pr-1">
                {neighbours.map(({ edge, node, direction }, index) => (
                  <li key={`${edge.key ?? index}`} className="rounded-md border border-white/[0.06] bg-white/[0.02] px-2 py-1.5">
                    <div className="flex items-center justify-between gap-2">
                      <Badge tone={edge.relation === "CONTRADICTS" ? "bad" : edge.relation === "SUPPORTS" ? "ok" : "neutral"}>{edge.relation}</Badge>
                      <span className="text-[10px] uppercase text-slate-600">{direction}</span>
                    </div>
                    <button className="mt-1 block w-full truncate text-left text-[11.5px] text-slate-300 hover:text-accent" onClick={() => node && setSelected(node)}>
                      {node ? String(node.label) : "—"}
                    </button>
                  </li>
                ))}
                {!neighbours.length ? <li className="text-[12px] text-slate-500">No recorded relations.</li> : null}
              </ul>
            )}
          </Card>

          <Card>
            <div className="label mb-2">Legend</div>
            <div className="grid grid-cols-2 gap-1.5">
              {Object.entries(TYPE_STYLE).filter(([type]) => typeCounts[type]).map(([type, style]) => (
                <div key={type} className="flex items-center gap-2 text-[11.5px] text-slate-400">
                  <span className="h-2.5 w-2.5 rounded-full border" style={{ background: style.fill, borderColor: style.stroke }} />
                  {style.label}
                </div>
              ))}
            </div>
            <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
              Relations are derived from stored evidence links (support, contradiction, supersession, dependency) and written to the graph store during ingestion and review.
            </p>
          </Card>
        </div>
      </div>
    </div>
  );
}

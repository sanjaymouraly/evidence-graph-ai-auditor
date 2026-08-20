"use client";

import Link from "next/link";
import { useEffect, useId, useState, type ReactNode } from "react";
import { statusTone, titleCase } from "@/lib/format";

export type Tone = "ok" | "warn" | "bad" | "info" | "neutral" | "accent";

const TONE_CLASSES: Record<Tone, string> = {
  ok: "border-ok/30 bg-ok/10 text-emerald-200",
  warn: "border-warn/30 bg-warn/10 text-amber-200",
  bad: "border-bad/30 bg-bad/10 text-red-200",
  info: "border-info/30 bg-info/10 text-sky-200",
  neutral: "border-white/10 bg-white/[0.04] text-slate-300",
  accent: "border-accent/35 bg-accent/10 text-amber-100",
};

export function Badge({ children, tone = "neutral", className = "" }: { children: ReactNode; tone?: Tone; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium ${TONE_CLASSES[tone]} ${className}`}>
      {children}
    </span>
  );
}

export function StatusPill({ status }: { status?: string | null }) {
  const tone = statusTone(status) as Tone;
  return <Badge tone={tone}>{titleCase(status ?? "unknown")}</Badge>;
}

export function SeverityDot({ severity }: { severity?: string | null }) {
  const color = severity === "critical" ? "bg-red-400" : severity === "high" ? "bg-orange-400" : severity === "medium" ? "bg-amber-300" : "bg-slate-400";
  return <span className={`inline-block h-2 w-2 rounded-full ${color}`} aria-hidden />;
}

export function Card({ children, className = "", pad = true }: { children: ReactNode; className?: string; pad?: boolean }) {
  return <div className={`card ${pad ? "card-pad" : ""} ${className}`}>{children}</div>;
}

export function SectionTitle({ title, hint, actions }: { title: string; hint?: string; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex items-start justify-between gap-4">
      <div>
        <h2 className="h2">{title}</h2>
        {hint ? <p className="mt-1 text-[12.5px] text-slate-400">{hint}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="h1">{title}</h1>
        {subtitle ? <p className="mt-1.5 max-w-3xl text-[13px] leading-relaxed text-slate-400">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone = "neutral",
  href,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: Tone;
  href?: string;
}) {
  const testId = `stat-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}`;
  const body = (
    <div className="card card-pad transition-colors hover:border-white/15" data-testid={testId}>
      <div className="flex items-center justify-between">
        <span className="label">{label}</span>
        <span className={`h-1.5 w-1.5 rounded-full ${tone === "ok" ? "bg-ok" : tone === "bad" ? "bg-bad" : tone === "warn" ? "bg-warn" : "bg-info"}`} />
      </div>
      <div className="mt-3 text-2xl font-semibold tracking-tight text-slate-50">{value}</div>
      {hint ? <div className="mt-1 text-[12px] text-slate-400">{hint}</div> : null}
    </div>
  );
  return href ? <Link href={href}>{body}</Link> : body;
}

export function Empty({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-white/10 px-6 py-12 text-center">
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full border border-white/10 bg-white/[0.03] text-slate-400">
        <Icon name="inbox" className="h-5 w-5" />
      </div>
      <p className="text-sm font-medium text-slate-200">{title}</p>
      {hint ? <p className="mt-1 max-w-md text-[12.5px] text-slate-400">{hint}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function Loading({ label = "Loading…", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div className="space-y-2" role="status" aria-live="polite">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="h-10 animate-pulse rounded-lg bg-white/[0.04]" />
      ))}
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="rounded-lg border border-bad/30 bg-bad/10 px-4 py-3 text-[13px] text-red-200">
      <div className="flex items-start justify-between gap-3">
        <span>{message}</span>
        {onRetry ? (
          <button type="button" onClick={onRetry} className="shrink-0 text-[12px] font-medium underline underline-offset-2">
            Retry
          </button>
        ) : null}
      </div>
    </div>
  );
}

export function Field({ label, hint, children, className = "" }: { label: string; hint?: string; children: ReactNode; className?: string }) {
  return (
    <label className={`block ${className}`}>
      <span className="mb-1.5 block text-[12px] font-medium text-slate-300">{label}</span>
      {children}
      {hint ? <span className="mt-1 block text-[11.5px] text-slate-500">{hint}</span> : null}
    </label>
  );
}

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: { id: string; label: string; count?: number }[];
  active: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1 border-b border-white/[0.07] pb-px">
      {tabs.map((tab) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onChange(tab.id)}
            className={`-mb-px rounded-t-md border-b-2 px-3 py-2 text-[13px] transition-colors ${
              selected ? "border-accent text-slate-100" : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            {tab.label}
            {tab.count !== undefined ? <span className="ml-2 rounded bg-white/[0.06] px-1.5 py-0.5 text-[10.5px] text-slate-400">{tab.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

export function Modal({ open, title, onClose, children, width = "max-w-xl" }: { open: boolean; title: string; onClose: () => void; children: ReactNode; width?: string }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-4 pt-24 backdrop-blur-sm" onClick={onClose}>
      <div className={`w-full ${width} card fade-in`} onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true">
        <div className="flex items-center justify-between border-b border-white/[0.07] px-5 py-3.5">
          <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
          <button type="button" onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-white/[0.06] hover:text-slate-200" aria-label="Close">
            <Icon name="x" className="h-4 w-4" />
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

export function Donut({
  segments,
  size = 132,
  thickness = 12,
  center,
}: {
  segments: { label: string; value: number; color: string }[];
  size?: number;
  thickness?: number;
  center?: ReactNode;
}) {
  const total = segments.reduce((sum, segment) => sum + segment.value, 0) || 1;
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={thickness} />
        {segments.map((segment) => {
          const length = (segment.value / total) * circumference;
          const dash = `${length} ${circumference - length}`;
          const element = (
            <circle
              key={segment.label}
              cx={size / 2}
              cy={size / 2}
              r={radius}
              fill="none"
              stroke={segment.color}
              strokeWidth={thickness}
              strokeDasharray={dash}
              strokeDashoffset={-offset}
              strokeLinecap="butt"
            />
          );
          offset += length;
          return element;
        })}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center">{center}</div>
    </div>
  );
}

export function Meter({ value, tone = "ok", label }: { value: number; tone?: "ok" | "warn" | "bad" | "info"; label?: string }) {
  const color = tone === "ok" ? "bg-ok" : tone === "warn" ? "bg-warn" : tone === "bad" ? "bg-bad" : "bg-info";
  return (
    <div>
      {label ? <div className="mb-1 flex items-center justify-between text-[11.5px] text-slate-400"><span>{label}</span><span>{Math.round(value * 100)}%</span></div> : null}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.07]">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }} />
      </div>
    </div>
  );
}

export function KeyValue({ items }: { items: { label: string; value: ReactNode }[] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
      {items.map((item) => (
        <div key={item.label} className="flex items-baseline justify-between gap-3 border-b border-white/[0.05] py-1.5">
          <dt className="text-[11.5px] uppercase tracking-wide text-slate-500">{item.label}</dt>
          <dd className="text-right text-[12.5px] text-slate-200">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="text-[11.5px] text-slate-400 hover:text-slate-200"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        } catch {
          setCopied(false);
        }
      }}
    >
      {copied ? "Copied" : label}
    </button>
  );
}

export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (value: boolean) => void; label?: string }) {
  const id = useId();
  return (
    <label htmlFor={id} className="inline-flex cursor-pointer items-center gap-2 text-[12.5px] text-slate-300">
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`relative h-5 w-9 rounded-full transition-colors ${checked ? "bg-accent" : "bg-white/[0.12]"}`}
      >
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-ink-950 transition-all ${checked ? "left-[18px]" : "left-0.5"}`} />
      </button>
      {label}
    </label>
  );
}

export type IconName =
  | "dashboard" | "upload" | "document" | "claim" | "graph" | "split" | "decision" | "plus"
  | "check" | "shield" | "ask" | "chart" | "report" | "audit" | "settings" | "search" | "x"
  | "inbox" | "arrow" | "download" | "refresh" | "external" | "lock" | "clock" | "alert" | "chevron";

export function Icon({ name, className = "h-4 w-4" }: { name: IconName; className?: string }) {
  const paths: Record<IconName, ReactNode> = {
    dashboard: <><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>,
    upload: <><path d="M12 16V4m0 0-4 4m4-4 4 4" /><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" /></>,
    document: <><path d="M7 3h7l4 4v14H7z" /><path d="M14 3v4h4" /><path d="M9.5 12h5M9.5 15.5h5" /></>,
    claim: <><circle cx="12" cy="12" r="8.5" /><path d="M8.5 12l2.5 2.5 4.5-5" /></>,
    graph: <><circle cx="6" cy="6" r="2.3" /><circle cx="18" cy="7" r="2.3" /><circle cx="12" cy="18" r="2.3" /><path d="M8.1 7.1l7.7.4M7 8.2l4 7.5M17 9.2l-4 6.6" /></>,
    split: <><path d="M5 5v14M19 5v14M5 12h7M12 12l7-6M12 12l7 6" /></>,
    decision: <><path d="M4 6h10M4 12h7M4 18h10" /><path d="M16 15l2.4 2.4L23 13" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    check: <path d="M5 12.5l4.5 4.5L19 7" />,
    shield: <><path d="M12 3l7 3v6c0 4.4-3 7.7-7 9-4-1.3-7-4.6-7-9V6z" /><path d="M8.8 12.2l2.3 2.3 4.3-4.6" /></>,
    ask: <><path d="M4 5h16v11H9l-5 4z" /><path d="M9.5 9.2a2.5 2.5 0 1 1 3.4 2.3c-.6.3-.9.8-.9 1.5" /><circle cx="12" cy="15" r=".6" fill="currentColor" /></>,
    chart: <><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></>,
    report: <><path d="M7 3h10v18H7z" /><path d="M9.5 8h5M9.5 11.5h5M9.5 15h3" /></>,
    audit: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
    settings: <><circle cx="12" cy="12" r="3.2" /><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M18.5 5.5l-1.7 1.7M7.2 16.8l-1.7 1.7" /></>,
    search: <><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-3.6-3.6" /></>,
    x: <path d="M6 6l12 12M18 6L6 18" />,
    inbox: <><path d="M3 13h5l1.5 3h5L16 13h5" /><path d="M4 13l2-8h12l2 8v5H4z" /></>,
    arrow: <path d="M5 12h14m0 0-5-5m5 5-5 5" />,
    download: <><path d="M12 4v12m0 0-4-4m4 4 4-4" /><path d="M5 20h14" /></>,
    refresh: <><path d="M4 12a8 8 0 0 1 13.7-5.7L20 8.5" /><path d="M20 4v4.5h-4.5" /><path d="M20 12a8 8 0 0 1-13.7 5.7L4 15.5" /><path d="M4 20v-4.5h4.5" /></>,
    external: <><path d="M14 4h6v6" /><path d="M20 4l-9 9" /><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></>,
    lock: <><rect x="5" y="10" width="14" height="10" rx="2" /><path d="M8 10V8a4 4 0 0 1 8 0v2" /></>,
    clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3.2 1.9" /></>,
    alert: <><path d="M12 4l9 16H3z" /><path d="M12 9.5v4.2" /><circle cx="12" cy="16.6" r=".7" fill="currentColor" /></>,
    chevron: <path d="M9 6l6 6-6 6" />,
  };
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      {paths[name]}
    </svg>
  );
}

"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { Icon, type IconName } from "./ui";

type NavItem = { href: string; label: string; icon: IconName; badge?: string };
type NavGroup = { title: string; items: NavItem[] };

const NAV: NavGroup[] = [
  {
    title: "Overview",
    items: [{ href: "/", label: "Dashboard", icon: "dashboard" }],
  },
  {
    title: "Evidence",
    items: [
      { href: "/ingestion", label: "Ingestion", icon: "upload" },
      { href: "/documents", label: "Documents", icon: "document" },
      { href: "/claims", label: "Claims", icon: "claim" },
      { href: "/graph", label: "Evidence graph", icon: "graph" },
      { href: "/contradictions", label: "Contradictions", icon: "split" },
    ],
  },
  {
    title: "Decisions",
    items: [
      { href: "/decisions", label: "All decisions", icon: "decision" },
      { href: "/decisions/new", label: "New decision", icon: "plus" },
      { href: "/reviews", label: "Review queue", icon: "shield" },
    ],
  },
  {
    title: "Intelligence",
    items: [
      { href: "/ask", label: "Grounded Q&A", icon: "ask" },
      { href: "/benchmarks", label: "Benchmarks", icon: "chart" },
      { href: "/reports", label: "Reports", icon: "report" },
    ],
  },
  {
    title: "Governance",
    items: [
      { href: "/audit", label: "Audit trail", icon: "audit" },
      { href: "/settings", label: "Providers & settings", icon: "settings" },
    ],
  },
];

type VersionInfo = { version?: string; gate?: string; policy?: string };

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, workspace, logout, ready } = useAuth();
  const [version, setVersion] = useState<VersionInfo | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    api<VersionInfo>("/api/health/version").then(setVersion).catch(() => setVersion(null));
  }, []);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  if (pathname?.startsWith("/login")) return <>{children}</>;

  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname?.startsWith(href));

  return (
    <div className="flex min-h-screen">
      <aside
        className={`fixed inset-y-0 left-0 z-40 w-64 shrink-0 overflow-y-auto border-r border-white/[0.07] bg-ink-900/95 px-3 py-4 backdrop-blur transition-transform lg:static lg:translate-x-0 ${
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <Link href="/" className="mb-6 flex items-center gap-3 px-2">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-accent/30 bg-accent/10 text-accent">
            <Icon name="shield" className="h-5 w-5" />
          </span>
          <span>
            <span className="block text-[13px] font-semibold leading-tight text-slate-100">Evidence-Graph</span>
            <span className="block text-[11px] leading-tight text-slate-500">Decision auditor</span>
          </span>
        </Link>

        <nav className="space-y-5">
          {NAV.map((group) => (
            <div key={group.title}>
              <div className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-600">{group.title}</div>
              <div className="space-y-0.5">
                {group.items.map((item) => {
                  const active = isActive(item.href);
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className={`group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] transition-colors ${
                        active ? "bg-accent/10 text-amber-100" : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-200"
                      }`}
                    >
                      <Icon name={item.icon} className={`h-4 w-4 ${active ? "text-accent" : "text-slate-500 group-hover:text-slate-300"}`} />
                      <span className="flex-1">{item.label}</span>
                      {active ? <span className="h-1.5 w-1.5 rounded-full bg-accent" /> : null}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="mt-6 rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
          <div className="flex items-center gap-2 text-[11.5px] text-slate-400">
            <span className="h-1.5 w-1.5 rounded-full bg-ok" />
            Deterministic gates active
          </div>
          <p className="mt-1.5 text-[11px] leading-relaxed text-slate-500">
            LLM output is advisory. Gate outcomes are computed by software, never by a model.
          </p>
          {version ? (
            <div className="mt-2 flex flex-wrap gap-1 text-[10px] text-slate-600">
              <span className="rounded bg-white/[0.05] px-1.5 py-0.5">{version.gate ?? "gate"}</span>
              <span className="rounded bg-white/[0.05] px-1.5 py-0.5">{version.policy ?? "policy"}</span>
            </div>
          ) : null}
        </div>
      </aside>

      {mobileOpen ? <div className="fixed inset-0 z-30 bg-black/60 lg:hidden" onClick={() => setMobileOpen(false)} aria-hidden /> : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 border-b border-white/[0.07] bg-ink-950/80 backdrop-blur">
          <div className="flex h-14 items-center gap-3 px-4 lg:px-6">
            <button
              type="button"
              className="rounded-md border border-white/10 p-1.5 text-slate-300 lg:hidden"
              onClick={() => setMobileOpen(true)}
              aria-label="Open navigation"
            >
              <Icon name="chevron" className="h-4 w-4" />
            </button>
            <div className="hidden items-center gap-2 text-[12.5px] text-slate-400 md:flex">
              <span className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-1 text-slate-300">
                {workspace?.name ?? "Workspace"}
              </span>
              <span className="text-slate-600">/</span>
              <span className="text-slate-500">{workspace?.slug ?? ""}</span>
            </div>
            <div className="flex-1" />
            <button
              type="button"
              onClick={() => router.push("/claims/")}
              className="hidden items-center gap-2 rounded-lg border border-white/10 bg-white/[0.02] px-3 py-1.5 text-[12px] text-slate-500 hover:text-slate-300 sm:flex"
            >
              <Icon name="search" className="h-3.5 w-3.5" />
              Search claims
              <kbd className="rounded border border-white/10 px-1 text-[10px] text-slate-500">/</kbd>
            </button>
            <div className="flex items-center gap-2">
              <div className="hidden text-right sm:block">
                <div className="text-[12.5px] font-medium leading-tight text-slate-200">{user?.name ?? "—"}</div>
                <div className="text-[10.5px] uppercase tracking-wide text-slate-500">{user?.role ?? ""}</div>
              </div>
              <div className="flex h-8 w-8 items-center justify-center rounded-full border border-white/10 bg-white/[0.05] text-[12px] font-semibold text-slate-300">
                {(user?.name ?? "?").slice(0, 1)}
              </div>
              <button type="button" onClick={logout} className="rounded p-1.5 text-slate-500 hover:bg-white/[0.05] hover:text-slate-300" title="Sign out" aria-label="Sign out">
                <Icon name="external" className="h-4 w-4" />
              </button>
            </div>
          </div>
        </header>

        <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 lg:px-8">
          {ready ? children : <div className="h-40 animate-pulse rounded-xl bg-white/[0.03]" />}
        </main>
      </div>
    </div>
  );
}

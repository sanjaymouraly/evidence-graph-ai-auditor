"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { ApiError, detailText } from "@/lib/api";
import { Icon } from "@/components/ui";

const DEMO = [
  { role: "Admin", email: "admin@northwind.example" },
  { role: "Reviewer", email: "reviewer@northwind.example" },
  { role: "Auditor", email: "auditor@northwind.example" },
  { role: "Viewer", email: "viewer@northwind.example" },
];

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("admin@northwind.example");
  const [password, setPassword] = useState("DemoPass!2026");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
      router.push("/");
    } catch (exc) {
      setError(exc instanceof ApiError ? detailText(exc.detail) : "Unable to reach the API");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.15fr_1fr]">
      <div className="relative hidden flex-col justify-between overflow-hidden border-r border-white/[0.07] bg-ink-900/60 p-12 lg:flex">
        <div className="grid-lines absolute inset-0 opacity-[0.35]" aria-hidden />
        <div className="relative">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-accent/30 bg-accent/10 text-accent">
              <Icon name="shield" className="h-5 w-5" />
            </span>
            <div>
              <div className="text-sm font-semibold text-slate-100">Evidence-Graph AI Auditor</div>
              <div className="text-[11px] text-slate-500">Northwind Manufacturing · synthetic demo workspace</div>
            </div>
          </div>

          <h1 className="mt-16 max-w-xl text-3xl font-semibold leading-tight tracking-tight text-slate-50">
            Decisions only pass when the evidence does.
          </h1>
          <p className="mt-4 max-w-lg text-[13.5px] leading-relaxed text-slate-400">
            Documents are parsed into a versioned evidence graph. Claims carry a precise source locator,
            contradictions are detected across sources, and a deterministic gate decides whether a case is
            sufficiently supported. Language models may explain evidence — they never decide.
          </p>

          <div className="mt-10 grid max-w-lg gap-3">
            {[
              ["Provenance to the page, cell or paragraph", "Every assertion links back to an immutable document version and a verified quote."],
              ["Contradictions before conclusions", "Conflicting prices, delivery terms and specs are surfaced with both sources."],
              ["Tamper-evident audit trail", "Every material action is hash-chained and independently verifiable."],
            ].map(([title, body]) => (
              <div key={title} className="flex gap-3 rounded-lg border border-white/[0.06] bg-white/[0.02] p-3.5">
                <Icon name="check" className="mt-0.5 h-4 w-4 shrink-0 text-ok" />
                <div>
                  <div className="text-[12.5px] font-medium text-slate-200">{title}</div>
                  <div className="mt-0.5 text-[12px] leading-relaxed text-slate-500">{body}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <p className="relative text-[11px] text-slate-600">
          All organisations, vendors and figures in this workspace are fictional synthetic data.
        </p>
      </div>

      <div className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <div className="flex items-center gap-2 text-slate-100">
              <Icon name="shield" className="h-5 w-5 text-accent" />
              <span className="text-sm font-semibold">Evidence-Graph AI Auditor</span>
            </div>
          </div>
          <h2 className="text-lg font-semibold text-slate-100">Sign in to the audit workspace</h2>
          <p className="mt-1 text-[12.5px] text-slate-400">Use a seeded demo account or your own credentials.</p>

          <form onSubmit={submit} className="mt-6 space-y-4">
            <label className="block">
              <span className="mb-1.5 block text-[12px] font-medium text-slate-300">Email</span>
              <input className="input" type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} required />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-[12px] font-medium text-slate-300">Password</span>
              <input className="input" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required />
            </label>
            {error ? <div className="rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 text-[12.5px] text-red-200">{error}</div> : null}
            <button type="submit" className="btn-primary w-full" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <div className="mt-8">
            <div className="label mb-2">Demo accounts · password DemoPass!2026</div>
            <div className="grid grid-cols-2 gap-2">
              {DEMO.map((account) => (
                <button
                  key={account.role}
                  type="button"
                  className="rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-2 text-left text-[12px] text-slate-300 hover:border-accent/30 hover:bg-accent/5"
                  onClick={() => {
                    setEmail(account.email);
                    setPassword("DemoPass!2026");
                  }}
                >
                  <span className="block text-[11px] uppercase tracking-wide text-slate-500">{account.role}</span>
                  <span className="block truncate text-[11.5px] text-slate-300">{account.email}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

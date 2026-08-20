"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, api, detailText } from "@/lib/api";
import type { Provider } from "@/lib/types";
import { humanDateTime, titleCase } from "@/lib/format";
import { can, useAuth } from "@/lib/auth";
import { Badge, Card, Empty, ErrorBox, Field, Icon, Loading, Modal, PageHeader, SectionTitle, Stat } from "@/components/ui";

type ProviderList = {
  items: Provider[];
  environment_providers?: { id: string; label: string; kind: string; model?: string; api_key_set: boolean }[];
  offline_available?: boolean;
};

type UsageSummary = {
  total_requests?: number;
  total_prompt_tokens?: number;
  total_completion_tokens?: number;
  estimated_cost_usd?: number;
  by_provider?: Record<string, number>;
  recent?: { id: string; operation: string; model?: string; success: boolean; latency_ms?: number; estimated_cost_usd?: number; created_at: string; error?: string }[];
};

const KINDS = [
  { value: "openai", label: "OpenAI / compatible" },
  { value: "anthropic", label: "Anthropic" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "custom", label: "Custom endpoint" },
  { value: "ollama", label: "Ollama (local)" },
  { value: "offline", label: "Offline (no network)" },
];

type Form = {
  name: string;
  kind: string;
  base_url: string;
  api_key: string;
  model: string;
  embedding_model: string;
  rerank_model: string;
  is_default_generation: boolean;
  is_default_embedding: boolean;
};

const BLANK: Form = {
  name: "",
  kind: "openai",
  base_url: "",
  api_key: "",
  model: "",
  embedding_model: "",
  rerank_model: "",
  is_default_generation: true,
  is_default_embedding: false,
};

export default function SettingsPage() {
  const { user, workspace } = useAuth();
  const [providers, setProviders] = useState<ProviderList | null>(null);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<Form>(BLANK);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; latency_ms?: number; error?: string; checked_at?: string; models?: string[] }>>({});

  const load = useCallback(async () => {
    setError(null);
    try {
      const [list, summary] = await Promise.all([
        api<ProviderList>("/api/providers"),
        api<UsageSummary>("/api/providers/usage/summary").catch(() => null),
      ]);
      setProviders(list);
      setUsage(summary);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to load settings");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function createProvider(event: React.FormEvent) {
    event.preventDefault();
    setBusy("create");
    setError(null);
    try {
      await api("/api/providers", { method: "POST", body: { ...form, enabled: true } });
      setOpen(false);
      setForm(BLANK);
      setNote("Provider created. Run a connection test before relying on it.");
      await load();
    } catch (exc) {
      setError(exc instanceof ApiError ? detailText(exc.detail) : "Could not create provider");
    } finally {
      setBusy(null);
    }
  }

  async function testProvider(provider: Provider) {
    setBusy(`test-${provider.id}`);
    try {
      const result = await api<{ ok: boolean; latency_ms?: number; error?: string; checked_at?: string; models?: string[] }>(
        `/api/providers/${provider.id}/test`,
        { method: "POST" },
      );
      setTestResults((previous) => ({ ...previous, [provider.id]: result }));
      await load();
    } catch (exc) {
      setTestResults((previous) => ({ ...previous, [provider.id]: { ok: false, error: exc instanceof ApiError ? detailText(exc.detail) : "Test failed" } }));
    } finally {
      setBusy(null);
    }
  }

  async function discover(provider: Provider) {
    setBusy(`discover-${provider.id}`);
    try {
      const result = await api<{ models?: string[]; error?: string }>(`/api/providers/${provider.id}/discover-models`, { method: "POST" });
      setNote(result.models?.length ? `${result.models.length} models discovered: ${result.models.slice(0, 6).join(", ")}` : result.error ?? "No models returned");
    } catch (exc) {
      setError(exc instanceof ApiError ? detailText(exc.detail) : "Discovery failed");
    } finally {
      setBusy(null);
    }
  }

  async function patch(provider: Provider, body: Record<string, unknown>) {
    setBusy(`patch-${provider.id}`);
    try {
      await api(`/api/providers/${provider.id}`, { method: "PATCH", body });
      await load();
    } catch (exc) {
      setError(exc instanceof ApiError ? detailText(exc.detail) : "Update failed");
    } finally {
      setBusy(null);
    }
  }

  async function remove(provider: Provider) {
    setBusy(`delete-${provider.id}`);
    try {
      await api(`/api/providers/${provider.id}`, { method: "DELETE" });
      await load();
    } catch (exc) {
      setError(exc instanceof ApiError ? detailText(exc.detail) : "Delete failed");
    } finally {
      setBusy(null);
    }
  }

  const isAdmin = can(user, "provider:create") || user?.role === "admin";

  return (
    <div className="fade-in">
      <PageHeader
        title="Providers & settings"
        subtitle="Separate generation, embedding and reranking providers. Keys are encrypted at rest and never returned by the API — only a masked hint is shown."
        actions={isAdmin ? <button className="btn-primary" onClick={() => setOpen(true)}><Icon name="plus" className="h-3.5 w-3.5" /> Add provider</button> : null}
      />

      {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}
      {note ? <div className="mb-4 rounded-lg border border-ok/25 bg-ok/[0.07] px-4 py-2.5 text-[12.5px] text-emerald-200">{note}</div> : null}

      <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Workspace" value={workspace?.name ?? "—"} hint={workspace?.slug} tone="info" />
        <Stat label="Configured providers" value={providers?.items.length ?? 0} hint={`${providers?.environment_providers?.length ?? 0} from environment`} tone="neutral" />
        <Stat label="Offline mode" value={providers?.offline_available ? "Available" : "—"} hint="Deterministic analysis without any API key" tone="ok" />
        <Stat label="Requests logged" value={usage?.total_requests ?? 0} hint={`${(usage?.estimated_cost_usd ?? 0).toFixed(4)} USD estimated`} tone="warn" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
        <div className="space-y-4">
          <Card>
            <SectionTitle title="Provider configurations" hint="Generation, embedding and reranking can use different providers." />
            {!providers ? <Loading rows={3} /> : providers.items.length === 0 ? (
              <Empty
                title="No providers configured"
                hint="The application works fully offline. Add a provider to enable model-generated narrative explanations."
                action={isAdmin ? <button className="btn-primary" onClick={() => setOpen(true)}>Add provider</button> : null}
              />
            ) : (
              <ul className="space-y-3">
                {providers.items.map((provider) => {
                  const result = testResults[provider.id] ?? provider.last_check;
                  return (
                    <li key={provider.id} className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div>
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-[13px] font-medium text-slate-100">{provider.name}</span>
                            <Badge tone="neutral">{titleCase(provider.kind)}</Badge>
                            {provider.is_default_generation ? <Badge tone="accent">generation</Badge> : null}
                            {provider.is_default_embedding ? <Badge tone="info">embedding</Badge> : null}
                            <Badge tone={provider.enabled ? "ok" : "warn"}>{provider.enabled ? "enabled" : "disabled"}</Badge>
                          </div>
                          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
                            <span className="mono">{provider.base_url || "default endpoint"}</span>
                            {provider.model ? <span>model {provider.model}</span> : null}
                            {provider.embedding_model ? <span>embed {provider.embedding_model}</span> : null}
                            <span>key {provider.api_key_set ? provider.api_key_hint ?? "stored" : "not set"}</span>
                          </div>
                        </div>
                        <div className="flex flex-wrap items-center gap-1.5">
                          <button className="btn-ghost px-2 py-1 text-[11.5px]" onClick={() => void testProvider(provider)} disabled={busy !== null}>
                            {busy === `test-${provider.id}` ? "Testing…" : "Test"}
                          </button>
                          <button className="btn-ghost px-2 py-1 text-[11.5px]" onClick={() => void discover(provider)} disabled={busy !== null}>Models</button>
                          <button className="btn-ghost px-2 py-1 text-[11.5px]" onClick={() => void patch(provider, { enabled: !provider.enabled })} disabled={busy !== null || !isAdmin}>
                            {provider.enabled ? "Disable" : "Enable"}
                          </button>
                          <button className="btn-ghost px-2 py-1 text-[11.5px]" onClick={() => void patch(provider, { is_default_generation: true })} disabled={busy !== null || !isAdmin || provider.is_default_generation}>
                            Make default
                          </button>
                          <button className="btn-danger px-2 py-1 text-[11.5px]" onClick={() => void remove(provider)} disabled={busy !== null || !isAdmin}>Delete</button>
                        </div>
                      </div>
                      {result ? (
                        <div className={`mt-2 rounded-md border p-2 text-[11.5px] ${result.ok ? "border-ok/25 bg-ok/[0.06] text-emerald-200" : "border-bad/30 bg-bad/[0.07] text-red-200"}`}>
                          {result.ok
                            ? `Connection OK${result.latency_ms ? ` · ${result.latency_ms} ms` : ""}${result.checked_at ? ` · ${humanDateTime(result.checked_at)}` : ""}`
                            : `Connection failed: ${result.error ?? "unknown error"}`}
                          {result.models?.length ? <div className="mt-1 text-slate-400">{result.models.length} models available</div> : null}
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>

          {providers?.environment_providers?.length ? (
            <Card>
              <SectionTitle title="Environment providers" hint="Detected from environment variables. Read-only here; manage them in your deployment configuration." />
              <ul className="space-y-1.5">
                {providers.environment_providers.map((env) => (
                  <li key={env.id} className="flex items-center justify-between text-[12px]">
                    <span className="text-slate-300">{env.label} <span className="text-slate-500">({env.kind})</span></span>
                    <Badge tone={env.api_key_set ? "ok" : "warn"}>{env.api_key_set ? "key present" : "no key"}</Badge>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}
        </div>

        <div className="space-y-4">
          <Card>
            <SectionTitle title="Secure configuration rules" />
            <ul className="space-y-2 text-[11.5px] leading-relaxed text-slate-400">
              <li className="flex gap-2"><Icon name="lock" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" /> API keys are encrypted with a workspace key and never returned in responses.</li>
              <li className="flex gap-2"><Icon name="shield" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-info" /> Base URLs are validated; private ranges and cloud metadata endpoints are blocked.</li>
              <li className="flex gap-2"><Icon name="alert" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" /> Errors and logs are scrubbed of key-like strings before storage.</li>
              <li className="flex gap-2"><Icon name="check" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ok" /> Only administrators may create, test or delete provider configurations.</li>
            </ul>
          </Card>

          <Card>
            <SectionTitle title="Usage & cost" hint="Recorded per request; local/offline operations cost nothing." />
            {usage ? (
              <div className="space-y-2 text-[12px]">
                <div className="flex justify-between"><span className="text-slate-400">Requests</span><span className="text-slate-200">{usage.total_requests ?? 0}</span></div>
                <div className="flex justify-between"><span className="text-slate-400">Prompt tokens</span><span className="text-slate-200">{usage.total_prompt_tokens ?? 0}</span></div>
                <div className="flex justify-between"><span className="text-slate-400">Completion tokens</span><span className="text-slate-200">{usage.total_completion_tokens ?? 0}</span></div>
                <div className="flex justify-between"><span className="text-slate-400">Estimated cost</span><span className="text-slate-200">${(usage.estimated_cost_usd ?? 0).toFixed(4)}</span></div>
                {usage.recent?.length ? (
                  <div className="mt-3 border-t border-white/[0.06] pt-2">
                    <div className="label mb-1">Recent requests</div>
                    <ul className="space-y-1">
                      {usage.recent.slice(0, 6).map((entry) => (
                        <li key={entry.id} className="flex items-center justify-between gap-2 text-[11px]">
                          <span className="text-slate-400">{entry.operation}</span>
                          <span className={entry.success ? "text-emerald-300" : "text-red-300"}>{entry.success ? "ok" : "failed"}</span>
                          <span className="text-slate-500">{entry.latency_ms ?? 0} ms</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            ) : (
              <p className="text-[12px] text-slate-500">No usage recorded yet.</p>
            )}
          </Card>

          <Card>
            <SectionTitle title="Environment reference" />
            <ul className="space-y-1 text-[11px] text-slate-400">
              {[
                ["EGA_DATABASE_URL", "SQLAlchemy URL (SQLite default)"],
                ["EGA_JWT_SECRET", "Signing secret for sessions"],
                ["EGA_PROVIDER_ENCRYPTION_KEY", "Fernet key for stored API keys"],
                ["EGA_ALLOW_LOCAL_PROVIDER_URLS", "Permit localhost endpoints (Ollama)"],
                ["EGA_SEED_DEMO_DATA", "Seed the synthetic workspace"],
                ["EGA_MAX_UPLOAD_MB", "Upload size ceiling"],
              ].map(([name, description]) => (
                <li key={name} className="flex items-baseline justify-between gap-3 border-b border-white/[0.05] py-1">
                  <code className="mono text-slate-300">{name}</code>
                  <span className="text-right text-slate-500">{description}</span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>

      <Modal open={open} title="Add provider" onClose={() => setOpen(false)} width="max-w-2xl">
        <form onSubmit={createProvider} className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Name">
              <input className="input" required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Production OpenAI" />
            </Field>
            <Field label="Kind">
              <select className="input" value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })}>
                {KINDS.map((kind) => <option key={kind.value} value={kind.value}>{kind.label}</option>)}
              </select>
            </Field>
            <Field label="Base URL" hint="Leave empty for the provider default. Localhost requires EGA_ALLOW_LOCAL_PROVIDER_URLS=true.">
              <input className="input" value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} placeholder="https://api.openai.com/v1" />
            </Field>
            <Field label="API key" hint="Stored encrypted; never logged or returned.">
              <input className="input" type="password" value={form.api_key} onChange={(event) => setForm({ ...form, api_key: event.target.value })} placeholder="sk-…" autoComplete="off" />
            </Field>
            <Field label="Generation model">
              <input className="input" value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} placeholder="gpt-4o-mini" />
            </Field>
            <Field label="Embedding model" hint="Optional; deterministic local embeddings are used otherwise.">
              <input className="input" value={form.embedding_model} onChange={(event) => setForm({ ...form, embedding_model: event.target.value })} placeholder="text-embedding-3-small" />
            </Field>
            <Field label="Rerank model">
              <input className="input" value={form.rerank_model} onChange={(event) => setForm({ ...form, rerank_model: event.target.value })} placeholder="optional" />
            </Field>
          </div>
          <div className="flex items-center gap-4 text-[12px] text-slate-300">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={form.is_default_generation} onChange={(event) => setForm({ ...form, is_default_generation: event.target.checked })} />
              Default for generation
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={form.is_default_embedding} onChange={(event) => setForm({ ...form, is_default_embedding: event.target.checked })} />
              Default for embeddings
            </label>
          </div>
          {error ? <ErrorBox message={error} /> : null}
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
            <button type="submit" className="btn-primary" disabled={busy === "create"}>{busy === "create" ? "Saving…" : "Save provider"}</button>
          </div>
        </form>
      </Modal>
    </div>
  );
}

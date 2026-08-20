"use client";

/**
 * Thin fetch wrapper around the FastAPI backend.
 *
 * The browser talks to the same origin that serves the static export (FastAPI mounts the
 * export at `/`). For `next dev`, set NEXT_PUBLIC_API_BASE=http://localhost:8000.
 */
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";
const TOKEN_KEY = "ega.token";
const USER_KEY = "ega.user";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setSession(token: string, user: unknown) {
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
}

export function cachedUser<T>(): T | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          const loc = Array.isArray((item as { loc?: unknown[] }).loc)
            ? (item as { loc: unknown[] }).loc.slice(1).join(".")
            : "";
          return `${loc ? loc + ": " : ""}${String((item as { msg: unknown }).msg)}`;
        }
        return JSON.stringify(item);
      })
      .join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return "Unexpected error";
}

type RequestOptions = {
  method?: string;
  body?: unknown;
  form?: FormData;
  signal?: AbortSignal;
};

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  const response = await fetch(`${API_BASE}${path}`, {
    method: options.method ?? "GET",
    headers,
    body,
    signal: options.signal,
    cache: "no-store",
  });
  if (response.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth/login")) {
    clearSession();
    window.location.href = "/login/";
    throw new ApiError(401, "Session expired");
  }
  const contentType = response.headers.get("content-type") ?? "";
  if (!response.ok) {
    let detail: unknown = response.statusText;
    if (contentType.includes("application/json")) {
      try {
        const payload = await response.json();
        detail = payload.detail ?? payload;
      } catch {
        detail = response.statusText;
      }
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  if (!contentType.includes("application/json")) return (await response.text()) as unknown as T;
  return (await response.json()) as T;
}

export async function fileObjectUrl(path: string): Promise<string> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${API_BASE}${path}`, { headers });
  if (!response.ok) throw new ApiError(response.status, await response.text());
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

export async function download(path: string, filename: string) {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${API_BASE}${path}`, { headers });
  if (!response.ok) throw new ApiError(response.status, await response.text());
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

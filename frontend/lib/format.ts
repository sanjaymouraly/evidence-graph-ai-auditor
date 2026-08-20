export function titleCase(value?: string | null): string {
  if (!value) return "";
  return value
    .replace(/[_-]+/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function humanDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value ?? "—";
  return date.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

export function humanDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value ?? "—";
  return date.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function relativeTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const diff = Date.now() - date.getTime();
  const minutes = Math.round(diff / 60000);
  if (Math.abs(minutes) < 1) return "just now";
  if (Math.abs(minutes) < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (Math.abs(days) < 30) return `${days}d ago`;
  return humanDate(value);
}

export function money(value?: number | null, currency?: string | null): string {
  if (value === null || value === undefined) return "—";
  const formatted = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value);
  return currency ? `${currency} ${formatted}` : formatted;
}

export function percent(value?: number | null, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function bytes(value?: number | null): string {
  if (!value) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} kB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function shortId(value?: string | null, length = 8): string {
  if (!value) return "—";
  return value.slice(0, length);
}

export function locatorText(span?: {
  type?: string;
  page?: number | null;
  sheet?: string | null;
  cell_range?: string | null;
  paragraph_index?: number | null;
  char_start?: number | null;
  char_end?: number | null;
  section_label?: string | null;
} | null): string {
  if (!span) return "no locator";
  if (span.page) return `page ${span.page}`;
  if (span.sheet) return `${span.sheet}${span.cell_range ? `!${span.cell_range}` : ""}`;
  if (span.paragraph_index !== null && span.paragraph_index !== undefined) return `¶ ${span.paragraph_index + 1}`;
  if (span.section_label) return `email ${span.section_label}`;
  if (span.char_start !== null && span.char_start !== undefined) {
    const end = span.char_end ?? span.char_start + 1;
    return `chars ${span.char_start}–${end}`;
  }
  return "span";
}

export function statusTone(status?: string | null): "ok" | "warn" | "bad" | "info" | "neutral" {
  switch ((status ?? "").toUpperCase()) {
    case "APPROVED":
    case "SUFFICIENTLY_SUPPORTED":
    case "VERIFIED":
    case "PASS":
    case "CONFIRMED":
    case "OK":
      return "ok";
    case "REVIEW_REQUIRED":
    case "POSSIBLE":
    case "WARN":
    case "UNCERTAIN":
    case "DRAFT":
      return "warn";
    case "CONTRADICTED":
    case "REJECTED":
    case "EVIDENCE_INCOMPLETE":
    case "FAIL":
    case "UNSUPPORTED":
    case "DISPUTED":
      return "bad";
    case "RUNNING":
    case "PENDING":
      return "info";
    default:
      return "neutral";
  }
}

export function pretty(value: unknown): string {
  if (value === undefined) return "—";
  try {
    const text = JSON.stringify(value, null, 2);
    return text === undefined ? String(value) : text;
  } catch {
    return String(value);
  }
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

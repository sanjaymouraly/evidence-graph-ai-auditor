import { describe, expect, it } from "vitest";
import {
  bytes, clamp, humanDate, humanDateTime, locatorText, money, percent, pretty, relativeTime, shortId,
  statusTone, titleCase,
} from "../lib/format";

describe("titleCase", () => {
  it("formats enum-ish values for display", () => {
    expect(titleCase("REVIEW_REQUIRED")).toBe("Review Required");
    expect(titleCase("policy_compliance")).toBe("Policy Compliance");
    expect(titleCase("")).toBe("");
    expect(titleCase(null)).toBe("");
  });
});

describe("dates", () => {
  it("renders date-only values without a time zone shift", () => {
    expect(humanDate("2025-09-01")).toBe("01 Sept 2025");
    expect(humanDate(null)).toBe("—");
  });

  it("round-trips timestamps and tolerates junk", () => {
    expect(humanDateTime("2025-09-01T10:30:00Z")).toMatch(/2025/);
    expect(humanDateTime("not-a-date")).toBe("not-a-date");
    expect(humanDateTime(undefined)).toBe("—");
  });

  it("describes relative time in both directions", () => {
    const now = Date.now();
    expect(relativeTime(new Date(now - 90_000).toISOString())).toMatch(/m ago|just now/);
    expect(relativeTime(new Date(now + 3 * 86_400_000).toISOString())).toMatch(/d/);
  });
});

describe("numbers and identifiers", () => {
  it("formats money with and without a currency", () => {
    expect(money(89400, "USD")).toBe("USD 89,400");
    expect(money(1234.5)).toBe("1,234.5");
    expect(money(null)).toBe("—");
    expect(money(undefined, "USD")).toBe("—");
  });

  it("formats percentages from 0-1 fractions", () => {
    expect(percent(1)).toBe("100.0%");
    expect(percent(0.8167)).toBe("81.7%");
    expect(percent(0.8167, 0)).toBe("82%");
    expect(percent(Number.NaN)).toBe("—");
  });

  it("formats byte counts", () => {
    expect(bytes(512)).toBe("512 B");
    expect(bytes(2048)).toBe("2.0 kB");
    expect(bytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(bytes(0)).toBe("—");
  });

  it("truncates identifiers deterministically", () => {
    expect(shortId("0123456789abcdef")).toBe("01234567");
    expect(shortId("0123456789abcdef", 4)).toBe("0123");
    expect(shortId(null)).toBe("—");
  });

  it("clamps values into range", () => {
    expect(clamp(5, 0, 1)).toBe(1);
    expect(clamp(-5, 0, 1)).toBe(0);
    expect(clamp(0.4, 0, 1)).toBe(0.4);
  });
});

describe("statusTone", () => {
  it("maps gate and workflow states onto badge tones", () => {
    expect(statusTone("APPROVED")).toBe("ok");
    expect(statusTone("SUFFICIENTLY_SUPPORTED")).toBe("ok");
    expect(statusTone("REVIEW_REQUIRED")).toBe("warn");
    expect(statusTone("EVIDENCE_INCOMPLETE")).toBe("bad");
    expect(statusTone("CONTRADICTED")).toBe("bad");
    expect(statusTone("REJECTED")).toBe("bad");
    expect(statusTone("DRAFT")).toBe("warn");
    expect(statusTone("SUPERSEDED")).toBe("neutral");
    expect(statusTone(null)).toBe("neutral");
  });
});

describe("locatorText", () => {
  it("describes every provenance locator type", () => {
    expect(locatorText({ type: "page", page: 3 })).toBe("page 3");
    expect(locatorText({ type: "paragraph", paragraph_index: 7 })).toBe("¶ 8");
    expect(locatorText({ type: "cell", sheet: "Budget", cell_range: "B4:D9" })).toBe("Budget!B4:D9");
    expect(locatorText({ type: "text", char_start: 10, char_end: 42 })).toBe("chars 10–42");
    expect(locatorText({ type: "email_section", section_label: "body" })).toBe("email body");
    expect(locatorText(null)).toBe("no locator");
  });
});

describe("pretty", () => {
  it("serialises structured values for the raw-data panels", () => {
    expect(pretty({ a: 1 })).toContain('"a": 1');
    expect(pretty(undefined)).toBe("—");
    expect(pretty("text")).toBe('"text"');
  });
});

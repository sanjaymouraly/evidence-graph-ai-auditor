import { describe, expect, it } from "vitest";
import { ROLE_PERMISSIONS, can } from "../lib/permissions";

/**
 * These assertions pin the UI-side RBAC matrix. The backend enforces the same permissions;
 * the UI copy only decides which controls are offered. A regression here is a usability or
 * information-disclosure bug, so the matrix is tested explicitly.
 */
describe("can()", () => {
  it("denies everything to anonymous users", () => {
    expect(can(null, "decision:read")).toBe(false);
    expect(can(undefined, "document:read")).toBe(false);
  });

  it("grants admins every permission via the wildcard", () => {
    const admin = { role: "admin" };
    for (const permission of ["decision:approve", "provider:write", "audit:read", "anything:at:all"]) {
      expect(can(admin, permission)).toBe(true);
    }
  });

  it("lets reviewers approve decisions but not administer providers", () => {
    const reviewer = { role: "reviewer" };
    expect(can(reviewer, "decision:approve")).toBe(true);
    expect(can(reviewer, "contradiction:review")).toBe(true);
    expect(can(reviewer, "provider:read")).toBe(true);
    expect(can(reviewer, "provider:write")).toBe(false);
    expect(can(reviewer, "provider:test")).toBe(false);
    expect(can(reviewer, "benchmark:run")).toBe(false);
  });

  it("lets auditors ingest documents but not approve decisions", () => {
    const auditor = { role: "auditor" };
    expect(can(auditor, "document:write")).toBe(true);
    expect(can(auditor, "decision:submit")).toBe(true);
    expect(can(auditor, "decision:approve")).toBe(false);
    expect(can(auditor, "provider:write")).toBe(false);
  });

  it("keeps viewers read-only", () => {
    const viewer = { role: "viewer" };
    const granted = ROLE_PERMISSIONS.viewer;
    expect(granted.every((permission) => permission.endsWith(":read"))).toBe(true);
    expect(can(viewer, "document:read")).toBe(true);
    expect(can(viewer, "document:write")).toBe(false);
    expect(can(viewer, "qa:ask")).toBe(false);
  });

  it("fails closed for an unknown role", () => {
    expect(can({ role: "intern" }, "document:read")).toBe(false);
  });
});

/**
 * Role-based access control matrix, mirrored by the backend `require_permission` checks.
 *
 * Kept in a dependency-free module so it can be unit tested in Node and reused anywhere in
 * the UI. The backend is authoritative: hiding a control is a UX affordance, not the
 * security boundary.
 */
export const ROLE_PERMISSIONS: Record<string, string[]> = {
  admin: ["*"],
  reviewer: [
    "document:read", "claim:read", "claim:review", "contradiction:read", "contradiction:review",
    "decision:read", "decision:write", "decision:submit", "decision:approve", "analysis:run",
    "report:read", "report:generate", "qa:ask", "benchmark:read", "audit:read", "provider:read",
  ],
  auditor: [
    "document:read", "document:write", "claim:read", "claim:review", "contradiction:read",
    "contradiction:review", "decision:read", "decision:write", "decision:submit", "analysis:run",
    "report:read", "report:generate", "qa:ask", "benchmark:read", "audit:read", "provider:read",
  ],
  viewer: ["document:read", "claim:read", "contradiction:read", "decision:read", "report:read", "audit:read"],
};

/** Any object carrying a role. Unknown roles are intentionally accepted and fail closed. */
export type PermissionSubject = { role: string };

export function can(user: PermissionSubject | null | undefined, permission: string): boolean {
  if (!user) return false;
  const granted = ROLE_PERMISSIONS[user.role] ?? [];
  return granted.includes("*") || granted.includes(permission);
}

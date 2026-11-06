import { apiFetch } from "@/lib/api";

export type Limits = Record<string, number | null>;

// Real, currently-configured role -> spending-limit policy (GET /procurement/approval-limits), fetched once
// and cached, never hardcoded, so the chat only offers an approval action that can actually succeed.
let cache: Promise<Limits> | null = null;

export function getApprovalLimits(): Promise<Limits> {
  if (!cache) {
    cache = apiFetch<{ limits: Limits }>("/procurement/approval-limits")
      .then((d) => d.limits)
      .catch((e) => {
        cache = null;
        throw e;
      });
  }
  return cache;
}

export function resetApprovalLimits(): void {
  cache = null;
}

/** The lowest-authority role whose limit covers this amount (null limit = no cap). */
export function pickQualifyingRole(limits: Limits, amount: number): string | null {
  const qualifying = Object.entries(limits)
    .filter(([, limit]) => limit === null || amount <= limit)
    .sort(([, a], [, b]) => (a === null ? Infinity : a) - (b === null ? Infinity : b));
  return qualifying.length ? qualifying[0][0] : null;
}

export const num = (n: number | null | undefined) => (n ?? 0).toLocaleString();

/** Costs here are tiny (a chat message is a hundredth of a cent): never round them away. */
export const usd = (n: number | null | undefined) => {
  const v = n ?? 0;
  return "$" + v.toFixed(!v ? 2 : v < 0.01 ? 7 : v < 1 ? 4 : 2);
};

export const seconds = (n: number | null | undefined) =>
  n == null ? "—" : n < 1 ? `${Math.round(n * 1000)} ms` : `${n.toFixed(2)} s`;

export const compact = (n: number) =>
  new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(n);

export function timeAgo(ts: number): string {
  const s = Math.max(1, Math.round((Date.now() - ts) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

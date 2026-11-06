import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api";
import type { Kind, RecentItem, Status, Summary } from "./types";

interface RecentEntry {
  stamp: string;
  items: RecentItem[];
}

/**
 * Loads the dashboard sequentially (status, then summary, then the active tab's table): Langfuse
 * rate-limits bursts. Each `recent` kind is fetched once per (range, refresh); switching tabs reuses it.
 * A failed refresh keeps the last good data on screen and raises `error`.
 */
export function useObservability(days: number, tab: Kind) {
  const [status, setStatus] = useState<Status | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [recent, setRecent] = useState<Partial<Record<Kind, RecentEntry>>>({});
  const [loading, setLoading] = useState(true);
  const [recentLoading, setRecentLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [nonce, setNonce] = useState(0);

  const stamp = `${days}:${nonce}`;
  const stampRef = useRef(stamp);
  stampRef.current = stamp;
  const tabRef = useRef(tab);
  tabRef.current = tab;
  const loadingRef = useRef(true);
  const inflight = useRef<Set<string>>(new Set());

  const fetchRecent = useCallback(async (kind: Kind, st: string, d: number) => {
    const key = `${st}:${kind}`;
    if (inflight.current.has(key)) return;
    inflight.current.add(key);
    setRecentLoading(true);
    try {
      const r = await apiFetch<{ items: RecentItem[] }>(`/observability/recent?days=${d}&kind=${kind}&limit=100`);
      if (stampRef.current === st) setRecent((p) => ({ ...p, [kind]: { stamp: st, items: r.items } }));
    } catch (e) {
      if (stampRef.current === st) setError((e as Error).message);
    } finally {
      inflight.current.delete(key);
      setRecentLoading(inflight.current.size > 0);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    loadingRef.current = true;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        try {
          const st = await apiFetch<Status>("/observability/status");
          if (!cancelled) setStatus(st);
        } catch (e) {
          if (!cancelled) setError((e as Error).message);
        }
        const s = await apiFetch<Summary>(`/observability/summary?days=${days}`);
        if (cancelled) return;
        setSummary(s);
        setUpdatedAt(Date.now());
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) {
          loadingRef.current = false;
          setLoading(false);
        }
      }
      if (!cancelled) await fetchRecent(tabRef.current, `${days}:${nonce}`, days);
    })();
    return () => {
      cancelled = true;
    };
  }, [days, nonce, fetchRecent]);

  // Switching tabs: fetch that kind only if it has not been loaded for this range/refresh.
  useEffect(() => {
    if (loadingRef.current) return;
    if (recent[tab]?.stamp !== stamp) void fetchRecent(tab, stamp, days);
  }, [tab, stamp, days, recent, fetchRecent]);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);
  const entry = recent[tab];
  return {
    status,
    summary,
    items: entry?.items ?? null,
    itemsStale: !!entry && entry.stamp !== stamp,
    loading,
    recentLoading,
    error,
    dismissError: () => setError(null),
    updatedAt,
    refresh,
  };
}

/** Theme-aware colours for recharts, read from the CSS variables and refreshed when the theme flips. */
export interface ChartTheme {
  series: string[];
  grid: string;
  axis: string;
  surface: string;
  border: string;
  text: string;
  dim: string;
}

function readTheme(): ChartTheme {
  const cs = getComputedStyle(document.documentElement);
  const g = (n: string) => cs.getPropertyValue(n).trim();
  return {
    series: ["--chart-1", "--chart-2", "--chart-3", "--chart-4", "--chart-5"].map(g),
    grid: g("--chart-grid"),
    axis: g("--text-faint"),
    surface: g("--surface-2"),
    border: g("--border-strong"),
    text: g("--text"),
    dim: g("--text-dim"),
  };
}

export function useChartTheme(): ChartTheme {
  const [theme, setTheme] = useState<ChartTheme>(readTheme);
  useEffect(() => {
    const mo = new MutationObserver(() => setTheme(readTheme()));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
  }, []);
  return theme;
}

export function useCountUp(target: number, duration = 700): number {
  const [value, setValue] = useState(0);
  const prev = useRef(0);
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || prev.current === target) {
      prev.current = target;
      setValue(target);
      return;
    }
    const from = prev.current;
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      setValue(from + (target - from) * eased);
      if (p < 1) raf = requestAnimationFrame(tick);
      else prev.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);
  return value;
}

/** Re-renders every second so "updated Xs ago" stays honest. */
export function useTick(ms = 1000) {
  const [, set] = useState(0);
  useEffect(() => {
    const id = setInterval(() => set((n) => n + 1), ms);
    return () => clearInterval(id);
  }, [ms]);
}

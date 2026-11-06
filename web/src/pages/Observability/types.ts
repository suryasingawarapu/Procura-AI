export type Kind = "traces" | "sessions" | "generations" | "errors";

export interface Status {
  configured: boolean;
  enabled: boolean;
  slug: string;
  environment: string;
  base_url: string;
  capture_content: boolean;
  reachable: boolean;
  project: string | null;
}

export interface Totals {
  observations: number;
  cost: number;
  tokens: number;
  avg_latency_s: number;
  errors: number;
  warnings: number;
}

export interface Summary {
  days: number;
  truncated: boolean;
  totals: Totals;
  levels: Record<string, number>;
  types: { type: string; count: number; cost: number; tokens: number }[];
  agents: { name: string; count: number; cost: number; tokens: number }[];
  steps: { name: string; count: number; cost: number; tokens: number; avg_latency_s: number }[];
  daily: { day: string; count: number; cost: number; tokens: number }[];
  users: { user_id: string; count: number; cost: number }[];
}

export interface ActivityItem {
  id: string;
  trace_id: string | null;
  name: string | null;
  type: string | null;
  level: string | null;
  status_message: string | null;
  user_id: string | null;
  session_id: string | null;
  environment: string | null;
  model: string | null;
  latency_s: number | null;
  total_cost: number | null;
  tokens: number | null;
  start_time: string | null;
  input: string | null;
  output: string | null;
}

export interface SessionItem {
  session_id: string;
  observations: number;
  cost: number;
  tokens: number;
  avg_latency_s: number;
}

export type RecentItem = ActivityItem | SessionItem;

export const isSession = (i: RecentItem): i is SessionItem => "observations" in i;

export const RANGES = [
  { days: 1, label: "24h" },
  { days: 7, label: "7d" },
  { days: 30, label: "30d" },
  { days: 90, label: "90d" },
] as const;

export const TABS: { kind: Kind; label: string }[] = [
  { kind: "traces", label: "Traces" },
  { kind: "sessions", label: "Sessions" },
  { kind: "generations", label: "Model calls" },
  { kind: "errors", label: "Errors & warnings" },
];

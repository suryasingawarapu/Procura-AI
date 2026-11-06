import clsx from "clsx";
import { useMemo, useState, type ReactNode } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { compact, num, usd } from "@/lib/format";
import { useChartTheme, type ChartTheme } from "./hooks";
import type { Summary } from "./types";

type Metric = "count" | "tokens" | "cost";

const metricLabel: Record<Metric, string> = { count: "Calls", tokens: "Tokens", cost: "Cost" };
const fmtMetric = (m: Metric, v: number) => (m === "cost" ? usd(v) : num(Math.round(v)));
const axisMetric = (m: Metric, v: number) => (m === "cost" ? "$" + Number(v.toPrecision(2)) : compact(v));

export function Segmented<T extends string | number>({
  value,
  onChange,
  options,
  label,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode }[];
  label: string;
}) {
  return (
    <div className="obs-seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          className={clsx("obs-seg__btn", o.value === value && "obs-seg__btn--on")}
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Tip({ active, payload, label, fmt }: { active?: boolean; payload?: any[]; label?: string; fmt: (v: number) => string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="obs-tip">
      {label ? <div className="obs-tip__title">{label}</div> : null}
      {payload.map((p, i) => (
        <div key={i} className="obs-tip__row">
          <span className="obs-tip__dot" style={{ background: p.color || p.payload?.fill || p.fill }} />
          <span>{p.name ?? p.dataKey}</span>
          <strong>{fmt(Number(p.value))}</strong>
        </div>
      ))}
    </div>
  );
}

const shortDay = (d: string) => {
  const dt = new Date(d + "T00:00:00");
  return Number.isNaN(dt.getTime()) ? d : dt.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

export function ActivityChart({ summary }: { summary: Summary }) {
  const theme = useChartTheme();
  const [metric, setMetric] = useState<Metric>("count");
  const data = useMemo(() => summary.daily.map((d) => ({ ...d, label: shortDay(d.day) })), [summary.daily]);
  const color = metric === "cost" ? theme.series[2] : metric === "tokens" ? theme.series[3] : theme.series[0];
  return (
    <section className="obs-panel card" aria-label="Activity per day">
      <header className="obs-panel__head">
        <div>
          <h2>Activity per day</h2>
          <p>Observations, tokens and cost over the selected range</p>
        </div>
        <Segmented
          label="Metric"
          value={metric}
          onChange={setMetric}
          options={(["count", "tokens", "cost"] as Metric[]).map((m) => ({ value: m, label: metricLabel[m] }))}
        />
      </header>
      <div className="obs-chart obs-chart--lg">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="obs-act" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.4} />
                <stop offset="100%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={theme.grid} vertical={false} />
            <XAxis dataKey="label" tick={{ fill: theme.axis, fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={20} />
            <YAxis
              tick={{ fill: theme.axis, fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={metric === "cost" ? 56 : 40}
              allowDecimals={false}
              tickFormatter={(v) => axisMetric(metric, v)}
            />
            <Tooltip cursor={{ stroke: theme.border }} content={<Tip fmt={(v) => fmtMetric(metric, v)} />} />
            <Area type="monotone" dataKey={metric} name={metricLabel[metric]} stroke={color} strokeWidth={2} fill="url(#obs-act)" animationDuration={500} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

const shortName = (n: string) => (n.includes(" - ") ? n.split(" - ").slice(1).join(" - ") : n);
const clip = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

export function CostChart({ summary }: { summary: Summary }) {
  const theme = useChartTheme();
  const [by, setBy] = useState<"step" | "agent">("step");
  const rows = by === "step" ? summary.steps : summary.agents;
  const anyCost = rows.some((r) => r.cost > 0);
  const metric: Metric = anyCost ? "cost" : "count";
  const data = useMemo(
    () =>
      [...rows]
        .filter((r) => r[metric] > 0)
        .sort((a, b) => b[metric] - a[metric])
        .slice(0, 8)
        .map((r) => ({ name: by === "step" ? shortName(r.name) : r.name, full: r.name, value: r[metric] })),
    [rows, metric, by],
  );
  return (
    <section className="obs-panel card" aria-label="Cost by agent and step">
      <header className="obs-panel__head">
        <div>
          <h2>{anyCost ? "Cost" : "Calls"} by {by === "step" ? "step" : "agent"}</h2>
          <p>{anyCost ? "Cost is attributed by the part of the name before ' - '" : "No priced calls yet, showing call counts"}</p>
        </div>
        <Segmented
          label="Group by"
          value={by}
          onChange={setBy}
          options={[
            { value: "step", label: "Step" },
            { value: "agent", label: "Agent" },
          ]}
        />
      </header>
      <div className="obs-chart" style={{ height: Math.max(180, data.length * 38 + 20) }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={theme.grid} horizontal={false} />
            <XAxis type="number" hide />
            <YAxis
              type="category"
              dataKey="name"
              width={132}
              tickLine={false}
              axisLine={false}
              tick={{ fill: theme.dim, fontSize: 12 }}
              tickFormatter={(v: string) => clip(v, 20)}
            />
            <Tooltip cursor={{ fill: theme.grid }} content={<Tip fmt={(v) => fmtMetric(metric, v)} />} />
            <Bar dataKey="value" name={metricLabel[metric]} radius={[0, 6, 6, 0]} barSize={16} animationDuration={500}>
              {data.map((_, i) => (
                <Cell key={i} fill={theme.series[i % theme.series.length]} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

export function TypesDonut({ summary }: { summary: Summary }) {
  const theme: ChartTheme = useChartTheme();
  const total = summary.types.reduce((a, t) => a + t.count, 0);
  const data = summary.types.map((t) => ({ name: t.type.charAt(0) + t.type.slice(1).toLowerCase(), value: t.count }));
  return (
    <section className="obs-panel card" aria-label="Observation types">
      <header className="obs-panel__head">
        <div>
          <h2>Observation types</h2>
          <p>Spans, model generations and tool calls</p>
        </div>
      </header>
      <div className="obs-donut">
        <div className="obs-donut__chart">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Tooltip content={<Tip fmt={(v) => num(v)} />} />
              <Pie data={data} dataKey="value" nameKey="name" innerRadius="64%" outerRadius="92%" paddingAngle={2} stroke="none" animationDuration={500}>
                {data.map((_, i) => (
                  <Cell key={i} fill={theme.series[i % theme.series.length]} />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
          <div className="obs-donut__center">
            <strong>{num(total)}</strong>
            <span>total</span>
          </div>
        </div>
        <ul className="obs-legend">
          {data.map((d, i) => (
            <li key={d.name}>
              <span className="obs-tip__dot" style={{ background: theme.series[i % theme.series.length] }} />
              <span className="obs-legend__name">{d.name}</span>
              <strong>{num(d.value)}</strong>
              <span className="obs-legend__pct">{total ? Math.round((d.value / total) * 100) : 0}%</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

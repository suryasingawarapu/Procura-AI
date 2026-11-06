import clsx from "clsx";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { useMemo, useState } from "react";
import { num, seconds, usd } from "@/lib/format";
import type { Summary } from "./types";

type StepKey = "name" | "count" | "tokens" | "cost" | "avg_latency_s";

const COLS: { key: StepKey; label: string; numeric?: boolean }[] = [
  { key: "name", label: "Step" },
  { key: "count", label: "Calls", numeric: true },
  { key: "tokens", label: "Tokens", numeric: true },
  { key: "cost", label: "Cost", numeric: true },
  { key: "avg_latency_s", label: "Avg latency", numeric: true },
];

export function StepsTable({ summary }: { summary: Summary }) {
  const [sort, setSort] = useState<{ key: StepKey; dir: 1 | -1 }>({ key: "cost", dir: -1 });
  const rows = useMemo(() => {
    const r = [...summary.steps];
    r.sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      const c = typeof av === "string" ? av.localeCompare(String(bv)) : (av as number) - (bv as number);
      return (c || b.count - a.count) * (typeof av === "string" ? sort.dir * -1 : sort.dir);
    });
    return r;
  }, [summary.steps, sort]);
  const maxCount = Math.max(1, ...summary.steps.map((s) => s.count));
  const maxCost = Math.max(0, ...summary.steps.map((s) => s.cost));
  const toggle = (key: StepKey) => setSort((s) => (s.key === key ? { key, dir: (s.dir * -1) as 1 | -1 } : { key, dir: key === "name" ? 1 : -1 }));

  return (
    <section className="obs-panel card" aria-label="Steps">
      <header className="obs-panel__head">
        <div>
          <h2>Steps</h2>
          <p>Every traced step, with usage and latency. Click a column to sort.</p>
        </div>
      </header>
      <div className="obs-scroll">
        <table className="obs-table">
          <thead>
            <tr>
              {COLS.map((c) => {
                const on = sort.key === c.key;
                return (
                  <th key={c.key} className={clsx(c.numeric && "num")} aria-sort={on ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
                    <button type="button" className="obs-th" onClick={() => toggle(c.key)}>
                      {c.label}
                      {on ? sort.dir === 1 ? <ArrowUp size={12} /> : <ArrowDown size={12} /> : <ChevronsUpDown size={12} className="obs-th__idle" />}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name}>
                <td className="obs-cell-name" title={r.name}>{r.name}</td>
                <td className="num">
                  <span className="obs-inbar">
                    <span className="obs-inbar__fill" style={{ width: `${(r.count / maxCount) * 100}%` }} />
                    <span className="obs-inbar__val">{num(r.count)}</span>
                  </span>
                </td>
                <td className="num">{num(r.tokens)}</td>
                <td className="num">
                  <span className="obs-inbar obs-inbar--alt">
                    <span className="obs-inbar__fill" style={{ width: maxCost ? `${(r.cost / maxCost) * 100}%` : "0%" }} />
                    <span className="obs-inbar__val">{usd(r.cost)}</span>
                  </span>
                </td>
                <td className="num">{seconds(r.avg_latency_s)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function UsersCard({ summary }: { summary: Summary }) {
  const max = Math.max(1, ...summary.users.map((u) => u.count));
  return (
    <section className="obs-panel card" aria-label="Users">
      <header className="obs-panel__head">
        <div>
          <h2>Users</h2>
          <p>Who generated the activity</p>
        </div>
      </header>
      {summary.users.length === 0 ? (
        <p className="obs-empty-sm">No users yet.</p>
      ) : (
        <ul className="obs-users">
          {summary.users.map((u) => (
            <li key={u.user_id}>
              <div className="obs-users__row">
                <span className="obs-users__id" title={u.user_id}>{u.user_id === "(anonymous)" ? "anonymous" : u.user_id}</span>
                <span className="obs-users__num">{num(u.count)}</span>
                <span className="obs-users__cost">{usd(u.cost)}</span>
              </div>
              <div className="obs-users__bar"><span style={{ width: `${(u.count / max) * 100}%` }} /></div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

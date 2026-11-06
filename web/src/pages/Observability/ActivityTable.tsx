import clsx from "clsx";
import { Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Badge, type Tone } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Skeleton } from "@/components/Skeleton";
import { num, seconds, timeAgo, usd } from "@/lib/format";
import { CopyIcon } from "./CopyButton";
import { TABS, isSession, type ActivityItem, type Kind, type RecentItem, type SessionItem } from "./types";

const PAGE = 25;

export const levelTone = (l: string | null): Tone => (l === "ERROR" ? "danger" : l === "WARNING" ? "warning" : "neutral");

export function LevelBadge({ level }: { level: string | null }) {
  return (
    <Badge tone={levelTone(level)} dot={level === "ERROR" || level === "WARNING"}>
      {(level ?? "DEFAULT").toLowerCase()}
    </Badge>
  );
}

export function SessionId({ id }: { id: string | null }) {
  if (!id) return <span className="obs-dim">—</span>;
  return (
    <span className="obs-sid" title={id}>
      <code>{id.length > 16 ? id.slice(0, 15) + "…" : id}</code>
      <CopyIcon text={id} />
    </span>
  );
}

const haystack = (i: RecentItem) =>
  isSession(i)
    ? i.session_id
    : [i.name, i.user_id, i.session_id, i.model, i.status_message, i.input, i.output, i.level].filter(Boolean).join(" ");

interface Props {
  kind: Kind;
  onKind: (k: Kind) => void;
  items: RecentItem[] | null;
  loading: boolean;
  stale: boolean;
  query: string;
  onQuery: (q: string) => void;
  onOpen: (i: RecentItem) => void;
}

export function ActivityTable({ kind, onKind, items, loading, stale, query, onQuery, onOpen }: Props) {
  const [limit, setLimit] = useState(PAGE);
  useEffect(() => setLimit(PAGE), [kind, query]);
  const q = query.trim().toLowerCase();
  const filtered = useMemo(() => (items ?? []).filter((i) => !q || haystack(i).toLowerCase().includes(q)), [items, q]);
  const shown = filtered.slice(0, limit);
  const open = (i: RecentItem) => onOpen(i);
  const keyOpen = (i: RecentItem) => (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open(i);
    }
  };
  const sessions = kind === "sessions";

  return (
    <section className="obs-panel card" aria-label="Recent activity">
      <header className="obs-panel__head obs-panel__head--wrap">
        <div role="tablist" aria-label="Activity" className="obs-tabs">
          {TABS.map((t) => (
            <button
              key={t.kind}
              role="tab"
              type="button"
              aria-selected={t.kind === kind}
              className={clsx("obs-tab", t.kind === kind && "obs-tab--on")}
              onClick={() => onKind(t.kind)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <label className="obs-search">
          <Search size={15} aria-hidden />
          <input value={query} onChange={(e) => onQuery(e.target.value)} placeholder="Filter by name, user, session, model" aria-label="Filter rows" />
          {query ? (
            <button type="button" aria-label="Clear filter" onClick={() => onQuery("")}>
              <X size={14} />
            </button>
          ) : null}
        </label>
      </header>

      <div className={clsx("obs-scroll", stale && "obs-stale")}>
        <table className="obs-table obs-table--rows">
          <thead>
            {sessions ? (
              <tr>
                <th>Session</th>
                <th className="num">Observations</th>
                <th className="num">Tokens</th>
                <th className="num">Cost</th>
                <th className="num">Avg latency</th>
              </tr>
            ) : (
              <tr>
                <th>Time</th>
                <th>Name</th>
                <th>Level</th>
                <th>User</th>
                <th>Session</th>
                <th className="num">Latency</th>
                <th className="num">Tokens</th>
                <th className="num">Cost</th>
              </tr>
            )}
          </thead>
          <tbody>
            {items === null
              ? Array.from({ length: 6 }).map((_, r) => (
                  <tr key={r}>
                    {Array.from({ length: sessions ? 5 : 8 }).map((__, c) => (
                      <td key={c}>
                        <Skeleton h={14} w={c === 1 ? "80%" : "60%"} />
                      </td>
                    ))}
                  </tr>
                ))
              : shown.map((i) =>
                  isSession(i) ? (
                    <SessionRow key={i.session_id} s={i} onOpen={open} onKey={keyOpen(i)} />
                  ) : (
                    <ActivityRow key={i.id} a={i} kind={kind} onOpen={open} onKey={keyOpen(i)} />
                  ),
                )}
          </tbody>
        </table>
        {items !== null && filtered.length === 0 ? (
          <div className="obs-empty">
            <strong>{q ? "Nothing matches that filter" : kind === "errors" ? "No errors or warnings" : "Nothing recorded in this range yet"}</strong>
            <p>{q ? "Try a different search term." : kind === "errors" ? "Failed calls appear here the moment they happen." : "Send a chat message, then refresh."}</p>
          </div>
        ) : null}
      </div>

      {items !== null && filtered.length > 0 ? (
        <footer className="obs-table-foot">
          <span>
            Showing {num(shown.length)} of {num(filtered.length)}
            {loading ? " · refreshing…" : ""}
          </span>
          {filtered.length > shown.length ? (
            <Button size="sm" onClick={() => setLimit((l) => l + PAGE)}>
              Show more
            </Button>
          ) : null}
        </footer>
      ) : null}
    </section>
  );
}

function ActivityRow({ a, kind, onOpen, onKey }: { a: ActivityItem; kind: Kind; onOpen: (i: RecentItem) => void; onKey: (e: React.KeyboardEvent) => void }) {
  const ts = a.start_time ? Date.parse(a.start_time) : NaN;
  return (
    <tr tabIndex={0} className="obs-row" onClick={() => onOpen(a)} onKeyDown={onKey}>
      <td className="obs-nowrap" title={a.start_time ?? ""}>{Number.isNaN(ts) ? "—" : timeAgo(ts)}</td>
      <td className="obs-cell-main">
        <div className="obs-cell-name" title={a.name ?? ""}>{a.name ?? "(unnamed)"}</div>
        {kind === "generations" && a.model ? <div className="obs-sub">{a.model}</div> : null}
        {kind === "errors" && a.status_message ? <div className="obs-sub obs-sub--err">{a.status_message}</div> : null}
        {kind === "traces" && a.input ? <div className="obs-sub">{a.input}</div> : null}
      </td>
      <td><LevelBadge level={a.level} /></td>
      <td className="obs-nowrap">{a.user_id ? a.user_id : <span className="obs-dim">anonymous</span>}</td>
      <td><SessionId id={a.session_id} /></td>
      <td className="num">{seconds(a.latency_s)}</td>
      <td className="num">{a.tokens ? num(a.tokens) : <span className="obs-dim">—</span>}</td>
      <td className="num">{a.total_cost ? usd(a.total_cost) : <span className="obs-dim">—</span>}</td>
    </tr>
  );
}

function SessionRow({ s, onOpen, onKey }: { s: SessionItem; onOpen: (i: RecentItem) => void; onKey: (e: React.KeyboardEvent) => void }) {
  return (
    <tr tabIndex={0} className="obs-row" onClick={() => onOpen(s)} onKeyDown={onKey}>
      <td><SessionId id={s.session_id} /></td>
      <td className="num">{num(s.observations)}</td>
      <td className="num">{num(s.tokens)}</td>
      <td className="num">{usd(s.cost)}</td>
      <td className="num">{seconds(s.avg_latency_s)}</td>
    </tr>
  );
}

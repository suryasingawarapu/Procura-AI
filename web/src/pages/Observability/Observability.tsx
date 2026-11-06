import { Activity, AlertTriangle, CircleDollarSign, Clock, ExternalLink, Hash, RefreshCw, TriangleAlert, X, Zap } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { NavBar } from "@/components/NavBar";
import { Skeleton } from "@/components/Skeleton";
import { compact, num, seconds, timeAgo, usd } from "@/lib/format";
import { ActivityTable } from "./ActivityTable";
import { ActivityChart, CostChart, Segmented, TypesDonut } from "./Charts";
import { useObservability, useTick } from "./hooks";
import { Kpi, KpiSkeleton } from "./Kpi";
import { RecordDrawer } from "./RecordDrawer";
import { StepsTable, UsersCard } from "./Tables";
import { RANGES, type Kind, type RecentItem } from "./types";
import "./observability.css";

const AUTO_REFRESH_MS = 30_000;

export default function Observability() {
  const [days, setDays] = useState<number>(7);
  const [tab, setTab] = useState<Kind>("traces");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<RecentItem | null>(null);
  const [auto, setAuto] = useState(false);
  const { status, summary, items, itemsStale, loading, recentLoading, error, dismissError, updatedAt, refresh } = useObservability(days, tab);
  useTick();

  const busy = loading || recentLoading;
  useEffect(() => {
    if (!auto) return;
    const id = setInterval(() => refresh(), AUTO_REFRESH_MS);
    return () => clearInterval(id);
  }, [auto, refresh]);

  const filterSession = useCallback((id: string) => {
    setSelected(null);
    setTab("traces");
    setQuery(id);
    document.getElementById("obs-activity")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const totals = summary?.totals;
  const daily = summary?.daily ?? [];
  const spark = (k: "count" | "cost" | "tokens") => daily.map((d) => d[k]);
  const allCostZero = !!summary && summary.totals.tokens > 0 && summary.steps.every((s) => s.cost === 0);
  const empty = !!summary && summary.totals.observations === 0;

  return (
    <div className="obs">
      <NavBar sub />
      <main className="obs__main">
        <div className="obs__head">
          <div>
            <h1>Observability</h1>
            <p className="obs__sub">Tracing, usage and cost for every request, straight from Langfuse.</p>
          </div>
          <div className="obs__controls">
            <Segmented
              label="Time range"
              value={days}
              onChange={setDays}
              options={RANGES.map((r) => ({ value: r.days, label: r.label }))}
            />
            <Button onClick={refresh} loading={busy} aria-label="Refresh" title="Refresh">
              {!busy ? <RefreshCw size={15} /> : null}
              Refresh
            </Button>
            <label className="obs-switch" title="Refresh every 30 seconds">
              <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
              <span className="obs-switch__track" aria-hidden />
              <span>Auto</span>
            </label>
            {status?.base_url ? (
              <a className="btn btn--secondary" href={status.base_url} target="_blank" rel="noopener noreferrer">
                Open Langfuse <ExternalLink size={14} />
              </a>
            ) : null}
          </div>
        </div>

        <div className="obs__status">
          {status ? (
            <>
              <span className="obs-pill">
                <span className={`status-dot ${status.configured && status.enabled && status.reachable ? "status-dot--ok" : "status-dot--bad"}`} />
                {status.configured && status.enabled && status.reachable ? "Connected" : "Not connected"}
              </span>
              {status.project ? <Badge>{status.project}</Badge> : null}
              <Badge tone="accent">agent · {status.slug}</Badge>
              <Badge>env · {status.environment}</Badge>
              <span className="obs-dim obs-host">{status.base_url.replace(/^https?:\/\//, "")}</span>
              {!status.capture_content ? <Badge tone="warning">content withheld</Badge> : null}
            </>
          ) : (
            <Skeleton w={320} h={24} />
          )}
          <span className="obs__updated">{updatedAt ? `Updated ${timeAgo(updatedAt)}` : loading ? "Loading from Langfuse…" : ""}</span>
        </div>

        {status && (!status.configured || !status.enabled || !status.reachable) ? (
          <div className="obs-banner obs-banner--warning" role="status">
            <TriangleAlert size={16} />
            <span>
              {!status.configured
                ? "Langfuse keys are not set (LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY)."
                : !status.enabled
                  ? "Tracing is switched off (LANGFUSE_ENABLED=0), so new activity is not being recorded."
                  : "Langfuse is not reachable or rejected the keys."}
            </span>
          </div>
        ) : null}

        {error ? (
          <div className="obs-banner obs-banner--danger" role="alert">
            <AlertTriangle size={16} />
            <span>{error}</span>
            <Button size="sm" onClick={refresh}>Retry</Button>
            <Button size="sm" variant="ghost" icon onClick={dismissError} aria-label="Dismiss">
              <X size={15} />
            </Button>
          </div>
        ) : null}

        <section className="obs-kpis" aria-label="Key metrics">
          {totals ? (
            <>
              <Kpi label="Observations" value={totals.observations} format={(n) => num(Math.round(n))} icon={Activity} spark={spark("count")} />
              <Kpi label="Total cost" value={totals.cost} format={usd} icon={CircleDollarSign} spark={spark("cost")} hint={allCostZero ? "Model has no price set" : undefined} />
              <Kpi label="Tokens" value={totals.tokens} format={(n) => (n >= 100000 ? compact(n) : num(Math.round(n)))} icon={Hash} spark={spark("tokens")} />
              <Kpi label="Avg latency" value={totals.avg_latency_s} format={seconds} icon={Clock} hint="Mean per observation" />
              <Kpi label="Errors" value={totals.errors} format={(n) => num(Math.round(n))} icon={Zap} tone={totals.errors > 0 ? "danger" : "success"} hint={totals.errors ? "Open the Errors tab" : "None in this range"} />
              <Kpi label="Warnings" value={totals.warnings} format={(n) => num(Math.round(n))} icon={TriangleAlert} tone={totals.warnings > 0 ? "warning" : "default"} hint={totals.warnings ? "Open the Errors tab" : "None in this range"} />
            </>
          ) : (
            Array.from({ length: 6 }).map((_, i) => <KpiSkeleton key={i} />)
          )}
        </section>

        {allCostZero ? (
          <div className="obs-banner obs-banner--info" role="note">
            <CircleDollarSign size={16} />
            <span>
              Cost is $0 for every step: Langfuse has no price for this model, so only tokens are shown. Add the model's price under Langfuse, Settings, Models to get costs. Calls made before a price existed stay at $0.
            </span>
          </div>
        ) : null}
        {summary?.truncated ? (
          <div className="obs-banner obs-banner--info" role="note">
            <Activity size={16} />
            <span>This range holds more than 5,000 observations. Figures cover the most recent 5,000; pick a shorter range for exact totals.</span>
          </div>
        ) : null}

        {empty ? (
          <div className="obs-hero-empty card">
            <Activity size={26} />
            <h2>No activity in this range</h2>
            <p>Send a chat message, then refresh. Every request, model call and ERPNext tool call is traced here.</p>
            <a className="btn btn--primary" href="/app/chat">Open Chat</a>
          </div>
        ) : null}

        {!empty ? (
          <>
            <div className="obs-grid obs-grid--a">
              {summary ? <ActivityChart summary={summary} /> : <ChartSkeleton lg />}
              {summary ? <TypesDonut summary={summary} /> : <ChartSkeleton />}
            </div>
            <div className="obs-grid obs-grid--b">
              {summary ? <CostChart summary={summary} /> : <ChartSkeleton />}
              {summary ? <UsersCard summary={summary} /> : <ChartSkeleton />}
            </div>
            {summary ? <StepsTable summary={summary} /> : <ChartSkeleton lg />}
          </>
        ) : null}

        <div id="obs-activity">
          <ActivityTable kind={tab} onKind={setTab} items={items} loading={recentLoading} stale={itemsStale} query={query} onQuery={setQuery} onOpen={setSelected} />
        </div>

        <footer className="obs__foot">
          Data comes from the Langfuse API through this server; the keys never reach the browser. Traces from the chat UI show as anonymous because the UI has no sign-in to send as a user id.
        </footer>
      </main>
      <RecordDrawer item={selected} onClose={() => setSelected(null)} onFilterSession={filterSession} />
    </div>
  );
}

function ChartSkeleton({ lg = false }: { lg?: boolean }) {
  return (
    <div className="obs-panel card">
      <Skeleton w={160} h={16} />
      <Skeleton w={240} h={12} style={{ margin: "10px 0 18px" }} />
      <Skeleton h={lg ? 240 : 170} />
    </div>
  );
}

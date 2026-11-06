import clsx from "clsx";
import { useEffect, useState } from "react";
import type { TraceEvent } from "./types";

// Lane colours shared with the landing page's workflow graph (the .lane-* classes in ui.css).
const STAGE_LANES: Record<string, string> = {
  router: "router",
  memory: "mem",
  orchestrator: "orch",
  vendra: "agent",
  procura: "agent",
  finora: "agent",
  approval: "orch",
  erpnext: "erp",
  waiting: "user",
};

export function TraceList({ events, running }: { events: TraceEvent[]; running: boolean }) {
  return (
    <ol className="trace" aria-label="Run trace">
      {events.map((ev, i) => {
        const live = running && i === events.length - 1;
        return (
          <li key={i} className={clsx("trace__item", live && "trace__item--live")}>
            <span className="trace__mark" aria-hidden />
            <span className={clsx("lane", `lane-${STAGE_LANES[ev.stage] || "user"}`)}>{ev.stage}</span>
            <span className="trace__text">{ev.text}</span>
          </li>
        );
      })}
    </ol>
  );
}

// Purely cosmetic: never asserts a specific agent ran. The real per-agent result only comes from the response.
const PHRASES = [
  "Reading your message...",
  "Routing to the right agent...",
  "Checking real ERPNext data...",
  "Coordinating Vendra, Procura & Finora...",
];

export function Thinking() {
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setI((n) => (n + 1) % PHRASES.length), 1400);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="thinking" role="status">
      <span className="thinking__dots" aria-hidden>
        <i /><i /><i />
      </span>
      <span key={i} className="thinking__text">{PHRASES[i]}</span>
    </div>
  );
}

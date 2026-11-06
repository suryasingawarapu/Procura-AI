import type { ReactNode } from "react";
import { Button } from "@/components/Button";
import { Drawer } from "@/components/Drawer";
import { num, seconds, usd } from "@/lib/format";
import { CopyButton } from "./CopyButton";
import { LevelBadge } from "./ActivityTable";
import { isSession, type RecentItem } from "./types";

const PREVIEW_LIMIT = 160; // the server clips input/output previews to this many characters

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="obs-field">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function Block({ label, text }: { label: string; text: string | null }) {
  if (!text) return null;
  return (
    <div className="obs-block">
      <div className="obs-block__head">
        <span>{label}</span>
        <CopyButton text={text} />
      </div>
      <pre>{text}</pre>
      {text.length >= PREVIEW_LIMIT ? <p className="obs-block__hint">Preview limited to {PREVIEW_LIMIT} characters by the server.</p> : null}
    </div>
  );
}

export function RecordDrawer({ item, onClose, onFilterSession }: { item: RecentItem | null; onClose: () => void; onFilterSession: (id: string) => void }) {
  const dash = <span className="obs-dim">—</span>;
  return (
    <Drawer open={!!item} onClose={onClose} title={item ? (isSession(item) ? "Session" : item.name ?? "Observation") : ""}>
      {item && isSession(item) ? (
        <>
          <dl className="obs-fields">
            <Field label="Session id"><code className="obs-wrap">{item.session_id}</code></Field>
            <Field label="Observations">{num(item.observations)}</Field>
            <Field label="Tokens">{num(item.tokens)}</Field>
            <Field label="Cost">{usd(item.cost)}</Field>
            <Field label="Avg latency">{seconds(item.avg_latency_s)}</Field>
          </dl>
          <div className="obs-drawer-actions">
            <CopyButton text={item.session_id} label="Copy session id" size="md" />
            <Button variant="primary" onClick={() => onFilterSession(item.session_id)}>
              Show this session's traces
            </Button>
          </div>
        </>
      ) : item ? (
        <>
          <dl className="obs-fields">
            <Field label="Level"><LevelBadge level={item.level} /></Field>
            <Field label="Type">{item.type ?? dash}</Field>
            <Field label="Status message">{item.status_message || dash}</Field>
            <Field label="User">{item.user_id || "anonymous"}</Field>
            <Field label="Session">{item.session_id ? <code className="obs-wrap">{item.session_id}</code> : dash}</Field>
            <Field label="Environment">{item.environment ?? dash}</Field>
            <Field label="Model">{item.model ?? dash}</Field>
            <Field label="Latency">{seconds(item.latency_s)}</Field>
            <Field label="Tokens">{item.tokens ? num(item.tokens) : dash}</Field>
            <Field label="Cost">{item.total_cost ? usd(item.total_cost) : dash}</Field>
            <Field label="Started">{item.start_time ? new Date(item.start_time).toLocaleString() : dash}</Field>
          </dl>
          <Block label="Input" text={item.input} />
          <Block label="Output" text={item.output} />
          {item.session_id ? (
            <div className="obs-drawer-actions">
              <Button onClick={() => onFilterSession(item.session_id as string)}>Show this session's traces</Button>
            </div>
          ) : null}
        </>
      ) : null}
    </Drawer>
  );
}

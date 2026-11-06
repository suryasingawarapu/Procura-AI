import clsx from "clsx";
import { motion } from "framer-motion";
import { AlertCircle, Check, Copy, Layers } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { MailCard } from "./MailCard";
import { Md } from "./Md";
import { TaskCard } from "./TaskCard";
import { Thinking, TraceList } from "./Trace";
import type { LiveState } from "./useChat";
import type { Msg } from "./types";

function Avatar() {
  return (
    <span className="avatar" aria-hidden>
      <Layers size={15} strokeWidth={2.2} />
    </span>
  );
}

function CopyButton({ target }: { target: React.RefObject<HTMLDivElement> }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText((target.current?.innerText || "").trim());
      setDone(true);
      setTimeout(() => setDone(false), 1200);
    } catch {
      /* clipboard API unavailable: nothing else to fall back to */
    }
  };
  return (
    <button type="button" className="msg__copy" onClick={copy} aria-label="Copy message">
      {done ? <Check size={13} /> : <Copy size={13} />} {done ? "Copied" : "Copy"}
    </button>
  );
}

const clock = (t: number) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

interface Actions {
  busy: boolean;
  onApprove: (prId: string, role: string, decision: "Approved" | "Rejected") => void;
  onResolve: (prId: string, subject: string, winner: string) => void;
  onSendMail: (msgId: string, mailId: string) => Promise<void>;
}

export function MessageView({ msg, onSendMail, ...actions }: { msg: Msg } & Actions) {
  const ref = useRef<HTMLDivElement>(null);
  const enter = { initial: { opacity: 0, y: 8 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.24, ease: [0.22, 1, 0.36, 1] as const } };

  if (msg.role === "user") {
    return (
      <motion.div className="msg msg--user" {...enter}>
        <div className="msg__bubble">{msg.text}</div>
        <div className="msg__meta">{clock(msg.time)}</div>
      </motion.div>
    );
  }

  let body: ReactNode;
  if (msg.pending && !msg.text) {
    body = <Thinking />;
  } else if (msg.error) {
    body = (
      <div className="callout callout--danger">
        <AlertCircle size={15} /> <span>Error: {msg.error}</span>
      </div>
    );
  } else if (msg.pr) {
    body = (
      <>
        {msg.lead ? <p className="msg__lead">{msg.lead}</p> : null}
        <TaskCard pr={msg.pr} trace={msg.trace} {...actions} />
      </>
    );
  } else {
    body = (
      <>
        {msg.trace?.length ? (
          <details className="task__trace">
            <summary>Live run trace &middot; {msg.trace.length} steps</summary>
            <TraceList events={msg.trace} running={false} />
          </details>
        ) : null}
        <Md text={msg.text || ""} />
        {msg.mail ? <MailCard mail={msg.mail} onSend={(mailId) => onSendMail(msg.id, mailId)} /> : null}
      </>
    );
  }

  return (
    <motion.div className="msg msg--assistant" {...enter}>
      <Avatar />
      <div className="msg__col">
        <div className={clsx("msg__body", (msg.pr || msg.mail) && "msg__body--wide")} ref={ref}>
          {body}
        </div>
        {!msg.pending ? (
          <div className="msg__meta">
            <span>{clock(msg.time)}</span>
            <CopyButton target={ref} />
          </div>
        ) : null}
      </div>
    </motion.div>
  );
}

/** The in-flight reply: rotating placeholder, then the live trace, then streamed markdown with a caret. */
export function LiveMessage({ live }: { live: LiveState }) {
  const streaming = live.streamed.length > 0;
  return (
    <div className="msg msg--assistant" aria-live="polite" aria-busy="true">
      <Avatar />
      <div className="msg__col">
        <div className="msg__body">
          {streaming ? (
            <Md text={live.streamed} streaming />
          ) : live.trace.length ? (
            <TraceList events={live.trace} running />
          ) : (
            <Thinking />
          )}
        </div>
      </div>
    </div>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiFetch, streamPost } from "@/lib/api";
import { loadConversations, newId, saveConversations, titleFrom } from "./store";
import type { Conversation, HistoryTurn, MailPreview, Msg, Pr, TraceEvent } from "./types";

const REQUESTED_BY = "jane.doe@example.com"; // no visible field for this, per explicit request
const HISTORY_CAP = 12;

export interface LiveState {
  convId: string;
  trace: TraceEvent[];
  streamed: string;
}

const pushHistory = (history: HistoryTurn[], turn: HistoryTurn): HistoryTurn[] => [...history, turn].slice(-HISTORY_CAP);

export function useChat() {
  const [conversations, setConversations] = useState<Conversation[]>(loadConversations);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [live, setLive] = useState<LiveState | null>(null);
  const [busy, setBusy] = useState(false);

  // Refs hold the latest values for async code that outlives a render.
  const convsRef = useRef(conversations);
  const activeRef = useRef(activeId);
  const historyRef = useRef<HistoryTurn[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  convsRef.current = conversations;
  activeRef.current = activeId;

  useEffect(() => {
    saveConversations(conversations);
  }, [conversations]);

  const active = useMemo(() => conversations.find((c) => c.id === activeId) ?? null, [conversations, activeId]);

  const patchConv = useCallback((id: string, fn: (c: Conversation) => Conversation) => {
    setConversations((all) => all.map((c) => (c.id === id ? fn(c) : c)));
  }, []);

  const addMsg = useCallback(
    (convId: string, msg: Msg, extra: Partial<Conversation> = {}) =>
      patchConv(convId, (c) => ({ ...c, ...extra, updatedAt: Date.now(), messages: [...c.messages, msg] })),
    [patchConv],
  );

  const patchMsg = useCallback(
    (convId: string, msgId: string, patch: Partial<Msg>) =>
      patchConv(convId, (c) => ({ ...c, updatedAt: Date.now(), messages: c.messages.map((m) => (m.id === msgId ? { ...m, ...patch } : m)) })),
    [patchConv],
  );

  const setBusyBoth = (v: boolean) => {
    busyRef.current = v;
    setBusy(v);
  };

  const newChat = useCallback(() => {
    setActiveId(null);
    historyRef.current = [];
  }, []);

  const open = useCallback((id: string) => {
    // The transcript is local, but the task it was working on is remembered by id, so a follow-up still
    // reaches that task; the server re-reads the task's real, current state from its id.
    setActiveId(id);
    historyRef.current = [];
  }, []);

  const remove = useCallback(
    (id: string) => {
      if (!window.confirm("Delete this conversation? This can't be undone.")) return;
      setConversations((all) => all.filter((c) => c.id !== id));
      // Also remove what the server stored for it (its turns and summaries in memory).
      fetch(`/procurement/chat/conversations/${encodeURIComponent(id)}`, { method: "DELETE" }).catch(() => {});
      if (activeRef.current === id) newChat();
    },
    [newChat],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const send = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text || busyRef.current) return;
      let convId = activeRef.current;
      const userMsg: Msg = { id: newId("m"), role: "user", time: Date.now(), text };
      if (!convId || !convsRef.current.some((c) => c.id === convId)) {
        convId = newId("conv");
        const conv: Conversation = { id: convId, title: titleFrom(text), updatedAt: Date.now(), messages: [userMsg] };
        setConversations((all) => [...all, conv]);
        setActiveId(convId);
        activeRef.current = convId;
      } else {
        addMsg(convId, userMsg);
      }
      const prId = convsRef.current.find((c) => c.id === convId)?.prId ?? null;
      historyRef.current = pushHistory(historyRef.current, { role: "user", content: text });

      const id = convId;
      const trace: TraceEvent[] = [];
      let streamed = "";
      let final: { reply?: string; pr?: Pr | null; mail?: MailPreview | null } | null = null;
      let failure: string | null = null;
      let aborted = false;
      const controller = new AbortController();
      abortRef.current = controller;
      setBusyBoth(true);
      setLive({ convId: id, trace: [], streamed: "" });
      const paint = () => setLive({ convId: id, trace: [...trace], streamed });

      try {
        await streamPost(
          "/procurement/chat/stream",
          {
            message: text,
            requested_by: REQUESTED_BY,
            mode: "live", // the only mode this UI offers: see the fixed warning badge in the top bar
            pr_id: prId,
            conversation_id: id,
            history: historyRef.current,
          },
          {
            status: (d: TraceEvent) => {
              trace.push(d);
              paint();
            },
            token: (d: { text: string }) => {
              streamed += d.text;
              paint();
            },
            final: (d) => {
              final = d;
            },
            error: (d: { detail: string }) => {
              failure = d.detail;
            },
          },
          controller.signal,
        );
        if (!final && !failure) failure = "The connection closed before the reply finished.";
      } catch (err) {
        if ((err as Error).name === "AbortError") aborted = true;
        else failure = (err as Error).message;
      }

      const reply: Msg = { id: newId("m"), role: "assistant", time: Date.now() };
      if (aborted) {
        reply.text = streamed ? streamed : "Stopped.";
        if (trace.length) reply.trace = trace;
      } else if (failure) {
        reply.error = failure;
      } else if (final) {
        const f = final as { reply?: string; pr?: Pr | null; mail?: MailPreview | null };
        if (f.pr) {
          reply.pr = f.pr;
          reply.trace = trace.length ? trace : undefined;
          historyRef.current = pushHistory(historyRef.current, { role: "assistant", content: `[task ${f.pr.pr_id}: ${f.pr.workflow_state}]` });
        } else {
          reply.text = f.reply || "(no reply)";
          if (f.mail) reply.mail = f.mail;
          historyRef.current = pushHistory(historyRef.current, { role: "assistant", content: reply.text });
        }
      }
      addMsg(id, reply, reply.pr ? { prId: reply.pr.pr_id } : {});
      setLive(null);
      setBusyBoth(false);
      abortRef.current = null;
    },
    [addMsg],
  );

  /** Approve / reject: GET the current version, POST the decision, then re-read the PR to report what really happened. */
  const approve = useCallback(
    async (prId: string, role: string, decision: "Approved" | "Rejected") => {
      const convId = activeRef.current;
      if (!convId) return;
      const msgId = newId("m");
      addMsg(convId, { id: msgId, role: "assistant", time: Date.now(), text: "Submitting your decision...", pending: true });
      try {
        const current = await apiFetch(`/procurement/requests/${encodeURIComponent(prId)}`);
        const result = await apiFetch(`/procurement/requests/${encodeURIComponent(prId)}/approve`, {
          method: "POST",
          body: JSON.stringify({
            approver_id: "chat-operator",
            approver_role: role,
            decision,
            reviewed_updated_at: current.updated_at,
          }),
        });
        const lines = [`**${result.decision}** by ${result.approver_id} (${result.approver_role}).`];
        if (result.decision === "Approved") {
          // The approval is not the same step as submitting the PO: re-read the PR to show what really happened.
          const updated = await apiFetch(`/procurement/requests/${encodeURIComponent(prId)}`);
          const po = updated.purchase_order;
          if (po && po.status === "submitted") {
            const name = po.result && po.result.name;
            lines.push((updated.mode === "demo" ? "*** SIMULATED -- not live ERPNext *** " : "") + `Purchase order submitted${name ? ` (${name})` : ""}.`);
          } else if (po && po.status === "failed") {
            lines.push(`Purchase order was NOT created: ${po.error}`);
          } else {
            lines.push("No purchase order was submitted (no recommendation to act on).");
          }
        }
        patchMsg(convId, msgId, { text: lines.join("\n\n"), pending: false });
      } catch (err) {
        patchMsg(convId, msgId, { text: undefined, error: (err as Error).message, pending: false });
      }
    },
    [addMsg, patchMsg],
  );

  /** A person presses Send on a prepared purchase-order email: the server sends it and returns the updated record. */
  const sendMail = useCallback(
    async (msgId: string, mailId: string) => {
      const convId = activeRef.current;
      if (!convId) return;
      const sent = await apiFetch<MailPreview>(`/procurement/mail/${encodeURIComponent(mailId)}/send`, {
        method: "POST",
        body: JSON.stringify({ confirm: true }),
      });
      patchMsg(convId, msgId, {
        mail: {
          mail_id: mailId, vendor: sent.vendor, to: sent.to, intended_to: sent.intended_to, subject: sent.subject, body: sent.body,
          attachment_name: sent.attachment_name, status: sent.status, sent_at: sent.sent_at, test_mode: true, sending_allowed: true,
        },
      });
    },
    [patchMsg],
  );

  /** A person picks the right price: record it, then resume the paused run. */
  const resolve = useCallback(
    async (prId: string, subjectKey: string, winnerMemoryId: string) => {
      const convId = activeRef.current;
      if (!convId) return;
      const msgId = newId("m");
      addMsg(convId, { id: msgId, role: "assistant", time: Date.now(), pending: true });
      setBusyBoth(true);
      try {
        await apiFetch("/procurement/conflicts/resolve", {
          method: "POST",
          body: JSON.stringify({ subject_key: subjectKey, winner_memory_id: winnerMemoryId, resolved_by: "chat-operator" }),
        });
        const pr = await apiFetch<Pr>(`/procurement/tasks/${encodeURIComponent(prId)}/resume`, { method: "POST" });
        patchMsg(convId, msgId, { lead: "Decision recorded. Continuing the run.", pr, pending: false });
        patchConv(convId, (c) => ({ ...c, prId: pr.pr_id }));
      } catch (err) {
        patchMsg(convId, msgId, { error: (err as Error).message, pending: false });
      }
      setBusyBoth(false);
    },
    [addMsg, patchMsg, patchConv],
  );

  return { conversations, active, activeId, live, busy, send, stop, newChat, open, remove, approve, resolve, sendMail };
}

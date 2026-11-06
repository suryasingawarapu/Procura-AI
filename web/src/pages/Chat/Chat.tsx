import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { Activity, ArrowDown, FileText, Menu, RefreshCw, Scale, Search, ShoppingCart, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/Button";
import { ThemeToggle } from "@/components/ThemeToggle";
import { apiFetch } from "@/lib/api";
import { resetApprovalLimits } from "./approvalLimits";
import { Composer } from "./Composer";
import { LiveMessage, MessageView } from "./Message";
import { ConnectionsPanel, ItemCodesPanel } from "./Panels";
import { Sidebar } from "./Sidebar";
import { useChat } from "./useChat";
import "./chat.css";

const SUGGESTIONS = [
  { icon: Search, title: "Explain Vendra", prompt: "What does Vendra check before approving a supplier?" },
  {
    icon: ShoppingCart,
    title: "Start a purchase",
    prompt: "Purchase 10 laptops with 16 GB RAM and 512 GB SSD, within ₹6,00,000, delivered to our Mumbai office within 20 days.",
    featured: true,
  },
  { icon: Scale, title: "RFQ vs Purchase Order", prompt: "What's the difference between an RFQ and a Purchase Order?" },
  { icon: FileText, title: "Draft a vendor email", prompt: "Draft an email to the recommended vendor accepting their quote.", featured: true },
];

type HealthState = "unknown" | "ok" | "bad";

export default function Chat() {
  const chat = useChat();
  const { active, live, busy } = chat;
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [panel, setPanel] = useState<"connections" | "items" | null>(null);
  const [health, setHealth] = useState<HealthState>("unknown");
  const [insert, setInsert] = useState<{ text: string; n: number } | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);
  const atBottomRef = useRef(true);

  const checkHealth = useCallback(() => {
    apiFetch("/procurement/health").then(() => setHealth("ok")).catch(() => setHealth("bad"));
  }, []);
  useEffect(checkHealth, [checkHealth]);

  const messages = active?.messages ?? [];
  const showEmpty = messages.length === 0 && !live;

  const scrollToBottom = useCallback((smooth = false) => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }, []);

  // Follow new content only while the reader is at the bottom: never fight a user who scrolled up.
  useLayoutEffect(() => {
    if (atBottomRef.current) scrollToBottom();
  }, [messages.length, live, scrollToBottom]);

  // Content can grow after the first paint (fonts, tables, streamed text): keep following it while pinned to the bottom.
  const threadRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = threadRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (atBottomRef.current) scrollToBottom();
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [showEmpty, scrollToBottom]);

  // Switching conversation always starts at the latest message.
  useLayoutEffect(() => {
    atBottomRef.current = true;
    setAtBottom(true);
    scrollToBottom();
  }, [chat.activeId, scrollToBottom]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    atBottomRef.current = near;
    setAtBottom(near);
  };

  const send = (text: string) => {
    atBottomRef.current = true; // sending is an intent to read the answer
    setAtBottom(true);
    void chat.send(text);
  };

  const closeSidebar = () => setSidebarOpen(false);

  return (
    <div className={clsx("chat-shell", sidebarOpen && "chat-shell--open")}>
      <aside className="chat-side" aria-label="Conversations">
        <Sidebar
          conversations={chat.conversations}
          activeId={chat.activeId}
          onNew={() => {
            chat.newChat();
            closeSidebar();
          }}
          onOpen={(id) => {
            chat.open(id);
            closeSidebar();
          }}
          onDelete={chat.remove}
          onConnections={() => {
            setPanel("connections");
            closeSidebar();
          }}
          onItemCodes={() => {
            setPanel("items");
            closeSidebar();
          }}
        />
      </aside>
      <div className="chat-backdrop" onClick={closeSidebar} aria-hidden />

      <main className="chat-main">
        <header className="chat-top">
          <Button variant="ghost" icon className="chat-top__menu" onClick={() => setSidebarOpen((o) => !o)} aria-label="Open conversations">
            <Menu size={18} />
          </Button>
          <div className="chat-top__title">{active?.title || "New conversation"}</div>
          <div className="chat-top__right">
            <span className="live-badge" title="Every action runs against the real ERPNext and purchase orders are auto-approved by policy">
              <TriangleAlert size={13} /> <span>LIVE &mdash; real ERPNext, auto-approved</span>
            </span>
            <span
              className={clsx("status-dot", health === "ok" && "status-dot--ok", health === "bad" && "status-dot--bad")}
              title={health === "ok" ? "Backend: connected" : health === "bad" ? "Backend: unreachable" : "Backend status"}
              role="img"
              aria-label={health === "ok" ? "Backend connected" : health === "bad" ? "Backend unreachable" : "Backend status unknown"}
            />
            <Link to="/observability" className="btn btn--ghost btn--sm chat-top__obs">
              <Activity size={15} /> Observability
            </Link>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                resetApprovalLimits(); // force a fresh read of the real policy next time it is needed
                checkHealth();
              }}
            >
              <RefreshCw size={14} /> Sync
            </Button>
            <ThemeToggle />
          </div>
        </header>

        <div className="chat-scroll" ref={scrollRef} onScroll={onScroll}>
          {showEmpty ? (
            <div className="empty">
              <motion.div className="empty__inner" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
                <span className="empty__badge">Procurement Coordinator</span>
                <h1>Ask the Coordinator anything</h1>
                <p>Vendors, quotations, budgets and approvals, answered from your real ERPNext data.</p>
                <div className="suggest">
                  {SUGGESTIONS.map(({ icon: Icon, title, prompt, featured }) => (
                    <button type="button" key={title} className={clsx("suggest__card", featured && "suggest__card--featured")} onClick={() => send(prompt)}>
                      <span className="suggest__icon"><Icon size={16} /></span>
                      <span className="suggest__title">{title}</span>
                      <span className="suggest__text">{prompt}</span>
                    </button>
                  ))}
                </div>
              </motion.div>
            </div>
          ) : (
            <div className="thread" role="log" aria-label="Conversation" ref={threadRef}>
              {messages.map((m) => (
                <MessageView key={m.id} msg={m} busy={busy} onApprove={chat.approve} onResolve={chat.resolve} onSendMail={chat.sendMail} />
              ))}
              {live && live.convId === chat.activeId ? <LiveMessage live={live} /> : null}
            </div>
          )}
        </div>

        <AnimatePresence>
          {!atBottom && !showEmpty ? (
            <motion.button
              type="button"
              className="jump"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 8 }}
              onClick={() => {
                atBottomRef.current = true;
                setAtBottom(true);
                scrollToBottom(true);
              }}
            >
              <ArrowDown size={14} /> Jump to latest
            </motion.button>
          ) : null}
        </AnimatePresence>

        <Composer busy={busy} insert={insert} onSend={send} onStop={chat.stop} />
      </main>

      <ConnectionsPanel open={panel === "connections"} onClose={() => setPanel(null)} />
      <ItemCodesPanel
        open={panel === "items"}
        onClose={() => setPanel(null)}
        onUse={(code) => {
          setInsert((p) => ({ text: code, n: (p?.n ?? 0) + 1 }));
          setPanel(null);
        }}
      />
    </div>
  );
}

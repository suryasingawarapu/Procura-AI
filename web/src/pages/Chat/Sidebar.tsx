import clsx from "clsx";
import { Link2, ListChecks, Plus, Search, Activity, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/Button";
import { Logo } from "@/components/Logo";
import { groupLabel } from "./store";
import type { Conversation } from "./types";

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  onNew: () => void;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
  onConnections: () => void;
  onItemCodes: () => void;
}

export function Sidebar({ conversations, activeId, onNew, onOpen, onDelete, onConnections, onItemCodes }: Props) {
  const [query, setQuery] = useState("");
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const sorted = [...conversations].filter((c) => !q || c.title.toLowerCase().includes(q)).sort((a, b) => b.updatedAt - a.updatedAt);
    const out: Record<string, Conversation[]> = { Today: [], Yesterday: [], Earlier: [] };
    for (const c of sorted) out[groupLabel(c.updatedAt)].push(c);
    return Object.entries(out).filter(([, list]) => list.length);
  }, [conversations, query]);

  return (
    <div className="side">
      <div className="side__brand">
        <Logo />
      </div>
      <Button variant="primary" className="side__new" onClick={onNew}>
        <Plus size={16} /> New chat
      </Button>

      <div className="side__label">Workspace</div>
      <nav className="side__nav" aria-label="Workspace">
        <button type="button" className="side__link" onClick={onConnections}>
          <Link2 size={16} /> Connections
        </button>
        <button type="button" className="side__link" onClick={onItemCodes}>
          <ListChecks size={16} /> Item codes
        </button>
        <Link to="/observability" className="side__link">
          <Activity size={16} /> Observability
        </Link>
      </nav>

      <div className="side__label">
        Conversations <span className="side__count">{conversations.length}</span>
      </div>
      <label className="side__search">
        <Search size={14} aria-hidden />
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search conversations" aria-label="Search conversations" />
      </label>

      <div className="side__list">
        {groups.length === 0 ? (
          <div className="side__empty">{conversations.length ? "No matches." : "No conversations yet."}</div>
        ) : (
          groups.map(([label, list]) => (
            <div key={label}>
              <div className="side__group">{label}</div>
              {list.map((c) => (
                <div key={c.id} className={clsx("conv", c.id === activeId && "conv--active")}>
                  <button type="button" className="conv__main" onClick={() => onOpen(c.id)} aria-current={c.id === activeId ? "true" : undefined}>
                    <span className="conv__title">{c.title}</span>
                  </button>
                  <button type="button" className="conv__del" onClick={() => onDelete(c.id)} aria-label={`Delete conversation: ${c.title}`} title="Delete this conversation">
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

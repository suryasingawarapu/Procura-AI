import type { Conversation } from "./types";

// The backend has no notion of a "conversation" for the UI: history here is a browser-local
// convenience (structured messages, never rendered HTML). v2 replaced the old HTML-based key.
const KEY = "procurement_conversations_v2";

export function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveConversations(conversations: Conversation[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(conversations));
  } catch {
    /* storage full or blocked: history just will not persist across reloads */
  }
}

export const newId = (prefix: string) => `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

export function titleFrom(text: string): string {
  const t = text.trim().replace(/\s+/g, " ");
  return t.length > 60 ? t.slice(0, 57) + "..." : t;
}

export function groupLabel(ts: number): "Today" | "Yesterday" | "Earlier" {
  const d = new Date(ts);
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (d.getTime() >= startToday) return "Today";
  if (d.getTime() >= startToday - 86_400_000) return "Yesterday";
  return "Earlier";
}

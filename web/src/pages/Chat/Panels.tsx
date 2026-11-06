import { useEffect, useState } from "react";
import { Drawer } from "@/components/Drawer";
import { Skeleton } from "@/components/Skeleton";
import { apiFetch } from "@/lib/api";

export interface Health {
  erpnext_backend?: string;
  erpnext_writes_allowed?: boolean;
  supabase_configured?: boolean;
  llm_provider_configured?: boolean;
  langfuse_configured?: boolean;
  ai_summaries_enabled?: boolean;
}

export function ConnectionsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    setHealth(null);
    setError(null);
    apiFetch<Health>("/procurement/health").then(setHealth).catch((e) => setError(e.message));
  }, [open]);

  const rows: Array<[string, string, boolean]> = health
    ? [
        ["ERPNext backend", String(health.erpnext_backend ?? "unknown"), true],
        ["ERPNext writes allowed", health.erpnext_writes_allowed ? "yes" : "no", !!health.erpnext_writes_allowed],
        ["Supabase memory", health.supabase_configured ? "configured" : "not configured", !!health.supabase_configured],
        ["LLM provider", health.llm_provider_configured ? "configured" : "not configured", !!health.llm_provider_configured],
        ["Langfuse tracing", health.langfuse_configured ? "configured" : "not configured", !!health.langfuse_configured],
        ["AI summaries", health.ai_summaries_enabled ? "enabled" : "disabled", !!health.ai_summaries_enabled],
      ]
    : [];

  return (
    <Drawer open={open} onClose={onClose} title="Connections">
      {error ? <p className="panel__error">Error: {error}</p> : null}
      {!health && !error ? (
        <div className="panel__skeletons">
          {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} h={44} />)}
        </div>
      ) : null}
      <div className="panel__rows">
        {rows.map(([label, value, ok]) => (
          <div className="panel__row" key={label}>
            <span>{label}</span>
            <span className="panel__value">
              {value} <span className={`status-dot ${ok ? "status-dot--ok" : "status-dot--bad"}`} />
            </span>
          </div>
        ))}
      </div>
    </Drawer>
  );
}

// Real item codes actually seeded into the demo pool / live ERPNext (see app/procurement/demo_fixtures.py and
// scripts/seed_live_erpnext_vendors.py): a static reference list, not a live query, but every code here is real.
export const KNOWN_ITEM_CODES: Array<[string, string, string]> = [
  ["LAPTOP-14IN", "14-inch Business Laptop", "IT Hardware"],
  ["OFFICE-CHAIR-STD", "Ergonomic Office Chair", "Office Supplies"],
  ["FREIGHT-STD", "Standard Freight Delivery Service", "Logistics"],
  ["PACK-BOX-MED-50", "Medium Shipping Carton (Pack of 50)", "Packaging"],
  ["FACILITY-MAINT-MO", "Facility Maintenance Service (Monthly)", "Facilities"],
  ["SOFTWARE-LIC-ANNUAL", "Annual Software License (Per Seat)", "Software Services"],
];

export function ItemCodesPanel({ open, onClose, onUse }: { open: boolean; onClose: () => void; onUse: (code: string) => void }) {
  return (
    <Drawer open={open} onClose={onClose} title="Item codes">
      <p className="panel__hint">Reference codes seeded into both the demo pool and live ERPNext. Use one of these when the agent asks for an item code.</p>
      <div className="panel__rows">
        {KNOWN_ITEM_CODES.map(([code, name, category]) => (
          <button type="button" key={code} className="panel__item" onClick={() => onUse(code)} title="Insert into the message box">
            <code>{code}</code>
            <span>{name}</span>
            <em>{category}</em>
          </button>
        ))}
      </div>
    </Drawer>
  );
}

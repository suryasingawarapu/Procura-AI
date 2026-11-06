// Data for the end-to-end trace graph. Memory keys, states and transitions are the ones the system
// actually uses; IDs, names and amounts are illustrative.

export type Lane = "user" | "router" | "orch" | "agent" | "erp" | "mem" | "vendor";

export interface FlowNode {
  id: string;
  col: number;
  row: number;
  lane: Lane;
  icon: keyof typeof ICONS;
  title: string;
  sub: string;
  head: string;
  body: string;
  code: string;
}

export interface FlowEdge {
  f: string;
  t: string;
  v?: boolean; // vertical (bottom/top handles)
  data?: boolean; // dashed: a read/write, not control flow
  label?: string;
}

export const NODE_W = 188;
export const NODE_H = 78;
export const COL = (c: number) => 40 + c * 248;
export const ROW = [60, 250, 440, 612];
export const CANVAS_W = 1510;
export const CANVAS_H = 724;

export const ICONS = {
  chat: "M4 5h16v11H9l-5 4z",
  route: "M7 12h4l6-6M11 12l6 6M3 12a2 2 0 1 0 4 0a2 2 0 1 0-4 0M17 6a2 2 0 1 0 4 0a2 2 0 1 0-4 0M17 18a2 2 0 1 0 4 0a2 2 0 1 0-4 0",
  db: "M5 6c0-1.7 3.1-3 7-3s7 1.3 7 3-3.1 3-7 3-7-1.3-7-3zM5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3",
  graph: "M9.5 5a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M2.5 19a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M16.5 19a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M11 7.2 6.2 16.8M13 7.2l4.8 9.6M7.5 19h9",
  cpu: "M8 6h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2zM10.5 9.5h3v5h-3zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4",
  cube: "M12 2l9 5v10l-9 5-9-5V7zM3 7l9 5 9-5M12 12v10",
  truck: "M2 6h12v10H2zM14 10h4l3 3v3h-7M4 18a2 2 0 1 0 4 0a2 2 0 1 0-4 0M15 18a2 2 0 1 0 4 0a2 2 0 1 0-4 0",
  clock: "M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 7v5l3 2",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM8.5 12l2.5 2.5 4.5-5",
  reply: "M10 8 4 13l6 5M4 13h11a5 5 0 0 0 5-5V6",
  file: "M6 2h8l5 5v15H6zM14 2v5h5",
} as const;

export const LANES: Record<Lane, string> = {
  user: "Requester",
  router: "Router",
  orch: "Orchestrator",
  agent: "Agent",
  erp: "ERPNext",
  mem: "Memory",
  vendor: "Vendor",
};

export const NODES: FlowNode[] = [
  { id: "chat1", col: 0, row: 0, lane: "user", icon: "chat", title: "Chat message", sub: "Requester · new instruction",
    head: "An instruction arrives in chat",
    body: "The requester types what they need in plain language. No form, no item picker.",
    code: '"Purchase 10 laptops with 16 GB RAM, within ₹6,00,000, delivered to our Mumbai office within 20 days."' },
  { id: "router1", col: 1, row: 0, lane: "router", icon: "route", title: "Router", sub: "intent = new_task",
    head: "Classified as a new task, fields parsed deterministically",
    body: "No LLM is needed to read numbers. A regex parser extracts quantity, budget, currency, location and deadline.",
    code: 'intent=new_task · qty=10 · budget=600000 INR · location="Mumbai" · due=+20d' },
  { id: "mem1", col: 2, row: 0, lane: "mem", icon: "db", title: "Memory · write", sub: "purchase_request v1",
    head: "The task is persisted before any agent runs",
    body: "The Purchase Request becomes version 1 in Procura's own memory space, so it survives anything that happens next.",
    code: 'procura / purchase_request:PR-7F3A · v1 · active\nstate="Waiting For Information"' },
  { id: "orch1", col: 3, row: 0, lane: "orch", icon: "graph", title: "Orchestrator", sub: "held · item_code missing",
    head: "Pipeline held: nothing is guessed",
    body: "Live mode needs an exact ERPNext item code, and free text is never mapped to a catalogue item, so the agent pipeline does not start.",
    code: 'blockers=["item_code"] · agents_run=0' },
  { id: "reply1", col: 4, row: 0, lane: "orch", icon: "reply", title: "Reply", sub: "asks for the item code",
    head: "The agent asks for exactly what is missing",
    body: "The reply names the one missing field instead of failing or inventing a value.",
    code: '"I need the exact ERPNext item code."' },
  { id: "wait", col: 5, row: 0, lane: "user", icon: "clock", title: "Wait", sub: "session closed · server restarted",
    head: "The session ends",
    body: "The browser tab is closed and the API server restarts. Nothing from Session 1's process or tab survives; only persistent memory and ERPNext do.",
    code: "process state: gone · persistent memory: intact" },
  { id: "vendor", col: 0, row: 1, lane: "vendor", icon: "truck", title: "Vendor", sub: "ACME Tech · overnight",
    head: "A supplier fixes its compliance gap",
    body: "ACME Tech, recorded as non-compliant by an earlier task, uploads its missing GST certificate. This happens outside the chat entirely.",
    code: "vendor action · no agent involved" },
  { id: "erpdoc", col: 1, row: 1, lane: "erp", icon: "file", title: "ERPNext", sub: "File → Supplier/ACME-TECH",
    head: "The document lands in ERPNext",
    body: "The certificate is attached to the Supplier record. Vendra will see it the next time it checks this supplier.",
    code: "File gst_certificate.pdf → Supplier/ACME-TECH" },
  { id: "chat2", col: 2, row: 1, lane: "user", icon: "chat", title: "Chat message", sub: '"Use LAPTOP-14IN"',
    head: "A one-line follow-up, the next day",
    body: "The requester replies with only the missing detail, in a brand-new session.",
    code: '"Use item code LAPTOP-14IN."' },
  { id: "router2", col: 3, row: 1, lane: "router", icon: "route", title: "Router", sub: "provide_info · no LLM",
    head: "Recognised as the missing detail, decided in code",
    body: "The task is waiting for information and the message is not a new instruction, so it is routed as provide_info without consulting the LLM. Leaving this to the model misrouted it roughly one time in three.",
    code: "intent=provide_info · path=deterministic short-circuit" },
  { id: "memread", col: 4, row: 1, lane: "mem", icon: "db", title: "Memory · read", sub: "recall PR · v1 → v2",
    head: "The task is recalled from the database, not the browser",
    body: "Procura's memory returns the active version of the request. The new field is written as version 2, which supersedes version 1 in one transaction.",
    code: 'purchase_request:PR-7F3A · v1 → v2 (supersedes)\nitem_code="LAPTOP-14IN"' },
  { id: "orch2", col: 5, row: 1, lane: "orch", icon: "graph", title: "Orchestrator", sub: "LangGraph · fixed order",
    head: "The LangGraph pipeline starts",
    body: "Vendra, then Procura, then Finora: a fixed dependency order with no cycles. Each agent reads the previous one's result from shared state.",
    code: "systems_needed=[vendra, procura, finora] · step_index=0" },
  { id: "vendra", col: 0, row: 2, lane: "agent", icon: "cpu", title: "Vendra", sub: "6 sub-agents · eligibility",
    head: "Six specialists check every supplier's real documents",
    body: "Suppliers and their attached files are read through Frappe's official MCP endpoint. ACME Tech now passes document verification.",
    code: "vendor_discovery ✓  document_verification ✓  onboarding_readiness ✓\nvendor_profile ✓  performance_snapshot ✓  risk_flagging ✓" },
  { id: "mem2", col: 0, row: 3, lane: "mem", icon: "db", title: "Memory · new version", sub: "eligibility v1 → v2",
    head: "A fact changes; its history does not disappear",
    body: "ACME Tech's eligibility fact is superseded rather than overwritten. Version 1 (disputed) stays queryable and is linked to version 2 (verified) by a supersedes edge; the new row records the task and source that caused the change.",
    code: "vendra / supplier_eligibility:ACME-TECH\nv1 disputed → v2 verified · edge=supersedes · source_type=erpnext_doctype" },
  { id: "erpq", col: 2, row: 3, lane: "erp", icon: "cube", title: "ERPNext · MCP", sub: "supplier quotations",
    head: "Real quotations are read from ERPNext",
    body: "Procura reads Supplier Quotations for the item through the same MCP endpoint, only for suppliers Vendra marked eligible.",
    code: "Supplier Quotation · item_code=LAPTOP-14IN · suppliers=eligible only" },
  { id: "procura", col: 2, row: 2, lane: "agent", icon: "cpu", title: "Procura", sub: "6 sub-agents · quotations",
    head: "Real quotations, compared and ranked",
    body: "Only Vendra-verified suppliers are considered. Quotations are compared and scored, and one supplier is recommended.",
    code: "eligible=3 · quotations=3 · recommended=ACME-TECH · total=₹5,82,000" },
  { id: "finora", col: 3, row: 2, lane: "agent", icon: "cpu", title: "Finora", sub: "6 sub-agents · budget",
    head: "The real total is checked against budget and authority",
    body: "The check uses the recommended cost, never the requester's original estimate, and picks the lowest role whose limit covers it.",
    code: "within_budget=true · approval_check_amount=582000 · required_role=director" },
  { id: "approval", col: 4, row: 2, lane: "orch", icon: "shield", title: "Approval gate", sub: "human or auto-approve policy",
    head: "Nothing is ordered without an approval",
    body: "A human approves in chat, or the live-mode auto-approve policy does and is labelled SYSTEM-AUTO-APPROVAL in the audit trail. Only then does submit_purchase_order run.",
    code: "approval:PR-7F3A · decision=Approved · approver_role=director" },
  { id: "po", col: 5, row: 2, lane: "erp", icon: "cube", title: "ERPNext · create PO", sub: "PUR-ORD-2026-00031",
    head: "A real Purchase Order is created",
    body: "submit_purchase_order writes the Purchase Order to ERPNext through MCP.",
    code: "Purchase Order PUR-ORD-2026-00031 · supplier=ACME-TECH · qty=10" },
  { id: "mem3", col: 5, row: 3, lane: "mem", icon: "db", title: "Memory · write", sub: "purchase_request v3 · Completed",
    head: "The outcome becomes part of the record",
    body: "The final state is version 3. Versions 1 and 2 remain, so the full path from first message to Purchase Order can be replayed and audited.",
    code: 'purchase_request:PR-7F3A · v3 · state="Completed"' },
];

export const EDGES: FlowEdge[] = [
  { f: "chat1", t: "router1" },
  { f: "router1", t: "mem1" },
  { f: "mem1", t: "orch1" },
  { f: "orch1", t: "reply1" },
  { f: "reply1", t: "wait" },
  { f: "wait", t: "chat2", label: "next day" },
  { f: "vendor", t: "erpdoc" },
  { f: "chat2", t: "router2" },
  { f: "router2", t: "memread" },
  { f: "memread", t: "orch2" },
  { f: "orch2", t: "vendra", label: "run pipeline" },
  { f: "vendra", t: "procura", label: "eligible suppliers" },
  { f: "procura", t: "finora" },
  { f: "finora", t: "approval" },
  { f: "approval", t: "po" },
  { f: "po", t: "mem3", v: true },
  { f: "mem1", t: "memread", v: true, data: true, label: "same record, recalled" },
  { f: "erpdoc", t: "vendra", v: true, data: true, label: "document on file" },
  { f: "vendra", t: "mem2", v: true, data: true },
  { f: "erpq", t: "procura", v: true, data: true },
];

export const STICKIES: { title: string; ids: string[]; right?: boolean }[] = [
  { title: "Session 1 · Day 1, 10:02", ids: ["chat1", "router1", "mem1", "orch1", "reply1"] },
  { title: "Overnight · vendor side", ids: ["vendor", "erpdoc"] },
  { title: "Session 2 · Day 2, 09:40", ids: ["chat2", "router2", "memread", "orch2"] },
  { title: "Agent pipeline · LangGraph", ids: ["vendra", "mem2", "erpq", "procura", "finora", "approval", "po", "mem3"], right: true },
];

export const ORDER = [
  "chat1", "router1", "mem1", "orch1", "reply1", "wait", "vendor", "erpdoc", "chat2", "router2",
  "memread", "orch2", "vendra", "mem2", "erpq", "procura", "finora", "approval", "po", "mem3",
];

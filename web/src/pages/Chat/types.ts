export interface TraceEvent {
  stage: string;
  text: string;
}

/** The backend's PurchaseRequest response. Only the fields this UI reads are named. */
export interface Pr {
  pr_id: string;
  workflow_state: string;
  mode?: string;
  quantity?: number | null;
  estimated_budget?: number | null;
  delivery_location?: string | null;
  required_date?: string | null;
  blockers?: string[];
  updated_at?: string;
  purchase_order?: { status?: string; error?: string; result?: { name?: string } } | null;
  coordinator_result?: CoordinatorResult | null;
  [key: string]: unknown;
}

export interface CoordinatorResult {
  error?: string | null;
  approval_summary?: string | null;
  result?: {
    recommended_supplier?: string | null;
    recommended_total_cost?: number | null;
    conflicts?: Conflict[];
    [key: string]: unknown;
  } | null;
  messages?: AgentMessage[];
  [key: string]: unknown;
}

export interface Conflict {
  subject_key: string;
  supplier?: string;
  item_code?: string;
  reason?: string;
  claims?: Array<{
    memory_id: string;
    value: number | string;
    currency?: string;
    source_type?: string;
    document_date?: string;
    source_reference?: string;
  }>;
}

export interface AgentMessage {
  sender: string;
  status: string;
  error?: string | null;
  payload?: any;
}

/** A purchase-order email waiting for a person's Send (or already sent). */
export interface MailPreview {
  mail_id: string;
  pr_id?: string;
  vendor?: string;
  to?: string;
  intended_to?: string | null;
  subject?: string;
  body?: string;
  attachment_name?: string | null;
  status?: string;
  sent_at?: string | null;
  test_mode?: boolean;
  sending_allowed?: boolean;
}

export type Role = "user" | "assistant";

export interface Msg {
  id: string;
  role: Role;
  time: number;
  /** Markdown text (plain replies, user messages, action results). */
  text?: string;
  /** A sentence shown above a task card. */
  lead?: string;
  pr?: Pr;
  mail?: MailPreview;
  trace?: TraceEvent[];
  error?: string;
  /** True while an action (approve / resolve) is still running. */
  pending?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  updatedAt: number;
  prId?: string;
  messages: Msg[];
}

export interface HistoryTurn {
  role: Role;
  content: string;
}

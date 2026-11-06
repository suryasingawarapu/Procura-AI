import clsx from "clsx";
import { AlertTriangle, Check, ChevronRight, Info, ShieldCheck, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, type Tone } from "@/components/Badge";
import { Button } from "@/components/Button";
import { getApprovalLimits, pickQualifyingRole } from "./approvalLimits";
import { Md } from "./Md";
import { TraceList } from "./Trace";
import type { AgentMessage, Conflict, Pr, TraceEvent } from "./types";

// System display names: matches app/procurement/identities.py's SYSTEM_IDS.
const SYSTEM_LABELS: Record<string, string> = {
  vendra: "Vendra (vendor/compliance)",
  procura: "Procura (quotes/negotiation)",
  finora: "Finora (budget/finance)",
};

const STATE_TONE: Record<string, Tone> = {
  Completed: "success",
  "Waiting For Approval": "warning",
  "Waiting For Information": "info",
  "Waiting For Clarification": "warning",
  Blocked: "danger",
  Failed: "danger",
  Running: "accent",
};

const money = (n: unknown) => (typeof n === "number" ? `₹${n.toLocaleString("en-IN")}` : String(n ?? "—"));

/** A natural-language note on which specialists ran normally and which did not. */
function describeSpecialists(specialists: Record<string, any>): string {
  const names = Object.keys(specialists);
  if (!names.length) return "";
  const notOk = names.filter((n) => specialists[n].status !== "ok");
  if (!notOk.length) return names.length === 1 ? `The ${names[0]} check completed normally.` : `All ${names.length} specialist checks completed normally (${names.join(", ")}).`;
  const notes = notOk
    .map((n) => {
      const s = specialists[n];
      const reason = s.status === "error" ? s.error : s.data && s.data.reason;
      return `${n} was ${s.status}${reason ? ` (${reason})` : ""}`;
    })
    .join("; ");
  return `${names.length - notOk.length} of ${names.length} specialist checks completed normally; ${notes}.`;
}

function AgentWork({ messages }: { messages: AgentMessage[] }) {
  if (!messages.length) return null;
  return (
    <div className="agent-work">
      {messages.map((m, i) => {
        const label = SYSTEM_LABELS[m.sender] || m.sender;
        const payload = m.payload || {};
        const notes: string[] = [];
        if (m.error) notes.push(m.error);
        const specNote = describeSpecialists(payload.specialists || {});
        if (specNote) notes.push(specNote);
        const basis = payload.eligibility_basis;
        if (basis?.rejected?.length) {
          notes.push("Excluded after checking its shared memory: " + basis.rejected.map((r: any) => `${r.supplier} (${r.reason})`).join("; ") + ".");
        }
        if (payload.reconciliation?.note) notes.push(payload.reconciliation.note + ".");
        for (const r of (payload.reconciliation?.resolved || []).slice(0, 3)) {
          notes.push(`Sources disagreed on ${r.supplier}'s price; ${r.reason}.`);
        }
        const history = payload.specialists?.quotation_history;
        if (history?.status === "ok") {
          const h = history.data;
          const first = (h.changes || [])[0];
          notes.push(
            `Quotation history: ${h.summary_used ? "read the stored summary first" : "no stored summary yet"}, then opened ${h.records_opened} supporting records` +
              (first ? `; ${first.supplier}'s ${first.field === "price" ? "price" : "delivery time"} changed from ${first.from} to ${first.to}.` : "."),
          );
        }
        return (
          <div className="agent-work__row" key={`${m.sender}-${i}`}>
            <span className={clsx("agent-work__dot", `agent-work__dot--${m.sender}`)} aria-hidden />
            <div>
              <div className="agent-work__name">{label}</div>
              <div className="agent-work__text">
                {payload.ai_summary ? <Md text={String(payload.ai_summary)} inline /> : <>reported &ldquo;{m.status}&rdquo;.</>}
                {notes.length ? <span className="agent-work__notes"> {notes.join(" ")}</span> : null}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ConflictBox({ conflict, pr, disabled, onResolve }: { conflict: Conflict; pr: Pr; disabled: boolean; onResolve: (prId: string, subject: string, winner: string) => void }) {
  return (
    <div className="conflict">
      <div className="conflict__title">
        <AlertTriangle size={15} /> Price disputed: {conflict.supplier || ""} for {conflict.item_code || ""}
      </div>
      {conflict.reason ? <div className="conflict__reason">{conflict.reason}</div> : null}
      <div className="conflict__choices">
        {(conflict.claims || []).map((cl) => (
          <Button
            key={cl.memory_id}
            size="sm"
            variant="secondary"
            disabled={disabled}
            title={cl.source_reference || ""}
            onClick={() => onResolve(pr.pr_id, conflict.subject_key, cl.memory_id)}
          >
            Use {cl.currency || ""} {cl.value} &mdash; {cl.source_type}
            {cl.document_date ? `, ${cl.document_date}` : ""}
          </Button>
        ))}
      </div>
    </div>
  );
}

interface Props {
  pr: Pr;
  trace?: TraceEvent[];
  busy: boolean;
  onApprove: (prId: string, role: string, decision: "Approved" | "Rejected") => void;
  onResolve: (prId: string, subject: string, winner: string) => void;
}

export function TaskCard({ pr, trace, busy, onApprove, onResolve }: Props) {
  const cr = pr.coordinator_result;
  const result = cr?.result;
  const [acted, setActed] = useState(false); // buttons are single-use, as before
  const [role, setRole] = useState<string | null>(null);
  const approvalAmount = result?.recommended_total_cost != null ? result.recommended_total_cost : (pr.estimated_budget ?? 0);

  // The amount an approval is checked against is the pipeline's recommended total, NEVER the requester's original
  // estimate (a real recommendation can come out far higher). Mirrors app.procurement.workflow.approval_check_amount.
  useEffect(() => {
    if (pr.workflow_state !== "Waiting For Approval") return;
    let live = true;
    getApprovalLimits()
      .then((limits) => live && setRole(pickQualifyingRole(limits, approvalAmount)))
      .catch(() => {
        /* buttons just will not render if the real policy cannot be fetched */
      });
    return () => {
      live = false;
    };
  }, [pr.workflow_state, approvalAmount]);

  const conflicts = result?.conflicts || [];
  const po = pr.purchase_order;
  const overBudget = result?.recommended_total_cost != null && pr.estimated_budget && result.recommended_total_cost > pr.estimated_budget;
  const act = <A extends unknown[]>(fn: (...a: A) => void) => (...a: A) => {
    setActed(true);
    fn(...a);
  };

  return (
    <div className="task">
      {trace?.length ? (
        <details className="task__trace">
          <summary>
            <ChevronRight size={14} className="task__trace-chevron" /> Live run trace &middot; {trace.length} steps
          </summary>
          <TraceList events={trace} running={false} />
        </details>
      ) : null}

      <div className="task__head">
        <span className="task__id">{pr.pr_id}</span>
        <Badge tone={STATE_TONE[pr.workflow_state] ?? "neutral"} dot>
          {pr.workflow_state}
        </Badge>
      </div>

      {pr.mode === "demo" ? (
        <div className="callout callout--warning">
          <AlertTriangle size={15} /> SIMULATED &mdash; not live ERPNext
        </div>
      ) : null}

      {pr.quantity ? (
        <dl className="task__grid">
          <div><dt>Quantity</dt><dd>{pr.quantity}</dd></div>
          <div><dt>Budget</dt><dd>{money(pr.estimated_budget)}</dd></div>
          <div><dt>Delivery</dt><dd>{pr.delivery_location || "—"}</dd></div>
          <div><dt>Needed by</dt><dd>{pr.required_date || "—"}</dd></div>
        </dl>
      ) : null}

      {pr.workflow_state === "Waiting For Clarification" ? (
        <>
          {conflicts.map((c) => (
            <ConflictBox key={c.subject_key} conflict={c} pr={pr} disabled={acted || busy} onResolve={act(onResolve)} />
          ))}
          <p className="task__hint">Choose the correct price and I&rsquo;ll continue the run.</p>
        </>
      ) : null}

      {pr.blockers?.length && pr.workflow_state !== "Waiting For Clarification" ? (
        <div className="callout callout--info">
          <Info size={15} /> <span><strong>I need:</strong> {pr.blockers.join("; ")}</span>
        </div>
      ) : null}

      {result?.recommended_supplier ? (
        <div className="reco">
          <div className="reco__label"><ShieldCheck size={14} /> Recommended supplier</div>
          <div className="reco__row">
            <span className="reco__name">{result.recommended_supplier}</span>
            <span className="reco__total">{money(result.recommended_total_cost)}</span>
          </div>
        </div>
      ) : null}
      {overBudget ? (
        <div className="callout callout--warning">
          <AlertTriangle size={15} /> This total ({money(result?.recommended_total_cost)}) exceeds the budget you originally stated ({money(pr.estimated_budget)}).
        </div>
      ) : null}
      {cr?.approval_summary ? <p className="task__para">{cr.approval_summary}</p> : null}
      {cr?.error ? (
        <div className="callout callout--danger">
          <X size={15} /> {cr.error}
        </div>
      ) : null}

      {cr?.messages ? <AgentWork messages={cr.messages} /> : null}

      {pr.workflow_state === "Waiting For Approval" && role ? (
        <div className="approve">
          <p className="task__hint">
            Approval authority required for {money(approvalAmount)}: <strong>{role}</strong>.
          </p>
          <div className="approve__btns">
            <Button variant="success" size="sm" disabled={acted || busy} onClick={act(() => onApprove(pr.pr_id, role, "Approved"))}>
              <Check size={15} /> Approve
            </Button>
            <Button variant="danger" size="sm" disabled={acted || busy} onClick={act(() => onApprove(pr.pr_id, role, "Rejected"))}>
              <X size={15} /> Reject
            </Button>
          </div>
        </div>
      ) : null}

      {pr.workflow_state === "Waiting For Information" ? (
        <p className="task__hint">Just reply below with it &mdash; e.g. an item code like DEMO-LAPTOP-14.</p>
      ) : null}

      {/* Live-mode auto-approval skips straight from a recommendation to a submitted PO, so this is the only place that shows. */}
      {pr.workflow_state !== "Waiting For Approval" && pr.workflow_state !== "Waiting For Information" && po ? (
        po.status === "submitted" ? (
          <div className="callout callout--success">
            <Check size={15} />
            <span>
              {pr.mode === "demo" ? "*** SIMULATED -- not live ERPNext *** " : ""}Purchase order submitted automatically (no human review)
              {po.result?.name ? `: ${po.result.name}` : ""}.
            </span>
          </div>
        ) : po.status === "failed" ? (
          <div className="callout callout--danger">
            <X size={15} /> Purchase order was NOT created: {po.error || "unknown error"}
          </div>
        ) : null
      ) : null}
    </div>
  );
}

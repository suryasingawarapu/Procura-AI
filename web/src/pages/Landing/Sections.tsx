import clsx from "clsx";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  ArrowRight, BadgeCheck, Cable, ChevronDown, ClipboardList, FileSearch, FileText, Lock, MessageSquare, PieChart, TriangleAlert,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Logo } from "@/components/Logo";
import { FlowGraph } from "./FlowGraph";
import { LANES } from "./flowData";
import { Reveal, SectionHead } from "./motion";

/* ------------------------------------------------------------------ 01 lifecycle */

const STEPS = [
  { n: "01", h: "Describe the purchase", p: "\"Purchase 10 laptops, 16GB RAM, within ₹6,00,000, delivered to Mumbai within 20 days.\" Plain language, parsed deterministically." },
  { n: "02", h: "Vendra checks eligibility", p: "Vendor discovery, document verification and risk flagging run first — nothing downstream sees a non-compliant supplier." },
  { n: "03", h: "Procura & Finora validate", p: "Real quotations are compared and ranked; the recommended total is checked against real budget and approval-limit policy." },
  { n: "04", h: "Approve & submit", p: "A human approves (or a configured policy auto-approves), and a real Purchase Order is created in ERPNext — never fabricated." },
];

export function Lifecycle() {
  return (
    <section className="lsec" id="how-it-works" aria-labelledby="t-how">
      <SectionHead
        eyebrow="§ 01 · Request lifecycle"
        title={<span id="t-how">From a sentence to a Purchase Order</span>}
        sub="No forms, no multi-system hand-offs — one conversation, the same deterministic path every time."
      />
      <ol className="steps">
        {STEPS.map((s, i) => (
          <Reveal as="li" key={s.n} delay={i * 0.08} className="step card card--hover">
            <span className="step__n">{s.n}</span>
            <h3>{s.h}</h3>
            <p>{s.p}</p>
          </Reveal>
        ))}
      </ol>
    </section>
  );
}

/* ------------------------------------------------------------------ 02 workflow */

export function Workflow() {
  return (
    <section className="lsec lsec--wide" id="workflow" aria-labelledby="t-flow">
      <SectionHead
        eyebrow="§ 02 · End-to-end trace"
        title={<span id="t-flow">One request, followed across two sessions</span>}
        sub="An annotated trace of a single purchase request: the first message, a pause for missing information, a vendor-side update in ERPNext, and the resumed run through to a Purchase Order. The memory keys, states and transitions are the ones the system actually uses; IDs, names and amounts are illustrative."
      />
      <Reveal>
        <FlowGraph />
      </Reveal>
    </section>
  );
}

/* ------------------------------------------------------------------ 03 memory */

const MEMORY_PROPS: { h: string; p: string }[] = [
  { h: "Scoped per agent", p: "An agent's identity is bound in code, never passed by the model, so Vendra cannot read or write Finora's memory." },
  { h: "Atomic versioning", p: "Identical content is a no-op. Changed content supersedes the active version and creates the next one in a single transaction." },
  { h: "Provenance graph", p: "supersedes and derived_from edges record where every fact came from, with ancestry and descendant queries." },
  { h: "Policy-controlled sharing", p: "Who may share with whom lives in the database. A pair that isn't listed is denied, and shared copies are new rows with lineage." },
  { h: "Vector search", p: "Memories can carry an embedding and be searched by similarity (pgvector), limited to the agent's own active memories. Embeddings are generated when an embedding provider is configured." },
  { h: "Stale detection & document expiry", p: "Superseded, archived and expired memories are left out of an agent's context. A supplier's verdict carries the expiry of the first document behind it, so it goes stale by itself when a GST certificate lapses. A shared copy goes stale when its source, or any ancestor along the sharing path, changes." },
  { h: "Summary-first retrieval", p: "Long histories are folded into a summary that records exactly what it covers. Procura reads the summary of an item's quotation history first, then opens the supporting records to see what changed. If something the summary covers changes, it is marked stale and rebuilt, never used." },
  { h: "Conflict detection & resolution", p: "Every quotation is a claim with its source and document date. When an email says ₹50,000 and an updated PDF says ₹53,000, the more authoritative, newer source wins automatically; when that cannot be decided, the task waits and you choose in the chat. The loser is kept, marked disputed." },
  { h: "Agents that use the shared memory", p: "The coordinator relays Vendra's verdicts to Procura, which selects suppliers from what it received; Finora refuses to proceed if the evidence it holds is no longer current. Every hand-off is recorded as data." },
];

const ROW_FIELDS: [string, string, "s" | "n" | "c"][] = [
  ["agent_id", '"vendra"', "s"],
  ["memory_key", '"supplier_eligibility:ACME-TECH"', "s"],
  ["version", "2", "n"],
  ["status", '"active"', "s"],
  ["content", '"ACME Tech (ACME-TECH): compliant"', "s"],
  ["source_type", '"erpnext_doctype"', "s"],
  ["verification_status", '"verified"', "s"],
  ["task_id", '"PR-7F3A"', "s"],
  ["sharing_classification", '"shareable"', "s"],
  ["structured_fact", "{ supplier, compliance, risk }", "c"],
  ["embedding", "vector(...)", "c"],
];

export function Memory() {
  return (
    <section className="lsec" id="memory" aria-labelledby="t-mem">
      <SectionHead
        eyebrow="§ 03 · Persistent memory"
        title={<span id="t-mem">Memory that outlives the conversation</span>}
        sub="Each agent owns a memory space in Postgres (Supabase with pgvector). Writes are atomic and versioned, every change carries its provenance, and nothing is ever overwritten in place."
      />
      <div className="memory">
        <ol className="memory__props">
          {MEMORY_PROPS.map((m, i) => (
            <Reveal as="li" key={m.h} delay={(i % 3) * 0.05} className="mprop">
              <span className="mprop__idx">{String(i + 1).padStart(2, "0")}</span>
              <div>
                <h4>{m.h}</h4>
                <p>{m.p}</p>
              </div>
            </Reveal>
          ))}
        </ol>

        <Reveal className="memory__fig">
          <figure>
            <div className="codecard">
              <div className="codecard__head">
                <span className="codecard__dots"><i /><i /><i /></span>
                <span className="codecard__title">agent_memories · one row</span>
              </div>
              <div className="codecard__rows">
                {ROW_FIELDS.map(([k, v, t]) => (
                  <div key={k} className="codecard__row">
                    <span className="tk-k">{k}</span>
                    <span className={`tk-${t}`}>{v}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="vchain" aria-label="Version chain: version 1 superseded by version 2">
              <div className="vnode vnode--old">
                <span className="vnode__l">v1 · superseded</span>
                <span className="vnode__d">disputed · missing GST</span>
              </div>
              <div className="vedge"><span>supersedes</span></div>
              <div className="vnode vnode--new">
                <span className="vnode__l">v2 · active</span>
                <span className="vnode__d">verified · task PR-7F3A</span>
              </div>
            </div>
            <figcaption className="figure-caption">
              <strong>Figure 2.</strong> A memory row and its version chain. Column names are the real schema; values are illustrative.
            </figcaption>
          </figure>
        </Reveal>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ 04 orchestration */

const PIPE = ["Coordinator", "Vendra", "Procura", "Finora", "Purchase Order"];

export function Pipeline() {
  return (
    <section className="lsec" id="architecture" aria-labelledby="t-arch">
      <SectionHead
        eyebrow="§ 04 · Orchestration"
        title={<span id="t-arch">How a request actually flows</span>}
        sub="The Coordinator runs a fixed LangGraph pipeline — the same three systems, in the same dependency order, every time. No agent ever loops back on another."
      />
      <Reveal>
        <ol className="pipe">
          {PIPE.map((p, i) => (
            <li key={p} className="pipe__item">
              <span className={clsx("pipe__node", i === 0 && "pipe__node--coord", i === PIPE.length - 1 && "pipe__node--end")}>{p}</span>
              {i < PIPE.length - 1 && (
                <span className="pipe__arrow" aria-hidden>
                  <span className="pipe__line" />
                  <span className="pipe__dot" style={{ animationDelay: `${i * 0.5}s` }} />
                  <ArrowRight size={15} />
                </span>
              )}
            </li>
          ))}
        </ol>
      </Reveal>
    </section>
  );
}

/* ------------------------------------------------------------------ 05 agents */

const AGENTS = [
  {
    name: "Vendra", icon: FileSearch, count: "6 sub-agents", lane: "agent",
    tag: "Vendor discovery & compliance — decides who is even eligible before anything else runs.",
    chips: ["vendor_discovery", "document_verification", "vendor_onboarding_readiness", "vendor_profile", "vendor_performance_snapshot", "vendor_risk_flagging"],
  },
  {
    name: "Procura", icon: FileText, count: "6 + 1 gated", lane: "router", featured: true,
    tag: "RFQs, quotations & negotiation — collects and ranks real quotes from eligible suppliers only.",
    chips: ["rfq_management", "quotation_processing", "quotation_comparison", "supplier_matching", "delivery_schedule_estimation", "quotation_history"],
    gated: "submit_purchase_order (approval-gated)",
  },
  {
    name: "Finora", icon: PieChart, count: "6 sub-agents", lane: "orch",
    tag: "Budget & finance — checks the real numbers before anything is ever recommended for approval.",
    chips: ["budget_verification", "cost_analysis", "invoice_matching", "payment_terms_review", "approval_routing", "financial_records"],
  },
];

export function Agents() {
  return (
    <section className="lsec" id="agents" aria-labelledby="t-agents">
      <SectionHead
        eyebrow="§ 05 · The agents"
        title={<span id="t-agents">3 agents, 19 specialist sub-agents</span>}
        sub="Each agent is itself a small orchestrator — running its own fixed pipeline of specialists, then handing its result forward."
      />
      <div className="agents">
        {AGENTS.map((a, i) => (
          <Reveal key={a.name} delay={i * 0.08} className={clsx("agent card card--hover", a.featured && "agent--featured")}>
            <div className="agent__top">
              <span className={`agent__icon lane-${a.lane}`}><a.icon size={20} /></span>
              <span className="agent__count">{a.count}</span>
            </div>
            <h3>{a.name}</h3>
            <p className="agent__tag">{a.tag}</p>
            <div className="chips">
              {a.chips.map((c) => <span key={c} className="chip">{c}</span>)}
              {a.gated ? <span className="chip chip--gated">{a.gated}</span> : null}
            </div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ 06 enterprise */

const FEATURES = [
  { icon: MessageSquare, h: "Conversational, not a form", p: "One message bar. A router decides whether you're asking a question, starting a purchase, or supplying a missing detail." },
  { icon: Cable, h: "Real ERPNext, via MCP", p: "Suppliers, items, RFQs, quotations, and Purchase Orders are created through Frappe's own official MCP endpoint — not a mock." },
  { icon: TriangleAlert, h: "Honest about Live mode", p: "Live mode is always visibly marked and never silently defaults on — a real Purchase Order is one message away, on purpose." },
  { icon: Lock, h: "Role-scoped access", p: "Per-agent API tokens can only ever touch their own resources — every read/write is authorized server-side, not just hidden client-side." },
  { icon: ClipboardList, h: "Full provenance", p: "Every Purchase Request is a versioned record with a complete audit trail — who changed what, and why, never overwritten." },
  { icon: BadgeCheck, h: "Never fabricates", p: "When something isn't built yet or data doesn't exist, the agent says so plainly instead of inventing an answer." },
];

export function Features() {
  return (
    <section className="lsec" id="security" aria-labelledby="t-sec">
      <SectionHead
        eyebrow="§ 06 · Enterprise-grade"
        title={<span id="t-sec">What makes it real, not a demo</span>}
        sub="Every one of these was verified live against a real ERPNext instance, not assumed."
      />
      <div className="features">
        {FEATURES.map((f, i) => (
          <Reveal key={f.h} delay={(i % 3) * 0.07} className="feature card card--hover">
            <span className="feature__icon"><f.icon size={19} /></span>
            <h4>{f.h}</h4>
            <p>{f.p}</p>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ 07 FAQ */

const FAQ = [
  { q: "Is the chat actually calling real agents, or is it scripted?", a: "Every reply is a real API call. Vendor eligibility, quotation ranking and budget checks are deterministic code — never an LLM guess — and the LLM is used only to route your message and narrate the result in plain language." },
  { q: "What's the difference between demo and live mode?", a: "Demo mode runs against a fully isolated synthetic vendor pool and never touches production storage. Live mode talks to a real ERPNext instance and, if configured, can submit a real Purchase Order — it's always visibly flagged in the chat header." },
  { q: "What happens if my request is missing information?", a: "The agent asks for exactly what's missing (e.g. an item code) and resumes the same task once you reply — nothing is restarted or lost." },
  { q: "What happens to a task if I close the browser or the server restarts?", a: "Nothing is lost. The task lives in persistent memory, not in the browser or the process. A follow-up message later recalls the active version from the database and continues from the state it was in." },
  { q: "Is my conversation stored?", a: "In live mode, yes: each chat turn is stored in persistent memory and periodically summarized, so a long conversation is compressed instead of cut off and survives a restart. Deleting a conversation in the sidebar deletes it from the database too. Demo mode stores nothing." },
  { q: "Who can approve a purchase?", a: "Approval authority is checked against a real role-based limit policy and the agent pipeline's actual recommended cost — never a stale original estimate." },
];

export function Faq() {
  const [open, setOpen] = useState<number | null>(0);
  const reduce = useReducedMotion();
  return (
    <section className="lsec lsec--narrow" id="faq" aria-labelledby="t-faq">
      <SectionHead eyebrow="§ 07 · Questions" title={<span id="t-faq">Frequently asked</span>} />
      <Reveal className="faq">
        {FAQ.map((f, i) => {
          const isOpen = open === i;
          return (
            <div key={f.q} className={clsx("faq__item", isOpen && "faq__item--open")}>
              <h3>
                <button
                  type="button"
                  className="faq__q"
                  aria-expanded={isOpen}
                  aria-controls={`faq-a-${i}`}
                  id={`faq-q-${i}`}
                  onClick={() => setOpen(isOpen ? null : i)}
                >
                  <span>{f.q}</span>
                  <ChevronDown size={18} className="faq__chev" />
                </button>
              </h3>
              <AnimatePresence initial={false}>
                {isOpen && (
                  <motion.div
                    id={`faq-a-${i}`}
                    role="region"
                    aria-labelledby={`faq-q-${i}`}
                    initial={reduce ? false : { height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }}
                    transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
                    style={{ overflow: "hidden" }}
                  >
                    <p className="faq__a">{f.a}</p>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          );
        })}
      </Reveal>
    </section>
  );
}

/* ------------------------------------------------------------------ footer */

export function FooterCta() {
  return (
    <section className="fcta" aria-labelledby="t-cta">
      <div className="fcta__glow" aria-hidden />
      <Reveal className="fcta__inner">
        <h2 id="t-cta">Tell the Coordinator what you need.</h2>
        <p>One message in. A recommendation, a comparison and — when you approve — a real Purchase Order out.</p>
        <div className="fcta__actions">
          <Link to="/chat" className="btn btn--primary btn--lg">Chat with Agents <ArrowRight size={17} /></Link>
          <Link to="/observability" className="btn btn--secondary btn--lg">See tracing &amp; cost</Link>
        </div>
      </Reveal>
    </section>
  );
}

export function Footer() {
  return (
    <footer className="lfoot">
      <div className="lfoot__inner">
        <Logo />
        <nav className="lfoot__links" aria-label="Footer">
          <a href="#how-it-works">How it works</a>
          <a href="#workflow">Workflow</a>
          <a href="#memory">Memory</a>
          <a href="#agents">Agents</a>
          <a href="#architecture">Architecture</a>
          <a href="#security">Security</a>
          <Link to="/observability">Observability</Link>
        </nav>
      </div>
    </footer>
  );
}

// Re-exported so the legend/lane names stay in one place if other sections need them.
export { LANES };

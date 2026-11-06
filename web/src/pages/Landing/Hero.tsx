import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ArrowRight, Check, Database, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/Badge";
import { CountUp } from "./motion";

const STATS: { to: number; label: string }[] = [
  { to: 3, label: "Orchestrated agents" },
  { to: 19, label: "Specialist sub-agents" },
  { to: 680, label: "Automated tests passing" },
  { to: 0, label: "Memory records overwritten" },
];

const TRACE = [
  { lane: "vendra", text: "Vendra finished: 3 of 5 candidate suppliers eligible" },
  { lane: "procura", text: "Procura recommends ACME-TECH at ₹5,82,000" },
  { lane: "finora", text: "Finora: within budget · director approval required" },
];
const LAST_STAGE = 5;

/** A decorative, looping preview of a purchase conversation. Illustrative only. */
function PreviewCard() {
  const reduce = useReducedMotion();
  const [stage, setStage] = useState(reduce ? LAST_STAGE : 0);

  useEffect(() => {
    if (reduce) return;
    const id = window.setInterval(() => setStage((s) => (s >= LAST_STAGE + 2 ? 0 : s + 1)), 1500);
    return () => window.clearInterval(id);
  }, [reduce]);

  return (
    <div className="preview" aria-label="Illustrative preview of a purchase conversation" role="img">
      <div className="preview__chrome">
        <span className="preview__dots"><i /><i /><i /></span>
        <span className="preview__title">Procurement Coordinator</span>
        <Badge tone="warning" dot>Live</Badge>
      </div>
      <div className="preview__body">
        <div className="pv-msg pv-msg--user">
          Purchase 10 laptops with 16 GB RAM, within ₹6,00,000, delivered to our Mumbai office within 20 days.
        </div>

        <AnimatePresence initial={false}>
          {stage >= 1 && (
            <motion.ol key="trace" className="pv-trace" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
              {TRACE.slice(0, Math.min(3, stage)).map((t, i) => (
                <motion.li key={t.lane} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.35 }}>
                  <span className={`pv-trace__mark${stage - 1 === i && stage <= 3 ? " pv-trace__mark--run" : ""}`}>
                    {stage - 1 === i && stage <= 3 ? null : <Check size={10} strokeWidth={3.2} />}
                  </span>
                  <span className={`lane lane-agent`}>{t.lane}</span>
                  <span className="pv-trace__text">{t.text}</span>
                </motion.li>
              ))}
            </motion.ol>
          )}

          {stage >= 4 && (
            <motion.div key="rec" className="pv-rec" initial={{ opacity: 0, y: 12, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0 }}>
              <div className="pv-rec__head">
                <span className="pv-rec__id">PR-7F3A</span>
                <Badge tone="accent" dot>Waiting For Approval</Badge>
              </div>
              <div className="pv-rec__grid">
                <div><span>Quantity</span><b>10</b></div>
                <div><span>Budget</span><b>₹6,00,000</b></div>
                <div><span>Delivery</span><b>Mumbai</b></div>
                <div><span>Needed by</span><b>+20 days</b></div>
              </div>
              <div className="pv-rec__reco">
                <ShieldCheck size={15} /> <span><b>Recommended:</b> ACME-TECH · <b>Total:</b> ₹5,82,000</span>
              </div>
              <div className="pv-rec__actions">
                <span className={`pv-btn pv-btn--ok${stage >= 5 ? " pv-btn--pulse" : ""}`}>Approve</span>
                <span className="pv-btn">Reject</span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <div className="preview__foot">
        <Database size={13} /> Saved to memory · purchase_request v1 → v2
        <span className="preview__illus">Illustrative</span>
      </div>
    </div>
  );
}

export function Hero() {
  const ref = useRef<HTMLElement>(null);
  const reduce = useReducedMotion();

  const onMove = (e: React.MouseEvent) => {
    if (reduce || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    ref.current.style.setProperty("--mx", `${((e.clientX - r.left) / r.width) * 100}%`);
    ref.current.style.setProperty("--my", `${((e.clientY - r.top) / r.height) * 100}%`);
  };

  return (
    <section className="hero" ref={ref} onMouseMove={onMove} aria-labelledby="hero-title">
      <div className="hero__mesh" aria-hidden />
      <div className="hero__grid" aria-hidden />
      <div className="hero__cursor" aria-hidden />

      <div className="hero__inner">
        <div className="hero__copy">
          <motion.span
            className="hero__eyebrow"
            initial={reduce ? false : { opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
          >
            <span className="pulse-dot" />
            Multi-agent procurement · persistent, provenance-tracked memory
          </motion.span>

          <motion.h1
            id="hero-title"
            initial={reduce ? false : { opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.65, delay: 0.08, ease: [0.22, 1, 0.36, 1] }}
          >
            One instruction in.
            <br />
            A real purchase, <span className="grad">handled end to end</span>.
          </motion.h1>

          <motion.p
            className="hero__lede"
            initial={reduce ? false : { opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.65, delay: 0.18, ease: [0.22, 1, 0.36, 1] }}
          >
            Type what you need to buy in plain language. A deterministic coordinator routes it through Vendra, Procura, and Finora — 19
            specialist checks in total — against your actual ERPNext data, and comes back with a genuine recommendation, a real quotation
            comparison, and (in Live mode) a real Purchase Order. Every agent writes what it learns into a versioned, provenance-tracked
            memory — so a task paused today resumes tomorrow exactly where it stopped, and every decision can be traced back to the
            record that produced it.
          </motion.p>

          <motion.div
            className="hero__actions"
            initial={reduce ? false : { opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.65, delay: 0.28, ease: [0.22, 1, 0.36, 1] }}
          >
            <Link to="/chat" className="btn btn--primary btn--lg">
              Chat with Agents <ArrowRight size={17} />
            </Link>
            <a href="#how-it-works" className="btn btn--secondary btn--lg">See how it works</a>
          </motion.div>
        </div>

        <motion.div
          className="hero__visual"
          initial={reduce ? false : { opacity: 0, y: 28, rotateX: 6 }}
          animate={{ opacity: 1, y: 0, rotateX: 0 }}
          transition={{ duration: 0.9, delay: 0.3, ease: [0.22, 1, 0.36, 1] }}
        >
          <PreviewCard />
        </motion.div>
      </div>

      <dl className="hero__stats">
        {STATS.map((s) => (
          <div className="hero__stat" key={s.label}>
            <dt className="hero__stat-num"><CountUp to={s.to} /></dt>
            <dd className="hero__stat-label">{s.label}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

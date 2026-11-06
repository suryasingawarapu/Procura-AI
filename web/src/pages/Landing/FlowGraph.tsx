import clsx from "clsx";
import { ChevronLeft, ChevronRight, Pause, Play, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check } from "lucide-react";
import {
  CANVAS_H, CANVAS_W, COL, EDGES, ICONS, LANES, NODES, NODE_H, NODE_W, ORDER, ROW, STICKIES,
  type FlowEdge, type FlowNode,
} from "./flowData";

type Placed = FlowNode & { x: number; y: number };
type Pt = { x: number; y: number };

const byId: Record<string, Placed> = Object.fromEntries(NODES.map((n) => [n.id, { ...n, x: COL(n.col), y: ROW[n.row] }]));
const rank = (id: string) => ORDER.indexOf(id);

const cubicMid = (p0: Pt, p1: Pt, p2: Pt, p3: Pt): Pt => ({
  x: (p0.x + 3 * p1.x + 3 * p2.x + p3.x) / 8,
  y: (p0.y + 3 * p1.y + 3 * p2.y + p3.y) / 8,
});

function edgeGeometry(e: FlowEdge): { d: string; mid: Pt } {
  const a = byId[e.f];
  const b = byId[e.t];
  if (e.v) {
    const down = b.y > a.y;
    const p0 = { x: a.x + NODE_W / 2, y: down ? a.y + NODE_H : a.y };
    const p3 = { x: b.x + NODE_W / 2, y: down ? b.y : b.y + NODE_H };
    const c = Math.abs(p3.y - p0.y) * 0.5 * (down ? 1 : -1);
    const p1 = { x: p0.x, y: p0.y + c };
    const p2 = { x: p3.x, y: p3.y - c };
    return { d: `M${p0.x},${p0.y} C${p1.x},${p1.y} ${p2.x},${p2.y} ${p3.x},${p3.y}`, mid: cubicMid(p0, p1, p2, p3) };
  }
  const x0 = a.x + NODE_W;
  const y0 = a.y + NODE_H / 2;
  const x3 = b.x;
  const y3 = b.y + NODE_H / 2;
  if (x3 > x0) {
    const c = Math.max(30, (x3 - x0) * 0.5);
    const p0 = { x: x0, y: y0 }, p1 = { x: x0 + c, y: y0 }, p2 = { x: x3 - c, y: y3 }, p3 = { x: x3, y: y3 };
    return { d: `M${x0},${y0} C${p1.x},${p1.y} ${p2.x},${p2.y} ${x3},${y3}`, mid: cubicMid(p0, p1, p2, p3) };
  }
  // Backward edge onto a later row: out to the right, along the gap between rows, then down into the target.
  const r = 12;
  const gx0 = x0 + 24;
  const gx3 = x3 - 24;
  const gy = (a.y + NODE_H + b.y) / 2;
  const d = [
    `M${x0},${y0}`, `L${gx0 - r},${y0}`, `Q${gx0},${y0} ${gx0},${y0 + r}`,
    `L${gx0},${gy - r}`, `Q${gx0},${gy} ${gx0 - r},${gy}`,
    `L${gx3 + r},${gy}`, `Q${gx3},${gy} ${gx3},${gy + r}`,
    `L${gx3},${y3 - r}`, `Q${gx3},${y3} ${gx3 + r},${y3}`, `L${x3},${y3}`,
  ].join(" ");
  return { d, mid: { x: gx0 - 110, y: gy } };
}

const GEOMS = EDGES.map(edgeGeometry);

const PAD = 14;
const STICKY_BOXES = STICKIES.map((s) => {
  const xs = s.ids.map((id) => byId[id].x);
  const ys = s.ids.map((id) => byId[id].y);
  const left = Math.min(...xs) - PAD;
  const top = Math.min(...ys) - 32;
  return { ...s, left, top, width: Math.max(...xs) + NODE_W + PAD - left, height: Math.max(...ys) + NODE_H + PAD - top };
});

type EdgeState = "idle" | "active" | "done";

export function FlowGraph() {
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [scale, setScale] = useState(1);
  const viewportRef = useRef<HTMLDivElement>(null);
  const startedRef = useRef(false);
  const stepRef = useRef(0);
  stepRef.current = step;

  const last = ORDER.length - 1;

  // Fit the fixed-size canvas to its container.
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const fit = () => setScale(Math.max(0.6, Math.min(1, (el.clientWidth - 24) / CANVAS_W)));
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Playback.
  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => {
      if (stepRef.current >= last) {
        setPlaying(false);
        return;
      }
      setStep((s) => s + 1);
    }, 1700);
    return () => window.clearInterval(id);
  }, [playing, last]);

  // Start playing the first time the graph scrolls into view (unless the user prefers reduced motion).
  useEffect(() => {
    const el = viewportRef.current;
    if (!el || !("IntersectionObserver" in window)) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting) && !startedRef.current) {
          startedRef.current = true;
          io.disconnect();
          setPlaying(true);
        }
      },
      { threshold: 0.35 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  // On narrow screens the canvas pans: keep the active node in view.
  useEffect(() => {
    const vp = viewportRef.current;
    if (!vp || vp.scrollWidth <= vp.clientWidth + 4) return;
    const n = byId[ORDER[step]];
    vp.scrollTo({ left: Math.max(0, (n.x + NODE_W / 2) * scale - vp.clientWidth / 2), behavior: "smooth" });
  }, [step, scale]);

  const goTo = useCallback((i: number) => {
    setPlaying(false);
    startedRef.current = true;
    setStep(Math.max(0, Math.min(last, i)));
  }, [last]);

  const togglePlay = () => {
    if (playing) return setPlaying(false);
    startedRef.current = true;
    if (step >= last) setStep(0);
    setPlaying(true);
  };

  const edgeState = (e: FlowEdge): EdgeState => {
    const r = rank(e.t);
    return r < step ? "done" : r === step && rank(e.f) < step ? "active" : "idle";
  };

  const n = byId[ORDER[step]];
  const markers = useMemo(() => ["idle", "active", "done"] as const, []);

  return (
    <div className="flow">
      <div className="flow__legend" aria-hidden>
        {(Object.keys(LANES) as (keyof typeof LANES)[]).map((k) => (
          <span key={k} className={`lane lane-${k}`}>{LANES[k]}</span>
        ))}
      </div>

      <div className="flow__toolbar">
        <button type="button" className="btn btn--primary btn--sm" onClick={togglePlay}>
          {playing ? <Pause size={14} /> : step >= last ? <RotateCcw size={14} /> : <Play size={14} />}
          {playing ? "Pause" : step >= last ? "Replay" : "Play trace"}
        </button>
        <button type="button" className="btn btn--secondary btn--sm" onClick={() => goTo(step - 1)} aria-label="Previous step" disabled={step === 0}>
          <ChevronLeft size={15} /> Prev
        </button>
        <button type="button" className="btn btn--secondary btn--sm" onClick={() => goTo(step + 1)} aria-label="Next step" disabled={step === last}>
          Next <ChevronRight size={15} />
        </button>
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => goTo(0)}>
          <RotateCcw size={14} /> Restart
        </button>
        <div className="flow__progress" aria-live="off">
          <span className="flow__progress-track"><span style={{ width: `${((step + 1) / ORDER.length) * 100}%` }} /></span>
          <span className="flow__progress-text">Step {step + 1} of {ORDER.length}</span>
        </div>
      </div>

      <div className="flow__viewport" ref={viewportRef}>
        <div className="flow__scale" style={{ width: CANVAS_W * scale, height: CANVAS_H * scale }}>
          <div className="flow__canvas" style={{ width: CANVAS_W, height: CANVAS_H, transform: `scale(${scale})` }}>
            {STICKY_BOXES.map((s) => (
              <div key={s.title} className="fsticky" style={{ left: s.left, top: s.top, width: s.width, height: s.height }}>
                <span className={clsx("fsticky__title", s.right && "fsticky__title--right")}>{s.title}</span>
              </div>
            ))}

            <svg className="flow__edges" width={CANVAS_W} height={CANVAS_H} viewBox={`0 0 ${CANVAS_W} ${CANVAS_H}`} aria-hidden>
              <defs>
                {markers.map((s) => (
                  <marker key={s} id={`farrow-${s}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                    <path d="M0,0 L10,5 L0,10 z" className={`farrow farrow--${s}`} />
                  </marker>
                ))}
              </defs>
              {EDGES.map((e, i) => {
                const s = edgeState(e);
                return (
                  <path
                    key={i}
                    d={GEOMS[i].d}
                    className={clsx("fedge", `fedge--${s}`, e.data && "fedge--data", s === "active" && playing && "fedge--flow")}
                    markerEnd={`url(#farrow-${s})`}
                  />
                );
              })}
            </svg>

            {EDGES.map((e, i) =>
              e.label ? (
                <div key={i} className={clsx("flabel", `flabel--${edgeState(e)}`)} style={{ left: GEOMS[i].mid.x, top: GEOMS[i].mid.y }}>
                  {e.label}
                </div>
              ) : null,
            )}

            {NODES.map((node) => {
              const p = byId[node.id];
              const r = rank(node.id);
              const state = r < step ? "done" : r === step ? "active" : "idle";
              return (
                <div
                  key={node.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`${node.title}: ${node.sub}`}
                  aria-current={state === "active" ? "step" : undefined}
                  className={clsx("fnode", `lane-${node.lane}`, `fnode--${state}`)}
                  style={{ left: p.x, top: p.y, width: NODE_W, height: NODE_H }}
                  onClick={() => goTo(r)}
                  onKeyDown={(ev) => {
                    if (ev.key === "Enter" || ev.key === " ") {
                      ev.preventDefault();
                      goTo(r);
                    }
                  }}
                >
                  <span className="fh fh--l" />
                  <span className="fh fh--r" />
                  <div className="fnode__icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <path d={ICONS[node.icon]} />
                    </svg>
                  </div>
                  <div className="fnode__text">
                    <div className="fnode__title">{node.title}</div>
                    <div className="fnode__sub">{node.sub}</div>
                  </div>
                  <span className="fnode__check"><Check size={11} strokeWidth={3} /></span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="flow__detail" aria-live="polite" key={n.id}>
        <div className="flow__detail-main">
          <div className="flow__detail-meta">
            <span className={`lane lane-${n.lane}`}>{LANES[n.lane]}</span>
            <span className="flow__detail-step">STEP {String(step + 1).padStart(2, "0")}</span>
          </div>
          <h4>{n.head}</h4>
          <p>{n.body}</p>
        </div>
        <pre className="flow__detail-code">{n.code}</pre>
      </div>

      <p className="figure-caption">
        <strong>Figure 1.</strong> One request traced across two sessions, as a node graph. Solid edges are control flow; dashed edges are
        data read from or written to memory and ERPNext. Click any node, or press Play to step through. Nothing in Session 2 depends on
        the browser tab, process or server instance that handled Session 1; the only shared state is persistent memory and ERPNext.
      </p>
    </div>
  );
}

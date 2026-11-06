// n8n-style node graph for the landing page's end-to-end trace. Plain DOM +
// one SVG layer for edges; no library. Nodes are laid out on a fixed grid
// and the whole canvas is scaled to fit its container.
(() => {
  const canvas = document.getElementById("flowCanvas");
  if (!canvas) return;

  const NODE_W = 188;
  const NODE_H = 78;
  const COL = (c) => 40 + c * 248;
  const ROW = [60, 250, 440, 612];
  const W = 1510;
  const H = 724;

  const ICONS = {
    chat: '<path d="M4 5h16v11H9l-5 4z"/>',
    route: '<circle cx="5" cy="12" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="19" cy="18" r="2"/><path d="M7 12h4l6-6M11 12l6 6"/>',
    db: '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
    graph: '<circle cx="12" cy="5" r="2.5"/><circle cx="5" cy="19" r="2.5"/><circle cx="19" cy="19" r="2.5"/><path d="M11 7.2 6.2 16.8M13 7.2l4.8 9.6M7.5 19h9"/>',
    cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
    cube: '<path d="M12 2l9 5v10l-9 5-9-5V7z"/><path d="M3 7l9 5 9-5M12 12v10"/>',
    truck: '<path d="M2 6h12v10H2zM14 10h4l3 3v3h-7"/><circle cx="6" cy="18" r="2"/><circle cx="17" cy="18" r="2"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
    reply: '<path d="M10 8 4 13l6 5M4 13h11a5 5 0 0 0 5-5V6"/>',
    file: '<path d="M6 2h8l5 5v15H6z"/><path d="M14 2v5h5"/>',
  };

  const LANES = {
    user: "Requester", router: "Router", orch: "Orchestrator", agent: "Agent",
    erp: "ERPNext", mem: "Memory", vendor: "Vendor",
  };

  const NODES = [
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

  // v: vertical (bottom/top handles); data: dashed (a read/write, not control flow)
  const EDGES = [
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

  const STICKIES = [
    { title: "Session 1 · Day 1, 10:02", ids: ["chat1", "router1", "mem1", "orch1", "reply1"] },
    { title: "Overnight · vendor side", ids: ["vendor", "erpdoc"] },
    { title: "Session 2 · Day 2, 09:40", ids: ["chat2", "router2", "memread", "orch2"] },
    { title: "Agent pipeline · LangGraph", ids: ["vendra", "mem2", "erpq", "procura", "finora", "approval", "po", "mem3"], right: true },
  ];

  const ORDER = ["chat1", "router1", "mem1", "orch1", "reply1", "wait", "vendor", "erpdoc", "chat2", "router2",
    "memread", "orch2", "vendra", "mem2", "erpq", "procura", "finora", "approval", "po", "mem3"];

  const byId = Object.fromEntries(NODES.map((n) => [n.id, { ...n, x: COL(n.col), y: ROW[n.row] }]));
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // -- edge geometry ---------------------------------------------------------

  function cubicMid(p0, p1, p2, p3) {
    return { x: (p0.x + 3 * p1.x + 3 * p2.x + p3.x) / 8, y: (p0.y + 3 * p1.y + 3 * p2.y + p3.y) / 8 };
  }

  function edgeGeometry(e) {
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
    // Backward edge onto a later row: route it the way n8n does -- out to
    // the right, along the gap between rows, then down into the target.
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

  // -- render ----------------------------------------------------------------

  canvas.style.width = `${W}px`;
  canvas.style.height = `${H}px`;

  const PAD = 14;
  const stickyHtml = STICKIES.map((s) => {
    const xs = s.ids.map((id) => byId[id].x);
    const ys = s.ids.map((id) => byId[id].y);
    const left = Math.min(...xs) - PAD;
    const top = Math.min(...ys) - 32;
    const width = Math.max(...xs) + NODE_W + PAD - left;
    const height = Math.max(...ys) + NODE_H + PAD - top;
    return `<div class="fsticky" style="left:${left}px;top:${top}px;width:${width}px;height:${height}px"><span class="fsticky-title${s.right ? " right" : ""}">${esc(s.title)}</span></div>`;
  }).join("");

  const geoms = EDGES.map(edgeGeometry);
  const svg = `<svg class="flow-edges" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">
    <defs>
      ${["idle", "active", "done"].map((s) => `<marker id="farrow-${s}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="farrow farrow-${s}"/></marker>`).join("")}
    </defs>
    ${EDGES.map((e, i) => `<path class="fedge${e.data ? " data" : ""}" data-edge="${i}" d="${geoms[i].d}" marker-end="url(#farrow-idle)"/>`).join("")}
  </svg>`;

  const labelHtml = EDGES.map((e, i) => e.label
    ? `<div class="flabel" data-label="${i}" style="left:${geoms[i].mid.x}px;top:${geoms[i].mid.y}px">${esc(e.label)}</div>` : "").join("");

  const nodeHtml = NODES.map((n) => {
    const p = byId[n.id];
    return `<div class="fnode lane-${n.lane}" data-node="${n.id}" role="button" tabindex="0" aria-label="${esc(n.title)}: ${esc(n.sub)}" style="left:${p.x}px;top:${p.y}px">
      <span class="fh fh-l"></span><span class="fh fh-r"></span>
      <div class="fnode-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[n.icon]}</svg></div>
      <div class="fnode-text"><div class="fnode-title">${esc(n.title)}</div><div class="fnode-sub">${esc(n.sub)}</div></div>
      <span class="fnode-check">&#10003;</span>
    </div>`;
  }).join("");

  canvas.innerHTML = stickyHtml + svg + labelHtml + nodeHtml;

  // -- state -------------------------------------------------------------------

  const viewport = document.getElementById("flowViewport");
  const scaleBox = document.getElementById("flowScale");
  const detail = document.getElementById("flowDetail");
  const progress = document.getElementById("flowProgress");
  const playBtn = document.getElementById("flowPlay");
  let step = 0;
  let timer = null;

  function render() {
    const rank = (id) => ORDER.indexOf(id);
    canvas.querySelectorAll(".fnode").forEach((el) => {
      const r = rank(el.dataset.node);
      el.classList.toggle("done", r < step);
      el.classList.toggle("active", r === step);
    });
    EDGES.forEach((e, i) => {
      const r = rank(e.t);
      const state = r < step ? "done" : r === step && rank(e.f) < step ? "active" : "idle";
      const path = canvas.querySelector(`[data-edge="${i}"]`);
      path.classList.remove("idle", "active", "done");
      path.classList.add(state);
      path.setAttribute("marker-end", `url(#farrow-${state})`);
      const label = canvas.querySelector(`[data-label="${i}"]`);
      if (label) {
        label.classList.remove("idle", "active", "done");
        label.classList.add(state);
      }
    });
    const n = byId[ORDER[step]];
    // On narrow screens the canvas pans; keep the active node in view.
    if (viewport.scrollWidth > viewport.clientWidth + 4) {
      const target = (n.x + NODE_W / 2) * scale - viewport.clientWidth / 2;
      viewport.scrollTo({ left: Math.max(0, target), behavior: "smooth" });
    }
    progress.textContent = `Step ${step + 1} of ${ORDER.length}`;
    detail.innerHTML = `
      <div class="flow-detail-main">
        <div class="flow-detail-meta"><span class="lane lane-${n.lane}">${esc(LANES[n.lane])}</span><span class="flow-detail-step">STEP ${String(step + 1).padStart(2, "0")}</span></div>
        <h4>${esc(n.head)}</h4>
        <p>${esc(n.body)}</p>
      </div>
      <pre class="flow-detail-code">${esc(n.code)}</pre>`;
  }

  function stop() {
    clearInterval(timer);
    timer = null;
    playBtn.innerHTML = step >= ORDER.length - 1 ? "&#8634; Replay" : "&#9654; Play trace";
  }

  function play() {
    if (step >= ORDER.length - 1) step = 0;
    render();
    playBtn.innerHTML = "&#10073;&#10073; Pause";
    timer = setInterval(() => {
      if (step >= ORDER.length - 1) return stop();
      step++;
      render();
    }, 1700);
  }

  function goTo(i) {
    stop();
    step = Math.max(0, Math.min(ORDER.length - 1, i));
    render();
    stop();
  }

  playBtn.addEventListener("click", () => (timer ? stop() : play()));
  document.getElementById("flowPrev").addEventListener("click", () => goTo(step - 1));
  document.getElementById("flowNext").addEventListener("click", () => goTo(step + 1));
  document.getElementById("flowRestart").addEventListener("click", () => goTo(0));
  canvas.addEventListener("click", (e) => {
    const el = e.target.closest(".fnode");
    if (el) goTo(ORDER.indexOf(el.dataset.node));
  });
  canvas.addEventListener("keydown", (e) => {
    const el = e.target.closest(".fnode");
    if (el && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      goTo(ORDER.indexOf(el.dataset.node));
    }
  });

  // -- fit the fixed-size canvas to its container ------------------------------

  let scale = 1;
  function fit() {
    const avail = viewport.clientWidth - 24;
    scale = Math.max(0.6, Math.min(1, avail / W));
    canvas.style.transform = `scale(${scale})`;
    scaleBox.style.width = `${W * scale}px`;
    scaleBox.style.height = `${H * scale}px`;
  }
  window.addEventListener("resize", fit);
  fit();
  render();

  // Start playing the first time the graph scrolls into view.
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!reduced && "IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => {
      if (entries.some((en) => en.isIntersecting)) {
        io.disconnect();
        if (!timer && step === 0) play();
      }
    }, { threshold: 0.35 });
    io.observe(viewport);
  }
})();

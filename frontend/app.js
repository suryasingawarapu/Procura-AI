// Plain fetch-based client over the real FastAPI backend. No build step,
// no framework -- every call below hits a real endpoint in app/api/.
// The browser never holds the API token: the backend authorizes this
// bundled UI itself (UI_LOCAL_AUTH, loopback only -- see app/api/auth.py).

const API_BASE = ""; // same origin as this page (FastAPI serves both)
const DEFAULT_REQUESTED_BY = "jane.doe@example.com"; // no visible field for this anymore, per explicit request

// Earlier versions of this page saved a token in localStorage; drop it.
try {
  localStorage.removeItem("apiToken");
} catch (e) {
  /* storage blocked -- nothing to clean up */
}

const AUTH_HELP =
  "The backend did not authorize this page. Set UI_LOCAL_AUTH=1 in the server's .env, open the app at http://127.0.0.1:8000, and restart the server.";

async function apiFetch(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const response = await fetch(API_BASE + path, { ...options, headers });
  let data;
  try {
    data = await response.json();
  } catch (e) {
    data = { detail: "(no JSON body)" };
  }
  if (!response.ok) {
    const detail = response.status === 401 ? AUTH_HELP : data.detail || `HTTP ${response.status}`;
    const err = new Error(detail);
    err.status = response.status;
    err.data = data;
    throw err;
  }
  return data;
}

// POST to a Server-Sent Events endpoint and dispatch each event to
// handlers[eventName](data) as it arrives. (EventSource can't POST.)
async function streamPost(path, body, handlers) {
  const response = await fetch(API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let data = {};
    try {
      data = await response.json();
    } catch (e) {
      /* no JSON body */
    }
    throw new Error(response.status === 401 ? AUTH_HELP : data.detail || `HTTP ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (handlers[event]) await handlers[event](JSON.parse(data || "null"));
    }
  }
}

// -- Procurement Assistant: a real chat, not a form ---------------------
//
// No "Start" button to click separately -- sending a message in the bar
// below IS the instruction. Every reply is a real call: a brand-new
// message becomes POST /procurement/chat (an LLM router decides what it
// needs); a short follow-up while a task is "Waiting For Information" is
// recognized as the missing detail. Nothing here is scripted -- every
// assistant bubble reflects an actual API response.

let chatCurrentPr = null;
let chatHistory = []; // rolling {role, content} sent to the backend's LLM router, capped

function pushHistory(role, content) {
  chatHistory.push({ role, content });
  if (chatHistory.length > 12) chatHistory = chatHistory.slice(-12);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// -- lightweight markdown (bold/italic/code/lists/tables) -----------------
//
// No library, no build step -- just enough to make an LLM's plain-text
// reply (or a narration sentence) read naturally instead of as one raw
// escaped blob. Input is escaped FIRST, so markdown syntax characters
// (*, `, |) are the only things ever interpreted as markup; nothing here
// ever lets through a real HTML tag from the model or the user.

function renderInlineMarkdown(escaped) {
  let s = escaped.replace(/`([^`]+)`/g, (_, code) => `<code>${code}</code>`);
  s = s.replace(/\*\*([^\n*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  return s;
}

function tableCells(line) {
  return line.split("|").map((c) => c.trim()).filter((c) => c.length);
}

// A line-by-line state machine, not a blank-line block splitter -- a lead-in
// line immediately followed by a list (no blank line between them, which is
// exactly how an LLM tends to write "Key points:\n- one\n- two") must still
// become a <p> plus a real <ul>, not one paragraph with literal "-" dashes.
function renderMarkdown(raw) {
  const lines = escapeHtml(raw).split("\n");
  const out = [];
  let para = [];
  const flushPara = () => {
    if (para.length) out.push(`<p>${renderInlineMarkdown(para.join("<br>"))}</p>`);
    para = [];
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) {
      flushPara();
      i++;
      continue;
    }

    if (line.includes("|") && lines[i + 1] && /^[\s|:-]+$/.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      flushPara();
      const header = tableCells(line);
      const rows = [];
      let j = i + 2;
      while (j < lines.length && lines[j].trim() && lines[j].includes("|")) {
        rows.push(tableCells(lines[j]));
        j++;
      }
      out.push(
        `<table class="md-table"><thead><tr>${header.map((c) => `<th>${renderInlineMarkdown(c)}</th>`).join("")}</tr></thead>` +
          `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${renderInlineMarkdown(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`
      );
      i = j;
      continue;
    }

    if (/^[-*]\s+/.test(line)) {
      flushPara();
      const items = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^[-*]\s+/, ""));
        i++;
      }
      out.push(`<ul>${items.map((it) => `<li>${renderInlineMarkdown(it)}</li>`).join("")}</ul>`);
      continue;
    }

    if (/^\d+\.\s+/.test(line)) {
      flushPara();
      const items = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\d+\.\s+/, ""));
        i++;
      }
      out.push(`<ol>${items.map((it) => `<li>${renderInlineMarkdown(it)}</li>`).join("")}</ol>`);
      continue;
    }

    para.push(line);
    i++;
  }
  flushPara();
  return out.join("");
}

function appendChatMessage(role, html) {
  const log = document.getElementById("chatLog");
  const el = document.createElement("div");
  el.className = `chat-msg ${role}`;
  el.innerHTML = html;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  saveActiveConversationMessage(role, html);
  return el;
}

// A small rotating status line under the typing dots -- purely cosmetic
// (never asserts a specific agent actually ran; the real per-agent result
// only ever comes from renderAgentWork once the real response arrives).
const THINKING_PHRASES = [
  "Reading your message...",
  "Routing to the right agent...",
  "Checking real ERPNext data...",
  "Coordinating Vendra, Procura & Finora...",
];

function startThinkingAnimation(el) {
  el.innerHTML = `<div class="typing-indicator"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div><div class="typing-status"></div>`;
  const statusEl = el.querySelector(".typing-status");
  let i = 0;
  statusEl.textContent = THINKING_PHRASES[0];
  const timer = setInterval(() => {
    i = (i + 1) % THINKING_PHRASES.length;
    statusEl.style.opacity = "0";
    setTimeout(() => {
      statusEl.textContent = THINKING_PHRASES[i];
      statusEl.style.opacity = "1";
    }, 150);
  }, 1300);
  return () => clearInterval(timer);
}

function metaRowHtml() {
  const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `<div class="chat-msg-meta"><span>${time}</span><button type="button" class="msg-copy-btn" data-copy-msg="1">&#128203; Copy</button></div>`;
}

// -- conversations (sidebar history) -------------------------------------
//
// The backend has no notion of a "conversation" -- only Purchase Request
// tasks. Conversation history here is a client-side convenience
// (localStorage, this browser only) so you can come back to an earlier
// thread; it stores the real rendered replies this session already
// received, never re-synthesizes anything.

const CONV_KEY = "procurement_conversations";
let conversations = [];
let activeConversationId = null;

function loadConversations() {
  try {
    const raw = localStorage.getItem(CONV_KEY);
    conversations = raw ? JSON.parse(raw) : [];
  } catch (e) {
    conversations = [];
  }
}

function saveConversations() {
  try {
    localStorage.setItem(CONV_KEY, JSON.stringify(conversations));
  } catch (e) {
    /* storage full/blocked -- conversation history just won't persist across reloads */
  }
}

function renderConversationList() {
  document.getElementById("chatCountBadge").textContent = String(conversations.length);
  const list = document.getElementById("conversationList");
  if (!conversations.length) {
    list.innerHTML = '<div class="conversation-empty">No conversations yet.</div>';
    return;
  }
  const sorted = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt);
  list.innerHTML = sorted
    .map(
      (c) => `<div class="conversation-item${c.id === activeConversationId ? " active" : ""}" data-conv="${c.id}">
        <div class="conversation-item-main">
          <div class="conversation-title">${escapeHtml(c.title)}</div>
          <div class="conversation-date">${new Date(c.updatedAt).toLocaleDateString()}</div>
        </div>
        <button type="button" class="conversation-delete-btn" data-delete-conv="${c.id}" title="Delete this conversation" aria-label="Delete this conversation">&times;</button>
      </div>`
    )
    .join("");
}

// Really removes it -- this browser's localStorage is the only place a
// conversation is ever stored, so deleting it here deletes it completely,
// not just hiding it from the list.
function deleteConversation(id) {
  if (!window.confirm("Delete this conversation? This can't be undone.")) return;
  conversations = conversations.filter((c) => c.id !== id);
  saveConversations();
  // Also remove what the server stored for it (its turns and summaries in memory).
  fetch(`${API_BASE}/procurement/chat/conversations/${encodeURIComponent(id)}`, { method: "DELETE" }).catch(() => {});
  if (activeConversationId === id) {
    startNewConversation();
  } else {
    renderConversationList();
  }
}

function startNewConversation() {
  activeConversationId = null;
  chatCurrentPr = null;
  chatHistory = [];
  document.getElementById("chatLog").innerHTML = "";
  document.getElementById("chatLog").style.display = "none";
  document.getElementById("emptyState").style.display = "flex";
  document.getElementById("chatInput").value = "";
  renderConversationList();
}

function ensureActiveConversation(firstUserMessage) {
  if (activeConversationId) return;
  const id = `conv-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const conv = {
    id,
    title: firstUserMessage.length > 60 ? firstUserMessage.slice(0, 57) + "..." : firstUserMessage,
    updatedAt: Date.now(),
    messages: [],
  };
  conversations.push(conv);
  activeConversationId = id;
  saveConversations();
  renderConversationList();
}

function saveActiveConversationMessage(role, html) {
  if (!activeConversationId) return;
  const conv = conversations.find((c) => c.id === activeConversationId);
  if (!conv) return;
  conv.messages.push({ role, html });
  conv.updatedAt = Date.now();
  saveConversations();
  renderConversationList();
}

function rememberConversationPr(prId) {
  const conv = conversations.find((c) => c.id === activeConversationId);
  if (conv && conv.prId !== prId) {
    conv.prId = prId;
    saveConversations();
  }
}

function openConversation(id) {
  const conv = conversations.find((c) => c.id === id);
  if (!conv) return;
  activeConversationId = id;
  // The transcript shown is stored locally, but the task it was working on is remembered by id, so a follow-up
  // ("LAPTOP-14IN") still reaches that task; the server re-reads the task's real, current state from its id.
  chatCurrentPr = conv.prId ? { pr_id: conv.prId } : null;
  chatHistory = [];
  const log = document.getElementById("chatLog");
  log.innerHTML = "";
  for (const m of conv.messages) {
    const el = document.createElement("div");
    el.className = `chat-msg ${m.role}`;
    el.innerHTML = m.html;
    log.appendChild(el);
  }
  log.scrollTop = log.scrollHeight;
  document.getElementById("emptyState").style.display = "none";
  log.style.display = "flex";
  renderConversationList();
}

// Mobile drawer: the sidebar slides in over the chat on narrow screens.
const appShell = document.querySelector(".app-shell");
const closeSidebar = () => appShell.classList.remove("sidebar-open");
document.getElementById("menuBtn").addEventListener("click", () => appShell.classList.toggle("sidebar-open"));
document.getElementById("sidebarBackdrop").addEventListener("click", closeSidebar);

document.getElementById("newChatBtn").addEventListener("click", () => {
  startNewConversation();
  closeSidebar();
});

document.getElementById("conversationList").addEventListener("click", (e) => {
  const deleteBtn = e.target.closest("[data-delete-conv]");
  if (deleteBtn) {
    deleteConversation(deleteBtn.dataset.deleteConv);
    return;
  }
  const item = e.target.closest(".conversation-item");
  if (item) {
    openConversation(item.dataset.conv);
    closeSidebar();
  }
});

// Real, currently-configured role -> spending-limit policy (see
// GET /procurement/approval-limits) -- fetched once and cached, never
// hardcoded here, so the chat only ever offers an approval action that
// can actually succeed instead of one guaranteed to fail with
// "exceeds that limit".
let _approvalLimitsCache = null;
async function getApprovalLimits() {
  if (!_approvalLimitsCache) {
    const data = await apiFetch("/procurement/approval-limits");
    _approvalLimitsCache = data.limits; // { manager: 500000, director: 5000000, cfo: null }
  }
  return _approvalLimitsCache;
}

// The lowest-authority role whose limit actually covers this budget
// (null limit = no cap). Returns null if nothing configured covers it
// (shouldn't happen with a cfo:null entry, but never assume one exists).
function pickQualifyingRole(limits, estimatedBudget) {
  const qualifying = Object.entries(limits)
    .filter(([, limit]) => limit === null || estimatedBudget <= limit)
    .sort(([, a], [, b]) => (a === null ? Infinity : a) - (b === null ? Infinity : b));
  return qualifying.length ? qualifying[0][0] : null;
}

// System display names -- matches app/procurement/identities.py's SYSTEM_IDS.
const SYSTEM_LABELS = { vendra: "Vendra (vendor/compliance)", procura: "Procura (quotes/negotiation)", finora: "Finora (budget/finance)" };

// A natural-language note on which specialists ran normally and which
// didn't, instead of a row of status-badge pills -- e.g. "All 6
// specialist checks completed normally" or "5 of 6 completed normally;
// payment_terms_review was skipped (no payment terms on file)."
function describeSpecialists(specialists) {
  const names = Object.keys(specialists);
  if (!names.length) return "";
  const notOk = names.filter((n) => specialists[n].status !== "ok");
  if (!notOk.length) {
    return `All ${names.length} specialist checks completed normally (${names.join(", ")}).`;
  }
  const notes = notOk
    .map((n) => {
      const s = specialists[n];
      const reason = s.status === "error" ? s.error : s.data && s.data.reason;
      return `${n} was ${s.status}${reason ? ` (${reason})` : ""}`;
    })
    .join("; ");
  return `${names.length - notOk.length} of ${names.length} specialist checks completed normally; ${notes}.`;
}

// Plain written prose, like an ordinary answer -- not a row of boxed
// cards. Each agent gets one short paragraph: its own real summary plus
// a natural note on its specialists, with just the agent's name bolded.
function renderAgentWork(messages) {
  if (!messages || !messages.length) return "";
  const paragraphs = messages
    .map((m) => {
      const label = SYSTEM_LABELS[m.sender] || m.sender;
      const payload = m.payload || {};
      const sentences = [];
      if (payload.ai_summary) {
        sentences.push(`<strong>${escapeHtml(label)}</strong> ${renderInlineMarkdown(escapeHtml(payload.ai_summary))}`);
      } else {
        sentences.push(`<strong>${escapeHtml(label)}</strong> reported "${escapeHtml(m.status)}".`);
      }
      if (m.error) sentences.push(escapeHtml(m.error));
      const specNote = describeSpecialists(payload.specialists || {});
      if (specNote) sentences.push(escapeHtml(specNote));
      const basis = payload.eligibility_basis;
      if (basis && basis.rejected && basis.rejected.length) {
        sentences.push(escapeHtml("Excluded after checking its shared memory: " + basis.rejected.map((r) => `${r.supplier} (${r.reason})`).join("; ") + "."));
      }
      if (payload.reconciliation && payload.reconciliation.note) sentences.push(escapeHtml(payload.reconciliation.note + "."));
      const resolved = (payload.reconciliation && payload.reconciliation.resolved) || [];
      for (const r of resolved.slice(0, 3)) {
        sentences.push(escapeHtml(`Sources disagreed on ${r.supplier}'s price; ${r.reason}.`));
      }
      const history = payload.specialists && payload.specialists.quotation_history;
      if (history && history.status === "ok") {
        const h = history.data;
        const first = (h.changes || [])[0];
        sentences.push(escapeHtml(
          `Quotation history: ${h.summary_used ? "read the stored summary first" : "no stored summary yet"}, then opened ${h.records_opened} supporting records` +
          (first ? `; ${first.supplier}'s ${first.field === "price" ? "price" : "delivery time"} changed from ${first.from} to ${first.to}.` : ".")
        ));
      }
      return `<p>${sentences.join(" ")}</p>`;
    })
    .join("");
  return `<div class="agent-work-block">${paragraphs}</div>`;
}

function statusSlug(s) {
  return String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

async function renderTaskReply(pr) {
  const cr = pr.coordinator_result;
  const parts = [];

  parts.push(`<div class="task-card-header">
    <span class="task-id">${escapeHtml(pr.pr_id)}</span>
    <span class="status-pill status-${statusSlug(pr.workflow_state)}">${escapeHtml(pr.workflow_state)}</span>
  </div>`);

  if (pr.mode === "demo") parts.push(`<div class="sim-banner">SIMULATED -- not live ERPNext</div>`);

  if (pr.quantity) {
    parts.push(`<div class="task-details-grid">
      <div class="detail-item"><span class="detail-label">Quantity</span><span class="detail-value">${escapeHtml(pr.quantity)}</span></div>
      <div class="detail-item"><span class="detail-label">Budget</span><span class="detail-value">${escapeHtml(pr.estimated_budget)}</span></div>
      <div class="detail-item"><span class="detail-label">Delivery</span><span class="detail-value">${escapeHtml(pr.delivery_location)}</span></div>
      <div class="detail-item"><span class="detail-label">Needed by</span><span class="detail-value">${escapeHtml(pr.required_date)}</span></div>
    </div>`);
  }

  if (pr.workflow_state === "Waiting For Clarification") {
    const conflicts = (pr.coordinator_result && pr.coordinator_result.result && pr.coordinator_result.result.conflicts) || [];
    for (const c of conflicts) {
      const buttons = (c.claims || [])
        .map((cl) => {
          const label = `Use ${cl.currency || ""} ${cl.value} \u2014 ${cl.source_type}${cl.document_date ? ", " + cl.document_date : ""}`;
          return `<button type="button" class="chat-resolve" data-subject="${escapeHtml(c.subject_key)}" data-winner="${escapeHtml(cl.memory_id)}" data-pr="${escapeHtml(pr.pr_id)}" title="${escapeHtml(cl.source_reference || "")}">${escapeHtml(label)}</button>`;
        })
        .join("");
      parts.push(`<div class="conflict-box">
        <div><strong>Price disputed:</strong> ${escapeHtml(c.supplier || "")} for ${escapeHtml(c.item_code || "")}</div>
        <div class="conflict-reason">${escapeHtml(c.reason || "")}</div>
        <div class="resolve-btns">${buttons}</div>
      </div>`);
    }
    parts.push(`<div class="hint">Choose the correct price and I'll continue the run.</div>`);
  }

  if (pr.blockers && pr.blockers.length && pr.workflow_state !== "Waiting For Clarification") {
    parts.push(`<div class="blocker-box"><strong>I need:</strong> ${escapeHtml(pr.blockers.join("; "))}</div>`);
  }

  if (cr && cr.result) {
    if (cr.result.recommended_supplier) {
      parts.push(`<div class="recommendation-box">
        <strong>Recommended supplier:</strong> ${escapeHtml(cr.result.recommended_supplier)}
        &middot; <strong>Total:</strong> ${escapeHtml(cr.result.recommended_total_cost)}
      </div>`);
      // The real total can come out well above what was originally
      // requested (quotations/compliance decide the price, not the
      // original guess) -- surfaced explicitly rather than silently
      // approved against a stale number.
      if (cr.result.recommended_total_cost != null && pr.estimated_budget && cr.result.recommended_total_cost > pr.estimated_budget) {
        parts.push(`<div class="alert-box warn">This total (${escapeHtml(cr.result.recommended_total_cost)}) exceeds the budget you originally stated (${escapeHtml(pr.estimated_budget)}).</div>`);
      }
    }
    if (cr.approval_summary) parts.push(`<p>${escapeHtml(cr.approval_summary)}</p>`);
    if (cr.error) parts.push(`<div class="alert-box error">${escapeHtml(cr.error)}</div>`);
  }

  if (cr) parts.push(renderAgentWork(cr.messages));

  if (pr.workflow_state === "Waiting For Approval") {
    // The real amount an approval must be checked against -- the agent
    // pipeline's own recommended_total_cost when one exists, NEVER
    // pr.estimated_budget (the requester's own, possibly very rough,
    // original guess -- a real recommendation can come out far higher,
    // and picking a role off the stale guess could let someone approve
    // an amount their role was never actually authorized for). Mirrors
    // app.procurement.workflow.approval_check_amount exactly.
    const approvalAmount = (cr && cr.result && cr.result.recommended_total_cost != null)
      ? cr.result.recommended_total_cost
      : pr.estimated_budget;
    let role = null;
    try {
      const limits = await getApprovalLimits();
      role = pickQualifyingRole(limits, approvalAmount);
    } catch (e) {
      /* fall through -- buttons just won't render if we can't fetch the real policy */
    }
    if (role) {
      parts.push(`<div class="hint">Approval authority required for ₹${approvalAmount.toLocaleString()}: ${escapeHtml(role)}.</div>
      <div class="chat-approve-btns">
        <button type="button" class="chat-approve" data-pr="${escapeHtml(pr.pr_id)}" data-decision="Approved" data-role="${escapeHtml(role)}">Approve</button>
        <button type="button" class="chat-approve" data-pr="${escapeHtml(pr.pr_id)}" data-decision="Rejected" data-role="${escapeHtml(role)}">Reject</button>
      </div>`);
    }
  } else if (pr.workflow_state === "Waiting For Information") {
    parts.push(`<div class="hint">Just reply below with it -- e.g. an item code like DEMO-LAPTOP-14.</div>`);
  } else if (pr.purchase_order) {
    // Live-mode auto-approval (LIVE_MODE_AUTO_APPROVE) skips straight
    // from a recommendation to a submitted PO -- no Approve click ever
    // happens, so this is the only place that result gets shown.
    const po = pr.purchase_order;
    if (po.status === "submitted") {
      const name = po.result && po.result.name;
      parts.push(`<div class="hint">${pr.mode === "demo" ? "*** SIMULATED -- not live ERPNext *** " : ""}Purchase order submitted automatically (no human review)${name ? `: ${escapeHtml(name)}` : ""}.</div>`);
    } else if (po.status === "failed") {
      parts.push(`<div class="hint">Purchase order was NOT created: ${escapeHtml(po.error || "unknown error")}</div>`);
    }
  }

  return `<div class="task-card">${parts.join("")}</div>`;
}

function looksLikeANewInstruction(text) {
  return /\b(purchase|buy|procure|order|need\s+\d+|want\s+\d+)\b/i.test(text);
}

// Lane colours shared with the landing page's workflow graph.
const STAGE_LANES = {
  router: "router", memory: "mem", orchestrator: "orch", vendra: "agent", procura: "agent",
  finora: "agent", approval: "orch", erpnext: "erp", waiting: "user",
};

function traceItemsHtml(events, running) {
  return events
    .map((ev, i) => {
      const live = running && i === events.length - 1;
      return `<li class="${live ? "running" : "done"}"><span class="trace-mark"></span><span class="lane lane-${STAGE_LANES[ev.stage] || "user"}">${escapeHtml(ev.stage)}</span><span class="trace-text">${escapeHtml(ev.text)}</span></li>`;
    })
    .join("");
}

async function sendMessage(text) {
  if (!text) return;
  document.getElementById("emptyState").style.display = "none";
  const log = document.getElementById("chatLog");
  log.style.display = "flex";

  ensureActiveConversation(text);
  appendChatMessage("user", escapeHtml(text));
  pushHistory("user", text);
  const bubble = appendChatMessage("assistant", "");
  let stopThinking = startThinkingAnimation(bubble);
  const sendBtn = document.getElementById("sendBtn");
  sendBtn.disabled = true;

  const trace = [];
  let streamed = "";
  let final = null;
  let failure = null;
  const stickToBottom = () => {
    log.scrollTop = log.scrollHeight;
  };
  // The rotating placeholder only covers the gap before the first real event.
  const firstEvent = () => {
    if (stopThinking) {
      stopThinking();
      stopThinking = null;
    }
  };
  const paintLive = () => {
    if (streamed) {
      const caret = '<span class="stream-caret"></span>';
      const html = renderMarkdown(streamed);
      bubble.innerHTML = /<\/(p|li)>$/.test(html) ? html.replace(/<\/(p|li)>$/, `${caret}</$1>`) : html + caret;
    } else {
      bubble.innerHTML = `<ol class="live-trace">${traceItemsHtml(trace, true)}</ol>`;
    }
    stickToBottom();
  };

  try {
    await streamPost(
      "/procurement/chat/stream",
      {
        message: text,
        requested_by: DEFAULT_REQUESTED_BY,
        mode: "live", // the only mode this UI offers now, per explicit request -- see the fixed warning badge in the topbar
        pr_id: chatCurrentPr ? chatCurrentPr.pr_id : null,
        conversation_id: activeConversationId,
        history: chatHistory,
      },
      {
        status: (d) => {
          firstEvent();
          trace.push(d);
          paintLive();
        },
        token: (d) => {
          firstEvent();
          streamed += d.text;
          paintLive();
        },
        final: (d) => {
          final = d;
        },
        error: (d) => {
          failure = d.detail;
        },
      }
    );
    if (!final && !failure) failure = "The connection closed before the reply finished.";
  } catch (err) {
    failure = err.message;
  }
  if (stopThinking) stopThinking();

  if (failure) {
    bubble.innerHTML = `<p>Error: ${escapeHtml(failure)}</p>` + metaRowHtml();
  } else if (final.pr) {
    chatCurrentPr = final.pr;
    rememberConversationPr(final.pr.pr_id);
    const traceHtml = trace.length
      ? `<details class="run-trace"><summary>Live run trace &middot; ${trace.length} steps</summary><ol class="live-trace">${traceItemsHtml(trace, false)}</ol></details>`
      : "";
    bubble.innerHTML = traceHtml + (await renderTaskReply(final.pr)) + metaRowHtml();
    pushHistory("assistant", `[task ${final.pr.pr_id}: ${final.pr.workflow_state}]`);
  } else {
    const reply = final.reply || "(no reply)";
    bubble.innerHTML = renderMarkdown(reply) + metaRowHtml();
    pushHistory("assistant", reply);
  }
  sendBtn.disabled = false;
  stickToBottom();
  // persist the final bubble content
  if (activeConversationId) {
    const conv = conversations.find((c) => c.id === activeConversationId);
    if (conv && conv.messages.length) {
      conv.messages[conv.messages.length - 1].html = bubble.innerHTML;
      saveConversations();
    }
  }
}

document.getElementById("chatForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("chatInput");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  input.style.height = "auto";
  await sendMessage(text);
});

// Enter sends, Shift+Enter inserts a newline -- the textarea itself never
// submits the form on plain Enter.
const chatInputEl = document.getElementById("chatInput");
chatInputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    document.getElementById("chatForm").requestSubmit();
  }
});
chatInputEl.addEventListener("input", () => {
  chatInputEl.style.height = "auto";
  chatInputEl.style.height = `${Math.min(chatInputEl.scrollHeight, 160)}px`;
});

// Copy-to-clipboard on an assistant bubble -- copies the message text only,
// never the timestamp/copy-button row itself.
document.getElementById("chatLog").addEventListener("click", async (e) => {
  const copyBtn = e.target.closest("[data-copy-msg]");
  if (!copyBtn) return;
  const bubble = copyBtn.closest(".chat-msg");
  const clone = bubble.cloneNode(true);
  const meta = clone.querySelector(".chat-msg-meta");
  if (meta) meta.remove();
  try {
    await navigator.clipboard.writeText(clone.innerText.trim());
    const original = copyBtn.innerHTML;
    copyBtn.innerHTML = "&#10003; Copied";
    setTimeout(() => { copyBtn.innerHTML = original; }, 1200);
  } catch (err) {
    /* clipboard API unavailable -- silently no-op, nothing else to fall back to */
  }
});

document.querySelectorAll(".suggestion-card").forEach((card) => {
  card.addEventListener("click", () => sendMessage(card.dataset.prompt));
});

document.getElementById("chatLog").addEventListener("click", async (e) => {
  const btn = e.target.closest(".chat-approve");
  if (!btn) return;
  const prId = btn.dataset.pr;
  const msg = appendChatMessage("assistant", "Submitting your decision...");
  try {
    const current = await apiFetch(`/procurement/requests/${encodeURIComponent(prId)}`);
    const result = await apiFetch(`/procurement/requests/${encodeURIComponent(prId)}/approve`, {
      method: "POST",
      body: JSON.stringify({
        approver_id: "chat-operator",
        approver_role: btn.dataset.role,
        decision: btn.dataset.decision,
        reviewed_updated_at: current.updated_at,
      }),
    });
    const lines = [`${result.decision} by ${result.approver_id} (${result.approver_role}).`];
    if (result.decision === "Approved") {
      // The approval itself is not the same step as actually submitting
      // the purchase order -- re-fetch the PR to show what really
      // happened next, instead of implying it's done just because the
      // approval call returned.
      const updated = await apiFetch(`/procurement/requests/${encodeURIComponent(prId)}`);
      const po = updated.purchase_order;
      if (po && po.status === "submitted") {
        const name = po.result && po.result.name;
        lines.push(
          (updated.mode === "demo" ? "*** SIMULATED -- not live ERPNext *** " : "") +
            `Purchase order submitted${name ? ` (${name})` : ""}.`
        );
      } else if (po && po.status === "failed") {
        lines.push(`Purchase order was NOT created: ${po.error}`);
      } else {
        lines.push("No purchase order was submitted (no recommendation to act on).");
      }
    }
    msg.innerHTML = escapeHtml(lines.join("\n"));
  } catch (err) {
    msg.innerHTML = `Error: ${escapeHtml(err.message)}`;
  }
  if (activeConversationId) {
    const conv = conversations.find((c) => c.id === activeConversationId);
    if (conv && conv.messages.length) {
      conv.messages[conv.messages.length - 1].html = msg.innerHTML;
      saveConversations();
    }
  }
});

document.getElementById("chatLog").addEventListener("click", async (e) => {
  const btn = e.target.closest(".chat-resolve");
  if (!btn) return;
  btn.closest(".conflict-box").querySelectorAll(".chat-resolve").forEach((b) => (b.disabled = true));
  const bubble = appendChatMessage("assistant", "");
  const stop = startThinkingAnimation(bubble);
  try {
    await apiFetch("/procurement/conflicts/resolve", {
      method: "POST",
      body: JSON.stringify({ subject_key: btn.dataset.subject, winner_memory_id: btn.dataset.winner, resolved_by: "chat-operator" }),
    });
    const pr = await apiFetch(`/procurement/tasks/${encodeURIComponent(btn.dataset.pr)}/resume`, { method: "POST" });
    chatCurrentPr = pr;
    rememberConversationPr(pr.pr_id);
    bubble.innerHTML = "<p>Decision recorded. Continuing the run.</p>" + (await renderTaskReply(pr)) + metaRowHtml();
  } catch (err) {
    bubble.innerHTML = `<p>Error: ${escapeHtml(err.message)}</p>` + metaRowHtml();
  }
  stop();
  document.getElementById("chatLog").scrollTop = document.getElementById("chatLog").scrollHeight;
  if (activeConversationId) {
    const conv = conversations.find((c) => c.id === activeConversationId);
    if (conv && conv.messages.length) {
      conv.messages[conv.messages.length - 1].html = bubble.innerHTML;
      saveConversations();
    }
  }
});

// -- theme toggle ----------------------------------------------------------

const THEME_KEY = "procurement_theme";
function applyTheme(theme) {
  if (theme === "light") {
    document.documentElement.setAttribute("data-theme", "light");
    document.getElementById("themeToggleBtn").textContent = "☽"; // moon
  } else {
    document.documentElement.removeAttribute("data-theme");
    document.getElementById("themeToggleBtn").textContent = "☀"; // sun
  }
}
applyTheme(localStorage.getItem(THEME_KEY) || "dark");
document.getElementById("themeToggleBtn").addEventListener("click", () => {
  const current = localStorage.getItem(THEME_KEY) || "dark";
  const next = current === "dark" ? "light" : "dark";
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
});

// -- backend health dot + Sync -----------------------------------------------

async function checkHealth() {
  const dot = document.getElementById("healthDot");
  try {
    await apiFetch("/procurement/health");
    dot.className = "status-dot ok";
    dot.title = "Backend: connected";
  } catch (e) {
    dot.className = "status-dot bad";
    dot.title = "Backend: unreachable";
  }
}
document.getElementById("syncBtn").addEventListener("click", () => {
  _approvalLimitsCache = null; // force a fresh read of the real policy next time it's needed
  checkHealth();
});
checkHealth();

// -- side panels: Connections / Item codes -----------------------------------

function togglePanel(id) {
  const panel = document.getElementById(id);
  panel.hidden = !panel.hidden;
  if (!panel.hidden && id === "connectionsPanel") loadConnections();
}
document.getElementById("navConnections").addEventListener("click", () => {
  closeSidebar();
  togglePanel("connectionsPanel");
});
document.getElementById("navFiles").addEventListener("click", () => {
  closeSidebar();
  togglePanel("filesPanel");
});
document.querySelectorAll(".close-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.getElementById(btn.dataset.close).hidden = true;
  });
});

async function loadConnections() {
  const body = document.getElementById("connectionsBody");
  body.innerHTML = "<p class=\"hint\">Loading...</p>";
  try {
    const h = await apiFetch("/procurement/health");
    const rows = [
      ["ERPNext backend", h.erpnext_backend, true],
      ["ERPNext writes allowed", h.erpnext_writes_allowed ? "yes" : "no", h.erpnext_writes_allowed],
      ["Supabase memory", h.supabase_configured ? "configured" : "not configured", h.supabase_configured],
      ["LLM provider", h.llm_provider_configured ? "configured" : "not configured", h.llm_provider_configured],
      ["Langfuse tracing", h.langfuse_configured ? "configured" : "not configured", h.langfuse_configured],
      ["AI summaries", h.ai_summaries_enabled ? "enabled" : "disabled", h.ai_summaries_enabled],
    ];
    body.innerHTML = rows
      .map(
        ([label, value, ok]) =>
          `<div class="conn-row"><span>${escapeHtml(label)}</span><span>${escapeHtml(String(value))} <span class="status-dot ${ok ? "ok" : "bad"}"></span></span></div>`
      )
      .join("");
  } catch (e) {
    body.innerHTML = `<p class="hint">Error: ${escapeHtml(e.message)}</p>`;
  }
}

// Real item codes actually seeded into the demo pool / live ERPNext this
// session (see app/procurement/demo_fixtures.py and
// scripts/seed_live_erpnext_vendors.py) -- a static reference list, not a
// live query, but every code here is real.
const KNOWN_ITEM_CODES = [
  ["LAPTOP-14IN", "14-inch Business Laptop", "IT Hardware"],
  ["OFFICE-CHAIR-STD", "Ergonomic Office Chair", "Office Supplies"],
  ["FREIGHT-STD", "Standard Freight Delivery Service", "Logistics"],
  ["PACK-BOX-MED-50", "Medium Shipping Carton (Pack of 50)", "Packaging"],
  ["FACILITY-MAINT-MO", "Facility Maintenance Service (Monthly)", "Facilities"],
  ["SOFTWARE-LIC-ANNUAL", "Annual Software License (Per Seat)", "Software Services"],
];
document.getElementById("filesBody").innerHTML = KNOWN_ITEM_CODES.map(
  ([code, name, category]) => `<div class="item-row"><code>${code}</code> — ${escapeHtml(name)} <span class="hint">(${escapeHtml(category)})</span></div>`
).join("");

// -- boot -----------------------------------------------------------------

loadConversations();
renderConversationList();

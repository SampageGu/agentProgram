const $ = (id) => document.getElementById(id);
const terminalEvents = new Set(["run_completed", "run_failed", "run_cancelled", "budget_exhausted"]);
const eventTypes = ["run_started","repository_mapped","model_started","model_completed","text_delta","tool_started","tool_completed","plan_updated","patch_applied","verification_passed","verification_failed","completion_verified","context_compacted","checkpoint_saved","run_resumed","run_interrupted","run_cancelled","run_completed","run_failed","budget_exhausted","subagent_started","subagent_completed","subagent_delegated","subagent_result","subagent_failed"];
const state = { conversationId: null, runId: null, conversation: null, source: null, events: new Map(), active: false };

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

async function refreshConversations() {
  const {conversations} = await api("/api/conversations");
  const root = $("conversation-list");
  root.replaceChildren();
  if (!conversations.length) {
    root.append(node("p", "muted", "还没有对话"));
    return;
  }
  for (const conversation of conversations) {
    const button = node("button", `conversation-item ${conversation.conversation_id === state.conversationId ? "active" : ""}`);
    button.type = "button";
    button.append(node("strong", "", conversation.title));
    const meta = node("span");
    meta.append(node("i", `status-dot ${conversation.status}`), document.createTextNode(`${conversation.message_count} 轮 · ${statusText(conversation.status)}`));
    button.append(meta);
    button.onclick = () => selectConversation(conversation.conversation_id);
    root.append(button);
  }
}

async function selectConversation(conversationId) {
  closeStream();
  state.conversationId = conversationId;
  state.events.clear();
  state.conversation = await api(`/api/conversations/${conversationId}`);
  const turns = state.conversation.turns || [];
  await Promise.all(turns.map(async (turn) => {
    try {
      const result = await api(`/api/runs/${turn.run_id}/events`);
      state.events.set(turn.run_id, new Map(result.events.map(event => [Number(event.sequence), event])));
    } catch {
      state.events.set(turn.run_id, new Map());
    }
  }));
  const last = turns.at(-1);
  state.runId = last?.run_id || null;
  state.active = Boolean(last?.run?.active);
  $("chat-title").textContent = state.conversation.title;
  renderConversation();
  if (last?.run) renderInspector(last.run, eventsFor(last.run_id));
  setActive(state.active);
  if (state.active) connectStream();
  await refreshConversations();
  $("sidebar").classList.remove("open");
}

function renderConversation() {
  const root = $("messages");
  root.replaceChildren();
  const turns = state.conversation?.turns || [];
  if (!turns.length) {
    root.append(welcomeView());
    return;
  }
  for (const turn of turns) root.append(renderTurn(turn, eventsFor(turn.run_id)));
  requestAnimationFrame(() => { root.scrollTop = root.scrollHeight; });
}

function welcomeView() {
  const template = document.createElement("template");
  template.innerHTML = '<div class="welcome"><span class="welcome-mark">M</span><h1>今天想一起改点什么？</h1><p>描述一个真实的编程任务。MiniCode 会先探索代码，再制定计划、修改文件并运行测试。</p><div class="prompts"><button type="button" data-prompt="先阅读项目结构，找出最值得修复的一个问题，并在验证后完成修改。"><strong>探索并修复</strong><span>阅读仓库，选择一个可验证的问题</span></button><button type="button" data-prompt="为当前项目补充一个有价值的功能，并添加对应测试。"><strong>实现新功能</strong><span>计划、编码、测试和 Diff 一次完成</span></button></div></div>';
  const view = template.content.firstElementChild;
  bindPrompts(view);
  return view;
}

function renderTurn(turn, events) {
  const wrapper = node("article", "turn");
  wrapper.dataset.runId = turn.run_id;
  wrapper.append(node("div", "user-message", turn.user_message));
  const assistant = node("div", "assistant-message");
  assistant.append(node("div", "avatar", "M"));
  const body = node("div", "assistant-body");
  const activities = renderActivities(events, turn.run?.delegations || []);
  if (activities.childElementCount) body.append(activities);
  const text = events.filter(event => event.type === "text_delta").map(event => event.data?.content || "").join("");
  const active = Boolean(turn.run?.active);
  const answer = node("div", `assistant-text ${active ? "cursor" : ""}`);
  if (text) answer.textContent = text;
  else {
    answer.classList.add("thinking");
    answer.textContent = active ? "正在理解任务并检查工作区…" : fallbackAnswer(turn.run?.status);
  }
  body.append(answer);
  const status = node("div", `run-status ${statusClass(turn.run?.status)}`, `${statusText(turn.run?.status)} · ${turn.run_id}`);
  body.append(status);
  assistant.append(body);
  wrapper.append(assistant);
  return wrapper;
}

function renderActivities(events, delegations) {
  const stack = node("div", "activity-stack");
  for (const event of events.filter(item => item.type === "tool_completed")) {
    const tool = event.data?.tool || "tool";
    const ok = Boolean(event.data?.ok);
    const details = node("details", "activity-card");
    const summary = node("summary");
    summary.append(node("span", "activity-icon", "⌁"), node("span", "", tool), node("span", `activity-result ${ok ? "ok" : "bad"}`, ok ? "完成" : "失败"));
    details.append(summary, node("pre", "", compact(event.data?.result || event.data?.message)));
    stack.append(details);
  }
  const childEvents = events.filter(item => ["subagent_result", "subagent_failed"].includes(item.type));
  for (const event of childEvents) {
    const ok = event.type === "subagent_result";
    const details = node("details", "activity-card subagent");
    const summary = node("summary");
    const child = event.data?.child_run_id || "Explore";
    summary.append(node("span", "activity-icon", "◇"), node("span", "", `Explore Subagent · ${child}`), node("span", `activity-result ${ok ? "ok" : "bad"}`, ok ? "已返回" : "失败"));
    const record = delegations.find(item => item.child_run_id === child) || event.data;
    details.append(summary, node("pre", "", compact(record)));
    stack.append(details);
  }
  return stack;
}

function eventsFor(runId) {
  return [...(state.events.get(runId)?.values() || [])].sort((a, b) => a.sequence - b.sequence);
}

function addEvent(runId, event) {
  if (!state.events.has(runId)) state.events.set(runId, new Map());
  state.events.get(runId).set(Number(event.sequence), event);
}

async function refreshCurrentRun() {
  if (!state.runId || !state.conversation) return;
  try {
    const run = await api(`/api/runs/${state.runId}`);
    const turn = state.conversation.turns.find(item => item.run_id === state.runId);
    if (turn) turn.run = run;
    state.active = Boolean(run.active);
    renderConversation();
    renderInspector(run, eventsFor(state.runId));
    setActive(state.active);
  } catch { /* the first checkpoint may not exist yet */ }
}

function connectStream() {
  closeStream();
  if (!state.runId) return;
  const runId = state.runId;
  const events = eventsFor(runId);
  const after = events.length ? events.at(-1).sequence : 0;
  state.source = new EventSource(`/api/runs/${runId}/stream?after=${after}`);
  $("connection").textContent = "Agent 正在工作 · SSE 已连接";
  for (const type of eventTypes) {
    state.source.addEventListener(type, async message => {
      addEvent(runId, JSON.parse(message.data));
      await refreshCurrentRun();
      if (terminalEvents.has(type)) {
        closeStream();
        setActive(false);
        await refreshConversations();
      }
    });
  }
  state.source.onerror = () => {
    if (state.active) $("connection").textContent = "流式连接正在重试…";
  };
}

function closeStream() {
  if (state.source) state.source.close();
  state.source = null;
}

function renderInspector(run, events) {
  $("run-label").textContent = `${run.status || "starting"} · ${run.run_id}`;
  $("step-label").textContent = run.step === undefined ? "—" : `${run.step} / ${run.max_steps}`;
  $("detail-badge").textContent = events.length;
  const plan = $("plan");
  plan.replaceChildren();
  if (!run.plan?.length) plan.append(node("li", "muted", "等待 Agent 创建计划"));
  else for (const item of run.plan) plan.append(node("li", item.status || "pending", item.step));
  const metrics = run.metrics || {};
  const rows = [
    ["修改文件", run.changed_files?.join(", ") || "尚无", ""],
    ["测试通过", metrics.verification_passed || 0, metrics.verification_passed ? "ok" : ""],
    ["测试失败", metrics.verification_failed || 0, metrics.verification_failed ? "bad" : ""],
    ["模型步骤", metrics.model_steps ?? "—", ""],
    ["Token", metrics.total_tokens ?? "—", ""],
    ["Subagent", run.delegations?.length || 0, run.delegations?.length ? "ok" : ""],
    ["Checkpoint", run.checkpoint ?? "—", ""]
  ];
  const evidence = $("evidence"); evidence.replaceChildren();
  for (const [label, value, className] of rows) {
    const row = node("div", "evidence-row");
    row.append(node("span", "", label), node("strong", className, String(value)));
    evidence.append(row);
  }
  $("diff").textContent = run.diff || "等待 Agent 查看 Diff";
  const timeline = $("timeline"); timeline.replaceChildren();
  for (const event of events.filter(item => item.type !== "text_delta")) {
    const row = node("div", "event");
    row.append(node("span", "", String(event.sequence).padStart(3, "0")), node("b", "", event.type), node("em", "", event.data?.message || ""));
    timeline.append(row);
  }
  $("event-count").textContent = String(events.length);
}

function setActive(active) {
  state.active = active;
  $("send-message").classList.toggle("hidden", active);
  $("stop-run").classList.toggle("hidden", !active);
  $("message-input").disabled = active;
  if (!active) $("connection").textContent = state.conversationId ? "可以继续追问" : "准备就绪";
}

async function sendMessage(message) {
  const value = message.trim();
  if (!value || state.active) return;
  $("form-error").textContent = "";
  setActive(true);
  try {
    const path = state.conversationId ? `/api/conversations/${state.conversationId}/messages` : "/api/conversations";
    const result = await api(path, {method: "POST", body: JSON.stringify({message: value})});
    state.conversationId = result.conversation_id;
    state.runId = result.run_id;
    $("message-input").value = "";
    resizeComposer();
    await selectConversation(result.conversation_id);
  } catch (error) {
    setActive(false);
    $("form-error").textContent = error.message;
  }
}

function newChat() {
  closeStream();
  state.conversationId = null; state.runId = null; state.conversation = null; state.events.clear();
  $("chat-title").textContent = "新对话";
  $("messages").replaceChildren(welcomeView());
  $("run-label").textContent = "尚未运行";
  $("detail-badge").textContent = "0";
  setActive(false);
  refreshConversations();
  $("message-input").focus();
}

function bindPrompts(root = document) {
  root.querySelectorAll("[data-prompt]").forEach(button => button.onclick = () => {
    $("message-input").value = button.dataset.prompt;
    resizeComposer();
    $("message-input").focus();
  });
}

function resizeComposer() {
  const input = $("message-input");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

function statusText(status) {
  return ({completed:"已完成",running:"运行中",starting:"启动中",failed:"失败",cancelled:"已取消",budget_exhausted:"预算耗尽",unknown:"等待状态"})[status] || "等待中";
}
function statusClass(status) { return status === "completed" ? "ok" : ["failed","cancelled","budget_exhausted"].includes(status) ? "bad" : ""; }
function fallbackAnswer(status) { return status === "completed" ? "任务已完成，请在右侧查看验证证据。" : statusText(status); }
function compact(value) { const text = typeof value === "string" ? value : JSON.stringify(value, null, 2); return text.length > 5000 ? `${text.slice(0, 5000)}\n…` : text; }

$("composer").addEventListener("submit", event => { event.preventDefault(); sendMessage($("message-input").value); });
$("message-input").addEventListener("input", resizeComposer);
$("message-input").addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendMessage(event.currentTarget.value); } });
$("stop-run").onclick = async () => { if (!state.runId) return; try { await api(`/api/runs/${state.runId}/cancel`, {method:"POST", body:"{}"}); $("connection").textContent = "正在安全停止…"; } catch (error) { $("form-error").textContent = error.message; } };
$("new-chat").onclick = newChat;
$("inspect-toggle").onclick = () => $("app-shell").classList.toggle("inspecting");
$("close-inspector").onclick = () => $("app-shell").classList.remove("inspecting");
$("menu-toggle").onclick = () => $("sidebar").classList.toggle("open");
$("copy-diff").onclick = async () => { try { await navigator.clipboard.writeText($("diff").textContent); $("copy-diff").textContent = "已复制"; setTimeout(() => $("copy-diff").textContent = "复制", 1200); } catch { $("copy-diff").textContent = "复制失败"; } };

bindPrompts();
refreshConversations().catch(error => { $("conversation-list").textContent = error.message; });

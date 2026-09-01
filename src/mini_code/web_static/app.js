const $ = (id) => document.getElementById(id);
const state = { runId: null, source: null, events: new Map() };
const eventTypes = ["run_started","repository_mapped","model_started","model_completed","text_delta","tool_started","tool_completed","plan_updated","patch_applied","verification_passed","verification_failed","completion_verified","context_compacted","checkpoint_saved","run_resumed","run_interrupted","run_cancelled","run_completed","run_failed","budget_exhausted"];

async function api(path, options = {}) {
  const response = await fetch(path, { headers: {"Content-Type":"application/json"}, ...options });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

async function refreshRuns() {
  const {runs} = await api("/api/runs");
  const root = $("run-list"); root.replaceChildren();
  if (!runs.length) { root.innerHTML = '<p class="muted">还没有 Coding Run</p>'; return; }
  runs.forEach(run => {
    const button = document.createElement("button"); button.className = `run-item ${run.run_id === state.runId ? "active" : ""}`;
    const title = document.createElement("strong"); title.textContent = run.task;
    const meta = document.createElement("span");
    const status = document.createElement("b"); status.textContent = run.status;
    const id = document.createElement("code"); id.textContent = run.run_id;
    meta.append(status, id); button.append(title, meta); button.onclick = () => selectRun(run.run_id); root.append(button);
  });
}

async function selectRun(runId) {
  state.runId = runId; state.events.clear(); if (state.source) state.source.close();
  $("composer").classList.add("hidden"); $("run-view").classList.remove("hidden"); $("page-title").textContent = "运行详情";
  await loadDetail();
  const {events} = await api(`/api/runs/${runId}/events`); events.forEach(addEvent); renderTimeline();
  connectStream(); await refreshRuns();
}

async function loadDetail() {
  const run = await api(`/api/runs/${state.runId}`);
  $("run-id").textContent = run.run_id; $("task-title").textContent = run.task;
  $("status").textContent = run.status; $("status").className = `status ${run.status}`;
  $("step-label").textContent = `STEP ${run.step} / ${run.max_steps}`;
  $("cancel-run").disabled = !run.active;
  $("diff").textContent = run.diff || "等待 Agent 查看 Diff";
  renderPlan(run.plan || []); renderMetrics(run.metrics); renderEvidence(run);
}

function renderPlan(plan) {
  const root = $("plan"); root.replaceChildren();
  if (!plan.length) { root.innerHTML = '<li class="muted">等待 Agent 创建计划</li>'; return; }
  plan.forEach(item => { const li = document.createElement("li"); li.className = item.status; li.textContent = item.step; root.append(li); });
}

function renderMetrics(metrics) {
  const values = metrics ? [["模型步骤",metrics.model_steps],["工具调用",metrics.tool_calls],["总 Token",metrics.total_tokens ?? "—"],["耗时",`${(metrics.duration_ms/1000).toFixed(1)}s`]] : [["模型步骤","—"],["工具调用","—"],["总 Token","—"],["耗时","—"]];
  const root = $("metrics"); root.replaceChildren(); values.forEach(([label,value]) => { const card=document.createElement("div");card.className="metric";const l=document.createElement("span");l.textContent=label;const v=document.createElement("strong");v.textContent=value;card.append(l,v);root.append(card); });
}

function renderEvidence(run) {
  const metrics = run.metrics || {}; const files = run.changed_files?.join(", ") || "尚无变更";
  const rows = [["修改文件",files,""],["测试通过",metrics.verification_passed || 0,(metrics.verification_passed||0)>0?"ok":""],["测试失败",metrics.verification_failed || 0,(metrics.verification_failed||0)>0?"bad":""],["Checkpoint",run.checkpoint,""],["完成协议",stateHas("completion_verified")?"已通过":"未通过",stateHas("completion_verified")?"ok":""]];
  const root=$("evidence");root.replaceChildren();rows.forEach(([label,value,cls])=>{const row=document.createElement("div");row.className="evidence-row";const l=document.createElement("span");l.textContent=label;const v=document.createElement("strong");v.textContent=value;v.className=cls;row.append(l,v);root.append(row);});
}

function stateHas(type) { return [...state.events.values()].some(event => event.type === type); }
function addEvent(event) { state.events.set(Number(event.sequence), event); }
function renderTimeline() {
  const root=$("timeline");root.replaceChildren();const events=[...state.events.values()].sort((a,b)=>a.sequence-b.sequence);
  events.filter(event=>event.type!=="text_delta").forEach(event=>{const row=document.createElement("div");row.className="event";const time=document.createElement("time");time.textContent=String(event.sequence).padStart(4,"0");const type=document.createElement("span");type.className="event-type";type.textContent=event.type;const msg=document.createElement("span");msg.className="event-message";msg.textContent=event.data?.message||"";row.append(time,type,msg);root.append(row);});
  $("event-count").textContent=`${events.length} events`;root.scrollTop=root.scrollHeight;
}

function connectStream() {
  const after = Math.max(0, ...state.events.keys()); state.source = new EventSource(`/api/runs/${state.runId}/stream?after=${after}`);
  $("connection").textContent="SSE 已连接";$("connection").classList.add("live");
  eventTypes.forEach(type => state.source.addEventListener(type, async message => { addEvent(JSON.parse(message.data)); renderTimeline(); await loadDetail(); if (["run_completed","run_failed","run_cancelled","budget_exhausted"].includes(type)) { state.source.close(); $("connection").textContent="运行已结束"; $("connection").classList.remove("live"); await refreshRuns(); } }));
  state.source.onerror=()=>{ $("connection").textContent="等待重连";$("connection").classList.remove("live"); };
}

$("start-run").onclick = async () => { const task=$("task").value.trim();$("form-error").textContent="";if(!task){$("form-error").textContent="请输入任务。";return;}try{const result=await api("/api/runs",{method:"POST",body:JSON.stringify({task})});await waitForRun(result.run_id);await selectRun(result.run_id);}catch(error){$("form-error").textContent=error.message;} };
async function waitForRun(runId){for(let i=0;i<30;i++){try{await api(`/api/runs/${runId}`);return;}catch{await new Promise(resolve=>setTimeout(resolve,100));}}throw new Error("Run 初始化超时");}
$("cancel-run").onclick=async()=>{if(!state.runId)return;try{await api(`/api/runs/${state.runId}/cancel`,{method:"POST",body:"{}"});$("cancel-run").disabled=true;}catch(error){alert(error.message);}};
$("new-run").onclick=()=>{if(state.source)state.source.close();state.runId=null;state.events.clear();$("run-view").classList.add("hidden");$("composer").classList.remove("hidden");$("page-title").textContent="运行控制台";$("connection").textContent="离线视图";$("connection").classList.remove("live");refreshRuns();$("task").focus();};
refreshRuns().catch(error=>{$("run-list").textContent=error.message;});

"use strict";

const trafficTerminal = new Set(["completed", "stopped", "failed", "interrupted"]);
const trafficStatus = {starting:"准备中",running:"运行中",stopping:"停止中",unknown:"清理待确认",completed:"已完成",stopped:"已停止",failed:"失败",interrupted:"中断已清理"};
let trafficState = null, trafficPending = false, trafficPollBusy = false;
let trafficChosen = null, trafficLastRender = "", trafficFormError = "";
let trafficFlows = [{ue:"ue1",target:"core",direction:"ul",rateMbps:1}];
const trafficName = (f) => `${f.ue.toUpperCase()} ${f.direction === "ul" ? "→" : "←"} ${f.target === "core" ? "Core" : "Edge"}`;
const trafficDate = (v) => new Date(v).toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false});
const trafficEnvironment = (v) => v === "cnf-lab" ? "基础服务链" : "双用户实验";
function trafficActive() { return trafficState?.jobs.find(j => !trafficTerminal.has(j.status)); }
function trafficMessage(message) {
  const node = $("#traffic-error");
  node.hidden = !message;
  node.textContent = message || "";
}
function trafficForm() {
  $("#traffic-flows").innerHTML = trafficFlows.map((f,i)=>`<div class="traffic-flow-row"><div class="traffic-flow-heading"><span class="traffic-flow-number">${String(i+1).padStart(2,"0")}</span><span>${esc(trafficName(f))}</span><button class="traffic-flow-remove" type="button" data-flow-remove="${i}" aria-label="删除第 ${i+1} 条流" ${trafficFlows.length===1?"disabled":""}>${icon("close")}</button></div><div class="traffic-flow-fields"><label for="flow-${i}-ue">终端<select id="flow-${i}-ue" data-flow="${i}" data-field="ue">${["ue1","ue2"].map(v=>`<option value="${v}" ${f.ue===v?"selected":""}>${v.toUpperCase()}</option>`).join("")}</select></label><label for="flow-${i}-target">目的后端<select id="flow-${i}-target" data-flow="${i}" data-field="target">${["core","edge"].map(v=>`<option value="${v}" ${f.target===v?"selected":""}>${v==="core"?"Core 中心":"Edge 边缘"}</option>`).join("")}</select></label><label for="flow-${i}-direction">传输方向<select id="flow-${i}-direction" data-flow="${i}" data-field="direction"><option value="ul" ${f.direction==="ul"?"selected":""}>上行 · UE 上传</option><option value="dl" ${f.direction==="dl"?"selected":""}>下行 · UE 下载</option></select></label><label for="flow-${i}-rate">目标速率 · Mbps<input id="flow-${i}-rate" data-flow="${i}" data-field="rateMbps" aria-describedby="traffic-validation" type="number" min="0.1" max="4" step="0.1" value="${f.rateMbps}" required/></label></div></div>`).join("");
  trafficValidate();
}
function trafficValidate() {
  const environment = $("#traffic-environment").value;
  const seconds = Number($("#traffic-duration").value);
  const targets = new Set();
  let error = "", total = 0;
  if (!Number.isInteger(seconds) || seconds < 10 || seconds > 300) error = "请输入 10–300 的整数秒。";
  for (const f of trafficFlows) {
    if (!finite(f.rateMbps) || f.rateMbps < .1 || f.rateMbps > 4) error = "每条流的目标速率须在 0.1–4 Mbps 内。";
    else total += f.rateMbps;
    const key = `${environment==="cnf-lab"?"shared":f.ue}/${f.target}`;
    if (targets.has(key)) error = environment === "cnf-lab" ? "基础服务链共享后端，Core 和 Edge 各只能配置一条流。" : "同一用户的同一后端只能配置一条流。";
    targets.add(key);
  }
  if (total > 4.000001) error = "目标发送速率之和超过 4 Mbps，请降低速率或删除流量。";
  $$("#traffic-form [aria-invalid]").forEach(e=>e.removeAttribute("aria-invalid"));
  if (!Number.isInteger(seconds) || seconds<10 || seconds>300) $("#traffic-duration").setAttribute("aria-invalid","true");
  trafficFlows.forEach((f,i)=>{if (!finite(f.rateMbps)||f.rateMbps<.1||f.rateMbps>4||total>4.000001) $(`#flow-${i}-rate`)?.setAttribute("aria-invalid","true");});
  if (error && !$("#traffic-form [aria-invalid]")) $("#traffic-environment").setAttribute("aria-invalid","true");
  const max = environment === "cnf-lab" ? 2 : 4;
  $("#traffic-add").disabled = trafficFlows.length >= max || trafficPending;
  $("#traffic-total").innerHTML = `${fmt(total,1)} <small> / 4 Mbps</small>`;
  $("#traffic-budget-fill").style.width = `${Math.min(100,total/4*100)}%`;
  $("#traffic-budget-fill").classList.toggle("over",total>4.000001);
  $("#traffic-environment-note").textContent = environment === "cnf-lab" ? "两个 UE 共享 Core / Edge，同一后端只支持一条流。" : "每个用户拥有独立 Core / Edge 测试实例。";
  $("#traffic-validation").textContent = error || trafficFormError;
  const active = trafficActive();
  const available = trafficState?.available && sourceLive();
  $("#traffic-start").disabled = !!error || trafficPending || !!active || !available;
  $("#traffic-start").innerHTML = trafficPending ? `${icon("clock")}请求处理中` : active ? `${icon("clock")}已有实验运行` : `${icon("arrow")}启动实验`;
  $("#traffic-config-fields").disabled = trafficPending;
  return error;
}
function trafficPreset(name) {
  const flow=(ue,target,direction)=>({ue,target,direction,rateMbps:1});
  if (name === "four") {
    $("#traffic-environment").value="cnf-multi-ue";
    trafficFlows=[flow("ue1","core","ul"),flow("ue2","edge","dl"),flow("ue1","edge","dl"),flow("ue2","core","ul")];
  } else if (name === "mixed") trafficFlows=[flow("ue1","core","ul"),flow("ue2","edge","dl")];
  else trafficFlows=[flow("ue1","core","ul")];
  trafficFormError="";
  trafficForm();
}
function trafficFlowLine(f) {
  return `<div class="traffic-task-flow"><span class="traffic-user-chip">${esc(f.ue.toUpperCase())}</span><span>${f.direction==="ul"?"→ 上传":"← 下载"}</span><span class="traffic-target-chip ${f.target}">${f.target==="core"?"Core":"Edge"}</span><small>${fmt(f.rateMbps,1)} Mbps</small></div>`;
}
function renderTraffic() {
  trafficValidate();
  if (!trafficState) return;
  const active=trafficActive(), job=active || trafficState.jobs[0];
  $("#traffic-nav-dot").hidden=!active;
  $("#traffic-service-text").textContent=trafficState.available?"任务服务可用":"任务服务断开";
  $("#traffic-service-dot").className=`dot ${trafficState.available?"mint":"amber"}`;
  const key=JSON.stringify([trafficState.jobs,trafficState.error,trafficState.available,trafficChosen]);
  if (key===trafficLastRender) return;
  trafficLastRender=key;
  const focused=document.activeElement;
  const focusId=focused?.id, focusJob=focused?.dataset?.trafficHistory;
  const focusedResult=focused?.closest('details[data-traffic-result-key]')?.dataset.trafficResultKey;
  const expanded=$$('details[data-traffic-result-key][open]').map(e=>e.dataset.trafficResultKey);
  const statusLabel=(j)=>`<span class="traffic-status ${j.status}"><span class="dot ${["failed","unknown","interrupted"].includes(j.status)?"danger":j.status==="stopping"?"amber":"mint"}"></span>${esc(trafficStatus[j.status]||j.status)}</span>`;
  if (!job) {
    $("#traffic-task-content").innerHTML=`<div class="traffic-empty"><div class="traffic-empty-icon">${icon("route")}</div><h3>准备好，让一条流出发</h3><p>左侧配置完成后启动。这里会显示任务进度、真实用户地址与当前传输路径。</p></div>`;
  } else {
    const stale = !trafficState.available;
    const remaining=Math.max(0,job.config.durationSeconds-(job.completedSeconds||0)-Math.min(job.segmentSeconds||45,Math.max(0,(job.elapsedSeconds||0)-(job.completedSeconds||0))));
    const bad=["failed","unknown","interrupted"].includes(job.status);
    const neutral=job.status==="stopped";
    const mainProgress=bad?`<strong class="traffic-progress-status">${esc(trafficStatus[job.status])}</strong>`:neutral?'<strong class="traffic-progress-status">已停止</strong>':`<strong>${job.progress||0}<small>%</small></strong>`;
    const reason=job.results.find(r=>r.error)?.error;
    const resolved=job.resolved?.map(f=>`${f.ue.toUpperCase()} ${f.ip}`).filter((v,i,a)=>a.indexOf(v)===i).join(" · ");
    $("#traffic-task-content").innerHTML=`<div class="traffic-task-body ${bad?"is-failure":neutral?"is-neutral":""}"><div class="traffic-state-line">${statusLabel(job)}<span class="traffic-job-id">#${esc(job.id.slice(0,8))}</span></div><div class="traffic-progress-line">${mainProgress}<span>${trafficTerminal.has(job.status)?`累计运行 ${fmt(job.elapsedSeconds||0,0)} 秒`:`约剩 ${Math.ceil(remaining)} 秒 · 不含检查与重连`}</span></div><div class="traffic-progress" role="progressbar" aria-label="任务分段进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${job.progress||0}"><span style="width:${job.progress||0}%"></span></div><p class="traffic-progress-note">执行进度；实际吞吐见下方已完成分段结果</p>${stale?`<p class="traffic-stale">${trafficTerminal.has(job.status)?"任务服务当前不可达；此处保留最近已确认的历史结果。":"任务连接中断，当前状态未经确认；VM1 任务可能仍在运行。"}</p>`:""}<p class="traffic-task-message">${esc(job.message)}${reason?`<br/><strong>${esc(short(reason,130))}</strong>`:""}</p><div class="traffic-task-meta"><div><span>业务环境</span><strong>${trafficEnvironment(job.config.environment)}</strong></div><div><span>计划持续</span><strong>${job.config.durationSeconds} 秒 · ${Math.ceil(job.config.durationSeconds/45)} 个分段</strong></div><div><span>当前分段</span><strong>${job.segment||0} / ${Math.ceil(job.config.durationSeconds/45)}</strong></div><div><span>启动时间</span><strong>${time(job.startedAt||job.createdAt)}</strong></div>${resolved?`<div><span>真实隧道</span><strong>${esc(resolved)}</strong></div>`:""}</div><div class="traffic-task-flows">${job.config.flows.map(trafficFlowLine).join("")}</div><div class="traffic-task-actions"><button type="button" class="button subtle" data-traffic-topology="${job.config.environment}">${icon("network")}查看对应拓扑</button>${!trafficTerminal.has(job.status)?`<button type="button" class="button traffic-stop" id="traffic-stop" data-traffic-stop="${job.id}" ${trafficPending||job.status==="stopping"?"disabled":""}>${icon("close")}${job.status==="stopping"?"正在确认停流":"停止本次实验"}</button>`:""}</div></div>`;
  }
  const historical=trafficState.jobs;
  if (!historical.some(j=>j.id===trafficChosen)) trafficChosen=historical[0]?.id;
  const selected=historical.find(j=>j.id===trafficChosen);
  $("#traffic-history").innerHTML=historical.map(j=>`<button type="button" data-traffic-history="${j.id}" class="${j.id===trafficChosen?"active":""}" aria-pressed="${j.id===trafficChosen}"><strong>${trafficDate(j.createdAt)} · ${esc(trafficStatus[j.status]||j.status)}</strong>${trafficEnvironment(j.config.environment)} · ${j.config.flows.length} 条流 · ${j.config.durationSeconds}s</button>`).join("");
  const resultNode=$("#traffic-result-content");
  if (!selected) resultNode.innerHTML='<p class="traffic-results-empty">还没有实验记录。完成第一条传输后，在这里查看实际接收速率。</p>';
  else {
    resultNode.innerHTML=`<div class="traffic-result-heading"><div><strong>${trafficEnvironment(selected.config.environment)} · #${esc(selected.id.slice(0,8))}</strong><p>${esc(trafficStatus[selected.status]||selected.status)} · 分段分别统计，未完成分段不计为零</p></div><a href="/api/traffic/${selected.id}/report" download>${icon("download")}下载报告</a></div>${selected.results.length?`<div class="traffic-table-hint">左右滑动查看更多分段结果 →</div><div class="traffic-result-scroll"><table><thead><tr><th>分段 / 流</th><th>业务路径</th><th>目标发送</th><th>实际接收</th><th>结果</th></tr></thead><tbody>${selected.results.map(r=>{const f=selected.config.flows[r.flowIndex];return `<tr><td>${r.segment} / ${r.flowIndex+1}</td><td>${esc(trafficName(f))}</td><td>${fmt(f.rateMbps,1)} Mbps</td><td>${finite(r.receiverMbps)?`${fmt(r.receiverMbps,3)} Mbps`:"—"}</td><td>${r.error?`<details data-traffic-result-key="${selected.id}-${r.segment}-${r.flowIndex}"><summary>${esc(trafficStatus[r.status]||r.status)} · ${esc(short(r.error,45))}</summary>${esc(r.error)}</details>`:`<span class="traffic-target-chip edge">成功</span>`}</td></tr>`;}).join("")}</tbody></table></div>`:'<p class="traffic-result-wait">尚无完整分段结果。任务运行时可切到拓扑观察活动。</p>'}`;
  }
  expanded.forEach(key=>{const detail=$$('details[data-traffic-result-key]').find(e=>e.dataset.trafficResultKey===key);if(detail) detail.open=true;});
  if (focusedResult) $$('details[data-traffic-result-key]').find(e=>e.dataset.trafficResultKey===focusedResult)?.querySelector('summary')?.focus({preventScroll:true});
  else if (focusJob) $$('[data-traffic-history]').find(e=>e.dataset.trafficHistory===focusJob)?.focus({preventScroll:true});
  else if (focusId && focusId==="traffic-stop") $("#traffic-stop")?.focus({preventScroll:true});
}
async function trafficPoll() {
  if (trafficPollBusy) return;
  trafficPollBusy=true;
  try {
    const response=await fetch('/api/traffic',{cache:'no-store',signal:AbortSignal.timeout(7000)});
    if (!response.ok) throw Error(`HTTP ${response.status}`);
    trafficState=await response.json();
    trafficMessage(trafficState.error || trafficFormError);
  } catch(e) {
    if (trafficState) trafficState.available=false;
    trafficMessage(`任务服务连接失败：${e.message}。已有任务可能仍在 VM1 运行，请恢复连接确认。`);
  } finally { trafficPollBusy=false; renderTraffic(); }
}
async function trafficAction(action,payload) {
  if (trafficPending) return;
  trafficPending=true; trafficFormError=""; trafficLastRender=""; renderTraffic();
  try {
    if (!trafficState?.token) throw Error('任务服务尚未连接，请稍后重试。');
    const response=await fetch(`/api/traffic/${action}`,{method:'POST',headers:{'Content-Type':'application/json','X-Traffic-Token':trafficState.token},body:JSON.stringify(payload),signal:AbortSignal.timeout(65000)});
    const value=await response.json();
    if (!response.ok) throw Error(value.error||`HTTP ${response.status}`);
    if (action==='start') {
      trafficChosen=value.job.id;
      trafficState.jobs=[value.job,...trafficState.jobs.filter(j=>j.id!==value.job.id)];
      // Prepare the exact environment now, while keeping the control panel open.
      pathSpace=value.job.config.environment;
      $("#path-space").value=pathSpace;
      toast('任务已创建。点击“查看对应拓扑”观察真实流量。');
    } else toast('已请求停止，正在确认 UE 内的发流进程退出。');
    trafficMessage("");
  } catch(e) {
    trafficFormError=e.message;
    trafficMessage(`${e.message}${e.name==="TimeoutError"?" 请求结果未确认，请等待任务状态恢复，不要重复启动。":""}`);
  } finally {trafficPending=false;trafficLastRender="";await trafficPoll();renderTraffic();}
}
$("#traffic-form").addEventListener('submit',event=>{
  event.preventDefault();
  if (trafficValidate()||trafficPending||trafficActive()) return;
  trafficAction('start',{environment:$("#traffic-environment").value,durationSeconds:Number($("#traffic-duration").value),flows:trafficFlows});
});
$("#traffic-flows").addEventListener('input',event=>{
  const e=event.target;
  if (!e.matches('[data-flow]')) return;
  trafficFlows[Number(e.dataset.flow)][e.dataset.field]=e.dataset.field==='rateMbps'?Number(e.value):e.value;
  trafficFormError="";
  const heading=e.closest('.traffic-flow-row').querySelector('.traffic-flow-heading>span+span');
  heading.textContent=trafficName(trafficFlows[Number(e.dataset.flow)]);
  trafficValidate();
});
$("#traffic-environment").addEventListener('change',()=>{trafficFormError="";trafficValidate();});
$("#traffic-duration").addEventListener('input',()=>{trafficFormError="";trafficValidate();});
$("#traffic-add").addEventListener('click',()=>{
  const env=$("#traffic-environment").value;
  for (const [ue,target] of [['ue1','core'],['ue2','edge'],['ue1','edge'],['ue2','core']]) {
    if (!trafficFlows.some(f=>f.target===target&&(env==='cnf-lab'||f.ue===ue))) {
      trafficFlows.push({ue,target,direction:'ul',rateMbps:1});break;
    }
  }
  trafficForm();
  $(`#flow-${trafficFlows.length-1}-ue`)?.focus();
});
document.addEventListener('click',event=>{
  const e=event.target.closest('[data-traffic-preset],[data-flow-remove],[data-traffic-stop],[data-traffic-topology],[data-traffic-history]');
  if (!e||trafficPending) return;
  if (e.dataset.trafficPreset) trafficPreset(e.dataset.trafficPreset);
  else if (e.dataset.flowRemove!==undefined) {trafficFlows.splice(Number(e.dataset.flowRemove),1);trafficForm();$("#traffic-add").focus();}
  else if (e.dataset.trafficStop) trafficAction('stop',{id:e.dataset.trafficStop});
  else if (e.dataset.trafficTopology) {pathSpace=e.dataset.trafficTopology;$("#path-space").value=pathSpace;topologyMode='business';$$('[data-topology]').forEach(b=>b.classList.toggle('active',b.dataset.topology==='business'));switchView('topology');}
  else if (e.dataset.trafficHistory) {trafficChosen=e.dataset.trafficHistory;trafficLastRender="";renderTraffic();}
});
trafficForm();
trafficPoll();
setInterval(trafficPoll,3000);

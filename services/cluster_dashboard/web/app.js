"use strict";

// All values originating in the cluster are escaped before entering HTML/SVG.
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = (x) =>
  String(x ?? "—").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const finite = (x) => typeof x === "number" && Number.isFinite(x);
const fmt = (x, digits = 1) =>
  finite(x)
    ? x.toLocaleString("en-US", {
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
      })
    : "—";
const bytes = (x) =>
  !finite(x)
    ? "—"
    : x >= 1024 ** 3
      ? `${fmt(x / 1024 ** 3)} GiB`
      : `${fmt(x / 1024 ** 2, 0)} MiB`;
const cpu = (x) => (!finite(x) ? "—" : `${fmt(x * 1000, 0)}m`);
const pct = (x, total) =>
  finite(x) && finite(total) && total > 0 ? (x / total) * 100 : null;
const time = (x) =>
  x && !Number.isNaN(Date.parse(x))
    ? new Date(x).toLocaleTimeString("zh-CN", { hour12: false })
    : "—";
const ageSeconds = (x) =>
  x ? Math.max(0, (Date.now() - Date.parse(x)) / 1000) : null;
const relative = (x) => {
  const s = ageSeconds(x);
  return s === null
    ? "—"
    : s < 60
      ? `${Math.floor(s)} 秒前`
      : s < 3600
        ? `${Math.floor(s / 60)} 分钟前`
        : `${Math.floor(s / 3600)} 小时前`;
};
const short = (s, n = 30) =>
  String(s ?? "—").length > n
    ? `${String(s).slice(0, n - 1)}…`
    : String(s ?? "—");
const icons = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  network:
    '<rect x="9" y="2" width="6" height="5" rx="1"/><rect x="2" y="17" width="6" height="5" rx="1"/><rect x="16" y="17" width="6" height="5" rx="1"/><path d="M12 7v5M5 17v-5h14v5"/>',
  box: '<path d="m12 2 9 5v10l-9 5-9-5V7zM3 7l9 5 9-5M12 12v10M7.5 4.5l9 5"/>',
  pulse: '<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  book: '<path d="M12 5c-3-3-6-3-10-2v16c4-1 7-1 10 2 3-3 6-3 10-2V3c-4-1-7-1-10 2v16"/>',
  layers: '<path d="m12 2 10 6-10 6L2 8zM2 12l10 6 10-6M2 16l10 6 10-6"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 5 0c0 2-2.5 2-2.5 4M12 17h.01"/>',
  refresh:
    '<path d="M20 8A8.5 8.5 0 0 0 5 5L2 8m0-5v5h5M4 16a8.5 8.5 0 0 0 15 3l3-3m0 5v-5h-5"/>',
  server:
    '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M11 6.5h6M11 17.5h6"/>',
  shield:
    '<path d="M12 2 3 6v6c0 5 9 10 9 10s9-5 9-10V6z"/><path d="m8 12 3 3 5-6"/>',
  "arrow-up-right": '<path d="M6 18 18 6M6 6h12v12"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  "check-circle": '<circle cx="12" cy="12" r="9"/><path d="m7.5 12 3 3 6-6"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  maximize: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
  phone:
    '<rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 18h4M10 5h4"/>',
  radio:
    '<path d="M8 7a7 7 0 0 0 0 10m8-10a7 7 0 0 1 0 10M5 4a11 11 0 0 0 0 16M19 4a11 11 0 0 1 0 16M12 12v9M9 21h6"/><circle cx="12" cy="10" r="2"/>',
  route:
    '<circle cx="5" cy="5" r="2"/><circle cx="19" cy="19" r="2"/><path d="M7 5h8a4 4 0 0 1 0 8H9a3 3 0 0 0 0 6h8"/>',
  cloud:
    '<path d="M6 19h12a4 4 0 0 0 .8-7.9A7 7 0 0 0 5.3 8.8 5.2 5.2 0 0 0 6 19Z"/>',
  chip: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4M10 10h4v4h-4z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  activity: '<path d="M3 20V4m0 16h18M7 15l4-6 4 3 5-7"/>',
  database:
    '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
  alert: '<path d="m12 3 10 18H2zM12 9v5M12 17h.01"/>',
  eye: '<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12Z"/><circle cx="12" cy="12" r="3"/>',
};
const icon = (name, cls = "") =>
  `<svg class="icon ${esc(cls)}" viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.box}</svg>`;
const hydrate = (root = document) =>
  $$("[data-icon]", root).forEach((e) => {
    e.innerHTML = icon(e.dataset.icon);
    e.removeAttribute("data-icon");
  });
const label = (text, kind = "quiet") =>
  `<span class="label ${esc(kind)}">${esc(text)}</span>`;
const badge = (ready) =>
  label(ready ? "Ready" : "未就绪", ready ? "good" : "bad");
const stateText = (s) =>
  ({ pass: "通过", fail: "异常", unknown: "待确认" })[s] || s;
let state = null,
  data = null,
  view = "overview",
  topologyMode = "business",
  resourceMode = "pods",
  namespace = "",
  search = "",
  pathSpace = "cnf-lab",
  topologyNamespace = "",
  zoomLevel = 1;
let lastObservation = null,
  lastDrawer = null,
  drawerFocus = null,
  toastTimer = null,
  pollFailure = null,
  previousBusy = false,
  requestedRefresh = false;
const nodeFor = (name) => data?.nodes.find((n) => n.name === name);
const podBy = (id) => data?.pods.find((p) => p.id === id);
const serviceBy = (id) => data?.services.find((s) => s.id === id);
const sourceLive = () =>
  state?.source !== "replay" &&
  !state?.error &&
  !pollFailure &&
  ageSeconds(data?.observedAt) <
    Math.max(60, (state?.intervalSeconds || 15) * 2 + 10);
const modeIcon = (p) =>
  ({
    UE1: "phone",
    UE2: "phone",
    gNB: "radio",
    UPF: "route",
    Envoy: "network",
    Core: "server",
    Edge: "cloud",
    MongoDB: "database",
  })[p?.component] || "box";

function toast(text) {
  $("#toast").textContent = text;
  $("#toast").classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("#toast").classList.add("hidden"), 4500);
}
function switchView(name) {
  if (!["overview", "topology", "traffic", "resources", "health", "guide"].includes(name))
    name = "overview";
  view = name;
  $$(".view").forEach((e) =>
    e.classList.toggle("active", e.id === `${name}-view`),
  );
  $$(".nav-item").forEach((e) =>
    e.classList.toggle("active", e.dataset.view === name),
  );
  const titles = {
    traffic: ["让流量，按需出发", "配置一次真实传输，观察路径、方向与结果。", "流量实验"],
    overview: [
      "把网络，看清楚",
      "从终端接入到核心与边缘，每一个节点、每一段连接，尽在此处。",
      "运行总览",
    ],
    topology: [
      "连接，有迹可循",
      "沿着功能关系理解业务路径，点击节点查看实际资源与观测证据。",
      "网络拓扑",
    ],
    resources: [
      "资源，各就其位",
      "按命名空间与类型检索，查看容器、服务、策略和存储的真实状态。",
      "资源清单",
    ],
    health: [
      "状态，逐项确认",
      "每项检查给出来源、时间与范围，保留异常和未知。",
      "健康检查",
    ],
    guide: [
      "读懂这张网",
      "从三台虚拟机开始，理解核心网、容器服务与业务数据之间的关系。",
      "系统导览",
    ],
  };
  const [title, desc, crumb] = titles[name];
  $("#page-title").innerHTML = `${esc(title)}<span class="title-dot">。</span>`;
  $("#page-description").textContent = desc;
  $("#breadcrumb-current").textContent = crumb;
  history.replaceState(null, "", `#${name}`);
  if (data) renderCurrent();
  window.scrollTo({ top: 0, behavior: "instant" });
}

function renderStats() {
  const active = data.pods.filter((p) => p.phase !== "Succeeded");
  const ready = active.filter((p) => p.ready).length;
  const nodesReady = data.nodes.filter(
    (n) => n.ready && !n.pressure.length,
  ).length;
  const restarts = data.pods.reduce((a, p) => a + p.restarts, 0);
  const failures = data.checks.filter((c) => c.status === "fail").length;
  const rows = [
    [
      "集群节点",
      nodesReady,
      `/ ${data.nodes.length}`,
      "server",
      "blue",
      `${nodesReady === data.nodes.length ? "节点均已就绪" : "存在节点异常"}`,
      nodesReady === data.nodes.length,
    ],
    [
      "运行中的 Pod",
      ready,
      `/ ${active.length}`,
      "box",
      "teal",
      `含 ${data.pods.filter((p) => p.namespace === "kube-system").length} 个系统 Pod`,
      ready === active.length,
    ],
    [
      "代理业务路径",
      data.routes.length,
      "条",
      "network",
      "purple",
      "来自当前挂载的 Envoy 配置",
      true,
    ],
    [
      "容器累计重启",
      restarts,
      "次",
      "refresh",
      "blue",
      failures ? `${failures} 项健康检查异常` : "按当前 Pod 生命周期累计",
      !failures,
    ],
  ];
  $("#stats").innerHTML = rows
    .map(
      ([name, value, denom, ic, color, note, good]) =>
        `<article class="stat-card"><div class="stat-top"><span>${name}</span><span class="stat-icon ${color}">${icon(ic)}</span></div><div class="stat-value">${value}<span class="stat-denominator">${denom}</span></div><div class="stat-bottom"><span class="dot ${good ? "mint" : "amber"}"></span><span>${esc(note)}</span></div></article>`,
    )
    .join("");
  $("#nav-pods").textContent = data.pods.length;
}
function meter(name, value, total, memory = false) {
  const p = pct(value, total);
  return `<div><div class="meter-heading"><span>${name}</span><strong>${p === null ? "—" : fmt(p) + "%"}</strong></div><div class="meter ${memory ? "memory" : ""} ${p > 85 ? "hot" : ""}"><span style="width:${p === null ? 0 : Math.min(100, p)}%"></span></div></div>`;
}
function renderNodes() {
  $("#nodes").innerHTML = data.nodes
    .map((n) => {
      const pods = data.pods.filter((p) => p.node === n.name);
      return `<button class="node-card" data-detail="${esc(n.id)}"><div class="node-card-top"><span class="node-symbol">${icon("server")}</span><div><div class="node-title">${esc(n.alias)} <span style="font-weight:400;color:#97a4b4">/</span> ${esc(n.role)}</div><div class="node-subtitle mono">${esc(n.name)}</div></div>${badge(n.ready && !n.pressure.length)}</div><div class="node-meta"><span class="mono">${esc(n.ip)}</span><span>·</span><span>${fmt(n.cpuCores, 0)} vCPU</span><span>·</span><span>${bytes(n.memoryBytes)} 可分配</span></div><div class="node-meters">${meter("CPU", n.cpuUsed, n.cpuCores)}${meter("内存", n.memoryUsed, n.memoryBytes, true)}</div><div class="node-footer"><span>${pods.filter((p) => p.ready).length}/${pods.length} Pods Ready</span><span>${esc(n.info?.kubeletVersion || "—")} ${icon("arrow-up-right")}</span></div></button>`;
    })
    .join("");
}
const ring = (checks, size = 58) => {
  const passed = checks.filter((c) => c.status === "pass").length;
  const total = checks.length;
  const color = checks.some((c) => c.status === "fail")
    ? "#d57a78"
    : checks.some((c) => c.status === "unknown")
      ? "#cfab6b"
      : "#66b9a0";
  return `<div class="health-ring"><svg viewBox="0 0 60 60" aria-hidden="true"><circle cx="30" cy="30" r="25" fill="none" stroke="#edf3f5" stroke-width="4"/><circle cx="30" cy="30" r="25" fill="none" stroke="${color}" stroke-width="4" stroke-dasharray="${total ? (passed / total) * 157 : 0} 157" stroke-linecap="round"/></svg><strong style="color:${color}">${passed}<span class="sr-only">项通过，共 ${total} 项</span></strong></div>`;
};
function renderHealth() {
  const checks = data.checks;
  const pass = checks.filter((c) => c.status === "pass").length;
  const fail = checks.filter((c) => c.status === "fail").length;
  const unknown = checks.filter((c) => c.status === "unknown").length;
  const title = fail
    ? `${fail} 项需要处理`
    : unknown
      ? `${unknown} 项观测待确认`
      : "观测项均通过";
  $("#health-preview").innerHTML =
    `<div class="health-score">${ring(checks)}<button class="check-detail-btn" data-go="health"><div class="health-score-title">${title}</div><div class="health-score-note">${pass} / ${checks.length} 项通过 · ${unknown} 项待确认</div></button></div>${checks
      .filter((c) => ["nodes", "pods", "endpoints", "tunnels"].includes(c.key))
      .map(
        (c) =>
          `<div class="check-mini"><span>${icon(c.status === "pass" ? "check-circle" : c.status === "fail" ? "alert" : "info")}${esc(c.title)}</span><span class="check-state ${c.status}">${stateText(c.status)}</span></div>`,
      )
      .join("")}`;
  $("#health-summary").innerHTML =
    `<div class="health-main">${ring(checks)}<div><h2>${title}</h2><p>${pass} 项通过 · ${fail} 项异常 · ${unknown} 项待确认 &nbsp; / &nbsp; ${time(data.observedAt)} 采集</p></div></div>`;
  $("#health-cards").innerHTML = checks
    .map(
      (c) =>
        `<article class="health-card ${esc(c.status)}"><span class="health-card-icon">${icon(c.status === "pass" ? "check-circle" : c.status === "fail" ? "alert" : "info")}</span><div><h3>${esc(c.title)}${label(stateText(c.status), c.status === "pass" ? "good" : c.status === "fail" ? "bad" : "warning")}</h3><p>${esc(c.detail)}</p><small>观测时间 ${time(c.at)} · ${state.source === "replay" ? "归档回放" : "只读检查"}</small></div></article>`,
    )
    .join("");
  $("#health-events").innerHTML =
    Object.entries(data.errors)
      .map(
        ([key, value]) =>
          `<div class="event-row"><span class="event-dot warning"></span><div><strong class="event-title">采集源 ${esc(key)}</strong><p>${esc(value)}</p></div></div>`,
      )
      .join("") +
    (data.events.length
      ? data.events
          .map(
            (e) =>
              `<div class="event-row"><span class="event-dot ${e.type === "Warning" ? "warning" : ""}"></span><div><strong class="event-title">${esc(e.reason)} · ${esc(e.namespace)} / ${esc(e.object)}</strong><p>${esc(e.message)}</p><span class="td-sub">${esc(e.at)} · 累计 ${e.count} 次</span></div></div>`,
          )
          .join("")
      : '<div class="empty-event">当前没有保留的 Kubernetes 事件；不等于从未出现异常。</div>');
}

function defs(prefix) {
  return `<defs><marker id="${prefix}-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="#b2becd"/></marker><marker id="${prefix}-proxy" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="#ac9bcf"/></marker><marker id="${prefix}-live" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="#4eafa7"/></marker><marker id="${prefix}-correlated" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="#bf8737"/></marker></defs>`;
}
function svgIcon(name, x, y, color = "#8aa6bb", size = 20) {
  return `<g transform="translate(${x},${y}) scale(${size / 24})" fill="none" stroke="${color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${icons[name] || icons.box}</g>`;
}
function graphCard(p, x, y, w = 132, h = 66, sub = null) {
  if (!p)
    return `<g><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="9" fill="#fafbfd" stroke="#e0e6ef" stroke-dasharray="4 3"/><text x="${x + w / 2}" y="${y + h / 2}" text-anchor="middle" class="graph-muted">未发现资源</text></g>`;
  const color = ["UE1", "UE2", "gNB", "Edge"].includes(p.component)
    ? "#63aaa1"
    : p.component === "UPF"
      ? "#9b88c5"
      : "#7d9fbd";
  const n = nodeFor(p.node);
  const title =
    p.component === "Core" || p.component === "Edge"
      ? `${p.component}${p.name.includes("ue1") ? " · UE1" : p.name.includes("ue2") ? " · UE2" : ""}`
      : p.component;
  const i = interfaceRates(p);
  const interfaceLabel = i && h === 66 ? `<text x="${x + 11}" y="${y + 58}" class="graph-muted" font-size="8">↑ ${fmt(i.ul, 3)}  ↓ ${fmt(i.dl, 3)} Mbps</text>` : "";
  return `<g class="graph-click" role="button" tabindex="0" aria-label="查看 ${esc(p.component)} ${esc(p.name)}" data-detail="${esc(p.id)}"><title>${esc(p.name)}\n${esc(p.description)}</title><rect class="card-bg" x="${x}" y="${y}" width="${w}" height="${h}" rx="9" fill="#fff" stroke="#e1e8ef"/><rect x="${x + 11}" y="${y + 12}" width="30" height="30" rx="8" fill="${color}12"/>${svgIcon(modeIcon(p), x + 17, y + 18, color, 18)}<text x="${x + 50}" y="${y + 27}" class="graph-label" font-size="12" font-weight="600">${esc(title)}</text><circle cx="${x + w - 12}" cy="${y + 16}" r="2.8" fill="${p.ready ? "#5eb89a" : "#d97a7e"}"/><text x="${x + 50}" y="${y + 42}" class="graph-muted" font-size="8">${esc(n?.alias || "—")} · ${esc(sub || p.ip || "无 IP")}</text>${h > 66 ? `<text x="${x + 13}" y="${y + h - 10}" class="graph-muted">${esc(short(p.name, Math.floor(w / 5.6)))}</text>` : ""}${interfaceLabel}</g>`;
}
function referencePath(d, prefix, label, x, y) {
  return `<path d="${d}" fill="none" stroke="#b9c5d4" stroke-width="1.4" stroke-dasharray="4 5" marker-end="url(#${prefix}-arrow)"/><text x="${x}" y="${y}" text-anchor="middle" class="graph-muted" font-size="8">${esc(label)}</text>`;
}

function interfaceRates(p) {
  const i = p?.interfaceTraffic?.find((i) =>
    p.component === "UPF" ? i.name === "upfgtp" : i.name.startsWith("uesimtun"),
  );
  if (!i) return null;
  return { ...i, ul: p.component === "UPF" ? i.rxMbps : i.txMbps,
    dl: p.component === "UPF" ? i.txMbps : i.rxMbps };
}

function activityPath(d, prefix, label, x, y, source, target) {
  const link = data.trafficLinks?.find((l) => l.source === source?.id && l.target === target?.id);
  const live = sourceLive();
  const active = live && link?.status === "active";
  const status = !live ? "观测过期" : !link || link.status === "unknown" ? "等待观测" :
    link.status === "idle" ? "端点空闲" : link.status === "unmatched" ? "活动未对应" : "端点关联";
  const color = active ? "#bf8737" : "#a0afbf";
  const flow = (reverse) => `<path d="${d}" transform="translate(0,${reverse ? 1.5 : -1.5})" fill="none" stroke="#bf8737" stroke-width="1.8" class="flow-observed flow-correlated${reverse ? " flow-reverse" : ""}" ${reverse ? "marker-start" : "marker-end"}="url(#${prefix}-correlated)"/>`;
  return `<g ${link ? `class="graph-click" role="button" tabindex="0" data-traffic-link="${esc(link.id)}"` : ""} aria-label="${esc(label)}：${status}"><title>${esc(label)} · ${status}\n端点活动关联，不是逐链路抓包；点击查看计数依据。</title>${referencePath(d, prefix, label, x, y)}${active && link.forwardActive ? flow(false) : ""}${active && link.reverseActive ? flow(true) : ""}<path d="${d}" fill="none" stroke="transparent" stroke-width="18"/><text x="${x}" y="${y + 38}" text-anchor="middle" fill="${color}" font-size="8">${status}</text></g>`;
}

function observedPaths(d, route, prefix) {
  if (!sourceLive() || !finite(route.rxMbps) || !finite(route.txMbps))
    return "";
  const forward =
    route.txMbps > 0
      ? `<path d="${d}" transform="translate(0,-1.5)" fill="none" stroke="#4eafa7" stroke-width="1.6" class="flow-observed" marker-end="url(#${prefix}-live)"/>`
      : "";
  const reverse =
    route.rxMbps > 0
      ? `<path d="${d}" transform="translate(0,1.5)" fill="none" stroke="#4eafa7" stroke-width="1.6" class="flow-observed flow-reverse" marker-start="url(#${prefix}-live)"/>`
      : "";
  return forward + reverse;
}
function businessMap(prefix, space = "cnf-lab", compact = false) {
  const ues = data.pods
    .filter((p) => ["UE1", "UE2"].includes(p.component))
    .sort((a, b) => a.component.localeCompare(b.component));
  const gnb = data.pods.find((p) => p.component === "gNB");
  const upf = data.pods.find((p) => p.component === "UPF");
  const envoy = data.pods.find(
    (p) => p.component === "Envoy" && p.namespace === space,
  );
  const routes = data.routes.filter((r) => r.namespace === space);
  const multi = space === "cnf-multi-ue";
  const height = multi ? 505 : 355;
  const center = multi ? 217 : 145;
  const gnbX = 215,
    upfX = 395,
    proxyX = 575,
    backendX = 890;
  let out = `<svg viewBox="0 0 1100 ${height}" role="group" aria-label="${multi ? "双用户" : "基础"}业务路径：UE、gNB、UPF、Envoy 和中心/边缘后端。橙色为前段端点活动关联，绿色为代理实测。">${defs(prefix)}<text x="28" y="27" class="graph-heading">SIMULATED ACCESS</text><text x="395" y="27" class="graph-heading">USER PLANE</text><text x="575" y="27" class="graph-heading">SERVICE GATEWAY</text><text x="890" y="27" class="graph-heading">CORE / EDGE</text>`;
  const ueYs = ues.length > 1 ? [center - 45, center + 55] : [center];
  ues.forEach((p, i) => {
    const y = ueYs[i] || center;
    out += activityPath(
      `M160 ${y + 33} C188 ${y + 33},188 ${center + 33},${gnbX - 6} ${center + 33}`,
      prefix,
      `${p.component} 接入`,
      190,
      y + 19,
      p, gnb,
    );
  });
  out += activityPath(
    `M347 ${center + 33}H${upfX - 7}`,
    prefix,
    "N3 · GTP-U",
    370,
    center + 18,
    gnb, upf,
  );
  out += activityPath(
    `M527 ${center + 33}H${proxyX - 7}`,
    prefix,
    "N6 / TCP",
    550,
    center + 18,
    upf, envoy,
  );
  let endpoints = [];
  routes.forEach((r, i) => {
    const svc = serviceBy(r.target);
    const pod = svc?.endpoints.map((e) => podBy(e.target)).find(Boolean);
    const y = multi ? 61 + i * 94 : i === 0 ? 87 : 195;
    const active =
      sourceLive() &&
      finite(r.rxMbps) &&
      finite(r.txMbps) &&
      r.rxMbps + r.txMbps > 0;
    const unavailable = !r.statsAvailable;
    const stroke = active ? "#4eafa7" : "#ac9bcf";
    const d = `M707 ${center + 33} C794 ${center + 33},795 ${y + 33},${backendX - 7} ${y + 33}`;
    out += `<g class="graph-click" role="button" tabindex="0" data-route="${data.routes.indexOf(r)}" aria-label="查看监听端口 ${r.port} 的转发证据"><title>${esc(r.listener)} → ${esc(svc?.name || r.target)}\n${r.statsAvailable ? "代理 cluster 计数采样" : "代理计数不可用"}</title><path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.6" marker-end="url(#${prefix}-proxy)"/>${observedPaths(d, r, prefix)}<path d="${d}" stroke="transparent" stroke-width="17" fill="none"/><rect x="750" y="${y + 9}" width="115" height="30" rx="5" fill="#fff" fill-opacity=".94"/><text x="807" y="${y + 21}" text-anchor="middle" class="graph-label" font-size="9">:${r.port} → :${esc(r.targets[0]?.port)}</text><text x="807" y="${y + 34}" text-anchor="middle" fill="${stroke}" font-size="8">${unavailable ? "计数不可用" : !finite(r.rxMbps) ? "等待第二次采样" : `${fmt(r.rxMbps + r.txMbps, 3)} Mbps · ${r.activeConnections ?? "—"} 连接`}</text></g>`;
    endpoints.push(graphCard(pod, backendX, y, 180, 68));
  });
  ues.forEach((p, i) => {
    out += graphCard(
      p,
      28,
      ueYs[i] || center,
      132,
      66,
      p.telemetry.interfaces?.[0]?.addresses?.[0] || "隧道未知",
    );
  });
  out +=
    graphCard(gnb, gnbX, center) +
    graphCard(upf, upfX, center) +
    graphCard(envoy, proxyX, center);
  out += endpoints.join("");
  const stripY = height - 62;
  out += `<rect x="215" y="${stripY}" width="492" height="44" rx="8" fill="#f4f7fb" stroke="#e9eef5"/><text x="230" y="${stripY + 17}" class="graph-muted" font-size="8">CONTROL PLANE / 点击查看实际放置</text>`;
  ["AMF", "SMF", "PCF", "NRF", "AUSF"].forEach((comp, i) => {
    const p = data.pods.find((p) => p.component === comp);
    if (p)
      out += `<g class="graph-click" role="button" tabindex="0" data-detail="${esc(p.id)}" aria-label="查看 ${comp}"><rect x="${232 + i * 90}" y="${stripY + 23}" width="73" height="16" rx="3" fill="#fff" stroke="#e6ebf3"/><circle cx="${240 + i * 90}" cy="${stripY + 31}" r="2" fill="${p.ready ? "#78b8a8" : "#cf8c8c"}"/><text x="${250 + i * 90}" y="${stripY + 34}" class="graph-label" font-size="8">${comp}</text></g>`;
  });
  out += `<text x="770" y="${height - 24}" class="graph-muted" font-size="8">橙色：端点活动关联 · 绿色：代理实测</text></svg>`;
  return out;
}

function podMap() {
  const pods = data.pods.filter(
    (p) => !topologyNamespace || p.namespace === topologyNamespace,
  );
  const ordered = [...data.nodes].sort((a, b) =>
    b.alias.localeCompare(a.alias),
  );
  const positions = new Map();
  const width = Math.max(1100, ordered.length * 360);
  const maxCount = Math.max(
    ...ordered.map((n) => pods.filter((p) => p.node === n.name).length),
    1,
  );
  const podTop = 100 + data.routes.length * 3;
  const height = podTop + maxCount * 65 + 30;
  let out = `<svg viewBox="0 0 ${width} ${height}" role="group" aria-label="按实际节点放置的所有 Pod。曲线表示 Envoy 配置经 Service 解析到的 Pod 关系，不是全量流量抓包。">${defs("pods")}`;
  ordered.forEach((n, i) => {
    const x = 14 + i * 360;
    const list = pods
      .filter((p) => p.node === n.name)
      .sort((a, b) =>
        a.component === "Envoy"
          ? -1
          : b.component === "Envoy"
            ? 1
            : a.name.localeCompare(b.name),
      );
    out += `<rect x="${x}" y="10" width="342" height="${height - 20}" rx="12" fill="${i % 2 ? "#f8f6fc" : "#f4f9fa"}" stroke="#e3eaf0"/><g class="graph-click" role="button" tabindex="0" data-detail="${esc(n.id)}" aria-label="查看 ${n.alias}"><text x="${x + 18}" y="38" class="graph-label" font-size="13" font-weight="600">${esc(n.alias)} / ${esc(n.role)}</text><text x="${x + 18}" y="57" class="graph-muted">${esc(n.name)} · ${list.length} Pods</text></g>`;
    list.forEach((p, j) =>
      positions.set(p.id, { x: x + 20, y: podTop + j * 65, p }),
    );
  });
  data.routes.forEach((r, i) => {
    const a = positions.get(r.source),
      svc = serviceBy(r.target);
    if (!a || !svc) return;
    svc.endpoints.forEach((ep) => {
      const b = positions.get(ep.target);
      if (!b) return;
      const same = a.x === b.x;
      const startX = same ? a.x + 272 : a.x > b.x ? a.x : a.x + 272;
      const endX = same ? b.x + 272 : a.x > b.x ? b.x + 272 : b.x;
      const control = same ? a.x + 310 : (startX + endX) / 2;
      const active =
        sourceLive() &&
        finite(r.rxMbps) &&
        finite(r.txMbps) &&
        r.rxMbps + r.txMbps > 0;
      const corridorY = 70 + i * 3;
      // Cross-node relations travel through a dedicated upper corridor so
      // passing behind a third Pod cannot imply an unintended connection.
      const d = same ? `M${startX} ${a.y + 25} C${control} ${a.y + 25},${control} ${b.y + 25},${endX} ${b.y + 25}`
        : `M${startX} ${a.y+25}C${startX-16} ${a.y+25},${startX-16} ${corridorY},${startX-16} ${corridorY}H${endX+16}C${endX+16} ${corridorY},${endX+16} ${b.y+25},${endX} ${b.y+25}`;
      out += `<g class="graph-click" role="button" tabindex="0" data-route="${i}" aria-label="查看 ${esc(svc.name)} 连接"><title>${esc(r.listener)} → ${esc(svc.name)} → ${esc(b.p.name)}</title><path d="${d}" stroke="${active ? "#55afa6" : "#aa98c5"}" stroke-width="1.5" fill="none" class="${active ? (r.rxMbps > r.txMbps ? "flow-observed flow-reverse" : "flow-observed") : ""}"/><path d="${d}" stroke="transparent" stroke-width="14" fill="none"/></g>`;
    });
  });
  positions.forEach(({ x, y, p }) => {
    const bound = data.services.filter((s) =>
      s.endpoints.some((e) => e.target === p.id),
    );
    out += `<g class="graph-click pod-graph" tabindex="0" role="button" data-detail="${esc(p.id)}" aria-label="查看 Pod ${esc(p.name)}"><title>${esc(p.name)}\n${bound.length} 个 Service 端点绑定</title><rect class="card-bg pod-bg" x="${x}" y="${y}" width="272" height="51" rx="7" fill="#fff" stroke="#e2e9f0"/><circle cx="${x + 13}" cy="${y + 16}" r="3" fill="${p.ready ? "#62b197" : "#d47e82"}"/><text x="${x + 24}" y="${y + 20}" class="graph-label" font-size="10" font-weight="500">${esc(p.component)}</text><text x="${x + 260}" y="${y + 18}" text-anchor="end" class="graph-muted" font-size="7">${esc(p.namespace)}</text><text x="${x + 12}" y="${y + 38}" class="graph-muted" font-size="8">${esc(short(p.name, 43))}</text></g>`;
  });
  const unassigned = pods.filter((p) => !positions.has(p.id));
  if (unassigned.length)
    out += `<text x="25" y="${height - 8}" fill="#cc8585" font-size="10">${unassigned.length} 个 Pod 未分配到已知节点，请在资源清单查看。</text>`;
  return out + "</svg>";
}

function serviceMap() {
  const svcs = data.services.filter(
    (s) => !topologyNamespace || s.namespace === topologyNamespace,
  );
  const pods = data.pods.filter(
    (p) => !topologyNamespace || p.namespace === topologyNamespace,
  );
  const height = 105 + Math.max(svcs.length, pods.length, 1) * 56;
  let out = `<svg viewBox="0 0 1100 ${height}" role="group" aria-label="Service 到 Pod 的实际 EndpointSlice 绑定图">${defs("svc")}<text x="35" y="30" class="graph-heading">SERVICES / ${svcs.length}</text><text x="725" y="30" class="graph-heading">POD ENDPOINTS / ${pods.length}</text><text x="550" y="30" text-anchor="middle" class="graph-muted">EndpointSlice · 悬停高亮关系</text>`;
  const positions = new Map(pods.map((p, i) => [p.id, { p, y: 57 + i * 56 }]));
  svcs.forEach((s, i) =>
    s.endpoints.forEach((e) => {
      const target = positions.get(e.target);
      if (!target) return;
      const y = 79 + i * 56;
      out += `<path class="binding-edge" data-from="${esc(s.id)}" data-to="${esc(e.target)}" d="M390 ${y}C550 ${y},550 ${target.y + 22},710 ${target.y + 22}" fill="none" stroke="${e.ready === true ? "#a3b7c9" : "#dba1a5"}" stroke-width="1.4" opacity=".5"><title>${esc(s.name)} → ${esc(target.p.name)} · Ready ${esc(e.ready)}</title></path>`;
    }),
  );
  svcs.forEach((s, i) => {
    const y = 57 + i * 56;
    out += `<g class="graph-click" role="button" tabindex="0" data-detail="${esc(s.id)}" aria-label="查看 Service ${esc(s.name)}"><rect class="card-bg" x="30" y="${y}" width="360" height="44" rx="7" fill="#fff" stroke="#e1e8f1"/><text x="43" y="${y + 18}" class="graph-label" font-size="11">${esc(s.name)}</text><text x="43" y="${y + 33}" class="graph-muted" font-size="9">${esc(s.namespace)} · ${esc(s.ip)} · ${s.endpoints.filter((e) => e.ready === true).length} Ready</text></g>`;
  });
  positions.forEach(({ p, y }) => {
    out += `<g class="graph-click" role="button" tabindex="0" data-detail="${esc(p.id)}" aria-label="查看 Pod ${esc(p.name)}"><rect class="card-bg" x="710" y="${y}" width="360" height="44" rx="7" fill="#fff" stroke="#e1e8f1"/><circle cx="725" cy="${y + 16}" r="3" fill="${p.ready ? "#62b197" : "#d47e82"}"/><text x="736" y="${y + 19}" class="graph-label" font-size="11">${esc(p.component)} · ${esc(nodeFor(p.node)?.alias || "未分配")}</text><text x="723" y="${y + 34}" class="graph-muted" font-size="8">${esc(short(p.name, 55))}</text></g>`;
  });
  if (!svcs.length && !pods.length)
    out +=
      '<text x="550" y="80" text-anchor="middle" class="graph-muted">该命名空间没有相关资源</text>';
  return out + "</svg>";
}

function controlMap() {
  const locations = {
    gNB: [35, 180],
    AMF: [255, 180],
    SMF: [495, 180],
    UPF: [835, 180],
    AUSF: [255, 335],
    UDM: [495, 335],
    UDR: [735, 335],
    PCF: [495, 50],
    NRF: [255, 50],
    NSSF: [35, 50],
    NEF: [735, 50],
    CHF: [735, 465],
    MongoDB: [495, 465],
  };
  const refs = [
    ["gNB", "AMF", "N2 / NGAP"],
    ["AMF", "SMF", "会话控制"],
    ["SMF", "UPF", "N4 / PFCP"],
    ["AMF", "AUSF", "鉴权"],
    ["AUSF", "UDM", "鉴权数据"],
    ["UDM", "UDR", "订阅数据"],
    ["PCF", "SMF", "策略"],
    ["AMF", "NRF", "服务发现"],
    ["AMF", "NSSF", "切片选择"],
  ];
  let out = `<svg viewBox="0 0 1100 600" role="group" aria-label="控制面架构参考图：所有虚线均为协议职责说明，不代表已观测到实时连接">${defs("control")}<rect x="230" y="20" width="810" height="560" rx="15" fill="#f6f8fc" stroke="#e7edf4"/><text x="250" y="565" class="graph-muted">核心网功能参考 · 实际放置以卡片节点标记为准 · 未采集控制面逐流证据</text>`;
  refs.forEach(([a, b, label]) => {
    if (
      !data.pods.some((p) => p.component === a) ||
      !data.pods.some((p) => p.component === b)
    )
      return;
    const [x1, y1] = locations[a],
      [x2, y2] = locations[b];
    let d, tx, ty;
    if (y1 === y2) {
      d = `M${x1 + 150} ${y1 + 35}H${x2 - 7}`;
      tx = (x1 + 150 + x2) / 2;
      ty = y1 + 20;
    } else {
      const sy = y1 + (y2 > y1 ? 70 : 0),
        ey = y2 + (y2 > y1 ? -7 : 77),
        cy = (sy + ey) / 2;
      d = `M${x1 + 75} ${sy}C${x1 + 75} ${cy},${x2 + 75} ${cy},${x2 + 75} ${ey}`;
      tx = (x1 + x2) / 2 + 112;
      ty = (y1 + y2) / 2 + 35;
    }
    out += referencePath(d, "control", label, tx, ty);
  });
  Object.entries(locations).forEach(([c, [x, y]]) => {
    const p = data.pods.find((p) => p.component === c);
    if (p) out += graphCard(p, x, y, 150, 70);
  });
  return out + "</svg>";
}

function applyZoom() {
  const svg = $("#full-topology > svg");
  if (svg) {
    svg.style.width = `${zoomLevel * 100}%`;
    svg.style.maxWidth = "none";
  }
  $("#zoom-label").textContent = `${Math.round(zoomLevel * 100)}%`;
  $("#zoom-out").disabled = zoomLevel <= 0.6;
  $("#zoom-in").disabled = zoomLevel >= 2;
}

function renderTopology() {
  $("#overview-topology").innerHTML = businessMap("overview", "cnf-lab", true);
  $("#full-topology").innerHTML =
    topologyMode === "business"
      ? businessMap("full", pathSpace)
      : topologyMode === "pods"
        ? podMap()
        : topologyMode === "services"
          ? serviceMap()
          : controlMap();
  $("#path-space-label").classList.toggle(
    "hidden",
    topologyMode !== "business",
  );
  $("#topology-namespace-label").classList.toggle(
    "hidden",
    !["pods", "services"].includes(topologyMode),
  );
  const spaces = [
    ...new Set(
      data.pods
        .map((p) => p.namespace)
        .concat(data.services.map((s) => s.namespace)),
    ),
  ].sort();
  $("#topology-namespace").innerHTML =
    '<option value="">全部命名空间</option>' +
    spaces.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  $("#topology-namespace").value = topologyNamespace;
  const descriptions = {
    business: [
      "业务路径与功能关系",
      "橙色表示 UE / UPF / 代理入口的端点活动关联，绿色表示代理实测；灰色含空闲或缺测。箭头区分上下行，点击连线查看证据。",
    ],
    pods: [
      "实际节点放置与代理关系",
      "曲线由当前 Envoy 挂载配置和 Service EndpointSlice 共同解析。点击 Pod 查看它绑定的全部服务。",
    ],
    services: [
      "Service → Pod 的端点绑定",
      "连线来自 Kubernetes EndpointSlice；悬停高亮关联，点击查看端口与就绪状态。绑定不等于实际网络连接。",
    ],
    control: [
      "控制面协议与职责参考",
      "所有连线均为架构参考，不是实时抓包；卡片绑定当前 Pod，可查看真实放置、资源与状态。",
    ],
  };
  $("#topology-heading").textContent = descriptions[topologyMode][0];
  $("#topology-note").textContent = descriptions[topologyMode][1];
  applyZoom();
  $("#route-table").innerHTML =
    `<table><thead><tr><th>环境 / 监听</th><th>代理 Pod</th><th>目的 Service</th><th>后端 → 代理</th><th>代理 → 后端</th><th>活动连接 / 累计</th></tr></thead><tbody>${data.routes.map((r, i) => `<tr data-route="${i}" tabindex="0" role="button" aria-label="查看 ${esc(r.listener)}"><td><span class="td-name mono">:${r.port}</span><div class="td-sub">${esc(r.namespace)}</div></td><td title="${esc(r.source)}">${esc(short(podBy(r.source)?.name, 26))}</td><td class="mono">${esc(serviceBy(r.target)?.name || r.target)}</td><td>${fmt(r.rxMbps, 3)} Mbps</td><td>${fmt(r.txMbps, 3)} Mbps</td><td>${r.activeConnections ?? "—"} / ${r.totalConnections ?? "—"}</td></tr>`).join("") || '<tr><td colspan="6" class="resource-empty">未解析到有效代理配置；请查看健康检查与采集错误。</td></tr>'}</tbody></table>`;
}

function renderChart() {
  const history = state.history.slice(-30);
  const valid = history.filter((h) => finite(h.rxMbps) && finite(h.txMbps));
  if (!valid.length) {
    $("#traffic-chart").innerHTML =
      `<div class="chart-empty">${icon("activity")}等待两次有效计数采样后绘制曲线。<br><small>${state.source === "replay" ? "回放数据不会生成虚构速率。" : "零流量会如实显示；采集失败与计数重置显示为空缺。"}</small></div>`;
    return;
  }
  const w = 650,
    h = 155,
    left = 45,
    right = 18,
    top = 14,
    bottom = 30;
  const innerW = w - left - right,
    innerH = h - top - bottom;
  const maximum =
    Math.max(0.01, ...valid.flatMap((v) => [v.rxMbps, v.txMbps])) * 1.15;
  let svg = `<svg viewBox="0 0 ${w} ${h}" class="chart-svg" role="img" aria-label="代理到后端及后端到代理的计数采样均值，单位 Mbps"><text x="6" y="10" fill="#a3b0c0" font-size="8">Mbps</text>`;
  for (let i = 0; i < 4; i++) {
    const y = top + (innerH * i) / 3;
    svg += `<path d="M${left} ${y}H${w - right}" stroke="#edf2f7" stroke-dasharray="3 4"/><text x="${left - 9}" y="${y + 3}" fill="#a5b0c1" font-size="8" text-anchor="end">${fmt(maximum * (1 - i / 3), maximum < 1 ? 3 : 1)}</text>`;
  }
  const start = Date.parse(history[0].at),
    end = Date.parse(history.at(-1).at);
  const xOf = (v) =>
    left +
    (end > start ? (Date.parse(v.at) - start) / (end - start) : 1) * innerW;
  [
    ["rxMbps", "#52b1ac"],
    ["txMbps", "#9c8ac9"],
  ].forEach(([key, color]) => {
    let segments = [],
      segment = [];
    history.forEach((v) => {
      if (finite(v[key]))
        segment.push([xOf(v), top + innerH - (v[key] / maximum) * innerH]);
      else if (segment.length) {
        segments.push(segment);
        segment = [];
      }
    });
    if (segment.length) segments.push(segment);
    segments.forEach((points) => {
      const d = points
        .map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`)
        .join(" ");
      if (points.length > 1)
        svg += `<path d="${d}L${points.at(-1)[0]} ${top + innerH}L${points[0][0]} ${top + innerH}Z" fill="${color}" opacity=".045"/><path d="${d}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round"/>`;
      points.forEach(([x, y]) => {
        svg += `<circle cx="${x}" cy="${y}" r="2" fill="${color}"/>`;
      });
    });
  });
  [history[0], history[Math.floor(history.length / 2)], history.at(-1)]
    .filter((v, i, a) => a.findIndex((x) => x.at === v.at) === i)
    .forEach((v) => {
      svg += `<text x="${xOf(v)}" y="${h - 8}" text-anchor="middle" fill="#a5b0c1" font-size="8">${time(v.at)}</text>`;
    });
  svg += `<text x="${w - 18}" y="11" text-anchor="end" font-size="8" fill="#95a5b8">当前 ${fmt(valid.at(-1).rxMbps, 3)} / ${fmt(valid.at(-1).txMbps, 3)} Mbps</text></svg>`;
  $("#traffic-chart").innerHTML = svg;
}
function renderEvents() {
  $("#event-count").textContent = data.events.length;
  $("#recent-events").innerHTML = data.events.length
    ? data.events
        .slice(0, 3)
        .map(
          (e) =>
            `<div class="event-row"><span class="event-dot ${e.type === "Warning" ? "warning" : ""}"></span><div><div class="event-title">${esc(e.reason)} <span class="td-sub">${esc(e.namespace)}</span></div><div class="event-description" title="${esc(e.message)}">${esc(e.object)} · ${esc(e.message)}</div></div><span class="event-time">${relative(e.at)}</span></div>`,
        )
        .join("")
    : `<div class="empty-event">${icon("clock")}<div><strong>当前没有保留的事件</strong><p>事件会过期；无事件不等于从未发生异常。</p></div></div>`;
}

function resourceButton(id, name, sub = "") {
  return `<span class="td-name">${esc(name)}</span>${sub ? `<div class="td-sub">${esc(sub)}</div>` : ""}`;
}
function renderResources() {
  const spaces = [
    ...new Set(
      data.pods
        .map((p) => p.namespace)
        .concat(data.services.map((s) => s.namespace)),
    ),
  ].sort();
  const filter = $("#namespace-filter");
  filter.innerHTML =
    '<option value="">全部命名空间</option>' +
    spaces.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  filter.value = namespace;
  const rows = data[resourceMode] || [];
  const filtered = rows.filter(
    (r) =>
      (!namespace || r.namespace === namespace) &&
      (!search ||
        JSON.stringify([r.name, r.node, r.namespace, r.component])
          .toLowerCase()
          .includes(search.toLowerCase())),
  );
  $("#resource-summary").innerHTML = [
    ["Pods", data.pods.length],
    ["Services", data.services.length],
    ["工作负载", data.workloads.length],
    ["NetworkPolicies", data.policies.length],
    ["PVC", data.pvcs.length],
  ]
    .map(
      ([name, n]) =>
        `<span class="summary-chip"><strong>${n}</strong>${name}</span>`,
    )
    .join("");
  let headers, html;
  if (resourceMode === "pods") {
    headers = [
      "Pod / 功能",
      "命名空间",
      "节点",
      "就绪 / 重启",
      "CPU",
      "内存",
      "eth0 接收 / 发送",
    ];
    html = filtered
      .map(
        (p) =>
          `<tr data-detail="${esc(p.id)}" tabindex="0" role="button" aria-label="查看 ${esc(p.name)}"><td>${resourceButton(p.id, short(p.name, 44), p.component)}</td><td>${label(p.namespace)}</td><td>${esc(nodeFor(p.node)?.alias || "未分配")}<div class="td-sub">${esc(p.node || "—")}</div></td><td>${badge(p.ready)} <span class="td-sub">${p.readyContainers}/${p.containerCount} · ${p.restarts} 次</span></td><td class="mono" title="指标时间 ${esc(p.metricsAt)}">${cpu(p.cpuUsed)}</td><td class="mono" title="指标时间 ${esc(p.metricsAt)}">${bytes(p.memoryUsed)}</td><td class="mono">${fmt(p.rxMbps, 3)} / ${fmt(p.txMbps, 3)} Mbps</td></tr>`,
      )
      .join("");
  } else if (resourceMode === "services") {
    headers = [
      "Service",
      "命名空间",
      "类型 / ClusterIP",
      "端口映射",
      "Ready 端点",
    ];
    html = filtered
      .map(
        (s) =>
          `<tr data-detail="${esc(s.id)}" tabindex="0" role="button" aria-label="查看 ${esc(s.name)}"><td>${resourceButton(s.id, s.name)}</td><td>${label(s.namespace)}</td><td>${esc(s.type)}<div class="td-sub mono">${esc(s.ip)}</div></td><td>${s.ports.map((p) => `<span class="mono">${esc(p.nodePort ? p.nodePort + " → " : "")}${esc(p.port)} → ${esc(p.targetPort)} / ${esc(p.protocol)}</span>`).join("<br>")}</td><td>${s.endpoints.filter((e) => e.ready === true).length} / ${s.endpoints.length}</td></tr>`,
      )
      .join("");
  } else if (resourceMode === "workloads") {
    headers = ["工作负载", "命名空间", "类型", "Ready / 期望"];
    html = filtered
      .map(
        (w) =>
          `<tr data-detail="workload/${esc(w.namespace)}/${esc(w.name)}" tabindex="0" role="button"><td class="td-name">${esc(w.name)}</td><td>${label(w.namespace)}</td><td>${esc(w.kind)}</td><td>${label(`${w.ready} / ${w.desired}`, w.ready >= w.desired ? "good" : "bad")}</td></tr>`,
      )
      .join("");
  } else if (resourceMode === "policies") {
    headers = ["策略名称", "命名空间", "方向", "Pod selector", "说明"];
    html = filtered
      .map(
        (p) =>
          `<tr data-detail="policy/${esc(p.namespace)}/${esc(p.name)}" tabindex="0" role="button"><td class="td-name">${esc(p.name)}</td><td>${label(p.namespace)}</td><td>${esc((p.spec.policyTypes || []).join(" / "))}</td><td class="mono">${esc(JSON.stringify(p.spec.podSelector))}</td><td>点击查看规则；对象存在不证明实际拦截</td></tr>`,
      )
      .join("");
  } else {
    headers = ["存储卷", "命名空间", "状态", "容量", "StorageClass"];
    html = filtered
      .map(
        (p) =>
          `<tr data-detail="pvc/${esc(p.namespace)}/${esc(p.name)}" tabindex="0" role="button"><td class="td-name">${esc(p.name)}</td><td>${label(p.namespace)}</td><td>${label(p.phase, p.phase === "Bound" ? "good" : "bad")}</td><td class="mono">${esc(p.capacity?.storage)}</td><td>${esc(p.spec.storageClassName)}</td></tr>`,
      )
      .join("");
  }
  $("#resource-table").innerHTML =
    `<table><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${html || `<tr><td colspan="${headers.length}" class="resource-empty">没有匹配的资源，请调整筛选条件。</td></tr>`}</tbody></table>`;
  $("#resource-footer").innerHTML =
    `<span>显示 ${filtered.length} / ${rows.length} 项 · ${time(data.observedAt)} 观测</span><a class="download-link" href="/api/export" download>导出当前观测 JSON ${icon("download")}</a>`;
}

function renderGuide() {
  $("#guide-flow").innerHTML = [
    ["phone", "UE", "业务发起"],
    ["radio", "gNB", "模拟接入"],
    ["route", "UPF", "用户面转发"],
    ["network", "Envoy", "端口分类与代理"],
    ["cloud", "Core / Edge", "业务服务"],
  ]
    .map(
      ([i, t, d], n) =>
        `${n ? `<span class="guide-arrow">${icon("arrow")}</span>` : ""}<div class="guide-step"><span>${icon(i)}</span><strong>${t}</strong><small>${d}</small></div>`,
    )
    .join("");
  const cards = [
    [
      "server",
      "三台节点，各司其职",
      "VM1 运行控制面与中心服务；VM2 运行 UPF；VM3 运行模拟基站、UE 和边缘服务。实际角色由标签识别，Pod 位置来自现场。",
      [
        "点击节点查看操作系统、内核、k3s 版本与标签",
        "节点资源比例以 Kubernetes allocatable 为分母",
      ],
    ],
    [
      "network",
      "三类连线，三种证据",
      "橙色前段动画由独立 UE 隧道、UPF IP 接口和代理入口计数关联，不是逐跳抓包；绿色后段来自代理 cluster 计数差分。点击连线可查看采样窗口与范围。",
      [
        "eth0 用量包括信令、探测等非业务流量",
        "动画只在新鲜采样且相应代理 cluster 速率大于零时出现",
      ],
    ],
    [
      "eye",
      "只读、定时、可追溯",
      "页面经本地服务，通过 SSH 在 VM1 执行固定读取指令。所有数据保留观测时间，断开连接时保留旧数据并标明过期。",
      [
        "默认每 15 秒采集，手动刷新可立即请求一轮",
        "健康检查不重启 Pod、不修改策略、不发起压测",
      ],
    ],
    [
      "shield",
      "控制面和用户面",
      "AMF/AUSF/UDM 等参与注册与鉴权；SMF 建立会话并配置 UPF；UPF 承载用户数据，Envoy 处理后续业务代理。",
      ["Pod Ready 不等于核心网协议流程完成", "控制面参考关系未执行逐流观测"],
    ],
    [
      "route",
      "中心、边缘与带宽",
      "Core 与 Edge 是不同放置的实验业务后端，历史 tc 实验验证了受控带宽差异。当前页面不读取或修改队列配置。",
      ["位置不同不直接证明时延更低", "容器 tc 带宽控制不等于原生 5G QoS"],
    ],
    [
      "book",
      "如何阅读这个页面",
      "先看总览中节点和健康检查，再在拓扑点击功能卡片；资源清单可查找所有 Pod、服务、工作负载、策略与存储。",
      [
        "详情显示指标独立采样时间和证据范围",
        "导出 JSON 可保存当前页面所依据的观测",
      ],
    ],
  ];
  $("#guide-cards").innerHTML = cards
    .map(
      ([i, t, p, li]) =>
        `<article class="guide-card">${icon(i)}<h3>${t}</h3><p>${p}</p><ul>${li.map((v) => `<li>${v}</li>`).join("")}</ul></article>`,
    )
    .join("");
}

function dl(rows) {
  return `<dl class="detail-list">${rows.map(([k, v, raw]) => `<div><dt>${esc(k)}</dt><dd>${raw ? v : esc(v)}</dd></div>`).join("")}</dl>`;
}
function detailLink(id, title, sub = "") {
  return `<button class="drawer-list-button" data-detail="${esc(id)}"><span>${esc(title)}${sub ? `<span class="td-sub"> · ${esc(sub)}</span>` : ""}</span>${icon("arrow-up-right")}</button>`;
}
function drawDetail(id, isUpdate = false) {
  if (!data) return;
  let html = "";
  const p = podBy(id),
    n = data.nodes.find((x) => x.id === id),
    s = serviceBy(id);
  if (p) {
    const node = nodeFor(p.node);
    const bound = data.services.filter((s) =>
      s.endpoints.some((e) => e.target === p.id),
    );
    html = `<div class="drawer-title"><span class="node-symbol">${icon(modeIcon(p))}</span><div><h2>${esc(p.component)} ${badge(p.ready)}</h2><p class="mono">${esc(p.name)}</p></div></div><p class="drawer-description">${esc(p.description)}</p><h3>运行信息</h3>${dl(
      [
        ["命名空间", p.namespace],
        ["Pod IP", p.ip],
        ["所在节点", node?.alias + " / " + (p.node || "未分配")],
        [
          "状态",
          `${p.phase} · ${p.readyContainers}/${p.containerCount} 容器 Ready`,
        ],
        ["累计重启", `${p.restarts} 次（当前 Pod 身份范围）`],
        ["创建时间", p.createdAt],
        ["Pod UID", p.uid],
      ],
    )}<h3>资源与网络观测</h3>${dl([
      ["CPU 使用", cpu(p.cpuUsed)],
      ["内存使用", bytes(p.memoryUsed)],
      ["指标采样", p.metricsAt],
      ["eth0 接收", `${fmt(p.rxMbps, 4)} Mbps`],
      ["eth0 发送", `${fmt(p.txMbps, 4)} Mbps`],
      [
        "累计字节",
        `${p.telemetry.rxBytes ?? "—"} RX / ${p.telemetry.txBytes ?? "—"} TX`,
      ],
      ["计数采样", p.telemetry.at],
      ["网络证据", "接口总量包含信令和探测；不是逐流业务吞吐。"],
    ])}`;
    if (p.interfaceTraffic?.length)
      html += `<h3>用户面独立接口计数</h3>${p.interfaceTraffic.map((i) => dl([
        ["接口", `${i.name} / ifindex ${i.index}`],
        ["接收 RX", `${fmt(i.rxMbps, 4)} Mbps / ${i.rxBytes} bytes`],
        ["发送 TX", `${fmt(i.txMbps, 4)} Mbps / ${i.txBytes} bytes`],
        ["采样窗口", `${i.windowStart || "等待第二次采样"} → ${i.at}`],
        ["来源", i.source],
        ["范围", p.component === "UPF" ? "全部 UE 的 IP 接口总量；RX 为上行，TX 为下行。" : "该 UE 的全部隧道内 IP 业务；TX 为上行，RX 为下行。"],
      ])).join("")}`;
    if (p.telemetry.interfaces)
      html += `<h3>UE 隧道接口</h3>${p.telemetry.interfaces
        .map((i) =>
          dl([
            ["接口", i.name],
            ["地址", i.addresses.join(", ")],
            ["operstate", i.state],
            [
              "观测范围",
              "接口存在；不能单独证明实时注册、会话有效或端到端可达。",
            ],
          ]),
        )
        .join("")}`;
    if (p.telemetry.error || p.telemetry.envoyError || p.telemetry.tunnelError)
      html += `<h3>观测缺失</h3><div class="drawer-code">${esc(p.telemetry.error || p.telemetry.envoyError || p.telemetry.tunnelError)}</div>`;
    html += `<h3>关联 Service · ${bound.length}</h3>${bound.map((v) => detailLink(v.id, v.name, `${v.endpoints.filter((e) => e.ready).length} Ready 端点`)).join("") || '<p class="freshness-note">没有发现绑定至此 Pod 的 Service。</p>'}`;
    const outgoing = data.routes.filter((r) => r.source === p.id);
    if (outgoing.length)
      html += `<h3>代理转发路径</h3>${outgoing.map((r) => `<button class="drawer-list-button" data-route="${data.routes.indexOf(r)}"><span>:${r.port} → ${esc(serviceBy(r.target)?.name || r.target)}</span>${icon("arrow-up-right")}</button>`).join("")}`;
    html += `<h3>容器 · ${p.containers.length}</h3>${p.containers.map((c) => `<div class="drawer-code">${esc(c.name)}\n${esc(c.image)}\nrequests: ${esc(JSON.stringify(c.resources.requests || {}))}\nlimits: ${esc(JSON.stringify(c.resources.limits || {}))}\nstate: ${esc(JSON.stringify(c.status.state || {}))}</div>`).join("")}<h3>标签</h3>${Object.entries(
      p.labels,
    )
      .map(([k, v]) => `<span class="drawer-chip">${esc(k)}=${esc(v)}</span>`)
      .join("")}`;
  } else if (n) {
    const pods = data.pods.filter((p) => p.node === n.name);
    html = `<div class="drawer-title"><span class="node-symbol">${icon("server")}</span><div><h2>${esc(n.alias)} ${badge(n.ready && !n.pressure.length)}</h2><p class="mono">${esc(n.name)}</p></div></div><p class="drawer-description">${esc(n.description)}</p><h3>主机与角色</h3>${dl(
      [
        ["角色", n.role],
        ["Internal IP", n.ip],
        ["Pod CIDR", n.podCIDR],
        ["可调度", n.schedulable ? "是" : "否 / 已 cordon"],
        ["操作系统", n.info?.osImage],
        ["内核", n.info?.kernelVersion],
        ["架构", n.info?.architecture],
        ["容器运行时", n.info?.containerRuntimeVersion],
        ["Kubernetes", n.info?.kubeletVersion],
      ],
    )}<h3>可分配资源与当前用量</h3>${dl([
      ["CPU", `${fmt(n.cpuUsed, 3)} / ${fmt(n.cpuCores, 0)} cores`],
      ["内存", `${bytes(n.memoryUsed)} / ${bytes(n.memoryBytes)}`],
      ["指标采样", n.metricsAt],
    ])}<h3>节点 Conditions</h3>${dl(n.conditions.map((c) => [c.type, `${c.status} · ${c.reason || ""}`]))}<h3>节点标签</h3>${Object.entries(
      n.labels,
    )
      .map(([k, v]) => `<span class="drawer-chip">${esc(k)}=${esc(v)}</span>`)
      .join(
        "",
      )}<h3>承载 Pod · ${pods.length}</h3>${pods.map((p) => detailLink(p.id, p.component, p.name)).join("")}`;
  } else if (s) {
    html = `<div class="drawer-title"><span class="node-symbol">${icon("network")}</span><div><h2>Service</h2><p class="mono">${esc(s.name)}</p></div></div><p class="drawer-description">Service 为一组端点提供稳定入口。端点来自当前 EndpointSlice，绑定不等于实际 TCP 连接。</p>${dl(
      [
        ["命名空间", s.namespace],
        ["类型", s.type],
        ["Cluster IP", s.ip],
        ["selector", JSON.stringify(s.selector)],
      ],
    )}<h3>端口</h3>${s.ports
      .map((p) =>
        dl([
          ["名称", p.name],
          ["协议", p.protocol],
          ["Service 端口", p.port],
          ["targetPort", p.targetPort],
          ["NodePort", p.nodePort],
        ]),
      )
      .join("")}<h3>端点 · ${s.endpoints.length}</h3>${
      s.endpoints
        .map(
          (e) =>
            `${e.target ? detailLink(e.target, podBy(e.target)?.name || e.target) : `<div class="drawer-code">${esc(e.addresses.join(", "))}</div>`}${dl(
              [
                ["地址", e.addresses.join(", ")],
                [
                  "Ready",
                  e.ready === true
                    ? "True"
                    : e.ready === false
                      ? "False"
                      : "未知",
                ],
              ],
            )}`,
        )
        .join("") ||
      '<p class="freshness-note">未发现端点。无 selector 的外部 Service 可能不绑定 Pod。</p>'
    }`;
  } else if (id.startsWith("traffic/")) {
    const link = data.trafficLinks?.find((l) => l.id === decodeURIComponent(id.slice(8)));
    if (!link) { missingDetail(id); return; }
    const status = {active: "同向端点活动", idle: "端点空闲", unmatched: "活动未对应", unknown: "等待有效观测"}[link.status];
    html = `<div class="drawer-title"><span class="node-symbol">${icon("route")}</span><div><h2>端点活动关联</h2><p>${esc(podBy(link.source)?.component)} → ${esc(podBy(link.target)?.component)}</p></div></div><p class="drawer-description">${esc(link.evidence)}</p>${dl([
      ["状态", status], ["上行关联", link.forwardActive ? "有" : "无 / 未知"],
      ["下行关联", link.reverseActive ? "有" : "无 / 未知"], ["关联范围", link.scope],
    ])}<h3>独立观测依据</h3>${link.observations.map((o) => dl([
      ["端点", o.label], ["上行", `${fmt(o.uplinkMbps, 4)} Mbps`],
      ["下行", `${fmt(o.downlinkMbps, 4)} Mbps`],
      ["采样窗口", `${o.windowStart || "等待第二次采样"} → ${o.at}`], ["范围", o.scope],
    ])).join("")}<h3>关联资源</h3>${detailLink(link.source, podBy(link.source)?.name || link.source)}${detailLink(link.target, podBy(link.target)?.name || link.target)}`;
  } else if (id.startsWith("route/")) {
    const r = data.routes.find((r) => r.id === decodeURIComponent(id.slice(6)));
    if (!r) {
      missingDetail(id);
      return;
    }
    const svc = serviceBy(r.target);
    html = `<div class="drawer-title"><span class="node-symbol">${icon("route")}</span><div><h2>监听 :${esc(r.port)}</h2><p>${esc(r.namespace)} / ${esc(r.listener)}</p></div></div><p class="drawer-description">当前代理 Pod 挂载配置与 Kubernetes 端点共同形成这条转发关系。速率来自 Envoy upstream cluster 计数器差分。</p><h3>配置证据</h3>${dl(
      [
        ["ConfigMap", r.configMap],
        ["cluster", r.cluster],
        ["目的地址", r.targets.map((t) => `${t.host}:${t.port}`).join(", ")],
        ["Service 解析", r.resolved ? "已解析" : "未解析"],
      ],
    )}<h3>连接与流量</h3>${dl([
      ["活动连接", r.activeConnections],
      ["累计连接", r.totalConnections],
      ["后端 → 代理", `${fmt(r.rxMbps, 4)} Mbps`],
      ["代理 → 后端", `${fmt(r.txMbps, 4)} Mbps`],
      ["统计范围", "该 upstream cluster；缺失、重置与首轮采样不记为 0。"],
    ])}<h3>关联资源</h3>${detailLink(r.source, podBy(r.source)?.name || r.source)}${svc ? detailLink(svc.id, svc.name) : ""}`;
  } else {
    const [kind, ns, ...rest] = id.split("/");
    const name = rest.join("/");
    const collection = {
      policy: "policies",
      pvc: "pvcs",
      workload: "workloads",
    }[kind];
    const r = data[collection]?.find(
      (r) => r.namespace === ns && r.name === name,
    );
    if (!r) {
      missingDetail(id);
      return;
    }
    html = `<div class="drawer-title"><span class="node-symbol">${icon(kind === "pvc" ? "database" : kind === "policy" ? "shield" : "box")}</span><div><h2>${esc(kind === "policy" ? "NetworkPolicy" : kind === "pvc" ? "PVC" : r.kind)}</h2><p class="mono">${esc(name)}</p></div></div><p class="drawer-description">${kind === "policy" ? "策略对象及规则可见；实际允许/拒绝效果需要单独连通性验证。" : kind === "pvc" ? "存储卷绑定状态来自 Kubernetes；未执行读写或持久性测试。" : "期望与就绪副本来自 Kubernetes 控制器；不证明应用业务协议正常。"}</p><h3>资源字段</h3><pre class="drawer-code">${esc(JSON.stringify(r, null, 2))}</pre>`;
  }
  html += `<div class="drawer-divider"></div><p class="freshness-note">观测 ${time(data.observedAt)} · ${relative(data.observedAt)}<br>${state.source === "replay" ? "归档回放；不代表当前集群" : sourceLive() ? "来自真实集群的只读观测" : "观测已过期或数据源不可达；请刷新确认"}</p><a class="button subtle drawer-export" href="/api/export" download>${icon("download")}导出本轮观测</a>`;
  const scroll = $("#detail-drawer").scrollTop;
  const focused = document.activeElement;
  const focusedDetail = focused?.dataset?.detail;
  const focusedRoute = focused?.dataset?.route;
  $("#drawer-content").innerHTML = html;
  lastDrawer = id;
  if (!isUpdate) {
    drawerFocus = document.activeElement;
    $("#drawer-backdrop").classList.remove("hidden");
    $("#detail-drawer").hidden = false;
    document.body.style.overflow = "hidden";
    $("#detail-drawer").focus();
    $("#detail-drawer").scrollTop = 0;
  } else {
    if (focusedDetail || focusedRoute) {
      const target = $$("[data-detail],[data-route]", $("#detail-drawer")).find(
        (e) =>
          focusedDetail
            ? e.dataset.detail === focusedDetail
            : e.dataset.route === focusedRoute,
      );
      (target || $("#detail-drawer")).focus({ preventScroll: true });
    }
    $("#detail-drawer").scrollTop = scroll;
  }
}
function missingDetail(id) {
  if (!$("#detail-drawer").hidden) {
    $("#drawer-content").innerHTML =
      `<h2>资源已不在当前观测中</h2><p class="drawer-description">${esc(id)}<br>原资源可能已删除、替换或暂时不可见，请返回资源清单确认。</p><p class="freshness-note">当前观测 ${time(data.observedAt)}</p>`;
  } else toast("该资源已不在当前观测中，请刷新列表。");
}
function closeDrawer() {
  lastDrawer = null;
  $("#detail-drawer").hidden = true;
  $("#drawer-backdrop").classList.add("hidden");
  document.body.style.overflow = "";
  if (drawerFocus?.isConnected) drawerFocus.focus();
}

function renderCurrent() {
  if (!data) return;
  $("#nav-pods").textContent = data.pods.length;
  if (view === "overview") {
    renderStats();
    renderNodes();
    renderHealth();
    renderTopology();
    renderChart();
    renderEvents();
  } else if (view === "topology") renderTopology();
  else if (view === "resources") renderResources();
  else if (view === "health") renderHealth();
  else if (view === "traffic") window.renderTraffic?.();
  else renderGuide();
}
function renderFreshness() {
  const age = ageSeconds(data?.observedAt),
    replay = state?.source === "replay";
  const disconnected = !!(state?.error || pollFailure);
  const stale =
    age !== null && age > Math.max(60, (state?.intervalSeconds || 15) * 2 + 10);
  let txt = replay
    ? "归档回放"
    : disconnected
      ? "数据源断开"
      : !data
        ? "正在连接"
        : stale
          ? "观测已过期"
          : state?.busy
            ? "正在采集"
            : Object.keys(data.errors).length
              ? "部分观测缺失"
              : "集群已连接";
  $("#connection").innerHTML =
    `<span class="dot ${disconnected ? "danger" : replay || stale || !data ? "amber" : "mint"}"></span>${txt}`;
  $("#observed-time").textContent = data
    ? `${time(data.observedAt)} 更新 · ${relative(data.observedAt)}`
    : "等待首轮真实观测";
  $("#time-dot").className =
    `dot ${disconnected || stale || replay ? "amber" : "mint"}`;
  const banner = $("#status-banner");
  let warning = "";
  if (disconnected)
    warning = `数据采集未成功：${pollFailure || state.error}。${data ? "当前保留上一次观测，请勿将其当作实时状态。" : "尚无可展示的真实集群数据。"}`;
  else if (replay)
    warning = `当前是归档回放，原始观测时间 ${data?.observedAt || "未知"}；页面不会模拟实时流量。`;
  else if (stale)
    warning = `本轮观测已有 ${Math.floor(age)} 秒未更新。页面暂停流量动画，保留上次数据供参考。`;
  else if (data && Object.keys(data.errors).length)
    warning = `部分数据源不可用：${Object.keys(data.errors).join("、")}。缺失值显示为“—”，请在健康检查中确认。`;
  banner.textContent = warning;
  banner.classList.toggle("hidden", !warning);
  banner.classList.toggle("error", disconnected);
  $("#footer-source").textContent = state
    ? `${replay ? "回放" : state.source === "local" ? "本机 kubectl" : "SSH → " + state.host} · ${state.intervalSeconds}s 采集 · ${data ? fmt(data.durationSeconds, 2) + "s 耗时" : "采集中"}`
    : "数据源连接中";
  $$(".flow-observed").forEach(
    (e) => (e.style.animationPlayState = sourceLive() ? "running" : "paused"),
  );
  ["#refresh-button", "#health-refresh", "#run-checks"].forEach((s) => {
    const b = $(s);
    b.disabled = !!state?.busy || !!requestedRefresh;
    b.classList.toggle("spin", !!state?.busy || !!requestedRefresh);
  });
}
async function poll() {
  try {
    const response = await fetch("/api/state", {
      cache: "no-store",
      signal: AbortSignal.timeout(7000),
    });
    if (!response.ok) throw Error(`HTTP ${response.status}`);
    const next = await response.json();
    pollFailure = null;
    if(state && state.serverStartedAt!==next.serverStartedAt && requestedRefresh){
      requestedRefresh=false;
      toast('页面服务已重启，请重新确认集群观测。');
    }
    state = next;
    if (next.data) {
      data = next.data;
      if (lastObservation !== data.observedAt) {
        lastObservation = data.observedAt;
        renderCurrent();
        if (lastDrawer) drawDetail(lastDrawer, true);
      } else if (previousBusy && !next.busy && view === "health")
        renderHealth();
    }
    if (requestedRefresh && next.completedAttempt >= requestedRefresh.attempt) {
      toast(next.error ? "采集失败，请查看提示。" : "本轮只读观测已更新。");
      requestedRefresh = false;
    }
    previousBusy = next.busy;
  } catch (error) {
    pollFailure = `页面服务不可达（${error.message}）`;
  }
  renderFreshness();
}
async function refresh() {
  try {
    const r = await fetch("/api/refresh", {
      method: "POST",
      signal: AbortSignal.timeout(7000),
    });
    if (!r.ok) throw Error(`HTTP ${r.status}`);
    requestedRefresh = await r.json();
    toast("已请求一轮真实观测与只读健康检查。");
    await poll();
  } catch (e) {
    toast(`刷新失败：${e.message}`);
  }
}

document.addEventListener("click", (event) => {
  const traffic = event.target.closest("[data-traffic-link]");
  if (traffic) {
    drawDetail(`traffic/${encodeURIComponent(traffic.dataset.trafficLink)}`);
    return;
  }
  const detail = event.target.closest("[data-detail]");
  if (detail) {
    drawDetail(detail.dataset.detail);
    return;
  }
  const route = event.target.closest("[data-route]");
  if (route) {
    const r = data.routes[Number(route.dataset.route)];
    if (r) drawDetail(`route/${encodeURIComponent(r.id)}`);
    return;
  }
  const nav = event.target.closest("[data-view],[data-go]");
  if (nav) switchView(nav.dataset.view || nav.dataset.go);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#detail-drawer").hidden) {
    closeDrawer();
    return;
  }
  if (
    (event.key === "Enter" || event.key === " ") &&
    event.target.matches("[data-detail],[data-route],[data-traffic-link]") &&
    event.target.tagName !== "BUTTON"
  ) {
    event.preventDefault();
    event.target.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  }
  if (event.key === "Tab" && !$("#detail-drawer").hidden) {
    const all = $$('button,a[href],[tabindex="0"]', $("#detail-drawer"));
    const first = all[0],
      last = all.at(-1);
    if (
      event.shiftKey &&
      (document.activeElement === first ||
        document.activeElement === $("#detail-drawer"))
    ) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }
});
$("#close-drawer").addEventListener("click", closeDrawer);
$("#drawer-backdrop").addEventListener("click", closeDrawer);
$("#refresh-button").addEventListener("click", refresh);
$("#health-refresh").addEventListener("click", refresh);
$("#run-checks").addEventListener("click", () => {
  switchView("health");
  refresh();
});
$("#expand-topology").addEventListener("click", () => switchView("topology"));
$("#help-button").addEventListener("click", () => switchView("guide"));
$$("[data-topology]").forEach((b) =>
  b.addEventListener("click", () => {
    topologyMode = b.dataset.topology;
    $$("[data-topology]").forEach((x) => x.classList.toggle("active", x === b));
    if (data) renderTopology();
  }),
);
$("#path-space").addEventListener("change", (e) => {
  pathSpace = e.target.value;
  if (data) renderTopology();
});
$("#topology-fit").addEventListener("click", () => {
  zoomLevel = 1;
  applyZoom();
  const map = $("#full-topology");
  map.scrollTo({ top: 0, left: 0, behavior: "smooth" });
  toast("已重置缩放并回到画布起点。");
});
$("#zoom-in").addEventListener("click", () => {
  zoomLevel = Math.min(2, Math.round((zoomLevel + 0.2) * 10) / 10);
  applyZoom();
});
$("#zoom-out").addEventListener("click", () => {
  zoomLevel = Math.max(0.6, Math.round((zoomLevel - 0.2) * 10) / 10);
  applyZoom();
});
$("#topology-namespace").addEventListener("change", (e) => {
  topologyNamespace = e.target.value;
  if (data) renderTopology();
});
$("#full-topology").addEventListener("pointerover", (e) => {
  if (topologyMode !== "services") return;
  const target = e.target.closest("[data-detail]");
  if (!target) return;
  $$(".binding-edge").forEach((edge) => {
    const match =
      edge.dataset.from === target.dataset.detail ||
      edge.dataset.to === target.dataset.detail;
    edge.style.opacity = match ? "1" : ".1";
    edge.style.strokeWidth = match ? "2.5" : "1.4";
  });
});
$("#full-topology").addEventListener("pointerout", (e) => {
  if (
    topologyMode === "services" &&
    !e.relatedTarget?.closest?.("[data-detail]")
  )
    $$(".binding-edge").forEach((edge) => {
      edge.style.opacity = ".5";
      edge.style.strokeWidth = "1.4";
    });
});
$$("[data-resource]").forEach((b) =>
  b.addEventListener("click", () => {
    resourceMode = b.dataset.resource;
    $$("[data-resource]").forEach((x) => x.classList.toggle("active", x === b));
    if (data) renderResources();
  }),
);
$("#namespace-filter").addEventListener("change", (e) => {
  namespace = e.target.value;
  renderResources();
});
$("#resource-search").addEventListener("input", (e) => {
  search = e.target.value;
  renderResources();
});
hydrate();
renderGuide();
switchView(location.hash.slice(1) || "overview");
poll();
setInterval(poll, 2500);
setInterval(renderFreshness, 1000);

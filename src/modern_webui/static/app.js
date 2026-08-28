const NAVIGATION = [
  { id: "run", label: "运行", pages: [
    ["daily_research", "🚀 每日研究"],
    ["past_daily", "🗓 过去日报"],
    ["trend_tasks", "📈 趋势任务"],
  ] },
  { id: "content", label: "内容", pages: [
    ["reports", "📚 报告查看"],
    ["favorites", "⭐ 收藏"],
    ["paper_search", "🔍 论文检索"],
  ] },
  { id: "configuration", label: "配置", pages: [
    ["keywords", "🏷️ 关键词"],
    ["data_sources", "🧭 数据源"],
    ["scoring", "🧮 评分策略"],
    ["api", "🔑 API"],
    ["notifications", "🔔 通知"],
    ["advanced", "⚙️ 高级设置"],
    ["accounts", "👤 账户"],
  ] },
  { id: "system", label: "系统", pages: [
    ["backup_sync", "☁️ 备份与同步"],
    ["history_tasks", "📜 历史维护"],
    ["diagnostics", "🩺 诊断"],
    ["analytics", "📊 数据分析"],
    ["logs", "🧾 日志"],
  ] },
];

const PAGE_META = {
  daily_research: ["运行 / 每日研究", "每日研究", "扫描、筛选、分析与报告生成。任务仍由现有 Worker 队列安全执行。"],
  past_daily: ["运行 / 过去日报", "过去日报", "按自然日将指定时间段加入队列，逐日生成与当日一致的报告。"],
  trend_tasks: ["运行 / 趋势任务", "趋势任务", "对指定关键词和时间段执行独立趋势研究。"],
  reports: ["内容 / 报告查看", "报告查看", "浏览日报、趋势研究和关键词趋势报告。"],
  favorites: ["内容 / 收藏", "收藏", "查看已标记论文、作者偏好和关键词偏好。"],
  paper_search: ["内容 / 论文检索", "论文检索", "从 SQLite 多维论文历史库检索已处理论文及其来源变体。"],
  keywords: ["配置 / 关键词", "关键词", "设置研究背景、主关键词和参考文献关键词提取。"],
  data_sources: ["配置 / 数据源", "数据源", "管理内置 arXiv 与额外数据来源。"],
  scoring: ["配置 / 评分策略", "评分策略", "控制论文资格判定、排序与作者偏好。"],
  api: ["配置 / API", "API 配置", "配置 LLM、PDF 解析和第三方数据服务。"],
  notifications: ["配置 / 通知", "通知", "配置任务完成和阶段级异常通知。"],
  advanced: ["配置 / 高级设置", "高级设置", "调整解析、并发、重试、代理和持久化行为。"],
  accounts: ["配置 / 账户", "账户管理", "管理本地面板账户与密码。"],
  backup_sync: ["系统 / 备份与同步", "备份与同步", "导出配置、配置 WebDAV 和管理 SQLite 本地备份。"],
  history_tasks: ["系统 / 历史维护", "历史维护", "导入旧版本历史、补全缺失字段并扫描遗漏论文。"],
  diagnostics: ["系统 / 诊断", "运行诊断", "查看正常每日研究、过去日报，以及所有任务的 LLM 与数据源健康记录。"],
  analytics: ["系统 / 数据分析", "数据分析", "查看已记录的 LLM Token 使用情况。"],
  logs: ["系统 / 日志", "运行日志", "按任务分组读取最近的本地运行日志。"],
};

const FALLBACK_ARXIV_CATEGORIES = [
  "quant-ph", "hep-th", "hep-ex", "hep-lat", "gr-qc", "astro-ph", "cond-mat", "physics", "math-ph", "cs.AI", "cs.LG", "stat.ML",
];
const BUILTIN_SOURCES = [
  ["prl", "PRL"], ["pra", "PRA"], ["prb", "PRB"], ["prc", "PRC"], ["prd", "PRD"], ["pre", "PRE"], ["prx", "PRX"], ["prxq", "PRX Quantum"], ["rmp", "RMP"],
  ["nature", "Nature"], ["nature_physics", "Nature Physics"], ["nature_communications", "Nature Communications"], ["science", "Science"], ["science_advances", "Science Advances"], ["npj_quantum_information", "npj Quantum Information"], ["quantum", "Quantum"], ["new_journal_of_physics", "New Journal of Physics"], ["huggingface_papers", "Hugging Face Papers"],
];

const state = {
  auth: null,
  settings: null,
  draft: { config: {}, env: {}, clearEnv: new Set() },
  group: "run",
  page: "daily_research",
  tables: {},
  timers: new Map(),
  pageData: {},
  renderToken: 0,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[character]));
}

function escapeAttribute(value) { return escapeHtml(value); }

function safeExternalUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch (_) {
    return null;
  }
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").slice(0, 19);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(date);
}

function formatNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat("zh-CN").format(number) : "—";
}

function formatPercent(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : "—";
}

function pageGroup(page) {
  return NAVIGATION.find((group) => group.pages.some(([id]) => id === page));
}

function configValue(key, fallback = "") {
  return state.draft.config[key] ?? state.settings?.config?.[key] ?? fallback;
}

function envValue(key, fallback = "") {
  return state.draft.env[key] ?? state.settings?.env?.[key] ?? fallback;
}

function secretConfigured(key) {
  return Boolean(state.settings?.secrets?.[key]);
}

function arxivCategories() {
  const supplied = state.settings?.arxiv_categories;
  if (Array.isArray(supplied) && supplied.length) {
    return supplied
      .filter((item) => item && typeof item.code === "string")
      .map((item) => ({ code: item.code, label: String(item.label || item.code) }));
  }
  // The fallback keeps an already-loaded offline page usable while the
  // settings request is still pending.  Normal deployments receive the full
  // shared catalog from the backend above.
  return FALLBACK_ARXIV_CATEGORIES.map((code) => ({ code, label: code }));
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && typeof options.body !== "string" && !(options.body instanceof Blob)) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, { credentials: "same-origin", ...options, headers });
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json().catch(() => ({})) : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" ? payload.detail : payload;
    throw new Error(detail || "请求未完成。 ");
  }
  return payload;
}

function toast(text, type = "success") {
  const node = $("#toast");
  node.textContent = text;
  node.className = `toast ${type}`;
  node.hidden = false;
  window.clearTimeout(node._timer);
  node._timer = window.setTimeout(() => { node.hidden = true; }, 4200);
}

function clearTimers() {
  for (const timer of state.timers.values()) window.clearTimeout(timer);
  state.timers.clear();
}

function scheduleRefresh(key, callback, milliseconds = 5000) {
  window.clearTimeout(state.timers.get(key));
  state.timers.set(key, window.setTimeout(callback, milliseconds));
}

function setLocation() {
  const target = `${state.group}/${state.page}`;
  if (window.location.hash.slice(1) !== target) window.history.replaceState(null, "", `#${target}`);
}

function readLocation() {
  const [group, page] = window.location.hash.slice(1).split("/");
  const candidate = NAVIGATION.find((item) => item.id === group && item.pages.some(([id]) => id === page));
  if (candidate) {
    state.group = group;
    state.page = page;
  }
}

function renderNavigation() {
  const navigation = $("#group-navigation");
  navigation.innerHTML = NAVIGATION.map((group) => `
    <button class="group-button ${group.id === state.group ? "is-active" : ""}" data-group="${group.id}">
      ${escapeHtml(group.label)}
    </button>
  `).join("");
  const current = pageGroup(state.page) || NAVIGATION[0];
  $("#top-tabs").innerHTML = current.pages.map(([id, label]) => `
    <button class="top-tab ${id === state.page ? "is-active" : ""}" data-page="${id}" role="tab" aria-selected="${id === state.page}">${escapeHtml(label)}</button>
  `).join("");
  $$("[data-group]", navigation).forEach((button) => button.addEventListener("click", () => {
    const group = NAVIGATION.find((item) => item.id === button.dataset.group);
    state.group = group.id;
    state.page = group.pages[0][0];
    renderNavigation();
    renderPage();
  }));
  $$("[data-page]", $("#top-tabs")).forEach((button) => button.addEventListener("click", () => {
    state.page = button.dataset.page;
    state.group = pageGroup(state.page).id;
    renderNavigation();
    renderPage();
  }));
}

function pageHeader() {
  const [breadcrumb, title, subtitle] = PAGE_META[state.page] || ["", "", ""];
  return `<header class="page-header"><p class="eyebrow">${escapeHtml(breadcrumb)}</p><h1>${escapeHtml(title)}</h1><p class="subtitle">${escapeHtml(subtitle)}</p></header>`;
}

function section(title, body, options = {}) {
  const icon = options.icon ? `${escapeHtml(options.icon)} ` : "";
  return `<section class="section-card ${options.className || ""}"><div class="section-heading"><h2>${icon}${escapeHtml(title)}</h2>${options.hint ? `<p>${escapeHtml(options.hint)}</p>` : ""}</div>${body}</section>`;
}

function divider() { return '<div class="section-divider"></div>'; }

function field(options) {
  const {
    label, key, type = "text", scope = "config", fallback = "", help = "", min, max, step, options: choices = [], placeholder = "", rows = 4, redraw = false, required = false,
  } = options;
  const value = scope === "env" ? envValue(key, fallback) : configValue(key, fallback);
  const data = `data-field="${escapeAttribute(key)}" data-scope="${scope}"${redraw ? ' data-redraw="1"' : ""}`;
  const hint = help ? `<span class="field-help">${escapeHtml(help)}</span>` : "";
  if (type === "checkbox") {
    return `<label class="toggle-field"><span>${escapeHtml(label)}${hint}</span><input type="checkbox" ${data} ${value ? "checked" : ""}/><i></i></label>`;
  }
  if (type === "textarea" || type === "lines") {
    const transform = type === "lines" ? ' data-transform="lines"' : "";
    const text = Array.isArray(value) ? value.join("\n") : String(value ?? "");
    return `<label class="form-field"><span>${escapeHtml(label)}${hint}</span><textarea ${data}${transform} rows="${rows}" placeholder="${escapeAttribute(placeholder)}">${escapeHtml(text)}</textarea></label>`;
  }
  if (type === "select" || type === "multiselect") {
    const multiple = type === "multiselect" ? " multiple" : "";
    const current = Array.isArray(value) ? value.map(String) : [String(value ?? "")];
    return `<label class="form-field"><span>${escapeHtml(label)}${hint}</span><select ${data}${multiple}>${choices.map((choice) => {
      const item = typeof choice === "object" ? choice : { value: choice, label: choice };
      return `<option value="${escapeAttribute(item.value)}" ${current.includes(String(item.value)) ? "selected" : ""}>${escapeHtml(item.label)}</option>`;
    }).join("")}</select></label>`;
  }
  if (type === "secret") {
    const configured = secretConfigured(key);
    return `<label class="form-field"><span>${escapeHtml(label)}${hint}</span><input type="password" ${data} data-secret="1" autocomplete="new-password" placeholder="${configured ? "已配置；留空则保持不变" : placeholder}" /></label>`;
  }
  const numeric = type === "number" || type === "range";
  const attrs = `${numeric ? ` min="${min ?? ""}" max="${max ?? ""}" step="${step ?? "1"}"` : ""}${required ? " required" : ""}`;
  return `<label class="form-field"><span>${escapeHtml(label)}${hint}</span><input type="${type === "range" ? "range" : type}" ${data}${attrs} value="${escapeAttribute(value)}" placeholder="${escapeAttribute(placeholder)}" /></label>`;
}

function bindFields(root = document) {
  $$("[data-field]", root).forEach((element) => {
    const eventName = element.type === "checkbox" || element.tagName === "SELECT" ? "change" : "input";
    element.addEventListener(eventName, () => {
      const scope = element.dataset.scope || "config";
      const key = element.dataset.field;
      let value;
      if (element.type === "checkbox") value = element.checked;
      else if (element.multiple) value = Array.from(element.selectedOptions).map((item) => item.value);
      else if (element.type === "number" || element.type === "range") value = element.value === "" ? 0 : Number(element.value);
      else value = element.value;
      if (element.dataset.transform === "lines") value = String(value).split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
      if (scope === "env") {
        if (element.dataset.secret && !value) delete state.draft.env[key];
        else state.draft.env[key] = value;
      } else {
        state.draft.config[key] = value;
      }
      if (element.dataset.redraw === "1") renderPage();
    });
  });
}

function tableId(key) { return `${state.page}:${key}`; }

function pagedTable(key, columns, rows, options = {}) {
  const id = tableId(key);
  const entry = state.tables[id] || { size: 5, page: 0 };
  state.tables[id] = entry;
  const size = entry.size;
  const pages = Math.max(1, Math.ceil(rows.length / size));
  entry.page = Math.min(entry.page, pages - 1);
  const visible = rows.slice(entry.page * size, (entry.page + 1) * size);
  const head = columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("");
  const body = visible.length ? visible.map((row) => `<tr>${columns.map((column) => `<td>${column.html ? column.html(row) : escapeHtml(column.value ? column.value(row) : row[column.key] ?? "—")}</td>`).join("")}</tr>`).join("") : `<tr><td class="empty-cell" colspan="${columns.length}">${escapeHtml(options.empty || "暂无数据")}</td></tr>`;
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table><div class="pager"><label>每页<select data-table-size="${escapeAttribute(id)}"><option value="5" ${size === 5 ? "selected" : ""}>5 条</option><option value="10" ${size === 10 ? "selected" : ""}>10 条</option></select></label><span>第 ${entry.page + 1} / ${pages} 页 · 共 ${rows.length} 条</span><button class="secondary-button compact-button" data-table-prev="${escapeAttribute(id)}" ${entry.page === 0 ? "disabled" : ""}>上一页</button><button class="secondary-button compact-button" data-table-next="${escapeAttribute(id)}" ${entry.page >= pages - 1 ? "disabled" : ""}>下一页</button></div></div>`;
}

function bindPagers(root = document) {
  $$('[data-table-size]', root).forEach((element) => element.addEventListener("change", () => {
    const item = state.tables[element.dataset.tableSize]; item.size = Number(element.value); item.page = 0; renderPage();
  }));
  $$('[data-table-prev]', root).forEach((element) => element.addEventListener("click", () => { state.tables[element.dataset.tablePrev].page -= 1; renderPage(); }));
  $$('[data-table-next]', root).forEach((element) => element.addEventListener("click", () => { state.tables[element.dataset.tableNext].page += 1; renderPage(); }));
}

function statusCard(status, options = {}) {
  const task = status.task || {};
  const total = Number(task.total);
  const current = Number(task.current);
  const progress = Number.isFinite(total) && total > 0 && Number.isFinite(current) ? `<div class="progress"><div><i style="width:${Math.max(3, Math.min(100, current / total * 100))}%"></i></div><span>当前进度 ${current} / ${total}</span></div>` : "";
  const stop = status.is_active ? '<button class="danger-button" data-stop-task="1">停止当前任务</button>' : "";
  return `<div class="status-card"><div class="status-line"><i class="status-dot ${escapeAttribute(task.state || "idle")}"></i><div><p class="eyebrow">当前任务</p><h3>${escapeHtml(task.label || "正在读取状态")}</h3><p class="muted">${escapeHtml(task.phase || "")}</p></div><span class="timestamp">${task.started_at ? `开始于 ${escapeHtml(formatTime(task.started_at))}` : ""}</span></div>${progress}${task.detail ? `<p class="issue-box">${escapeHtml(task.detail)}</p>` : ""}<div class="action-row">${options.startLabel ? `<button class="primary-button" data-start-task="${escapeAttribute(options.mode)}" ${status.can_start ? "" : "disabled"}>${escapeHtml(options.startLabel)} <span>→</span></button>` : ""}${stop}<button class="secondary-button" data-refresh-status="${escapeAttribute(options.kind || "daily")}">刷新状态</button></div></div>`;
}

function metrics(items) {
  return `<div class="metric-grid">${items.map((item) => `<article class="metric-card"><p>${escapeHtml(item.label)}</p><strong>${escapeHtml(item.value)}</strong><span>${escapeHtml(item.help || "")}</span></article>`).join("")}</div>`;
}

async function fetchStatus(kind) { return api(`/api/status/${encodeURIComponent(kind)}`); }

async function renderDaily(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div class="loading">正在读取每日研究状态…</div>`;
  const status = await fetchStatus("daily");
  if (token !== state.renderToken) return;
  root.innerHTML = `${pageHeader()}${section("每日研究", statusCard(status, { startLabel: "开始每日研究", mode: "daily_research", kind: "daily" }), { icon: "🚀" })}${divider()}${section("状态面板", `${metrics([
    { label: "待处理论文", value: formatNumber(status.queue?.pending), help: "会在后续每日研究中处理" },
    { label: "待重试论文", value: formatNumber(status.queue?.retry), help: "保留阶段状态与问题摘要" },
    { label: "最近一次运行", value: formatNumber(status.last_run?.total_papers), help: status.last_run ? `${status.last_run.status || "已记录"} · ${formatTime(status.last_run.completed_at)}` : "尚无记录" },
  ])}<label class="refresh-row"><span><strong>状态自动刷新</strong><small>有正在运行或等待接手的任务时，每 5 秒自动刷新。</small></span><input id="daily-auto-refresh" type="checkbox" checked /><i></i></label>`, { icon: "📊" })}${divider()}${renderDailySettings()}`;
  bindCommon(root);
  if (status.is_active && $("#daily-auto-refresh")?.checked) scheduleRefresh("daily", () => renderPage(), 5000);
}

function renderDailySettings() {
  return section("每日研究设置", `<div class="form-grid three">${field({ label: "生成 HTML 报告", key: "enable_html_report", type: "checkbox", fallback: true })}${field({ label: "生成 Markdown 报告", key: "enable_markdown_report", type: "checkbox", fallback: true })}${field({ label: "报告包含全部论文", key: "include_all_in_report", type: "checkbox", fallback: true })}</div><div class="form-grid two">${field({ label: "本次最多处理论文数（0 不限）", key: "daily_max_papers_per_run", type: "number", min: 0, step: 1, fallback: 200 })}${field({ label: "每日运行时间", key: "daily_run_time", type: "time", fallback: "12:00" })}</div>`, { icon: "⚙️", hint: "修改后点击左侧“保存所有更改”生效。" });
}

async function renderPastDaily(token) {
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  const values = state.pageData.past || { from: yesterday, to: yesterday };
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div class="loading">正在读取过去日报队列…</div>`;
  const status = await fetchStatus("past");
  if (token !== state.renderToken) return;
  const queue = status.backfill || {};
  root.innerHTML = `${pageHeader()}${section("过去日报", `<p class="hint-text">选择过去日期范围后开始运行。系统会按天把任务写入持久化队列，并与其他研究任务安全互斥。</p><div class="form-grid two"><label class="form-field"><span>开始日期</span><input id="backfill-from" type="date" max="${yesterday}" value="${escapeAttribute(values.from)}" /></label><label class="form-field"><span>结束日期</span><input id="backfill-to" type="date" max="${yesterday}" value="${escapeAttribute(values.to)}" /></label></div><div class="action-row"><button id="backfill-start" class="primary-button" ${status.can_start ? "" : "disabled"}>开始运行 <span>→</span></button><button class="secondary-button" data-refresh-status="past">刷新状态</button></div>${statusCard(status, { kind: "past" })}`, { icon: "🗓" })}${divider()}${section("过去日报队列", `${metrics([
    { label: "等待中", value: formatNumber(queue.pending), help: queue.next_date ? `下一日期：${queue.next_date}` : "暂无待处理日期" },
    { label: "运行中", value: formatNumber(queue.running), help: queue.active_date ? `当前日期：${queue.active_date}` : "" },
    { label: "已完成", value: formatNumber(queue.completed), help: "已生成历史日期报告" },
    { label: "失败", value: formatNumber(queue.failed), help: queue.first_error || "失败任务可在日志中查看" },
  ])}`, { icon: "📋" })}`;
  bindCommon(root);
  $("#backfill-from").addEventListener("change", (event) => { state.pageData.past = { ...values, from: event.target.value }; });
  $("#backfill-to").addEventListener("change", (event) => { state.pageData.past = { ...values, to: event.target.value }; });
  $("#backfill-start").addEventListener("click", async () => {
    const from = $("#backfill-from").value; const to = $("#backfill-to").value;
    if (!from || !to || from > to) return toast("请填写有效的开始和结束日期。", "error");
    try { await api("/api/tasks/backfill_run", { method: "POST", body: { args: { date_from: from, date_to: to } } }); toast("过去日报已加入队列。 "); renderPage(); } catch (error) { toast(error.message, "error"); }
  });
  if (status.is_active) scheduleRefresh("past", () => renderPage(), 5000);
}

function renderTrendForm(templates = []) {
  const config = configValue;
  const today = new Date().toISOString().slice(0, 10);
  const defaultFrom = new Date(Date.now() - Number(config("trend_default_date_range_days", 365)) * 86400000).toISOString().slice(0, 10);
  const configuredPrompt = String(config("trend_analysis_prompt", "") || "");
  const matchingTemplate = templates.find((item) => item.text === configuredPrompt)?.name || "";
  const values = state.pageData.trend || { keywords: "", date_from: defaultFrom, date_to: today, categories: [], max_results: config("trend_max_results", 500), sort_order: config("trend_sort_order", "ascending"), analysis_prompt: configuredPrompt, template: matchingTemplate };
  const categories = arxivCategories().map((item) => `<option value="${escapeAttribute(item.code)}" ${values.categories.includes(item.code) ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("");
  const templateOptions = templates.map((item) => `<option value="${escapeAttribute(item.name)}" ${values.template === item.name ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("");
  return `<div class="form-grid two"><label class="form-field"><span>研究关键词</span><input id="trend-keywords" value="${escapeAttribute(values.keywords)}" placeholder="例如 quantum error correction" /></label><label class="form-field"><span>arXiv 分类（可选）</span><select id="trend-categories" multiple>${categories}</select></label><label class="form-field"><span>开始日期</span><input id="trend-from" type="date" value="${escapeAttribute(values.date_from)}" /></label><label class="form-field"><span>结束日期</span><input id="trend-to" type="date" value="${escapeAttribute(values.date_to)}" /></label></div><div class="form-grid two"><label class="form-field"><span>排序</span><select id="trend-sort"><option value="ascending" ${values.sort_order === "ascending" ? "selected" : ""}>由早到晚</option><option value="descending" ${values.sort_order === "descending" ? "selected" : ""}>由晚到早</option></select></label><label class="form-field"><span>最多结果数</span><input id="trend-max-results" type="number" min="1" max="5000" value="${escapeAttribute(values.max_results)}" /></label></div><div class="form-grid two"><label class="form-field"><span>已保存提示词模板</span><select id="trend-template"><option value="">不使用模板</option>${templateOptions}</select></label><div class="form-field"><span>模板操作</span><button id="trend-template-delete" class="secondary-button" ${values.template ? "" : "disabled"}>删除当前模板</button></div></div><label class="form-field"><span>深度分析提示词（可选）</span><textarea id="trend-prompt" rows="6" placeholder="可留空">${escapeHtml(values.analysis_prompt)}</textarea></label><details class="compact-form"><summary>保存新的提示词模板</summary><div class="form-grid two"><label class="form-field"><span>模板名称</span><input id="trend-template-name" maxlength="120" placeholder="例如：实验进展综述" /></label><label class="form-field"><span>模板内容</span><textarea id="trend-template-text" rows="5" maxlength="8000" placeholder="填写可复用的深度分析提示词"></textarea></label></div><div class="action-row"><button id="trend-template-save" class="secondary-button">保存模板</button></div></details>`;
}

async function renderTrend(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div class="loading">正在读取趋势任务状态…</div>`;
  const [status, templateData] = await Promise.all([fetchStatus("trend"), api("/api/trend/templates")]);
  if (token !== state.renderToken) return;
  const templates = templateData.items || [];
  root.innerHTML = `${pageHeader()}${section("趋势研究", `${renderTrendForm(templates)}<div class="action-row"><button id="trend-start" class="primary-button" ${status.can_start ? "" : "disabled"}>开始运行 <span>→</span></button><button class="secondary-button" data-refresh-status="trend">刷新状态</button></div>${statusCard(status, { kind: "trend" })}`, { icon: "📈" })}${divider()}${section("趋势研究配置", `<div class="form-grid two">${field({ label: "默认日期范围（天）", key: "trend_default_date_range_days", type: "number", min: 30, max: 3650, fallback: 365 })}${field({ label: "报告位置", key: "trend_report_position", type: "select", choices: [{ value: "beginning", label: "报告开头" }, { value: "end", label: "报告末尾" }], fallback: "end" })}${field({ label: "生成 TL;DR", key: "trend_generate_tldr", type: "checkbox", fallback: true })}${field({ label: "TL;DR 批大小", key: "trend_tldr_batch_size", type: "number", min: 1, max: 50, fallback: 10 })}</div><div class="form-grid two">${field({ label: "输出 HTML", key: "trend_output_html", type: "checkbox", fallback: true })}${field({ label: "输出 Markdown", key: "trend_output_md", type: "checkbox", fallback: true })}</div>`, { icon: "⚙️", hint: "输出格式会在保存时转换为兼容配置。" })}`;
  bindCommon(root);
  const preserveTrend = () => {
    state.pageData.trend = {
      keywords: $("#trend-keywords").value, date_from: $("#trend-from").value, date_to: $("#trend-to").value,
      categories: Array.from($("#trend-categories").selectedOptions).map((item) => item.value), sort_order: $("#trend-sort").value,
      max_results: Number($("#trend-max-results").value), analysis_prompt: $("#trend-prompt").value, template: $("#trend-template").value,
    };
  };
  ["#trend-keywords", "#trend-from", "#trend-to", "#trend-categories", "#trend-sort", "#trend-max-results", "#trend-prompt", "#trend-template"].forEach((selector) => $(selector).addEventListener("change", preserveTrend));
  $("#trend-template").addEventListener("change", (event) => {
    const selected = templates.find((item) => item.name === event.target.value);
    if (selected) $("#trend-prompt").value = selected.text;
    preserveTrend();
  });
  $("#trend-template-save").addEventListener("click", async () => {
    const name = $("#trend-template-name").value.trim();
    const text = $("#trend-template-text").value.trim();
    try {
      await api("/api/trend/templates", { method: "PUT", body: { name, text } });
      preserveTrend(); state.pageData.trend.template = name; state.pageData.trend.analysis_prompt = text;
      toast("提示词模板已保存。", "success"); renderPage();
    } catch (error) { toast(error.message, "error"); }
  });
  $("#trend-template-delete").addEventListener("click", async () => {
    const name = $("#trend-template").value;
    if (!name || !window.confirm(`确认删除提示词模板“${name}”？`)) return;
    try {
      await api("/api/trend/templates/delete", { method: "POST", body: { name } });
      preserveTrend(); state.pageData.trend.template = "";
      toast("提示词模板已删除。", "success"); renderPage();
    } catch (error) { toast(error.message, "error"); }
  });
  $("#trend-start").addEventListener("click", async () => {
    preserveTrend(); const values = state.pageData.trend;
    if (!values.keywords.trim()) return toast("请填写至少一个研究关键词。", "error");
    if (!values.date_from || !values.date_to || values.date_from > values.date_to) return toast("请填写有效的日期范围。", "error");
    try {
      await api("/api/tasks/trend_research", { method: "POST", body: { args: { keywords: values.keywords.match(/(?:[^\s"]+|"[^"]*")+/g) || [], date_from: values.date_from, date_to: values.date_to, categories: values.categories, sort_order: values.sort_order, max_results: values.max_results, analysis_prompt: values.analysis_prompt.trim() } } });
      toast("趋势任务已加入队列。 "); renderPage();
    } catch (error) { toast(error.message, "error"); }
  });
  if (status.is_active) scheduleRefresh("trend", () => renderPage(), 5000);
}

function reportTypeLabel(type) {
  return ({ daily: "每日研究", trend: "趋势研究", keyword_trend: "关键词趋势" })[type] || type;
}

function reportGroupKey(type, source) {
  return `${type}:${source}`;
}

function reportPicker(title, icon, type, rows, selected) {
  if (!rows.length) {
    return `<div class="report-picker"><h3>${escapeHtml(icon)} ${escapeHtml(title)}</h3><p class="report-count">0 份报告</p><p class="muted">暂无报告</p></div>`;
  }
  const groups = new Map();
  rows.forEach((row) => {
    const source = String(row.source || "unknown");
    if (!groups.has(source)) groups.set(source, []);
    groups.get(source).push(row);
  });
  const selections = state.pageData.reportSelections || {};
  const body = [...groups.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([source, groupRows]) => {
    const groupKey = reportGroupKey(type, source);
    const saved = selections[groupKey];
    const selectedHere = groupRows.some((item) => item.id === selected) ? selected : (groupRows.some((item) => item.id === saved) ? saved : groupRows[0].id);
    const sourceLabel = type === "keyword_trend"
      ? "关键词趋势"
      : String(groupRows[0].source_label || source);
    return `<div class="report-picker-group"><label class="report-select-field"><span>${escapeHtml(sourceLabel)} <small>(${groupRows.length})</small></span><select data-report-select="${escapeAttribute(groupKey)}">${groupRows.map((item) => `<option value="${escapeAttribute(item.id)}" ${item.id === selectedHere ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}</select></label><button class="secondary-button compact-button report-preview-button" data-preview-group="${escapeAttribute(groupKey)}">预览</button></div>`;
  }).join("");
  return `<div class="report-picker"><h3>${escapeHtml(icon)} ${escapeHtml(title)}</h3><p class="report-count">${rows.length} 份报告</p>${body}</div>`;
}

function formatReportSize(bytes) {
  const size = Number(bytes);
  return Number.isFinite(size) ? `${(size / 1024).toFixed(1)} KB` : "—";
}

function findAdjacentDailyReport(report, rows, direction) {
  if (!report?.date) return null;
  const sameSource = rows.filter((item) => item.source === report.source && item.date);
  const dates = [...new Set(sameSource.map((item) => item.date))].sort();
  const current = dates.indexOf(report.date);
  const targetDate = dates[current + direction];
  if (!targetDate) return null;
  // ``list_reports`` returns the newest report first, including when a
  // supplement and a normal run share the same logical calendar date.
  return sameSource.find((item) => item.date === targetDate) || null;
}

function reportInfoHtml(report) {
  const metadata = report.type === "trend" && report.metadata && Object.keys(report.metadata).length
    ? `<details class="report-metadata"><summary>报告元数据</summary><div class="metric-grid compact-metrics">${report.metadata.keyword !== undefined ? `<div class="metric-card"><p>关键词</p><strong>${escapeHtml(report.metadata.keyword)}</strong></div>` : ""}${report.metadata.date_from && report.metadata.date_to ? `<div class="metric-card"><p>时间范围</p><strong>${escapeHtml(report.metadata.date_from)} → ${escapeHtml(report.metadata.date_to)}</strong></div>` : ""}${report.metadata.total_papers !== undefined ? `<div class="metric-card"><p>论文数量</p><strong>${escapeHtml(report.metadata.total_papers)}</strong></div>` : ""}</div></details>`
    : "";
  return `<p class="report-file-info"><strong>${escapeHtml(reportTypeLabel(report.type))}</strong> · <code>${escapeHtml(report.source)}</code> · <code>${escapeHtml(report.name)}</code> · ${escapeHtml(formatReportSize(report.size_bytes))} · 修改时间：${escapeHtml(formatTime(report.modified_at))}</p>${metadata}`;
}

function normalizeReportText(value) {
  return String(value || "").replace(/\s+/g, " ").trim().toLocaleLowerCase();
}

function buildReportMarkButton(documentNode, preference, current) {
  const button = documentNode.createElement("button");
  button.type = "button";
  button.className = `adr-report-mark-btn${preference === current ? " active" : ""}`;
  button.dataset.preference = preference;
  button.title = preference === "like" ? "喜欢" : "不感兴趣";
  button.textContent = preference === "like" ? "👍" : "👎";
  return button;
}

function buildMarkedReportHtml(rawHtml, papers) {
  const parser = new DOMParser();
  const documentNode = parser.parseFromString(String(rawHtml || ""), "text/html");
  documentNode.querySelectorAll(".revision-label").forEach((node) => {
    const text = normalizeReportText(node.textContent);
    if (/^v\d+$/.test(text) || text === "↻ 重试") node.remove();
  });
  const candidates = Array.isArray(papers) ? papers.filter((paper) => paper?.title && paper?.paper_id && paper?.source) : [];
  const used = new Set();
  let injected = 0;
  documentNode.querySelectorAll(".card.pass, .card.fail").forEach((card) => {
    const cardText = normalizeReportText(card.textContent);
    const matchIndex = candidates.findIndex((paper, index) => !used.has(index) && cardText.includes(normalizeReportText(paper.title)));
    if (matchIndex < 0) return;
    const field = card.querySelector(".field");
    if (!field) return;
    const paper = candidates[matchIndex];
    used.add(matchIndex);
    const current = ["like", "dislike"].includes(paper.preference) ? paper.preference : "none";
    const bar = documentNode.createElement("div");
    bar.className = "adr-report-mark-bar";
    bar.dataset.source = String(paper.source);
    bar.dataset.paperId = String(paper.paper_id);
    bar.dataset.current = current;
    bar.append(buildReportMarkButton(documentNode, "like", current), buildReportMarkButton(documentNode, "dislike", current));
    field.insertBefore(bar, field.firstChild);
    injected += 1;
  });
  if (injected) {
    const style = documentNode.createElement("style");
    style.textContent = ".adr-report-mark-bar{float:right;display:flex;gap:4px;margin-left:12px}.adr-report-mark-btn{border:1px solid rgba(127,127,127,.45);border-radius:8px;background:rgba(255,255,255,.78);cursor:pointer;font-size:13px;line-height:1;padding:4px 7px;color:inherit}.adr-report-mark-btn:hover{background:rgba(255,255,255,.95)}.adr-report-mark-btn.active[data-preference=like]{background:#16a34a;border-color:#16a34a;color:#fff}.adr-report-mark-btn.active[data-preference=dislike]{background:#dc2626;border-color:#dc2626;color:#fff}";
    (documentNode.head || documentNode.documentElement).appendChild(style);
    const script = documentNode.createElement("script");
    script.textContent = "(function(){if(window.__adrReportMarks)return;window.__adrReportMarks=true;function set(bar,pref){pref=pref==='like'||pref==='dislike'?pref:'none';bar.dataset.current=pref;bar.querySelectorAll('.adr-report-mark-btn').forEach(function(button){button.classList.toggle('active',button.dataset.preference===pref);});}window.addEventListener('message',function(event){var data=event.data||{};if(data.type==='adr-report-mark-state'){document.querySelectorAll('.adr-report-mark-bar').forEach(function(bar){if(bar.dataset.source===String(data.source||'')&&bar.dataset.paperId===String(data.paper_id||'')){set(bar,data.preference);}});}});document.addEventListener('click',function(event){var button=event.target&&event.target.closest?event.target.closest('.adr-report-mark-btn'):null;if(!button)return;event.preventDefault();var bar=button.closest('.adr-report-mark-bar');if(!bar)return;var wanted=button.dataset.preference===(bar.dataset.current||'none')?'none':button.dataset.preference;set(bar,wanted);parent.postMessage({type:'adr-report-mark',source:bar.dataset.source,paper_id:bar.dataset.paperId,preference:wanted},'*');});})();";
    (documentNode.body || documentNode.documentElement).appendChild(script);
  }
  return { html: `<!doctype html>${documentNode.documentElement.outerHTML}`, injected };
}

async function fetchReportHtml(reportId) {
  const response = await fetch(`/api/reports/${encodeURIComponent(reportId)}/file`, { credentials: "same-origin" });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(text || "读取报告失败。 ");
  }
  return response.text();
}

async function renderReports(token) {
  const root = $("#page-root");
  // The report iframe is replaced on every selection.  Dispose its previous
  // message listener before creating a new sandboxed preview.
  state.reportMarkAbortController?.abort();
  state.reportMarkAbortController = null;
  const showNonArxiv = Boolean(state.pageData.showNonArxiv);
  root.innerHTML = `${pageHeader()}<div class="loading">正在读取报告目录…</div>`;
  const reports = await api(`/api/reports?non_arxiv=${showNonArxiv ? "1" : "0"}`);
  if (token !== state.renderToken) return;
  const all = [...reports.daily, ...reports.trend, ...reports.keyword_trend];
  let selected = state.pageData.selectedReport;
  if (!selected || !all.some((item) => item.id === selected)) selected = all[0]?.id || "";
  state.pageData.selectedReport = selected;
  if (!state.pageData.reportSelections) state.pageData.reportSelections = {};
  const chooseReport = (reportId) => {
    const report = all.find((item) => item.id === reportId);
    if (!report) return;
    state.pageData.selectedReport = report.id;
    state.pageData.reportSelections[reportGroupKey(report.type, report.source)] = report.id;
    renderPage();
  };
  root.innerHTML = `${pageHeader()}${section("报告浏览", `<div class="toolbar"><label class="toggle-field"><span>显示非 arXiv 来源报告</span><input id="report-non-arxiv" type="checkbox" ${showNonArxiv ? "checked" : ""}/><i></i></label><button id="reports-refresh" class="secondary-button">刷新列表</button></div><div class="report-grid">${reportPicker("每日研究", "📅", "daily", reports.daily, selected)}${reportPicker("趋势研究", "🔬", "trend", reports.trend, selected)}${reportPicker("关键词趋势", "📈", "keyword_trend", reports.keyword_trend, selected)}</div>`, { icon: "📚" })}${selected ? `<div id="report-preview" class="loading">正在加载报告预览…</div>` : section("报告预览", '<p class="empty-state">尚未生成可查看的报告。</p>')}`;
  bindCommon(root);
  $("#report-non-arxiv").addEventListener("change", (event) => { state.pageData.showNonArxiv = event.target.checked; state.pageData.selectedReport = ""; state.pageData.reportSelections = {}; renderPage(); });
  $("#reports-refresh").addEventListener("click", () => { state.pageData.selectedReport = ""; state.pageData.reportSelections = {}; renderPage(); });
  $$('[data-report-select]', root).forEach((select) => select.addEventListener("change", () => chooseReport(select.value)));
  $$('[data-preview-group]', root).forEach((button) => button.addEventListener("click", () => {
    const select = $$('[data-report-select]', root).find((item) => item.dataset.reportSelect === button.dataset.previewGroup);
    if (select) chooseReport(select.value);
  }));
  const report = all.find((item) => item.id === selected);
  if (report) await loadReportPreview(report, reports, token, chooseReport);
}

async function loadReportPreview(report, reports, token, chooseReport) {
  const preview = $("#report-preview");
  if (!preview) return;
  try {
    const [html, paperResponse] = await Promise.all([
      fetchReportHtml(report.id),
      report.type === "daily"
        ? api(`/api/reports/${encodeURIComponent(report.id)}/papers`).catch(() => ({ items: [] }))
        : Promise.resolve({ items: [] }),
    ]);
    if (token !== state.renderToken || state.pageData.selectedReport !== report.id) return;
    const marked = buildMarkedReportHtml(html, paperResponse.items || []);
    const previous = report.type === "daily" ? findAdjacentDailyReport(report, reports.daily, -1) : null;
    const next = report.type === "daily" ? findAdjacentDailyReport(report, reports.daily, 1) : null;
    const navigation = report.type === "daily" ? `<div class="report-navigation"><button class="secondary-button compact-button" data-report-nav="${previous ? escapeAttribute(previous.id) : ""}" ${previous ? "" : "disabled"}>← 前一天</button><button class="secondary-button compact-button" data-report-nav="${next ? escapeAttribute(next.id) : ""}" ${next ? "" : "disabled"}>后一天 →</button></div>` : "";
    preview.innerHTML = section("报告预览", `${reportInfoHtml(report)}${navigation}<iframe class="report-frame" sandbox="allow-scripts allow-popups" referrerpolicy="no-referrer" title="报告预览"></iframe>`);
    const frame = $(".report-frame", preview);
    frame.srcdoc = marked.html;
    $$('[data-report-nav]', preview).forEach((button) => button.addEventListener("click", () => {
      if (button.dataset.reportNav) chooseReport(button.dataset.reportNav);
    }));
    if (report.type === "daily" && marked.injected) {
      const markAbortController = new AbortController();
      state.reportMarkAbortController = markAbortController;
      window.addEventListener("message", async function onReportMark(event) {
        if (event.source !== frame.contentWindow) return;
        const action = event.data || {};
        if (action.type !== "adr-report-mark") return;
        const paper = (paperResponse.items || []).find((item) => String(item.source) === String(action.source || "") && String(item.paper_id) === String(action.paper_id || ""));
        if (!paper || !["like", "dislike", "none"].includes(action.preference)) return;
        try {
          const saved = await api("/api/preferences", { method: "PUT", body: { ...paper, preference: action.preference } });
          paper.preference = saved.preference;
          frame.contentWindow?.postMessage({ type: "adr-report-mark-state", source: paper.source, paper_id: paper.paper_id, preference: saved.preference }, "*");
          toast("论文偏好已保存。 ");
        } catch (error) {
          frame.contentWindow?.postMessage({ type: "adr-report-mark-state", source: paper.source, paper_id: paper.paper_id, preference: paper.preference || "none" }, "*");
          toast(error.message, "error");
        }
      }, { signal: markAbortController.signal });
    }
  } catch (error) {
    preview.innerHTML = section("报告预览", `<p class="error-message">${escapeHtml(error.message)}</p>`);
  }
}

async function renderFavorites(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div class="loading">正在读取收藏数据…</div>`;
  const data = await api("/api/favorites");
  if (token !== state.renderToken) return;
  if (!data.available) {
    root.innerHTML = `${pageHeader()}${section("收藏", '<p class="empty-state">SQLite 数据库尚未创建；运行一次每日研究或导入历史后即可查看。</p>', { icon: "⭐" })}`;
    return;
  }
  const cards = data.liked.map((row) => `<article class="favorite-card"><span>${escapeHtml(formatTime(row.updated_at))}</span><strong>${escapeHtml(row.title)}</strong><small>${escapeHtml(row.source)} · ${escapeHtml(row.paper_id)}</small></article>`);
  root.innerHTML = `${pageHeader()}${section("收藏的论文", `${metrics([{ label: "👍 收藏", value: formatNumber(data.counts?.like), help: "正向偏好" }, { label: "👎 不喜欢", value: formatNumber(data.counts?.dislike), help: "负向偏好" }])}<div class="card-list">${pagedItems("favorite-cards", cards, "暂无已收藏论文")}</div>`, { icon: "⭐" })}${divider()}${section("收藏画像", `<div class="form-grid two">${pagedTable("favorite-authors", [{ label: "作者", key: "name" }, { label: "收藏次数", key: "count" }], data.authors || [], { empty: "暂无作者统计" })}${pagedTable("favorite-keywords", [{ label: "关键词", key: "keyword" }, { label: "次数", key: "count" }], data.keywords || [], { empty: "暂无关键词统计" })}</div>`, { icon: "🧩" })}`;
  bindCommon(root);
}

function pagedItems(key, items, empty = "暂无数据") {
  const id = tableId(key);
  const entry = state.tables[id] || { size: 5, page: 0 };
  state.tables[id] = entry;
  const pages = Math.max(1, Math.ceil(items.length / entry.size));
  entry.page = Math.min(entry.page, pages - 1);
  const visible = items.slice(entry.page * entry.size, (entry.page + 1) * entry.size);
  return `${visible.length ? visible.join("") : `<p class="empty-state">${escapeHtml(empty)}</p>`}<div class="pager"><label>每页<select data-table-size="${escapeAttribute(id)}"><option value="5" ${entry.size === 5 ? "selected" : ""}>5 条</option><option value="10" ${entry.size === 10 ? "selected" : ""}>10 条</option></select></label><span>第 ${entry.page + 1} / ${pages} 页 · 共 ${items.length} 条</span><button class="secondary-button compact-button" data-table-prev="${escapeAttribute(id)}" ${entry.page === 0 ? "disabled" : ""}>上一页</button><button class="secondary-button compact-button" data-table-next="${escapeAttribute(id)}" ${entry.page >= pages - 1 ? "disabled" : ""}>下一页</button></div>`;
}

function searchParamsFromState() {
  const values = state.pageData.search || { query: "", source: "", completed_from: "", completed_to: "", min_score: "", liked_only: false, page: 0, size: 20 };
  state.pageData.search = values;
  const params = new URLSearchParams({ query: values.query, limit: String(values.size), offset: String(values.page * values.size) });
  if (values.source) params.set("source", values.source);
  if (values.completed_from) params.set("completed_from", values.completed_from);
  if (values.completed_to) params.set("completed_to", values.completed_to);
  if (values.min_score) params.set("min_score", values.min_score);
  if (values.liked_only) params.set("liked_only", "true");
  return params;
}

function sourceVariantCard(variant) {
  const statuses = [
    variant.strategy_id ? `策略：${variant.strategy_id}` : "",
    variant.score_status ? `评分：${variant.score_status}` : "",
    variant.translation_status ? `翻译：${variant.translation_status}` : "",
    variant.analysis_status ? `分析：${variant.analysis_status}` : "",
  ].filter(Boolean).join(" · ");
  const url = safeExternalUrl(variant.url);
  const pdfUrl = safeExternalUrl(variant.pdf_url);
  const analysis = variant.analysis && typeof variant.analysis === "object" && Object.keys(variant.analysis).length
    ? `<details class="paper-analysis"><summary>深度分析</summary><pre>${escapeHtml(JSON.stringify(variant.analysis, null, 2))}</pre></details>`
    : "";
  return `<article class="paper-variant"><h4>${escapeHtml(variant.source || "—")} · ${escapeHtml(formatTime(variant.completed_at) || "未完成")}</h4>${statuses ? `<p class="muted">${escapeHtml(statuses)}</p>` : ""}${variant.tldr ? `<p><strong>TL;DR：</strong>${escapeHtml(variant.tldr)}</p>` : ""}${variant.abstract_cn ? `<p><strong>中文摘要：</strong>${escapeHtml(variant.abstract_cn)}</p>` : ""}${variant.extracted_keywords?.length ? `<p><strong>关键词：</strong>${escapeHtml(variant.extracted_keywords.join(" · "))}</p>` : ""}${analysis}${variant.last_error ? `<p class="error-message">问题：${escapeHtml(variant.last_error)}</p>` : ""}${url || pdfUrl ? `<p>${url ? `<a href="${escapeAttribute(url)}" target="_blank" rel="noopener noreferrer">论文页面</a>` : ""}${url && pdfUrl ? " · " : ""}${pdfUrl ? `<a href="${escapeAttribute(pdfUrl)}" target="_blank" rel="noopener noreferrer">PDF</a>` : ""}</p>` : ""}${variant.report_path ? `<p class="muted">报告路径：${escapeHtml(variant.report_path)}</p>` : ""}</article>`;
}

function paperCard(item) {
  const score = Number.isFinite(Number(item.total_score)) ? Number(item.total_score).toFixed(1) : "—";
  const badge = item.is_qualified === true ? "🟢" : item.is_qualified === false ? "⚪" : "·";
  const sources = (item.sources || [item.source]).filter(Boolean).join(", ");
  const preference = item.preference === "like" ? " 👍" : item.preference === "dislike" ? " 👎" : "";
  const url = safeExternalUrl(item.url);
  const pdfUrl = safeExternalUrl(item.pdf_url);
  const metadata = [
    `来源：${sources || "—"}`,
    `完成：${formatTime(item.completed_at) || "—"}`,
    item.published_date ? `发布：${item.published_date}` : "",
    item.strategy_id ? `策略：${item.strategy_id}` : "",
  ].filter(Boolean).join(" ｜ ");
  const variants = (item.variants || []).map(sourceVariantCard).join("");
  return `<details class="paper-card"><summary><span class="score-pill">${score}</span><strong>${badge} ${escapeHtml(item.title)}${preference}</strong><small>${escapeHtml(metadata)}</small></summary><div class="paper-content">${item.authors?.length ? `<p><strong>作者：</strong>${escapeHtml(item.authors.slice(0, 12).join(", "))}</p>` : ""}${item.tldr ? `<p><strong>TL;DR：</strong>${escapeHtml(item.tldr)}</p>` : ""}${(item.merged_keywords || item.extracted_keywords)?.length ? `<p><strong>合并关键词：</strong>${escapeHtml((item.merged_keywords || item.extracted_keywords).join(" · "))}</p>` : ""}${item.categories?.length ? `<p class="muted">分类：${escapeHtml(item.categories.join(" "))}</p>` : ""}${url || pdfUrl ? `<p>${url ? `<a href="${escapeAttribute(url)}" target="_blank" rel="noopener noreferrer">arXiv / 论文页面</a>` : ""}${url && pdfUrl ? " ｜ " : ""}${pdfUrl ? `<a href="${escapeAttribute(pdfUrl)}" target="_blank" rel="noopener noreferrer">PDF</a>` : ""}</p>` : ""}${variants ? `<div class="paper-variants"><h3>来源变体</h3>${variants}</div>` : ""}</div></details>`;
}

async function renderPaperSearch(token) {
  const root = $("#page-root");
  const values = state.pageData.search || { query: "", source: "", completed_from: "", completed_to: "", min_score: "", liked_only: false, page: 0, size: 20 };
  root.innerHTML = `${pageHeader()}${section("检索条件", `<div class="form-grid two"><label class="form-field"><span>关键词</span><input id="search-query" value="${escapeAttribute(values.query)}" placeholder="标题、摘要、TL;DR 或关键词" /></label><label class="form-field"><span>来源</span><select id="search-source"><option value="">全部来源</option></select></label><label class="form-field"><span>完成日期开始</span><input id="search-from" type="date" value="${escapeAttribute(values.completed_from)}" /></label><label class="form-field"><span>完成日期结束</span><input id="search-to" type="date" value="${escapeAttribute(values.completed_to)}" /></label><label class="form-field"><span>最低分数</span><input id="search-score" type="number" step="0.5" min="0" value="${escapeAttribute(values.min_score)}" /></label><label class="toggle-field"><span>仅收藏论文</span><input id="search-liked" type="checkbox" ${values.liked_only ? "checked" : ""}/><i></i></label></div><div class="action-row"><button id="search-run" class="primary-button">搜索 <span>→</span></button></div>`, { icon: "🔍" })}<div id="search-results"><p class="empty-state">填写条件后开始搜索。</p></div>`;
  const sourceSelect = $("#search-source");
  try {
    const sourceProbe = await api("/api/papers?limit=5&offset=0");
    if (token !== state.renderToken) return;
    if (!sourceProbe.available) {
      root.innerHTML = `${pageHeader()}${section("论文检索", '<p class="info-box">SQLite 数据库尚未创建；运行一次每日研究或导入历史后即可检索。</p>', { icon: "🔍" })}`;
      return;
    }
    sourceSelect.insertAdjacentHTML("beforeend", (sourceProbe.sources || []).map((source) => `<option value="${escapeAttribute(source)}" ${values.source === source ? "selected" : ""}>${escapeHtml(source)}</option>`).join(""));
    if (values.executed) await loadSearchResults(token);
  } catch (error) { $("#search-results").innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`; }
  $("#search-run").addEventListener("click", () => {
    state.pageData.search = { ...values, query: $("#search-query").value, source: sourceSelect.value, completed_from: $("#search-from").value, completed_to: $("#search-to").value, min_score: $("#search-score").value, liked_only: $("#search-liked").checked, page: 0, size: values.size, executed: true };
    loadSearchResults(token);
  });
}

async function loadSearchResults(token) {
  const target = $("#search-results"); if (!target) return;
  target.innerHTML = '<div class="loading">正在检索 SQLite 历史库…</div>';
  try {
    const values = state.pageData.search;
    const result = await api(`/api/papers?${searchParamsFromState()}`);
    if (token !== state.renderToken) return;
    if (!result.available) {
      target.innerHTML = '<p class="info-box">SQLite 数据库尚未创建；运行一次每日研究或导入历史后即可检索。</p>';
      return;
    }
    const pages = Math.max(1, Math.ceil(result.total / values.size));
    const pager = pages > 1 ? `<div class="pager"><span>第 ${values.page + 1} / ${pages} 页 · 每页 20 篇</span><button id="search-prev" class="secondary-button compact-button" ${values.page === 0 ? "disabled" : ""}>上一页</button><button id="search-next" class="secondary-button compact-button" ${values.page >= pages - 1 ? "disabled" : ""}>下一页</button></div>` : "";
    target.innerHTML = section(`检索结果（共 ${result.total} 篇匹配）`, `${result.items?.length ? result.items.map(paperCard).join("") : '<p class="empty-state">没有匹配的论文。</p>'}${pager}`);
    $("#search-prev")?.addEventListener("click", () => { values.page -= 1; loadSearchResults(token); });
    $("#search-next")?.addEventListener("click", () => { values.page += 1; loadSearchResults(token); });
  } catch (error) { target.innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`; }
}

async function renderKeywords(token) {
  const root = $("#page-root");
  const context = escapeHtml(String(configValue("research_context", "") || ""));
  root.innerHTML = `${pageHeader()}<section class="section-card"><p class="hint-text">研究背景会用于评分和参考文献关键词提取。</p><label class="form-field"><textarea data-field="research_context" data-scope="config" aria-label="研究背景" rows="6" placeholder="描述你的研究问题、方法与关注方向">${context}</textarea></label></section>${divider()}${section("主关键词", `${field({ label: "每行一个关键词", key: "primary_keywords", type: "lines", rows: 8, fallback: [] })}${field({ label: "主关键词权重", key: "primary_keyword_weight", type: "number", min: 0.1, max: 5, step: 0.1, fallback: 1 })}`, { icon: "🏷️", hint: "主关键词参与资格判定与排序。" })}${divider()}${renderReferenceExtraction()}`;
  bindCommon(root);
  try {
    const result = await api("/api/extracted-keywords");
    if (token !== state.renderToken || !configValue("enable_reference_extraction", false)) return;
    const host = $("#extracted-keywords");
    if (host) host.innerHTML = result.items?.length ? pagedTable("extracted-keywords", [{ label: "关键词", key: "keyword" }, { label: "权重", value: (row) => Number(row.weight).toFixed(2) }], result.items, { empty: "尚未提取关键词" }) : '<p class="empty-state">尚未提取关键词。</p>';
    bindPagers(root);
  } catch (error) { /* cache visibility should not prevent configuration */ }
}

function renderReferenceExtraction() {
  const enabled = Boolean(configValue("enable_reference_extraction", false));
  const content = `${field({ label: "启用参考文献关键词提取", key: "enable_reference_extraction", type: "checkbox", fallback: false, redraw: true })}${enabled ? `<div class="form-grid two">${field({ label: "最多关键词数量", key: "max_reference_keywords", type: "number", min: 1, max: 50, fallback: 10 })}${field({ label: "相似度阈值", key: "similarity_threshold", type: "number", min: 0, max: 1, step: 0.05, fallback: 0.75 })}</div><div class="form-grid three">${weightField("高重要度", "high", 1, 3)}${weightField("中重要度", "medium", 0.2, 5)}${weightField("低重要度", "low", 0.1, 2)}</div><div id="extracted-keywords"><div class="loading">正在读取已提取关键词…</div></div>` : '<p class="hint-text">关闭后不会展示或使用此前提取的关键词；缓存会保留，重新开启后可继续复用。</p>'}`;
  return section("参考文献 PDF 关键词提取", content, { icon: "📚" });
}

function weightField(label, level, defaultWeight, defaultCount) {
  return `<div class="mini-card"><h3>${escapeHtml(label)}</h3>${field({ label: "权重", key: `ref_weight_${level}`, type: "number", min: 0, max: 5, step: 0.1, fallback: defaultWeight })}${field({ label: "数量", key: `ref_count_${level}`, type: "number", min: 0, max: 20, fallback: defaultCount })}</div>`;
}

function ensureSourceState() {
  if (state.pageData.sources) return state.pageData.sources;
  const config = state.settings.config || {};
  const definitions = Array.isArray(config.extra_source_definitions) ? config.extra_source_definitions : [];
  const builtins = state.settings.builtin_sources || [];
  const builtinCodes = new Set(builtins.map((item) => item.code));
  state.pageData.sources = {
    arxiv: (config.enabled_sources || []).includes("arxiv"),
    domains: Array.isArray(config.domains) ? config.domains : ["quant-ph"],
    extraEnabled: Boolean(config.extra_sources_enabled),
    builtins: builtins.filter((item) => item.code === "prl" ? (config.enabled_sources || []).includes("prl") : definitions.some((definition) => definition.code === item.code)).map((item) => item.code),
    custom: definitions.filter((definition) => !builtinCodes.has(definition.code)),
  };
  return state.pageData.sources;
}

function sourceDefinition(code) {
  return (state.settings.builtin_sources || []).find((item) => item.code === code);
}

function renderSources() {
  const data = ensureSourceState();
  const builtinOptions = (state.settings.builtin_sources || []).map((item) => `<option value="${escapeAttribute(item.code)}" ${data.builtins.includes(item.code) ? "selected" : ""}>${escapeHtml(item.display_name)}（${escapeHtml(item.code)}）</option>`).join("");
  const customRows = pagedItems(
    "custom-sources",
    data.custom.map((item, index) => `<div class="list-row"><span><strong>${escapeHtml(item.display_name)}</strong> · ${escapeHtml(item.code)} · ${escapeHtml(item.full_name)}${item.issn?.length ? ` · ISSN: ${escapeHtml(item.issn.join(", "))}` : ""}</span><button class="icon-danger" data-remove-custom="${index}" aria-label="移除来源">×</button></div>`),
    "暂无自定义额外来源。",
  );
  const categoryOptions = arxivCategories().map((item) => `<option value="${escapeAttribute(item.code)}" ${data.domains.includes(item.code) ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("");
  return `${section("arXiv", `<label class="toggle-field"><span>启动 arXiv 来源</span><input id="source-arxiv" type="checkbox" ${data.arxiv ? "checked" : ""}/><i></i></label>${data.arxiv ? `<p class="hint-text">选择需要扫描的 arXiv 分类。</p><div class="form-grid two"><label class="form-field"><span>arXiv 分类</span><select id="source-domains" multiple>${categoryOptions}</select></label>${field({ label: "请求超时（秒）", key: "arxiv_fetch_timeout_seconds", type: "number", min: 30, max: 1800, fallback: 180 })}${field({ label: "公告回看宽限（天）", key: "arxiv_announcement_lookback_grace_days", type: "number", min: 0, max: 30, fallback: 2 })}</div>` : ""}`)}${divider()}${section("额外数据源", `<label class="toggle-field"><span>启动额外数据源</span><input id="extra-enabled" type="checkbox" ${data.extraEnabled ? "checked" : ""}/><i></i></label>${data.extraEnabled ? `<div class="form-grid two"><label class="form-field"><span>内置来源</span><select id="extra-builtins" multiple>${builtinOptions}</select></label>${field({ label: "按数据源分类整理报告", key: "reports_by_source", type: "checkbox", fallback: true })}</div><div class="source-custom"><h3>自定义来源</h3>${customRows}<details><summary>添加自定义 OpenAlex 期刊来源</summary><div class="form-grid two"><label class="form-field"><span>来源代码</span><input id="custom-code" placeholder="optica_express" /></label><label class="form-field"><span>展示名称</span><input id="custom-display" placeholder="Opt. Express" /></label><label class="form-field"><span>完整名称</span><input id="custom-full" placeholder="Optics Express" /></label><label class="form-field"><span>ISSN（逗号分隔）</span><input id="custom-issn" placeholder="1094-4087" /></label></div><button id="custom-add" class="secondary-button">添加来源</button></details></div>${data.builtins.includes("huggingface_papers") ? `<div class="form-grid two">${field({ label: "Hugging Face 可用性滞后（天）", key: "huggingface_papers_availability_lag_days", type: "number", min: 0, max: 30, fallback: 2 })}${field({ label: "回看宽限（天）", key: "huggingface_papers_lookback_grace_days", type: "number", min: 0, max: 30, fallback: 2 })}${field({ label: "请求超时（秒）", key: "huggingface_papers_request_timeout_seconds", type: "number", min: 5, max: 600, fallback: 30 })}${field({ label: "请求间隔（秒）", key: "huggingface_papers_request_interval_seconds", type: "number", min: 0, max: 60, step: 0.05, fallback: 0.25 })}</div>` : ""}` : '<p class="hint-text">开启后可选择内置来源或添加 ISSN 期刊来源。</p>'}`)}`;
}

function bindSources(root) {
  const data = ensureSourceState();
  $("#source-arxiv", root)?.addEventListener("change", (event) => { data.arxiv = event.target.checked; renderPage(); });
  $("#extra-enabled", root)?.addEventListener("change", (event) => { data.extraEnabled = event.target.checked; renderPage(); });
  $("#source-domains", root)?.addEventListener("change", (event) => { data.domains = Array.from(event.target.selectedOptions).map((item) => item.value); });
  $("#extra-builtins", root)?.addEventListener("change", (event) => { data.builtins = Array.from(event.target.selectedOptions).map((item) => item.value); renderPage(); });
  $$('[data-remove-custom]', root).forEach((button) => button.addEventListener("click", () => { data.custom.splice(Number(button.dataset.removeCustom), 1); renderPage(); }));
  $("#custom-add", root)?.addEventListener("click", () => {
    const code = $("#custom-code").value.trim().toLowerCase(); const display = $("#custom-display").value.trim(); const full = $("#custom-full").value.trim(); const issn = $("#custom-issn").value.split(",").map((item) => item.trim()).filter(Boolean);
    if (!/^[a-z][a-z0-9_]{1,63}$/.test(code) || !display || !full || !issn.length) return toast("请填写有效的来源代码、名称和 ISSN。", "error");
    if (data.custom.some((item) => item.code === code) || data.builtins.includes(code)) return toast("来源代码已经存在。", "error");
    data.custom.push({ type: "openalex_journal", code, display_name: display, full_name: full, issn }); renderPage();
  });
}

async function renderDataSources(_token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}${renderSources()}`;
  bindCommon(root); bindSources(root);
}

async function renderScoring(token) {
  const strategy = configValue("score_strategy", "core_relevance_v2");
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}${section("评分策略", `${field({ label: "策略", key: "score_strategy", type: "select", choices: [{ value: "core_relevance_v2", label: "核心相关度 V2" }, { value: "legacy_weighted_keyword_v1", label: "加权关键词 V1（兼容）" }, { value: "learned_preference_v1", label: "学习偏好 V1" }], fallback: "core_relevance_v2", redraw: true })}${strategyDescription(strategy)}${renderStrategyFields(strategy)}`, { icon: "🧮" })}${divider()}${renderAuthorBonus()}`;
  bindCommon(root);
  if (strategy === "learned_preference_v1") {
    try {
      const learned = await api("/api/learned-preferences");
      if (token !== state.renderToken) return;
      const host = $("#learned-library");
      if (host) host.innerHTML = `<div class="form-grid two">${pagedTable("learned-keywords", [{ label: "关键词", key: "term" }, { label: "权重", value: (row) => Number(row.weight).toFixed(2) }], learned.keywords || [], { empty: "暂无学习关键词" })}${pagedTable("learned-authors", [{ label: "作者", key: "term" }, { label: "权重", value: (row) => Number(row.weight).toFixed(2) }], learned.authors || [], { empty: "暂无学习作者" })}</div>`;
      bindPagers(root);
    } catch (error) { /* preference library is optional */ }
  }
}

function strategyDescription(strategy) {
  const descriptions = {
    core_relevance_v2: "V2 先检查主关键词的内容相关度：加权平均分达到阈值，且至少一个主关键词达到强匹配分。参考关键词和专家作者仅用于排序，不能让无关论文通过。",
    legacy_weighted_keyword_v1: "兼容旧版加权关键词公式：主关键词数量与权重共同计算通过分数。适合希望延续旧阈值行为的配置。",
    learned_preference_v1: "在加权关键词资格基础上，将 👍/👎 形成的关键词和作者偏好作为受限排序信号。偏好不会绕过基础资格条件。",
  };
  return `<p class="info-box">${escapeHtml(descriptions[strategy] || descriptions.core_relevance_v2)}</p>`;
}

function renderStrategyFields(strategy) {
  if (strategy === "core_relevance_v2") return `<div class="form-grid three">${field({ label: "加权平均相关度阈值", key: "core_relevance_threshold", type: "number", min: 0, max: 100, step: 0.5, fallback: 6 })}${field({ label: "主关键词强匹配最低分", key: "core_keyword_min_score", type: "number", min: 0, max: 100, step: 0.5, fallback: 7 })}${field({ label: "参考词排序权重", key: "reference_ranking_weight", type: "number", min: 0, max: 5, step: 0.05, fallback: 0.25 })}</div>${field({ label: "单关键词最高分", key: "max_score_per_keyword", type: "number", min: 1, max: 100, fallback: 10 })}`;
  if (strategy === "learned_preference_v1") return `<div class="form-grid two">${field({ label: "学习权重衰减", key: "learned_weight_dampening", type: "number", min: 0, max: 1, step: 0.05, fallback: 0.5 })}${field({ label: "学习词权重上限", key: "learned_term_weight_cap", type: "number", min: 0.1, max: 10, step: 0.1, fallback: 2 })}</div>${legacyStrategyFields()}<div id="learned-library"><div class="loading">正在读取偏好词库…</div></div>`;
  return legacyStrategyFields();
}

function legacyStrategyFields() { return `<div class="form-grid three">${field({ label: "基础通过分", key: "passing_score_base", type: "number", min: 0, max: 100, step: 0.5, fallback: 5 })}${field({ label: "权重系数", key: "passing_score_weight_coefficient", type: "number", min: 0, max: 20, step: 0.5, fallback: 3 })}${field({ label: "单关键词最高分", key: "max_score_per_keyword", type: "number", min: 1, max: 100, fallback: 10 })}</div>`; }

function renderAuthorBonus() {
  const enabled = Boolean(configValue("enable_author_bonus", false));
  return section("作者偏好", `${field({ label: "启用专家作者排序加分", key: "enable_author_bonus", type: "checkbox", fallback: false, redraw: true })}${enabled ? `<div class="form-grid two">${field({ label: "专家作者（每行一位）", key: "expert_authors", type: "lines", rows: 6, fallback: [] })}${field({ label: "加分", key: "author_bonus_points", type: "number", min: 0, max: 50, step: 0.5, fallback: 5 })}</div>` : '<p class="hint-text">作者偏好可帮助排序，不会令无关论文通过资格筛选。</p>'}`, { icon: "👤" });
}

function llmSection(role, title, icon) {
  const prefix = role === "cheap" ? "CHEAP_LLM" : "SMART_LLM";
  const keyPrefix = role === "cheap" ? "cheap" : "smart";
  const presets = [
    ["openai", "OpenAI", "https://api.openai.com/v1"], ["deepseek", "DeepSeek", "https://api.deepseek.com/v1"], ["ollama", "Ollama（本地）", "http://127.0.0.1:11434/v1"], ["zhipu", "智谱 AI", "https://open.bigmodel.cn/api/paas/v4"], ["custom", "自定义", ""],
  ];
  const base = envValue(`${prefix}__BASE_URL`, "https://api.openai.com/v1");
  const detected = presets.find(([id, _label, url]) => url && base.includes(url))?.[0] || "custom";
  return section(title, `<div class="form-grid two"><label class="form-field"><span>服务商预设</span><select id="${keyPrefix}-provider">${presets.map(([id, label]) => `<option value="${id}" ${id === detected ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}</select></label>${field({ label: "Base URL", key: `${prefix}__BASE_URL`, scope: "env", fallback: base, placeholder: "https://api.example.com/v1" })}${field({ label: "API Key", key: `${prefix}__API_KEY`, scope: "env", type: "secret" })}${field({ label: "模型名称", key: `${prefix}__MODEL_NAME`, scope: "env", fallback: role === "cheap" ? "gpt-4o-mini" : "gpt-4o" })}${field({ label: "Temperature", key: `${prefix}__TEMPERATURE`, scope: "env", type: "number", min: 0, max: 2, step: 0.1, fallback: 0.3 })}</div><div class="action-row"><button class="secondary-button" data-test-llm="${role}">测试连接</button><span id="${keyPrefix}-test-result" class="inline-result"></span></div>`, { icon });
}

function mineruSection() {
  const selected = configValue("pdf_parser_mode", "pymupdf") === "mineru";
  if (!selected) return "";
  return `${divider()}${section("MinerU PDF 解析 API", `${field({ label: "MinerU API Key", key: "MINERU_API_KEY", scope: "env", type: "secret" })}<div class="action-row"><button class="secondary-button" data-test-mineru="1">测试连接</button><a href="https://mineru.net/apiManage/apiKey" target="_blank" rel="noreferrer">打开 MinerU API 控制面板</a><span id="mineru-test-result" class="inline-result"></span></div>`, { icon: "📄", hint: "仅在 PDF 解析器选择 MinerU 时显示。" })}`;
}

function thirdPartySection() {
  const openAlexEnabled = String(envValue("ENABLE_OPENALEX", "true")).toLowerCase() !== "false";
  const semanticEnabled = String(envValue("ENABLE_SEMANTIC_SCHOLAR_TLDR", "true")).toLowerCase() !== "false";
  return `${divider()}${section("第三方 API 密钥", `<div class="subsection"><h3>📚 OpenAlex</h3><label class="toggle-field"><span>启用 OpenAlex</span><input id="openalex-enabled" type="checkbox" ${openAlexEnabled ? "checked" : ""}/><i></i></label>${openAlexEnabled ? `${field({ label: "OpenAlex API Key", key: "OPENALEX_API_KEY", scope: "env", type: "secret" })}<div class="action-row"><button class="secondary-button" data-test-third="openalex">测试连接</button><a href="https://openalex.org/settings/api" target="_blank" rel="noreferrer">OpenAlex API 控制台</a><span id="openalex-test-result" class="inline-result"></span></div>` : ""}</div><div class="subsection"><h3>🧠 Semantic Scholar</h3><label class="toggle-field"><span>启用 Semantic Scholar TL;DR</span><input id="semantic-enabled" type="checkbox" ${semanticEnabled ? "checked" : ""}/><i></i></label>${semanticEnabled ? `${field({ label: "Semantic Scholar API Key", key: "SEMANTIC_SCHOLAR_API_KEY", scope: "env", type: "secret" })}<div class="action-row"><button class="secondary-button" data-test-third="semantic_scholar">测试连接</button><a href="https://www.semanticscholar.org/product/api#api-key-form" target="_blank" rel="noreferrer">Semantic Scholar API 控制台</a><span id="semantic_scholar-test-result" class="inline-result"></span></div>` : ""}</div>`, { icon: "🔑", hint: "开关关闭后不会请求相应第三方服务。" })}`;
}

async function renderApi(_token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}${llmSection("cheap", "低成本 LLM", "💸")}${divider()}${llmSection("smart", "高性能 LLM", "🧠")}${mineruSection()}${thirdPartySection()}`;
  bindCommon(root);
  $$("[data-test-llm]", root).forEach((button) => button.addEventListener("click", () => testLlm(button.dataset.testLlm)));
  $("[data-test-mineru]", root)?.addEventListener("click", () => testConnection("mineru", {}, "mineru-test-result"));
  $$("[data-test-third]", root).forEach((button) => button.addEventListener("click", () => testConnection(button.dataset.testThird, {}, `${button.dataset.testThird}-test-result`)));
  $("#openalex-enabled", root)?.addEventListener("change", (event) => { state.draft.env.ENABLE_OPENALEX = event.target.checked ? "true" : "false"; renderPage(); });
  $("#semantic-enabled", root)?.addEventListener("change", (event) => { state.draft.env.ENABLE_SEMANTIC_SCHOLAR_TLDR = event.target.checked ? "true" : "false"; renderPage(); });
  ["cheap", "smart"].forEach((role) => $("#" + role + "-provider", root)?.addEventListener("change", (event) => {
    const urls = { openai: "https://api.openai.com/v1", deepseek: "https://api.deepseek.com/v1", ollama: "http://127.0.0.1:11434/v1", zhipu: "https://open.bigmodel.cn/api/paas/v4", custom: "" };
    const key = `${role === "cheap" ? "CHEAP_LLM" : "SMART_LLM"}__BASE_URL`;
    if (event.target.value !== "custom") state.draft.env[key] = urls[event.target.value];
    renderPage();
  }));
}

async function testLlm(role) {
  const prefix = role === "cheap" ? "CHEAP_LLM" : "SMART_LLM";
  const keyPrefix = role === "cheap" ? "cheap" : "smart";
  await testConnection(`${role}_llm`, {
    api_key: state.draft.env[`${prefix}__API_KEY`], base_url: envValue(`${prefix}__BASE_URL`), model: envValue(`${prefix}__MODEL_NAME`),
  }, `${keyPrefix}-test-result`);
}

async function testConnection(kind, payload, resultId) {
  const result = $("#" + resultId);
  if (result) result.textContent = "正在测试…";
  try {
    const response = await api(`/api/connections/${encodeURIComponent(kind)}`, { method: "POST", body: payload });
    if (result) { result.textContent = response.message || (response.ok ? "连接正常" : "连接失败"); result.className = `inline-result ${response.ok ? "success" : "error"}`; }
  } catch (error) { if (result) { result.textContent = error.message; result.className = "inline-result error"; } else toast(error.message, "error"); }
}

function renderNotifications() {
  const enabled = Boolean(configValue("notifications_enabled", false));
  const emailEnabled = Boolean(configValue("notify_email_enabled", false));
  const channel = (key, label, fields) => `<details class="channel-card" ${configValue(key, false) ? "open" : ""}><summary>${escapeHtml(label)}</summary>${field({ label: `启用${label}`, key, type: "checkbox", fallback: false, redraw: true })}${configValue(key, false) ? fields : ""}</details>`;
  const emailFields = emailEnabled ? `<div class="form-grid three">${field({ label: "SMTP 主机", key: "SMTP_HOST", scope: "env" })}${field({ label: "端口", key: "SMTP_PORT", scope: "env", type: "number", min: 1, max: 65535, fallback: 587 })}${field({ label: "使用 TLS", key: "SMTP_USE_TLS", scope: "env", type: "checkbox", fallback: true })}${field({ label: "用户名", key: "SMTP_USER", scope: "env" })}${field({ label: "密码", key: "SMTP_PASSWORD", scope: "env", type: "secret" })}${field({ label: "发件人", key: "SMTP_FROM", scope: "env" })}${field({ label: "收件人（逗号分隔）", key: "SMTP_TO", scope: "env" })}</div><div class="action-row"><button id="smtp-test" class="secondary-button">测试 SMTP 连接</button><span id="smtp-test-result" class="inline-result"></span></div>` : "";
  const mainBody = field({ label: "启用通知", key: "notifications_enabled", type: "checkbox", fallback: false, redraw: true }) + (enabled ? `<div class="form-grid three">${field({ label: "任务成功通知", key: "notify_on_success", type: "checkbox", fallback: true })}${field({ label: "任务失败通知", key: "notify_on_failure", type: "checkbox", fallback: true })}${field({ label: "通知中展示论文数量", key: "notification_top_n", type: "number", min: 1, max: 50, fallback: 5 })}</div>${field({ label: "附加报告文件", key: "notify_attach_reports", type: "checkbox", fallback: false })}${divider()}${channel("notify_email_enabled", "邮件", emailFields)}` : '<p class="hint-text">启用后可配置邮件、企业微信、钉钉、Telegram、Slack 或通用 Webhook。</p>');
  const channels = enabled ? section("通知渠道", `${channel("notify_wechat_enabled", "企业微信", field({ label: "Webhook URL", key: "WECHAT_WEBHOOK_URL", scope: "env", type: "secret" }))}${channel("notify_dingtalk_enabled", "钉钉", `${field({ label: "Webhook URL", key: "DINGTALK_WEBHOOK_URL", scope: "env", type: "secret" })}${field({ label: "签名密钥（可选）", key: "DINGTALK_SECRET", scope: "env", type: "secret" })}`)}${channel("notify_telegram_enabled", "Telegram", `<div class="form-grid two">${field({ label: "Bot Token", key: "TELEGRAM_BOT_TOKEN", scope: "env", type: "secret" })}${field({ label: "Chat ID", key: "TELEGRAM_CHAT_ID", scope: "env" })}</div>`)}${channel("notify_slack_enabled", "Slack", field({ label: "Webhook URL", key: "SLACK_WEBHOOK_URL", scope: "env", type: "secret" }))}${channel("notify_generic_webhook_enabled", "通用 Webhook", field({ label: "Webhook URL", key: "GENERIC_WEBHOOK_URL", scope: "env", type: "secret" }))}`, { icon: "📣" }) : "";
  return `${section("通知设置", mainBody, { icon: "🔔" })}${channels}`;
}

async function renderNotificationsPage(_token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}${renderNotifications()}`;
  bindCommon(root);
  $("#smtp-test", root)?.addEventListener("click", () => testConnection("smtp", { host: envValue("SMTP_HOST"), port: envValue("SMTP_PORT", "587"), user: envValue("SMTP_USER"), password: state.draft.env.SMTP_PASSWORD, use_tls: envValue("SMTP_USE_TLS", "true") }, "smtp-test-result"));
}

function renderProxySettings() {
  const enabled = Boolean(configValue("proxy_enabled", false));
  return section("网络代理", `${field({ label: "启用网络代理", key: "proxy_enabled", type: "checkbox", fallback: false, redraw: true })}${enabled ? `${field({ label: "代理地址", key: "proxy_url", fallback: "", placeholder: "http://127.0.0.1:7890" })}${field({ label: "不使用代理的地址（每行一项）", key: "proxy_no_proxy", type: "textarea", rows: 4, fallback: "localhost,127.0.0.1" })}${divider()}<p class="hint-text">选择需要经由代理访问的服务。</p><div class="form-grid two">${field({ label: "arXiv", key: "proxy_arxiv", type: "checkbox", fallback: true })}${field({ label: "OpenAlex", key: "proxy_openalex", type: "checkbox", fallback: false })}${field({ label: "Hugging Face Papers", key: "proxy_huggingface_papers", type: "checkbox", fallback: false })}${field({ label: "Semantic Scholar", key: "proxy_semantic_scholar", type: "checkbox", fallback: false })}${field({ label: "LLM API", key: "proxy_llm_api", type: "checkbox", fallback: false })}${field({ label: "通知", key: "proxy_notifications", type: "checkbox", fallback: false })}${field({ label: "WebDAV", key: "proxy_webdav", type: "checkbox", fallback: true })}${field({ label: "检查更新", key: "proxy_update_check", type: "checkbox", fallback: false })}</div>` : '<p class="hint-text">关闭时保留既有代理设置，重新启用后会恢复。</p>'}`, { icon: "🌐" });
}

function renderAdvanced() {
  const mineru = configValue("pdf_parser_mode", "pymupdf") === "mineru";
  const tracker = Boolean(configValue("keyword_tracker_enabled", true));
  return `${section("PDF 解析器", `<p class="hint-text">PyMuPDF 使用本地解析；选择 MinerU 后才会启用其云端相关配置。</p><div class="form-grid two">${field({ label: "PDF 解析器", key: "pdf_parser_mode", type: "select", choices: [{ value: "pymupdf", label: "PyMuPDF" }, { value: "mineru", label: "MinerU" }], fallback: "pymupdf", redraw: true })}${mineru ? field({ label: "MinerU 模型", key: "mineru_model_version", type: "select", choices: [{ value: "pipeline", label: "Pipeline" }, { value: "vlm", label: "VLM" }], fallback: "pipeline" }) : ""}${field({ label: "PDF 下载大小上限（MB）", key: "pdf_download_max_mb_ui", type: "number", min: 1, max: 1024, fallback: Math.floor(Number(configValue("pdf_download_max_bytes", 52428800)) / 1048576) })}</div>`, { icon: "📄" })}${divider()}${section("并发与 LLM 请求池", `<div class="form-grid three">${field({ label: "启用并发处理", key: "concurrency_enabled", type: "checkbox", fallback: false })}${field({ label: "工作线程数", key: "concurrency_workers", type: "number", min: 1, max: 10, fallback: 3 })}${field({ label: "启用 LLM 请求池", key: "llm_request_pool_enabled", type: "checkbox", fallback: true })}${field({ label: "每分钟 LLM 请求数", key: "llm_requests_per_minute", type: "number", min: 1, max: 600, fallback: 30 })}${field({ label: "慢等待日志阈值（秒）", key: "llm_request_pool_log_slow_wait_seconds", type: "number", min: 0, max: 120, step: 0.5, fallback: 5 })}</div>`, { icon: "⚡" })}${divider()}${section("每日研究持久化", `<div class="form-grid two">${field({ label: "启用深度分析", key: "daily_enable_deep_analysis", type: "checkbox", fallback: true })}${field({ label: "SQLite 数据库路径", key: "daily_research_db_path", fallback: "data/daily_research/daily_research.db" })}</div>`, { icon: "💾" })}${divider()}${section("报告与自动更新", `<div class="form-grid two">${field({ label: "记录 Token 使用", key: "token_tracking_enabled", type: "checkbox", fallback: true })}${field({ label: "自动检查更新并发送通知", key: "auto_update_enabled", type: "checkbox", fallback: true })}</div>`, { icon: "📊" })}${divider()}${section("关键词趋势", `${field({ label: "启用关键词跟踪", key: "keyword_tracker_enabled", type: "checkbox", fallback: true, redraw: true })}${tracker ? `<div class="form-grid two">${field({ label: "启用 AI 归一化", key: "keyword_normalization_enabled", type: "checkbox", fallback: true })}${field({ label: "归一化批大小", key: "keyword_normalization_batch_size", type: "number", min: 5, max: 100, fallback: 25 })}${field({ label: "趋势查看天数", key: "keyword_trend_default_days", type: "number", min: 7, max: 365, fallback: 30 })}${field({ label: "柱图 Top N", key: "keyword_chart_top_n", type: "number", min: 5, max: 50, fallback: 15 })}${field({ label: "趋势图 Top N", key: "keyword_trend_top_n", type: "number", min: 3, max: 20, fallback: 5 })}${field({ label: "生成趋势报告", key: "keyword_report_enabled", type: "checkbox", fallback: true })}${field({ label: "趋势报告频率", key: "keyword_report_frequency", type: "select", choices: ["daily", "weekly", "monthly", "always"], fallback: "weekly" })}</div>` : ""}`, { icon: "🧩" })}${divider()}${section("重试与日志", `<div class="form-grid three">${field({ label: "最大重试次数", key: "retry_max_attempts", type: "number", min: 1, max: 10, fallback: 3 })}${field({ label: "最短等待（秒）", key: "retry_min_wait", type: "number", min: 1, max: 60, fallback: 2 })}${field({ label: "最长等待（秒）", key: "retry_max_wait", type: "number", min: 5, max: 300, fallback: 30 })}${field({ label: "运行锁最大年龄（小时）", key: "run_lock_max_age_hours", type: "number", min: 1, max: 168, fallback: 12 })}${field({ label: "日志轮转方式", key: "log_rotation_type", type: "select", choices: [{ value: "time", label: "按时间" }, { value: "size", label: "按大小" }], fallback: "time" })}${field({ label: "日志保留天数", key: "log_keep_days", type: "number", min: 1, max: 365, fallback: 30 })}</div>`, { icon: "♻️" })}${divider()}${renderProxySettings()}`;
}

async function renderAdvancedPage(_token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}${renderAdvanced()}`;
  bindCommon(root);
}

function webdavSettings() {
  const enabled = Boolean(configValue("webdav_enabled", false));
  const backupEnabled = Boolean(configValue("backup_enabled", true));
  const database = configValue("daily_research_db_path", "data/daily_research/daily_research.db").split("/").pop();
  return `${section("配置导出", `<p class="hint-text">导出当前 config.json 与 .env。导出文件含凭据，请妥善保存。</p><button id="config-export" class="secondary-button">导出配置</button>`, { icon: "📦" })}${divider()}${section("WebDAV", `${field({ label: "启用 WebDAV 同步", key: "webdav_enabled", type: "checkbox", fallback: false, redraw: true })}${enabled ? `<div class="form-grid two">${field({ label: "WebDAV URL", key: "WEBDAV_URL", scope: "env", placeholder: "https://dav.example.com/dav/" })}${field({ label: "用户名", key: "WEBDAV_USERNAME", scope: "env" })}${field({ label: "密码", key: "WEBDAV_PASSWORD", scope: "env", type: "secret" })}</div><div class="action-row"><button class="secondary-button" data-webdav="test">测试连接</button><button class="secondary-button" data-webdav="upload">上传</button><button class="secondary-button" data-webdav="download">下载</button><span id="webdav-result" class="inline-result"></span></div><h3>同步设置</h3><div class="form-grid two">${field({ label: "远程目录", key: "webdav_remote_path", fallback: "/arxiv-daily-researcher/" })}${field({ label: "同步时机", key: "webdav_sync_mode", type: "select", choices: [{ value: "manual", label: "手动" }, { value: "scheduled", label: "定时" }, { value: "after_report", label: "报告完成后" }], fallback: "after_report", redraw: true })}${configValue("webdav_sync_mode", "after_report") === "scheduled" ? field({ label: "Cron 表达式", key: "webdav_cron_schedule", fallback: "0 23 * * *" }) : ""}</div><h3>同步范围</h3><div class="form-grid two">${field({ label: "配置文件", key: "webdav_sync_configs", type: "checkbox", fallback: true })}${field({ label: `历史数据（${database}）`, key: "webdav_sync_history", type: "checkbox", fallback: true })}${field({ label: "关键词数据", key: "webdav_sync_keywords", type: "checkbox", fallback: true })}${field({ label: "报告文件", key: "webdav_sync_reports", type: "checkbox", fallback: false })}</div>` : '<p class="hint-text">启用后可展开连接凭据、同步设置和同步范围。</p>'}`, { icon: "☁️" })}${divider()}${section("本地备份", `${field({ label: "启用自动备份", key: "backup_enabled", type: "checkbox", fallback: true, redraw: true })}${backupEnabled ? `<div class="form-grid two">${field({ label: "本地保存天数（0 永久保存）", key: "backup_local_retention_days", type: "number", min: 0, fallback: 7 })}${field({ label: "当天最多数量（0 不限）", key: "backup_local_same_day_max_count", type: "number", min: 0, fallback: 0 })}</div>` : ""}<div class="action-row"><button id="backup-create" class="primary-button">生成本地备份</button><button id="backup-export" class="secondary-button">导出备份</button></div><div class="action-row"><label class="file-button">导入备份<input id="backup-file" type="file" accept=".zip,.gz,.db" hidden /></label><button id="backup-restore" class="secondary-button" disabled>上传并恢复</button><span id="backup-result" class="inline-result"></span></div><div id="backup-list"><div class="loading">正在读取备份列表…</div></div>`, { icon: "🗄️" })}`;
}

async function renderBackupSync(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}${webdavSettings()}`;
  bindCommon(root);
  $("#config-export", root).addEventListener("click", () => { window.location.assign("/api/configuration/export"); });
  $$("[data-webdav]", root).forEach((button) => button.addEventListener("click", async () => {
    try { await saveAll(false); const result = await api("/api/webdav", { method: "POST", body: { operation: button.dataset.webdav } }); $("#webdav-result").textContent = result.ok ? "操作完成" : "连接失败"; $("#webdav-result").className = `inline-result ${result.ok ? "success" : "error"}`; } catch (error) { toast(error.message, "error"); }
  }));
  $("#backup-create", root).addEventListener("click", async () => { try { await saveAll(false); const result = await api("/api/backups/create", { method: "POST", body: {} }); toast(result.created ? `已创建备份：${result.name}` : `未创建备份：${result.reason || "未知原因"}`, result.created ? "success" : "error"); renderPage(); } catch (error) { toast(error.message, "error"); } });
  $("#backup-export", root).addEventListener("click", () => { window.location.assign("/api/backups/export"); });
  $("#backup-file", root).addEventListener("change", (event) => { $("#backup-restore").disabled = !event.target.files?.[0]; });
  $("#backup-restore", root).addEventListener("click", async () => {
    const file = $("#backup-file").files?.[0]; if (!file) return;
    if (!window.confirm("确认恢复该 SQLite 备份？当前数据库会被归档后替换。")) return;
    try { const result = await api("/api/backups/restore", { method: "POST", body: file, headers: { "X-File-Name": file.name } }); $("#backup-result").textContent = `已恢复：${result.source_member}`; $("#backup-result").className = "inline-result success"; renderPage(); } catch (error) { $("#backup-result").textContent = error.message; $("#backup-result").className = "inline-result error"; }
  });
  try {
    const backups = await api("/api/backups"); if (token !== state.renderToken) return;
    $("#backup-list").innerHTML = pagedTable("backups", [{ label: "文件名", key: "name" }, { label: "大小", value: (row) => `${Math.round(Number(row.size_bytes) / 1024)} KB` }, { label: "时间", value: (row) => formatTime(row.modified_at) }], backups.items || [], { empty: "暂无本地备份" }); bindPagers(root);
  } catch (error) { $("#backup-list").innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`; }
}

function importSummary(summary) {
  if (!summary) return '<p class="empty-state">尚无旧历史导入记录。</p>';
  const metric = (label, key, fallback = 0) => ({ label, value: formatNumber(summary[key] ?? fallback), help: "" });
  const missing = ["missing_cards", "missing_tldr", "missing_translation", "missing_analysis"].map((key) => Number(summary[key] || 0)).reduce((a, b) => a + b, 0);
  return `<p class="hint-text">完成时间：${escapeHtml(formatTime(summary.finished_at))} · ${summary.full_repair_enabled ? "完整导入流程" : "仅导入已有 HTML 论文"}</p>${metrics([metric("扫描报告", "reports_scanned"), metric("导入论文卡", "cards_selected", summary.cards_found), metric("写入投递记录", "delivered_ledger_rows"), metric("补充任务", "backlog_queued")])}${missing ? `<p class="issue-box">仍有 ${missing} 项历史数据待补全；可运行“补全历史数据”。</p>` : ""}`;
}

async function renderHistory(token) {
  const root = $("#page-root");
  const fullRepair = Boolean(configValue("legacy_import_full_repair_enabled", false));
  root.innerHTML = `${pageHeader()}<div class="loading">正在读取历史维护状态…</div>`;
  const data = await api("/api/history");
  if (token !== state.renderToken) return;
  const status = data.status || {};
  const tasks = data.tasks || [];
  root.innerHTML = `${pageHeader()}${section("旧版本历史导入", `<p class="hint-text">导入旧版本 HTML 报告中的论文。SQLite 是历史论文数据的唯一索引；HTML 解析与新报告生成都会同步写入。</p>${field({ label: "启用完整补全流程", key: "legacy_import_full_repair_enabled", type: "checkbox", fallback: false, redraw: true })}<p class="hint-text">${fullRepair ? "开启后会在导入后安排缺失字段补全、遗漏扫描和补充报告。" : "关闭后仅导入 HTML 已包含的论文，避免新的每日研究重复处理。"}</p><div class="action-row"><button id="history-import" class="primary-button" ${status.can_start ? "" : "disabled"}>读取旧历史 <span>→</span></button><button id="history-repair" class="secondary-button">补全历史数据</button><button id="history-omission" class="secondary-button">扫描历史遗漏</button></div>`, { icon: "📜" })}${divider()}${section("状态面板", `${statusCard(status, { kind: "history" })}${divider()}<h3>最近一次导入结果</h3>${importSummary(data.last_import)}${divider()}<h3>未完成任务</h3>${pagedTable("history-tasks", [{ label: "任务", value: (row) => ({ legacy_import: "旧版本历史导入", history_data_repair: "历史数据补全", history_omission_scan: "历史遗漏扫描" }[row.mode] || row.mode) }, { label: "状态", key: "state" }, { label: "时间", value: (row) => formatTime(row.updated_at || row.created_at) }, { label: "问题摘要", value: (row) => row.issue || "—" }, { label: "操作", html: (row) => ["failed", "rejected", "interrupted", "skipped_busy"].includes(row.state) ? `<button class="secondary-button compact-button" data-history-retry="${escapeAttribute(row.request_id)}">重试</button>` : "—" }], tasks, { empty: "没有未完成的历史维护任务。" })}`, { icon: "📊" })}`;
  bindCommon(root);
  $("#history-import").addEventListener("click", async () => {
    try { await api("/api/tasks/legacy_import", { method: "POST", body: { args: { full_repair: Boolean(configValue("legacy_import_full_repair_enabled", false)) } } }); toast("旧历史导入已加入闲时队列。 "); renderPage(); } catch (error) { toast(error.message, "error"); }
  });
  $("#history-repair").addEventListener("click", async () => { try { await api("/api/tasks/history_data_repair", { method: "POST", body: { args: {} } }); toast("历史数据补全已加入队列。 "); renderPage(); } catch (error) { toast(error.message, "error"); } });
  $("#history-omission").addEventListener("click", async () => { try { await api("/api/tasks/history_omission_scan", { method: "POST", body: { args: {} } }); toast("历史遗漏扫描已加入队列。 "); renderPage(); } catch (error) { toast(error.message, "error"); } });
  $$('[data-history-retry]', root).forEach((button) => button.addEventListener("click", async () => { try { await api(`/api/history/${encodeURIComponent(button.dataset.historyRetry)}/retry`, { method: "POST", body: {} }); toast("历史维护任务已重新加入队列。 "); renderPage(); } catch (error) { toast(error.message, "error"); } }));
  if (status.is_active) scheduleRefresh("history", () => renderPage(), 5000);
}

function healthTable(key, kind, rows) {
  if (kind === "llm") return pagedTable(key, [
    { label: "模型", key: "model" }, { label: "角色", value: (row) => (row.roles || []).join(" / ") || "—" }, { label: "最近状态", key: "last_status" }, { label: "最近事件", value: (row) => formatTime(row.last_event_at) }, { label: "成功率", value: (row) => formatPercent(row.success_rate) }, { label: "最近失败信息", value: (row) => row.last_error || "—" },
  ], rows, { empty: "所选范围内没有 LLM 调用记录。" });
  return pagedTable(key, [
    { label: "来源", value: (row) => row.name || row.source }, { label: "最近状态", key: "last_status" }, { label: "最近事件", value: (row) => formatTime(row.last_event_at) }, { label: "成功率", value: (row) => formatPercent(row.success_rate) }, { label: "最近失败信息", value: (row) => row.last_error || "—" },
  ], rows, { empty: "所选范围内没有数据源请求记录。" });
}

function diagnosticsRangeControl(id, value) {
  return `<label class="form-field narrow-field"><span>查看范围</span><select id="${escapeAttribute(id)}"><option value="3" ${value === "3" ? "selected" : ""}>近 3 天</option><option value="7" ${value === "7" ? "selected" : ""}>近 7 天</option><option value="14" ${value === "14" ? "selected" : ""}>近 14 天</option><option value="30" ${value === "30" ? "selected" : ""}>近 30 天</option><option value="all" ${value === "all" ? "selected" : ""}>全部</option></select></label>`;
}

async function renderDiagnostics(token) {
  const root = $("#page-root");
  const ranges = { runs: "7", llm: "7", sources: "7", ...(state.pageData.diagnosticsRanges || {}) };
  root.innerHTML = `${pageHeader()}<div class="loading">正在读取运行诊断…</div>`;
  const [runData, llmData, sourceData] = await Promise.all([
    api(`/api/diagnostics?days=${ranges.runs}`),
    api(`/api/diagnostics?days=${ranges.llm}`),
    api(`/api/diagnostics?days=${ranges.sources}`),
  ]);
  if (token !== state.renderToken) return;
  root.innerHTML = `${pageHeader()}${section("运行诊断", `${diagnosticsRangeControl("diagnostics-range", ranges.runs)}<p class="hint-text">仅显示每日研究和过去日报最近运行；旧历史导入状态位于“历史维护”。</p>${pagedTable("operational-runs", [{ label: "类型", value: (row) => row.run_kind === "backfill" || row.run_kind === "backfill_run" ? "过去日报" : "每日研究" }, { label: "开始", value: (row) => formatTime(row.started_at) }, { label: "完成", value: (row) => formatTime(row.completed_at) }, { label: "状态", key: "status" }, { label: "论文数", key: "total_papers" }, { label: "问题摘要", value: (row) => row.error_summary || "—" }], runData.runs || [], { empty: "所选范围内没有每日研究或过去日报运行记录。" })}`, { icon: "🩺" })}${divider()}${section("LLM 健康", `${diagnosticsRangeControl("llm-range", ranges.llm)}<p class="hint-text">覆盖所有任务中的真实 LLM 调用，包括旧历史导入。</p>${healthTable("llm-health", "llm", llmData.llm || [])}`, { icon: "🧠" })}${divider()}${section("数据源健康", `${diagnosticsRangeControl("source-range", ranges.sources)}<p class="hint-text">覆盖所有任务中的真实数据源请求，包括旧历史导入。</p>${healthTable("source-health", "source", sourceData.sources || [])}`, { icon: "🌐" })}`;
  bindCommon(root);
  [["#diagnostics-range", "runs"], ["#llm-range", "llm"], ["#source-range", "sources"]].forEach(([selector, key]) => $(selector, root).addEventListener("change", (event) => {
    state.pageData.diagnosticsRanges = { ...ranges, [key]: event.target.value };
    renderPage();
  }));
}

function localDateKey(value) {
  const date = value instanceof Date ? value : new Date(value);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function tokenHeatmap(rows) {
  const values = new Map((rows || []).map((row) => [String(row.date || ""), Number(row.total || 0)]));
  const max = Math.max(0, ...Array.from(values.values()));
  const today = new Date();
  const cells = [];
  for (let offset = 364; offset >= 0; offset -= 1) {
    const day = new Date(today.getFullYear(), today.getMonth(), today.getDate() - offset);
    const key = localDateKey(day);
    const total = values.get(key) || 0;
    const ratio = max ? total / max : 0;
    const level = total === 0 ? 0 : ratio <= .25 ? 1 : ratio <= .5 ? 2 : ratio <= .75 ? 3 : 4;
    cells.push(`<i class="heat-cell level-${level}" title="${escapeAttribute(`${key} · ${formatNumber(total)} Token`)}"></i>`);
  }
  return `<div class="heatmap-wrap"><div class="heatmap" aria-label="近一年 Token 使用热力图">${cells.join("")}</div><div class="heatmap-legend"><span>少</span><i class="heat-cell level-0"></i><i class="heat-cell level-1"></i><i class="heat-cell level-2"></i><i class="heat-cell level-3"></i><i class="heat-cell level-4"></i><span>多</span></div></div>`;
}

function tokenTrendChart(rows) {
  const values = (rows || []).filter((row) => row && row.date).slice().sort((left, right) => String(left.date).localeCompare(String(right.date)));
  if (!values.length) return '<p class="empty-state">所选范围内没有 Token 使用记录。</p>';
  const width = 900; const height = 250; const left = 42; const right = 16; const top = 28; const bottom = 35;
  const maximum = Math.max(1, ...values.flatMap((row) => [Number(row.prompt || 0), Number(row.completion || 0)]));
  const x = (index) => values.length === 1 ? (left + width - right) / 2 : left + index * (width - left - right) / (values.length - 1);
  const y = (value) => top + (height - top - bottom) * (1 - Number(value || 0) / maximum);
  const points = (key) => values.map((row, index) => `${x(index).toFixed(1)},${y(row[key]).toFixed(1)}`).join(" ");
  const labels = Array.from({ length: Math.min(6, values.length) }, (_, index) => Math.round(index * (values.length - 1) / Math.max(1, Math.min(6, values.length) - 1)));
  const labelText = labels.map((index) => `<text x="${x(index).toFixed(1)}" y="${height - 12}" text-anchor="middle">${escapeHtml(String(values[index].date).slice(5))}</text>`).join("");
  const grid = [0, .25, .5, .75, 1].map((ratio) => `<line x1="${left}" x2="${width - right}" y1="${y(maximum * ratio).toFixed(1)}" y2="${y(maximum * ratio).toFixed(1)}" />`).join("");
  return `<div class="trend-chart"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Token 使用趋势"><g class="trend-grid">${grid}</g><polyline class="trend-line prompt" points="${points("prompt")}" /><polyline class="trend-line completion" points="${points("completion")}" /><g class="trend-labels">${labelText}</g><g class="trend-legend"><rect x="${left}" y="5" width="11" height="11" class="prompt"/><text x="${left + 17}" y="15">输入 Token</text><rect x="${left + 100}" y="5" width="11" height="11" class="completion"/><text x="${left + 117}" y="15">输出 Token</text></g></svg></div>`;
}

async function renderAnalytics(token) {
  const root = $("#page-root");
  const range = state.pageData.analyticsRange || "30";
  root.innerHTML = `${pageHeader()}<div class="loading">正在读取 Token 使用记录…</div>`;
  const data = await api(`/api/analytics?days=${range}`);
  if (token !== state.renderToken) return;
  const control = `<label class="form-field narrow-field"><span>查看范围</span><select id="analytics-range"><option value="7" ${range === "7" ? "selected" : ""}>近 7 天</option><option value="30" ${range === "30" ? "selected" : ""}>近 30 天</option><option value="90" ${range === "90" ? "selected" : ""}>近 90 天</option><option value="365" ${range === "365" ? "selected" : ""}>近 365 天</option><option value="all" ${range === "all" ? "selected" : ""}>全部</option></select></label>`;
  const totals = (data.daily || []).reduce((sum, item) => sum + Number(item.total || 0), 0);
  const today = (data.heatmap_daily || []).find((row) => row.date === localDateKey(new Date())) || { prompt: 0, completion: 0 };
  const recentCutoff = new Date(); recentCutoff.setDate(recentCutoff.getDate() - 29);
  const lastThirty = (data.heatmap_daily || []).filter((row) => new Date(`${row.date}T00:00:00`) >= recentCutoff).reduce((sum, row) => sum + Number(row.total || 0), 0);
  root.innerHTML = `${pageHeader()}${section("Token 使用概览", `${control}${metrics([{ label: "今日输入 Token", value: formatNumber(today.prompt), help: "今日已记录调用" }, { label: "今日输出 Token", value: formatNumber(today.completion), help: "今日已记录调用" }, { label: "近 30 天 Token", value: formatNumber(lastThirty), help: "输入与输出合计" }, { label: "当前范围累计", value: formatNumber(totals), help: "${formatNumber((data.daily || []).length)} 个有记录日期" }])}`, { icon: "📊" })}${divider()}${section("近一年使用热力图", tokenHeatmap(data.heatmap_daily || []), { icon: "🗓" })}${divider()}${section("使用趋势", `${tokenTrendChart(data.daily || [])}<p class="hint-text">当前范围：输入 ${formatNumber((data.daily || []).reduce((sum, row) => sum + Number(row.prompt || 0), 0))} · 输出 ${formatNumber((data.daily || []).reduce((sum, row) => sum + Number(row.completion || 0), 0))}</p>`, { icon: "📈" })}${divider()}${section("按模型", pagedTable("analytics-models", [{ label: "模型", key: "model" }, { label: "输入", value: (row) => formatNumber(row.prompt) }, { label: "输出", value: (row) => formatNumber(row.completion) }, { label: "总计", value: (row) => formatNumber(row.total) }], data.models || [], { empty: "暂无模型使用记录。" }), { icon: "🧠" })}${divider()}${section("按日期", pagedTable("analytics-daily", [{ label: "日期", key: "date" }, { label: "输入", value: (row) => formatNumber(row.prompt) }, { label: "输出", value: (row) => formatNumber(row.completion) }, { label: "总计", value: (row) => formatNumber(row.total) }, { label: "运行次数", key: "runs" }], data.daily || [], { empty: "暂无每日使用记录。" }), { icon: "📅" })}`;
  bindCommon(root);
  $("#analytics-range", root).addEventListener("change", (event) => { state.pageData.analyticsRange = event.target.value; renderPage(); });
}

async function renderLogs(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div class="loading">正在读取日志列表…</div>`;
  const data = await api("/api/logs");
  if (token !== state.renderToken) return;
  let selected = state.pageData.selectedLog;
  if (!selected || !(data.items || []).some((item) => item.id === selected)) selected = data.items?.[0]?.id || "";
  state.pageData.selectedLog = selected;
  root.innerHTML = `${pageHeader()}${section("日志列表", pagedTable("logs", [{ label: "日志", html: (row) => `<button class="report-link ${row.id === selected ? "is-selected" : ""}" data-open-log="${escapeAttribute(row.id)}">${escapeHtml(row.name)}</button>` }, { label: "分组", key: "group" }, { label: "修改时间", value: (row) => formatTime(row.modified_at) }, { label: "大小", value: (row) => `${Math.round(Number(row.size_bytes) / 1024)} KB` }], data.items || [], { empty: "尚无运行日志。" }), { icon: "🧾" })}${selected ? `<div id="log-content" class="loading">正在读取日志内容…</div>` : ""}`;
  bindCommon(root);
  $$('[data-open-log]', root).forEach((button) => button.addEventListener("click", () => { state.pageData.selectedLog = button.dataset.openLog; renderPage(); }));
  if (selected) {
    try {
      const log = await api(`/api/logs/${encodeURIComponent(selected)}`); if (token !== state.renderToken) return;
      $("#log-content").outerHTML = section("日志内容", `<p class="hint-text">${escapeHtml(log.name)}${log.truncated ? " · 仅显示最后 2,000 行" : ""}</p><pre class="log-viewer">${escapeHtml(log.content)}</pre>`, { icon: "📋" });
    } catch (error) { $("#log-content").innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`; }
  }
}

async function renderAccounts(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div class="loading">正在读取账户列表…</div>`;
  const data = await api("/api/accounts");
  if (token !== state.renderToken) return;
  if (!data.enabled) {
    root.innerHTML = `${pageHeader()}${section("账户管理", '<p class="info-box">当前已跳过登录，仅建议在可信内网使用。可在 .env 设置 WEBUI_AUTH_ENABLED=true 后重新启用账户验证。</p>', { icon: "👤" })}`;
    return;
  }
  const ownForm = `<form id="own-password-form" class="stack-form compact-form"><h3>修改我的密码</h3><label>当前密码<input name="current_password" type="password" required /></label><label>新密码<input name="new_password" type="password" minlength="6" required /></label><label>确认新密码<input name="password_confirmation" type="password" minlength="6" required /></label><button class="primary-button" type="submit">保存密码</button></form>`;
  const ownerForms = data.is_owner ? `<div class="form-grid two"><form id="add-account-form" class="stack-form compact-form"><h3>新增管理员</h3><label>用户名<input name="username" required /></label><label>密码<input name="password" type="password" minlength="6" required /></label><label>确认密码<input name="password_confirmation" type="password" minlength="6" required /></label><button class="secondary-button" type="submit">新增账户</button></form><form id="reset-account-form" class="stack-form compact-form"><h3>重置管理员密码</h3><label>账户<select name="username">${data.items.filter((item) => !item.current).map((item) => `<option value="${escapeAttribute(item.username)}">${escapeHtml(item.username)}</option>`).join("")}</select></label><label>新密码<input name="new_password" type="password" minlength="6" required /></label><label>确认密码<input name="password_confirmation" type="password" minlength="6" required /></label><button class="secondary-button" type="submit">重置密码</button></form></div>${data.items.filter((item) => item.role !== "所有者").length ? `<div class="account-delete-list"><h3>删除管理员</h3>${data.items.filter((item) => item.role !== "所有者").map((item) => `<button class="danger-button compact-button" data-delete-account="${escapeAttribute(item.username)}">删除 ${escapeHtml(item.username)}</button>`).join(" ")}</div>` : ""}` : '<p class="hint-text">普通管理员可以修改自己的密码；账户所有者可管理其他管理员。</p>';
  root.innerHTML = `${pageHeader()}${section("账户列表", `${pagedTable("accounts", [{ label: "用户名", key: "username" }, { label: "角色", key: "role" }, { label: "当前账户", value: (row) => row.current ? "当前" : "—" }], data.items || [], { empty: "暂无账户" })}`, { icon: "👥" })}${divider()}${section("账户操作", `${ownForm}${divider()}${ownerForms}`, { icon: "🔐" })}`;
  bindCommon(root);
  bindAccountForm("#own-password-form", "/api/accounts/change-password", () => toast("密码已修改。", "success"));
  bindAccountForm("#add-account-form", "/api/accounts/add", () => { toast("管理员账户已创建。", "success"); renderPage(); });
  bindAccountForm("#reset-account-form", "/api/accounts/reset", () => toast("管理员密码已重置。", "success"));
  $$('[data-delete-account]', root).forEach((button) => button.addEventListener("click", async () => {
    if (!window.confirm(`确认删除管理员 ${button.dataset.deleteAccount}？`)) return;
    try { await api("/api/accounts/delete", { method: "POST", body: { username: button.dataset.deleteAccount } }); toast("管理员已删除。", "success"); renderPage(); } catch (error) { toast(error.message, "error"); }
  }));
}

function bindAccountForm(selector, endpoint, onSuccess) {
  const form = $(selector); if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(form).entries());
    try { await api(endpoint, { method: "POST", body: payload }); form.reset(); onSuccess(); } catch (error) { toast(error.message, "error"); }
  });
}

function normalizeForSave() {
  const config = { ...(state.settings?.config || {}), ...state.draft.config };
  // UI-only helpers map back to the stable config.json contract.
  if (Object.prototype.hasOwnProperty.call(config, "pdf_download_max_mb_ui")) {
    config.pdf_download_max_bytes = Math.max(1, Number(config.pdf_download_max_mb_ui) || 50) * 1024 * 1024;
    delete config.pdf_download_max_mb_ui;
  }
  const sourceState = state.pageData.sources;
  if (sourceState) {
    const definitions = [
      ...sourceState.builtins.filter((code) => code !== "prl").map(sourceDefinition).filter(Boolean),
      ...sourceState.custom,
    ];
    const enabled = [];
    if (sourceState.arxiv) enabled.push("arxiv");
    if (sourceState.extraEnabled) {
      if (sourceState.builtins.includes("prl")) enabled.push("prl");
      enabled.push(...definitions.map((item) => item.code));
    }
    const validDomains = new Set(arxivCategories().map((item) => item.code));
    config.enabled_sources = enabled;
    // Streamlit drops legacy/invalid category values during collection rather
    // than persisting a source filter that arXiv cannot honour.
    config.domains = sourceState.domains.filter((code) => validDomains.has(code));
    config.extra_sources_enabled = Boolean(sourceState.extraEnabled);
    config.extra_source_definitions = definitions;
  }
  if (Object.prototype.hasOwnProperty.call(config, "trend_output_html") || Object.prototype.hasOwnProperty.call(config, "trend_output_md")) {
    const formats = [];
    if (config.trend_output_md !== false) formats.push("markdown");
    if (config.trend_output_html !== false) formats.push("html");
    config.trend_output_formats = formats;
    delete config.trend_output_html;
    delete config.trend_output_md;
  }
  config.score_strategy_explicit = true;
  return config;
}

async function saveAll(showMessage = true) {
  if (!state.settings) return null;
  try {
    const result = await api("/api/settings", { method: "PUT", body: { config: normalizeForSave(), env: state.draft.env, clear_env: Array.from(state.draft.clearEnv) } });
    state.settings = result;
    state.draft = { config: {}, env: {}, clearEnv: new Set() };
    state.pageData.sources = undefined;
    if (showMessage) toast("配置已保存。", "success");
    return result;
  } catch (error) {
    if (showMessage) toast(error.message, "error");
    throw error;
  }
}

function bindCommon(root = document) {
  bindFields(root);
  bindPagers(root);
  $$('[data-refresh-status]', root).forEach((button) => button.addEventListener("click", () => renderPage()));
  $$('[data-start-task]', root).forEach((button) => button.addEventListener("click", async () => {
    try { await api(`/api/tasks/${encodeURIComponent(button.dataset.startTask)}`, { method: "POST", body: { args: {} } }); toast("任务已加入队列。", "success"); renderPage(); } catch (error) { toast(error.message, "error"); }
  }));
  $$('[data-stop-task]', root).forEach((button) => button.addEventListener("click", async () => {
    if (!window.confirm("确认停止当前任务？已完成的阶段会保留，未完成论文将留队等待重试。")) return;
    try { await api("/api/tasks/stop", { method: "POST", body: {} }); toast("已发送停止请求。", "success"); renderPage(); } catch (error) { toast(error.message, "error"); }
  }));
}

const PAGE_RENDERERS = {
  daily_research: renderDaily,
  past_daily: renderPastDaily,
  trend_tasks: renderTrend,
  reports: renderReports,
  favorites: renderFavorites,
  paper_search: renderPaperSearch,
  keywords: renderKeywords,
  data_sources: renderDataSources,
  scoring: renderScoring,
  api: renderApi,
  notifications: renderNotificationsPage,
  advanced: renderAdvancedPage,
  accounts: renderAccounts,
  backup_sync: renderBackupSync,
  history_tasks: renderHistory,
  diagnostics: renderDiagnostics,
  analytics: renderAnalytics,
  logs: renderLogs,
};

async function renderPage() {
  clearTimers();
  if (state.page !== "reports") {
    state.reportMarkAbortController?.abort();
    state.reportMarkAbortController = null;
  }
  setLocation();
  const token = ++state.renderToken;
  const renderer = PAGE_RENDERERS[state.page] || renderDaily;
  try { await renderer(token); } catch (error) { if (token === state.renderToken) $("#page-root").innerHTML = `${pageHeader()}<section class="section-card"><p class="error-message">${escapeHtml(error.message)}</p><button class="secondary-button" id="page-retry">重试</button></section>`; $("#page-retry")?.addEventListener("click", renderPage); }
}

function showApp() {
  $("#auth").hidden = true;
  $("#app").hidden = false;
  renderNavigation();
  $("#file-status").textContent = ".env 与 config.json 已加载";
}

function showAuth(auth) {
  $("#app").hidden = true;
  $("#auth").hidden = false;
  $("#auth-error").textContent = "";
  const setup = $("#setup-form"); const login = $("#login-form");
  if (!auth.enabled) {
    $("#auth-title").textContent = "内网模式";
    $("#auth-hint").textContent = "登录验证已关闭，正在进入面板。";
    setup.hidden = true; login.hidden = true;
    return;
  }
  if (!auth.configured) {
    $("#auth-title").textContent = "初始化管理员账户";
    $("#auth-hint").textContent = "创建本地管理员账户，或在可信内网中跳过登录。";
    setup.hidden = false; login.hidden = true;
  } else {
    $("#auth-title").textContent = "登录管理面板";
    $("#auth-hint").textContent = "使用本机配置的管理员账户登录。";
    setup.hidden = true; login.hidden = false;
  }
}

async function loadSettings() {
  state.settings = await api("/api/settings");
  $("#version-label").textContent = "现代管理面板 · 预览";
}

async function loginSubmit(event) {
  event.preventDefault();
  try {
    await api("/api/auth/login", { method: "POST", body: { username: $("#login-username").value, password: $("#login-password").value } });
    $("#login-password").value = "";
    state.auth = await api("/api/auth/status"); await loadSettings(); showApp(); renderPage();
  } catch (error) { $("#auth-error").textContent = error.message; }
}

async function setupSubmit(event) {
  event.preventDefault();
  try {
    await api("/api/auth/setup", { method: "POST", body: { username: $("#setup-username").value, password: $("#setup-password").value, password_confirmation: $("#setup-password-confirmation").value } });
    state.auth = await api("/api/auth/status"); await loadSettings(); showApp(); renderPage();
  } catch (error) { $("#auth-error").textContent = error.message; }
}

async function skipAuth() {
  if (!window.confirm("确认跳过登录？仅建议在可信内网使用。")) return;
  try { await api("/api/auth/setup", { method: "POST", body: { action: "skip" } }); state.auth = await api("/api/auth/status"); await loadSettings(); showApp(); renderPage(); } catch (error) { $("#auth-error").textContent = error.message; }
}

async function logout() {
  await api("/api/auth/logout", { method: "POST", body: {} }).catch(() => null);
  clearTimers(); window.location.reload();
}

async function initialize() {
  readLocation();
  $("#login-form").addEventListener("submit", loginSubmit);
  $("#setup-form").addEventListener("submit", setupSubmit);
  $("#skip-auth-button").addEventListener("click", skipAuth);
  $("#logout-button").addEventListener("click", logout);
  $("#save-button").addEventListener("click", () => saveAll(true));
  $("#reload-button").addEventListener("click", async () => { try { await loadSettings(); state.draft = { config: {}, env: {}, clearEnv: new Set() }; state.pageData.sources = undefined; toast("配置已重新加载。", "success"); renderPage(); } catch (error) { toast(error.message, "error"); } });
  $("#restart-worker-button").addEventListener("click", async () => { if (!window.confirm("确认请求重启研究容器？正在运行的任务会由容器重启策略处理。")) return; try { await api("/api/system/restart-worker", { method: "POST", body: {} }); toast("已发送研究容器重启请求。", "success"); } catch (error) { toast(error.message, "error"); } });
  window.addEventListener("hashchange", () => { readLocation(); renderNavigation(); renderPage(); });
  try {
    state.auth = await api("/api/auth/status");
    if (!state.auth.authenticated) { showAuth(state.auth); return; }
    await loadSettings(); showApp(); renderPage();
  } catch (error) { showAuth({ configured: false, enabled: true }); $("#auth-hint").textContent = error.message; }
}

initialize();

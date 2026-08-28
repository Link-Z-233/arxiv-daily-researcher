const state = {
  status: null,
  timer: null,
  refreshing: false,
};

const $ = (selector) => document.querySelector(selector);

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(date);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "请求未完成");
  return payload;
}

function message(text = "", isError = false) {
  const node = $("#action-message");
  node.textContent = text;
  node.classList.toggle("error", isError);
}

function showApp() {
  $("#auth").hidden = true;
  $("#app").hidden = false;
}

function showAuth(status) {
  $("#app").hidden = true;
  $("#auth").hidden = false;
  const hint = $("#auth-hint");
  if (!status.configured) {
    hint.textContent = "请先在现有 Streamlit 面板完成账户初始化，再使用此预览界面登录。";
    $("#login-form").hidden = true;
  } else {
    hint.textContent = "使用与 Streamlit 面板相同的账户登录。";
    $("#login-form").hidden = false;
  }
}

function setProgress(task) {
  const current = Number.isInteger(task.current) ? task.current : null;
  const total = Number.isInteger(task.total) && task.total > 0 ? task.total : null;
  const wrap = $("#progress-wrap");
  if (current === null || total === null) {
    wrap.hidden = true;
    return;
  }
  const percent = Math.max(0, Math.min(100, Math.round((current / total) * 100)));
  $("#progress-value").style.width = `${Math.max(4, percent)}%`;
  $("#progress-caption").textContent = `当前进度 ${current} / ${total} · ${percent}%`;
  wrap.hidden = false;
}

function renderStatus(data) {
  state.status = data;
  const task = data.task;
  const orb = $("#status-orb");
  orb.className = `status-orb ${task.state || "idle"}`;
  $("#task-label").textContent = task.label || "未知状态";
  $("#task-phase").textContent = task.phase || "";
  const started = formatTime(task.started_at);
  $("#task-started").textContent = started ? `开始于 ${started}` : "";
  const detail = $("#task-detail");
  detail.textContent = task.detail || "";
  detail.hidden = !task.detail;
  setProgress(task);

  $("#pending-count").textContent = String(data.queue.pending ?? 0);
  $("#retry-count").textContent = String(data.queue.retry ?? 0);
  const last = data.last_run;
  $("#last-run-count").textContent = last ? String(last.total_papers ?? 0) : "—";
  $("#last-run-meta").textContent = last
    ? `${last.status === "completed" ? "已完成" : last.status || "已记录"} · ${formatTime(last.completed_at)}`
    : "尚无记录";

  $("#start-button").disabled = !data.can_start;
  const stop = $("#stop-button");
  stop.hidden = !data.is_active || task.state === "queued";
  updateAutoRefresh();
}

function updateAutoRefresh() {
  const enabled = $("#auto-refresh").checked;
  const active = Boolean(state.status?.is_active);
  if (state.timer) {
    window.clearInterval(state.timer);
    state.timer = null;
  }
  if (enabled && active) state.timer = window.setInterval(loadStatus, 5000);
}

async function loadStatus() {
  if (state.refreshing) return;
  state.refreshing = true;
  $("#refresh-button").disabled = true;
  try {
    renderStatus(await api("/api/daily/status"));
  } catch (error) {
    message(error.message, true);
  } finally {
    state.refreshing = false;
    $("#refresh-button").disabled = false;
  }
}

async function startRun() {
  message();
  $("#start-button").disabled = true;
  try {
    await api("/api/daily/run", { method: "POST", body: "{}" });
    message("每日研究已加入队列，正在等待工作进程接手。");
    await loadStatus();
  } catch (error) {
    message(error.message, true);
    await loadStatus();
  }
}

async function stopRun() {
  if (!window.confirm("确认停止当前任务？已完成的阶段会保留，未完成论文会留队等待重试。")) return;
  message();
  try {
    await api("/api/daily/stop", { method: "POST", body: "{}" });
    message("已发送停止请求，工作进程将在安全检查点结束。");
  } catch (error) {
    message(error.message, true);
  }
  await loadStatus();
}

async function login(event) {
  event.preventDefault();
  const error = $("#login-error");
  error.textContent = "";
  try {
    await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: $("#username").value, password: $("#password").value }),
    });
    $("#password").value = "";
    showApp();
    await loadStatus();
  } catch (err) {
    error.textContent = err.message;
  }
}

async function logout() {
  await api("/api/auth/logout", { method: "POST", body: "{}" }).catch(() => null);
  if (state.timer) window.clearInterval(state.timer);
  window.location.reload();
}

async function initialize() {
  $("#login-form").addEventListener("submit", login);
  $("#refresh-button").addEventListener("click", loadStatus);
  $("#start-button").addEventListener("click", startRun);
  $("#stop-button").addEventListener("click", stopRun);
  $("#logout-button").addEventListener("click", logout);
  $("#auto-refresh").addEventListener("change", updateAutoRefresh);
  try {
    const auth = await api("/api/auth/status");
    if (!auth.authenticated) {
      showAuth(auth);
      return;
    }
    showApp();
    await loadStatus();
  } catch (error) {
    showAuth({ configured: false });
    $("#auth-hint").textContent = error.message;
  }
}

initialize();

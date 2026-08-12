"""运行管理 Tab — 立即运行每日研究、查看运行状态/日志、停止进程。

架构：
  本地模式：直接 subprocess.Popen 启动 main.py，日志写入 logs/manual_*.log。
  Docker 模式：原子写入 data/run/webui_triggers/*.json 请求队列，
               主研究容器 entrypoint.sh 的 trigger_watcher 每 5 秒轮询，
               验证请求后启动 worker（真实 PID 写入 webui_triggered.pid）。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import streamlit as st

from utils.run_lock import is_lock_held
from utils.daily_research_store import DailyResearchStore
from utils.webui_trigger import enqueue_trigger, trigger_directory
from webui.i18n import t

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_LOGS_DIR     = _PROJECT_ROOT / "logs"
_LOCK_DIR     = _PROJECT_ROOT / "data" / "run"
_MAIN_PY      = _PROJECT_ROOT / "main.py"
_TRIGGER_QUEUE_DIR = trigger_directory(_LOCK_DIR.parent)
_TRIGGER_STATUS_DIR = _TRIGGER_QUEUE_DIR / "status"
_DEFAULT_DAILY_DB_PATH = _PROJECT_ROOT / "data" / "daily_research" / "daily_research.db"

_IS_DOCKER_WEBUI = not _MAIN_PY.exists()

# session_state 键（日志查看器）
_LOG_ACTIVE = "rm_log_active_path"    # 当前展示的日志路径（str）
_LOG_CLOSED = "rm_log_viewer_closed"  # 是否关闭内容区


# ─── 工具函数 ────────────────────────────────────────────────────────────────


def _read_pid_from_file(path: Path) -> Optional[int]:
    try:
        content = path.read_text(encoding="utf-8").strip()
        m = re.search(r"PID=(\d+)", content)
        if m:
            return int(m.group(1))
        first = content.splitlines()[0].strip()
        return int(first) if first.isdigit() else None
    except Exception:
        return None


def _is_lock_held(lock_path: Path) -> bool:
    """Ask the OS about the lock instead of trusting a stale/reused PID."""
    try:
        return is_lock_held(lock_path)
    except OSError:
        # If the shared volume is temporarily unreadable, show a conservative
        # state rather than offering a conflicting launch action.
        return True


def _get_lock_files() -> list[Path]:
    """只返回 *.lock 文件（main.py 拥有的任务锁），按修改时间倒序。"""
    if not _LOCK_DIR.exists():
        return []
    files = list(_LOCK_DIR.glob("*.lock"))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def _scan_all_logs() -> dict[str, list[Path]]:
    """
    扫描 logs/ 目录下所有 *.log，按类型分组（最新在前）。

    分组：
      manual  → manual_*.log（面板手动触发）
      daily   → daily_*.log / cron_*.log / startup_*.log（定时/启动触发）
      trend   → trend_*.log
      system  → system*.log / arxiv_researcher*.log
      other   → 其余
    """
    if not _LOGS_DIR.exists():
        return {}

    all_logs = sorted(
        _LOGS_DIR.glob("**/*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    groups: dict[str, list[Path]] = {
        "manual": [], "daily": [], "trend": [], "system": [], "other": [],
    }
    for p in all_logs:
        name = p.name.lower()
        if name.startswith("manual_"):
            groups["manual"].append(p)
        elif name.startswith(("daily_", "cron_", "startup_")):
            groups["daily"].append(p)
        elif name.startswith("trend_"):
            groups["trend"].append(p)
        elif name.startswith(("system", "arxiv_researcher")):
            groups["system"].append(p)
        else:
            groups["other"].append(p)

    return {k: v for k, v in groups.items() if v}


def _read_log_tail(log_path: Path, max_lines: int = 300) -> str:
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        if len(lines) > max_lines:
            lines = [
                f"... (省略前 {len(lines) - max_lines} 行，仅显示最后 {max_lines} 行) ..."
            ] + lines[-max_lines:]
        return "\n".join(lines)
    except Exception as e:
        return f"读取日志失败: {e}"


def _get_all_running_locks() -> list[tuple[Path, Optional[int]]]:
    """Return locks currently held by the worker, with PID only as a hint."""
    running = []
    for f in _get_lock_files():
        pid = _read_pid_from_file(f)
        if _is_lock_held(f):
            running.append((f, pid))
    return running


# ─── 触发文件机制 ─────────────────────────────────────────────────────────────


def _trigger_age_seconds() -> Optional[float]:
    """Return the age of the oldest queued (not already running) request."""
    queued = list(_TRIGGER_QUEUE_DIR.glob("*.json")) if _TRIGGER_QUEUE_DIR.exists() else []
    if not queued:
        return None
    oldest = min(path.stat().st_mtime for path in queued)
    return time.time() - oldest


def _latest_trigger_status() -> Optional[dict]:
    """Load the newest worker-owned trigger status for concise UI feedback."""
    if not _TRIGGER_STATUS_DIR.exists():
        return None
    status_files = list(_TRIGGER_STATUS_DIR.glob("*.json"))
    if not status_files:
        return None
    try:
        import json

        latest = max(status_files, key=lambda path: path.stat().st_mtime)
        payload = json.loads(latest.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None


def _enqueue_worker_trigger(mode: str, **args) -> tuple[bool, str]:
    """Queue a validated request for the worker container without shell input."""
    try:
        request_path = enqueue_trigger(_LOCK_DIR.parent, mode, **args)
        return True, request_path.stem
    except Exception as e:
        return False, str(e)


def _render_run_control() -> None:
    trigger_age   = _trigger_age_seconds()
    trigger_stale   = trigger_age is not None and trigger_age > 30
    trigger_pending = trigger_age is not None and not trigger_stale

    active_locks = _get_all_running_locks()
    is_running   = bool(active_locks)

    if trigger_stale:
        st.warning(f"⚠️ {t('rm_trigger_stale').format(n=int(trigger_age))}")
        if _IS_DOCKER_WEBUI:
            st.caption("为避免丢失主容器尚未消费的请求，Docker 模式不从 WebUI 删除队列文件。")
        elif st.button(t("rm_clear_trigger_btn"), key="rm_clear_trigger"):
            for request_path in _TRIGGER_QUEUE_DIR.glob("*.json"):
                request_path.unlink(missing_ok=True)
            st.rerun()
        return

    if trigger_pending:
        st.info(f"⏳ {t('rm_trigger_pending')}")

    can_run = not trigger_pending and not is_running
    col_run, col_status = st.columns([1, 4])
    with col_run:
        run_clicked = st.button(
            "▶ " + t("run_now_btn"), key="rm_run_now",
            type="primary", use_container_width=True, disabled=not can_run,
        )
    with col_status:
        if is_running:
            lock_info = ", ".join(
                f"`{f.name}`" + (f" PID={pid}" if pid is not None else "")
                for f, pid in active_locks
            )
            st.info(f"🟢 {t('rm_status_running')} — {lock_info}")
        elif trigger_pending:
            st.caption(t("rm_trigger_pending_short"))
        else:
            status = _latest_trigger_status()
            if status and status.get("state") in {"failed", "rejected", "interrupted"}:
                detail = status.get("error") or status.get("return_code") or status["state"]
                st.warning(f"最近一次 WebUI 请求 {status['state']}: {detail}")
            else:
                _show_last_run_hint()

    if run_clicked:
        if _IS_DOCKER_WEBUI:
            ok, err = _enqueue_worker_trigger("daily_research")
            if ok:
                st.toast(t("rm_trigger_sent_short"), icon="✅")
                st.rerun()
            else:
                st.error(f"{t('rm_trigger_failed')}: {err}")
        else:
            _LOCK_DIR.mkdir(parents=True, exist_ok=True)
            log_file = _LOGS_DIR / f"manual_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
            try:
                with open(log_file, "w") as lf:
                    proc = subprocess.Popen(
                        [sys.executable, str(_MAIN_PY), "--mode", "daily_research"],
                        cwd=str(_PROJECT_ROOT),
                        stdout=lf, stderr=lf, start_new_session=True,
                    )
                st.toast(f"✅ {t('process_started')} (PID={proc.pid})", icon="✅")
                time.sleep(0.5)
                st.rerun()
            except Exception as e:
                st.error(f"启动失败: {e}")

def _show_last_run_hint() -> None:
    log_groups  = _scan_all_logs()
    manual_logs = log_groups.get("manual", [])
    if manual_logs:
        latest  = manual_logs[0]
        mtime   = datetime.datetime.fromtimestamp(latest.stat().st_mtime)
        size_kb = latest.stat().st_size / 1024
        st.caption(
            f"✅ {t('rm_last_run_at')}: {mtime:%Y-%m-%d %H:%M:%S}  "
            f"({latest.name}, {size_kb:.0f} KB)"
        )
    else:
        st.caption(t("rm_no_panel_process"))


def _format_scan_time(value: object) -> str:
    """Render stored ISO timestamps compactly without assuming timezone shape."""
    text = str(value or "").strip()
    return text.replace("T", " ")[:19] if text else "—"


def _daily_db_path_from_config(config_values: dict) -> Path:
    """Resolve the same relative persistence path accepted by the WebUI.

    The run manager must not silently inspect the default database when the
    user has configured a different local persistence path in Advanced.
    Portable configs deliberately constrain paths to the project tree.  For a
    malformed value keep this diagnostics-only view conservative and fall back
    to the default instead of reading an arbitrary host database.
    """
    configured = config_values.get("daily_research_db_path")
    if not isinstance(configured, str) or not configured.strip():
        return _DEFAULT_DAILY_DB_PATH
    try:
        from utils.config_io import _resolve_project_relative_config_path

        return _resolve_project_relative_config_path(
            configured, label="daily_research.db_path"
        )
    except ValueError:
        return _DEFAULT_DAILY_DB_PATH


def _receipt_query_summary(query: object) -> str:
    if not isinstance(query, dict):
        return "—"
    checked = query.get("api_entries_checked", 0)
    window_entries = query.get("window_entries", 0)
    pages = query.get("pages_observed", 0)
    attempts = query.get("attempts", 0)
    return f"checked {checked} · in window {window_entries} · pages {pages} · attempts {attempts}"


def _render_scan_receipts(config_values: dict) -> None:
    """Show durable arXiv scan evidence, not an inferred report count.

    This remains local-only UI observability.  An unavailable/corrupt database
    is stated plainly and never changes scheduler, history, or delivery state.
    """
    st.markdown(
        f'<p class="section-title">🔎 {t("rm_scan_receipts_title")}</p>',
        unsafe_allow_html=True,
    )
    st.caption(t("rm_scan_receipts_hint"))

    database_path = _daily_db_path_from_config(config_values)
    if not database_path.is_file():
        st.info(t("rm_scan_receipts_empty"))
        return

    try:
        runs = DailyResearchStore(database_path).get_recent_runs(limit=10)
    except Exception as exc:
        st.warning(f"{t('rm_scan_receipts_load_error')}: {exc}")
        return

    arxiv_runs = [
        run for run in runs if any(receipt.get("source") == "arxiv" for receipt in run["receipts"])
    ]
    if not arxiv_runs:
        st.info(t("rm_scan_receipts_empty"))
        return

    for index, run in enumerate(arxiv_runs):
        arxiv_receipt = next(
            receipt for receipt in run["receipts"] if receipt.get("source") == "arxiv"
        )
        receipt_status = arxiv_receipt.get("status", "unknown")
        label = (
            f"{_format_scan_time(run.get('scan_started_at') or run.get('started_at'))} · "
            f"{t('rm_scan_receipt_run')}: {run.get('status', 'unknown')} · "
            f"ArXiv: {receipt_status}"
        )
        with st.expander(label, expanded=index == 0):
            requested_days = arxiv_receipt.get("requested_scan_days", run.get("scan_days"))
            grace_days = arxiv_receipt.get("announcement_lookback_grace_days", 0)
            effective_days = arxiv_receipt.get("effective_days", requested_days)
            domains = arxiv_receipt.get("domains", [])
            col1, col2, col3 = st.columns(3)
            col1.metric(t("rm_scan_receipt_window"), f"{requested_days} + {grace_days} = {effective_days} {t('rm_scan_receipt_days')}")
            col2.metric(t("rm_scan_receipt_candidates"), arxiv_receipt.get("total_new_candidates", 0))
            col3.metric(t("rm_scan_receipt_domains"), len(domains) if isinstance(domains, list) else 0)
            st.caption(
                f"{t('rm_scan_receipt_window_range')}: "
                f"{_format_scan_time(arxiv_receipt.get('window_start'))} → "
                f"{_format_scan_time(arxiv_receipt.get('window_end'))}"
            )
            if run.get("error"):
                st.error(f"{t('rm_scan_receipt_run_error')}: {run['error']}")

            domain_rows = []
            for domain in arxiv_receipt.get("domain_receipts", []):
                if not isinstance(domain, dict):
                    continue
                queries = domain.get("queries", {})
                domain_rows.append(
                    {
                        t("rm_scan_receipt_domain"): domain.get("domain", "—"),
                        t("rm_scan_receipt_status"): domain.get("status", "unknown"),
                        t("rm_scan_receipt_submitted"): _receipt_query_summary(
                            queries.get("submitted") if isinstance(queries, dict) else None
                        ),
                        t("rm_scan_receipt_updated"): _receipt_query_summary(
                            queries.get("updated") if isinstance(queries, dict) else None
                        ),
                        t("rm_scan_receipt_new"): domain.get("new_candidates", 0),
                        t("rm_scan_receipt_history_skip"): domain.get(
                            "skipped_legacy_history", 0
                        ),
                        t("rm_scan_receipt_dedup"): (
                            int(domain.get("deduplicated_within_domain", 0))
                            + int(domain.get("skipped_already_collected", 0))
                        ),
                    }
                )
                if domain.get("error"):
                    st.warning(
                        f"{domain.get('domain', '—')}: {domain['error']}"
                    )
            if domain_rows:
                st.dataframe(domain_rows, hide_index=True, use_container_width=True)
            else:
                st.caption(t("rm_scan_receipts_no_domain_detail"))

            # Raw receipt is intentionally opt-in, and contains only source
            # query counters/configured category names (never credentials).
            with st.expander(t("rm_scan_receipt_raw"), expanded=False):
                st.code(
                    json.dumps(arxiv_receipt, ensure_ascii=False, indent=2, sort_keys=True),
                    language="json",
                )


# ─── 状态面板 ────────────────────────────────────────────────────────────────


def _render_status() -> None:
    lock_files = _get_lock_files()
    if not lock_files:
        st.success(f"✅ {t('rm_no_running_tasks')}")
        return

    for f in lock_files:
        pid        = _read_pid_from_file(f)
        is_running = _is_lock_held(f)
        icon       = "🟢" if is_running else "🔴"
        status     = t("rm_status_running") if is_running else t("rm_status_stopped")
        pid_str    = f"PID={pid}" if pid else t("rm_no_pid")
        mtime      = datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")

        cols = st.columns([5, 1])
        with cols[0]:
            st.markdown(f"{icon} `{f.name}` — {status} | {pid_str} | {t('rm_started_at')}: {mtime}")
        with cols[1]:
            if not is_running:
                st.caption("锁文件保留为稳定锚点")


# ─── 日志查看器 ──────────────────────────────────────────────────────────────


def _make_log_options(logs: list[Path]) -> list[tuple[str, Optional[Path]]]:
    """构建 selectbox 选项，首项为空占位符。"""
    opts: list[tuple[str, Optional[Path]]] = [("—", None)]
    for p in logs:
        mtime   = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
        size_kb = p.stat().st_size / 1024
        opts.append((f"{p.name}  [{mtime}  {size_kb:.0f}KB]", p))
    return opts


def _refresh_latest_log() -> None:
    """刷新按钮回调：自动打开最新的非系统日志文件。"""
    log_groups = _scan_all_logs()
    non_system = (
        log_groups.get("manual", [])
        + log_groups.get("daily", [])
        + log_groups.get("trend", [])
        + log_groups.get("other", [])
    )
    if non_system:
        newest = max(non_system, key=lambda p: p.stat().st_mtime)
        st.session_state[_LOG_ACTIVE] = str(newest)
        st.session_state[_LOG_CLOSED] = False


def _render_log_section() -> None:
    """
    三列并排的日志选择器 + 共享内容区。

    布局：
      [📌 系统日志 ▼]  [📀 运行日志 ▼]  [📄 其他日志 ▼]
      ─────────────────────────────────────────────────
      <选中日志的内容（单一显示区）>
    """
    log_groups  = _scan_all_logs()
    system_logs = log_groups.get("system", [])
    run_logs    = log_groups.get("manual", []) + log_groups.get("daily", [])
    run_logs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    other_logs  = log_groups.get("trend", []) + log_groups.get("other", [])
    other_logs.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    sys_opts = _make_log_options(system_logs)
    run_opts = _make_log_options(run_logs)
    oth_opts = _make_log_options(other_logs)

    sys_map = {o[0]: o[1] for o in sys_opts}
    run_map = {o[0]: o[1] for o in run_opts}
    oth_map = {o[0]: o[1] for o in oth_opts}

    # 初次加载：从所有非系统日志中选时间最新的，无则不预选（不选系统日志）
    if _LOG_ACTIVE not in st.session_state:
        non_system = run_logs + other_logs
        if non_system:
            newest = max(non_system, key=lambda p: p.stat().st_mtime)
            st.session_state[_LOG_ACTIVE] = str(newest)

    # ── 三列选择器 ──────────────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)

    with col1:
        st.caption(f"📌 **{t('rm_log_group_system')}**")
        sys_sel = st.selectbox(
            t("rm_log_group_system"), [o[0] for o in sys_opts],
            key="rm_sel_sys", label_visibility="collapsed",
            disabled=not system_logs,
        )
        new_sys = sys_map.get(sys_sel)
        if new_sys and st.session_state.get("rm_sel_sys_prev") != sys_sel:
            st.session_state[_LOG_ACTIVE] = str(new_sys)
            st.session_state[_LOG_CLOSED] = False
        st.session_state["rm_sel_sys_prev"] = sys_sel

    with col2:
        st.caption(f"📀 **{t('rm_log_group_runs')}**")
        run_sel = st.selectbox(
            t("rm_log_group_runs"), [o[0] for o in run_opts],
            key="rm_sel_run", label_visibility="collapsed",
            disabled=not run_logs,
        )
        new_run = run_map.get(run_sel)
        if new_run and st.session_state.get("rm_sel_run_prev") != run_sel:
            st.session_state[_LOG_ACTIVE] = str(new_run)
            st.session_state[_LOG_CLOSED] = False
        st.session_state["rm_sel_run_prev"] = run_sel

    with col3:
        st.caption(f"📄 **{t('rm_log_group_secondary')}**")
        oth_sel = st.selectbox(
            t("rm_log_group_secondary"), [o[0] for o in oth_opts],
            key="rm_sel_oth", label_visibility="collapsed",
            disabled=not other_logs,
        )
        new_oth = oth_map.get(oth_sel)
        if new_oth and st.session_state.get("rm_sel_oth_prev") != oth_sel:
            st.session_state[_LOG_ACTIVE] = str(new_oth)
            st.session_state[_LOG_CLOSED] = False
        st.session_state["rm_sel_oth_prev"] = oth_sel

    # ── 共享内容区 ──────────────────────────────────────────────────────────
    active_str = st.session_state.get(_LOG_ACTIVE)
    is_closed  = st.session_state.get(_LOG_CLOSED, False)

    if not active_str or is_closed:
        st.caption(t("rm_no_log_selected"))
        return

    active_path = Path(active_str)
    if not active_path.exists():
        st.warning(t("rm_log_file_missing"))
        return

    stat = active_path.stat()
    info_col, r_col, c_col, _ = st.columns([4, 1, 1, 1])
    with info_col:
        st.caption(
            f"`{active_path.name}`  ·  {stat.st_size/1024:.1f} KB  ·  "
            f"{t('reports_mtime')}: "
            f"{datetime.datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M:%S}"
        )
    with r_col:
        st.button(
            f"🔄 {t('rm_refresh_log_btn')}",
            key="rm_log_refresh",
            use_container_width=True,
            on_click=_refresh_latest_log,
        )
    with c_col:
        if st.button(f"✖ {t('rm_close_log_btn')}", key="rm_log_close", use_container_width=True):
            st.session_state[_LOG_CLOSED] = True
            st.rerun()

    st.code(_read_log_tail(active_path, max_lines=300), language="", line_numbers=True)


# ─── 主渲染 ─────────────────────────────────────────────────────────────────


def render(_env_values: dict, config_values: dict) -> None:
    flat = config_values

    st.markdown(
        f'<p class="section-title">🚀 {t("run_now_section_title")}</p>',
        unsafe_allow_html=True,
    )
    _render_run_control()

    st.divider()

    st.markdown(
        f'<p class="section-title">📊 {t("rm_status_title")}</p>',
        unsafe_allow_html=True,
    )
    _render_status()

    st.divider()

    _render_scan_receipts(config_values)

    st.divider()

    # ── 每日研究设置 ──────────────────────────────────────────────────────
    st.markdown(
        f'<p class="section-title">⚙️ {t("daily_research_settings_title")}</p>',
        unsafe_allow_html=True,
    )
    col_ds1, col_ds2, col_ds3 = st.columns(3)
    with col_ds1:
        st.toggle(
            t("html_reports_label"),
            value=flat.get("enable_html_report", True),
            key="enable_html_report",
        )
    with col_ds2:
        st.toggle(
            t("markdown_report_label"),
            value=flat.get("enable_markdown_report", True),
            key="enable_markdown_report",
        )
    with col_ds3:
        st.toggle(
            t("include_all_in_report"),
            value=flat.get("include_all_in_report", True),
            key="include_all_in_report",
            help=t("include_all_help"),
        )

    st.divider()

    st.markdown(
        f'<p class="section-title">📋 {t("run_log_title")}</p>',
        unsafe_allow_html=True,
    )
    _render_log_section()


def collect(_env_values: dict, _config_values: dict) -> dict:
    """从 session_state 收集每日研究设置。"""
    return {
        "enable_html_report": st.session_state.get("enable_html_report", True),
        "enable_markdown_report": st.session_state.get("enable_markdown_report", True),
        "include_all_in_report": st.session_state.get("include_all_in_report", True),
    }

"""运行管理 Tab — 立即运行每日研究、查看运行状态/日志、停止进程。

架构：
  本地模式：直接 subprocess.Popen 启动 main.py，日志写入 logs/manual_*.log。
  Docker 模式：原子写入 data/run/webui_triggers/*.json 请求队列，
               主研究容器 entrypoint.sh 的 trigger_watcher 每 5 秒轮询，
               验证请求后启动 worker（真实 PID 写入 webui_triggered.pid）。
"""

from __future__ import annotations

import datetime
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import streamlit as st

from utils.run_lock import is_lock_held
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
# 进度面板用的配置快照：fragment 无法接收参数，经 session_state 传递
_PROGRESS_CONFIG_KEY = "rm_status_config_values"

# 阶段心跳 → i18n key（交付提交后 run 即终态，无独立 deliver 阶段）
_PHASE_LABEL_KEYS = {
    "prepare": "rm_progress_phase_prepare",
    "scan": "rm_progress_phase_scan",
    "score": "rm_progress_phase_score",
    "analyze": "rm_progress_phase_analyze",
    "report": "rm_progress_phase_report",
}


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
                t("rm_log_truncated").format(skipped=len(lines) - max_lines, kept=max_lines)
            ] + lines[-max_lines:]
        return "\n".join(lines)
    except Exception:
        # A filesystem/decoder exception can reveal host paths.  The selected
        # log remains local, but the UI only needs a generic read failure.
        return t("rm_log_read_failed")


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


def _latest_run_log() -> Optional[Path]:
    """最新一次运行日志（手动/定时/趋势任一），用于运行中实时尾部。"""
    groups = _scan_all_logs()
    for key in ("manual", "daily", "trend"):
        if groups.get(key):
            return groups[key][0]
    return None


def _request_worker_stop(active_locks: list[tuple[Path, Optional[int]]]) -> None:
    """向 worker 写停止请求（仅对 WebUI 触发的运行生效，尽力而为）。"""
    from utils.webui_trigger import request_stop

    stopped = []
    for _lock_path, pid in active_locks:
        if pid is None:
            continue
        try:
            request_stop(_LOCK_DIR.parent, pid)
            stopped.append(pid)
        except OSError as exc:
            st.warning(t("rm_stop_failed").format(err=exc))
            return
    if stopped:
        st.toast(
            t("rm_stop_sent").format(pids=", ".join(str(p) for p in stopped)),
            icon="⏹",
        )
    else:
        st.info(t("rm_stop_no_pid"))


def _format_elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def _active_run_progress() -> Optional[dict]:
    """读取活跃 run 的阶段/论文进度；库缺失或异常时静默返回 None。"""
    config_values = st.session_state.get(_PROGRESS_CONFIG_KEY) or {}
    db_path = _daily_db_path_from_config(config_values)
    if not db_path.exists():
        return None
    try:
        from utils.daily_research_store import DailyResearchStore

        return DailyResearchStore(db_path).active_run_progress()
    except Exception:
        return None


def _render_run_progress(progress: dict) -> None:
    """在运行状态下方渲染阶段 + 已处理计数（数据来自持久化队列）。"""
    phase_key = _PHASE_LABEL_KEYS.get(progress.get("phase"), "rm_progress_phase_report")
    phase = t(phase_key)
    registered = int(progress.get("registered") or 0)
    scored = int(progress.get("scored") or 0)
    analyzed = int(progress.get("analyzed") or 0)
    completed = int(progress.get("completed") or 0)
    failed = int(progress.get("failed") or 0)

    started_at = progress.get("started_at")
    elapsed_text = ""
    if isinstance(started_at, str) and started_at:
        try:
            started = datetime.datetime.fromisoformat(started_at)
            elapsed_text = _format_elapsed(
                (datetime.datetime.now() - started).total_seconds()
            )
        except ValueError:
            elapsed_text = ""

    st.caption(
        t("rm_progress_caption").format(
            phase=phase,
            registered=registered,
            scored=scored,
            analyzed=analyzed,
            completed=completed,
            failed=failed,
            elapsed=elapsed_text or "-",
        )
    )
    # 评分阶段分母精确（登记数），深度分析用 已分析/(已分析+待分析) 近似。
    phase_value = progress.get("phase")
    if phase_value == "score" and registered > 0:
        st.progress(min(1.0, scored / registered))
    elif phase_value == "analyze":
        pending = int(progress.get("awaiting_analysis") or 0)
        denominator = analyzed + pending
        if denominator > 0:
            st.progress(min(1.0, analyzed / denominator))


def _render_live_status_body() -> None:
    """实时状态渲染体：运行锁 + 进度 + 触发状态 + 活跃日志尾部。

    作为普通函数被完整渲染调用一次，或被 fragment 以 5 秒周期调用。
    """
    active_locks = _get_all_running_locks()
    is_running = bool(active_locks)

    if is_running:
        f, pid = active_locks[0]
        pid_info = f" · PID {pid}" if pid is not None else ""
        st.markdown(f"**🟢 {t('rm_status_running')}** · `{f.name}`{pid_info}")
        progress = _active_run_progress()
        if progress is not None:
            _render_run_progress(progress)
        # 停止控件放在自动刷新的 fragment 里：整页脚本不会自动重跑，
        # 只有这里能随运行状态自动出现/消失。
        with st.popover("⏹ " + t("rm_stop_btn")):
            st.warning(t("rm_stop_confirm_hint"))
            if st.button(t("rm_stop_confirm"), key="rm_stop_confirm", type="primary"):
                _request_worker_stop(active_locks)
        log = _latest_run_log()
        if log is not None:
            tail = _read_log_tail(log, max_lines=12)
            with st.expander(
                f"📜 {log.name} · {t('rm_live_tail_hint')}", expanded=True
            ):
                st.code(tail)
        return

    trigger_age = _trigger_age_seconds()
    trigger_pending = trigger_age is not None and trigger_age <= 30
    if trigger_pending:
        st.caption(t("rm_trigger_pending_short"))
        return

    status = _latest_trigger_status()
    if status and status.get("state") == "skipped_busy":
        st.info(t("rm_status_skipped_busy"))
    elif status and status.get("state") in {"failed", "rejected", "interrupted"}:
        # The worker-owned status may contain an exception string.
        # Keep that detail in the worker log: local WebUI feedback
        # only needs the terminal state and a safe numeric exit code.
        return_code = status.get("return_code")
        suffix = (
            f" (exit {return_code})"
            if isinstance(return_code, int) and not isinstance(return_code, bool)
            else ""
        )
        st.warning(t("rm_trigger_state_failed").format(state=status["state"], suffix=suffix))
    else:
        _show_last_run_hint()


if hasattr(st, "fragment"):
    @st.fragment(run_every="5s")
    def _live_status_fragment() -> None:
        _render_live_status_body()
else:  # Streamlit < 1.37：退化为静态渲染，功能不缺失。
    def _live_status_fragment() -> None:
        _render_live_status_body()


def _render_run_control() -> None:
    trigger_age   = _trigger_age_seconds()
    trigger_stale   = trigger_age is not None and trigger_age > 30
    trigger_pending = trigger_age is not None and not trigger_stale

    active_locks = _get_all_running_locks()
    is_running   = bool(active_locks)

    if trigger_stale:
        st.warning(f"⚠️ {t('rm_trigger_stale').format(n=int(trigger_age))}")
        if _IS_DOCKER_WEBUI:
            st.caption(t("rm_trigger_docker_keep"))
        elif st.button(t("rm_clear_trigger_btn"), key="rm_clear_trigger"):
            for request_path in _TRIGGER_QUEUE_DIR.glob("*.json"):
                request_path.unlink(missing_ok=True)
            st.rerun()
        return

    if trigger_pending:
        st.info(f"⏳ {t('rm_trigger_pending')}")

    can_run = not trigger_pending and not is_running
    col_run, col_refresh = st.columns(2)
    with col_run:
        run_clicked = st.button(
            "▶ " + t("run_now_btn"), key="rm_run_now",
            type="primary", use_container_width=True, disabled=not can_run,
        )
    with col_refresh:
        st.toggle(
            t("rm_auto_refresh"),
            value=st.session_state.get("rm_auto_refresh_on", True),
            key="rm_auto_refresh_on",
            help=t("rm_auto_refresh_help"),
        )

    if run_clicked:
        if _IS_DOCKER_WEBUI:
            ok, _ = _enqueue_worker_trigger("daily_research")
            if ok:
                st.toast(t("rm_trigger_sent_short"), icon="✅")
                st.rerun()
            else:
                st.error(t("rm_trigger_failed"))
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
            except Exception:
                st.error(t("rm_launch_failed"))

    # 过去时间段每日报告：为过去的某一天补跑当天的每日研究。
    col_date, col_back = st.columns([3, 2])
    with col_date:
        backfill_date = st.date_input(
            t("rm_backfill_date_label"),
            value=None,
            min_value=datetime.date(1991, 1, 1),
            max_value=datetime.date.today() - datetime.timedelta(days=1),
            key="rm_backfill_date",
            help=t("rm_backfill_help"),
        )
    with col_back:
        backfill_clicked = st.button(
            "🗓 " + t("rm_backfill_btn"),
            key="rm_backfill_run",
            use_container_width=True,
            disabled=not can_run or backfill_date is None,
        )
    if backfill_clicked and backfill_date is not None:
        if _IS_DOCKER_WEBUI:
            ok, _ = _enqueue_worker_trigger(
                "backfill_run", target_date=backfill_date.isoformat()
            )
            if ok:
                st.toast(t("rm_backfill_sent"), icon="🗓")
                st.rerun()
            else:
                st.error(t("rm_trigger_failed"))
        else:
            _LOCK_DIR.mkdir(parents=True, exist_ok=True)
            log_file = _LOGS_DIR / f"backfill_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
            try:
                with open(log_file, "w") as lf:
                    subprocess.Popen(
                        [
                            sys.executable, str(_MAIN_PY), "--mode", "backfill_run",
                            "--target-date", backfill_date.isoformat(),
                        ],
                        cwd=str(_PROJECT_ROOT),
                        stdout=lf, stderr=lf, start_new_session=True,
                    )
                st.toast(t("rm_backfill_sent"), icon="🗓")
                time.sleep(0.5)
                st.rerun()
            except Exception:
                st.error(t("rm_launch_failed"))

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


# ─── 状态面板 ────────────────────────────────────────────────────────────────


def _render_status_panel(config_values: dict) -> None:
    """运行状态 + 待处理队列合并为一个卡片，避免零散的提示块。"""
    # 进度面板运行在自动刷新的 fragment 里，无法直接接收这里的参数；
    # 把配置快照放进 session_state 供 fragment 每次重绘时读取。
    st.session_state[_PROGRESS_CONFIG_KEY] = dict(config_values or {})
    with st.container(border=True):
        if st.session_state.get("rm_auto_refresh_on", True):
            _live_status_fragment()
        else:
            _render_live_status_body()
        _render_queue_metrics(config_values)


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


def _render_queue_metrics(config_values: dict) -> None:
    """常驻队列指标：待处理 / 失败待重试。"""
    db_path = _daily_db_path_from_config(config_values)
    if not db_path.exists():
        return
    try:
        from utils.daily_research_store import DailyResearchStore

        counts = DailyResearchStore(db_path).count_pending_papers()
    except Exception:
        return

    col1, col2 = st.columns(2)
    with col1:
        st.metric(t("rm_queue_pending"), f"{counts['total']:,}")
    with col2:
        st.metric(t("rm_queue_failed"), f"{counts['failed_retry']:,}")


def render(_env_values: dict, config_values: dict) -> None:
    flat = config_values

    st.markdown(
        f'<p class="section-title">🚀 {t("run_now_section_title")}</p>',
        unsafe_allow_html=True,
    )
    _render_run_control()
    _render_status_panel(config_values)

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
    col_ds4, col_ds5 = st.columns(2)
    with col_ds4:
        st.number_input(
            t("daily_max_papers_label"),
            min_value=0,
            max_value=100000,
            value=int(flat.get("daily_max_papers_per_run", 200)),
            step=1,
            key="daily_max_papers_per_run",
            help=t("daily_max_papers_help"),
        )
    with col_ds5:
        default_run_time = datetime.time(12, 0)
        raw_run_time = flat.get("daily_run_time")
        if isinstance(raw_run_time, str):
            try:
                default_run_time = datetime.time.fromisoformat(raw_run_time)
            except ValueError:
                pass
        st.time_input(
            t("daily_run_time_label"),
            value=default_run_time,
            key="daily_run_time_input",
            help=t("daily_run_time_help"),
        )

    st.divider()

    st.markdown(
        f'<p class="section-title">📋 {t("run_log_title")}</p>',
        unsafe_allow_html=True,
    )
    _render_log_section()


def collect(_env_values: dict, _config_values: dict) -> dict:
    """从 session_state 收集每日研究设置。

    每次只渲染当前页面，未访问过的页面没有会话状态；此时回退到
    配置文件里的现有值，而不是默认值，避免保存时改写未浏览的设置。
    """
    flat = _config_values or {}

    def current(key: str, default):
        return st.session_state.get(key, flat.get(key, default))

    selected_run_time = st.session_state.get("daily_run_time_input")
    if selected_run_time is not None:
        run_time_value = (
            f"{selected_run_time.hour:02d}:{selected_run_time.minute:02d}"
        )
    else:
        run_time_value = str(current("daily_run_time", "12:00") or "12:00")

    return {
        "enable_html_report": current("enable_html_report", True),
        "enable_markdown_report": current("enable_markdown_report", True),
        "include_all_in_report": current("include_all_in_report", True),
        "daily_max_papers_per_run": int(current("daily_max_papers_per_run", 200)),
        "daily_run_time": run_time_value,
    }

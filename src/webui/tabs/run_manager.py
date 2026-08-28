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
from utils.webui_trigger import (
    enqueue_trigger,
    sanitize_task_error_summary,
    trigger_directory,
)
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
_LOG_VIEWER_HEIGHT_PX = 800
_LIVE_LOG_TAIL_LINES = 80
_LIVE_LOG_TAIL_HEIGHT_PX = 360
# 进度面板用的配置快照：fragment 无法接收参数，经 session_state 传递
_PROGRESS_CONFIG_KEY = "rm_status_config_values"
_PROGRESS_SHOW_QUEUE_KEY = "rm_status_show_queue"
_PROGRESS_EXCLUDE_HISTORY_KEY = "rm_status_exclude_history_tasks"

# Historical maintenance is deliberately an idle-time workflow. Its detailed
# queue/progress state belongs to System → History Maintenance, while its log
# remains available from System → Run Logs. Keep it out of the daily landing
# page so that page only reflects operational research work.
_HISTORY_MAINTENANCE_MODES = frozenset(
    {"legacy_import", "history_data_repair", "history_omission_scan"}
)
_HISTORY_MAINTENANCE_LOCK_NAMES = frozenset(
    {
        "legacy_import.lock",
        "history_data_repair.lock",
        "history_omission_scan.lock",
    }
)

# 阶段心跳 → i18n key（交付提交后 run 即终态，无独立 deliver 阶段）
_PHASE_LABEL_KEYS = {
    "prepare": "rm_progress_phase_prepare",
    "scan": "rm_progress_phase_scan",
    "score": "rm_progress_phase_score",
    "analyze": "rm_progress_phase_analyze",
    "report": "rm_progress_phase_report",
    "legacy_import": "rm_progress_phase_legacy_import",
    "legacy_history": "rm_progress_phase_legacy_history",
    "legacy_keywords": "rm_progress_phase_legacy_keywords",
    "legacy_reports": "rm_progress_phase_legacy_reports",
    "legacy_write": "rm_progress_phase_legacy_write",
    "legacy_backlog": "rm_progress_phase_legacy_backlog",
    "legacy_scan": "rm_progress_phase_legacy_scan",
    "legacy_supplement": "rm_progress_phase_legacy_supplement",
    "history_repair": "rm_progress_phase_history_repair",
    "history_omission_scan": "rm_progress_phase_history_omission_scan",
    "history_omission_week": "rm_progress_phase_history_omission_week",
}

# The Docker trigger watcher writes a tiny outer ``manual_*.log`` around every
# request. The real worker creates a mode-specific log after ``main.py`` has
# validated and started it. Use the held task lock to select that real log for
# the live panel; only use ``manual_`` during the short startup hand-off.
_LIVE_LOG_PREFIXES_BY_LOCK = {
    "daily_research.lock": ("daily_", "cron_", "startup_"),
    "legacy_import.lock": ("legacy_import_",),
    "history_data_repair.lock": ("history_data_repair_",),
    "history_omission_scan.lock": ("history_omission_scan_",),
    "supplement_run.lock": ("supplement_run_", "supplement_"),
    "backfill_run.lock": ("backfill_run_", "backfill_"),
    "keyword_maintenance.lock": ("keyword_",),
}
_RUN_KIND_LOCK_NAMES = {
    "daily": "daily_research.lock",
    "daily_research": "daily_research.lock",
    "legacy_import": "legacy_import.lock",
    "history_data_repair": "history_data_repair.lock",
    "history_omission_scan": "history_omission_scan.lock",
    "supplement": "supplement_run.lock",
    "supplement_run": "supplement_run.lock",
    "backfill": "backfill_run.lock",
    "backfill_run": "backfill_run.lock",
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


def _configured_worker_lock_dir() -> Path:
    """Return the worker's configured ``<data_dir>/run`` directory.

    Trigger requests intentionally remain in the default shared ``data/run``
    volume because the Docker entrypoint watches that stable path.  The worker
    itself, however, follows ``paths.data_dir`` when it creates its real
    ``run_lock`` files.  Read that single path from the persisted config so
    the status panel also works for installations using a custom data root.
    """
    try:
        from utils.config_io import (
            _resolve_project_relative_config_path,
            read_config_json,
        )

        raw_config = read_config_json()
        paths = raw_config.get("paths", {}) if isinstance(raw_config, dict) else {}
        configured = paths.get("data_dir", "data") if isinstance(paths, dict) else "data"
        data_dir = _resolve_project_relative_config_path(
            configured, label="paths.data_dir"
        )
        return data_dir / "run"
    except (ImportError, OSError, ValueError, TypeError):
        # Diagnostics must remain available if a hand-edited config is
        # temporarily malformed or unreadable.  The legacy default is still
        # the correct trigger-volume location in that case.
        return _LOCK_DIR


def _get_lock_files() -> list[Path]:
    """Return worker ``*.lock`` files from default and configured data roots.

    A custom root is listed in addition to the stable trigger root.  This
    keeps in-flight jobs visible while a user changes paths and avoids losing
    an old default-root lock during that transition.
    """
    directories = []
    for directory in (_LOCK_DIR, _configured_worker_lock_dir()):
        candidate = Path(directory)
        if candidate not in directories:
            directories.append(candidate)

    files = []
    for directory in directories:
        try:
            if directory.is_dir():
                files.extend(directory.glob("*.lock"))
        except OSError:
            # A shared volume can briefly be unavailable during a container
            # restart.  Other readable lock directories remain useful.
            continue
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return files


def _scan_all_logs() -> dict[str, list[Path]]:
    """
    扫描 logs/ 目录下所有 *.log，按类型分组（最新在前）。

    分组：
      manual  → manual_/legacy_import_/history_*_/supplement_/backfill_*.log（面板触发）
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
        if name.startswith((
            "manual_", "legacy_import_", "history_data_repair_",
            "history_omission_scan_", "supplement_", "backfill_"
        )):
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


def _trigger_age_seconds(
    *, exclude_modes: frozenset[str] = frozenset()
) -> Optional[float]:
    """Return the age of the oldest queued request visible to this view."""
    if not _TRIGGER_QUEUE_DIR.exists():
        return None
    try:
        queued = list(_TRIGGER_QUEUE_DIR.glob("*.json"))
    except OSError:
        return None

    timestamps: list[float] = []
    for path in queued:
        if exclude_modes:
            try:
                payload = read_trigger_payload(path)
            except (OSError, ValueError):
                # A malformed request is still operationally relevant. Leave
                # it visible so the regular trigger diagnostics can flag it.
                payload = None
            if isinstance(payload, dict) and payload.get("mode") in exclude_modes:
                continue
        try:
            timestamps.append(path.stat().st_mtime)
        except OSError:
            continue
    if not timestamps:
        return None
    oldest = min(timestamps)
    return time.time() - oldest


def _trigger_queue_state(
    active_locks: Optional[list[tuple[Path, Optional[int]]]] = None,
    *,
    exclude_modes: frozenset[str] = frozenset(),
) -> tuple[Optional[float], bool, bool]:
    """Classify a queued trigger without mistaking deliberate idle waiting for failure.

    The worker consumes requests synchronously.  A user may queue a past-date
    range while a daily run is still active; that request can legitimately sit
    in ``*.json`` for much longer than the short watcher pickup interval.
    Treat it as pending while a visible worker lock is held, and only mark it
    stale when no worker activity can explain the wait.
    """
    trigger_age = _trigger_age_seconds(exclude_modes=exclude_modes)
    if trigger_age is None:
        return None, False, False
    if active_locks is None:
        active_locks = _get_all_running_locks()
    trigger_stale = trigger_age > 30 and not active_locks
    return trigger_age, not trigger_stale, trigger_stale


def _latest_trigger_status(
    *, exclude_modes: frozenset[str] = frozenset()
) -> Optional[dict]:
    """Load the newest worker-owned trigger status visible to this view."""
    if not _TRIGGER_STATUS_DIR.exists():
        return None
    try:
        status_files = sorted(
            _TRIGGER_STATUS_DIR.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None
    if not status_files:
        return None
    import json

    for status_path in status_files:
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("mode") in exclude_modes:
            continue
        return payload
    return None


def _exclude_history_maintenance_locks(
    active_locks: list[tuple[Path, Optional[int]]],
) -> list[tuple[Path, Optional[int]]]:
    """Hide idle-time history-maintenance locks from the daily status card."""
    return [
        lock
        for lock in active_locks
        if lock[0].name not in _HISTORY_MAINTENANCE_LOCK_NAMES
    ]


def _enqueue_worker_trigger(mode: str, **args) -> tuple[bool, str]:
    """Queue a validated request for the worker container without shell input."""
    try:
        request_path = enqueue_trigger(_LOCK_DIR.parent, mode, **args)
        return True, request_path.stem
    except Exception as e:
        return False, str(e)


def _primary_running_lock(
    active_locks: list[tuple[Path, Optional[int]]],
    progress: Optional[dict] = None,
) -> tuple[Path, Optional[int]]:
    """Choose the lock that owns the visible active run, when known.

    An old-history import deliberately takes its lock before waiting for a
    daily-style worker to become idle. During that wait two locks can be held;
    the SQLite run kind tells us which one is actually doing work so the live
    panel does not attach the daily progress to the import's waiting log.
    """
    if progress:
        expected = _RUN_KIND_LOCK_NAMES.get(str(progress.get("run_kind") or ""))
        if expected:
            for lock in active_locks:
                if lock[0].name == expected:
                    return lock
    # Before a daily/trend run has opened its SQLite ledger, there is no
    # ``progress`` payload yet. If an import lock and another visible lock
    # coexist, the import is normally waiting at its exclusive idle gate;
    # show the already-running task's actual log in that hand-off window.
    if len(active_locks) > 1 and any(
        lock_path.name == "legacy_import.lock" for lock_path, _ in active_locks
    ):
        for lock in active_locks:
            if lock[0].name != "legacy_import.lock":
                return lock
    return active_locks[0]


def _newest_log_with_prefixes(prefixes: tuple[str, ...]) -> Optional[Path]:
    if not _LOGS_DIR.exists():
        return None
    try:
        matches = [
            path
            for path in _LOGS_DIR.glob("**/*.log")
            if path.name.lower().startswith(prefixes)
        ]
        return max(matches, key=lambda path: path.stat().st_mtime) if matches else None
    except OSError:
        return None


def _latest_run_log(
    active_locks: Optional[list[tuple[Path, Optional[int]]]] = None,
    progress: Optional[dict] = None,
) -> Optional[Path]:
    """Return the useful log for a running task, with a safe generic fallback.

    In Docker, the watcher-level manual log normally has only request claim,
    child launch and exit lines. Looking it up first made long v4 jobs appear
    to stop after three lines even while their true logs continued to grow.
    """
    if active_locks:
        lock_path, _pid = _primary_running_lock(active_locks, progress)
        prefixes = _LIVE_LOG_PREFIXES_BY_LOCK.get(lock_path.name)
        if prefixes is None and lock_path.name.startswith("trend_research_"):
            prefixes = ("trend_",)
        if prefixes:
            selected = _newest_log_with_prefixes(prefixes)
            if selected is not None:
                return selected

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
    detail = str(progress.get("detail") or "").strip()
    current = progress.get("current")
    total = progress.get("total")
    if not isinstance(current, int) or isinstance(current, bool) or current < 0:
        current = None
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        total = None

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

    caption = t("rm_progress_caption").format(
        phase=phase,
        registered=registered,
        scored=scored,
        analyzed=analyzed,
        completed=completed,
        failed=failed,
        elapsed=elapsed_text or "-",
    )
    if detail:
        suffix = f"{detail[:240]}"
        if current is not None and total is not None:
            suffix += f" ({current}/{total})"
        caption += f" · {suffix}"
    st.caption(caption)
    # 评分阶段分母精确（登记数），深度分析用 已分析/(已分析+待分析) 近似。
    phase_value = progress.get("phase")
    if (
        isinstance(phase_value, str)
        and (phase_value.startswith("legacy_") or phase_value.startswith("history_"))
        and current is not None
        and total is not None
        and total > 0
    ):
        st.progress(min(1.0, current / total))
    elif phase_value == "score" and registered > 0:
        st.progress(min(1.0, scored / registered))
    elif phase_value == "analyze":
        pending = int(progress.get("awaiting_analysis") or 0)
        denominator = analyzed + pending
        if denominator > 0:
            st.progress(min(1.0, analyzed / denominator))


def _render_live_status_body(*, exclude_history_tasks: bool = False) -> None:
    """实时状态渲染体：运行锁 + 进度 + 触发状态 + 活跃日志尾部。

    作为普通函数被完整渲染调用一次，或被 fragment 以 5 秒周期调用。
    """
    active_locks = _get_all_running_locks()
    excluded_modes = (
        _HISTORY_MAINTENANCE_MODES if exclude_history_tasks else frozenset()
    )
    if exclude_history_tasks:
        active_locks = _exclude_history_maintenance_locks(active_locks)
    is_running = bool(active_locks)

    if is_running:
        progress = _active_run_progress()
        f, pid = _primary_running_lock(active_locks, progress)
        pid_info = f" · PID {pid}" if pid is not None else ""
        st.markdown(f"**🟢 {t('rm_status_running')}** · `{f.name}`{pid_info}")
        if progress is not None:
            _render_run_progress(progress)
        if len(active_locks) > 1:
            related = ", ".join(
                lock_path.name for lock_path, _ in active_locks if lock_path != f
            )
            if related:
                st.caption(t("rm_status_related_locks").format(locks=related))
        # 停止控件放在自动刷新的 fragment 里：整页脚本不会自动重跑，
        # 只有这里能随运行状态自动出现/消失。
        with st.popover("⏹ " + t("rm_stop_btn")):
            st.warning(t("rm_stop_confirm_hint"))
            if st.button(t("rm_stop_confirm"), key="rm_stop_confirm", type="primary"):
                _request_worker_stop(active_locks)
        log = _latest_run_log(active_locks, progress)
        if log is not None:
            tail = _read_log_tail(log, max_lines=_LIVE_LOG_TAIL_LINES)
            with st.expander(
                f"📜 {log.name} · {t('rm_live_tail_hint')}", expanded=True
            ):
                try:
                    st.code(tail, language="", height=_LIVE_LOG_TAIL_HEIGHT_PX)
                except TypeError:
                    # Streamlit < 1.40 has no ``height`` on st.code; the
                    # full 800px log viewer remains available below.
                    st.code(tail)
        return

    trigger_age = _trigger_age_seconds(exclude_modes=excluded_modes)
    trigger_pending = trigger_age is not None and trigger_age <= 30
    if trigger_pending:
        st.caption(t("rm_trigger_pending_short"))
        return

    status = _latest_trigger_status(exclude_modes=excluded_modes)
    if status and status.get("state") == "skipped_busy":
        st.info(t("rm_status_skipped_busy"))
    elif status and status.get("state") == "succeeded":
        mode = str(status.get("mode") or "worker")
        st.success(t("rm_trigger_state_succeeded").format(mode=mode))
        _show_last_run_hint()
    elif status and status.get("state") in {"failed", "rejected", "interrupted"}:
        return_code = status.get("return_code")
        suffix = (
            f" (exit {return_code})"
            if isinstance(return_code, int) and not isinstance(return_code, bool)
            else ""
        )
        st.warning(t("rm_trigger_state_failed").format(state=status["state"], suffix=suffix))
        # Only the worker-generated, pre-sanitized summary is eligible for
        # the panel. Older raw ``error`` fields may contain request details,
        # so the full log remains their sole diagnostic location.
        summary = sanitize_task_error_summary(status.get("error_summary"))
        if summary:
            st.caption(t("rm_trigger_error_summary").format(summary=summary))
    else:
        _show_last_run_hint()


if hasattr(st, "fragment"):
    @st.fragment(run_every="5s")
    def _live_status_fragment() -> None:
        config_values = st.session_state.get(_PROGRESS_CONFIG_KEY) or {}
        show_queue = bool(st.session_state.get(_PROGRESS_SHOW_QUEUE_KEY, True))
        exclude_history_tasks = bool(
            st.session_state.get(_PROGRESS_EXCLUDE_HISTORY_KEY, False)
        )
        if show_queue:
            _render_status_snapshot(
                config_values, exclude_history_tasks=exclude_history_tasks
            )
        else:
            _render_status_snapshot(
                config_values,
                show_queue=False,
                exclude_history_tasks=exclude_history_tasks,
            )
        # ``run_every`` is fixed when a fragment starts.  Once the task has
        # finished (and no newly submitted request is waiting for the worker),
        # rerun the app scope so the parent stops mounting this fragment;
        # otherwise an idle status panel would keep polling forever.
        if not _status_needs_polling(exclude_history_tasks=exclude_history_tasks):
            st.rerun()
else:  # Streamlit < 1.37：退化为静态渲染，功能不缺失。
    def _live_status_fragment() -> None:
        config_values = st.session_state.get(_PROGRESS_CONFIG_KEY) or {}
        show_queue = bool(st.session_state.get(_PROGRESS_SHOW_QUEUE_KEY, True))
        exclude_history_tasks = bool(
            st.session_state.get(_PROGRESS_EXCLUDE_HISTORY_KEY, False)
        )
        if show_queue:
            _render_status_snapshot(
                config_values, exclude_history_tasks=exclude_history_tasks
            )
        else:
            _render_status_snapshot(
                config_values,
                show_queue=False,
                exclude_history_tasks=exclude_history_tasks,
            )


def _render_run_control() -> None:
    active_locks = _get_all_running_locks()
    is_running   = bool(active_locks)
    trigger_age, trigger_pending, trigger_stale = _trigger_queue_state(active_locks)

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
    run_clicked = st.button(
        "▶ " + t("run_now_btn"), key="rm_run_now",
        type="primary", width="stretch", disabled=not can_run,
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

def _render_backfill_control(config_values: dict, *, show_queue: bool = True) -> None:
    """Render the historical daily-report range queue before run status."""
    active_locks = _get_all_running_locks()
    trigger_age, trigger_pending, trigger_stale = _trigger_queue_state(active_locks)
    # A past-date request is durable and is intentionally allowed to queue
    # behind a currently running daily/legacy task.  The worker watcher and
    # workflow gates will consume it safely once that task becomes idle.
    can_run = not trigger_pending and not trigger_stale

    if trigger_pending:
        st.caption(f"⏳ {t('rm_trigger_pending')}")
    elif trigger_stale:
        st.warning(f"⚠️ {t('rm_trigger_stale').format(n=int(trigger_age or 0))}")

    latest_past_date = datetime.date.today() - datetime.timedelta(days=1)
    col_from, col_to = st.columns(2)
    with col_from:
        date_from = st.date_input(
            t("rm_backfill_date_from_label"),
            value=latest_past_date,
            min_value=datetime.date(1991, 1, 1),
            max_value=latest_past_date,
            key="rm_backfill_date_from",
            help=t("rm_backfill_help"),
        )
    with col_to:
        date_to = st.date_input(
            t("rm_backfill_date_to_label"),
            value=latest_past_date,
            min_value=datetime.date(1991, 1, 1),
            max_value=latest_past_date,
            key="rm_backfill_date_to",
            help=t("rm_backfill_help"),
        )

    valid_range = date_from <= date_to
    if not valid_range:
        st.warning(t("rm_backfill_invalid_range"))
    # 操作按钮独占下一行，让日期范围清晰且避免窄列中的长按钮换行。
    backfill_clicked = st.button(
        "▶ " + t("rm_backfill_btn"),
        key="rm_backfill_run",
        type="primary",
        width="stretch",
        disabled=not can_run or not valid_range,
    )

    if backfill_clicked:
        if _IS_DOCKER_WEBUI:
            ok, _ = _enqueue_worker_trigger(
                "backfill_run",
                date_from=date_from.isoformat(),
                date_to=date_to.isoformat(),
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
                            sys.executable,
                            str(_MAIN_PY),
                            "--mode",
                            "backfill_run",
                            "--date-from",
                            date_from.isoformat(),
                            "--date-to",
                            date_to.isoformat(),
                        ],
                        cwd=str(_PROJECT_ROOT),
                        stdout=lf,
                        stderr=lf,
                        start_new_session=True,
                    )
                st.toast(t("rm_backfill_sent"), icon="🗓")
                time.sleep(0.5)
                st.rerun()
            except Exception:
                st.error(t("rm_launch_failed"))

    if show_queue:
        _render_backfill_queue_summary(config_values)


def _render_backfill_queue_summary(config_values: dict) -> None:
    """Show the durable past-date queue beside its range launcher."""
    db_path = _daily_db_path_from_config(config_values or {})
    if not db_path.exists():
        st.caption(t("rm_backfill_queue_empty"))
        return
    try:
        from utils.daily_research_store import DailyResearchStore

        summary = DailyResearchStore(db_path).backfill_queue_summary()
    except Exception:
        st.caption(t("rm_backfill_queue_empty"))
        return
    if not any(summary.get(key, 0) for key in ("pending", "running", "completed", "failed")):
        st.caption(t("rm_backfill_queue_empty"))
        return

    pending_col, running_col, completed_col, failed_col = st.columns(4)
    with pending_col:
        st.metric(t("rm_queue_pending"), f"{summary.get('pending', 0):,}")
    with running_col:
        st.metric(t("rm_status_running"), f"{summary.get('running', 0):,}")
    with completed_col:
        st.metric(t("rm_backfill_queue_completed"), f"{summary.get('completed', 0):,}")
    with failed_col:
        st.metric(t("rm_queue_failed"), f"{summary.get('failed', 0):,}")

    if summary.get("active_date"):
        st.caption(
            t("rm_backfill_queue_active").format(date=summary["active_date"])
        )
    elif summary.get("next_date"):
        st.caption(
            t("rm_backfill_queue_next").format(date=summary["next_date"])
        )

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


def _render_status_snapshot(
    config_values: dict,
    *,
    show_queue: bool = True,
    exclude_history_tasks: bool = False,
) -> None:
    """Render the status-card contents that need periodic updates."""
    _render_live_status_body(exclude_history_tasks=exclude_history_tasks)
    if show_queue:
        st.divider()
        st.caption(f"📋 **{t('rm_daily_queue_title')}**")
        _render_queue_metrics(config_values)


def _status_needs_polling(
    active_locks: Optional[list[tuple[Path, Optional[int]]]] = None,
    *,
    exclude_history_tasks: bool = False,
) -> bool:
    """Whether the status panel should keep its short-lived live fragment.

    A Docker launch first writes a trigger request; the worker can take up to
    one watcher interval to create its run lock.  Keep polling across that
    hand-off so a user who just clicked Run does not need to refresh manually.
    Once there is neither a held lock nor a non-stale queued request, the
    fragment is unmounted and the idle page performs no periodic work.
    """
    if active_locks is None:
        active_locks = _get_all_running_locks()
    if exclude_history_tasks:
        active_locks = _exclude_history_maintenance_locks(active_locks)
    if active_locks:
        return True
    if exclude_history_tasks:
        _age, trigger_pending, _trigger_stale = _trigger_queue_state(
            active_locks, exclude_modes=_HISTORY_MAINTENANCE_MODES
        )
    else:
        _age, trigger_pending, _trigger_stale = _trigger_queue_state(active_locks)
    return trigger_pending


def _render_status_panel(
    config_values: dict,
    *,
    show_queue: bool = True,
    exclude_history_tasks: bool = False,
) -> None:
    """运行状态 + 待处理队列；运行中（含刚提交的接手阶段）自动刷新。"""
    # 进度面板运行在自动刷新的 fragment 里，无法直接接收这里的参数；
    # 把配置快照放进 session_state 供 fragment 每次重绘时读取。
    st.session_state[_PROGRESS_CONFIG_KEY] = dict(config_values or {})
    st.session_state[_PROGRESS_SHOW_QUEUE_KEY] = show_queue
    st.session_state[_PROGRESS_EXCLUDE_HISTORY_KEY] = exclude_history_tasks
    auto_refresh = st.toggle(
        t("rm_auto_refresh"),
        value=st.session_state.get("rm_auto_refresh_on", True),
        key="rm_auto_refresh_on",
        help=t("rm_auto_refresh_help"),
    )
    should_poll = _status_needs_polling(
        exclude_history_tasks=exclude_history_tasks
    )
    with st.container(border=True):
        if auto_refresh and should_poll:
            _live_status_fragment()
        else:
            if exclude_history_tasks:
                _render_status_snapshot(
                    config_values,
                    show_queue=show_queue,
                    exclude_history_tasks=True,
                )
            elif show_queue:
                _render_status_snapshot(config_values)
            else:
                _render_status_snapshot(config_values, show_queue=False)


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


def _render_log_content(active_path: Path) -> None:
    """Render the selected log in a fixed-height native scroll viewport."""
    with st.container(height=_LOG_VIEWER_HEIGHT_PX, border=True):
        st.code(
            _read_log_tail(active_path, max_lines=300),
            language="",
            line_numbers=True,
        )


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
            width="stretch",
            on_click=_refresh_latest_log,
        )
    with c_col:
        if st.button(f"✖ {t('rm_close_log_btn')}", key="rm_log_close", width="stretch"):
            st.session_state[_LOG_CLOSED] = True
            st.rerun()

    _render_log_content(active_path)


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


def _render_daily_research_settings(flat: dict) -> None:
    """Render the low-frequency daily-run settings without crowding the landing page."""
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


def render_daily_research(_env_values: dict, config_values: dict) -> None:
    """Daily landing page: launch controls, current status, and compact settings."""
    st.markdown(
        f'<p class="section-title">🚀 {t("run_now_section_title")}</p>',
        unsafe_allow_html=True,
    )
    _render_run_control()

    st.divider()
    st.markdown(
        f'<p class="section-title">📊 {t("rm_status_panel_title")}</p>',
        unsafe_allow_html=True,
    )
    # Keep the ordinary paper queue beside its launch/status controls. It is
    # the only queue relevant to a regular daily run; history maintenance and
    # past-date work deliberately stay on their own pages.
    _render_status_panel(
        config_values, show_queue=True, exclude_history_tasks=True
    )

    # Daily timing/output switches are useful but do not belong in the first
    # screen of a run console. Keep them available without making that screen
    # feel like a configuration page.
    with st.expander(t("daily_research_settings_title"), expanded=False):
        _render_daily_research_settings(config_values)


def render_past_daily_reports(_env_values: dict, config_values: dict) -> None:
    """Render the durable past-date daily-report queue launcher."""
    st.markdown(
        f'<p class="section-title">🗓 {t("rm_backfill_section_title")}</p>',
        unsafe_allow_html=True,
    )
    _render_backfill_control(config_values, show_queue=False)
    st.divider()
    st.markdown(
        f'<p class="section-title">📋 {t("rm_backfill_queue_title")}</p>',
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        _render_backfill_queue_summary(config_values)


def render_queue(_env_values: dict, config_values: dict) -> None:
    """Render paper and past-date queue summaries in one operational page."""
    st.markdown(
        f'<p class="section-title">📋 {t("rm_queue_page_title")}</p>',
        unsafe_allow_html=True,
    )
    _render_queue_metrics(config_values)
    st.divider()
    st.markdown(
        f'<p class="section-title">🗓 {t("rm_backfill_queue_title")}</p>',
        unsafe_allow_html=True,
    )
    _render_backfill_queue_summary(config_values)


def render_logs(_env_values: dict, _config_values: dict) -> None:
    """Render the shared 800px native-scroll run-log viewer."""
    _render_log_section()


def render(_env_values: dict, config_values: dict) -> None:
    """Backward-compatible composite view for integrations using the old tab."""
    render_daily_research(_env_values, config_values)
    st.divider()
    render_past_daily_reports(_env_values, config_values)
    st.divider()
    render_queue(_env_values, config_values)
    st.divider()
    render_logs(_env_values, config_values)


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

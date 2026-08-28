"""数据管理 Tab — 配置导出 + WebDAV 同步 + 数据库备份 + 旧历史导入"""

import io
import json
import zipfile
import logging
import subprocess
import sys
from datetime import datetime, time as dt_time
import streamlit as st
from pathlib import Path

from webui.i18n import t
from webui.secret_fields import render_secret_input, resolve_secret_value
from webui.tabs.run_manager import _daily_db_path_from_config
from utils.backup import (
    LOCAL_BACKUP_RETENTION_DAYS,
    LOCAL_BACKUP_SAME_DAY_MAX_COUNT,
    MIN_LOCAL_BACKUP_RETENTION_DAYS,
    MIN_LOCAL_BACKUP_SAME_DAY_MAX_COUNT,
    export_backup_zip,
    restore_backup_archive,
    validate_local_backup_retention_days,
    validate_local_backup_same_day_max_count,
)
from utils.legacy_history import LEGACY_IMPORT_STATE_KEY
from utils.webui_trigger import (
    enqueue_trigger,
    read_trigger_payload,
    sanitize_task_error_summary,
    trigger_directory,
    trigger_status_directory,
)

logger = logging.getLogger(__name__)

# 路径常量
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "configs" / "config.json"
DEFAULT_ENV_PATH = _PROJECT_ROOT / ".env"

SECRET_FIELD_KEYS = ("webdav_password",)
_MAX_VISIBLE_LIST_ROWS = 10
_TABLE_SCROLL_HEIGHT_PX = 390
_HISTORY_TASK_LIMIT = 40
_HISTORY_TASK_MODES = frozenset(
    {"legacy_import", "history_data_repair", "history_omission_scan"}
)
_HISTORY_TASK_LABEL_KEYS = {
    "legacy_import": "dm_task_legacy_import",
    "history_data_repair": "dm_task_history_repair",
    "history_omission_scan": "dm_task_history_omission",
}
_HISTORY_RUN_KIND_BY_MODE = {
    "legacy_import": "legacy_import",
    "history_data_repair": "history_data_repair",
    "history_omission_scan": "history_omission_scan",
}


def render_backup_sync(env_values: dict, config_values: dict) -> None:
    """Render configuration export, WebDAV sync, and local SQLite backups."""
    flat = config_values

    # ==================== 配置导出 ====================
    st.markdown(
        f'<p class="section-title">📦 {t("dm_export_title")}</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="hint-text">{t("dm_export_hint")}</p>',
        unsafe_allow_html=True,
    )

    col_exp1, col_exp2 = st.columns(2)
    with col_exp1:
        zip_data = _build_export_zip()
        if zip_data:
            st.download_button(
                label=t("dm_export_btn"),
                data=zip_data,
                file_name="arxiv_researcher_config.zip",
                mime="application/zip",
                width="stretch",
            )
        else:
            st.warning(t("dm_export_no_files"))

    with col_exp2:
        st.caption(t("dm_export_contents"))

    st.divider()

    # ==================== WebDAV 同步 ====================
    st.markdown(
        f'<p class="section-title">☁️ {t("dm_webdav_title")}</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="hint-text">{t("dm_webdav_hint")}</p>',
        unsafe_allow_html=True,
    )

    # 全局开关（与代理配置一致：关闭时收起全部详细设置）
    st.toggle(
        t("dm_webdav_enable"),
        value=flat.get("webdav_enabled", False),
        key="webdav_enabled",
    )

    if st.session_state.get("webdav_enabled", flat.get("webdav_enabled", False)):
        # WebDAV 连接凭据（直接在面板配置，类似 API tab）
        st.text_input(
            t("dm_webdav_url_label"),
            value=env_values.get("WEBDAV_URL", ""),
            key="webdav_url",
            placeholder="https://dav.jianguoyun.com/dav/",
        )

        col_u, col_p = st.columns(2)
        with col_u:
            st.text_input(
                t("dm_webdav_username_label"),
                value=env_values.get("WEBDAV_USERNAME", ""),
                key="webdav_username",
            )
        with col_p:
            render_secret_input(
                st,
                label=t("dm_webdav_password_label"),
                env_values=env_values,
                env_key="WEBDAV_PASSWORD",
                field_key="webdav_password",
                configured_hint=t("secret_configured_keep_blank"),
            )

        # 操作按钮（紧跟凭据后面）
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            if st.button(t("dm_webdav_test_btn"), width="stretch"):
                _do_test_connection(env_values)
        with col_b:
            if st.button(t("dm_webdav_upload_btn"), width="stretch"):
                _do_sync("upload", env_values)
        with col_c:
            if st.button(t("dm_webdav_download_btn"), width="stretch"):
                _do_sync("download", env_values)

        st.divider()

        # 远程路径 & 同步设置
        st.markdown(
            f'<p class="section-title">⚙️ {t("dm_webdav_sync_settings")}</p>',
            unsafe_allow_html=True,
        )

        st.text_input(
            t("dm_webdav_remote_path"),
            value=flat.get("webdav_remote_path", "/arxiv-daily-researcher/"),
            key="webdav_remote_path",
            help=t("dm_webdav_remote_path_help"),
        )

        # 同步模式
        mode_options = ["manual", "scheduled", "after_report"]
        mode_labels = [
            t("dm_webdav_mode_manual"),
            t("dm_webdav_mode_scheduled"),
            t("dm_webdav_mode_after_report"),
        ]
        current_mode = flat.get("webdav_sync_mode", "after_report")
        current_idx = mode_options.index(current_mode) if current_mode in mode_options else 2

        selected_label = st.selectbox(
            t("dm_webdav_sync_mode"),
            options=mode_labels,
            index=current_idx,
            key="webdav_sync_mode_label",
        )
        _mode_idx = mode_labels.index(selected_label)
        st.session_state["webdav_sync_mode"] = mode_options[_mode_idx]

        # 定时同步 — 时间选择器（小时:分钟）
        if st.session_state.get("webdav_sync_mode") == "scheduled":
            # Parse existing cron or default to 23:00
            cron_str = flat.get("webdav_cron_schedule", "0 23 * * *")
            try:
                parts = cron_str.split()
                default_hour = int(parts[1]) if len(parts) > 1 else 23
                default_minute = int(parts[0]) if len(parts) > 0 else 0
            except (ValueError, IndexError):
                default_hour, default_minute = 23, 0

            sync_time = st.time_input(
                t("dm_webdav_sync_time"),
                value=dt_time(default_hour, default_minute),
                key="webdav_sync_time",
                help=t("dm_webdav_sync_time_help"),
            )
            # Store as cron expression for backend compatibility
            st.session_state["webdav_cron_schedule"] = (
                f"{sync_time.minute} {sync_time.hour} * * *"
            )

        st.divider()

        # 同步范围
        st.markdown(
            f'<p class="section-title">📂 {t("dm_webdav_scope_title")}</p>',
            unsafe_allow_html=True,
        )

        col1, col2 = st.columns(2)
        with col1:
            st.toggle(
                t("dm_webdav_sync_configs_label"),
                value=flat.get("webdav_sync_configs", True),
                key="webdav_sync_configs",
            )
            st.toggle(
                t("dm_webdav_sync_history_label"),
                value=flat.get("webdav_sync_history", True),
                key="webdav_sync_history",
            )
        with col2:
            st.toggle(
                t("dm_webdav_sync_keywords_label"),
                value=flat.get("webdav_sync_keywords", True),
                key="webdav_sync_keywords",
            )
            st.toggle(
                t("dm_webdav_sync_reports_label"),
                value=flat.get("webdav_sync_reports", False),
                key="webdav_sync_reports",
            )

    st.divider()

    # ==================== 数据库备份 ====================
    backup_data_dir, backup_database = _configured_backup_paths(flat)
    st.markdown(
        f'<p class="section-title">🗄️ {t("dm_backup_title")}</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="hint-text">{t("dm_backup_hint")}</p>',
        unsafe_allow_html=True,
    )

    st.toggle(
        t("dm_backup_enable"),
        value=flat.get("backup_enabled", True),
        key="backup_enabled",
    )

    configured_retention = flat.get(
        "backup_local_retention_days", LOCAL_BACKUP_RETENTION_DAYS
    )
    if (
        isinstance(configured_retention, bool)
        or not isinstance(configured_retention, int)
        or configured_retention < MIN_LOCAL_BACKUP_RETENTION_DAYS
    ):
        configured_retention = LOCAL_BACKUP_RETENTION_DAYS
    st.number_input(
        t("dm_backup_retention_days_label"),
        min_value=MIN_LOCAL_BACKUP_RETENTION_DAYS,
        value=configured_retention,
        step=1,
        key="backup_local_retention_days",
        help=t("dm_backup_retention_days_help"),
    )

    configured_same_day_max = flat.get(
        "backup_local_same_day_max_count", LOCAL_BACKUP_SAME_DAY_MAX_COUNT
    )
    if (
        isinstance(configured_same_day_max, bool)
        or not isinstance(configured_same_day_max, int)
        or configured_same_day_max < MIN_LOCAL_BACKUP_SAME_DAY_MAX_COUNT
    ):
        configured_same_day_max = LOCAL_BACKUP_SAME_DAY_MAX_COUNT
    st.number_input(
        t("dm_backup_same_day_max_label"),
        min_value=MIN_LOCAL_BACKUP_SAME_DAY_MAX_COUNT,
        value=configured_same_day_max,
        step=1,
        key="backup_local_same_day_max_count",
        help=t("dm_backup_same_day_max_help"),
    )

    if st.session_state.get("backup_enabled", flat.get("backup_enabled", True)):
        if st.button(t("dm_backup_now_btn"), width="stretch"):
            _do_backup(env_values, flat)

    # 导入 / 导出：都是压缩包，导入端自动识别 zip / gzip / 原始 SQLite
    st.divider()
    col_export, col_import = st.columns(2)
    with col_export:
        if st.button(t("dm_backup_export_btn"), key="dm_backup_export", width="stretch"):
            try:
                with st.spinner(t("dm_backup_running")):
                    st.session_state["dm_export_bundle"] = export_backup_zip(
                        backup_data_dir, database=backup_database
                    )
            except Exception as e:
                st.error(f"{t('dm_backup_failed')}: {e}")
        bundle = st.session_state.get("dm_export_bundle")
        if bundle:
            st.download_button(
                label=t("dm_backup_download_btn"),
                data=bundle[0],
                file_name=bundle[1],
                mime="application/zip",
                width="stretch",
            )

    with col_import:
        uploaded = st.file_uploader(
            t("dm_backup_import_label"),
            type=["zip", "gz", "db"],
            key="dm_backup_import_file",
        )
        if uploaded is not None and st.button(
            t("dm_backup_import_btn"), key="dm_backup_import", width="stretch"
        ):
            try:
                with st.spinner(t("dm_backup_running")):
                    result = restore_backup_archive(
                        backup_data_dir,
                        uploaded.getvalue(),
                        uploaded.name,
                        database=backup_database,
                    )
                st.success(
                    t("dm_backup_import_ok").format(
                        source=result["source_member"],
                        archived=result["archived_previous"] or "—",
                    )
                )
            except Exception as e:
                st.error(f"{t('dm_backup_failed')}: {e}")

    _render_backup_list(_list_backups(flat))


def render_history_maintenance(_env_values: dict, config_values: dict) -> None:
    """Render the legacy-import launch point before its task/status audit."""
    _render_legacy_import_section(config_values)
    st.divider()
    _render_history_task_list(config_values)


def render(env_values: dict, config_values: dict) -> None:
    """Backward-compatible composite view for the former data-management tab."""
    render_backup_sync(env_values, config_values)
    st.divider()
    render_history_maintenance(env_values, config_values)


def _legacy_import_store(config_values: dict):
    """打开（可选自定义路径的）日报数据库；异常时静默返回 None。"""
    try:
        from utils.daily_research_store import DailyResearchStore

        return DailyResearchStore(_daily_db_path_from_config(config_values or {}))
    except Exception:
        logger.debug("旧历史导入状态读取失败", exc_info=True)
        return None


def _history_task_already_queued(queue_dir: Path, mode: str) -> bool:
    """Return whether one specific history task already has a durable request.

    Other WebUI jobs are intentionally allowed to remain ahead of a legacy
    import.  The worker's FIFO trigger queue plus the importer gates make that
    the safe way to honour a click made while daily research/backfill is busy.
    Only a duplicate legacy-import request should disable its button.
    """
    try:
        candidates = [
            *queue_dir.glob("*.json"),
            *queue_dir.glob("*.running"),
        ]
    except OSError:
        # If the shared queue cannot be read, leave the button available and
        # let the atomic enqueue operation surface a real filesystem error.
        return False
    for request_path in candidates:
        try:
            payload = read_trigger_payload(request_path)
        except (OSError, ValueError):
            # A malformed or stale request is owned by the watcher and must
            # not turn an unrelated legacy-import action into a permanent
            # disabled control.
            continue
        if payload.get("mode") == mode:
            return True
    return False


def _legacy_import_already_queued(queue_dir: Path) -> bool:
    """Backward-compatible helper used by existing callers/tests."""
    return _history_task_already_queued(queue_dir, "legacy_import")


def _history_task_label(mode: object) -> str:
    """Return a localized maintenance-workflow name without trusting disk data."""
    normalized = str(mode or "").strip()
    key = _HISTORY_TASK_LABEL_KEYS.get(normalized)
    return t(key) if key else normalized or t("dm_task_unknown")


def _format_history_task_time(value: object) -> str:
    """Format ISO timestamps from the durable trigger protocol defensively."""
    if not isinstance(value, str) or not value.strip():
        return "—"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        return "—"


def _history_task_phase_text(mode: str, state: str, store) -> str:
    """Show current SQLite progress when a WebUI maintenance request is active."""
    if state == "queued":
        return t("dm_task_progress_queued")
    if state == "starting":
        return t("dm_task_progress_starting")
    if state != "running":
        return t("dm_task_progress_terminal")
    if store is None:
        return t("dm_task_progress_running")
    try:
        progress = store.active_run_progress()
    except Exception:
        progress = None
    expected_kind = _HISTORY_RUN_KIND_BY_MODE.get(mode)
    if not isinstance(progress, dict) or progress.get("run_kind") != expected_kind:
        return t("dm_task_progress_waiting_idle")

    phase = str(progress.get("phase") or "").strip()
    detail = sanitize_task_error_summary(progress.get("detail"), max_chars=120)
    current = progress.get("current")
    total = progress.get("total")
    parts = [phase or t("dm_task_progress_running")]
    if detail:
        parts.append(detail)
    if (
        isinstance(current, int)
        and not isinstance(current, bool)
        and isinstance(total, int)
        and not isinstance(total, bool)
        and total > 0
    ):
        parts.append(f"{max(0, current)}/{total}")
    return " · ".join(parts)


def _read_history_task_records(
    config_values: dict,
    *,
    queue_dir: Path | None = None,
    status_dir: Path | None = None,
    store=None,
    limit: int = _HISTORY_TASK_LIMIT,
) -> list[dict]:
    """Read bounded, user-triggered history tasks from queue/status receipts.

    The watcher owns task execution and its receipt files.  This reader only
    consumes the compact protocol fields, so the panel never parses raw logs
    or exposes command lines, host paths, endpoints, or credentials.
    """
    data_dir = _PROJECT_ROOT / "data"
    queue_dir = queue_dir or trigger_directory(data_dir)
    status_dir = status_dir or trigger_status_directory(data_dir)
    records: dict[str, dict] = {}

    def add_record(record: dict, *, replace: bool = False) -> None:
        request_id = str(record.get("request_id") or "").strip()
        mode = str(record.get("mode") or "").strip()
        if not request_id or mode not in _HISTORY_TASK_MODES:
            return
        existing = records.get(request_id)
        if existing is None or replace:
            records[request_id] = record

    # Terminal/running status receipts are sufficient for start, completion,
    # and sanitized issue evidence after the watcher has claimed a request.
    try:
        status_paths = sorted(
            status_dir.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        status_paths = []
    for path in status_paths[: max(1, limit * 3)]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        mode = str(payload.get("mode") or "")
        state = str(payload.get("state") or "unknown")
        issue = sanitize_task_error_summary(
            payload.get("error_summary") or payload.get("error")
        )
        if state == "skipped_busy" and not issue:
            issue = t("dm_task_issue_busy")
        completed_at = (
            payload.get("updated_at")
            if state in {"succeeded", "failed", "rejected", "interrupted", "skipped_busy"}
            else ""
        )
        add_record(
            {
                "request_id": payload.get("request_id") or path.stem,
                "mode": mode,
                "state": state,
                "created_at": payload.get("created_at"),
                "started_at": payload.get("started_at"),
                "completed_at": completed_at,
                "issue": issue,
                "args": payload.get("args") if isinstance(payload.get("args"), dict) else {},
                "sort_at": payload.get("updated_at") or payload.get("created_at") or "",
            }
        )

    # A request still in the shared queue has not written a receipt yet. It
    # remains a first-class task: its purpose is exactly to wait for idle work
    # rather than disappear from the operator's view.
    for pattern, state in (("*.json", "queued"), ("*.running", "starting")):
        try:
            request_paths = list(queue_dir.glob(pattern))
        except OSError:
            request_paths = []
        for path in request_paths:
            try:
                payload = read_trigger_payload(path)
            except (OSError, ValueError):
                continue
            add_record(
                {
                    "request_id": payload.get("request_id"),
                    "mode": payload.get("mode"),
                    "state": state,
                    "created_at": payload.get("created_at"),
                    "started_at": "",
                    "completed_at": "",
                    "issue": "",
                    "args": payload.get("args") if isinstance(payload.get("args"), dict) else {},
                    "sort_at": payload.get("created_at") or "",
                },
                # The live queue is newer authority than a status file that
                # was written during watcher hand-off.
                replace=True,
            )

    rows = list(records.values())
    rows.sort(key=lambda row: str(row.get("sort_at") or ""), reverse=True)
    live_store = store if store is not None else _legacy_import_store(config_values)
    for row in rows:
        row["label"] = _history_task_label(row["mode"])
        row["progress"] = _history_task_phase_text(
            str(row["mode"]), str(row["state"]), live_store
        )
        row["retryable"] = row["state"] in {
            "failed", "rejected", "interrupted", "skipped_busy"
        }
    return rows[: max(1, limit)]


def _render_history_task_list(config_values: dict) -> None:
    """Render a compact, retryable audit of WebUI history-maintenance tasks."""
    st.markdown(
        f'<p class="section-title">🗂 {t("dm_history_task_list_title")}</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="hint-text">{t("dm_history_task_list_hint")}</p>',
        unsafe_allow_html=True,
    )
    tasks = _read_history_task_records(config_values)
    if not tasks:
        st.caption(t("dm_history_task_list_empty"))
        return

    rows = [
        {
            t("dm_task_col_name"): task["label"],
            t("dm_task_col_state"): t(f"dm_task_state_{task['state']}")
            if f"dm_task_state_{task['state']}" in _KNOWN_I18N_TASK_STATE_KEYS
            else str(task["state"]),
            t("dm_task_col_progress"): task["progress"],
            t("dm_task_col_started"): _format_history_task_time(task["started_at"]),
            t("dm_task_col_completed"): _format_history_task_time(task["completed_at"]),
            t("dm_task_col_issue"): task["issue"] or "—",
        }
        for task in tasks
    ]
    if len(rows) > _MAX_VISIBLE_LIST_ROWS:
        with st.container(height=_TABLE_SCROLL_HEIGHT_PX, border=True):
            st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.dataframe(rows, hide_index=True, width="stretch")

    retryable = [task for task in tasks if task["retryable"]]
    if not retryable:
        return
    st.caption(t("dm_task_retry_hint"))
    for task in retryable:
        col_label, col_action = st.columns([5, 1])
        col_label.caption(
            f"{task['label']} · {_format_history_task_time(task['completed_at'])}"
        )
        if col_action.button(
            t("dm_task_retry_btn"),
            key=f"history_task_retry_{task['request_id']}",
            width="stretch",
        ):
            _retry_history_task(task, config_values)


def _retry_history_task(task: dict, config_values: dict) -> None:
    """Queue one retry through the same validated workflow as the primary controls."""
    mode = str(task.get("mode") or "")
    if mode == "legacy_import":
        args = task.get("args")
        if isinstance(args, dict) and isinstance(args.get("full_repair"), bool):
            full_repair = args["full_repair"]
        else:
            full_repair = bool(
                st.session_state.get(
                    "legacy_import_full_repair_enabled",
                    config_values.get("legacy_import_full_repair_enabled", False),
                )
            )
        _enqueue_legacy_import(full_repair)
        return
    if mode == "history_data_repair":
        _enqueue_history_task(mode, "dm_history_repair_queued")
        return
    if mode == "history_omission_scan":
        _enqueue_history_task(mode, "dm_history_omission_queued")


# ``t()`` returns its key for unknown values. Keep state labels explicit so a
# future protocol state stays readable instead of looking like a translation
# failure in the task table.
_KNOWN_I18N_TASK_STATE_KEYS = frozenset(
    {
        "dm_task_state_queued",
        "dm_task_state_starting",
        "dm_task_state_running",
        "dm_task_state_succeeded",
        "dm_task_state_failed",
        "dm_task_state_rejected",
        "dm_task_state_interrupted",
        "dm_task_state_skipped_busy",
    }
)


def _render_legacy_import_section(config_values: dict) -> None:
    """v3.2 importer plus independent SQLite repair/omission workflows."""
    import json as json_module

    # ==================== 旧版本历史导入 ====================
    st.markdown(
        f'<p class="section-title">📜 {t("dm_legacy_title")}</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="hint-text">{t("dm_legacy_hint")}</p>',
        unsafe_allow_html=True,
    )

    queue_dir = trigger_directory(_PROJECT_ROOT / "data")
    legacy_pending = _legacy_import_already_queued(queue_dir)
    full_repair_enabled = st.toggle(
        t("dm_legacy_full_repair_toggle"),
        value=bool(config_values.get("legacy_import_full_repair_enabled", False)),
        key="legacy_import_full_repair_enabled",
        help=t("dm_legacy_full_repair_help"),
    )
    st.caption(
        t(
            "dm_legacy_full_repair_on_hint"
            if full_repair_enabled
            else "dm_legacy_full_repair_off_hint"
        )
    )

    # Full repair is deliberately opt-in. The button passes the live toggle
    # instead of relying on a separate Save click, so its one-time behavior is
    # never surprising when the user has just changed the switch.
    if st.button(
        t("dm_legacy_import_btn"),
        width="stretch",
        disabled=legacy_pending,
        type="primary",
    ):
        _enqueue_legacy_import(full_repair_enabled)

    if legacy_pending:
        st.info(t("dm_legacy_running_hint"))

    st.caption(f"**{t('dm_history_maintenance_title')}**")
    repair_pending = _history_task_already_queued(queue_dir, "history_data_repair")
    omission_pending = _history_task_already_queued(queue_dir, "history_omission_scan")
    col_repair, col_omission = st.columns(2)
    with col_repair:
        if st.button(
            t("dm_history_repair_btn"),
            key="dm_history_repair",
            width="stretch",
            disabled=repair_pending,
        ):
            _enqueue_history_task("history_data_repair", "dm_history_repair_queued")
    with col_omission:
        if st.button(
            t("dm_history_omission_btn"),
            key="dm_history_omission",
            width="stretch",
            disabled=omission_pending,
        ):
            _enqueue_history_task("history_omission_scan", "dm_history_omission_queued")
    if repair_pending or omission_pending:
        active = []
        if repair_pending:
            active.append(t("dm_history_repair_short"))
        if omission_pending:
            active.append(t("dm_history_omission_short"))
        st.info(t("dm_history_task_running_hint").format(tasks="、".join(active)))

    store = _legacy_import_store(config_values)
    if store is None:
        return

    try:
        summary_raw = store.get_app_state(LEGACY_IMPORT_STATE_KEY)
    except Exception:
        summary_raw = None
    if summary_raw:
        try:
            summary = json_module.loads(summary_raw)
        except ValueError:
            summary = None
        if isinstance(summary, dict):
            st.caption(f"**{t('dm_legacy_summary_title')}**")
            finished = str(summary.get("finished_at") or "")[:19].replace("T", " ")
            mode_label = t(
                "dm_legacy_mode_full"
                if summary.get("full_repair_enabled")
                else "dm_legacy_mode_light"
            )
            st.caption(
                t("dm_legacy_summary_line").format(
                    finished=finished or "—",
                    mode=mode_label,
                    reports=summary.get("reports_scanned", 0),
                    cards=summary.get("cards_selected", summary.get("cards_found", 0)),
                    delivered=summary.get("delivered_ledger_rows", 0),
                    missing_cards=summary.get("missing_cards", 0),
                    missing_tldr=summary.get("missing_tldr", 0),
                    missing_translation=summary.get("missing_translation", 0),
                    missing_analysis=summary.get("missing_analysis", 0),
                    backlog=summary.get("backlog_queued", 0),
                )
            )
            source_breakdown = summary.get("source_breakdown")
            if isinstance(source_breakdown, dict) and source_breakdown:
                source_text = " · ".join(
                    f"{source}: {count}"
                    for source, count in sorted(source_breakdown.items())
                    if isinstance(source, str)
                )
                if source_text:
                    st.caption(
                        t("dm_legacy_source_breakdown").format(sources=source_text)
                    )
            repair = summary.get("history_repair")
            if isinstance(repair, dict):
                st.caption(
                    t("dm_history_repair_line").format(
                        state=repair.get("state", "—"),
                        candidates=repair.get("candidates", 0),
                        tldr=(repair.get("repaired") or {}).get("tldr", 0),
                        translation=(repair.get("repaired") or {}).get("translation", 0),
                        analysis=(repair.get("repaired") or {}).get("analysis", 0),
                        pending=repair.get("pending_after", 0),
                    )
                )
            supplement = summary.get("supplement")
            if isinstance(supplement, dict):
                st.caption(
                    t("dm_legacy_supplement_line").format(
                        state=supplement.get("state", "—"),
                        processed=supplement.get("processed", 0),
                        pending=supplement.get(
                            "pending_after", supplement.get("pending_before", 0)
                        ),
                    )
                )
            omission = summary.get("omission_scan")
            if isinstance(omission, dict):
                scan = omission.get("scan") if isinstance(omission.get("scan"), dict) else {}
                st.caption(
                    t("dm_history_omission_line").format(
                        state=omission.get("state", "—"),
                        found=scan.get("missed_found", 0),
                        weeks=len(omission.get("weeks") or []),
                        pending=omission.get("pending_after", 0),
                    )
                )
            report_keywords = summary.get("report_keywords")
            if isinstance(report_keywords, dict):
                state = str(report_keywords.get("state") or "html_only")
                state_key = {
                    "html_only": "dm_report_keywords_state_html_only",
                    "not_needed": "dm_report_keywords_state_not_needed",
                    "supplemented": "dm_report_keywords_state_supplemented",
                    "no_matching_records": "dm_report_keywords_state_no_matching",
                    "not_found": "dm_report_keywords_state_not_found",
                    "not_configured": "dm_report_keywords_state_not_configured",
                    "unreadable": "dm_report_keywords_state_unreadable",
                    "unsupported_schema": "dm_report_keywords_state_unsupported",
                }.get(state)
                state_label = t(state_key) if state_key else state
                st.caption(
                    t("dm_report_keywords_line").format(
                        state=state_label,
                        html_papers=report_keywords.get("html_papers", 0),
                        html_terms=report_keywords.get("html_terms", 0),
                        fallback_papers=report_keywords.get("fallback_papers", 0),
                        fallback_terms=report_keywords.get("fallback_terms", 0),
                    )
                )
            elif isinstance(summary.get("legacy_keywords"), dict):
                # v4.0 summaries remain readable after upgrade. A new import
                # replaces this with the report-scoped v4.1 keyword summary.
                st.caption(t("dm_report_keywords_legacy_summary"))
    else:
        st.caption(t("dm_legacy_none"))

    try:
        backlog = store.supplement_backlog_summary()
    except Exception:
        backlog = None
    if backlog and backlog.get("pending"):
        breakdown = backlog.get("breakdown", {})
        missing = sum(
            counts.get("pending", 0) + counts.get("failed", 0)
            for reason, counts in breakdown.items()
            if reason != "missed_scan"
        )
        missed = sum(
            counts.get("pending", 0) + counts.get("failed", 0)
            for reason, counts in breakdown.items()
            if reason == "missed_scan"
        )
        st.caption(f"**{t('dm_legacy_backlog_title')}**")
        st.caption(
            t("dm_legacy_backlog_line").format(
                pending=backlog.get("pending", 0), missing=missing, missed=missed
            )
        )


def _enqueue_legacy_import(full_repair: bool) -> None:
    """Queue the selected lightweight or complete legacy-import workflow."""
    mode = "legacy_import"
    queued_key = "dm_legacy_queued"
    main_py = _PROJECT_ROOT / "main.py"
    if not main_py.exists():
        try:
            enqueue_trigger(_PROJECT_ROOT / "data", mode, full_repair=full_repair)
        except Exception as e:
            st.error(f"{t('dm_legacy_failed')}: {e}")
            return
        st.toast(t(queued_key), icon="📜")
        st.rerun()
        return

    logs_dir = _PROJECT_ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"{mode}_{datetime.now():%Y%m%d_%H%M%S}.log"
    try:
        with open(log_file, "w") as lf:
            subprocess.Popen(
                [
                    sys.executable,
                    str(main_py),
                    "--mode",
                    mode,
                    "--legacy-full-repair" if full_repair else "--no-legacy-full-repair",
                ],
                cwd=str(_PROJECT_ROOT),
                stdout=lf,
                stderr=lf,
                start_new_session=True,
            )
        st.toast(t(queued_key), icon="📜")
        st.rerun()
    except Exception as e:
        st.error(f"{t('dm_legacy_failed')}: {e}")


def _enqueue_history_task(mode: str, queued_key: str) -> None:
    """Queue one independent SQLite maintenance task through the same worker."""
    if mode not in {"history_data_repair", "history_omission_scan"}:
        raise ValueError(f"unsupported history task: {mode}")
    main_py = _PROJECT_ROOT / "main.py"
    try:
        if not main_py.exists():
            enqueue_trigger(_PROJECT_ROOT / "data", mode)
        else:
            logs_dir = _PROJECT_ROOT / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            log_file = logs_dir / f"{mode}_{datetime.now():%Y%m%d_%H%M%S}.log"
            with open(log_file, "w") as lf:
                subprocess.Popen(
                    [sys.executable, str(main_py), "--mode", mode],
                    cwd=str(_PROJECT_ROOT),
                    stdout=lf,
                    stderr=lf,
                    start_new_session=True,
                )
        st.toast(t(queued_key), icon="🗂️")
        st.rerun()
    except Exception as exc:
        st.error(f"{t('dm_legacy_failed')}: {exc}")


def collect(env_values: dict, _config_values: dict) -> tuple:
    """收集数据管理 Tab 的配置值。返回 (env_updates, config_updates)。"""
    def current_env(session_key: str, env_key: str, default=""):
        # 未访问的页面没有会话状态；回退到 .env 现值，避免保存时清空。
        if session_key in st.session_state:
            return st.session_state[session_key]
        value = env_values.get(env_key)
        return value if value not in (None, "") else default

    env_updates = {
        "WEBDAV_URL": current_env("webdav_url", "WEBDAV_URL"),
        "WEBDAV_USERNAME": current_env("webdav_username", "WEBDAV_USERNAME"),
        "WEBDAV_PASSWORD": resolve_secret_value(
            env_values, "WEBDAV_PASSWORD", "webdav_password", st.session_state
        ),
    }

    flat = _config_values or {}

    def current_cfg(key: str, default):
        return st.session_state.get(key, flat.get(key, default))

    config_updates = {
        "webdav_enabled": current_cfg("webdav_enabled", False),
        "webdav_remote_path": current_cfg(
            "webdav_remote_path", "/arxiv-daily-researcher/"
        ),
        "webdav_sync_mode": current_cfg("webdav_sync_mode", "after_report"),
        "webdav_cron_schedule": current_cfg("webdav_cron_schedule", "0 23 * * *"),
        "webdav_sync_configs": current_cfg("webdav_sync_configs", True),
        "webdav_sync_history": current_cfg("webdav_sync_history", True),
        "webdav_sync_keywords": current_cfg("webdav_sync_keywords", True),
        "webdav_sync_reports": current_cfg("webdav_sync_reports", False),
        "backup_enabled": current_cfg("backup_enabled", True),
        "backup_local_retention_days": current_cfg(
            "backup_local_retention_days", LOCAL_BACKUP_RETENTION_DAYS
        ),
        "backup_local_same_day_max_count": current_cfg(
            "backup_local_same_day_max_count", LOCAL_BACKUP_SAME_DAY_MAX_COUNT
        ),
        "legacy_import_full_repair_enabled": current_cfg(
            "legacy_import_full_repair_enabled", False
        ),
    }

    return env_updates, config_updates


# ==================== 内部辅助函数 ====================


def _build_export_zip() -> bytes | None:
    """构建包含 config.json 和 .env 的 zip 压缩包。"""
    files_to_zip = []

    # Check multiple possible paths (local vs Docker mount)
    for config_path in [DEFAULT_CONFIG_PATH, Path("/app/configs/config.json")]:
        if config_path.exists():
            files_to_zip.append(("config.json", config_path))
            break

    for env_path in [DEFAULT_ENV_PATH, Path("/app/.env")]:
        if env_path.exists():
            files_to_zip.append((".env", env_path))
            break

    if not files_to_zip:
        return None

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, filepath in files_to_zip:
            zf.write(filepath, arcname)
    return buf.getvalue()


def _configured_backup_paths(config_values: dict | None) -> tuple[Path, Path]:
    """Resolve the configured archive root and exact SQLite database safely.

    The worker honours both ``paths.data_dir`` and
    ``daily_research.db_path``. The WebUI's immediate backup/import/export
    actions must use those same paths rather than silently falling back to
    the legacy ``data/`` tree.
    """
    flat = config_values or {}
    try:
        from utils.config_io import _resolve_project_relative_config_path

        raw_data_dir = flat.get("data_dir", "data")
        data_dir = _resolve_project_relative_config_path(
            raw_data_dir, label="paths.data_dir"
        )
    except (ImportError, OSError, TypeError, ValueError):
        data_dir = _PROJECT_ROOT / "data"

    if not isinstance(flat.get("daily_research_db_path"), str):
        return data_dir, data_dir / "daily_research" / "daily_research.db"
    return data_dir, _daily_db_path_from_config(flat)


def _list_backups(config_values: dict | None = None):
    """List rotated backups from the configured local archive directory."""
    try:
        from utils.backup import list_local_backups

        data_dir, _database = _configured_backup_paths(config_values)
        return list_local_backups(data_dir)
    except Exception as e:
        logger.warning(f"列出现有备份失败: {e}")
        return []


def _render_backup_list(backups: list[dict]) -> None:
    """Show every local backup, keeping the page viewport to ten rows."""
    if not backups:
        st.caption(t("dm_backup_none"))
        return
    st.caption(t("dm_backup_existing"))
    rows = [
        {
            t("dm_backup_col_name"): item["name"],
            t("dm_backup_col_size"): f"{item['size_bytes'] / 1024:.0f} KB",
            t("dm_backup_col_time"): item["modified_at"],
        }
        for item in backups
    ]
    if len(rows) > _MAX_VISIBLE_LIST_ROWS:
        with st.container(height=_TABLE_SCROLL_HEIGHT_PX, border=True):
            st.table(rows)
    else:
        st.table(rows)


def _do_backup(env_values: dict, config_values: dict | None = None):
    """立即创建一次压缩数据库备份（WebDAV 已配置时默认镜像上传）。"""
    try:
        from utils.backup import create_backup
        from utils.webdav_sync import WebDAVSync

        # 备份默认压缩后上传：只要 WebDAV 凭据可用（表单或 .env）就镜像，
        # 但必须先尊重 WebDAV 总开关；关闭后绝不能因 .env 残留凭据而写远端。
        flat = config_values or {}
        webdav_enabled = st.session_state.get(
            "webdav_enabled", flat.get("webdav_enabled", False)
        )
        url = (st.session_state.get("webdav_url") or env_values.get("WEBDAV_URL") or "").strip()
        username = (
            st.session_state.get("webdav_username") or env_values.get("WEBDAV_USERNAME") or ""
        ).strip()
        password = resolve_secret_value(
            env_values, "WEBDAV_PASSWORD", "webdav_password", st.session_state
        )
        webdav_sync = None
        if webdav_enabled and url and username:
            webdav_sync = WebDAVSync(
                url=url,
                username=username,
                password=password,
                remote_path=(
                    st.session_state.get("webdav_remote_path")
                    or "/arxiv-daily-researcher/"
                ).strip(),
            )
        else:
            st.info(t("dm_backup_local_only"))

        retention_days = validate_local_backup_retention_days(
            st.session_state.get(
                "backup_local_retention_days", LOCAL_BACKUP_RETENTION_DAYS
            )
        )
        same_day_max_count = validate_local_backup_same_day_max_count(
            st.session_state.get(
                "backup_local_same_day_max_count",
                LOCAL_BACKUP_SAME_DAY_MAX_COUNT,
            )
        )
        data_dir, database = _configured_backup_paths(config_values)

        with st.spinner(t("dm_backup_running")):
            result = create_backup(
                data_dir,
                database=database,
                retention_days=retention_days,
                same_day_max_count=same_day_max_count,
                webdav_sync=webdav_sync,
            )

        if not result.get("created"):
            st.warning(t("dm_backup_skip_reason").format(result.get("reason", "")))
            return
        if result.get("uploaded"):
            st.success(t("dm_backup_done_uploaded"))
        elif result.get("upload_error"):
            st.warning(t("dm_backup_done_upload_failed").format(result["upload_error"]))
        elif webdav_sync is not None and result.get("skipped_reason"):
            st.success(t("dm_backup_done_unchanged"))
        else:
            st.success(t("dm_backup_done_local"))
        st.rerun()
    except ImportError:
        st.error(t("dm_webdav_missing_lib"))
    except Exception as e:
        st.error(f"{t('dm_backup_failed')}: {e}")


def _do_test_connection(env_values: dict):
    """测试 WebDAV 连接。"""
    try:
        from utils.webdav_sync import WebDAVSync

        # Use current form values directly so users can test before clicking Save.
        url = (st.session_state.get("webdav_url") or "").strip()
        username = (st.session_state.get("webdav_username") or "").strip()
        password = resolve_secret_value(
            env_values, "WEBDAV_PASSWORD", "webdav_password", st.session_state
        )
        remote_path = (
            st.session_state.get("webdav_remote_path") or "/arxiv-daily-researcher/"
        ).strip()

        if not url or not username:
            st.error(t("dm_webdav_not_configured"))
            return

        client = WebDAVSync(
            url=url,
            username=username,
            password=password,
            remote_path=remote_path,
        )

        if client.test_connection():
            st.success(t("dm_webdav_test_ok"))
        else:
            st.error(t("dm_webdav_test_fail"))
    except ImportError:
        st.error(t("dm_webdav_missing_lib"))
    except Exception as e:
        st.error(f"{t('dm_webdav_test_fail')}: {e}")


def _do_sync(direction: str, env_values: dict):
    """执行 WebDAV 同步。"""
    try:
        from utils.webdav_sync import WebDAVSync

        # Use current form values directly so users can sync immediately.
        url = (st.session_state.get("webdav_url") or "").strip()
        username = (st.session_state.get("webdav_username") or "").strip()
        password = resolve_secret_value(
            env_values, "WEBDAV_PASSWORD", "webdav_password", st.session_state
        )
        remote_path = (
            st.session_state.get("webdav_remote_path") or "/arxiv-daily-researcher/"
        ).strip()

        if not url or not username:
            st.error(t("dm_webdav_not_configured"))
            return

        client = WebDAVSync(
            url=url,
            username=username,
            password=password,
            remote_path=remote_path,
        )

        include_reports = st.session_state.get("webdav_sync_reports", False)
        include_configs = st.session_state.get("webdav_sync_configs", True)
        include_history = st.session_state.get("webdav_sync_history", True)
        include_keywords = st.session_state.get("webdav_sync_keywords", True)
        with st.spinner(t("dm_webdav_syncing")):
            result = client.sync_all(
                direction=direction,
                include_reports=include_reports,
                include_configs=include_configs,
                include_history=include_history,
                include_keywords=include_keywords,
            )

        if result["success"] == result["total"]:
            st.success(
                f"{t('dm_webdav_sync_done')} "
                f"{result['success']}/{result['total']} — "
                f"{result['elapsed_seconds']}s"
            )
        else:
            st.warning(
                f"{t('dm_webdav_sync_partial')} "
                f"{result['success']}/{result['total']} — "
                f"{result['elapsed_seconds']}s"
            )
            for path, ok in result["results"].items():
                if not ok:
                    st.caption(f"❌ {path}")
    except ImportError:
        st.error(t("dm_webdav_missing_lib"))
    except Exception as e:
        st.error(f"{t('dm_webdav_sync_error')}: {e}")

"""数据管理 Tab — 配置导出 + WebDAV 同步 + 数据库备份 + 旧历史导入"""

import io
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
from utils.backup import export_backup_zip, restore_backup_archive
from utils.legacy_history import LEGACY_IMPORT_STATE_KEY
from utils.webui_trigger import enqueue_trigger, trigger_directory

logger = logging.getLogger(__name__)

# 路径常量
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "configs" / "config.json"
DEFAULT_ENV_PATH = _PROJECT_ROOT / ".env"

SECRET_FIELD_KEYS = ("webdav_password",)


def render(env_values: dict, config_values: dict):
    """渲染数据管理 Tab。"""
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
                use_container_width=True,
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
            if st.button(t("dm_webdav_test_btn"), use_container_width=True):
                _do_test_connection(env_values)
        with col_b:
            if st.button(t("dm_webdav_upload_btn"), use_container_width=True):
                _do_sync("upload", env_values)
        with col_c:
            if st.button(t("dm_webdav_download_btn"), use_container_width=True):
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

    if st.session_state.get("backup_enabled", flat.get("backup_enabled", True)):
        col_b1, col_b2 = st.columns([3, 1])
        with col_b1:
            st.number_input(
                t("dm_backup_keep_label"),
                min_value=1,
                max_value=60,
                value=int(flat.get("backup_keep", 5)),
                key="backup_keep",
                help=t("dm_backup_keep_help"),
            )
        with col_b2:
            if st.button(t("dm_backup_now_btn"), use_container_width=True):
                _do_backup(env_values)

    # 导入 / 导出：都是压缩包，导入端自动识别 zip / gzip / 原始 SQLite
    st.divider()
    col_export, col_import = st.columns(2)
    with col_export:
        if st.button(t("dm_backup_export_btn"), key="dm_backup_export", use_container_width=True):
            try:
                with st.spinner(t("dm_backup_running")):
                    st.session_state["dm_export_bundle"] = export_backup_zip(
                        _PROJECT_ROOT / "data"
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
                use_container_width=True,
            )

    with col_import:
        uploaded = st.file_uploader(
            t("dm_backup_import_label"),
            type=["zip", "gz", "db"],
            key="dm_backup_import_file",
        )
        if uploaded is not None and st.button(
            t("dm_backup_import_btn"), key="dm_backup_import", use_container_width=True
        ):
            try:
                with st.spinner(t("dm_backup_running")):
                    result = restore_backup_archive(
                        _PROJECT_ROOT / "data", uploaded.getvalue(), uploaded.name
                    )
                st.success(
                    t("dm_backup_import_ok").format(
                        source=result["source_member"],
                        archived=result["archived_previous"] or "—",
                    )
                )
            except Exception as e:
                st.error(f"{t('dm_backup_failed')}: {e}")

    backups = _list_backups()
    if backups:
        st.caption(t("dm_backup_existing"))
        rows = [
            {
                t("dm_backup_col_name"): item["name"],
                t("dm_backup_col_size"): f"{item['size_bytes'] / 1024:.0f} KB",
                t("dm_backup_col_time"): item["modified_at"],
            }
            for item in backups[:10]
        ]
        st.table(rows)
    else:
        st.caption(t("dm_backup_none"))

    _render_legacy_import_section(config_values)


def _legacy_import_store(config_values: dict):
    """打开（可选自定义路径的）日报数据库；异常时静默返回 None。"""
    try:
        from utils.daily_research_store import DailyResearchStore

        return DailyResearchStore(_daily_db_path_from_config(config_values or {}))
    except Exception:
        logger.debug("旧历史导入状态读取失败", exc_info=True)
        return None


def _render_legacy_import_section(config_values: dict) -> None:
    """旧版本（v3.2）历史导入：按钮 + 最近结果 + 补充积压概览。"""
    import json as json_module

    st.divider()

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
    trigger_pending = bool(list(queue_dir.glob("*.json"))) if queue_dir.exists() else False

    col_import, col_supplement = st.columns(2)
    with col_import:
        if st.button(
            t("dm_legacy_import_btn"),
            use_container_width=True,
            disabled=trigger_pending,
            type="primary",
        ):
            _enqueue_legacy_import()
    with col_supplement:
        backlog_pending = None
        store_probe = _legacy_import_store(config_values)
        if store_probe is not None:
            try:
                backlog_pending = store_probe.supplement_backlog_summary().get("pending", 0)
            except Exception:
                backlog_pending = None
        if st.button(
            t("dm_supplement_btn"),
            use_container_width=True,
            disabled=trigger_pending or not backlog_pending,
            help=t("dm_supplement_help"),
        ):
            _enqueue_legacy_import(mode="supplement_run", queued_key="dm_supplement_queued")

    if trigger_pending:
        st.info(t("dm_legacy_running_hint"))

    store = store_probe
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
            st.caption(
                t("dm_legacy_summary_line").format(
                    finished=finished or "—",
                    reports=summary.get("reports_scanned", 0),
                    cards=summary.get("cards_found", 0),
                    delivered=summary.get("delivered_ledger_rows", 0),
                    missing_cards=summary.get("missing_cards", 0),
                    missing_translation=summary.get("missing_translation", 0),
                    missing_analysis=summary.get("missing_analysis", 0),
                    backlog=summary.get("backlog_queued", 0),
                )
            )
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


def _enqueue_legacy_import(mode: str = "legacy_import", queued_key: str = "dm_legacy_queued") -> None:
    """Docker 模式走触发队列；本地模式直接后台启动对应进程。"""
    main_py = _PROJECT_ROOT / "main.py"
    if not main_py.exists():
        try:
            enqueue_trigger(_PROJECT_ROOT / "data", mode)
        except Exception as e:
            st.error(f"{t('dm_legacy_failed')}: {e}")
            return
        st.toast(t(queued_key), icon="📜")
        st.rerun()

    logs_dir = _PROJECT_ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"{mode}_{datetime.now():%Y%m%d_%H%M%S}.log"
    try:
        with open(log_file, "w") as lf:
            subprocess.Popen(
                [sys.executable, str(main_py), "--mode", mode],
                cwd=str(_PROJECT_ROOT),
                stdout=lf,
                stderr=lf,
                start_new_session=True,
            )
        st.toast(t(queued_key), icon="📜")
        st.rerun()
    except Exception as e:
        st.error(f"{t('dm_legacy_failed')}: {e}")


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
        "backup_keep": current_cfg("backup_keep", 5),
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


def _list_backups():
    """List rotated database backups under the shared data volume."""
    try:
        from utils.backup import list_local_backups

        return list_local_backups(_PROJECT_ROOT / "data")
    except Exception as e:
        logger.warning(f"列出现有备份失败: {e}")
        return []


def _do_backup(env_values: dict):
    """立即创建一次压缩数据库备份（WebDAV 已配置时默认镜像上传）。"""
    try:
        from utils.backup import create_backup
        from utils.webdav_sync import WebDAVSync

        # 备份默认压缩后上传：只要 WebDAV 凭据可用（表单或 .env）就镜像，
        # 不再提供单独的上传开关。
        url = (st.session_state.get("webdav_url") or env_values.get("WEBDAV_URL") or "").strip()
        username = (
            st.session_state.get("webdav_username") or env_values.get("WEBDAV_USERNAME") or ""
        ).strip()
        password = resolve_secret_value(
            env_values, "WEBDAV_PASSWORD", "webdav_password", st.session_state
        )
        webdav_sync = None
        if url and username:
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

        keep = int(st.session_state.get("backup_keep", 5) or 5)
        with st.spinner(t("dm_backup_running")):
            result = create_backup(
                _PROJECT_ROOT / "data", keep=keep, webdav_sync=webdav_sync
            )

        if not result.get("created"):
            st.warning(t("dm_backup_skip_reason").format(result.get("reason", "")))
            return
        if result.get("uploaded"):
            st.success(t("dm_backup_done_uploaded"))
        elif result.get("upload_error"):
            st.warning(t("dm_backup_done_upload_failed").format(result["upload_error"]))
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

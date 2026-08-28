#!/usr/bin/env python3
"""
ArXiv Daily Researcher - Streamlit Config Panel

Usage:
    streamlit run src/webui/config_panel.py

    Docker:
    docker compose up -d config-panel
"""

import sys
from pathlib import Path

# Add src to path for config_io imports (src/webui/ -> src/ -> project root)
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root / "src"))

import streamlit as st

from utils.config_io import (
    read_env,
    write_env,
    read_config_json,
    write_config_json,
    flatten_config_dict,
    build_config_dict,
    DEFAULT_ENV_PATH,
    DEFAULT_CONFIG_PATH,
)

from webui.styles import CUSTOM_CSS
from webui.tabs import llm, search, keywords, scoring, notifications, advanced, reports
from webui.tabs import favorites, run_manager, trend_runner, data_management
from webui.tabs.analytics import (
    render_content as render_analytics,
    render_diagnostics,
)
from webui.auth import (
    render_account_controls,
    render_account_management,
    require_authentication,
)
from webui.i18n import t
from webui.navigation import NAVIGATION_GROUP_IDS, NAVIGATION_GROUPS
from webui.secret_fields import clear_secret_field_state


# ==================== Page Config ====================

st.set_page_config(
    page_title="ArXiv Researcher - Config",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# Initialize language (default: Chinese)
if "lang" not in st.session_state:
    st.session_state["lang"] = "zh"


# ==================== Data Loading ====================


@st.cache_data(ttl=5)
def load_env():
    return read_env()


@st.cache_data(ttl=5)
def load_config():
    raw = read_config_json()
    return flatten_config_dict(raw) if raw else {}


# Gate the panel before rendering navigation, reports, logs, or any writable
# configuration control. The initial local setup screen appears only while no
# administrator password hash has been configured.
initial_env_values = load_env()
if not require_authentication(initial_env_values):
    st.stop()


def do_save():
    """保存所有配置到磁盘。"""
    env_values = load_env()
    config_values = load_config()

    # Collect from all tabs
    env_updates = {}
    config_updates = {}

    # API tab -> env only
    env_updates.update(llm.collect(env_values, config_values))

    # Search tab -> config only
    config_updates.update(search.collect(env_values, config_values))

    # Keywords tab -> config only
    config_updates.update(keywords.collect(env_values, config_values))

    # Scoring tab -> config only
    config_updates.update(scoring.collect(env_values, config_values))

    # Notifications tab -> both env and config
    notif_env, notif_cfg = notifications.collect(env_values, config_values)
    env_updates.update(notif_env)
    config_updates.update(notif_cfg)

    # Advanced tab -> config only
    config_updates.update(advanced.collect(env_values, config_values))

    # Run Manager tab -> config only（每日研究设置）
    config_updates.update(run_manager.collect(env_values, config_values))

    # Data Management tab -> both env and config
    dm_env, dm_cfg = data_management.collect(env_values, config_values)
    env_updates.update(dm_env)
    config_updates.update(dm_cfg)

    # Trend Runner tab -> config only（趋势分析配置）
    config_updates.update(trend_runner.collect(env_values, config_values))

    # Merge and write env
    merged_env = {**env_values, **env_updates}
    write_env(merged_env)

    # Merge and write config
    merged_config = {**config_values, **config_updates}
    config_dict = build_config_dict(**merged_config)
    write_config_json(config_dict)

    # Never retain newly entered credentials in Streamlit session state after
    # persisting them.  Saved secrets are intentionally not rendered back into
    # password widgets on the next run.
    clear_secret_field_state(
        st.session_state,
        (*llm.SECRET_FIELD_KEYS, *notifications.SECRET_FIELD_KEYS, *data_management.SECRET_FIELD_KEYS),
    )

    # Clear cache to reload fresh data
    st.cache_data.clear()


# ==================== Sidebar ====================


if st.session_state.get("webui_active_group") not in NAVIGATION_GROUP_IDS:
    st.session_state["webui_active_group"] = "run"


with st.sidebar:
    st.markdown("### ArXiv Daily Researcher")
    st.caption(t("sidebar_caption"))

    for group_id, group_label_key, _items in NAVIGATION_GROUPS:
        active = st.session_state.get("webui_active_group") == group_id
        if st.button(
            t(group_label_key),
            key=f"nav_group_{group_id}",
            type="primary" if active else "secondary",
            width="stretch",
        ):
            st.session_state["webui_active_group"] = group_id
            st.rerun()

    st.divider()

    if st.button(t("save_btn"), type="primary", width="stretch", key="save_btn"):
        try:
            do_save()
            st.success(t("save_success"))
        except Exception as e:
            st.error(t("save_failed") + str(e))

    if st.button(t("reload_btn"), width="stretch", key="reload_btn"):
        st.cache_data.clear()
        st.rerun()

    # 重启主研究容器：写入共享卷标记，worker 的 trigger_watcher 收到后
    # 结束容器（restart: unless-stopped 会自动拉起，并按最新 config 重装 cron）。
    if st.button(t("restart_worker_btn"), width="stretch", key="restart_worker_btn"):
        import datetime as _dt

        marker = _project_root / "data" / "run" / "webui_triggers" / "restart_worker.request"
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                f"requested_at={_dt.datetime.now().isoformat()}\n", encoding="utf-8"
            )
            st.toast(t("restart_worker_sent"), icon="🔄")
        except OSError as exc:
            st.error(t("restart_worker_failed").format(err=exc))

    st.divider()

    # File status
    env_exists = DEFAULT_ENV_PATH.exists()
    cfg_exists = DEFAULT_CONFIG_PATH.exists()
    found = t("file_found")
    not_found = t("file_not_found")
    st.markdown(f"`.env`: {found if env_exists else not_found}")
    st.markdown(f"`config.json`: {found if cfg_exists else not_found}")

    st.divider()

    # Language toggle
    lang_label = t("lang_toggle")
    if st.button(lang_label, width="stretch", key="lang_btn"):
        st.session_state["lang"] = "en" if st.session_state["lang"] == "zh" else "zh"
        st.rerun()

    render_account_controls(initial_env_values)

    version_file = _project_root / "VERSION"
    app_version = (
        version_file.read_text(encoding="utf-8").strip()
        if version_file.exists()
        else "unknown"
    )
    st.caption(f"v{app_version} | Powered by Streamlit")


# ==================== Main Content ====================

# Load data
env_values = load_env()
config_values = load_config()

pages = {
    "daily_research": run_manager.render_daily_research,
    "past_daily": run_manager.render_past_daily_reports,
    "trend_tasks": trend_runner.render,
    "logs": run_manager.render_logs,
    "reports": reports.render,
    "favorites": favorites.render_favorites,
    "paper_search": favorites.render_search,
    "analytics": render_analytics,
    "keywords": keywords.render,
    "data_sources": search.render,
    "scoring": scoring.render,
    "api": llm.render,
    "notifications": notifications.render,
    "advanced": advanced.render,
    "accounts": lambda env, _config: render_account_management(env),
    "backup_sync": data_management.render_backup_sync,
    "history_tasks": data_management.render_history_maintenance,
    "diagnostics": render_diagnostics,
}

# The sidebar selects a workflow-level group only. Its individual tools retain
# the native horizontal Streamlit tabs that the panel used before the sidebar
# reorganization, so familiar tab switching remains available in the main area.
active_group = st.session_state.get("webui_active_group", "run")
_group_id, _group_label_key, active_items = next(
    group
    for group in NAVIGATION_GROUPS
    if group[0] == active_group
)
top_tabs = st.tabs([t(label_key) for _page_id, label_key in active_items])
for tab, (page_id, _label_key) in zip(top_tabs, active_items):
    with tab:
        pages[page_id](env_values, config_values)

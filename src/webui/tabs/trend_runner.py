"""趋势分析 Tab — 配置并启动研究趋势分析任务。"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st

from utils.run_lock import is_lock_held
from utils.webui_trigger import enqueue_trigger
from webui.i18n import t, _TRANSLATIONS

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_MAIN_PY = _PROJECT_ROOT / "main.py"
_LOCK_DIR = _PROJECT_ROOT / "data" / "run"
_LOGS_DIR = _PROJECT_ROOT / "logs"
_IS_DOCKER_WEBUI = not _MAIN_PY.exists()

# 所有可用的趋势分析技能
ALL_TREND_SKILL_IDS = [
    "hot_topics",
    "time_evolution",
    "key_researchers",
    "research_gaps",
    "methodology_trends",
    "comprehensive_analysis",
]


def _get_trend_lock_files() -> list[Path]:
    if not _LOCK_DIR.exists():
        return []
    return list(_LOCK_DIR.glob("trend_research_*.lock"))


def _is_lock_held(lock_path: Path) -> bool:
    """Use the OS flock state; lock-file PIDs are diagnostic only."""
    try:
        return is_lock_held(lock_path)
    except OSError:
        return True


def _read_pid_from_lock(lock_path: Path):
    try:
        content = lock_path.read_text(encoding="utf-8")
        m = re.search(r"PID=(\d+)", content)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _skill_label(skill_id: str) -> str:
    """获取技能的当前语言标签。"""
    lang = st.session_state.get("lang", "zh")
    entry = _TRANSLATIONS.get(f"skill_{skill_id}", {})
    return entry.get(lang, entry.get("en", skill_id.replace("_", " ").title()))


def _build_output_formats() -> list[str]:
    """从两个 toggle 值构建输出格式列表。"""
    formats = []
    if st.session_state.get("trend_output_md", True):
        formats.append("markdown")
    if st.session_state.get("trend_output_html", True):
        formats.append("html")
    return formats


def render(_env_values: dict, config_values: dict) -> None:
    """渲染趋势分析 Tab。"""
    from utils.config_io import flatten_config_dict

    flat = flatten_config_dict(config_values) if config_values else {}

    # ── 1. 运行控制（最顶部）──────────────────────────────────────────────
    st.markdown(
        f'<p class="section-title">🚀 {t("tr_section_run_control")}</p>',
        unsafe_allow_html=True,
    )

    # 当前趋势分析进程状态
    trend_locks = _get_trend_lock_files()
    any_trend_running = False
    if trend_locks:
        st.info(t("tr_locks_found").format(n=len(trend_locks)))
        for lock in trend_locks:
            pid = _read_pid_from_lock(lock)
            is_running = _is_lock_held(lock)
            any_trend_running = any_trend_running or is_running
            status = f"🟢 {t('rm_status_running')}" if is_running else f"🔴 {t('rm_status_stopped')}"
            st.caption(f"{status} — `{lock.name}` PID={pid or t('rm_no_pid')}")

    # 触发队列守卫：与每日推送一致，pending/运行中不允许重复提交，
    # 否则重复请求只会在 worker 侧被锁静默吞掉，用户无从得知。
    from webui.tabs.run_manager import _trigger_age_seconds

    trigger_age = _trigger_age_seconds()
    trigger_pending = trigger_age is not None and trigger_age <= 30
    if trigger_pending:
        st.info(f"⏳ {t('rm_trigger_pending')}")
    can_run = not trigger_pending and not any_trend_running

    # 关键词输入放在运行按钮前（运行需要关键词）
    keywords_input = st.text_input(
        t("trend_keywords_label"),
        value="",
        key="tr_keywords",
        placeholder=t("trend_keywords_placeholder"),
        help=t("trend_keywords_help"),
    )

    col_run, _ = st.columns([1, 4])

    with col_run:
        run_clicked = st.button(
            t("trend_run_btn"),
            key="tr_run_btn",
            type="primary",
            use_container_width=True,
            disabled=not can_run,
        )

    st.divider()

    # ── 2. 分析参数 ──────────────────────────────────────────────────────────
    st.markdown(
        f'<p class="section-title">🔍 {t("tr_section_params")}</p>',
        unsafe_allow_html=True,
    )

    col_d1, col_d2 = st.columns(2)
    default_days = flat.get("trend_default_date_range_days", 365)
    with col_d1:
        date_from = st.date_input(
            t("trend_date_from"),
            value=date.today() - timedelta(days=default_days),
            key="tr_date_from",
        )
    with col_d2:
        date_to = st.date_input(
            t("trend_date_to"),
            value=date.today(),
            key="tr_date_to",
        )

    categories_input = st.text_input(
        t("trend_categories_label"),
        value="",
        key="tr_categories",
        placeholder=t("tr_categories_placeholder"),
        help=t("trend_categories_help"),
    )

    st.divider()

    # ── 3. 分析配置 ──────────────────────────────────────────────────────────
    st.markdown(
        f'<p class="section-title">⚙️ {t("trend_config_title")}</p>',
        unsafe_allow_html=True,
    )

    col_c1, col_c2 = st.columns(2)
    with col_c1:
        sort_options = ["ascending", "descending"]
        current_sort = flat.get("trend_sort_order", "ascending")
        st.selectbox(
            t("trend_sort_label"),
            options=sort_options,
            index=sort_options.index(current_sort) if current_sort in sort_options else 0,
            key="trend_sort_order",
        )
        st.number_input(
            t("tr_default_date_range_days_label"),
            min_value=30,
            max_value=3650,
            value=flat.get("trend_default_date_range_days", 365),
            key="trend_default_date_range_days",
            help=t("tr_default_date_range_days_help"),
        )

    with col_c2:
        st.number_input(
            t("trend_max_results_label"),
            min_value=10,
            max_value=5000,
            value=flat.get("trend_max_results", 500),
            key="trend_max_results",
        )
        pos_options = ["beginning", "end"]
        current_pos = flat.get("trend_report_position", "end")
        st.selectbox(
            t("trend_report_position_label"),
            options=pos_options,
            index=pos_options.index(current_pos) if current_pos in pos_options else 1,
            key="trend_report_position",
        )

    col_c3, col_c4 = st.columns(2)
    with col_c3:
        st.toggle(
            t("trend_generate_tldr_label"),
            value=flat.get("trend_generate_tldr", True),
            key="trend_generate_tldr",
        )
    with col_c4:
        st.number_input(
            t("trend_tldr_batch_label"),
            min_value=1,
            max_value=50,
            value=flat.get("trend_tldr_batch_size", 10),
            key="trend_tldr_batch_size",
        )

    # 输出格式
    output_formats = flat.get("trend_output_formats", ["markdown", "html"])
    st.markdown(f"**{t('trend_output_formats_label')}**")
    col_fmt1, col_fmt2, col_fmt3 = st.columns(3)
    with col_fmt1:
        st.toggle(
            t("trend_output_md_label"),
            value="markdown" in output_formats,
            key="trend_output_md",
        )
    with col_fmt2:
        st.toggle(
            t("trend_output_html_label"),
            value="html" in output_formats,
            key="trend_output_html",
        )

    # 启用的技能
    st.markdown(f"**{t('trend_skills_label')}**")
    current_skills = flat.get("trend_enabled_skills", ALL_TREND_SKILL_IDS)
    skill_cols = st.columns(3)
    for i, skill_id in enumerate(ALL_TREND_SKILL_IDS):
        with skill_cols[i % 3]:
            st.checkbox(
                _skill_label(skill_id),
                value=skill_id in current_skills,
                key=f"skill_{skill_id}",
            )

    # ── 处理按钮逻辑 ────────────────────────────────────────────────────────

    # 处理运行
    if run_clicked:
        if not keywords_input.strip():
            st.error(t("tr_err_no_keywords"))
        elif date_from > date_to:
            st.error(t("tr_err_date_range"))
        else:
            try:
                keyword_list = shlex.split(keywords_input)
                categories = shlex.split(categories_input) if categories_input.strip() else []
            except ValueError as e:
                st.error(t("tr_start_failed").format(err=e))
                return

            request_args = {
                "keywords": keyword_list,
                "date_from": str(date_from),
                "date_to": str(date_to),
                "categories": categories,
                "sort_order": st.session_state.get("trend_sort_order", "ascending"),
                "max_results": int(st.session_state.get("trend_max_results", 500)),
            }
            try:
                if _IS_DOCKER_WEBUI:
                    request_path = enqueue_trigger(
                        _LOCK_DIR.parent, "trend_research", **request_args
                    )
                    st.success(t("tr_started").format(pid=request_path.stem))
                else:
                    command = [
                        sys.executable,
                        str(_MAIN_PY),
                        "--mode",
                        "trend_research",
                        "--keywords",
                        *keyword_list,
                        "--date-from",
                        str(date_from),
                        "--date-to",
                        str(date_to),
                        "--max-results",
                        str(request_args["max_results"]),
                        "--sort-order",
                        request_args["sort_order"],
                    ]
                    if categories:
                        command.extend(["--categories", *categories])
                    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
                    log_file = _LOGS_DIR / f"manual_trend_{datetime.now():%Y%m%d_%H%M%S}.log"
                    with log_file.open("w", encoding="utf-8") as handle:
                        proc = subprocess.Popen(
                            command,
                            cwd=str(_PROJECT_ROOT),
                            stdout=handle,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                    st.success(t("tr_started").format(pid=proc.pid))
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.error(t("tr_start_failed").format(err=e))


def collect(_env_values: dict, _config_values: dict) -> dict:
    """从 session_state 收集趋势分析配置，保存到 config.json。"""
    flat = _config_values or {}

    def current(key: str, default):
        return st.session_state.get(key, flat.get(key, default))

    configured_skills = flat.get("trend_enabled_skills", [])
    if not isinstance(configured_skills, list):
        configured_skills = []
    enabled_skills = [
        skill_id
        for skill_id in ALL_TREND_SKILL_IDS
        if st.session_state.get(f"skill_{skill_id}", skill_id in configured_skills)
    ]

    configured_formats = flat.get("trend_output_formats", ["markdown", "html"])
    if not isinstance(configured_formats, list) or not configured_formats:
        configured_formats = ["markdown", "html"]

    def current_formats() -> list[str]:
        """两个输出格式 toggle 的值；未渲染时回退到配置文件现值。"""
        formats = []
        if st.session_state.get(
            "trend_output_md", "markdown" in configured_formats
        ):
            formats.append("markdown")
        if st.session_state.get(
            "trend_output_html", "html" in configured_formats
        ):
            formats.append("html")
        return formats

    return {
        "trend_default_date_range_days": current("trend_default_date_range_days", 365),
        "trend_max_results": current("trend_max_results", 500),
        "trend_sort_order": current("trend_sort_order", "ascending"),
        "trend_report_position": current("trend_report_position", "end"),
        "trend_generate_tldr": current("trend_generate_tldr", True),
        "trend_tldr_batch_size": current("trend_tldr_batch_size", 10),
        "trend_output_formats": current_formats(),
        "trend_enabled_skills": enabled_skills,
    }

"""趋势分析 Tab — 配置并启动研究趋势分析任务。"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st

from utils.run_lock import is_lock_held
from utils.webui_trigger import enqueue_trigger
from webui.arxiv_categories import ARXIV_CATEGORIES, format_arxiv_category
from webui.i18n import t, _TRANSLATIONS

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_MAIN_PY = _PROJECT_ROOT / "main.py"
_LOCK_DIR = _PROJECT_ROOT / "data" / "run"
_LOGS_DIR = _PROJECT_ROOT / "logs"
_IS_DOCKER_WEBUI = not _MAIN_PY.exists()

# 分析技能只保留综合分析；细分技能由自定义提示词取代
ALL_TREND_SKILL_IDS = [
    "comprehensive_analysis",
]

_MAX_PROMPT_LENGTH = 8000


def _templates_path() -> Path:
    # 不导入 worker 的 config 模块（WebUI 镜像不携带 src/config.py）；
    # DATA_DIR 固定为项目根下的 data/。
    return _PROJECT_ROOT / "data" / "trend_prompt_templates.json"


def _load_prompt_templates() -> dict[str, str]:
    path = _templates_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(name): str(text)
        for name, text in data.items()
        if isinstance(name, str) and isinstance(text, str) and name.strip()
    }


def _write_prompt_templates(templates: dict[str, str]) -> None:
    path = _templates_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再原子替换，避免半写状态被读到
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(templates, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


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

    st.multiselect(
        t("trend_categories_label"),
        options=ARXIV_CATEGORIES,
        default=[],
        format_func=format_arxiv_category,
        key="tr_categories",
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

    # 分析技能：只保留综合分析（默认勾选）
    st.markdown(f"**{t('trend_skills_label')}**")
    current_skills = flat.get("trend_enabled_skills", ALL_TREND_SKILL_IDS)
    skill_cols = st.columns(3)
    with skill_cols[0]:
        st.checkbox(
            _skill_label("comprehensive_analysis"),
            value="comprehensive_analysis" in current_skills,
            key="skill_comprehensive_analysis",
        )

    # 深度分析提示词：下拉选择已保存模板，新增在折叠表单里完成（与额外来源同款交互）
    templates = _load_prompt_templates()
    template_names = sorted(templates)
    none_label = t("trend_prompt_template_none")

    configured_prompt = (flat.get("trend_analysis_prompt", "") or "").strip()
    default_index = 0
    for offset, name in enumerate(template_names, start=1):
        if templates[name].strip() == configured_prompt:
            default_index = offset
            break

    col_tpl, col_tpl_del = st.columns([4, 1])
    with col_tpl:
        st.selectbox(
            t("trend_prompt_template_label"),
            [none_label, *template_names],
            index=default_index,
            key="tr_prompt_template",
            help=t("trend_prompt_template_help"),
        )
    selected_template = st.session_state.get("tr_prompt_template", none_label)
    active_prompt = (
        templates.get(selected_template, "") if selected_template != none_label else ""
    )

    with col_tpl_del:
        st.write("")
        can_delete = bool(
            selected_template != none_label and selected_template in templates
        )
        if st.button(
            t("trend_prompt_template_delete"),
            key="tr_prompt_template_delete",
            disabled=not can_delete,
            use_container_width=True,
        ):
            updated = {
                name: text for name, text in templates.items() if name != selected_template
            }
            try:
                _write_prompt_templates(updated)
                st.session_state["tr_prompt_template"] = none_label
                st.toast(t("trend_prompt_template_deleted"), icon="🗑")
                st.rerun()
            except OSError as exc:
                st.error(t("tr_start_failed").format(err=exc))

    with st.expander(t("trend_prompt_add_title"), expanded=False):
        st.text_input(
            t("trend_prompt_template_name_label"),
            value="",
            key="tr_prompt_new_name",
            placeholder=t("trend_prompt_template_name_placeholder"),
        )
        st.text_area(
            t("trend_prompt_label"),
            value="",
            height=180,
            max_chars=_MAX_PROMPT_LENGTH,
            key="tr_prompt_new_text",
            placeholder=t("trend_prompt_placeholder"),
            help=t("trend_prompt_help"),
        )
        if st.button(t("trend_prompt_add_btn"), key="tr_prompt_add"):
            name = (st.session_state.get("tr_prompt_new_name", "") or "").strip()
            text = (st.session_state.get("tr_prompt_new_text", "") or "").strip()
            if not name:
                st.error(t("trend_prompt_template_name_required"))
            elif not text:
                st.error(t("trend_prompt_template_text_required"))
            else:
                updated = dict(_load_prompt_templates())
                updated[name] = text
                try:
                    _write_prompt_templates(updated)
                    st.session_state["tr_prompt_new_name"] = ""
                    st.session_state["tr_prompt_new_text"] = ""
                    st.session_state["tr_prompt_template"] = name
                    st.toast(t("trend_prompt_template_saved"), icon="💾")
                    st.rerun()
                except OSError as exc:
                    st.error(t("tr_start_failed").format(err=exc))

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
                categories = [
                    code
                    for code in st.session_state.get("tr_categories", [])
                    if code in ARXIV_CATEGORIES
                ]
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
                "analysis_prompt": active_prompt.strip(),
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
                    if request_args["analysis_prompt"]:
                        command.extend(
                            ["--analysis-prompt", request_args["analysis_prompt"]]
                        )
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

    templates = _load_prompt_templates()
    selected_template = st.session_state.get("tr_prompt_template")
    if isinstance(selected_template, str) and selected_template in templates:
        analysis_prompt = templates[selected_template].strip()
    elif "tr_prompt_template" in st.session_state:
        # 页面已浏览且明确选择了「不使用模板」
        analysis_prompt = ""
    else:
        analysis_prompt = str(current("trend_analysis_prompt", "") or "").strip()

    return {
        "trend_default_date_range_days": current("trend_default_date_range_days", 365),
        "trend_max_results": current("trend_max_results", 500),
        "trend_sort_order": current("trend_sort_order", "ascending"),
        "trend_report_position": current("trend_report_position", "end"),
        "trend_generate_tldr": current("trend_generate_tldr", True),
        "trend_tldr_batch_size": current("trend_tldr_batch_size", 10),
        "trend_output_formats": current_formats(),
        "trend_enabled_skills": enabled_skills,
        "trend_analysis_prompt": analysis_prompt,
    }

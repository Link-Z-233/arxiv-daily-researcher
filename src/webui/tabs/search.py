"""Search & Data Sources tab for the Streamlit config panel."""

import streamlit as st
from webui.arxiv_categories import ARXIV_CATEGORIES, format_arxiv_category
from webui.i18n import t
from utils.source_registry import (
    builtin_extra_source_definitions,
    validate_source_definitions,
)

CORE_DATA_SOURCES = ("arxiv", "prl")


def render(_env_values: dict, config_values: dict):
    """Render the Search & Data Sources tab."""

    flat = config_values

    # ---- Search Settings ----
    st.markdown(
        f'<p class="section-title">🔎 {t("search_settings_title")}</p>', unsafe_allow_html=True
    )

    st.number_input(
        t("search_days_label"),
        min_value=1,
        max_value=365,
        value=flat.get("search_days", 7),
        key="search_days",
        help=t("search_days_help"),
    )
    st.info(t("daily_scan_all_results"))

    st.divider()

    # ---- Data Sources ----
    st.markdown(
        f'<p class="section-title">🧭 {t("data_sources_title")}</p>', unsafe_allow_html=True
    )

    current_sources = flat.get("enabled_sources", ["arxiv"])
    if not isinstance(current_sources, list):
        current_sources = []

    source_states = {}
    col_arxiv, col_prl = st.columns(2)
    with col_arxiv:
        source_states["arxiv"] = st.checkbox(
            "arXiv", value="arxiv" in current_sources, key="source_arxiv"
        )
    with col_prl:
        source_states["prl"] = st.checkbox(
            "PRL", value="prl" in current_sources, key="source_prl"
        )

    st.toggle(
        t("reports_by_source_toggle"),
        value=flat.get("reports_by_source", True),
        key="reports_by_source",
        help=t("reports_by_source_help"),
    )

    # ---- Extra Sources（数据源区内，紧跟核心来源；不单开小标题）----
    extra_cfg = flat.get("extra_source_definitions", [])
    if not isinstance(extra_cfg, list):
        extra_cfg = []
    extra_cfg = [d for d in extra_cfg if isinstance(d, dict)]
    extra_enabled = st.toggle(
        t("extra_sources_enabled"),
        value=bool(flat.get("extra_sources_enabled", False)),
        key="extra_sources_enabled",
        help=t("extra_sources_help"),
    )

    # 内置来源模板目录：PRA/PRB/Nature/…/Hugging Face Papers
    builtin_templates = {d["code"]: d for d in builtin_extra_source_definitions()}
    configured_codes = {str(d.get("code", "")).lower() for d in extra_cfg}

    # 自定义（非内置）来源保存在会话里，初始值来自磁盘配置
    if "extra_custom_definitions" not in st.session_state:
        st.session_state["extra_custom_definitions"] = [
            d
            for d in extra_cfg
            if str(d.get("code", "")).lower() not in builtin_templates
        ]

    selected_builtins: list[str] = []
    custom_definitions: list[dict] = list(st.session_state["extra_custom_definitions"])

    # 额外来源启用后才展开来源配置
    if extra_enabled:
        def _builtin_label(code: str) -> str:
            info = builtin_templates[code]
            return f"{info['display_name']}（{code}）"

        selected_builtins = st.multiselect(
            t("extra_sources_builtin_label"),
            options=list(builtin_templates),
            default=[c for c in builtin_templates if c in configured_codes],
            format_func=_builtin_label,
            key="extra_builtin_selected",
        )

        if custom_definitions:
            st.markdown(f"**{t('extra_sources_custom_title')}**")
            for index, definition in enumerate(custom_definitions):
                col_info, col_rm = st.columns([5, 1])
                issn_text = ", ".join(definition.get("issn", []))
                col_info.caption(
                    f"{definition.get('display_name', '?')} · "
                    f"`{definition.get('code', '?')}` · "
                    f"{definition.get('full_name', '')}"
                    + (f" · ISSN: {issn_text}" if issn_text else "")
                )
                if col_rm.button(
                    "✖",
                    key=f"extra_custom_rm_{index}",
                    use_container_width=True,
                    help=t("extra_sources_removed"),
                ):
                    st.session_state["extra_custom_definitions"].pop(index)
                    st.rerun()

        with st.expander(t("extra_sources_add_title"), expanded=False):
            col_add1, col_add2 = st.columns(2)
            with col_add1:
                st.text_input(
                    t("extra_sources_add_code"),
                    value="",
                    key="extra_new_code",
                    placeholder="optica_express",
                )
                st.text_input(
                    t("extra_sources_add_full"),
                    value="",
                    key="extra_new_full_name",
                    placeholder="Optics Express",
                )
            with col_add2:
                st.text_input(
                    t("extra_sources_add_display"),
                    value="",
                    key="extra_new_display_name",
                    placeholder="Opt. Express",
                )
                st.text_input(
                    t("extra_sources_add_issn"),
                    value="",
                    key="extra_new_issn",
                    placeholder="1094-4087",
                )
            if st.button(t("extra_sources_add_btn"), key="extra_new_add"):
                candidate = {
                    "type": "openalex_journal",
                    "code": st.session_state.get("extra_new_code", ""),
                    "display_name": st.session_state.get("extra_new_display_name", ""),
                    "full_name": st.session_state.get("extra_new_full_name", ""),
                    "issn": [
                        item.strip()
                        for item in st.session_state.get("extra_new_issn", "").split(",")
                        if item.strip()
                    ],
                }
                try:
                    validate_source_definitions([candidate])
                except ValueError as exc:
                    st.error(f"{t('extra_sources_invalid')}: {exc}")
                else:
                    st.session_state["extra_custom_definitions"].append(candidate)
                    for field in (
                        "extra_new_code",
                        "extra_new_display_name",
                        "extra_new_full_name",
                        "extra_new_issn",
                    ):
                        st.session_state[field] = ""
                    st.toast(t("extra_sources_added"), icon="✅")
                    st.rerun()

    # 由多选与自定义列表推导出最终定义（供 HF 参数区判断）
    try:
        parsed_extra = validate_source_definitions(
            [builtin_templates[code] for code in selected_builtins] + custom_definitions
        )
    except ValueError:
        parsed_extra = []

    extra_codes = {item["code"] for item in parsed_extra} if extra_enabled else set()
    if "huggingface_papers" in extra_codes:
        st.info(t("huggingface_papers_source_notice"))
        hf_col1, hf_col2 = st.columns(2)
        with hf_col1:
            st.number_input(
                t("huggingface_papers_availability_lag_label"),
                min_value=0,
                max_value=30,
                value=flat.get("huggingface_papers_availability_lag_days", 2),
                key="huggingface_papers_availability_lag_days",
                help=t("huggingface_papers_availability_lag_help"),
            )
            st.number_input(
                t("huggingface_papers_request_timeout_label"),
                min_value=5,
                max_value=600,
                value=flat.get("huggingface_papers_request_timeout_seconds", 30),
                key="huggingface_papers_request_timeout_seconds",
            )
        with hf_col2:
            st.number_input(
                t("huggingface_papers_lookback_grace_label"),
                min_value=0,
                max_value=30,
                value=flat.get("huggingface_papers_lookback_grace_days", 2),
                key="huggingface_papers_lookback_grace_days",
                help=t("huggingface_papers_lookback_grace_help"),
            )
            st.number_input(
                t("huggingface_papers_request_interval_label"),
                min_value=0.0,
                max_value=60.0,
                step=0.05,
                value=float(flat.get("huggingface_papers_request_interval_seconds", 0.25)),
                key="huggingface_papers_request_interval_seconds",
                help=t("huggingface_papers_request_interval_help"),
            )

    st.divider()

    # ---- ArXiv Settings（目标分类 + 抓取参数放在一起）----
    st.markdown(
        f'<p class="section-title">🗂️ {t("arxiv_settings_title")}</p>', unsafe_allow_html=True
    )
    st.markdown(f'<p class="hint-text">{t("arxiv_domains_hint")}</p>', unsafe_allow_html=True)

    current_domains = flat.get("domains", ["quant-ph"])
    if not isinstance(current_domains, list):
        current_domains = []

    st.multiselect(
        t("select_arxiv_cats"),
        options=ARXIV_CATEGORIES,
        default=[d for d in current_domains if d in ARXIV_CATEGORIES],
        format_func=format_arxiv_category,
        key="arxiv_domains",
    )

    col_ax1, col_ax2 = st.columns(2)
    with col_ax1:
        st.number_input(
            t("arxiv_fetch_timeout_label"),
            min_value=30,
            max_value=1800,
            value=flat.get("arxiv_fetch_timeout_seconds", 180),
            key="arxiv_fetch_timeout_seconds",
            help=t("arxiv_fetch_timeout_help"),
        )
    with col_ax2:
        st.number_input(
            t("arxiv_announcement_lookback_grace_label"),
            min_value=0,
            max_value=30,
            value=flat.get("arxiv_announcement_lookback_grace_days", 2),
            key="arxiv_announcement_lookback_grace_days",
            help=t("arxiv_announcement_lookback_grace_help"),
        )


def collect(_env_values: dict, _config_values: dict) -> dict:
    """Collect current values from session state. Returns config updates.

    Only the active page renders, so unvisited pages have no session state;
    fall back to the values already on disk before defaults.
    """
    flat = _config_values or {}

    def current(key: str, default):
        return st.session_state.get(key, flat.get(key, default))

    configured_sources = flat.get("enabled_sources", [])
    if not isinstance(configured_sources, list):
        configured_sources = []
    enabled = [
        src
        for src in CORE_DATA_SOURCES
        if st.session_state.get(f"source_{src}", src in configured_sources)
    ]

    configured_domains = flat.get("domains", ["quant-ph"])
    if not isinstance(configured_domains, list) or not configured_domains:
        configured_domains = ["quant-ph"]
    domains = [
        domain
        for domain in st.session_state.get("arxiv_domains", configured_domains)
        if domain in ARXIV_CATEGORIES
    ]

    configured_extra = flat.get("extra_source_definitions", [])
    if not isinstance(configured_extra, list):
        configured_extra = []
    configured_extra = [d for d in configured_extra if isinstance(d, dict)]

    builtin_templates = {d["code"]: d for d in builtin_extra_source_definitions()}
    configured_codes = {
        str(d.get("code", "")).lower() for d in configured_extra
    }

    # 页面未渲染时（保存前从未打开过本页）保留磁盘现值
    if "extra_builtin_selected" not in st.session_state:
        selected_builtins = [
            code for code in builtin_templates if code in configured_codes
        ]
    else:
        selected_builtins = list(st.session_state["extra_builtin_selected"])

    if "extra_custom_definitions" not in st.session_state:
        custom_definitions = [
            d
            for d in configured_extra
            if str(d.get("code", "")).lower() not in builtin_templates
        ]
    else:
        custom_definitions = list(st.session_state["extra_custom_definitions"])

    try:
        extra_definitions = validate_source_definitions(
            [builtin_templates[code] for code in selected_builtins] + custom_definitions
        )
    except ValueError as exc:
        raise ValueError(f"{t('extra_sources_invalid')}: {exc}") from exc

    return {
        "search_days": current("search_days", 7),
        "enabled_sources": enabled,
        "extra_sources_enabled": bool(current("extra_sources_enabled", False)),
        "extra_source_definitions": extra_definitions,
        "reports_by_source": current("reports_by_source", True),
        "arxiv_fetch_timeout_seconds": current("arxiv_fetch_timeout_seconds", 180),
        "arxiv_announcement_lookback_grace_days": current(
            "arxiv_announcement_lookback_grace_days", 2
        ),
        "huggingface_papers_availability_lag_days": current(
            "huggingface_papers_availability_lag_days", 2
        ),
        "huggingface_papers_lookback_grace_days": current(
            "huggingface_papers_lookback_grace_days", 2
        ),
        "huggingface_papers_request_timeout_seconds": current(
            "huggingface_papers_request_timeout_seconds", 30
        ),
        "huggingface_papers_request_interval_seconds": current(
            "huggingface_papers_request_interval_seconds", 0.25
        ),
        "domains": domains,
    }

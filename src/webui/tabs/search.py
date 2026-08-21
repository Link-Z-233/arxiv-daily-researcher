"""Search & Data Sources tab for the Streamlit config panel."""

import json
import streamlit as st
from webui.i18n import t
from utils.source_registry import (
    builtin_extra_source_definitions,
    validate_source_definitions,
)

CORE_DATA_SOURCES = ("arxiv", "prl")

# Common ArXiv categories
ARXIV_CATEGORIES = [
    "quant-ph",
    "cond-mat",
    "hep-th",
    "hep-ph",
    "hep-ex",
    "hep-lat",
    "gr-qc",
    "astro-ph",
    "nucl-th",
    "nucl-ex",
    "math-ph",
    "physics.atom-ph",
    "physics.optics",
    "physics.comp-ph",
    "cs.AI",
    "cs.LG",
    "cs.CL",
    "cs.CV",
    "cs.CR",
    "cs.SE",
    "stat.ML",
    "math.QA",
]


def render(_env_values: dict, config_values: dict):
    """Render the Search & Data Sources tab."""

    flat = config_values

    # ---- Search Settings ----
    st.markdown(
        f'<p class="section-title">🔎 {t("search_settings_title")}</p>', unsafe_allow_html=True
    )
    st.markdown(f'<p class="hint-text">{t("search_settings_hint")}</p>', unsafe_allow_html=True)

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
    st.markdown(f'<p class="hint-text">{t("data_sources_hint")}</p>', unsafe_allow_html=True)

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

    st.markdown(f"**{t('extra_sources_title')}**")
    extra_cfg = flat.get("extra_source_definitions", [])
    extra_enabled = st.toggle(
        t("extra_sources_enabled"),
        value=bool(flat.get("extra_sources_enabled", False)),
        key="extra_sources_enabled",
        help=t("extra_sources_help"),
    )
    default_json = json.dumps(
        extra_cfg,
        ensure_ascii=False,
        indent=2,
    )
    extra_json = st.text_area(
        t("extra_sources_json_label"),
        value=default_json,
        key="extra_source_definitions_json",
        height=220,
        disabled=not extra_enabled,
        help=t("extra_sources_json_help"),
    )
    with st.expander(t("extra_sources_templates_title"), expanded=False):
        st.caption(t("extra_sources_templates_help"))
        st.code(
            json.dumps(
                builtin_extra_source_definitions(), ensure_ascii=False, indent=2
            ),
            language="json",
        )
    if extra_enabled:
        try:
            parsed_extra = validate_source_definitions(json.loads(extra_json or "[]"))
            st.caption(t("extra_sources_valid"))
        except (ValueError, json.JSONDecodeError) as exc:
            st.error(f"{t('extra_sources_invalid')}: {exc}")
            parsed_extra = []

    st.toggle(
        t("reports_by_source_toggle"),
        value=flat.get("reports_by_source", True),
        key="reports_by_source",
        help=t("reports_by_source_help"),
    )

    st.number_input(
        t("arxiv_fetch_timeout_label"),
        min_value=30,
        max_value=1800,
        value=flat.get("arxiv_fetch_timeout_seconds", 180),
        key="arxiv_fetch_timeout_seconds",
        help=t("arxiv_fetch_timeout_help"),
    )

    st.number_input(
        t("arxiv_announcement_lookback_grace_label"),
        min_value=0,
        max_value=30,
        value=flat.get("arxiv_announcement_lookback_grace_days", 2),
        key="arxiv_announcement_lookback_grace_days",
        help=t("arxiv_announcement_lookback_grace_help"),
    )

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

    # ---- ArXiv Domains ----
    st.markdown(
        f'<p class="section-title">🗂️ {t("arxiv_domains_title")}</p>', unsafe_allow_html=True
    )
    st.markdown(f'<p class="hint-text">{t("arxiv_domains_hint")}</p>', unsafe_allow_html=True)

    current_domains = flat.get("domains", ["quant-ph"])
    if not isinstance(current_domains, list):
        current_domains = []

    st.multiselect(
        t("select_arxiv_cats"),
        options=ARXIV_CATEGORIES,
        default=[d for d in current_domains if d in ARXIV_CATEGORIES],
        key="arxiv_domains",
    )

    st.text_input(
        t("custom_domains_label"),
        value=", ".join(d for d in current_domains if d not in ARXIV_CATEGORIES),
        key="custom_domains",
        help=t("custom_domains_help"),
    )


def collect(_env_values: dict, _config_values: dict) -> dict:
    """Collect current values from session state. Returns config updates."""
    # Collect enabled sources
    enabled = [
        src for src in CORE_DATA_SOURCES if st.session_state.get(f"source_{src}", False)
    ]
    # Collect domains
    domains = list(st.session_state.get("arxiv_domains", ["quant-ph"]))
    custom = st.session_state.get("custom_domains", "")
    if custom:
        domains.extend(d.strip() for d in custom.split(",") if d.strip())

    raw_extra = st.session_state.get("extra_source_definitions_json", "[]")
    try:
        extra_definitions = validate_source_definitions(json.loads(raw_extra or "[]"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"额外来源配置无效: {exc}") from exc

    return {
        "search_days": st.session_state.get("search_days", 7),
        "enabled_sources": enabled,
        "extra_sources_enabled": bool(
            st.session_state.get("extra_sources_enabled", False)
        ),
        "extra_source_definitions": extra_definitions,
        "reports_by_source": st.session_state.get("reports_by_source", True),
        "arxiv_fetch_timeout_seconds": st.session_state.get("arxiv_fetch_timeout_seconds", 180),
        "arxiv_announcement_lookback_grace_days": st.session_state.get(
            "arxiv_announcement_lookback_grace_days", 2
        ),
        "huggingface_papers_availability_lag_days": st.session_state.get(
            "huggingface_papers_availability_lag_days", 2
        ),
        "huggingface_papers_lookback_grace_days": st.session_state.get(
            "huggingface_papers_lookback_grace_days", 2
        ),
        "huggingface_papers_request_timeout_seconds": st.session_state.get(
            "huggingface_papers_request_timeout_seconds", 30
        ),
        "huggingface_papers_request_interval_seconds": st.session_state.get(
            "huggingface_papers_request_interval_seconds", 0.25
        ),
        "domains": domains,
    }

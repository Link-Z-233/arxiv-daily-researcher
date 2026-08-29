"""Advanced Settings tab for the Streamlit config panel."""

import streamlit as st
from webui.i18n import t
from webui.tabs import proxy as _proxy_section


def render(env_values: dict, config_values: dict):
    """Render the Advanced Settings tab."""

    flat = config_values

    # ---- PDF Parser ----
    st.markdown(f'<p class="section-title">📄 {t("pdf_parser_title")}</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="hint-text">{t("pdf_parser_hint")}</p>', unsafe_allow_html=True)

    mode_options = ["pymupdf", "mineru"]
    current_mode = flat.get("pdf_parser_mode", "pymupdf")
    selected_mode = st.selectbox(
        t("parser_mode_label"),
        options=mode_options,
        index=mode_options.index(current_mode) if current_mode in mode_options else 0,
        key="pdf_parser_mode",
        help=t("parser_mode_help"),
    )

    # MinerU-specific options should never suggest that they affect the local
    # PyMuPDF parser.  They reappear immediately after MinerU is selected.
    if selected_mode == "mineru":
        version_options = ["pipeline", "vlm"]
        current_ver = flat.get("mineru_model_version", "pipeline")
        st.selectbox(
            t("mineru_version_label"),
            options=version_options,
            index=version_options.index(current_ver) if current_ver in version_options else 0,
            key="mineru_model_version",
            help=t("mineru_version_help"),
        )

    st.number_input(
        t("pdf_download_max_mb_label"),
        min_value=1,
        max_value=1024,
        value=max(
            1,
            int(flat.get("pdf_download_max_bytes", 50 * 1024 * 1024)) // (1024 * 1024),
        ),
        key="pdf_download_max_mb",
        help=t("pdf_download_max_mb_help"),
    )

    st.divider()

    # ---- Concurrency ----
    st.markdown(f'<p class="section-title">⚡ {t("concurrency_title")}</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="hint-text">{t("concurrency_hint")}</p>', unsafe_allow_html=True)

    col3, col4 = st.columns(2)
    with col3:
        st.toggle(
            t("enable_concurrency"),
            value=flat.get("concurrency_enabled", False),
            key="concurrency_enabled",
        )
    with col4:
        st.number_input(
            t("worker_threads_label"),
            min_value=1,
            max_value=10,
            value=flat.get("concurrency_workers", 3),
            key="concurrency_workers",
            help=t("worker_threads_help"),
        )

    st.divider()

    # ---- LLM Request Pool ----
    st.markdown(f'<p class="section-title">🚦 {t("llm_pool_title")}</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="hint-text">{t("llm_pool_hint")}</p>', unsafe_allow_html=True)

    col4a, col4b, col4c = st.columns(3)
    with col4a:
        st.toggle(
            t("enable_llm_pool"),
            value=flat.get("llm_request_pool_enabled", True),
            key="llm_request_pool_enabled",
        )
    with col4b:
        st.number_input(
            t("llm_rpm_label"),
            min_value=1,
            max_value=600,
            value=flat.get("llm_requests_per_minute", 30),
            key="llm_requests_per_minute",
        )
    with col4c:
        st.number_input(
            t("llm_slow_wait_label"),
            min_value=0.0,
            max_value=120.0,
            value=float(flat.get("llm_request_pool_log_slow_wait_seconds", 5.0)),
            key="llm_request_pool_log_slow_wait_seconds",
        )

    st.divider()

    # ---- Daily Research Persistence ----
    st.markdown(
        f'<p class="section-title">💾 {t("daily_persistence_title")}</p>',
        unsafe_allow_html=True,
    )
    st.markdown(f'<p class="hint-text">{t("daily_persistence_hint")}</p>', unsafe_allow_html=True)

    st.toggle(
        t("daily_deep_analysis_label"),
        value=flat.get("daily_enable_deep_analysis", True),
        key="daily_enable_deep_analysis",
    )
    st.text_input(
        t("daily_db_path_label"),
        value=flat.get("daily_research_db_path", "data/daily_research/daily_research.db"),
        key="daily_research_db_path",
    )

    st.divider()

    # ---- Report & Token Tracking ----
    st.markdown(
        f'<p class="section-title">📊 {t("advanced_reports_title")}</p>',
        unsafe_allow_html=True,
    )

    col5, col6 = st.columns(2)
    with col5:
        st.toggle(
            t("token_tracking_label"),
            value=flat.get("token_tracking_enabled", True),
            key="token_tracking_enabled",
        )
    with col6:
        st.toggle(
            t("auto_update_label"),
            value=flat.get("auto_update_enabled", True),
            key="auto_update_enabled",
            help=t("auto_update_help"),
        )

    st.divider()

    # ---- Keyword Tracker ----
    st.markdown(f'<p class="section-title">🧩 {t("kw_tracker_title")}</p>', unsafe_allow_html=True)

    st.toggle(
        t("enable_kw_tracker"),
        value=flat.get("keyword_tracker_enabled", True),
        key="keyword_tracker_enabled",
    )

    # 与代理配置一致：开关关闭时收起全部详细设置，磁盘现值保持不变。
    if st.session_state.get(
        "keyword_tracker_enabled", flat.get("keyword_tracker_enabled", True)
    ):
        col8, col9 = st.columns(2)
        with col8:
            st.toggle(
                t("ai_normalization_label"),
                value=flat.get("keyword_normalization_enabled", True),
                key="keyword_normalization_enabled",
            )
            st.number_input(
                t("normalization_batch_label"),
                min_value=5,
                max_value=100,
                value=flat.get("keyword_normalization_batch_size", 25),
                key="keyword_normalization_batch_size",
            )
        with col9:
            st.number_input(
                t("trend_view_days_label"),
                min_value=7,
                max_value=365,
                value=flat.get("keyword_trend_default_days", 30),
                key="keyword_trend_default_days",
            )

        col10, col11 = st.columns(2)
        with col10:
            st.number_input(
                t("bar_chart_top_n_label"),
                min_value=5,
                max_value=50,
                value=flat.get("keyword_chart_top_n", 15),
                key="keyword_chart_top_n",
            )
        with col11:
            st.number_input(
                t("trend_chart_top_n_label"),
                min_value=3,
                max_value=20,
                value=flat.get("keyword_trend_top_n", 5),
                key="keyword_trend_top_n",
            )

        st.toggle(
            t("enable_trend_reports_label"),
            value=flat.get("keyword_report_enabled", True),
            key="keyword_report_enabled",
        )

        freq_options = ["daily", "weekly", "monthly", "always"]
        current_freq = flat.get("keyword_report_frequency", "weekly")
        st.selectbox(
            t("report_frequency_label"),
            options=freq_options,
            index=freq_options.index(current_freq) if current_freq in freq_options else 1,
            key="keyword_report_frequency",
        )

    st.divider()

    # ---- Retry ----
    st.markdown(f'<p class="section-title">♻️ {t("retry_title")}</p>', unsafe_allow_html=True)

    col12, col13, col14 = st.columns(3)
    with col12:
        st.number_input(
            t("max_retries_label"),
            min_value=1,
            max_value=10,
            value=flat.get("retry_max_attempts", 3),
            key="retry_max_attempts",
        )
    with col13:
        st.number_input(
            t("min_wait_label"),
            min_value=1,
            max_value=60,
            value=flat.get("retry_min_wait", 2),
            key="retry_min_wait",
        )
    with col14:
        st.number_input(
            t("max_wait_label"),
            min_value=5,
            max_value=300,
            value=flat.get("retry_max_wait", 30),
            key="retry_max_wait",
        )

    st.number_input(
        t("run_lock_max_age_label"),
        min_value=1,
        max_value=168,
        value=flat.get("run_lock_max_age_hours", 12),
        key="run_lock_max_age_hours",
        help=t("run_lock_max_age_help"),
    )

    col15, col16 = st.columns(2)
    with col15:
        rot_options = ["time", "size"]
        current_rot = flat.get("log_rotation_type", "time")
        st.selectbox(
            t("log_rotation_label"),
            options=rot_options,
            index=rot_options.index(current_rot) if current_rot in rot_options else 0,
            key="log_rotation_type",
        )
    with col16:
        st.number_input(
            t("log_retention_label"),
            min_value=1,
            max_value=365,
            value=flat.get("log_keep_days", 30),
            key="log_keep_days",
        )

    # 趋势分析设置已移至「趋势分析」 Tab，请在趋势分析 Tab 中配置。

    st.divider()

    # ---- 网络代理（原独立 Tab 并入）----
    _proxy_section.render(env_values, config_values)


def collect(env_values: dict, config_values: dict) -> dict:
    """从 session_state 收集当前值，返回 config 更新字典。"""
    # 注意：趋势分析的配置已移至 trend_runner.py；网络代理并入本页，
    # 由 proxy.collect 一并收集。
    flat = config_values or {}

    def current(key: str, default):
        return st.session_state.get(key, flat.get(key, default))

    configured_pdf_mb = int(
        flat.get("pdf_download_max_bytes", 50 * 1024 * 1024)
    ) // (1024 * 1024)

    return {
        "pdf_parser_mode": current("pdf_parser_mode", "pymupdf"),
        "mineru_model_version": current("mineru_model_version", "pipeline"),
        "pdf_download_max_bytes": int(
            current("pdf_download_max_mb", configured_pdf_mb)
        ) * 1024 * 1024,
        "concurrency_enabled": current("concurrency_enabled", False),
        "concurrency_workers": current("concurrency_workers", 3),
        "llm_request_pool_enabled": current("llm_request_pool_enabled", True),
        "llm_requests_per_minute": current("llm_requests_per_minute", 30),
        "llm_request_pool_log_slow_wait_seconds": current("llm_request_pool_log_slow_wait_seconds", 5.0),
        "daily_research_persistence_enabled": True,
        "daily_research_db_path": current("daily_research_db_path", "data/daily_research/daily_research.db"),
        "daily_enable_deep_analysis": current("daily_enable_deep_analysis", True),
        "token_tracking_enabled": current("token_tracking_enabled", True),
        "auto_update_enabled": current("auto_update_enabled", True),
        "keyword_tracker_enabled": current("keyword_tracker_enabled", True),
        "keyword_normalization_enabled": current("keyword_normalization_enabled", True),
        "keyword_normalization_batch_size": current("keyword_normalization_batch_size", 25),
        # The modern panel can choose the LLM role.  Preserve that choice when
        # an operator later saves an unrelated setting through Streamlit.
        "keyword_normalization_llm_role": current("keyword_normalization_llm_role", "cheap"),
        "keyword_trend_default_days": current("keyword_trend_default_days", 30),
        "keyword_chart_top_n": current("keyword_chart_top_n", 15),
        "keyword_trend_top_n": current("keyword_trend_top_n", 5),
        "keyword_report_enabled": current("keyword_report_enabled", True),
        "keyword_report_frequency": current("keyword_report_frequency", "weekly"),
        "retry_max_attempts": current("retry_max_attempts", 3),
        "retry_min_wait": current("retry_min_wait", 2),
        "retry_max_wait": current("retry_max_wait", 30),
        "run_lock_max_age_hours": current("run_lock_max_age_hours", 12),
        "log_rotation_type": current("log_rotation_type", "time"),
        "log_keep_days": current("log_keep_days", 30),
        **_proxy_section.collect(env_values, config_values),
    }

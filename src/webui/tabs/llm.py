"""API 配置 Tab（含 LLM、MinerU 和其他第三方 API 配置）。"""

import streamlit as st
from webui.i18n import t
from webui.secret_fields import render_secret_input, resolve_secret_value


SECRET_FIELD_KEYS = (
    "cheap_api_key",
    "smart_api_key",
    "mineru_key",
    "semantic_scholar_key",
    "openalex_key",
)


# Provider presets
LLM_PROVIDERS = {
    "OpenAI": {"base_url": "https://api.openai.com/v1", "cheap": "gpt-4o-mini", "smart": "gpt-4o"},
    "DeepSeek": {
        "base_url": "https://api.deepseek.com/v1",
        "cheap": "deepseek-chat",
        "smart": "deepseek-chat",
    },
    "Ollama (Local)": {
        "base_url": "http://127.0.0.1:11434/v1",
        "cheap": "qwen2.5:7b",
        "smart": "qwen2.5:14b",
    },
    "Zhipu AI": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "cheap": "glm-4-flash",
        "smart": "glm-4",
    },
    "Custom": {"base_url": "", "cheap": "", "smart": ""},
}

MINERU_API_DASHBOARD_URL = "https://mineru.net/apiManage/apiKey"
OPENALEX_API_DASHBOARD_URL = "https://openalex.org/settings/api"
SEMANTIC_SCHOLAR_API_DASHBOARD_URL = (
    "https://www.semanticscholar.org/product/api#api-key-form"
)


def _detect_provider(base_url: str) -> str:
    """从 base_url 推断 LLM Provider。"""
    for name, info in LLM_PROVIDERS.items():
        if name == "Custom":
            continue
        if info["base_url"] and info["base_url"] in base_url:
            return name
    return "Custom"


def _mineru_parser_is_selected(config_values: dict) -> bool:
    """Return the unsaved/current parser selection without changing it."""
    configured_mode = (config_values or {}).get("pdf_parser_mode", "pymupdf")
    return st.session_state.get("pdf_parser_mode", configured_mode) == "mineru"


def _env_toggle(env_values: dict, key: str, default: bool) -> bool:
    """Read a boolean from `.env` without treating arbitrary text as true."""
    value = env_values.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def render(env_values: dict, _config_values: dict):
    """渲染 API 配置 Tab。"""

    # ---- CHEAP LLM ----
    st.markdown(f'<p class="section-title">💸 {t("cheap_llm_title")}</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="hint-text">{t("cheap_llm_hint")}</p>', unsafe_allow_html=True)

    current_cheap_base = env_values.get("CHEAP_LLM__BASE_URL", "https://api.openai.com/v1")
    detected_cheap = _detect_provider(current_cheap_base)

    col1, col2 = st.columns([1, 2])
    with col1:
        cheap_provider = st.selectbox(
            t("provider_preset"),
            options=list(LLM_PROVIDERS.keys()),
            index=list(LLM_PROVIDERS.keys()).index(detected_cheap),
            key="cheap_provider",
        )
    with col2:
        preset = LLM_PROVIDERS[cheap_provider]
        cheap_base = st.text_input(
            t("base_url"),
            value=current_cheap_base if cheap_provider == detected_cheap else preset["base_url"],
            key="cheap_base_url",
        )

    col3, col4 = st.columns(2)
    with col3:
        cheap_key = render_secret_input(
            st,
            label=t("api_key"),
            env_values=env_values,
            env_key="CHEAP_LLM__API_KEY",
            field_key="cheap_api_key",
            configured_hint=t("secret_configured_keep_blank"),
        )
    with col4:
        default_model = env_values.get("CHEAP_LLM__MODEL_NAME", preset["cheap"])
        cheap_model = st.text_input(
            t("model_name"),
            value=default_model,
            key="cheap_model_name",
        )

    cheap_temp = st.slider(
        t("temperature"),
        min_value=0.0,
        max_value=2.0,
        value=float(env_values.get("CHEAP_LLM__TEMPERATURE", "0.3")),
        step=0.1,
        key="cheap_temperature",
    )

    if st.button(t("test_cheap_btn"), key="test_cheap", type="secondary"):
        with st.spinner(t("testing_connection")):
            from utils.config_io import validate_llm_connection

            ok, msg = validate_llm_connection(
                resolve_secret_value(
                    env_values, "CHEAP_LLM__API_KEY", "cheap_api_key", st.session_state
                ),
                cheap_base,
                cheap_model,
            )
        if ok:
            st.success(msg)
        else:
            st.error(msg)

    st.divider()

    # ---- SMART LLM ----
    st.markdown(f'<p class="section-title">🧠 {t("smart_llm_title")}</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="hint-text">{t("smart_llm_hint")}</p>', unsafe_allow_html=True)

    current_smart_base = env_values.get("SMART_LLM__BASE_URL", "https://api.openai.com/v1")
    detected_smart = _detect_provider(current_smart_base)

    col5, col6 = st.columns([1, 2])
    with col5:
        smart_provider = st.selectbox(
            t("provider_preset"),
            options=list(LLM_PROVIDERS.keys()),
            index=list(LLM_PROVIDERS.keys()).index(detected_smart),
            key="smart_provider",
        )
    with col6:
        smart_preset = LLM_PROVIDERS[smart_provider]
        smart_base = st.text_input(
            t("base_url"),
            value=(
                current_smart_base if smart_provider == detected_smart else smart_preset["base_url"]
            ),
            key="smart_base_url",
        )

    col7, col8 = st.columns(2)
    with col7:
        smart_key = render_secret_input(
            st,
            label=t("api_key"),
            env_values=env_values,
            env_key="SMART_LLM__API_KEY",
            field_key="smart_api_key",
            configured_hint=t("secret_configured_keep_blank"),
        )
    with col8:
        default_smart_model = env_values.get("SMART_LLM__MODEL_NAME", smart_preset["smart"])
        smart_model = st.text_input(
            t("model_name"),
            value=default_smart_model,
            key="smart_model_name",
        )

    smart_temp = st.slider(
        t("temperature"),
        min_value=0.0,
        max_value=2.0,
        value=float(env_values.get("SMART_LLM__TEMPERATURE", "0.3")),
        step=0.1,
        key="smart_temperature",
    )

    if st.button(t("test_smart_btn"), key="test_smart", type="secondary"):
        with st.spinner(t("testing_connection")):
            from utils.config_io import validate_llm_connection

            ok, msg = validate_llm_connection(
                resolve_secret_value(
                    env_values, "SMART_LLM__API_KEY", "smart_api_key", st.session_state
                ),
                smart_base,
                smart_model,
            )
        if ok:
            st.success(msg)
        else:
            st.error(msg)

    # ---- MinerU API ----
    # This service is relevant only when the parser selected on the Advanced
    # page is MinerU.  Keeping it out of the page otherwise prevents stale
    # cloud credentials/options from looking active for PyMuPDF users.
    if _mineru_parser_is_selected(_config_values):
        st.divider()
        st.markdown(
            f'<p class="section-title">📄 {t("mineru_section_title")}</p>',
            unsafe_allow_html=True,
        )
        st.markdown(f'<p class="hint-text">{t("mineru_section_hint")}</p>', unsafe_allow_html=True)

        mineru_key = render_secret_input(
            st,
            label=t("mineru_api_key_label"),
            env_values=env_values,
            env_key="MINERU_API_KEY",
            field_key="mineru_key",
            configured_hint=t("secret_configured_keep_blank"),
            help=t("mineru_key_help"),
        )

        col_m1, col_m2 = st.columns([1, 3])
        with col_m1:
            test_mineru_btn = st.button(
                t("test_mineru_btn"),
                key="test_mineru",
                type="secondary",
                use_container_width=True,
            )
        with col_m2:
            st.caption(t("mineru_expire_note"))

        # Keep the official dashboard immediately below the test action so a
        # failed/expired token has an obvious, trustworthy renewal destination.
        st.markdown(f"[{t('mineru_console_link')}]({MINERU_API_DASHBOARD_URL})")

        if test_mineru_btn:
            with st.spinner(t("testing_mineru")):
                from utils.config_io import validate_mineru_connection

                ok, msg = validate_mineru_connection(
                    resolve_secret_value(env_values, "MINERU_API_KEY", "mineru_key", st.session_state)
                )
            if ok:
                st.success(msg)
            else:
                st.warning(msg)

    st.divider()

    # ---- 其他第三方 API Keys ----
    st.markdown(
        f'<p class="section-title">🔑 {t("third_party_keys_title")}</p>', unsafe_allow_html=True
    )
    st.markdown(f'<p class="hint-text">{t("third_party_keys_hint")}</p>', unsafe_allow_html=True)

    # ---- OpenAlex ----
    st.markdown(
        f'<p class="subsection-title">📚 {t("openalex_section_title")}</p>',
        unsafe_allow_html=True,
    )
    openalex_enabled = st.toggle(
        t("openalex_enabled_label"),
        value=_env_toggle(env_values, "ENABLE_OPENALEX", True),
        key="openalex_enabled",
        help=t("openalex_enabled_help"),
    )
    if openalex_enabled:
        st.markdown(f'<p class="hint-text">{t("openalex_section_hint")}</p>', unsafe_allow_html=True)
        render_secret_input(
            st,
            label=t("openalex_api_key_label"),
            env_values=env_values,
            env_key="OPENALEX_API_KEY",
            field_key="openalex_key",
            configured_hint=t("secret_configured_keep_blank"),
        )
        test_openalex_btn = st.button(
            t("test_openalex_btn"),
            key="test_openalex",
            type="secondary",
        )
        st.markdown(f"[{t('openalex_console_link')}]({OPENALEX_API_DASHBOARD_URL})")
        if test_openalex_btn:
            with st.spinner(t("testing_openalex")):
                from utils.config_io import validate_openalex_connection

                ok, msg = validate_openalex_connection(
                    resolve_secret_value(
                        env_values, "OPENALEX_API_KEY", "openalex_key", st.session_state
                    ),
                )
            if ok:
                st.success(msg)
            else:
                st.error(msg)

    st.divider()

    # ---- Semantic Scholar ----
    st.markdown(
        f'<p class="subsection-title">🧠 {t("semantic_scholar_section_title")}</p>',
        unsafe_allow_html=True,
    )
    semantic_scholar_enabled = st.toggle(
        t("semantic_scholar_enabled_label"),
        value=_env_toggle(env_values, "ENABLE_SEMANTIC_SCHOLAR_TLDR", True),
        key="semantic_scholar_enabled",
        help=t("semantic_scholar_enabled_help"),
    )
    if semantic_scholar_enabled:
        st.markdown(
            f'<p class="hint-text">{t("semantic_scholar_section_hint")}</p>',
            unsafe_allow_html=True,
        )
        render_secret_input(
            st,
            label=t("s2_api_key_label"),
            env_values=env_values,
            env_key="SEMANTIC_SCHOLAR_API_KEY",
            field_key="semantic_scholar_key",
            configured_hint=t("secret_configured_keep_blank"),
        )
        test_semantic_scholar_btn = st.button(
            t("test_semantic_scholar_btn"),
            key="test_semantic_scholar",
            type="secondary",
        )
        st.markdown(
            f"[{t('semantic_scholar_console_link')}]({SEMANTIC_SCHOLAR_API_DASHBOARD_URL})"
        )
        if test_semantic_scholar_btn:
            with st.spinner(t("testing_semantic_scholar")):
                from utils.config_io import validate_semantic_scholar_connection

                ok, msg = validate_semantic_scholar_connection(
                    resolve_secret_value(
                        env_values,
                        "SEMANTIC_SCHOLAR_API_KEY",
                        "semantic_scholar_key",
                        st.session_state,
                    )
                )
            if ok:
                st.success(msg)
            else:
                st.error(msg)


def collect(env_values: dict, _config_values: dict) -> dict:
    """从 session_state 收集当前值，返回 env 更新字典。

    未访问的页面没有会话状态；非密钥项回退到 .env 现值，避免保存时清空。
    """

    def current_env(session_key: str, env_key: str, default=""):
        if session_key in st.session_state:
            return st.session_state[session_key]
        value = env_values.get(env_key)
        return value if value not in (None, "") else default

    def current_toggle(session_key: str, env_key: str, default: bool) -> str:
        value = st.session_state.get(session_key, _env_toggle(env_values, env_key, default))
        # Streamlit returns booleans.  Parse a stale/manual string as well so
        # an old browser session containing ``\"false\"`` cannot be saved as on.
        enabled = _env_toggle({env_key: value}, env_key, default)
        return "true" if enabled else "false"

    return {
        "CHEAP_LLM__API_KEY": resolve_secret_value(
            env_values, "CHEAP_LLM__API_KEY", "cheap_api_key", st.session_state
        ),
        "CHEAP_LLM__BASE_URL": current_env("cheap_base_url", "CHEAP_LLM__BASE_URL"),
        "CHEAP_LLM__MODEL_NAME": current_env("cheap_model_name", "CHEAP_LLM__MODEL_NAME"),
        "CHEAP_LLM__TEMPERATURE": current_env(
            "cheap_temperature", "CHEAP_LLM__TEMPERATURE", "0.3"
        ),
        "SMART_LLM__API_KEY": resolve_secret_value(
            env_values, "SMART_LLM__API_KEY", "smart_api_key", st.session_state
        ),
        "SMART_LLM__BASE_URL": current_env("smart_base_url", "SMART_LLM__BASE_URL"),
        "SMART_LLM__MODEL_NAME": current_env("smart_model_name", "SMART_LLM__MODEL_NAME"),
        "SMART_LLM__TEMPERATURE": current_env(
            "smart_temperature", "SMART_LLM__TEMPERATURE", "0.3"
        ),
        "ENABLE_OPENALEX": current_toggle("openalex_enabled", "ENABLE_OPENALEX", True),
        "OPENALEX_API_KEY": resolve_secret_value(
            env_values, "OPENALEX_API_KEY", "openalex_key", st.session_state
        ),
        "ENABLE_SEMANTIC_SCHOLAR_TLDR": current_toggle(
            "semantic_scholar_enabled", "ENABLE_SEMANTIC_SCHOLAR_TLDR", True
        ),
        "SEMANTIC_SCHOLAR_API_KEY": resolve_secret_value(
            env_values,
            "SEMANTIC_SCHOLAR_API_KEY",
            "semantic_scholar_key",
            st.session_state,
        ),
        "MINERU_API_KEY": resolve_secret_value(
            env_values, "MINERU_API_KEY", "mineru_key", st.session_state
        ),
    }

"""Scoring Configuration tab for the Streamlit config panel."""

import streamlit as st
from webui.i18n import t


_MAX_VISIBLE_LIBRARY_ROWS = 10
_LIBRARY_SCROLL_HEIGHT_PX = 320


def render(_env_values: dict, config_values: dict):
    """Render the Scoring configuration tab."""

    flat = config_values

    # ---- Strategy / Qualification ----
    st.markdown(f'<p class="section-title">🧮 {t("scoring_title")}</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="hint-text">{t("scoring_hint")}</p>', unsafe_allow_html=True)

    strategy_options = [
        "core_relevance_v2",
        "legacy_weighted_keyword_v1",
        "learned_preference_v1",
    ]
    strategy = st.selectbox(
        t("score_strategy_label"),
        options=strategy_options,
        index=strategy_options.index(
            flat.get("score_strategy", "legacy_weighted_keyword_v1")
        )
        if flat.get("score_strategy", "legacy_weighted_keyword_v1") in strategy_options
        else 1,
        key="score_strategy",
        help=t("score_strategy_help"),
    )

    if strategy == "core_relevance_v2":
        st.info(t("core_relevance_info"))
        col1, col2, col3 = st.columns(3)
        with col1:
            st.number_input(
                t("core_relevance_threshold_label"),
                min_value=0.0,
                max_value=float(flat.get("max_score_per_keyword", 10)),
                value=float(flat.get("core_relevance_threshold", 6.0)),
                step=0.5,
                key="core_relevance_threshold",
            )
        with col2:
            st.number_input(
                t("core_keyword_min_score_label"),
                min_value=0.0,
                max_value=float(flat.get("max_score_per_keyword", 10)),
                value=float(flat.get("core_keyword_min_score", 7.0)),
                step=0.5,
                key="core_keyword_min_score",
            )
        with col3:
            st.number_input(
                t("reference_ranking_weight_label"),
                min_value=0.0,
                max_value=5.0,
                value=float(flat.get("reference_ranking_weight", 0.25)),
                step=0.05,
                key="reference_ranking_weight",
                help=t("reference_ranking_weight_help"),
            )
        if not flat.get("primary_keywords", []):
            st.warning(t("core_relevance_no_primary_warning"))
    elif strategy == "learned_preference_v1":
        st.info(t("learned_strategy_info"))
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            st.number_input(
                t("learned_weight_dampening_label"),
                min_value=0.0,
                max_value=1.0,
                value=float(flat.get("learned_weight_dampening", 0.5)),
                step=0.05,
                key="learned_weight_dampening",
                help=t("learned_weight_dampening_help"),
            )
        with col_d2:
            st.number_input(
                t("learned_term_weight_cap_label"),
                min_value=0.1,
                max_value=10.0,
                value=float(flat.get("learned_term_weight_cap", 2.0)),
                step=0.1,
                key="learned_term_weight_cap",
                help=t("learned_term_weight_cap_help"),
            )
        _render_learned_library_summary(flat)
    else:
        st.warning(t("legacy_strategy_warning"))

    if strategy in ("legacy_weighted_keyword_v1", "learned_preference_v1"):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.number_input(
                t("base_score_label"),
                min_value=0.0,
                max_value=100.0,
                value=float(flat.get("passing_score_base", 5.0)),
                step=0.5,
                key="passing_score_base",
            )
        with col2:
            st.number_input(
                t("weight_coeff_label"),
                min_value=0.0,
                max_value=20.0,
                value=float(flat.get("passing_score_weight_coefficient", 3.0)),
                step=0.5,
                key="passing_score_weight_coefficient",
            )
        with col3:
            st.number_input(
                t("max_score_per_kw_label"),
                min_value=1,
                max_value=100,
                value=flat.get("max_score_per_keyword", 10),
                key="max_score_per_keyword",
            )
    else:
        st.number_input(
            t("max_score_per_kw_label"),
            min_value=1,
            max_value=100,
            value=flat.get("max_score_per_keyword", 10),
            key="max_score_per_keyword",
        )

    if strategy != "core_relevance_v2":
        lang = st.session_state.get("lang", "zh")
        # Preview calculation belongs to the explicit legacy compatibility
        # policy; V2 has no weight-sum qualification formula.
        keywords = flat.get("primary_keywords", [])
        weight = flat.get("primary_keyword_weight", 1.0)
        base = st.session_state.get("passing_score_base", flat.get("passing_score_base", 5.0))
        coeff = st.session_state.get(
            "passing_score_weight_coefficient", flat.get("passing_score_weight_coefficient", 3.0)
        )
        total_weight = len(keywords) * weight
        passing = base + coeff * total_weight
        if lang == "zh":
            info_msg = (
                f"共 {len(keywords)} 个关键词，权重 {weight}："
                f"通过分数 = {base} + {coeff} × {total_weight:.1f} = **{passing:.1f}**"
            )
        else:
            info_msg = (
                f"With {len(keywords)} keyword(s) at weight {weight}: "
                f"Passing Score = {base} + {coeff} x {total_weight:.1f} = **{passing:.1f}**"
            )
        st.info(info_msg)

    st.divider()

    # ---- Author Bonus ----
    st.markdown(
        f'<p class="section-title">👤 {t("author_bonus_title")}</p>', unsafe_allow_html=True
    )
    st.markdown(f'<p class="hint-text">{t("author_bonus_hint")}</p>', unsafe_allow_html=True)

    st.toggle(
        t("enable_author_bonus"),
        value=flat.get("enable_author_bonus", False),
        key="enable_author_bonus",
    )

    if st.session_state.get("enable_author_bonus", False):
        col4, col5 = st.columns([3, 1])
        with col4:
            current_authors = flat.get("expert_authors", [])
            st.text_area(
                t("expert_authors_label"),
                value="\n".join(current_authors),
                height=100,
                key="expert_authors_text",
                help=t("expert_authors_help"),
            )
        with col5:
            st.number_input(
                t("bonus_points_label"),
                min_value=0.0,
                max_value=50.0,
                value=float(flat.get("author_bonus_points", 5.0)),
                step=0.5,
                key="author_bonus_points",
            )



def _render_learned_term_rows(terms: list[dict]) -> None:
    """Render all learned terms, using a native scroll viewport after 10."""
    def render_rows() -> None:
        for row in terms:
            st.markdown(f"- `{row['term']}` ({row['weight']:+.2f})")

    if len(terms) > _MAX_VISIBLE_LIBRARY_ROWS:
        with st.container(height=_LIBRARY_SCROLL_HEIGHT_PX, border=True):
            render_rows()
    else:
        render_rows()


def _render_learned_library_summary(flat: dict):
    """Show what the learned keyword/author library currently looks like."""
    try:
        from utils.daily_research_store import DailyResearchStore
        from webui.tabs.run_manager import _daily_db_path_from_config

        db_path = _daily_db_path_from_config(flat)
        if db_path is None or not db_path.exists():
            st.caption(t("learned_library_empty"))
            return
        store = DailyResearchStore(db_path)
        terms = store.get_learned_preference_terms(limit=30)
    except Exception:
        st.caption(t("learned_library_empty"))
        return

    if not terms:
        st.caption(t("learned_library_empty"))
        return

    keywords = [row for row in terms if row["term_type"] == "keyword"]
    authors = [row for row in terms if row["term_type"] == "author"]
    col_k, col_a = st.columns(2)
    with col_k:
        st.caption(t("learned_library_keywords"))
        _render_learned_term_rows(keywords)
    with col_a:
        st.caption(t("learned_library_authors"))
        _render_learned_term_rows(authors)
    st.caption(t("learned_library_note"))


def collect(_env_values: dict, _config_values: dict) -> dict:
    """Collect current values from session state. Returns config updates."""
    # Widgets only render for the active strategy, so session state may not
    # hold keys owned by the other branch.  Fall back to the values already
    # on disk before defaults; otherwise saving an unrelated tab would
    # silently rewrite the user's tuned formula.  config_values arrives
    # already flattened by load_config().
    flat = _config_values or {}

    def current(key: str, default):
        return st.session_state.get(key, flat.get(key, default))

    result = {
        "score_strategy": current("score_strategy", "core_relevance_v2"),
        # Once the user visits and saves the scoring tab, their selected
        # strategy becomes explicit.  Untouched legacy files remain legacy
        # because config_panel preserves the flat marker until this update.
        "score_strategy_explicit": True,
        "core_relevance_threshold": current("core_relevance_threshold", 6.0),
        "core_keyword_min_score": current("core_keyword_min_score", 7.0),
        "reference_ranking_weight": current("reference_ranking_weight", 0.25),
        "learned_weight_dampening": current("learned_weight_dampening", 0.5),
        "learned_term_weight_cap": current("learned_term_weight_cap", 2.0),
        "passing_score_base": current("passing_score_base", 5.0),
        "passing_score_weight_coefficient": current("passing_score_weight_coefficient", 3.0),
        "max_score_per_keyword": current("max_score_per_keyword", 10),
        "enable_author_bonus": current("enable_author_bonus", False),
    }

    if result["enable_author_bonus"]:
        authors_text = st.session_state.get("expert_authors_text", "")
        result["expert_authors"] = [a.strip() for a in authors_text.split("\n") if a.strip()]
        result["author_bonus_points"] = st.session_state.get("author_bonus_points", 5.0)

    return result

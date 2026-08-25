"""论文检索 Tab — 基于数据库元数据的全量历史检索。

数据永不删除，存量只增不减；本页用 SQLite 元数据（标题、作者、摘要、
TLDR、提取关键词）做全文匹配，并支持来源/日期/分数/收藏过滤。
只读展示，不做任何写入。
"""

from __future__ import annotations

import streamlit as st

from utils.daily_research_store import DailyResearchStore
from webui.i18n import t
from webui.tabs.run_manager import _daily_db_path_from_config

_PAGE_SIZE = 20


def _open_store(config_values: dict):
    db_path = _daily_db_path_from_config(config_values or {})
    if db_path is None or not db_path.exists():
        return None
    try:
        return DailyResearchStore(db_path)
    except Exception:
        return None


def _distinct_sources(store: DailyResearchStore) -> list[str]:
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT source FROM daily_papers ORDER BY source"
        ).fetchall()
    return [row["source"] for row in rows]


def render(env_values: dict, config_values: dict):
    """渲染论文检索 Tab。"""
    st.markdown(
        f'<p class="section-title">🔍 {t("ps_title")}</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="hint-text">{t("ps_hint")}</p>',
        unsafe_allow_html=True,
    )

    store = _open_store(config_values)
    if store is None:
        st.info(t("ps_no_data"))
        return

    sources = _distinct_sources(store)

    col_q, col_f = st.columns([3, 1])
    with col_q:
        query = st.text_input(
            t("ps_query_label"),
            key="ps_query",
            placeholder=t("ps_query_placeholder"),
        )
    with col_f:
        source_filter = st.selectbox(
            t("ps_source_label"),
            [t("ps_source_all"), *sources],
            key="ps_source",
        )

    col_date, col_score, col_like = st.columns(3)
    with col_date:
        date_range = st.date_input(
            t("ps_date_label"),
            value=(),
            key="ps_date_range",
            help=t("ps_date_help"),
        )
    with col_score:
        min_score = st.number_input(
            t("ps_min_score_label"),
            min_value=0.0,
            max_value=None,
            value=0.0,
            step=0.5,
            key="ps_min_score",
            help=t("ps_min_score_help"),
        )
    with col_like:
        st.checkbox(
            t("ps_liked_only_label"),
            value=False,
            key="ps_liked_only",
        )

    if "ps_executed" not in st.session_state:
        st.session_state["ps_executed"] = False
    if st.button(t("ps_search_btn"), type="primary", width="stretch"):
        st.session_state["ps_executed"] = True
        st.session_state["ps_page"] = 0
    if not st.session_state["ps_executed"]:
        st.caption(t("ps_idle_hint"))
        return

    selected_source = (
        None if source_filter == t("ps_source_all") else source_filter
    )
    completed_from = None
    completed_to = None
    if date_range:
        if len(date_range) == 2 and date_range[0] and date_range[1]:
            completed_from = date_range[0].isoformat()
            completed_to = date_range[1].isoformat()
        elif len(date_range) == 1 and date_range[0]:
            completed_from = date_range[0].isoformat()

    effective_min_score = min_score if min_score > 0 else None
    page = int(st.session_state.get("ps_page", 0))

    try:
        result = store.search_papers(
            query=query,
            source=selected_source,
            liked_only=bool(st.session_state.get("ps_liked_only", False)),
            min_score=effective_min_score,
            completed_from=completed_from,
            completed_to=completed_to,
            limit=_PAGE_SIZE,
            offset=page * _PAGE_SIZE,
        )
    except Exception as exc:
        st.error(t("ps_search_failed").format(exc))
        return

    total = result["total"]
    st.divider()
    st.markdown(
        f'<p class="section-title">{t("ps_result_title").format(total=total)}</p>',
        unsafe_allow_html=True,
    )
    if total == 0:
        st.info(t("ps_no_match"))
        return

    for item in result["items"]:
        _render_paper(item)

    _render_pagination(total, page)


def _render_paper(item: dict):
    score = item.get("total_score")
    score_text = f"{score:.1f}" if isinstance(score, (int, float)) else "—"
    qualified = item.get("is_qualified")
    if qualified is True:
        badge = "🟢"
    elif qualified is False:
        badge = "⚪"
    else:
        badge = "·"
    like = item.get("preference")
    like_icon = {"like": " 👍", "dislike": " 👎"}.get(like, "")

    header = f"{badge} {item['title']}{like_icon}"
    with st.expander(f"[{score_text}] {header}"):
        meta_parts = [
            f"{t('ps_col_source')}: {item.get('source', '—')}",
            f"{t('ps_col_completed')}: {(item.get('completed_at') or '—')[:19]}",
        ]
        if item.get("published_date"):
            meta_parts.append(
                f"{t('ps_col_published')}: {item['published_date']}"
            )
        if item.get("strategy_id"):
            meta_parts.append(f"{t('ps_col_strategy')}: `{item['strategy_id']}`")
        st.caption(" ｜ ".join(meta_parts))

        if item.get("authors"):
            st.markdown(
                f"**{t('ps_col_authors')}**: " + ", ".join(item["authors"][:12])
            )
        if item.get("tldr"):
            st.markdown(f"**TL;DR**: {item['tldr']}")
        if item.get("extracted_keywords"):
            st.markdown(
                f"**{t('ps_col_keywords')}**: "
                + " · ".join(item["extracted_keywords"])
            )
        if item.get("categories"):
            st.caption(
                f"{t('ps_col_categories')}: " + " ".join(item["categories"])
            )

        links = []
        if item.get("url"):
            links.append(f"[{t('ps_link_abs')}]({item['url']})")
        if item.get("pdf_url"):
            links.append(f"[{t('ps_link_pdf')}]({item['pdf_url']})")
        if links:
            st.markdown(" ｜ ".join(links))


def _render_pagination(total: int, page: int):
    pages = max(1, -(-total // _PAGE_SIZE))
    if pages <= 1:
        return
    col_prev, col_info, col_next = st.columns([1, 2, 1])
    if col_prev.button(
        t("ps_prev_page"), disabled=(page <= 0), width="stretch"
    ):
        st.session_state["ps_page"] = max(0, page - 1)
        st.rerun()
    col_info.caption(t("ps_page_info").format(page=page + 1, pages=pages))
    if col_next.button(
        t("ps_next_page"),
        disabled=(page >= pages - 1),
        width="stretch",
    ):
        st.session_state["ps_page"] = page + 1
        st.rerun()

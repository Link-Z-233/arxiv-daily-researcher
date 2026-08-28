"""收藏与检索 Tab — 按时间浏览收藏的论文、关键词统计与全量论文检索。

收藏（偏好）数据只增不删；本页为只读展示：标记时间倒序的收藏列表
（标题为论文页超链接）、收藏论文的关键词统计，以及下方的论文检索。
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from utils.daily_research_store import DailyResearchStore
from webui.i18n import t
from webui.tabs import paper_search


_MAX_VISIBLE_LIST_ROWS = 10
_TABLE_SCROLL_HEIGHT_PX = 390
_LIST_SCROLL_HEIGHT_PX = 420


def _fallback_url(source: str, paper_id: str) -> str | None:
    """元数据缺 URL 时按来源构造论文页链接（arXiv 短 id 可直接访问）。"""
    if source.lower() == "arxiv" and paper_id:
        return f"https://arxiv.org/abs/{paper_id}"
    return None


def _safe_href(url: str) -> str | None:
    if url.startswith(("http://", "https://")) and " " not in url.split("#")[0]:
        return html.escape(url, quote=True)
    return None


def _render_favorites_list(store: DailyResearchStore, liked: list[dict]) -> None:
    urls = store.liked_paper_urls()
    container_args = {"border": True}
    if len(liked) > _MAX_VISIBLE_LIST_ROWS:
        container_args["height"] = _LIST_SCROLL_HEIGHT_PX
    with st.container(**container_args):
        for row in liked:
            time_text = str(row.get("updated_at") or "")[:16].replace("T", " ")
            title = str(row.get("title") or row.get("paper_id") or "")
            url = (
                urls.get((row.get("source", ""), row.get("paper_id", "")))
                or _fallback_url(str(row.get("source", "")), str(row.get("paper_id", "")))
            )
            href = _safe_href(url) if url else None
            if href:
                label = (
                    f'<a href="{href}" target="_blank" rel="noopener noreferrer">'
                    f"{html.escape(title)}</a>"
                )
            else:
                label = html.escape(title)
            st.markdown(f"- `{time_text}` {label}", unsafe_allow_html=True)


def _render_preference_stats(store: DailyResearchStore) -> None:
    """收藏画像：关键词统计（取代旧的领域统计）+ 高频作者。"""

    def render_ranked_table(rows: list[dict], columns: list[str]) -> None:
        """Keep every statistic, with a native scroll viewport after 10 rows."""
        table = pd.DataFrame(rows, columns=columns)
        if len(table) > _MAX_VISIBLE_LIST_ROWS:
            with st.container(height=_TABLE_SCROLL_HEIGHT_PX, border=True):
                st.table(table)
        else:
            st.table(table)

    aggregation = store.aggregate_liked_preferences()
    col_authors, col_keywords = st.columns(2)
    with col_authors:
        st.markdown(f"**👤 {t('fav_top_authors')}**")
        if aggregation["authors"]:
            render_ranked_table(aggregation["authors"], ["name", "count"])
        else:
            st.caption(t("fav_no_marks"))
    with col_keywords:
        st.markdown(f"**🔑 {t('fav_keywords_title')}**")
        ranked = store.aggregate_liked_keywords()
        if ranked:
            render_ranked_table(ranked, ["keyword", "count"])
        else:
            st.caption(t("fav_keywords_empty"))


def render_favorites(_env_values: dict, config_values: dict) -> None:
    """Render the read-only paper-preference view and its profile summaries."""
    st.markdown(f'<p class="hint-text">{t("fav_hint")}</p>', unsafe_allow_html=True)

    store = paper_search._open_store(config_values)
    if store is None:
        st.info(t("ps_no_data"))
        return

    # ── 收藏的论文（按标记时间倒序）────────────────────────────────────
    st.markdown(
        f'<p class="section-title">⭐ {t("fav_list_title")}</p>', unsafe_allow_html=True
    )
    counts = store.get_preference_counts()
    if counts["like"] == 0 and counts["dislike"] == 0:
        st.caption(t("fav_no_marks"))
    else:
        col1, col2 = st.columns(2)
        with col1:
            st.metric(t("fav_likes"), counts["like"])
        with col2:
            st.metric(t("fav_dislikes"), counts["dislike"])

        liked = store.list_preferences(preference="like", limit=500)
        if liked:
            st.caption(t("fav_list_hint"))
            _render_favorites_list(store, liked)
        else:
            st.caption(t("fav_no_likes"))

        st.divider()
        _render_preference_stats(store)


def render_search(env_values: dict, config_values: dict) -> None:
    """Render the full SQLite paper search independently from favorites."""
    paper_search.render(env_values, config_values)


def render(env_values: dict, config_values: dict) -> None:
    """Backward-compatible composite view for the former combined tab."""
    render_favorites(env_values, config_values)
    st.divider()
    render_search(env_values, config_values)


def collect(_env_values: dict, _config_values: dict) -> dict:
    """收藏与检索 Tab 无配置需保存，返回空字典。"""
    return {}

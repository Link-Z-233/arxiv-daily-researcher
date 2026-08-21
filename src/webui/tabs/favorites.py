"""收藏偏好 Tab — 论文喜欢/不喜欢标注与兴趣画像汇总。

标记只进入 SQLite 的 paper_preferences 表并汇总为作者/领域/关键词画像；
不接入评分链路，评分策略不受任何影响。数据永不删除。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from utils.daily_research_store import DailyResearchStore
from webui.i18n import t
from webui.tabs.run_manager import _daily_db_path_from_config


def _match_primary_keywords(
    keywords: list[str], liked_titles: list[str]
) -> list[dict[str, object]]:
    """在已喜欢论文的标题里统计主要关键词命中次数（大小写不敏感）。"""
    lowered = [title.lower() for title in liked_titles]
    counts: list[dict[str, object]] = []
    for keyword in keywords:
        needle = keyword.strip().lower()
        if not needle:
            continue
        hits = sum(1 for title in lowered if needle in title)
        if hits:
            counts.append({"keyword": keyword, "count": hits})
    counts.sort(key=lambda item: (-int(item["count"]), str(item["keyword"])))
    return counts


def render(_env_values: dict, config_values: dict):
    st.markdown(
        f'<p class="section-title">⭐ {t("fav_title")}</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="hint-text">{t("fav_hint")}</p>',
        unsafe_allow_html=True,
    )

    db_path: Path = _daily_db_path_from_config(config_values)
    if not db_path.exists():
        st.info(t("fav_mark_empty"))
        return
    store = DailyResearchStore(db_path)

    # ==================== 论文标注 ====================
    st.markdown(
        f'<p class="subsection-title">📋 {t("fav_mark_title")}</p>',
        unsafe_allow_html=True,
    )
    papers = store.list_delivered_papers(limit=30)
    if not papers:
        st.info(t("fav_mark_empty"))
    else:
        preference_map = store.get_preference_map(papers)
        for paper in papers:
            key = (paper["source"], paper["paper_id"])
            current = preference_map.get(key)
            col_btn, col_info = st.columns([1, 4])
            with col_btn:
                btn_col1, btn_col2, btn_col3 = st.columns(3)
                clicked = None
                if btn_col1.button(
                    "👍", key=f"fav_like_{paper['source']}_{paper['paper_id']}",
                    help=t("fav_like"),
                    use_container_width=True,
                    type="primary" if current == "like" else "secondary",
                ):
                    clicked = "like"
                if btn_col2.button(
                    "👎", key=f"fav_dislike_{paper['source']}_{paper['paper_id']}",
                    help=t("fav_dislike"),
                    use_container_width=True,
                    type="primary" if current == "dislike" else "secondary",
                ):
                    clicked = "dislike"
                if btn_col3.button(
                    "✖", key=f"fav_clear_{paper['source']}_{paper['paper_id']}",
                    help=t("fav_clear"),
                    use_container_width=True,
                    disabled=current is None,
                ):
                    clicked = "none"
            with col_info:
                state_label = ""
                if current == "like":
                    state_label = f" · {t('fav_state_like')}"
                elif current == "dislike":
                    state_label = f" · {t('fav_state_dislike')}"
                st.markdown(f"**{paper['title']}**")
                meta_bits = [
                    paper["source"],
                    f"v{paper['version']}" if paper.get("version") else "",
                    (paper.get("completed_at") or "")[:10],
                ]
                st.caption(
                    " · ".join(bit for bit in meta_bits if bit) + state_label
                )

            if clicked is not None:
                store.set_paper_preference(
                    paper["source"],
                    paper["paper_id"],
                    preference=clicked,
                    title=paper["title"],
                    canonical_id=paper.get("canonical_id"),
                    version=paper.get("version"),
                    authors=paper.get("authors"),
                    categories=paper.get("categories"),
                )
                st.rerun()

    # ==================== 偏好汇总 ====================
    st.markdown(
        f'<p class="subsection-title">📊 {t("fav_summary_title")}</p>',
        unsafe_allow_html=True,
    )
    counts = store.get_preference_counts()
    if counts["like"] == 0 and counts["dislike"] == 0:
        st.info(t("fav_no_marks"))
        return

    col1, col2 = st.columns(2)
    with col1:
        st.metric(t("fav_likes"), counts["like"])
    with col2:
        st.metric(t("fav_dislikes"), counts["dislike"])

    aggregation = store.aggregate_liked_preferences()

    col_authors, col_categories = st.columns(2)
    with col_authors:
        st.markdown(
            f'<p class="subsection-title">👤 {t("fav_top_authors")}</p>',
            unsafe_allow_html=True,
        )
        if aggregation["authors"]:
            st.table(
                pd.DataFrame(aggregation["authors"][:10], columns=["name", "count"])
            )
        else:
            st.caption(t("fav_no_marks"))
    with col_categories:
        st.markdown(
            f'<p class="subsection-title">🗂 {t("fav_top_categories")}</p>',
            unsafe_allow_html=True,
        )
        if aggregation["categories"]:
            st.table(
                pd.DataFrame(aggregation["categories"][:10], columns=["name", "count"])
            )
        else:
            st.caption(t("fav_no_marks"))

    liked = store.list_preferences(preference="like", limit=500)
    primary_keywords = config_values.get("primary_keywords") if config_values else None
    if liked and isinstance(primary_keywords, list) and primary_keywords:
        matched = _match_primary_keywords(
            [k for k in primary_keywords if isinstance(k, str)],
            [row["title"] for row in liked],
        )
        st.markdown(
            f'<p class="subsection-title">🔑 {t("fav_matched_keywords")}</p>',
            unsafe_allow_html=True,
        )
        if matched:
            st.table(pd.DataFrame(matched, columns=["keyword", "count"]))
        else:
            st.caption(t("fav_no_keyword_hits"))

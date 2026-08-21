"""用量统计 Tab — LLM token 消耗的热力图与趋势折线。

数据来自 daily_research_store.run_token_usage（每日研究与趋势运行结束时落库）。
只读展示，不做任何写入。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from utils.daily_research_store import DailyResearchStore
from webui.i18n import t
from webui.tabs.run_manager import _daily_db_path_from_config

# GitHub 风格的五档绿色色阶；0 档为无数据灰。
_HEAT_LEVEL_COLORS = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]
_WEEK_LABELS = ["一", "", "三", "", "五", "", "日"]
_MONTH_LABELS = [
    "1月", "2月", "3月", "4月", "5月", "6月",
    "7月", "8月", "9月", "10月", "11月", "12月",
]

_RANGE_DAYS = [
    ("7", 7),
    ("30", 30),
    ("90", 90),
    ("365", 365),
    ("all", None),
]


def _load_daily_totals(store: DailyResearchStore) -> dict[str, dict]:
    rows = store.get_daily_token_totals()
    return {row["date"]: row for row in rows if isinstance(row.get("date"), str)}


def _format_tokens(value: int) -> str:
    return f"{value:,}"


def _heat_level(total: int, daily_max: int) -> int:
    if total <= 0 or daily_max <= 0:
        return 0
    ratio = total / daily_max
    if ratio <= 0.25:
        return 1
    if ratio <= 0.5:
        return 2
    if ratio <= 0.75:
        return 3
    return 4


def _render_heatmap_html(daily: dict[str, dict]) -> str:
    """最近 53 周的日用量热力图，纯 HTML/CSS，无外部依赖。"""
    today = date.today()
    # 网格从 52 周前的周一开始，到本周为止（GitHub 布局，列为周、行为星期）。
    this_monday = today - timedelta(days=today.weekday())
    start = this_monday - timedelta(weeks=52)

    daily_max = max(d["total"] for d in daily.values()) if daily else 0

    def cell(day: date) -> str:
        key = day.isoformat()
        row = daily.get(key)
        total = row["total"] if row else 0
        level = _heat_level(total, daily_max)
        runs = row["runs"] if row else 0
        tooltip = (
            f"{key} · {total:,} tokens · {runs} 次运行"
            if total
            else f"{key} · 无用量"
        )
        return (
            f'<td title="{tooltip}" style="width:11px;height:11px;'
            f'border-radius:2px;background:{_HEAT_LEVEL_COLORS[level]};"></td>'
        )

    grid_rows = []
    for weekday in range(7):
        prefix = (
            f'<td style="padding:0 4px 0 0;font-size:10px;color:#666;'
            f'white-space:nowrap;">{_WEEK_LABELS[weekday]}</td>'
        )
        cells = [
            cell(start + timedelta(weeks=week_offset, days=weekday))
            for week_offset in range(53)
        ]
        grid_rows.append("<tr>" + prefix + "".join(cells) + "</tr>")

    # 月份标签行：每周的起始日进入新月份时打一个标签。
    month_cells = []
    current_month = -1
    for week_offset in range(53):
        week_start = start + timedelta(weeks=week_offset)
        label = ""
        if week_start.month != current_month:
            current_month = week_start.month
            label = _MONTH_LABELS[current_month - 1]
        month_cells.append(
            f'<td style="font-size:10px;color:#666;padding:0 0 2px 0;'
            f'white-space:nowrap;">{label}</td>'
        )
    header_row = '<tr><td style="width:26px;"></td>' + "".join(month_cells) + "</tr>"

    legend = "".join(
        f'<span style="display:inline-block;width:11px;height:11px;'
        f'border-radius:2px;background:{color};margin:0 1px;"></span>'
        for color in _HEAT_LEVEL_COLORS
    )
    legend_html = (
        f'<div style="font-size:10px;color:#666;margin-top:6px;">'
        f'{t("usage_heatmap_less")} {legend} {t("usage_heatmap_more")}</div>'
    )

    return (
        '<div style="overflow-x:auto;">'
        '<table style="border-collapse:separate;border-spacing:2px 2px;">'
        f"{header_row}"
        + "".join(grid_rows)
        + "</table>"
        + legend_html
        + "</div>"
    )


def render(_env_values: dict, config_values: dict):
    st.markdown(
        f'<p class="section-title">📊 {t("usage_title")}</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="hint-text">{t("usage_hint")}</p>',
        unsafe_allow_html=True,
    )

    db_path: Path = _daily_db_path_from_config(config_values)
    if not db_path.exists():
        st.info(t("usage_empty"))
        return

    try:
        store = DailyResearchStore(db_path)
        daily = _load_daily_totals(store)
    except Exception as exc:
        st.error(f"{t('usage_load_failed')}: {exc}")
        return

    if not daily:
        st.info(t("usage_empty"))
        return

    st.markdown(
        f'<p class="subsection-title">{t("usage_heatmap_title")}</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        _render_heatmap_html(daily),
        unsafe_allow_html=True,
    )

    st.markdown(
        f'<p class="subsection-title">{t("usage_trend_title")}</p>',
        unsafe_allow_html=True,
    )
    labels = {key: t(f"usage_range_{key}") for key, _ in _RANGE_DAYS}
    choice = st.radio(
        t("usage_range_label"),
        list(labels.keys()),
        format_func=lambda key: labels[key],
        horizontal=True,
        key="usage_range_choice",
    )
    days = dict(_RANGE_DAYS)[choice]

    window_rows = store.get_daily_token_totals(days=days)
    if not window_rows:
        st.info(t("usage_empty"))
        return

    frame = pd.DataFrame(
        {
            t("usage_prompt_tokens"): [row["prompt"] for row in window_rows],
            t("usage_completion_tokens"): [row["completion"] for row in window_rows],
        },
        index=pd.to_datetime([row["date"] for row in window_rows]),
    )
    st.area_chart(frame, height=280)

    total_prompt = sum(row["prompt"] for row in window_rows)
    total_completion = sum(row["completion"] for row in window_rows)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(t("usage_prompt_tokens"), _format_tokens(total_prompt))
    with col2:
        st.metric(t("usage_completion_tokens"), _format_tokens(total_completion))
    with col3:
        st.metric(t("usage_total_tokens"), _format_tokens(total_prompt + total_completion))

    st.markdown(
        f'<p class="subsection-title">{t("usage_by_model_title")}</p>',
        unsafe_allow_html=True,
    )
    models = store.get_token_usage_by_model(days=days)
    if models:
        st.table(
            pd.DataFrame(
                models,
                columns=[
                    "model",
                    "prompt",
                    "completion",
                    "total",
                ],
            ).rename(
                columns={
                    "model": t("usage_model"),
                    "prompt": t("usage_prompt_tokens"),
                    "completion": t("usage_completion_tokens"),
                    "total": t("usage_total_tokens"),
                }
            )
        )

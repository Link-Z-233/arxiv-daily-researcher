"""数据分析 Tab — 用量统计 + 数据源健康 + 精简运行诊断，合并为一页。

数据全部来自 SQLite 只读查询（token 用量、扫描收据、运行诊断），
本页不做任何写入。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from utils.daily_research_store import DailyResearchStore
from utils.scoring_evaluation import (
    ScoringEvaluationError,
    build_operational_diagnostics,
    build_recent_scan_receipt_summaries,
)
from utils.source_registry import source_display_names
from webui.i18n import t
from webui.tabs.run_manager import _daily_db_path_from_config

# GitHub 风格的五档绿色色阶；0 档为无数据灰。
_HEAT_LEVEL_COLORS = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]


def _week_labels() -> list[str]:
    return [t("usage_week_mon"), "", t("usage_week_wed"), "", t("usage_week_fri"), "", t("usage_week_sun")]


def _month_label(month: int) -> str:
    labels = [
        t("usage_month_1"), t("usage_month_2"), t("usage_month_3"),
        t("usage_month_4"), t("usage_month_5"), t("usage_month_6"),
        t("usage_month_7"), t("usage_month_8"), t("usage_month_9"),
        t("usage_month_10"), t("usage_month_11"), t("usage_month_12"),
    ]
    return labels[month - 1]

# 热力图窗口：近一年（GitHub 布局，53 列周网格）。
_HEATMAP_DAYS = 365

_RANGE_DAYS = [
    ("7", 7),
    ("30", 30),
    ("90", 90),
    ("365", 365),
    ("all", None),
]


def components_html(html: str, *, height: int) -> None:
    """components.html 的薄封装，便于测试替换。"""
    components.html(html, height=height, scrolling=False)


def _scroll_right_document(body_html: str) -> str:
    """包一层文档并让横向滚动容器默认滚到最右（展示最新日期）。"""
    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        "html,body{margin:0;padding:4px 0;}</style></head><body>"
        f"{body_html}"
        "<script>(function(){var w=document.getElementById('usage-heatmap');"
        "if(w){w.scrollLeft=w.scrollWidth;}})();</script>"
        "</body></html>"
    )


# ─── 用量统计 ─────────────────────────────────────────────────────────────────


def _load_daily_totals(store: DailyResearchStore) -> dict[str, dict]:
    rows = store.get_daily_token_totals()
    return {row["date"]: row for row in rows if isinstance(row.get("date"), str)}


def _format_tokens(value: int) -> str:
    return f"{value:,}"


def _format_compact(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:.0f}"


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
    """近一年的日用量热力图；容器默认滚动到最右（最新日期可见）。"""
    today = date.today()
    first_day = today - timedelta(days=_HEATMAP_DAYS - 1)
    # 网格从 first_day 所在周的周一开始（GitHub 布局，列为周、行为星期）。
    start = first_day - timedelta(days=first_day.weekday())
    weeks = -(-(today - start).days // 7)

    daily_max = 0
    for offset in range(_HEATMAP_DAYS):
        row = daily.get((first_day + timedelta(days=offset)).isoformat())
        if row:
            daily_max = max(daily_max, row["total"])

    def cell(day: date) -> str:
        key = day.isoformat()
        row = daily.get(key)
        total = row["total"] if row else 0
        level = _heat_level(total, daily_max)
        runs = row["runs"] if row else 0
        tooltip = (
            t("usage_heatmap_tip").format(date=key, tokens=f"{total:,}", runs=runs)
            if total
            else t("usage_heatmap_none").format(date=key)
        )
        return (
            f'<td title="{tooltip}" style="width:11px;height:11px;'
            f'border-radius:2px;background:{_HEAT_LEVEL_COLORS[level]};"></td>'
        )

    grid_rows = []
    for weekday in range(7):
        prefix = (
            f'<td style="padding:0 4px 0 0;font-size:10px;color:#666;'
            f'white-space:nowrap;">{_week_labels()[weekday]}</td>'
        )
        cells = [
            cell(start + timedelta(weeks=week_offset, days=weekday))
            for week_offset in range(weeks)
        ]
        grid_rows.append("<tr>" + prefix + "".join(cells) + "</tr>")

    # 月份标签行：每周的起始日进入新月份时打一个标签。
    month_cells = []
    current_month = -1
    for week_offset in range(weeks):
        week_start = start + timedelta(weeks=week_offset)
        label = ""
        if week_start.month != current_month:
            current_month = week_start.month
            label = _month_label(current_month)
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
        '<div id="usage-heatmap" style="overflow-x:auto;">'
        '<table style="border-collapse:separate;border-spacing:2px 2px;">'
        f"{header_row}"
        + "".join(grid_rows)
        + "</table>"
        + legend_html
        + "</div>"
    )


def _nice_ceiling(value: float) -> float:
    """把数据最大值向上取整到 1/2/5×10^k 的"好看"刻度。"""
    if value <= 0:
        return 1.0
    exponent = 0
    scaled = value
    while scaled > 10:
        scaled /= 10
        exponent += 1
    while scaled < 1:
        scaled *= 10
        exponent -= 1
    for step in (1, 2, 5, 10):
        if scaled <= step:
            return step * (10**exponent)
    return 10 * (10**exponent)


def _fill_daily_gaps(rows: list[dict]) -> list[dict]:
    """把窗口内缺失的日期补成 0 用量，让 x 轴按真实日期等距分布。"""
    if not rows:
        return []
    by_date = {row["date"]: row for row in rows}
    start = date.fromisoformat(rows[0]["date"])
    end = date.fromisoformat(rows[-1]["date"])
    zero = {"prompt": 0, "completion": 0, "total": 0, "runs": 0}
    filled = []
    day = start
    while day <= end:
        key = day.isoformat()
        row = by_date.get(key, zero)
        filled.append(
            {
                "date": key,
                "prompt": row.get("prompt", 0),
                "completion": row.get("completion", 0),
            }
        )
        day += timedelta(days=1)
    return filled


def _render_trend_chart_html(rows: list[dict]) -> str:
    """静态 SVG 堆叠面积图：输入叠在输出上方，双系列都清晰可见。

    坐标轴随数据自适应，无任何手动缩放/平移交互；x 轴按日期等距
    分布（缺失日补 0），切换时间段会真实改变图形密度。
    """
    label_prompt = t("usage_prompt_tokens")
    label_completion = t("usage_completion_tokens")

    rows = _fill_daily_gaps(rows)
    # 超长历史抽稀采样（一年内不会触发），保证折线可读。
    if len(rows) > 366:
        step = -(-len(rows) // 366)
        rows = rows[::step]

    width, height = 760, 280
    pad_left, pad_right, pad_top, pad_bottom = 64, 16, 16, 40
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom

    totals = [row["prompt"] + row["completion"] for row in rows]
    ceiling = _nice_ceiling(max(totals, default=0) * 1.05)

    count = len(rows)
    xs = [
        pad_left + (i * plot_w / (count - 1) if count > 1 else plot_w / 2)
        for i in range(count)
    ]

    def y_of(value: float) -> float:
        return pad_top + plot_h * (1 - value / ceiling)

    base_y = pad_top + plot_h

    # 直接构造两个堆叠多边形：输出在底（绿），输入叠在上方（蓝）。
    completions = [row["completion"] for row in rows]
    prompts = [row["prompt"] for row in rows]

    def stacked_polygon(upper: list[float], lower: list[float], fill: str) -> str:
        top = [f"{x:.1f},{y_of(v):.1f}" for x, v in zip(xs, upper)]
        bottom = [
            f"{x:.1f},{y_of(v):.1f}"
            for x, v in zip(reversed(xs), reversed(lower))
        ]
        return f'<polygon points="{" ".join(top + bottom)}" fill="{fill}" stroke="none"/>'

    def top_line(values: list[float], stroke: str) -> str:
        pts = " ".join(f"{x:.1f},{y_of(v):.1f}" for x, v in zip(xs, values))
        return (
            f'<polyline points="{pts}" fill="none" stroke="{stroke}" '
            f'stroke-width="1.6" stroke-linejoin="round"/>'
        )

    parts = [
        f'<svg viewBox="0 0 {width} {height}" '
        f'style="width:100%;height:auto;font-family:sans-serif;" '
        f'xmlns="http://www.w3.org/2000/svg">'
    ]

    # 网格线 + Y 轴刻度（总量自适应量级）
    for tick in range(5):
        value = ceiling * tick / 4
        y = y_of(value)
        parts.append(
            f'<line x1="{pad_left}" y1="{y:.1f}" x2="{width - pad_right}" '
            f'y2="{y:.1f}" stroke="#e5e7eb" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{pad_left - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="11" fill="#6b7280">{_format_compact(value)}</text>'
        )

    # 输出（绿）垫底，输入（蓝）叠在上方；两条上缘线便于读数。
    parts.append(
        stacked_polygon(completions, [0.0] * count, "rgba(22,163,74,0.35)")
    )
    totals_upper = [p + c for p, c in zip(prompts, completions)]
    parts.append(
        stacked_polygon(totals_upper, completions, "rgba(37,99,235,0.28)")
    )
    parts.append(top_line(completions, "#16a34a"))
    parts.append(top_line(totals_upper, "#2563eb"))

    # X 轴日期标签（最多 6 个，均匀取点）
    label_count = min(6, count)
    for i in range(label_count):
        index = round(i * (count - 1) / (label_count - 1)) if label_count > 1 else 0
        parts.append(
            f'<text x="{xs[index]:.1f}" y="{height - 16}" text-anchor="middle" '
            f'font-size="11" fill="#6b7280">{rows[index]["date"][5:]}</text>'
        )

    # 图例
    parts.append(
        f'<rect x="{pad_left}" y="4" width="12" height="12" fill="#2563eb"/>'
        f'<text x="{pad_left + 18}" y="14" font-size="12" fill="#374151">'
        f"{label_prompt}</text>"
    )
    prompt_text_width = 18 + len(label_prompt) * 12
    parts.append(
        f'<rect x="{pad_left + prompt_text_width}" y="4" width="12" height="12" '
        f'fill="#16a34a"/>'
        f'<text x="{pad_left + prompt_text_width + 18}" y="14" font-size="12" '
        f'fill="#374151">{label_completion}</text>'
    )

    parts.append("</svg>")
    return "".join(parts)


def _render_usage_section(_env_values: dict, config_values: dict) -> None:
    st.markdown(
        f'<p class="section-title">📊 {t("usage_title")}</p>',
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

    # ── 顶部汇总：当日输入/输出 + 近 30 天累计 ────────────────────────────
    today_row = daily.get(date.today().isoformat()) or {"prompt": 0, "completion": 0}
    month_rows = store.get_daily_token_totals(days=30)
    month_prompt = sum(row["prompt"] for row in month_rows)
    month_completion = sum(row["completion"] for row in month_rows)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(t("usage_today_prompt"), _format_tokens(today_row["prompt"]))
    with col2:
        st.metric(t("usage_today_completion"), _format_tokens(today_row["completion"]))
    with col3:
        st.metric(
            t("usage_month_total"),
            _format_tokens(month_prompt + month_completion),
        )

    # ── 热力图（近一个月，默认滚动到最右/最新）────────────────────────────
    st.markdown(
        f'<p class="subsection-title">{t("usage_heatmap_title")}</p>',
        unsafe_allow_html=True,
    )
    heatmap_html = _render_heatmap_html(daily)
    components_html(_scroll_right_document(heatmap_html), height=160)

    # ── 用量趋势（静态自适应折线图）────────────────────────────────────────
    st.markdown(
        f'<p class="subsection-title">{t("usage_trend_title")}</p>',
        unsafe_allow_html=True,
    )
    labels = {key: t(f"usage_range_{key}") for key, _ in _RANGE_DAYS}
    choice = st.segmented_control(
        t("usage_range_label"),
        list(labels.keys()),
        selection_mode="single",
        default="30",
        format_func=lambda key: labels[key],
        key="usage_range_choice",
    )
    if choice not in labels:
        choice = "30"
    days = dict(_RANGE_DAYS)[choice]

    window_rows = store.get_daily_token_totals(days=days)
    if not window_rows:
        st.info(t("usage_empty"))
        return

    components_html(_render_trend_chart_html(window_rows), height=300)

    range_prompt = sum(row["prompt"] for row in window_rows)
    range_completion = sum(row["completion"] for row in window_rows)
    st.caption(
        t("usage_range_totals").format(
            prompt=_format_tokens(range_prompt),
            completion=_format_tokens(range_completion),
            total=_format_tokens(range_prompt + range_completion),
        )
    )

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


# ─── 数据源健康 ───────────────────────────────────────────────────────────────


def _render_source_health_section(_env_values: dict, config_values: dict) -> None:
    st.markdown(
        f'<p class="section-title">📡 {t("sh_title")}</p>',
        unsafe_allow_html=True,
    )
    st.caption(t("sh_hint"))

    db_path = _daily_db_path_from_config(config_values or {})
    if not db_path.exists():
        st.info(t("sh_no_data"))
        return
    try:
        store = DailyResearchStore(db_path)
    except Exception:
        st.info(t("sh_no_data"))
        return

    try:
        summaries = store.get_source_health(window=20)
    except Exception as exc:
        st.error(t("sh_load_failed").format(exc))
        return

    if not summaries:
        st.info(t("sh_no_receipts"))
        return

    display_names = source_display_names()
    ordered = sorted(
        summaries.items(),
        key=lambda pair: (pair[0] != "arxiv", pair[0]),
    )

    for source, summary in ordered:
        name = display_names.get(source, source)
        ok = summary["last_status"] == "succeeded"
        icon = "✅" if ok else "❌"
        rate = summary["success_rate"]

        with st.container(border=True):
            col_name, col_rate, col_new, col_last = st.columns([2, 1, 1, 2])
            col_name.markdown(f"**{icon} {name}** `{source}`")
            col_rate.metric(
                t("sh_success_rate"),
                f"{rate * 100:.0f}%",
                help=t("sh_success_rate_help"),
            )
            new_candidates = summary.get("last_new_candidates")
            col_new.metric(
                t("sh_new_candidates"),
                f"{new_candidates:,}" if new_candidates is not None else "—",
                help=t("sh_new_candidates_help"),
            )
            last_scan = (summary.get("last_scan_at") or "")[:19]
            col_last.caption(
                t("sh_last_scan").format(time=last_scan or "—")
            )

            if summary.get("last_error"):
                with st.expander(t("sh_last_error")):
                    st.code(summary["last_error"], language=None)

    st.caption(
        t("sh_window_note").format(
            scans=sum(s["scans_in_window"] for s in summaries.values())
        )
    )


# ─── 运行诊断（精简）─────────────────────────────────────────────────────────


def _format_scan_time(value: object) -> str:
    """Render the already-sanitized timestamp returned by read-only diagnostics."""
    import datetime as _dt

    if not isinstance(value, str):
        return "—"
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        return "—"


def _diagnostic_count(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _format_percentage(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1:
        return f"{value:.1%}"
    return "—"


def _render_diagnostics_section(config_values: dict) -> None:
    """精简版运行诊断：核心指标一行 + 最近一次扫描收据一张表。"""
    st.markdown(
        f'<p class="section-title">🩺 {t("an_diag_title")}</p>',
        unsafe_allow_html=True,
    )

    database_path = _daily_db_path_from_config(config_values)
    if not database_path.is_file():
        st.info(t("rm_health_empty"))
        return

    try:
        diagnostics = build_operational_diagnostics(
            database_path, recent_runs=10, baseline_runs=20
        )
    except ScoringEvaluationError:
        st.warning(t("rm_health_load_error"))
        return

    recent = diagnostics.get("windows", {}).get("recent", {})
    runs = recent.get("runs", {}) if isinstance(recent, dict) else {}
    papers = recent.get("papers", {}) if isinstance(recent, dict) else {}
    scoring = papers.get("scoring", {}) if isinstance(papers, dict) else {}
    run_states = runs.get("status_counts", {}) if isinstance(runs, dict) else {}
    notifications = diagnostics.get("outbox", {}).get("notifications", {})

    col1, col2, col3 = st.columns(3)
    col1.metric(
        t("an_diag_recent_runs"),
        f"{_diagnostic_count(run_states.get('completed'))}/"
        f"{_diagnostic_count(runs.get('available_run_count'))}",
        help=t("an_diag_recent_runs_help"),
    )
    col2.metric(
        t("rm_health_qualification_rate"),
        _format_percentage(
            scoring.get("qualification_rate") if isinstance(scoring, dict) else None
        ),
    )
    col3.metric(
        t("rm_health_notification_backlog"),
        _diagnostic_count(
            notifications.get("open_rows") if isinstance(notifications, dict) else None
        ),
    )
    failed_runs = _diagnostic_count(run_states.get("failed"))
    if failed_runs:
        st.warning(t("an_diag_failed_runs").format(n=failed_runs))

    # 最近一次运行的来源扫描收据：每来源一行，状态/候选数/扫描时间。
    try:
        snapshot = build_recent_scan_receipt_summaries(database_path, limit=5)
    except ScoringEvaluationError:
        st.caption(t("rm_scan_receipts_legacy"))
        return
    if not snapshot.get("receipt_table_available"):
        st.caption(t("rm_scan_receipts_legacy"))
        return
    completed_runs = [
        run for run in snapshot.get("runs", []) if run.get("receipts")
    ]
    if not completed_runs:
        st.caption(t("rm_scan_receipts_empty"))
        return

    latest = completed_runs[0]
    receipts = [r for r in latest.get("receipts", []) if isinstance(r, dict)]
    succeeded = sum(1 for r in receipts if r.get("status") == "succeeded")
    st.caption(
        t("an_diag_latest_scan").format(
            time=_format_scan_time(latest.get("scan_started_at") or latest.get("started_at")),
            ok=succeeded,
            total=len(receipts),
        )
    )
    st.dataframe(
        [
            {
                t("rm_scan_receipt_source"): r.get("source", "unknown"),
                t("rm_scan_receipt_status"): r.get("status", "unknown"),
                t("rm_scan_receipt_candidates"): _diagnostic_count(
                    r.get("candidate_count")
                ),
                t("rm_scan_receipt_scanned_at"): _format_scan_time(r.get("scanned_at")),
            }
            for r in receipts
        ],
        hide_index=True,
        use_container_width=True,
    )


# ─── 主渲染 ───────────────────────────────────────────────────────────────────


def render(env_values: dict, config_values: dict) -> None:
    """渲染数据分析 Tab：用量统计 → 数据源健康 → 运行诊断（精简）。"""
    _render_usage_section(env_values, config_values)

    st.divider()
    _render_source_health_section(env_values, config_values)

    st.divider()
    _render_diagnostics_section(config_values)

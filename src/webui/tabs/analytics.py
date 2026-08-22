"""数据分析 Tab — 用量统计 + 数据源健康 + 精简运行诊断，合并为一页。

数据全部来自 SQLite 只读查询（token 用量、扫描收据、运行诊断），
本页不做任何写入。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

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


# ─── 用量统计 ─────────────────────────────────────────────────────────────────


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


def _render_usage_section(_env_values: dict, config_values: dict) -> None:
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


# ─── 数据源健康 ───────────────────────────────────────────────────────────────


def _render_source_health_section(_env_values: dict, config_values: dict) -> None:
    st.markdown(
        f'<p class="section-title">📡 {t("sh_title")}</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="hint-text">{t("sh_hint")}</p>',
        unsafe_allow_html=True,
    )

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
    st.caption(t("an_diag_hint"))

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
    st.markdown(
        f'<p class="hint-text">{t("an_hint")}</p>',
        unsafe_allow_html=True,
    )
    _render_usage_section(env_values, config_values)

    st.divider()
    _render_source_health_section(env_values, config_values)

    st.divider()
    _render_diagnostics_section(config_values)

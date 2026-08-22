"""数据源健康 Tab — 每个数据源最近扫描状态聚合。

数据来自 daily_scan_receipts（每次扫描的终态收据，失败也持久保留）。
只读展示，不做任何写入。
"""

from __future__ import annotations

import streamlit as st

from utils.daily_research_store import DailyResearchStore
from utils.source_registry import source_display_names
from webui.i18n import t
from webui.tabs.run_manager import _daily_db_path_from_config


def _open_store(config_values: dict):
    db_path = _daily_db_path_from_config(config_values or {})
    if db_path is None or not db_path.exists():
        return None
    try:
        return DailyResearchStore(db_path)
    except Exception:
        return None


def render(env_values: dict, config_values: dict):
    """渲染数据源健康 Tab。"""
    st.markdown(
        f'<p class="section-title">📡 {t("sh_title")}</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="hint-text">{t("sh_hint")}</p>',
        unsafe_allow_html=True,
    )

    store = _open_store(config_values)
    if store is None:
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

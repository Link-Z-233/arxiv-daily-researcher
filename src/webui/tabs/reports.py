"""Reports Viewer tab for the Streamlit config panel."""

from __future__ import annotations

import datetime
import html
import json
import re
from pathlib import Path
from typing import NamedTuple, Optional

import streamlit as st
import streamlit.components.v1 as components

import pandas as pd

from utils.daily_research_store import DailyResearchStore
from webui.i18n import t
from webui.tabs import paper_search
from utils.source_registry import source_display_names

# project root: tabs/ -> webui/ -> src/ -> project_root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_REPORTS_DIR = _PROJECT_ROOT / "data" / "reports"

_SEL_KEY = "rsel"  # prefix for all selectbox session-state keys
_PREVIEW_KEY = "preview_report"
_FORCE_LATEST_KEY = "reports_force_latest"

# ArXiv 来源标识（用于非 ArXiv 过滤）
_ARXIV_SOURCES = {"ARXIV", "arxiv"}

# 标题旁裸版本徽标（v1/v2…）。生成端已不再输出；这里在显示层过滤
# 历史报告里的残留，带说明文字的修订标签（🔁 修订版 …）保留。
_BARE_VERSION_RE = re.compile(r'\s*<span class="revision-label">\s*v\d+\s*</span>')


def _strip_bare_version_labels(report_html: str) -> str:
    """去掉标题旁的裸 vN 徽标（仅影响预览显示，不改存档文件）。"""
    return _BARE_VERSION_RE.sub("", report_html)


# ─── data structures ──────────────────────────────────────────────────────────


class ReportFile(NamedTuple):
    path: Path
    display: str  # human-friendly label shown in UI
    source: str  # stable source code / keyword slug / "keyword_trend"
    report_type: str  # "daily" | "trend" | "keyword_trend"
    date_key: str  # YYYY-MM-DD 日期字符串，用于前后天导航


# ─── label formatting ─────────────────────────────────────────────────────────


def _fmt_daily(stem: str) -> str:
    """ARXIV_Report_2026-03-10_12-27-47  →  2026-03-10  12:27:47"""
    m = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})$", stem)
    if m:
        return f"{m.group(1)}  {m.group(2).replace('-', ':')}"
    return stem


def _extract_date_key(stem: str) -> str:
    """从报告文件名中提取 YYYY-MM-DD 作为导航键。"""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", stem)
    return m.group(1) if m else ""


def _fmt_trend(stem: str) -> str:
    """2025-03-10_2026-03-10  →  2025-03-10 → 2026-03-10"""
    m = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})$", stem)
    if m:
        return f"{m.group(1)} → {m.group(2)}"
    return stem


def _fmt_kw(stem: str) -> str:
    """keyword_trends_2026-03-09  →  2026-03-09"""
    m = re.search(r"(\d{4}-\d{2}-\d{2})$", stem)
    return m.group(1) if m else stem


def _source_from_daily_filename(stem: str) -> str:
    """Recover a source code when reports are stored without source folders."""
    match = re.match(r"(?P<source>.+?)_Report_", stem, flags=re.IGNORECASE)
    return match.group("source").strip().lower() if match else "unknown"


# ─── discovery ────────────────────────────────────────────────────────────────


def _discover_reports(reports_dir: Path = _REPORTS_DIR) -> dict[str, list[ReportFile]]:
    """Scan data/reports/ and return three lists, each newest-first."""
    result: dict[str, list[ReportFile]] = {
        "daily": [],
        "trend": [],
        "keyword_trend": [],
    }

    # daily_research/html/{source}/*.html
    daily_html = reports_dir / "daily_research" / "html"
    if daily_html.exists():
        for src_dir in daily_html.iterdir():
            if src_dir.is_dir():
                src = src_dir.name.strip().lower()
                for f in src_dir.glob("*.html"):
                    result["daily"].append(
                        ReportFile(f, _fmt_daily(f.stem), src, "daily", _extract_date_key(f.stem))
                    )
        # ``reports_by_source=false`` writes files directly in the html
        # directory. Keep that supported while deriving groups without a
        # hard-coded source list.
        for f in daily_html.glob("*.html"):
            result["daily"].append(
                ReportFile(
                    f,
                    _fmt_daily(f.stem),
                    _source_from_daily_filename(f.stem),
                    "daily",
                    _extract_date_key(f.stem),
                )
            )
        result["daily"].sort(key=lambda r: r.path.stat().st_mtime, reverse=True)

    # trend_research/html/{keyword-slug}/*.html
    trend_html = reports_dir / "trend_research" / "html"
    if trend_html.exists():
        for kw_dir in trend_html.iterdir():
            if kw_dir.is_dir():
                slug = kw_dir.name
                for f in kw_dir.glob("*.html"):
                    result["trend"].append(
                        ReportFile(f, _fmt_trend(f.stem), slug, "trend", _extract_date_key(f.stem))
                    )
        result["trend"].sort(key=lambda r: r.path.stat().st_mtime, reverse=True)

    # keyword_trend/html/*.html
    kw_html = reports_dir / "keyword_trend" / "html"
    if kw_html.exists():
        for f in sorted(kw_html.glob("*.html"), reverse=True):
            result["keyword_trend"].append(
                ReportFile(
                    f, _fmt_kw(f.stem), "keyword_trend", "keyword_trend", _extract_date_key(f.stem)
                )
            )

    return result


def _load_trend_metadata(html_path: Path) -> dict | None:
    md_dir = html_path.parent.parent.parent / "markdown" / html_path.parent.name
    meta_path = md_dir / f"{html_path.stem}_metadata.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


# ─── file browser helpers ─────────────────────────────────────────────────────


def _sel_key(rtype: str, group: str) -> str:
    return f"{_SEL_KEY}_{rtype}_{group}"


def _make_on_change(key: str, by_display: dict[str, ReportFile]):
    """Return an on_change callback that updates preview_report."""

    def _cb():
        chosen = st.session_state.get(key)
        if chosen in by_display:
            st.session_state[_PREVIEW_KEY] = by_display[chosen]

    return _cb


def _render_group_selectbox(
    rtype: str,
    group: str,
    reports: list[ReportFile],
    group_label: Optional[str] = None,
) -> None:
    """Render one selectbox for a source/slug group; updates preview on change."""
    by_display = {r.display: r for r in reports}
    labels = [r.display for r in reports]
    key = _sel_key(rtype, group)

    # Auto-set preview if nothing selected yet
    if _PREVIEW_KEY not in st.session_state and reports:
        st.session_state[_PREVIEW_KEY] = reports[0]

    st.selectbox(
        f"**{group_label or group}** ({len(labels)})",
        labels,
        key=key,
        on_change=_make_on_change(key, by_display),
    )
    # Button to explicitly load this selectbox's current selection into preview
    if st.button(t("reports_preview_btn"), key=f"btn_{key}", use_container_width=True):
        chosen = st.session_state.get(key)
        if chosen in by_display:
            st.session_state[_PREVIEW_KEY] = by_display[chosen]
            st.rerun()


def _filter_visible_reports(
    all_reports: dict[str, list[ReportFile]],
    show_non_arxiv: bool,
) -> dict[str, list[ReportFile]]:
    """返回当前界面实际可见的报告集合。"""
    daily_reports = all_reports.get("daily", [])
    if not show_non_arxiv:
        daily_reports = [r for r in daily_reports if r.source.upper() in _ARXIV_SOURCES]

    return {
        "daily": daily_reports,
        "trend": all_reports.get("trend", []),
        "keyword_trend": all_reports.get("keyword_trend", []),
    }


def _latest_visible_report(visible_reports: dict[str, list[ReportFile]]) -> Optional[ReportFile]:
    """从当前可见报告中找出修改时间最新的一份。"""
    candidates = [r for reports in visible_reports.values() for r in reports]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.path.stat().st_mtime)


def _render_category_col(
    rtype: str,
    reports: list[ReportFile],
    header: str,
    source_labels: Optional[dict[str, str]] = None,
) -> None:
    """渲染一个报告类型列（daily / trend / keyword_trend）。"""
    count = len(reports)
    st.markdown(
        f"**{header}**<br>"
        f"<span style='color:#888;font-size:0.82em'>{count} {t('reports_count_unit')}</span>",
        unsafe_allow_html=True,
    )

    if not reports:
        st.caption(t("reports_empty_type"))
        return

    if rtype == "keyword_trend":
        _render_group_selectbox(rtype, "keyword_trend", reports)
    else:
        # Group by source / keyword slug
        groups = sorted({r.source for r in reports})
        for grp in groups:
            grp_reports = [r for r in reports if r.source == grp]
            _render_group_selectbox(
                rtype,
                grp,
                grp_reports,
                (source_labels or {}).get(grp, grp),
            )


# ─── navigation helpers ───────────────────────────────────────────────────────


def _find_adjacent_report(
    current: ReportFile,
    all_reports: dict[str, list[ReportFile]],
    direction: int,  # -1 = 前一天, +1 = 后一天
) -> Optional[ReportFile]:
    """
    在同一数据源（source）内寻找相邻「日期」的报告。

    修复说明：原外用 current_dates.index() 会在同一天有多份报告时
    永远返回第一条（导致「后一天」在同日期内打转而不前进）。
    现在改为基于唯一日期列表寻找，返回目标日期中最新的报告。
    """
    if not current.date_key:
        return None

    # 同类型、同 source 的所有报告
    same_source = [
        r
        for r in all_reports.get(current.report_type, [])
        if r.source == current.source and r.date_key
    ]
    if not same_source:
        return None

    # 唯一日期列表（升序）
    unique_dates = sorted({r.date_key for r in same_source})
    if current.date_key not in unique_dates:
        return None

    date_idx = unique_dates.index(current.date_key)
    new_date_idx = date_idx + direction
    if not (0 <= new_date_idx < len(unique_dates)):
        return None  # 已是最早/最新日期

    target_date = unique_dates[new_date_idx]
    # 返回目标日期内最新的一份报告（按文件修改时间）
    candidates = [r for r in same_source if r.date_key == target_date]
    return max(candidates, key=lambda r: r.path.stat().st_mtime)


# ─── preview ──────────────────────────────────────────────────────────────────


def _build_sandboxed_preview_html(report_html: str) -> str:
    """Wrap a saved report in an origin-isolated iframe for WebUI preview.

    Reports are generated locally, but can also be restored from WebDAV or be
    edited outside this application.  ``components.html`` runs its contents in
    a Streamlit component iframe which may share an origin with the WebUI, so
    rendering report HTML there directly would let a compromised historical
    report execute in that context.

    The report is therefore encoded into an inner ``srcdoc`` iframe.  It keeps
    scripts enabled for existing KaTeX-based reports, while deliberately not
    granting ``allow-same-origin`` (or forms, downloads, or sandbox-escaping
    popups).  Escaping before insertion is important: otherwise a report could
    close the inner iframe and inject markup into the outer component document.
    """
    escaped_report = html.escape(_strip_bare_version_labels(report_html), quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
html, body {{ margin: 0; width: 100%; height: 100%; overflow: hidden; }}
iframe {{ display: block; width: 100%; height: 100%; border: 0; }}
</style>
</head>
<body>
<iframe
    title="Report preview"
    sandbox="allow-scripts allow-popups"
    referrerpolicy="no-referrer"
    srcdoc="{escaped_report}">
</iframe>
</body>
</html>"""


def _render_html_iframe(report: ReportFile) -> None:
    """固定 800px 高度的沙箱 HTML 预览。"""
    try:
        html_content = report.path.read_text(encoding="utf-8")
        components.html(_build_sandboxed_preview_html(html_content), height=800, scrolling=False)
    except Exception as e:
        st.error(f"{t('reports_load_error')}: {e}")


def _render_preview(
    report: ReportFile,
    all_reports: dict[str, list[ReportFile]],
    config_values: dict,
) -> None:
    """渲染报告预览区：文件信息栏 + 前/后天导航 + 正文（或 HTML 预览）。"""

    # File info bar
    stat = report.path.stat()
    size_kb = stat.st_size / 1024
    mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    type_cn = {
        "daily": t("rtype_daily"),
        "trend": t("rtype_trend"),
        "keyword_trend": t("rtype_keyword_trend"),
    }.get(report.report_type, report.report_type)

    st.caption(
        f"**{type_cn}** · `{report.source}` · `{report.path.name}` · "
        f"{size_kb:.1f} KB · {t('reports_mtime')}: {mtime}"
    )

    # Trend metadata
    if report.report_type == "trend":
        meta = _load_trend_metadata(report.path)
        if meta:
            with st.expander(t("reports_meta_expander"), expanded=False):
                cols = st.columns(3)
                if "keyword" in meta:
                    cols[0].metric(t("meta_keyword"), meta["keyword"])
                if "date_from" in meta and "date_to" in meta:
                    cols[1].metric(t("meta_date_range"), f"{meta['date_from']} → {meta['date_to']}")
                if "total_papers" in meta:
                    cols[2].metric(t("meta_papers"), meta["total_papers"])

    # ── 前/后天导航按钮（仅 daily 类型显示，且只在同一 source 内跳转）──
    if report.report_type == "daily":
        prev_report = _find_adjacent_report(report, all_reports, -1)
        next_report = _find_adjacent_report(report, all_reports, +1)

        nav_col1, nav_spacer, nav_col2 = st.columns([1, 4, 1])
        with nav_col1:
            if prev_report:
                if st.button(
                    t("report_prev_day"),
                    key="nav_prev",
                    use_container_width=True,
                    type="secondary",
                ):
                    st.session_state[_PREVIEW_KEY] = prev_report
                    st.rerun()
            else:
                st.button(
                    t("report_prev_day"),
                    key="nav_prev",
                    disabled=True,
                    use_container_width=True,
                    help=t("report_no_prev"),
                )
        with nav_col2:
            if next_report:
                if st.button(
                    t("report_next_day"),
                    key="nav_next",
                    use_container_width=True,
                    type="secondary",
                ):
                    st.session_state[_PREVIEW_KEY] = next_report
                    st.rerun()
            else:
                st.button(
                    t("report_next_day"),
                    key="nav_next",
                    disabled=True,
                    use_container_width=True,
                    help=t("report_no_next"),
                )

    # ── 正文：日报保持原始 HTML 报告样式，收藏按钮内联注入；其余类型同样 HTML 预览 ──
    if report.report_type == "daily":
        if not _render_daily_report(report, config_values):
            # 库里没有对应记录的旧日报：直接 HTML 原文预览（无标记按钮）
            _render_html_iframe(report)
    else:
        _render_html_iframe(report)


# ─── 报告正文论文卡片（标记内联，收藏融合）────────────────────────────────


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


def _apply_report_mark(store: DailyResearchStore, paper: dict, preference: str) -> None:
    store.set_paper_preference(
        paper["source"],
        paper["paper_id"],
        preference=preference,
        title=paper["title"],
        canonical_id=paper.get("canonical_id"),
        version=paper.get("version"),
        authors=paper.get("authors"),
        categories=paper.get("categories"),
    )


# ─── HTML 报告内联收藏（保留报告原样式，注入标记按钮）──────────────────────

# 自定义组件宿主页面：沙箱报告 iframe + Streamlit 组件消息通道。
_COMPONENT_DIR = Path(__file__).resolve().parent.parent / "report_component"

_CARD_OPEN_RE = re.compile(r'<div class="card (?:pass|fail)">')
# 卡片内第一个 field 行就是评分行；标记按钮插入该行行首并 float 到最右。
_SCORE_FIELD_RE = re.compile(r'<div class="field">')

_MARK_BAR_CSS = (
    "<style>"
    ".arxiv-mark-bar{float:right;display:flex;gap:4px;margin-left:12px;}"
    ".arxiv-mark-btn{border:1px solid rgba(127,127,127,.45);border-radius:8px;"
    "background:rgba(255,255,255,.78);cursor:pointer;font-size:13px;line-height:1;"
    "padding:4px 7px;color:inherit;}"
    ".arxiv-mark-btn:hover{background:rgba(255,255,255,.95);}"
    '.arxiv-mark-btn.active[data-pref="like"]{background:#16a34a;border-color:#16a34a;color:#fff;}'
    '.arxiv-mark-btn.active[data-pref="dislike"]{background:#dc2626;border-color:#dc2626;color:#fff;}'
    "</style>"
)

_MARK_BAR_JS = """<script>(function(){
  if (window.__arxivMarkInjected) return; window.__arxivMarkInjected = true;
  function post(msg){ parent.postMessage(msg, "*"); }
  // 把偏好应用到某个标记条：更新 data-current 与按钮 active 态。
  function applyPref(bar, pref){
    pref = (pref === "like" || pref === "dislike") ? pref : "none";
    bar.setAttribute("data-current", pref);
    var buttons = bar.querySelectorAll(".arxiv-mark-btn");
    for (var i = 0; i < buttons.length; i++) {
      var b = buttons[i];
      if (b.getAttribute("data-pref") === pref) { b.classList.add("active"); }
      else { b.classList.remove("active"); }
    }
  }
  // 服务端权威状态（含初始状态）通过该消息下发；HTML 本体不携带状态，
  // 标记变化时报告 iframe 不会重建，页面不闪烁。
  window.addEventListener("message", function(ev){
    var data = ev.data;
    if (!data || data.type !== "arxiv-report-states") return;
    var states = data.states || {};
    var bars = document.querySelectorAll(".arxiv-mark-bar");
    for (var i = 0; i < bars.length; i++) {
      applyPref(bars[i], states[bars[i].getAttribute("data-paper")]);
    }
  });
  var lastHeight = 0;
  var pendingHeight = 0;
  var heightTimer = null;
  // 高度上报做 200ms 节流：宿主侧 setFrameHeight 会再触发组件渲染，
  // 不节流会形成"上报→渲染→重建→再上报"的自激循环。
  function flushHeight(){
    heightTimer = null;
    if (pendingHeight && pendingHeight !== lastHeight){
      lastHeight = pendingHeight;
      post({type:"arxiv-report-height", height:pendingHeight});
    }
  }
  function reportHeight(){
    var h = document.documentElement && document.documentElement.scrollHeight;
    if (!h) return;
    pendingHeight = h;
    if (!heightTimer){ heightTimer = setTimeout(flushHeight, 200); }
  }
  document.addEventListener("click", function(ev){
    var target = ev.target;
    var btn = target && target.closest ? target.closest(".arxiv-mark-btn") : null;
    if (!btn) return;
    ev.preventDefault();
    var bar = btn.closest(".arxiv-mark-bar");
    // 再点同一个偏好即取消（切回 none）；只有 👍/👎 两个按钮。
    var want = btn.getAttribute("data-pref") === (bar.getAttribute("data-current") || "none")
      ? "none" : btn.getAttribute("data-pref");
    // 先本地生效，避免等待服务端往返；落库后的权威状态随后校准。
    applyPref(bar, want);
    post({
      type: "arxiv-report-mark",
      source: bar.getAttribute("data-source"),
      paper_id: bar.getAttribute("data-paper"),
      pref: want,
      nonce: Date.now() + "-" + Math.random()
    });
  });
  window.addEventListener("load", reportHeight);
  window.addEventListener("resize", reportHeight);
  if (document.readyState === "complete") { reportHeight(); }
  if (window.MutationObserver) {
    var observer = new MutationObserver(reportHeight);
    observer.observe(document.documentElement, {subtree: true, childList: true, attributes: true});
  }
})();</script>"""


def _build_mark_bar(paper: dict) -> str:
    """构造 👍/👎 标记按钮条（插入评分行，浮动到卡片最右侧）。

    按钮的 active 态不写入 HTML：初始状态与后续变更都由宿主通过
    ``arxiv-report-states`` 消息下发。这样标记变化时报告 HTML 保持
    逐字节不变，沙箱 iframe 不会重建，页面也就不闪烁。
    """
    source = html.escape(str(paper.get("source", "")), quote=True)
    paper_id = html.escape(str(paper.get("paper_id", "")), quote=True)

    return (
        f'<div class="arxiv-mark-bar" data-source="{source}" data-paper="{paper_id}" '
        'data-current="none">'
        f'<button type="button" class="arxiv-mark-btn" data-pref="like" '
        f'title="{html.escape(t("fav_like"), quote=True)}">👍</button>'
        f'<button type="button" class="arxiv-mark-btn" data-pref="dislike" '
        f'title="{html.escape(t("fav_dislike"), quote=True)}">👎</button>'
        "</div>"
    )


def _append_mark_assets(report_html: str) -> str:
    """把按钮样式与点击脚本追加到报告尾部（</body> 前）。"""
    payload = _MARK_BAR_CSS + _MARK_BAR_JS
    close_idx = report_html.rfind("</body>")
    if close_idx == -1:
        return report_html + payload
    return report_html[:close_idx] + payload + report_html[close_idx:]


def _inject_mark_controls(report_html: str, papers: list[dict]) -> str:
    """按卡片标题把论文与 HTML 卡片配对，把标记按钮插入评分行最右侧。

    报告本体（样式/结构/内容）保持生成时的原样：只在每张卡片的评分行
    （第一个 field 行）开头插入一个浮动到行尾的按钮条，并在文档末尾
    追加样式与脚本。找不到评分行的卡片不注入。
    """
    candidates: list[tuple[str, dict]] = []
    for paper in papers:
        title = paper.get("title")
        if title:
            candidates.append((html.escape(str(title)), paper))

    matches = list(_CARD_OPEN_RE.finditer(report_html))
    if not matches or not candidates:
        return report_html

    used: set[int] = set()
    pieces: list[str] = []
    last = 0
    injected = 0
    for index, match in enumerate(matches):
        block_end = matches[index + 1].start() if index + 1 < len(matches) else len(report_html)
        block = report_html[match.end() : block_end]
        chosen = None
        for candidate_index, (escaped_title, paper) in enumerate(candidates):
            if candidate_index in used:
                continue
            if escaped_title in block:
                chosen = (candidate_index, paper)
                break
        if chosen is None:
            continue
        field = _SCORE_FIELD_RE.search(block)
        if field is None:
            continue
        used.add(chosen[0])
        insert_at = match.end() + field.end()
        pieces.append(report_html[last:insert_at])
        pieces.append(_build_mark_bar(chosen[1]))
        last = insert_at
        injected += 1

    if not injected:
        return report_html
    pieces.append(report_html[last:])
    return _append_mark_assets("".join(pieces))


def _render_report_component(report_html: str, states: dict, key: str):
    """渲染报告查看自定义组件；返回组件回传的标记动作（无则 None）。

    ``states`` 是 {paper_id: preference} 的当前偏好快照，独立于报告
    HTML 下发：标记变化只会更新 states，报告 iframe 不重建。
    """
    try:
        viewer = components.declare_component(
            "arxiv_report_viewer", path=str(_COMPONENT_DIR)
        )
        return viewer(html=report_html, states=states, key=key, default=None)
    except Exception:
        # 组件基础设施不可用时退回纯预览（由调用方处理）。
        return None


def _render_preference_profile(store: DailyResearchStore, config_values: dict) -> None:
    """兴趣画像汇总（折叠展示，标记数据永不删除）。"""
    with st.expander(f"📊 {t('fav_summary_title')}", expanded=False):
        counts = store.get_preference_counts()
        if counts["like"] == 0 and counts["dislike"] == 0:
            st.caption(t("fav_no_marks"))
            return

        col1, col2 = st.columns(2)
        with col1:
            st.metric(t("fav_likes"), counts["like"])
        with col2:
            st.metric(t("fav_dislikes"), counts["dislike"])

        aggregation = store.aggregate_liked_preferences()
        col_authors, col_categories = st.columns(2)
        with col_authors:
            st.markdown(f"**👤 {t('fav_top_authors')}**")
            if aggregation["authors"]:
                st.table(
                    pd.DataFrame(aggregation["authors"][:10], columns=["name", "count"])
                )
            else:
                st.caption(t("fav_no_marks"))
        with col_categories:
            st.markdown(f"**🗂 {t('fav_top_categories')}**")
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
            st.markdown(f"**🔑 {t('fav_matched_keywords')}**")
            if matched:
                st.table(pd.DataFrame(matched, columns=["keyword", "count"]))
            else:
                st.caption(t("fav_no_keyword_hits"))


def _render_daily_report(report: ReportFile, config_values: dict) -> bool:
    """日报以原始 HTML 报告呈现，收藏按钮内联注入卡片；无库记录时返回 False。"""
    from webui.tabs.run_manager import _daily_db_path_from_config

    if not report.date_key:
        return False

    db_path = _daily_db_path_from_config(config_values or {})
    if not db_path.exists():
        return False
    try:
        store = DailyResearchStore(db_path)
        result = store.search_papers(
            query="",
            source=report.source,
            completed_from=report.date_key,
            completed_to=report.date_key,
            limit=200,
        )
    except Exception:
        return False

    if result["total"] == 0:
        return False

    try:
        html_content = _strip_bare_version_labels(
            report.path.read_text(encoding="utf-8")
        )
    except OSError:
        return False

    enriched = _inject_mark_controls(html_content, result["items"])
    if enriched is html_content:
        # 报告里没有可配对的论文卡片（旧版/异构报告），退回纯 HTML 预览。
        return False

    states = {
        str(item["paper_id"]): (item.get("preference") or "none")
        for item in result["items"]
        if item.get("paper_id")
    }
    key = f"rv_{report.source}_{report.path.name}"
    value = _render_report_component(enriched, states, key)
    if isinstance(value, dict) and value.get("paper_id"):
        guard_key = f"rv_consumed_{key}"
        if st.session_state.get(guard_key) != value.get("nonce"):
            st.session_state[guard_key] = value.get("nonce")
            paper = next(
                (
                    item
                    for item in result["items"]
                    if item.get("paper_id") == value.get("paper_id")
                ),
                None,
            )
            if paper is not None:
                _apply_report_mark(store, paper, value.get("pref") or "none")
                # 重跑一次以刷新 states/画像；报告 HTML 不含状态，
                # iframe 不会重建，用户看不到闪烁。
                st.rerun()

    _render_preference_profile(store, config_values)
    return True


# ─── main render ──────────────────────────────────────────────────────────────


def render(env_values: dict, config_values: dict) -> None:
    """渲染报告查看 Tab：报告浏览/预览/随手标记 + 页面最下方的论文检索。"""

    st.markdown(
        f'<p class="hint-text">{t("reports_hint")}</p>',
        unsafe_allow_html=True,
    )

    # 工具栏：刷新 + 非ArXiv过滤开关
    col_refresh, col_filter, _ = st.columns([1, 2, 3])
    with col_refresh:
        if st.button(t("reports_refresh"), use_container_width=True):
            for k in list(st.session_state.keys()):
                if k.startswith(_SEL_KEY) or k == _PREVIEW_KEY:
                    del st.session_state[k]
            st.session_state[_FORCE_LATEST_KEY] = True
            st.rerun()
    with col_filter:
        show_non_arxiv = st.toggle(
            t("report_show_non_arxiv"),
            value=False,
            key="reports_show_non_arxiv",
            help=t("report_show_non_arxiv_help"),
        )

    st.divider()

    source_labels = source_display_names(
        config_values.get("extra_source_definitions", [])
    )
    all_reports = _discover_reports()
    visible_reports = _filter_visible_reports(all_reports, show_non_arxiv)
    total = sum(len(v) for v in visible_reports.values())

    if total == 0:
        st.info(t("reports_empty"))
        st.caption(f"📂 {t('reports_dir_label')}: `{_REPORTS_DIR}`")
    else:
        _render_report_browser(visible_reports, source_labels, config_values)

    # ── 论文检索（页面最下方）──────────────────────────────────────────
    st.divider()
    paper_search.render(env_values, config_values)


def _render_report_browser(
    visible_reports: dict[str, list[ReportFile]],
    source_labels: dict[str, str],
    config_values: dict,
) -> None:
    """三列报告浏览器 + 预览 + 随手标记。"""
    latest_visible = _latest_visible_report(visible_reports)
    current_preview: ReportFile | None = st.session_state.get(_PREVIEW_KEY)
    visible_paths = {str(r.path) for reports in visible_reports.values() for r in reports}
    force_latest = st.session_state.pop(_FORCE_LATEST_KEY, False)

    if force_latest or current_preview is None or str(current_preview.path) not in visible_paths:
        if latest_visible is not None:
            st.session_state[_PREVIEW_KEY] = latest_visible

    # ── 三列报告浏览器 ──────────────────────────────────────────────────
    c1, c2, c3 = st.columns(3)

    with c1:
        _render_category_col(
            "daily",
            visible_reports["daily"],
            f"📅 {t('rtype_daily')}",
            source_labels,
        )

    with c2:
        _render_category_col(
            "trend",
            visible_reports["trend"],
            f"🔬 {t('rtype_trend')}",
        )

    with c3:
        _render_category_col(
            "keyword_trend",
            visible_reports["keyword_trend"],
            f"📈 {t('rtype_keyword_trend')}",
        )

    # ── 预览区 ────────────────────────────────────────────────────────────
    report: ReportFile | None = st.session_state.get(_PREVIEW_KEY)
    if report is None:
        return

    st.divider()
    _render_preview(report, visible_reports, config_values)


def collect(_env_values: dict, _config_values: dict) -> dict:
    """报告查看 Tab 无配置需保存，返回空字典。"""
    return {}

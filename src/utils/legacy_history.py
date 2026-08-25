"""v3.2 历史数据导入：旧 JSON 历史与 HTML 日报卡片 → SQLite。

v4 的 SQLite 账本是唯一每日研究历史，升级后并不会迁移旧数据。本模块把
两块 v3.2 遗产合并进 SQLite：

1. ``data/history/*_history.json`` — 只有「论文标识 → 交付时间」；
2. ``data/reports/daily_research/html/**/*.html`` — 日报卡片里保存着
   标题/作者/摘要/评分/翻译/深度分析等完整数据。
3. ``data/keywords/keywords.db`` — v3.2 的逐论文关键词、标准化词和
   别名映射，HTML 报告只能恢复其中一部分。

导入规则：
- 同一论文出现多份卡片时，最新报告覆盖旧数据；
- 由 v4 运行写入的行（带阶段指纹）永不被旧数据降级；
- 数据完整的卡片同时补写交付账本，防止未来重复推送；
- 缺卡片 / 缺翻译 / 及格但缺深度分析的论文进入补充运行积压表，
  之后由补充报告流程统一重跑并推送一次。
"""

from __future__ import annotations

import html as html_module
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# app_state key holding the latest import summary for the WebUI panel.
LEGACY_IMPORT_STATE_KEY = "legacy_import_summary"

_HISTORY_FILE_RE = re.compile(r"^(?P<source>.+)_history\.json$", re.IGNORECASE)

# 报告文件名中的时间戳：ARXIV_Report_2026-03-03_16-10-08[[_392421].html]
_REPORT_TS_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{2}-\d{2}-\d{2})(?:_(?P<micro>\d+))?"
)
_ARXIV_VERSION_RE = re.compile(r"^(?P<canonical>.+?)(?:v(?P<version>[0-9]+))$")

_CARD_SPLIT_RE = re.compile(r'<div class="card (?:pass|fail)">')
_TITLE_ANCHOR_RE = re.compile(
    r'<div class="card-title"><a href="(?P<url>[^"]+)"[^>]*>'
    r"(?P<title>.*?)</a>",
    re.DOTALL,
)
_BARE_TITLE_RE = re.compile(r'<div class="card-title">(?P<title>.*?)</div>', re.DOTALL)
_TITLE_INDEX_RE = re.compile(r"^\s*\d+\.\s*")
_BADGE_RE = re.compile(r'<span class="badge (?:pass|fail)">')
_SCORE_V1_RE = re.compile(
    r'Score:</span> <span class="score">(?P<total>[\d.]+)</span> / (?P<passing>[\d.]+)'
)
_SCORE_V2_RE = re.compile(
    r'Core relevance:</span> <span class="score">(?P<relevance>[\d.]+)</span> '
    r"/ (?P<threshold>[\d.]+)"
    r"(?:\s*\| Ranking: (?P<ranking>[\d.]+))?"
)
_FIELD_RE = re.compile(
    r'<div class="field"><span class="field-label">(?P<label>[^<]+):</span> '
    r"(?P<value>.*?)</div>",
    re.DOTALL,
)
_SS_TLDR_RE = re.compile(
    r'<div class="tldr"><strong>Semantic Scholar TL;DR:</strong> (?P<text>.*?)</div>',
    re.DOTALL,
)
_TLDR_RE = re.compile(
    r'<div class="tldr"><strong>TL;DR:</strong> (?P<text>.*?)</div>', re.DOTALL
)
_DETAILS_RE = re.compile(
    r"<details[^>]*><summary>(?P<summary>[^<]*)</summary>\s*"
    r'<div class="analysis-content">(?P<content>.*?)</div>\s*</details>',
    re.DOTALL,
)
_SCORE_TABLE_ROW_RE = re.compile(r"<tr[^>]*>(?P<row>.*?)</tr>", re.DOTALL)
_SCORE_CELL_RE = re.compile(r"<td[^>]*>(?P<cell>.*?)</td>", re.DOTALL)
_REASONING_RE = re.compile(r"<strong>评分理由:</strong> (?P<text>.*?)</p>", re.DOTALL)
_AUTHOR_BONUS_RE = re.compile(r"<td[^>]*>(?P<bonus>\+[\d.]+)</td>")
_EXPERTS_RE = re.compile(r"专家: (?P<experts>.*?)</td>", re.DOTALL)
_ANALYSIS_SECTION_RE = re.compile(r"^深度分析")
_ANALYSIS_PARAGRAPH_RE = re.compile(r"<p><strong>(?P<label>[^<]+):</strong>\s*(?P<value>.*?)</p>", re.DOTALL)
_ANALYSIS_LIST_RE = re.compile(r"\s*<ul>(?P<items>.*?)</ul>", re.DOTALL)
_LIST_ITEM_RE = re.compile(r"<li>(?P<item>.*?)</li>", re.DOTALL)

# 深度分析卡片里的展示标签 → 字段 id。旧报告使用英文标签，新模板使用
# name/label；模板映射在导入时动态补充，这里只兜底历史固定值。
_FALLBACK_ANALYSIS_LABELS = {
    "chinese title": "chinese_title",
    "中文标题": "chinese_title",
    "summary": "summary",
    "摘要": "summary",
    "内容摘要": "summary",
    "innovations": "innovations",
    "创新点": "innovations",
    "methodology": "methodology",
    "方法": "methodology",
    "key results": "key_results",
    "关键结果": "key_results",
    "tech stack": "tech_stack",
    "技术栈": "tech_stack",
    "strengths": "strengths",
    "优点": "strengths",
    "limitations": "limitations",
    "局限": "limitations",
    "局限性": "limitations",
    "relevance to keywords": "relevance_to_keywords",
    "与研究方向的相关性": "relevance_to_keywords",
    "future work": "future_work",
    "未来工作": "future_work",
    "label": "label",
}


def _strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", fragment or "")
    return html_module.unescape(text).strip()


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _clean_text(fragment: str) -> str:
    return _collapse_ws(_strip_tags(fragment))


def _report_timestamp(path: Path) -> Optional[datetime]:
    match = _REPORT_TS_RE.search(path.stem)
    if not match:
        return None
    micro = match.group("micro") or ""
    if micro:
        micro = (micro + "000000")[:6]
    time_text = match.group("time").replace("-", ":")
    stamp = f"{match.group('date')}T{time_text}"
    try:
        return datetime.fromisoformat(stamp + (f".{micro}" if micro else ""))
    except ValueError:
        return None


def _source_from_path(path: Path, html_root: Path) -> Optional[str]:
    """Report source comes from its per-source directory (or filename prefix)."""
    relative = path.parent.relative_to(html_root)
    if relative.parts and relative.parts[0] != ".":
        return str(relative.parts[0]).strip().lower()
    match = re.match(r"(?P<source>.+?)_Report_", path.stem, re.IGNORECASE)
    return match.group("source").strip().lower() if match else None


def _split_arxiv_version(paper_id: str) -> Tuple[str, Optional[int]]:
    """Keep report-card parsing usable in the slim WebUI image.

    The worker's ``sources.base_source`` exports the same compatibility split,
    but importing that package from the WebUI would pull in network clients
    which deliberately are not shipped there.
    """
    value = str(paper_id or "").strip()
    match = _ARXIV_VERSION_RE.match(value)
    if not match:
        return value, None
    return match.group("canonical"), int(match.group("version"))


def _arxiv_pdf_url(paper_id: str) -> Optional[str]:
    canonical, _ = _split_arxiv_version(paper_id)
    return f"https://arxiv.org/pdf/{canonical}.pdf"


def _identity_from_url(url: str) -> Optional[Dict[str, Any]]:
    """Best-effort identity from a card link (arXiv abs page or DOI)."""
    arxiv_match = re.search(r"arxiv\.org/abs/([^/?#]+)", url or "", re.IGNORECASE)
    if arxiv_match:
        paper_id = arxiv_match.group(1)
        canonical, version = _split_arxiv_version(paper_id)
        return {
            "kind": "arxiv",
            "paper_id": paper_id,
            "canonical_id": canonical,
            "version": version if version is not None else 0,
        }
    doi_match = re.search(r"doi\.org/(?P<doi>.+)$", url or "", re.IGNORECASE)
    if doi_match:
        doi = doi_match.group("doi").strip().rstrip("/.")
        return {
            "kind": "doi",
            "paper_id": url.strip(),
            "canonical_id": doi.lower(),
            "version": 0,
        }
    return None


def normalize_doi_key(value: str) -> str:
    """Normalize a DOI URL/bare DOI for history ↔ card matching."""
    text = str(value or "").strip()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text, flags=re.IGNORECASE)
    return text.strip().rstrip("/.").lower()


def _parse_history_key(source: str, key: str) -> Optional[Tuple[str, int]]:
    """历史键 → (canonical, version)。arXiv 兼容 id vN 与 canonical@vN 两种。"""
    text = str(key or "").strip()
    if not text:
        return None
    if source == "arxiv":
        if "@v" in text:
            canonical, _, version = text.rpartition("@v")
            if canonical:
                try:
                    return canonical, int(version)
                except ValueError:
                    return canonical, 0
        canonical, version = _split_arxiv_version(text)
        return canonical, version if version is not None else 0
    return normalize_doi_key(text), 0


def load_legacy_history_files(history_dir: Path) -> Dict[str, Dict[Tuple[str, int], str]]:
    """读取旧 JSON 历史：{source: {(canonical, version): delivered_at ISO}}."""
    result: Dict[str, Dict[Tuple[str, int], str]] = {}
    directory = Path(history_dir)
    if not directory.is_dir():
        return result
    for item in sorted(directory.glob("*_history.json")):
        if item.name.lower().startswith("history_old"):
            # v1.0 存档文件，不属于 v3.2 活跃历史。
            continue
        match = _HISTORY_FILE_RE.match(item.name)
        source = match.group("source").strip().lower() if match else "unknown"
        try:
            raw = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("[LegacyImport] 跳过无法解析的历史文件 %s: %s", item.name, exc)
            continue
        if not isinstance(raw, dict):
            continue
        entries: Dict[Tuple[str, int], str] = {}
        for key, delivered_at in raw.items():
            identity = _parse_history_key(source, str(key))
            if identity is None:
                continue
            if not isinstance(delivered_at, str) or not delivered_at.strip():
                continue
            entries[identity] = delivered_at.strip()
        result[source] = entries
        logger.info("[LegacyImport] 历史文件 %s: %s 条", item.name, len(entries))
    return result


def _analysis_label_map() -> Dict[str, str]:
    """展示标签 → 深度分析字段 id（模板动态值优先，历史标签兜底）。"""
    mapping = dict(_FALLBACK_ANALYSIS_LABELS)
    try:
        from config import settings

        template = settings.load_report_template("deep_analysis_template.json")
        for module in template.get("modules", []):
            module_id = module.get("id")
            if not isinstance(module_id, str) or not module_id:
                continue
            for field in ("label", "name"):
                label = module.get(field)
                if isinstance(label, str) and label.strip():
                    mapping.setdefault(label.strip().lower(), module_id)
                    mapping.setdefault(label.strip(), module_id)
    except Exception:  # pragma: no cover - 模板缺失时仅用静态映射
        pass
    return mapping


def _parse_analysis_sections(content: str) -> Dict[str, Any]:
    """深度分析 <p><strong>标签:</strong> …</p>（含随后的 <ul> 列表）→ 字段字典。"""
    label_map = _analysis_label_map()
    analysis: Dict[str, Any] = {}

    def assign(label: str, value: Any) -> None:
        if value is None:
            return
        key = label_map.get(label.strip()) or label_map.get(label.strip().lower())
        analysis[key or label.strip()] = value

    pos = 0
    while True:
        match = _ANALYSIS_PARAGRAPH_RE.search(content, pos)
        if not match:
            break
        label = html_module.unescape(match.group("label")).strip()
        inline_value = _clean_text(match.group("value"))
        end = match.end()
        if inline_value:
            assign(label, inline_value)
        else:
            list_match = _ANALYSIS_LIST_RE.match(content, end)
            if list_match:
                items = [
                    _clean_text(item_match.group("item"))
                    for item_match in _LIST_ITEM_RE.finditer(list_match.group("items"))
                ]
                items = [item for item in items if item]
                assign(label, items or None)
                end = list_match.end()
        pos = end
    return analysis


def _parse_score_details(content: str) -> Dict[str, Any]:
    """评分详情表格 → keyword_scores / author_bonus / expert_authors_found."""
    keyword_scores: Dict[str, float] = {}
    author_bonus = 0.0
    experts: List[str] = []
    for row_match in _SCORE_TABLE_ROW_RE.finditer(content):
        cells = [
            _clean_text(cell.group("cell"))
            for cell in _SCORE_CELL_RE.finditer(row_match.group("row"))
        ]
        if len(cells) >= 4 and cells[0] not in ("关键词",):
            # 列顺序：关键词 | 权重 | 相关度 | 得分（作者行为变体）
            if cells[1] == "-" and cells[2].startswith("+"):
                try:
                    author_bonus = float(cells[2].lstrip("+"))
                except ValueError:
                    pass
                expert_text = cells[3]
                prefix = "专家:"
                if expert_text.startswith(prefix):
                    experts = [
                        item.strip()
                        for item in expert_text[len(prefix):].split(",")
                        if item.strip()
                    ]
                continue
            try:
                relevance = float(cells[2].split("/")[0])
            except (ValueError, IndexError):
                continue
            keyword_scores[cells[0]] = relevance
    return {
        "keyword_scores": keyword_scores,
        "author_bonus": author_bonus,
        "expert_authors_found": experts,
    }


def parse_legacy_report_cards(html_root: Path) -> List[Dict[str, Any]]:
    """解析所有旧 HTML 日报，按报告时间升序返回卡片记录（新报告在后）。"""
    root = Path(html_root)
    if not root.is_dir():
        return []
    files: List[Tuple[datetime, Path, str]] = []
    for path in root.rglob("*.html"):
        source = _source_from_path(path, root)
        if not source:
            continue
        stamp = _report_timestamp(path)
        if stamp is None:
            logger.warning("[LegacyImport] 文件名无时间戳，跳过: %s", path.name)
            continue
        files.append((stamp, path, source))
    files.sort(key=lambda item: item[0])

    cards: List[Dict[str, Any]] = []
    for stamp, path, source in files:
        cards.extend(_parse_report_file(path, source, stamp))
    return cards


def _parse_report_file(path: Path, source: str, stamp: datetime) -> List[Dict[str, Any]]:
    """Read one report and reconstruct its cards without changing the archive."""
    try:
        html_text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("[LegacyImport] 读取报告失败 %s: %s", path, exc)
        return []
    cards: List[Dict[str, Any]] = []
    for chunk in _CARD_SPLIT_RE.split(html_text)[1:]:
        card = _parse_card(chunk, source, stamp, path)
        if card is not None:
            cards.append(card)
    return cards


def parse_legacy_report_file(
    path: Path,
    *,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Parse one saved daily report for display-time card actions.

    This lightweight helper is shared by the WebUI and the full import. It
    deliberately does not need the worker's source clients, so the thin WebUI
    image can restore 👍/👎 controls for historical reports after an upgrade.
    """
    report_path = Path(path)
    source_name = str(source or "").strip().lower()
    if not source_name:
        match = re.match(r"(?P<source>.+?)_Report_", report_path.stem, re.IGNORECASE)
        source_name = match.group("source").strip().lower() if match else ""
    if not source_name:
        return []
    stamp = _report_timestamp(report_path)
    if stamp is None:
        try:
            stamp = datetime.fromtimestamp(report_path.stat().st_mtime)
        except OSError as exc:
            logger.warning("[LegacyImport] 无法读取报告时间 %s: %s", report_path, exc)
            return []
    return _parse_report_file(report_path, source_name, stamp)


def _parse_card(chunk: str, source: str, stamp: datetime, path: Path) -> Optional[Dict[str, Any]]:
    title_match = _TITLE_ANCHOR_RE.search(chunk)
    url = ""
    if title_match:
        url = html_module.unescape(title_match.group("url")).strip()
        title = _clean_text(title_match.group("title"))
        title = _TITLE_INDEX_RE.sub("", title)
    else:
        bare = _BARE_TITLE_RE.search(chunk)
        if not bare:
            return None
        title = _TITLE_INDEX_RE.sub("", _clean_text(bare.group("title")))
    if not title:
        return None

    identity = _identity_from_url(url) if url else None
    if identity is None:
        # 没有 URL 的卡片无法对齐旧历史，保留标题但仍尝试字段解析。
        identity = {"kind": "unknown", "paper_id": title, "canonical_id": title.lower(), "version": 0}
    if identity["kind"] == "unknown" and source == "arxiv":
        # 标题兜底无法构造 arXiv 身份，交给调用方按缺失记录。
        identity = {"kind": "title", "paper_id": title, "canonical_id": "", "version": 0}

    is_qualified = '<span class="badge pass">' in chunk

    fields: Dict[str, str] = {}
    for field_match in _FIELD_RE.finditer(chunk):
        label = html_module.unescape(field_match.group("label")).strip()
        fields[label] = _clean_text(field_match.group("value"))

    published: Optional[datetime] = None
    published_text = fields.get("Published")
    if published_text:
        try:
            published = datetime.strptime(published_text[:10], "%Y-%m-%d")
        except ValueError:
            published = None
    version_from_field: Optional[int] = None
    version_text = fields.get("Version")
    if version_text:
        match = re.search(r"v(\d+)", version_text)
        if match:
            version_from_field = int(match.group(1))

    abstract = ""
    abstract_cn = ""
    score_details_raw = ""
    extracted_keywords: List[str] = []
    analysis: Dict[str, Any] = {}
    for details in _DETAILS_RE.finditer(chunk):
        summary = html_module.unescape(details.group("summary")).strip()
        content = details.group("content")
        if summary == "摘要翻译":
            abstract_cn = _clean_text(re.sub(r"</?p>", " ", content))
        elif summary == "Abstract":
            abstract = _clean_text(re.sub(r"</?p>", " ", content))
        elif summary == "评分详情":
            score_details_raw = content
        elif summary == "关键词":
            extracted_keywords = [
                item.strip()
                for item in _clean_text(content).split(",")
                if item.strip()
            ]
        elif _ANALYSIS_SECTION_RE.match(summary):
            analysis = _parse_analysis_sections(content)

    ss_tldr = ""
    ss_match = _SS_TLDR_RE.search(chunk)
    if ss_match:
        ss_tldr = _clean_text(ss_match.group("text"))
    tldr = ""
    tldr_match = _TLDR_RE.search(chunk)
    if tldr_match:
        tldr = _clean_text(tldr_match.group("text"))

    score_payload: Dict[str, Any] = {}
    v2_match = _SCORE_V2_RE.search(chunk)
    v1_match = _SCORE_V1_RE.search(chunk)
    if v2_match:
        threshold = float(v2_match.group("threshold"))
        ranking = float(v2_match.group("ranking") or v2_match.group("relevance"))
        score_payload = {
            "strategy_id": "core_relevance_v2",
            "total_score": ranking,
            "ranking_score": ranking,
            "relevance_score": float(v2_match.group("relevance")),
            "qualification_threshold": threshold,
            "passing_score": threshold,
            "is_qualified": is_qualified,
        }
    elif v1_match:
        score_payload = {
            "total_score": float(v1_match.group("total")),
            "passing_score": float(v1_match.group("passing")),
            "is_qualified": is_qualified,
        }
    if score_payload:
        details = _parse_score_details(score_details_raw)
        reasoning_match = _REASONING_RE.search(chunk)
        score_payload.update(
            {
                "keyword_scores": details["keyword_scores"],
                "author_bonus": details["author_bonus"],
                "expert_authors_found": details["expert_authors_found"],
                "reasoning": _clean_text(reasoning_match.group("text")) if reasoning_match else "",
                "tldr": tldr,
                "extracted_keywords": extracted_keywords,
            }
        )

    return {
        "source": source,
        "paper_id": identity["paper_id"],
        "canonical_id": identity["canonical_id"],
        "version": version_from_field if version_from_field is not None else identity["version"],
        "identity_kind": identity["kind"],
        "report_path": str(path),
        "report_at": stamp,
        "title": title,
        "url": url,
        "authors": [item.strip() for item in fields.get("Authors", "").split(",") if item.strip()],
        "published": published,
        "published_text": published_text or stamp.strftime("%Y-%m-%d"),
        "abstract": abstract,
        "abstract_cn": abstract_cn,
        "ss_tldr": ss_tldr,
        "score_payload": score_payload,
        "analysis": analysis,
    }


def _paper_json_from_card(card: Dict[str, Any]) -> Dict[str, Any]:
    paper_id = card["paper_id"]
    pdf_url = _arxiv_pdf_url(paper_id) if card["source"] == "arxiv" else None
    published = card["published"] or card["report_at"]
    return {
        "paper_id": paper_id,
        "title": card["title"],
        "authors": card["authors"],
        "abstract": card["abstract"],
        "published_date": published.isoformat(),
        "url": card["url"],
        "source": card["source"],
        "pdf_url": pdf_url,
        "doi": None,
        "journal": None,
        "categories": [],
        "semantic_scholar_tldr": card["ss_tldr"] or None,
        "arxiv_id": paper_id if card["source"] == "arxiv" else None,
        "canonical_id": card["canonical_id"],
        "version": card["version"] or None,
    }


def import_legacy_history(
    store: Any,
    *,
    history_dir: Path,
    reports_html_dir: Path,
    delivery_run_id: str,
    legacy_keywords_db_path: Optional[Path] = None,
    progress_logger: Optional[Any] = None,
) -> Dict[str, Any]:
    """把旧 JSON 历史与 HTML 日报卡片合并进 SQLite，返回汇总。"""
    log = progress_logger or logger
    summary: Dict[str, Any] = {
        "finished_at": None,
        "history_files": {},
        "reports_scanned": 0,
        "cards_found": 0,
        "imported": 0,
        "skipped_existing_newer": 0,
        "skipped_v4_rows": 0,
        "delivered_ledger_rows": 0,
        "missing_cards": 0,
        "missing_translation": 0,
        "missing_analysis": 0,
        "backlog_queued": 0,
        "legacy_keywords": {},
        "errors": [],
    }

    histories = load_legacy_history_files(history_dir)
    summary["history_files"] = {
        source: len(entries) for source, entries in histories.items()
    }

    keyword_db_path = (
        Path(legacy_keywords_db_path)
        if legacy_keywords_db_path is not None
        else Path(history_dir).parent / "keywords" / "keywords.db"
    )
    if not keyword_db_path.is_file():
        summary["legacy_keywords"] = {"state": "not_found", "records_imported": 0}
        log.info("[LegacyKeywordImport] 未找到旧 keywords.db，跳过关键词库迁移")
    else:
        try:
            from keyword_tracker.database import KeywordDatabase

            keyword_summary = KeywordDatabase(store.db_path).import_legacy_database(
                keyword_db_path, progress_logger=log
            )
            summary["legacy_keywords"] = keyword_summary
            if keyword_summary.get("state") in {"failed", "unreadable", "unsupported_schema"}:
                summary["errors"].append(
                    "旧 keywords.db 迁移未完成: "
                    + str(keyword_summary.get("error") or keyword_summary["state"])
                )
        except Exception as exc:  # 历史关键词不能阻断 HTML / JSON 主迁移
            log.warning("[LegacyImport] 旧 keywords.db 迁移初始化失败: %s", exc)
            summary["legacy_keywords"] = {"state": "failed", "error": str(exc)}
            summary["errors"].append(f"旧 keywords.db 迁移初始化失败: {exc}")

    cards = parse_legacy_report_cards(reports_html_dir)
    summary["cards_found"] = len(cards)
    summary["reports_scanned"] = len({card["report_path"] for card in cards})

    # 旧历史的交付时间优先于报告时间戳（更接近真实推送时刻）。
    arxiv_history = histories.get("arxiv", {})
    arxiv_by_canonical: Dict[str, str] = {}
    for (canonical, _version), delivered_at in arxiv_history.items():
        previous = arxiv_by_canonical.get(canonical)
        if previous is None or delivered_at > previous:
            arxiv_by_canonical[canonical] = delivered_at

    card_index: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    canonical_index: Dict[str, List[Dict[str, Any]]] = {}
    for card in cards:
        if not card["canonical_id"]:
            continue
        key = (card["source"], card["canonical_id"], card["version"])
        existing = card_index.get(key)
        if existing is None or card["report_at"] > existing["report_at"]:
            card_index[key] = card
        canonical_index.setdefault(card["canonical_id"], []).append(card)

    backlog_entries: List[Dict[str, Any]] = []

    def delivered_at_for(card: Dict[str, Any]) -> str:
        if card["source"] == "arxiv":
            exact = arxiv_history.get((card["canonical_id"], card["version"]))
            if exact:
                return exact
            fallback = arxiv_by_canonical.get(card["canonical_id"])
            if fallback:
                return fallback
        return card["report_at"].isoformat()

    for key, card in sorted(card_index.items(), key=lambda item: item[1]["report_at"]):
        try:
            has_score = bool(card["score_payload"])
            needs_translation = bool(card["abstract"].strip())
            has_translation = bool(card["abstract_cn"].strip())
            # v3.2 的期刊/OpenAlex 卡片没有 PDF 深度分析能力；它们即使
            # 及格也不该被误判为缺数据、重新塞进补充队列。旧版只有
            # arXiv 来源走过深度分析流程。
            needs_analysis = (
                card["source"] == "arxiv"
                and card["score_payload"].get("is_qualified", False)
            )
            has_analysis = bool(card["analysis"])

            reasons: List[str] = []
            if not has_score:
                reasons.append("missing_data")
            if needs_translation and not has_translation:
                reasons.append("missing_translation")
            if needs_analysis and not has_analysis:
                reasons.append("missing_analysis")

            complete = not reasons
            delivered_at = delivered_at_for(card)
            payload = {
                "source": card["source"],
                "paper_id": card["paper_id"],
                "canonical_id": card["canonical_id"],
                "version": card["version"],
                "paper_json": _paper_json_from_card(card),
                "score_json": json.dumps(card["score_payload"], ensure_ascii=False) if has_score else None,
                "abstract_cn": card["abstract_cn"] or None,
                "analysis_json": json.dumps(card["analysis"], ensure_ascii=False) if has_analysis else None,
                "score_status": "succeeded" if has_score else "pending",
                "translation_status": (
                    "succeeded" if has_translation else ("not_required" if not needs_translation else "pending")
                ),
                "analysis_status": (
                    "succeeded"
                    if has_analysis
                    else ("not_required" if card["source"] != "arxiv" else "pending")
                ),
                "completed_at": delivered_at,
                "report_path": card["report_path"],
                "delivered_at": delivered_at,
                "delivery_run_id": delivery_run_id,
            }
            outcome = store.import_legacy_paper(payload, delivered=complete)
            summary[outcome if outcome in summary else "imported"] = (
                summary.get(outcome, 0) + 1
            )
            if complete:
                summary["delivered_ledger_rows"] += 1
            else:
                backlog_entries.append(
                    {
                        "source": card["source"],
                        "canonical_id": card["canonical_id"],
                        "version": card["version"],
                        "paper_id": card["paper_id"],
                        "reason": reasons[0],
                        "detail": "缺失: " + ", ".join(reasons),
                        # 这张旧报告卡已经带有论文元数据。把它随积压
                        # 一起保存，后续自动补充可直接重试评分/翻译/分析，
                        # 不必因为历史 arXiv ID 暂时抓取失败而卡住。
                        "paper_json": _paper_json_from_card(card),
                    }
                )
                for reason in reasons:
                    if reason in summary:
                        summary[reason] += 1
        except Exception as exc:  # 单卡片失败不终止整个导入
            log.warning("[LegacyImport] 导入卡片失败 %s: %s", key, exc)
            summary["errors"].append(f"{key}: {exc}")

    # 旧历史里有记录、但任何报告卡片都找不到的论文。
    arxiv_card_canonicals = {
        card["canonical_id"] for card in cards if card["source"] == "arxiv"
    }
    doi_card_canonicals = {
        normalize_doi_key(card["paper_id"])
        for card in cards
        if card["identity_kind"] == "doi"
    }
    for (canonical, version), delivered_at in arxiv_history.items():
        if canonical in arxiv_card_canonicals:
            continue
        paper_id = f"{canonical}v{version}" if version else canonical
        backlog_entries.append(
            {
                "source": "arxiv",
                "canonical_id": canonical,
                "version": version,
                "paper_id": paper_id,
                "reason": "missing_data",
                "detail": "旧历史有交付记录但未找到报告卡片",
            }
        )
        summary["missing_cards"] += 1
    openalex_history = histories.get("openalex", {})
    for (canonical, _version), _delivered_at in openalex_history.items():
        if canonical in doi_card_canonicals:
            continue
        backlog_entries.append(
            {
                "source": "openalex",
                "canonical_id": canonical,
                "version": 0,
                "paper_id": f"https://doi.org/{canonical}",
                "reason": "missing_data",
                "detail": "旧历史有交付记录但未找到报告卡片",
            }
        )
        summary["missing_cards"] += 1

    if backlog_entries:
        try:
            summary["backlog_queued"] = store.record_supplement_backlog(backlog_entries)
        except Exception as exc:
            log.warning("[LegacyImport] 补充积压写入失败: %s", exc)
            summary["errors"].append(f"backlog: {exc}")

    summary["finished_at"] = datetime.now().isoformat()
    log.info(
        "[LegacyImport] 导入完成: 报告 %s 份、卡片 %s 张、账本 %s 条、积压 %s 条",
        summary["reports_scanned"],
        summary["cards_found"],
        summary["delivered_ledger_rows"],
        summary["backlog_queued"],
    )
    return summary

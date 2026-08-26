"""Patch repaired SQLite fields back into already generated daily reports.

SQLite determines *what* needs repair.  This module only applies the repaired
values to the report artifacts recorded in the delivery ledger; it never scans
reports to discover missing data.  The small text-level patcher intentionally
keeps archived reports' surrounding layout, statistics, and reader marks
unchanged.
"""

from __future__ import annotations

import html
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable

logger = logging.getLogger(__name__)

_CARD_OPEN_RE = re.compile(
    r'<div\b[^>]*\bclass=["\'][^"\']*\bcard\s+(?:pass|fail)\b[^"\']*["\'][^>]*>',
    re.IGNORECASE,
)
_DIV_TAG_RE = re.compile(r"</?div\b[^>]*>", re.IGNORECASE)
_HREF_RE = re.compile(r'<a\b[^>]*\bhref=["\'](?P<href>[^"\']+)["\']', re.IGNORECASE)
_TLDR_BLOCK_RE = re.compile(
    r'<div\b[^>]*\bclass=["\']tldr["\'][^>]*>\s*'
    r"<strong>\s*TL;DR:\s*</strong>.*?</div>",
    re.IGNORECASE | re.DOTALL,
)
_TRANSLATION_BLOCK_RE = re.compile(
    r"<details\b[^>]*>\s*<summary>\s*摘要翻译\s*</summary>\s*"
    r'<div\b[^>]*\bclass=["\']analysis-content["\'][^>]*>.*?</div>\s*</details>',
    re.IGNORECASE | re.DOTALL,
)
_ANALYSIS_BLOCK_RE = re.compile(
    r"<details\b[^>]*>\s*<summary>\s*深度分析[^<]*</summary>\s*"
    r'<div\b[^>]*\bclass=["\']analysis-content["\'][^>]*>.*?</div>\s*</details>',
    re.IGNORECASE | re.DOTALL,
)


def _atomic_write(path: Path, content: str) -> None:
    """Replace one report only after its complete patched text is durable."""
    path = Path(path)
    mode = None
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        pass
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _card_end(document: str, start: int) -> int | None:
    """Return the end of one outer card div while respecting nested divs."""
    depth = 0
    for tag in _DIV_TAG_RE.finditer(document, start):
        if tag.group(0).startswith("</"):
            depth -= 1
            if depth == 0:
                return tag.end()
        else:
            depth += 1
    return None


def _normalised_href(value: str) -> str:
    return html.unescape(str(value or "")).strip().rstrip("/").casefold()


def _card_matches(
    card: str,
    *,
    paper_id: str,
    canonical_id: str,
    title: str,
) -> bool:
    """Match stable URL identity first, with title as old-report fallback."""
    wanted = {
        _normalised_href(paper_id),
        _normalised_href(canonical_id),
    }
    wanted.discard("")
    for match in _HREF_RE.finditer(card):
        href = _normalised_href(match.group("href"))
        if any(value and (href.endswith("/" + value) or value in href) for value in wanted):
            return True
    title_text = html.escape(str(title or ""), quote=False)
    return bool(title_text and title_text in card)


def _analysis_label_map() -> Dict[str, str]:
    labels: Dict[str, str] = {}
    try:
        from config import settings

        template = settings.load_report_template("deep_analysis_template.json")
        for module in template.get("modules", []):
            if not isinstance(module, dict):
                continue
            key = module.get("id")
            if isinstance(key, str) and key:
                label = module.get("label") or module.get("name") or key.replace("_", " ").title()
                labels[key] = str(label)
    except Exception:  # pragma: no cover - fallback is enough for damaged templates
        pass
    return labels


def _html_analysis_block(analysis: Dict[str, Any]) -> str:
    labels = _analysis_label_map()
    parts = ['<details><summary>深度分析（补全）</summary><div class="analysis-content">']
    for key, value in analysis.items():
        if key == "__meta" or value in (None, "", [], {}):
            continue
        label = html.escape(labels.get(str(key), str(key).replace("_", " ").title()))
        if isinstance(value, list):
            parts.append(f"<p><strong>{label}:</strong></p><ul>")
            parts.extend(f"<li>{html.escape(str(item))}</li>" for item in value if str(item).strip())
            parts.append("</ul>")
        elif isinstance(value, dict):
            parts.append(f"<p><strong>{label}:</strong></p><ul>")
            parts.extend(
                f"<li><strong>{html.escape(str(item_key))}:</strong> {html.escape(str(item_value))}</li>"
                for item_key, item_value in value.items()
            )
            parts.append("</ul>")
        else:
            parts.append(f"<p><strong>{label}:</strong> {html.escape(str(value))}</p>")
    parts.append("</div></details>")
    return "".join(parts)


def _patch_html_card(card: str, updates: Dict[str, Any]) -> str:
    """Insert or replace the small field blocks inside one matched HTML card."""
    working = card
    inserts: list[str] = []
    tldr = str(updates.get("tldr") or "").strip()
    if tldr:
        tldr_html = f'<div class="tldr"><strong>TL;DR:</strong> {html.escape(tldr)}</div>'
        if _TLDR_BLOCK_RE.search(working):
            working = _TLDR_BLOCK_RE.sub(tldr_html, working, count=1)
        else:
            inserts.append(tldr_html)

    translation = str(updates.get("abstract_cn") or "").strip()
    if translation:
        translation_html = (
            '<details open><summary>摘要翻译</summary><div class="analysis-content"><p>'
            f"{html.escape(translation)}</p></div></details>"
        )
        if _TRANSLATION_BLOCK_RE.search(working):
            working = _TRANSLATION_BLOCK_RE.sub(translation_html, working, count=1)
        else:
            inserts.append(translation_html)

    analysis = updates.get("analysis")
    if isinstance(analysis, dict) and analysis:
        analysis_html = _html_analysis_block(analysis)
        if _ANALYSIS_BLOCK_RE.search(working):
            working = _ANALYSIS_BLOCK_RE.sub(analysis_html, working, count=1)
        else:
            inserts.append(analysis_html)

    if not inserts:
        return working
    first_details = re.search(r"<details\b", working, re.IGNORECASE)
    insertion = "\n" + "\n".join(inserts) + "\n"
    if first_details is not None:
        return working[: first_details.start()] + insertion + working[first_details.start() :]
    closing = working.rfind("</div>")
    if closing == -1:
        raise ValueError("报告卡片缺少结束 div")
    return working[:closing] + insertion + working[closing:]


def patch_html_report(
    path: Path,
    *,
    source: str,
    paper_id: str,
    canonical_id: str,
    title: str,
    updates: Dict[str, Any],
) -> bool:
    """Patch a matching HTML card and return whether a card was found."""
    original = Path(path).read_text(encoding="utf-8", errors="replace")
    pieces: list[str] = []
    cursor = 0
    changed = False
    for match in _CARD_OPEN_RE.finditer(original):
        if match.start() < cursor:
            continue
        end = _card_end(original, match.start())
        if end is None:
            raise ValueError("HTML 报告卡片结构不完整")
        card = original[match.start() : end]
        if not _card_matches(
            card,
            paper_id=paper_id,
            canonical_id=canonical_id,
            title=title,
        ):
            continue
        pieces.extend((original[cursor : match.start()], _patch_html_card(card, updates)))
        cursor = end
        changed = True
        break
    if not changed:
        return False
    pieces.append(original[cursor:])
    _atomic_write(Path(path), "".join(pieces))
    return True


def _markdown_analysis_block(analysis: Dict[str, Any]) -> str:
    labels = _analysis_label_map()
    lines = ["", "<details>", "<summary>深度分析（补全）</summary>", ""]
    for key, value in analysis.items():
        if key == "__meta" or value in (None, "", [], {}):
            continue
        label = labels.get(str(key), str(key).replace("_", " ").title())
        if isinstance(value, list):
            lines.append(f"**{label}**:")
            lines.extend(f"- {item}" for item in value if str(item).strip())
        elif isinstance(value, dict):
            lines.append(f"**{label}**:")
            lines.extend(f"- **{item_key}**: {item_value}" for item_key, item_value in value.items())
        else:
            lines.append(f"**{label}**: {value}")
    lines.extend(["", "</details>", ""])
    return "\n".join(lines)


def patch_markdown_report(
    path: Path,
    *,
    title: str,
    updates: Dict[str, Any],
) -> bool:
    """Append repaired values to the matching Markdown paper section."""
    original = Path(path).read_text(encoding="utf-8", errors="replace")
    title_pattern = re.compile(r"^### .*?" + re.escape(str(title or "")) + r".*$", re.MULTILINE)
    match = title_pattern.search(original)
    if match is None:
        return False
    end_match = re.search(r"^---\s*$", original[match.end() :], re.MULTILINE)
    end = match.end() + end_match.start() if end_match else len(original)
    block = original[match.start() : end]
    additions: list[str] = []
    if str(updates.get("tldr") or "").strip() and "TL;DR" not in block:
        additions.append(f"> **TL;DR（补全）**: {str(updates['tldr']).strip()}")
    if str(updates.get("abstract_cn") or "").strip() and "摘要翻译" not in block:
        additions.append(f"**摘要翻译（补全）**: {str(updates['abstract_cn']).strip()}")
    if isinstance(updates.get("analysis"), dict) and "深度分析" not in block:
        additions.append(_markdown_analysis_block(updates["analysis"]))
    if not additions:
        return True
    patched = original[:end] + "\n\n" + "\n\n".join(additions) + original[end:]
    _atomic_write(Path(path), patched)
    return True


def patch_historical_reports(
    paths: Iterable[str | Path],
    *,
    source: str,
    paper_id: str,
    canonical_id: str,
    paper: Dict[str, Any],
    tldr: str = "",
    abstract_cn: str = "",
    analysis: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Patch all recorded artifacts for one repaired paper.

    Returns a compact result suitable for a durable repair summary.  Missing
    or unmatched files are errors because the SQLite field is already fixed
    but the reader-facing historical report still needs a retry.
    """
    updates = {
        "tldr": tldr,
        "abstract_cn": abstract_cn,
        "analysis": analysis or {},
    }
    existing = [Path(value) for value in paths if Path(value).is_file()]
    if not existing:
        return {"success": False, "patched": 0, "errors": ["未找到交付记录关联的报告文件"]}
    patched = 0
    errors: list[str] = []
    title = str(paper.get("title") or "")
    for path in existing:
        try:
            if path.suffix.lower() == ".html":
                matched = patch_html_report(
                    path,
                    source=source,
                    paper_id=paper_id,
                    canonical_id=canonical_id,
                    title=title,
                    updates=updates,
                )
            elif path.suffix.lower() in {".md", ".markdown"}:
                matched = patch_markdown_report(path, title=title, updates=updates)
            else:
                continue
            if matched:
                patched += 1
            else:
                errors.append(f"{path.name}: 未找到对应论文卡片")
        except Exception as exc:
            logger.warning("历史报告补丁失败 %s: %s", path, exc)
            errors.append(f"{path.name}: {exc}")
    return {"success": not errors and patched > 0, "patched": patched, "errors": errors}

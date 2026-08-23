"""旧历史时间段扫描：寻找 v3.2 时代被漏掉的 arXiv 论文。

导入旧历史后，对旧历史覆盖的日期范围做一次按月分块的完整 arXiv 领域
扫描，把既不在 SQLite（daily_papers / paper_deliveries / 补充积压）也
不在旧历史里的论文记入补充运行积压（reason=missed_scan，随附抓取到的
元数据），等待补充报告流程统一处理。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from utils.legacy_history import load_legacy_history_files

logger = logging.getLogger(__name__)

# 单次查询的时间窗口：按月分块，避免超大区间的深层分页。
SCAN_CHUNK_DAYS = 31


def _known_arxiv_identities(store: Any) -> Set[Tuple[str, int]]:
    """SQLite 中已知的 arXiv 身份（论文行、交付账本、补充积压）。"""
    known: Set[Tuple[str, int]] = set()
    with store._connect() as conn:
        for table in ("daily_papers", "paper_deliveries", "supplement_backlog"):
            rows = conn.execute(
                f"SELECT canonical_id, version FROM {table} "
                "WHERE source = 'arxiv' AND canonical_id != ''"
            ).fetchall()
            for row in rows:
                known.add((row["canonical_id"], int(row["version"] or 0)))
    return known


def _history_date_range(history_dir: Path) -> Optional[Tuple[date, date]]:
    """旧历史覆盖的日期范围（各来源最早/最晚交付日期）。"""
    histories = load_legacy_history_files(history_dir)
    dates: List[date] = []
    for entries in histories.values():
        for delivered_at in entries.values():
            text = str(delivered_at)[:10]
            try:
                dates.append(date.fromisoformat(text))
            except ValueError:
                continue
    if not dates:
        return None
    return min(dates), max(dates)


def _month_chunks(start: date, end: date) -> List[Tuple[date, date]]:
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=SCAN_CHUNK_DAYS - 1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def scan_legacy_range(
    store: Any,
    *,
    history_dir: Path,
    fetch_between: Callable[[date, date], List[Any]],
    logger_override: Optional[Any] = None,
    idle_check: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """扫描旧历史时间段，返回遗漏论文汇总并写入补充积压。

    ``fetch_between(date_from, date_to)`` 返回该闭区间内的论文元数据列表
    （由调用方注入 ArxivSource.fetch_domain_papers_between，便于测试与
    代理配置复用）。``idle_check`` 在每个月份分块前调用，供长扫描在
    每日研究启动时暂停让路。
    """
    log = logger_override or logger
    summary: Dict[str, Any] = {
        "range_start": None,
        "range_end": None,
        "chunks_scanned": 0,
        "papers_scanned": 0,
        "missed_found": 0,
        "backlog_queued": 0,
        "skipped_reason": None,
    }

    date_range = _history_date_range(history_dir)
    if date_range is None:
        summary["skipped_reason"] = "旧历史为空，无法确定扫描时间段"
        log.info("[LegacyScan] %s", summary["skipped_reason"])
        return summary
    start, end = date_range
    summary["range_start"] = start.isoformat()
    summary["range_end"] = end.isoformat()

    known = _known_arxiv_identities(store)
    log.info(
        "[LegacyScan] 扫描 %s 至 %s（SQLite 已知身份 %s 个）",
        start, end, len(known),
    )

    missed: List[Dict[str, Any]] = []
    for chunk_start, chunk_end in _month_chunks(start, end):
        if idle_check is not None:
            idle_check()
        papers = fetch_between(chunk_start, chunk_end)
        summary["chunks_scanned"] += 1
        summary["papers_scanned"] += len(papers)
        chunk_missed = 0
        for paper in papers:
            canonical = (getattr(paper, "canonical_id", None) or paper.paper_id).strip()
            version = int(getattr(paper, "version", None) or 0)
            if (canonical, version) in known:
                continue
            known.add((canonical, version))
            chunk_missed += 1
            missed.append(
                {
                    "source": "arxiv",
                    "canonical_id": canonical,
                    "version": version,
                    "paper_id": paper.paper_id,
                    "reason": "missed_scan",
                    "detail": f"时间段扫描发现（{chunk_start}~{chunk_end} 提交）",
                    "paper_json": paper.to_dict(),
                }
            )
        log.info(
            "[LegacyScan] 分块 %s~%s: %s 篇，本块遗漏 %s 篇",
            chunk_start, chunk_end, len(papers), chunk_missed,
        )

    summary["missed_found"] = len(missed)
    if missed:
        try:
            summary["backlog_queued"] = store.record_supplement_backlog(missed)
        except Exception as exc:
            log.warning("[LegacyScan] 遗漏论文写入积压失败: %s", exc)
            summary["backlog_queued"] = 0
    log.info(
        "[LegacyScan] 扫描完成: %s 篇论文中遗漏 %s 篇，新入积压 %s 篇",
        summary["papers_scanned"], summary["missed_found"], summary["backlog_queued"],
    )
    return summary

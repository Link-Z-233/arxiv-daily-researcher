"""
每日研究模式主流程

从多个数据源抓取论文，评分、深度分析并生成报告。

工作流程:
1. 加载配置
2. 准备关键词（主要关键词 + Reference 提取的次要关键词）
3. 从多个数据源抓取论文
4. 对所有论文进行加权评分
5. 对 ArXiv 及格论文进行深度分析（其他来源跳过）
6. 按数据源分别生成报告
7. 关键词趋势处理
8. 发送通知
"""

import hashlib
from dataclasses import asdict
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Any

from tqdm import tqdm

from config import settings
from utils.logger import setup_logger
from utils.token_counter import token_counter
from agents import KeywordAgent, AnalysisAgent
from sources import (
    ArxivFetchError,
    HuggingFacePapersFetchError,
    OpenAlexFetchError,
    PaperMetadata,
    SearchAgent,
    SourceScanReceiptError,
)
from report.daily import Reporter
from notifications import NotifierAgent, RunResult
from utils.daily_research_store import DailyResearchStore
from utils.daily_research_errors import PaperStageError, paper_stage_error
from utils.daily_research_fingerprints import (
    build_score_audit_metadata,
    build_stage_input_fingerprints,
)
from scoring_policy import (
    optional_score_value,
    qualification_threshold_for,
    qualification_score_for,
    ranking_score_for,
    response_strategy_id,
)
from utils.webdav_sync import (
    after_report_sync_maintenance_entry,
    deliver_pending_after_report_syncs,
)

logger = setup_logger("DailyResearch")


def _validate_report_paths(
    report_paths: Dict[str, Path], scored_papers_by_source: Dict[str, List[Dict[str, Any]]]
) -> None:
    """Ensure every enabled report artifact exists before committing history."""
    if not settings.ENABLE_MARKDOWN_REPORT and not settings.ENABLE_HTML_REPORT:
        raise RuntimeError(
            "日报配置无有效输出格式：至少启用 Markdown 或 HTML 报告，"
            "否则不会写入论文交付历史"
        )

    expected_keys = set()
    for source, papers in scored_papers_by_source.items():
        if not papers:
            continue
        if settings.ENABLE_MARKDOWN_REPORT:
            expected_keys.add(source)
        if settings.ENABLE_HTML_REPORT:
            expected_keys.add(f"{source}_html")

    missing = expected_keys.difference(report_paths)
    invalid = []
    for key in expected_keys.intersection(report_paths):
        path = Path(report_paths[key])
        try:
            valid = path.is_file() and path.stat().st_size > 0
        except OSError:
            valid = False
        if not valid:
            invalid.append(f"{key}={path}")

    if missing or invalid:
        details = []
        if missing:
            details.append(f"缺少输出: {', '.join(sorted(missing))}")
        if invalid:
            details.append(f"空文件或不可访问: {', '.join(sorted(invalid))}")
        raise RuntimeError("报告完整性校验失败，未写入论文历史: " + "; ".join(details))


def _select_top_papers(
    scored_papers_by_source: Dict[str, List[Dict[str, Any]]], limit: int
) -> List[Dict[str, Any]]:
    """Select notification recommendations exclusively from qualified papers."""
    qualified = []
    for source, scored_papers in scored_papers_by_source.items():
        for paper in scored_papers:
            score_response = paper["score_response"]
            if not score_response.is_qualified:
                continue
            qualified.append(
                {
                    "title": paper["title"],
                    "score": ranking_score_for(score_response),
                    # Keep notification payloads display-only, but make V2's
                    # distinction explicit and safe for older hydrated rows.
                    "relevance_score": qualification_score_for(score_response),
                    "qualification_threshold": qualification_threshold_for(score_response),
                    "has_separate_relevance_score": optional_score_value(
                        score_response, "relevance_score"
                    ) is not None,
                    "strategy_id": response_strategy_id(score_response),
                    "source": source,
                    "tldr": score_response.tldr,
                    "url": paper["url"],
                }
            )
    qualified.sort(key=lambda item: item["score"], reverse=True)
    return qualified[: max(0, limit)]


def _exclude_sqlite_delivered_papers(
    store: DailyResearchStore, papers_by_source: Dict[str, List[PaperMetadata]]
) -> Dict[str, List[PaperMetadata]]:
    """Filter exact versions already committed to the SQLite delivery ledger."""
    filtered = {}
    for source, papers in papers_by_source.items():
        delivered = [
            paper for paper in papers if store.is_paper_delivered(source, paper.paper_id)
        ]
        if delivered:
            logger.info(
                "[%s] SQLite 交付记录跳过 %s 篇已完成论文（兼容历史文件恢复）",
                source,
                len(delivered),
            )
        filtered[source] = [paper for paper in papers if paper not in delivered]
    return filtered


def _arxiv_mirror_canonical_id(paper: PaperMetadata) -> str:
    """Return a normalised arXiv canonical ID exposed by a mirror record.

    Mirror sources deliberately retain their own source/paper identity in the
    delivery ledger.  This helper is only for deciding whether an arXiv record
    should take precedence in the current report, or whether an already
    delivered arXiv version has made a late mirror notification redundant.
    """
    from sources.base_source import split_arxiv_version

    raw_id = getattr(paper, "arxiv_id", None)
    if not isinstance(raw_id, str) or not raw_id.strip():
        return ""
    canonical_id, _version = split_arxiv_version(raw_id.strip())
    return canonical_id.strip()


def _exclude_cross_source_arxiv_mirrors(
    store: DailyResearchStore | None,
    papers_by_source: Dict[str, List[PaperMetadata]],
) -> Dict[str, List[PaperMetadata]]:
    """Prefer arXiv records over supplement-source mirrors without blocking revisions.

    A Hugging Face entry is a mirror of a particular arXiv work, but its
    source identity remains separate so a later arXiv v2/v3 is never hidden.
    We suppress only the mirror: first if the same canonical arXiv work is in
    the current batch, then if any arXiv version was previously delivered.
    This avoids double reports when HF's curated feed arrives after the
    canonical arXiv delivery while preserving HF-only discovery when arXiv is
    not enabled.
    """
    arxiv_canonicals = {
        (paper.canonical_id or paper.paper_id).strip()
        for paper in papers_by_source.get("arxiv", [])
        if (paper.canonical_id or paper.paper_id).strip()
    }
    if not arxiv_canonicals and store is None:
        return papers_by_source

    filtered: Dict[str, List[PaperMetadata]] = {}
    for source, papers in papers_by_source.items():
        if source != "huggingface_papers":
            filtered[source] = papers
            continue

        kept = []
        skipped_current = 0
        skipped_delivered = 0
        for paper in papers:
            canonical_id = _arxiv_mirror_canonical_id(paper)
            if canonical_id and canonical_id in arxiv_canonicals:
                skipped_current += 1
                continue
            if canonical_id and store is not None and store.has_delivered_arxiv_canonical(canonical_id):
                skipped_delivered += 1
                continue
            kept.append(paper)
        if skipped_current or skipped_delivered:
            logger.info(
                "[%s] 跨源 arXiv 镜像去重跳过 %s 篇（同轮 arXiv %s，已交付 arXiv %s）",
                source,
                skipped_current + skipped_delivered,
                skipped_current,
                skipped_delivered,
            )
        filtered[source] = kept
    return filtered


def _score_single_paper(
    paper,
    source,
    analysis_agent,
    all_keywords,
    translation_cache,
    cache_lock,
    keyword_tracker,
    score_response=None,
    abstract_cn=None,
    translate=True,
):
    """
    对单篇论文进行评分和翻译（供并发调用）。

    线程安全：translation_cache 通过 cache_lock 保护。
    """
    if score_response is None:
        try:
            score_response = analysis_agent.score_paper_with_keywords(
                title=paper.title,
                authors=paper.authors,
                abstract=paper.abstract,
                keywords_dict=all_keywords,
            )
        except Exception as exc:
            raise paper_stage_error("score", exc) from exc

    if abstract_cn is None:
        abstract_cn = ""
    if translate and abstract_cn == "" and paper.abstract and paper.abstract.strip():
        abstract_hash = hashlib.md5(paper.abstract.encode("utf-8")).hexdigest()

        with cache_lock:
            cached = translation_cache.get(abstract_hash)

        if cached:
            abstract_cn = cached
            logger.debug(f"使用缓存的翻译: {paper.title[:30]}...")
        else:
            try:
                abstract_cn = analysis_agent.translate_abstract(paper.abstract)
            except Exception as exc:
                raise paper_stage_error("translation", exc) from exc
            if not abstract_cn or not abstract_cn.strip():
                raise PaperStageError("translation", "摘要翻译返回空结果")
            with cache_lock:
                translation_cache[abstract_hash] = abstract_cn
            logger.debug(f"翻译并缓存: {paper.title[:30]}...")

    scored = {
        "paper_metadata": paper,
        "paper_id": paper.paper_id,
        "title": paper.title,
        "authors": paper.get_authors_string(),
        "abstract": paper.abstract,
        "abstract_cn": abstract_cn,
        "url": paper.url,
        "pdf_url": paper.pdf_url,
        "published": paper.published_date.strftime("%Y-%m-%d") if paper.published_date else "N/A",
        "score_response": score_response,
    }

    if keyword_tracker and score_response.extracted_keywords:
        try:
            keyword_tracker.record_keywords(
                keywords=score_response.extracted_keywords, paper_id=paper.paper_id, source=source
            )
        except Exception as e:
            logger.warning(f"关键词记录失败 ({paper.paper_id[:30]}...): {e}")

    return scored


def _score_or_translate_stage_error(stage: str, exc: BaseException) -> PaperStageError:
    """Classify only the local stage that raised, never its message text."""
    return paper_stage_error(stage, exc)


def _deep_analyze_single_paper(paper_info, analysis_agent):
    """
    对单篇论文进行深度分析（供并发调用）。

    返回:
        dict 或 None: {'paper_id': ..., 'analysis': ...} 或 None（失败时）
    """
    paper_meta = paper_info.get("paper_metadata")
    pdf_url = paper_meta.get_best_pdf_url() if paper_meta else paper_info.get("pdf_url")

    max_attempts = max(1, int(getattr(settings, "RETRY_MAX_ATTEMPTS", 3)))
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            analysis = analysis_agent.deep_analyze(
                title=paper_info["title"],
                pdf_url=pdf_url,
                abstract=paper_info["abstract"],
                fallback_to_abstract=True,
            )
            if analysis:
                return {
                    "paper_id": paper_info["paper_id"],
                    "analysis": analysis,
                    "paper_meta": paper_meta,
                    "title": paper_info["title"],
                }
            last_error = RuntimeError("深度分析未返回结果")
        except Exception as exc:
            last_error = exc

        if attempt < max_attempts:
            wait_seconds = min(
                int(getattr(settings, "RETRY_MIN_WAIT", 2)) * (2 ** (attempt - 1)),
                int(getattr(settings, "RETRY_MAX_WAIT", 30)),
            )
            logger.warning(
                "深度分析失败，将重试 (%s/%s)，等待 %ss: %s",
                attempt,
                max_attempts,
                wait_seconds,
                last_error,
            )
            time.sleep(wait_seconds)

    raise PaperStageError(
        "analysis",
        f"深度分析在 {max_attempts} 次尝试后仍失败: {last_error}",
        cause=last_error,
    ) from last_error


def _score_or_hydrate_paper(
    run_id,
    source,
    paper,
    analysis_agent,
    all_keywords,
    translation_cache,
    cache_lock,
    keyword_tracker,
    store,
    previous_version_info=None,
):
    """Reuse persisted scoring when available, otherwise score and persist."""
    existing_record = None
    if store:
        existing_record = store.get_paper_record(source, paper.paper_id)
        # Restore durable optional enrichment before calculating cache keys.
        # In particular, an earlier retry may have retained an arXiv PDF URL
        # while a new source response was temporarily missing it.
        store.restore_optional_enrichment_from_record(paper, existing_record)
        fingerprints = build_stage_input_fingerprints(
            paper,
            all_keywords,
            getattr(analysis_agent, "deep_template", {}),
        )
        store.upsert_paper_seen(run_id, source, paper, fingerprints)
        record = store.get_paper_record(source, paper.paper_id)
        hydrated = store.hydrate_scored_paper(paper, record, require_translation=False)
        if hydrated:
            scored = hydrated
            score_is_new = False
            logger.debug(f"复用已持久化评分: {paper.title[:30]}...")
        else:
            try:
                score_response = analysis_agent.score_paper_with_keywords(
                    title=paper.title,
                    authors=paper.authors,
                    abstract=paper.abstract,
                    keywords_dict=all_keywords,
                )
            except Exception as exc:
                raise _score_or_translate_stage_error("score", exc) from exc
            scored = _score_single_paper(
                paper,
                source,
                analysis_agent,
                all_keywords,
                translation_cache,
                cache_lock,
                keyword_tracker,
                score_response=score_response,
                abstract_cn="",
                translate=False,
            )
            store.update_score(
                run_id,
                source,
                scored,
                score_input_fingerprint=fingerprints.get("score"),
                score_audit_metadata=build_score_audit_metadata(
                    paper,
                    all_keywords,
                    fingerprints.get("score"),
                ),
            )
            score_is_new = True

        record = store.get_paper_record(source, paper.paper_id)
        translation_required = bool(paper.abstract and paper.abstract.strip())
        translation_done = record["translation_status"] in ("succeeded", "not_required")
        if translation_required and not translation_done:
            abstract_hash = hashlib.md5(paper.abstract.encode("utf-8")).hexdigest()
            with cache_lock:
                cached = translation_cache.get(abstract_hash)
            if cached:
                abstract_cn = cached
            else:
                try:
                    abstract_cn = analysis_agent.translate_abstract(paper.abstract)
                except Exception as exc:
                    raise _score_or_translate_stage_error("translation", exc) from exc
                if not abstract_cn or not abstract_cn.strip():
                    raise PaperStageError("translation", "摘要翻译返回空结果")
                with cache_lock:
                    translation_cache[abstract_hash] = abstract_cn
            store.update_translation(
                run_id,
                source,
                paper.paper_id,
                abstract_cn,
                translation_input_fingerprint=fingerprints.get("translation"),
            )
            scored["abstract_cn"] = abstract_cn
        elif not translation_required:
            store.mark_translation_not_required(run_id, source, paper.paper_id)
        elif record["abstract_cn"]:
            scored["abstract_cn"] = record["abstract_cn"]

        _add_paper_delivery_context(
            scored,
            paper,
            existing_record,
            store.get_previous_version_record(source, paper),
            previous_version_info,
        )
        scored["stage_fingerprints"] = fingerprints
        return scored

    scored = _score_single_paper(
        paper,
        source,
        analysis_agent,
        all_keywords,
        translation_cache,
        cache_lock,
        keyword_tracker,
    )

    _add_paper_delivery_context(
        scored,
        paper,
        existing_record,
        store.get_previous_version_record(source, paper) if store else None,
        previous_version_info,
    )

    return scored


def _add_paper_delivery_context(
    scored, paper, existing_record=None, previous_record=None, previous_history=None
):
    """Attach retry/revision metadata consumed by report renderers."""
    if existing_record is not None and existing_record["completed_at"] is None:
        # Every fetched candidate is now registered before the per-run queue
        # limit is applied.  A pristine pending row is therefore not itself a
        # retry; only prior stage work/failure should receive that label.
        was_attempted = bool(existing_record["retry_count"]) or any(
            existing_record[field] != "pending"
            for field in ("score_status", "translation_status", "analysis_status")
        )
        if was_attempted:
            scored["is_retry"] = True

    previous_version = None
    previous_pushed_at = None
    if previous_record is not None:
        previous_version = previous_record["version"]
        previous_pushed_at = (
            previous_record["delivered_at"]
            if "delivered_at" in previous_record.keys()
            else None
        ) or previous_record["completed_at"]
    elif previous_history:
        previous_version = previous_history.get("version")
        previous_pushed_at = previous_history.get("processed_at")

    if previous_version is not None:
        scored["revision"] = {
            "version": paper.version,
            "previous_version": previous_version,
            "previous_pushed_at": previous_pushed_at,
        }


def _delivered_papers_for_finalization(
    scored_papers_by_source: Dict[str, List[Dict[str, Any]]],
    analyses_by_source: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Build the exact paper set eligible for an atomic report delivery commit."""
    delivered = {}
    for source, scored_papers in scored_papers_by_source.items():
        analyzed_ids = {item["paper_id"] for item in analyses_by_source.get(source, [])}
        eligible = []
        for paper_info in scored_papers:
            paper_meta = paper_info.get("paper_metadata")
            requires_analysis = bool(
                settings.DAILY_ENABLE_DEEP_ANALYSIS
                and paper_info["score_response"].is_qualified
                and paper_meta
                and paper_meta.has_pdf_access()
            )
            if requires_analysis and paper_info["paper_id"] not in analyzed_ids:
                raise RuntimeError(
                    f"深度分析尚未完成，不能交付日报: {source}:{paper_info['paper_id']}"
                )
            eligible.append({**paper_info, "requires_analysis": requires_analysis})
        if eligible:
            delivered[source] = eligible
    return delivered


def _run_result_notification_entries(
    notifier: NotifierAgent, result: RunResult
) -> List[Dict[str, Any]]:
    """Return one durable notification request per currently configured channel."""
    if result.success and not notifier.settings.NOTIFY_ON_SUCCESS:
        return []
    if not result.success and not notifier.settings.NOTIFY_ON_FAILURE:
        return []
    payload = {"result": asdict(result)}
    return [
        {"event_type": "daily_run_result", "channel": channel, "payload": payload}
        for channel in notifier.configured_channels()
    ]


def _build_daily_run_result(
    total_papers_count: int,
    scored_papers_by_source: Dict[str, List[Dict[str, Any]]],
    analyses_by_source: Dict[str, List[Dict[str, Any]]],
    report_paths: Dict[str, Path],
) -> RunResult:
    """Build the immutable report-delivery notification payload before committing it."""
    run_result = RunResult(
        run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_papers_fetched=total_papers_count,
        top_papers=_select_top_papers(scored_papers_by_source, settings.NOTIFICATION_TOP_N),
    )
    for source, scored_papers in scored_papers_by_source.items():
        source_qualified = sum(1 for p in scored_papers if p["score_response"].is_qualified)
        source_analyzed = len(analyses_by_source.get(source, []))
        run_result.papers_by_source[source] = len(scored_papers)
        run_result.qualified_by_source[source] = source_qualified
        run_result.analyzed_by_source[source] = source_analyzed
        run_result.total_qualified += source_qualified
        run_result.total_analyzed += source_analyzed

    run_result.report_paths = {source: str(path) for source, path in report_paths.items()}
    if settings.TOKEN_TRACKING_ENABLED:
        run_result.token_usage = token_counter.get_summary()
    return run_result


class DailyResearchPipeline:
    """
    每日研究模式流水线。

    从多个数据源抓取论文，评分筛选，深度分析，生成报告，发送通知。
    """

    def run(self):
        """
        执行每日研究完整流程。
        """
        store = None
        run_id = None
        notifier = None
        report_delivery_committed = False
        try:
            print("\n" + "=" * 80)
            print("🚀 多数据源研究系统启动")
            print("=" * 80 + "\n")

            logger.info("=" * 80)
            logger.info("启动多数据源研究系统")
            logger.info("=" * 80)

            if settings.TOKEN_TRACKING_ENABLED:
                token_counter.reset()

            # SQLite is the authoritative daily-research ledger. It stores
            # exact versions, resumable stages, scan checkpoints and atomic
            # delivery state; falling back to JSON would discard those
            # guarantees on an upgraded installation.
            store = DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
            run_id = store.start_run(0)
            logger.info(f"每日研究 SQLite 状态库已启用: {settings.DAILY_RESEARCH_DB_PATH}")

            # WebDAV is non-critical post-report maintenance. Retry old
            # uploads before the long scan without letting a remote outage
            # affect this run's paper identity or delivery state.
            try:
                sync_summary = deliver_pending_after_report_syncs(store, logger)
                if sync_summary["claimed"]:
                    logger.info(
                        "已补发待处理 WebDAV 同步: 完成 %s，延后 %s",
                        sync_summary["completed"],
                        sync_summary["deferred"],
                    )
            except Exception as exc:
                logger.warning("待补发 WebDAV 同步检查失败，将继续生成日报: %s", exc)

            # Notification retries are independent from the current paper scan.
            # Do this before processing so an old failed webhook is not delayed
            # by a long LLM run, and never affects whether a paper is "new".
            if settings.ENABLE_NOTIFICATIONS and store:
                try:
                    notifier = NotifierAgent()
                    retry_summary = notifier.deliver_pending_run_results(store)
                    if retry_summary["claimed"]:
                        logger.info(
                            "已补发待处理通知: 发送 %s，延后 %s",
                            retry_summary["sent"],
                            retry_summary["deferred"],
                        )
                except Exception as exc:
                    # The report pipeline must remain available even if a
                    # notification provider or its templates are broken.
                    logger.warning("待补发通知检查失败，将继续生成日报: %s", exc)

            # ==================== 阶段1: 配置加载 ====================
            logger.info(">>> 阶段1: 加载配置...")

            logger.info(f"启用的数据源: {settings.ENABLED_SOURCES}")
            if "arxiv" in settings.ENABLED_SOURCES:
                logger.info(f"ArXiv目标领域: {settings.TARGET_DOMAINS}")
            if settings.TARGET_JOURNALS:
                logger.info(f"目标期刊: {settings.TARGET_JOURNALS}")
            logger.info(f"搜索天数: {settings.SEARCH_DAYS}")
            logger.info("日报抓取: 完整扫描时间窗口内的全部论文（由请求限速和重试保护服务）")
            logger.info(f"启用Reference提取: {settings.ENABLE_REFERENCE_EXTRACTION}")

            # ==================== 阶段2: 关键词准备 ====================
            logger.info(">>> 阶段2: 准备关键词...")

            keyword_agent = KeywordAgent()
            all_keywords = keyword_agent.get_all_keywords()

            if not all_keywords:
                logger.error("错误: 未找到任何关键词。请在 configs/config.json 中配置主要关键词。")
                fail_result = RunResult(
                    run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    success=False,
                    error_message="未找到任何关键词，请在 configs/config.json 中配置主要关键词",
                )
                if settings.ENABLE_NOTIFICATIONS:
                    try:
                        NotifierAgent().notify(fail_result)
                    except Exception:
                        pass
                if store and run_id:
                    store.fail_run(run_id, fail_result.error_message)
                return fail_result

            logger.info("关键词准备完成:")
            logger.info(
                f"  - 主要关键词: {len(settings.PRIMARY_KEYWORDS)} 个（权重 {settings.PRIMARY_KEYWORD_WEIGHT}）"
            )
            if settings.ENABLE_REFERENCE_EXTRACTION:
                ref_count = len(all_keywords) - len(settings.PRIMARY_KEYWORDS)
                logger.info(f"  - Reference关键词: {ref_count} 个（权重 0.3-0.8）")
            logger.info(f"  - 关键词总数: {len(all_keywords)} 个")
            logger.info(f"  - 总权重: {sum(all_keywords.values()):.2f}")

            total_weight = sum(all_keywords.values())
            if settings.normalized_score_strategy() == "core_relevance_v2":
                logger.info(
                    "  - V2 核心相关性门槛: %.1f；核心关键词强匹配门槛: %.1f",
                    settings.CORE_RELEVANCE_THRESHOLD,
                    settings.CORE_KEYWORD_MIN_SCORE,
                )
            else:
                passing_score = settings.calculate_passing_score(total_weight)
                logger.info(f"  - 动态及格分: {passing_score:.1f}")
                logger.info(
                    f"  - 及格分公式: {settings.PASSING_SCORE_BASE} + {settings.PASSING_SCORE_WEIGHT_COEFFICIENT} × {total_weight:.1f}"
                )

            # ==================== 阶段3: 抓取所有最新论文 ====================
            logger.info(">>> 阶段3: 从多个数据源抓取论文...")

            search_agent = SearchAgent(
                history_dir=settings.HISTORY_DIR,
                enabled_sources=settings.ENABLED_SOURCES,
                arxiv_domains=settings.TARGET_DOMAINS,
                journals=settings.TARGET_JOURNALS,
                openalex_email=settings.OPENALEX_EMAIL,
                openalex_api_key=settings.OPENALEX_API_KEY,
                enable_semantic_scholar=settings.ENABLE_SEMANTIC_SCHOLAR_TLDR,
                semantic_scholar_api_key=settings.SEMANTIC_SCHOLAR_API_KEY,
                extra_source_definitions=getattr(settings, "EXTRA_SOURCE_DEFINITIONS", []),
                # SQLite is the sole daily-history authority. Legacy JSON files
                # are neither read as a filter nor updated after delivery.
                use_legacy_history_filter=False,
            )

            # Semantic Scholar is optional enrichment, but a synchronous
            # lookup can take a while for journal-heavy scans.  Establish the
            # recovery checkpoint immediately before the source queries, not
            # before construction/configuration work, so a successful scan
            # window starts where the APIs were actually queried.
            effective_scan_days = settings.SEARCH_DAYS
            if store and run_id:
                # A failed run must not let its unreported papers age out of
                # the user-configured window.  The store records a per-source
                # checkpoint only after a complete report/no-paper scan has
                # committed, so this recovery window is expanded precisely
                # when a prior scan did not reach a durable terminal state.
                effective_scan_days = store.prepare_scan(
                    run_id,
                    settings.SEARCH_DAYS,
                    search_agent.get_enabled_sources(),
                )
                if effective_scan_days > settings.SEARCH_DAYS:
                    logger.warning(
                        "日报恢复扫描窗口已扩展: 配置 %s 天 -> %s 天；"
                        "已交付版本会由 SQLite 账本过滤，不会重复推送",
                        settings.SEARCH_DAYS,
                        effective_scan_days,
                    )

            try:
                scan_receipt_callbacks = {}
                if store and run_id:
                    # Receipt persistence is part of source completeness, not
                    # optional analytics. Every configured report source gets
                    # one callback: arXiv emits a rich domain receipt, while
                    # supplementary feeds/each OpenAlex journal emit a source
                    # summary. A callback failure aborts before reports or
                    # watermarks can make an incomplete scan look complete.
                    for receipt_source in search_agent.get_enabled_sources():
                        scan_receipt_callbacks[receipt_source] = (
                            lambda receipt, source=receipt_source: store.record_scan_receipt(
                                run_id, source, receipt
                            )
                        )
                papers_by_source: Dict[str, List[PaperMetadata]] = search_agent.fetch_all_papers(
                    days=effective_scan_days,
                    scan_receipt_callbacks=scan_receipt_callbacks,
                )
            except SourceScanReceiptError as sre:
                error_detail = str(sre)
                logger.error("数据源扫描收据持久化失败，终止本次运行: %s", error_detail)
                receipt_fail_result = RunResult(
                    run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    success=False,
                    error_message=f"数据源扫描收据失败: {error_detail}",
                )
                if settings.ENABLE_NOTIFICATIONS:
                    try:
                        NotifierAgent().notify(receipt_fail_result)
                        NotifierAgent().notify_error(
                            "source_scan_receipt",
                            "数据源扫描收据持久化失败；本次日报已终止，"
                            "以避免将无完整扫描证据的结果标记为成功。",
                        )
                    except Exception as ne:
                        logger.warning("发送扫描收据错误通知失败: %s", ne)
                if store and run_id:
                    store.fail_run(run_id, error_detail)
                return receipt_fail_result
            except ArxivFetchError as afe:
                # ArXiv 抓取彻底失败（多次重试后仍无法获取任何论文）
                error_detail = str(afe)
                logger.error(f"ArXiv 抓取失败，终止本次运行: {error_detail}")
                fetch_fail_result = RunResult(
                    run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    success=False,
                    error_message=f"ArXiv 抓取失败: {error_detail}",
                )
                if settings.ENABLE_NOTIFICATIONS:
                    try:
                        NotifierAgent().notify(fetch_fail_result)
                        NotifierAgent().notify_error(
                            "arxiv_fetch",
                            f"ArXiv 论文抓取失败\n\n错误详情：{error_detail}\n\n建议检查网络连接及 ArXiv 服务状态。",
                        )
                    except Exception as ne:
                        logger.warning(f"发送错误通知失败: {ne}")
                if store and run_id:
                    store.fail_run(run_id, error_detail)
                return fetch_fail_result
            except HuggingFacePapersFetchError as hfe:
                # The optional source is still fail-closed once enabled: an
                # incomplete curated feed must not be reported as an empty
                # success, because its missed entries could fall outside the
                # recovery window before the next run.
                error_detail = str(hfe)
                logger.error("Hugging Face Papers 抓取失败，终止本次运行: %s", error_detail)
                fetch_fail_result = RunResult(
                    run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    success=False,
                    error_message=f"Hugging Face Papers 抓取失败: {error_detail}",
                )
                if settings.ENABLE_NOTIFICATIONS:
                    try:
                        NotifierAgent().notify(fetch_fail_result)
                        NotifierAgent().notify_error(
                            "huggingface_papers_fetch",
                            "Hugging Face Papers 抓取失败\n\n"
                            f"错误详情：{error_detail}\n\n"
                            "已终止本次日报，以避免产生不完整的数据源结果。",
                        )
                    except Exception as ne:
                        logger.warning("发送错误通知失败: %s", ne)
                if store and run_id:
                    store.fail_run(run_id, error_detail)
                return fetch_fail_result
            except OpenAlexFetchError as oae:
                # Enabled journals are part of the requested daily scope.  A
                # malformed entry, failed page, or partial journal list must
                # not fall through to the generic exception path: return a
                # normal failed result so schedulers observe it, while leaving
                # the source watermark unchanged for a full retry next run.
                error_detail = str(oae)
                logger.error("OpenAlex 期刊抓取失败，终止本次运行: %s", error_detail)
                fetch_fail_result = RunResult(
                    run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    success=False,
                    error_message=f"OpenAlex 期刊抓取失败: {error_detail}",
                )
                if settings.ENABLE_NOTIFICATIONS:
                    try:
                        NotifierAgent().notify(fetch_fail_result)
                        NotifierAgent().notify_error(
                            "openalex_fetch",
                            "OpenAlex 期刊论文抓取失败\n\n"
                            f"错误详情：{error_detail}\n\n"
                            "已终止本次日报，以避免产生不完整的期刊数据源结果。",
                        )
                    except Exception as ne:
                        logger.warning("发送错误通知失败: %s", ne)
                if store and run_id:
                    store.fail_run(run_id, error_detail)
                return fetch_fail_result

            # SQLite is the only authoritative delivery ledger. Filter exact
            # source/version deliveries before registering new queue entries.
            papers_by_source = _exclude_sqlite_delivered_papers(store, papers_by_source)

            papers_by_source = _exclude_cross_source_arxiv_mirrors(
                store, papers_by_source
            )

            registered_candidate_count = store.register_paper_candidates(
                run_id, papers_by_source
            )
            papers_by_source, pending_paper_count = store.select_pending_papers(
                search_agent.get_enabled_sources(),
                int(getattr(settings, "DAILY_MAX_PAPERS_PER_RUN", 0)),
            )
            total_papers_count = sum(len(papers) for papers in papers_by_source.values())
            deferred_paper_count = pending_paper_count - total_papers_count

            if total_papers_count == 0:
                logger.info("未找到新的或待恢复的论文。")
                print("\n未找到新的或待恢复的论文，程序退出。")
                no_papers_result = RunResult(
                    run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), success=True
                )
                if store and run_id:
                    store.complete_run(run_id, {})
                    # The scan/checkpoint commit is complete before the
                    # optional status notification. A provider failure must
                    # never turn an already completed no-paper run back into
                    # a failed run or reopen its recovery window.
                    report_delivery_committed = True
                if settings.ENABLE_NOTIFICATIONS:
                    # No-paper runs do not have a report delivery to recover;
                    # retain the legacy best-effort behaviour for this status-only notice.
                    try:
                        (notifier or NotifierAgent()).notify(no_papers_result)
                    except Exception as exc:
                        logger.warning("无新论文通知发送失败，运行状态仍保持已完成: %s", exc)
                return no_papers_result

            logger.info(
                "完整扫描发现 %s 篇未交付候选；SQLite 当前待处理 %s 篇，"
                "本次处理 %s 篇，留待后续 %s 篇（%s 个数据源）",
                registered_candidate_count,
                pending_paper_count,
                total_papers_count,
                deferred_paper_count,
                len(papers_by_source),
            )

            if store and run_id:
                store.set_run_total(run_id, total_papers_count)

            # ==================== 阶段4: 对所有论文评分 ====================
            logger.info(">>> 阶段4: 对所有论文进行加权评分...")

            analysis_agent = AnalysisAgent()
            scored_papers_by_source: Dict[str, List[Dict[str, Any]]] = {}

            keyword_tracker = None
            if settings.KEYWORD_TRACKER_ENABLED:
                try:
                    from keyword_tracker import KeywordTracker

                    keyword_tracker = KeywordTracker()
                    logger.debug("KeywordTracker 已初始化")
                except Exception as e:
                    logger.warning(f"KeywordTracker 初始化失败: {e}")

            translation_cache = {}
            cache_lock = threading.Lock()
            stage_errors = []
            logger.debug("翻译缓存已启用")

            for source, papers in papers_by_source.items():
                if not papers:
                    continue

                logger.info(f"  评分数据源 [{source}]: {len(papers)} 篇论文")
                scored_papers = []

                if settings.ENABLE_CONCURRENCY and len(papers) > 1:
                    logger.info(f"    使用并发模式 (workers={settings.CONCURRENCY_WORKERS})")
                    with tqdm(
                        total=len(papers), desc=f"📊 [{source}] 评分", unit="篇", ncols=100
                    ) as pbar:
                        with ThreadPoolExecutor(
                            max_workers=settings.CONCURRENCY_WORKERS
                        ) as executor:
                            futures = {
                                executor.submit(
                                    _score_or_hydrate_paper,
                                    run_id,
                                    source,
                                    paper,
                                    analysis_agent,
                                    all_keywords,
                                    translation_cache,
                                    cache_lock,
                                    keyword_tracker,
                                    store,
                                    (
                                        None
                                        if store
                                        else search_agent.get_previous_processed_version(
                                            paper.paper_id, source
                                        )
                                    ),
                                ): paper
                                for paper in papers
                            }
                            for future in as_completed(futures):
                                try:
                                    result = future.result()
                                    scored_papers.append(result)
                                except Exception as e:
                                    paper = futures[future]
                                    logger.error(f"论文评分异常 ({paper.title[:30]}...): {e}")
                                    if store:
                                        stage = e.stage if isinstance(e, PaperStageError) else "score"
                                        store.update_error(
                                            run_id, source, paper.paper_id, str(e), stage=stage
                                        )
                                    stage_errors.append((source, paper.paper_id, str(e)))
                                pbar.update(1)
                else:
                    with tqdm(
                        total=len(papers), desc=f"📊 [{source}] 评分", unit="篇", ncols=100
                    ) as pbar:
                        for idx, paper in enumerate(papers, 1):
                            pbar.set_description(f"📊 [{source}] [{idx}/{len(papers)}]")
                            pbar.set_postfix_str(f"{paper.title[:35]}...")

                            try:
                                result = _score_or_hydrate_paper(
                                    run_id,
                                    source,
                                    paper,
                                    analysis_agent,
                                    all_keywords,
                                    translation_cache,
                                    cache_lock,
                                    keyword_tracker,
                                    store,
                                    (
                                        None
                                        if store
                                        else search_agent.get_previous_processed_version(
                                            paper.paper_id, source
                                        )
                                    ),
                                )
                            except Exception as e:
                                logger.error(f"论文评分异常 ({paper.title[:30]}...): {e}")
                                if store:
                                    stage = e.stage if isinstance(e, PaperStageError) else "score"
                                    store.update_error(
                                        run_id, source, paper.paper_id, str(e), stage=stage
                                    )
                                stage_errors.append((source, paper.paper_id, str(e)))
                                pbar.update(1)
                                continue

                            scored_papers.append(result)
                            pbar.update(1)

                scored_papers_by_source[source] = scored_papers

                qualified_count = sum(1 for p in scored_papers if p["score_response"].is_qualified)
                logger.info(f"    [{source}] 评分完成: {qualified_count}/{len(papers)} 篇及格")

            if stage_errors:
                details = "; ".join(
                    f"{source}:{paper_id} - {error}" for source, paper_id, error in stage_errors
                )
                raise RuntimeError(f"评分/翻译阶段失败，未生成日报: {details}")

            if translation_cache:
                cache_savings = total_papers_count - len(translation_cache)
                if cache_savings > 0:
                    logger.info(f"  翻译缓存节省了 {cache_savings} 次API调用")

            # ==================== 阶段5: 深度分析及格论文 ====================
            analyses_by_source: Dict[str, List[Dict[str, Any]]] = {}
            analysis_errors = []

            if not settings.DAILY_ENABLE_DEEP_ANALYSIS:
                logger.info(
                    ">>> 阶段5: 深度分析已通过配置关闭，仅保留评分与列表输出"
                )
            else:
                for source, scored_papers in scored_papers_by_source.items():
                    qualified_papers = [p for p in scored_papers if p["score_response"].is_qualified]

                    if not qualified_papers:
                        logger.info(f">>> 阶段5: [{source}] 没有及格论文，跳过深度分析")
                        continue

                    papers_with_pdf = []
                    for p in qualified_papers:
                        paper_meta = p.get("paper_metadata")
                        if paper_meta and paper_meta.has_pdf_access():
                            papers_with_pdf.append(p)

                    if not papers_with_pdf:
                        logger.info(
                            f">>> 阶段5: [{source}] {len(qualified_papers)} 篇及格论文均无PDF可用，跳过深度分析"
                        )
                        continue

                    logger.info(
                        f">>> 阶段5: [{source}] 深度分析 {len(papers_with_pdf)}/{len(qualified_papers)} 篇有PDF的及格论文..."
                    )

                    qualified_papers_with_analysis = []
                    papers_to_analyze = []

                    for paper_info in papers_with_pdf:
                        cached_analysis = None
                        if store:
                            record = store.get_paper_record(source, paper_info["paper_id"])
                            cached_analysis = store.hydrate_analysis(record)

                        if cached_analysis:
                            qualified_papers_with_analysis.append(
                                {
                                    "paper_id": paper_info["paper_id"],
                                    "analysis": cached_analysis,
                                }
                            )
                            logger.debug(f"复用已持久化深度分析: {paper_info['title'][:30]}...")
                        else:
                            papers_to_analyze.append(paper_info)

                    if papers_to_analyze and len(papers_to_analyze) != len(papers_with_pdf):
                        logger.info(
                            f"    [{source}] 复用 {len(papers_with_pdf) - len(papers_to_analyze)} 篇已完成深度分析"
                        )

                    if settings.ENABLE_CONCURRENCY and len(papers_to_analyze) > 1:
                        logger.info(f"    使用并发模式 (workers={settings.CONCURRENCY_WORKERS})")
                        with tqdm(
                            total=len(papers_to_analyze),
                            desc=f"🔬 [{source}] 深度分析",
                            unit="篇",
                            ncols=100,
                        ) as pbar:
                            with ThreadPoolExecutor(
                                max_workers=settings.CONCURRENCY_WORKERS
                            ) as executor:
                                futures = {
                                    executor.submit(
                                        _deep_analyze_single_paper, paper_info, analysis_agent
                                    ): paper_info
                                    for paper_info in papers_to_analyze
                                }
                                for future in as_completed(futures):
                                    paper_info = futures[future]
                                    try:
                                        result = future.result()
                                        if result:
                                            qualified_papers_with_analysis.append(
                                                {
                                                    "paper_id": result["paper_id"],
                                                    "analysis": result["analysis"],
                                                }
                                            )
                                            if store:
                                                store.update_analysis(
                                                    run_id,
                                                    source,
                                                    result["paper_id"],
                                                    result["analysis"],
                                                    analysis_input_fingerprint=(
                                                        paper_info.get("stage_fingerprints", {}).get("analysis")
                                                    ),
                                                )
                                            pm = result.get("paper_meta")
                                            if pm and pm.arxiv_id:
                                                pbar.write(
                                                    f"  ✓ 完成 (via arXiv {pm.arxiv_id}): {result['title'][:50]}..."
                                                )
                                            else:
                                                pbar.write(f"  ✓ 完成: {result['title'][:55]}...")
                                        else:
                                            if store:
                                                store.update_error(
                                                    run_id,
                                                    source,
                                                    paper_info["paper_id"],
                                                    "深度分析未返回结果",
                                                    stage="analysis",
                                                )
                                            analysis_errors.append(
                                                (source, paper_info["paper_id"], "深度分析未返回结果")
                                            )
                                            pbar.write(f"  ✗ 失败: {paper_info['title'][:55]}...")
                                    except Exception as e:
                                        logger.error(
                                            f"深度分析异常 ({paper_info['title'][:30]}...): {e}"
                                        )
                                        if store:
                                            store.update_error(
                                                run_id,
                                                source,
                                                paper_info["paper_id"],
                                                str(e),
                                                stage=(
                                                    e.stage if isinstance(e, PaperStageError) else "analysis"
                                                ),
                                            )
                                        analysis_errors.append(
                                            (source, paper_info["paper_id"], str(e))
                                        )
                                        pbar.write(f"  ✗ 异常: {paper_info['title'][:55]}...")
                                    pbar.update(1)
                    else:
                        with tqdm(
                            total=len(papers_to_analyze),
                            desc=f"🔬 [{source}] 深度分析",
                            unit="篇",
                            ncols=100,
                        ) as pbar:
                            for idx, paper_info in enumerate(papers_to_analyze, 1):
                                pbar.set_description(f"🔬 [{source}] [{idx}/{len(papers_to_analyze)}]")
                                pbar.set_postfix_str(f"{paper_info['title'][:35]}...")

                                try:
                                    result = _deep_analyze_single_paper(paper_info, analysis_agent)
                                    qualified_papers_with_analysis.append(
                                        {"paper_id": result["paper_id"], "analysis": result["analysis"]}
                                    )
                                    if store:
                                        store.update_analysis(
                                            run_id,
                                            source,
                                            result["paper_id"],
                                            result["analysis"],
                                            analysis_input_fingerprint=(
                                                paper_info.get("stage_fingerprints", {}).get("analysis")
                                            ),
                                        )
                                    pm = result.get("paper_meta")
                                    if pm and pm.arxiv_id:
                                        pbar.write(
                                            f"  ✓ 完成 (via arXiv {pm.arxiv_id}): {result['title'][:50]}..."
                                        )
                                    else:
                                        pbar.write(f"  ✓ 完成: {result['title'][:55]}...")
                                except Exception as e:
                                    logger.error(
                                        f"深度分析异常 ({paper_info['title'][:30]}...): {e}"
                                    )
                                    if store:
                                        store.update_error(
                                            run_id,
                                            source,
                                            paper_info["paper_id"],
                                            str(e),
                                            stage=(
                                                e.stage if isinstance(e, PaperStageError) else "analysis"
                                            ),
                                        )
                                    analysis_errors.append((source, paper_info["paper_id"], str(e)))
                                    pbar.write(f"  ✗ 失败: {paper_info['title'][:55]}...")

                                pbar.update(1)

                    analyses_by_source[source] = qualified_papers_with_analysis
                    logger.info(
                        f"    [{source}] 深度分析完成: {len(qualified_papers_with_analysis)}/{len(papers_with_pdf)} 篇成功"
                    )

            if analysis_errors:
                details = "; ".join(
                    f"{source}:{paper_id} - {error}"
                    for source, paper_id, error in analysis_errors
                )
                raise RuntimeError(f"深度分析阶段失败，未生成日报: {details}")

            # ==================== 阶段6: 生成分数据源报告 ====================
            logger.info(">>> 阶段6: 生成分数据源研究报告...")

            reporter = Reporter()
            report_paths = reporter.generate_reports_by_source(
                scored_papers_by_source=scored_papers_by_source,
                keywords_dict=all_keywords,
                analyses_by_source=analyses_by_source,
                token_usage=token_counter.get_summary() if settings.TOKEN_TRACKING_ENABLED else None,
            )
            _validate_report_paths(report_paths, scored_papers_by_source)

            # Commit the critical daily-delivery state before optional keyword
            # trend post-processing.  A later interruption therefore cannot turn
            # a valid report into a second day's "new" paper batch.
            run_result = _build_daily_run_result(
                total_papers_count,
                scored_papers_by_source,
                analyses_by_source,
                report_paths,
            )
            if store and run_id:
                notification_entries = []
                if settings.ENABLE_NOTIFICATIONS:
                    try:
                        notifier = notifier or NotifierAgent()
                        notification_entries = _run_result_notification_entries(notifier, run_result)
                    except Exception as exc:
                        # A malformed/temporarily unavailable notifier must not
                        # reopen already analyzed papers.  A normal provider
                        # delivery failure still has its per-channel outbox row.
                        logger.error("无法建立通知 outbox 条目，日报仍将完成: %s", exc)
                maintenance_entry = after_report_sync_maintenance_entry(run_id)
                store.finalize_report_delivery(
                    run_id,
                    report_paths,
                    _delivered_papers_for_finalization(
                        scored_papers_by_source, analyses_by_source
                    ),
                    notification_entries,
                    [maintenance_entry] if maintenance_entry is not None else [],
                )
                report_delivery_committed = True

                # Report delivery has already committed.  A WebDAV failure now
                # only reschedules its own outbox row and can never make this
                # paper version appear as new on a later day.
                try:
                    sync_summary = deliver_pending_after_report_syncs(store, logger)
                    if sync_summary["claimed"]:
                        logger.info(
                            "报告后 WebDAV 同步: 完成 %s，待补发 %s",
                            sync_summary["completed"],
                            sync_summary["deferred"],
                        )
                except Exception as exc:
                    logger.error("报告后 WebDAV 同步调度异常，已保留待补发状态: %s", exc)
            # ==================== 阶段7: 关键词趋势处理 ====================
            if settings.KEYWORD_TRACKER_ENABLED and settings.KEYWORD_NORMALIZATION_ENABLED:
                logger.info(">>> 阶段7: 运行每日关键词标准化...")
                try:
                    from keyword_tracker import KeywordTracker

                    tracker = keyword_tracker or KeywordTracker()
                    stats = tracker.run_daily_normalization()
                    logger.info(
                        f"  标准化完成: 处理 {stats['processed']} 个, 新增规范词 {stats['new_canonical']}, 合并 {stats['merged']}"
                    )

                    if settings.KEYWORD_REPORT_ENABLED:
                        today = date.today()
                        should_generate_report = False

                        if settings.KEYWORD_REPORT_FREQUENCY == "always":
                            should_generate_report = True
                        elif settings.KEYWORD_REPORT_FREQUENCY == "daily":
                            should_generate_report = True
                        elif settings.KEYWORD_REPORT_FREQUENCY == "weekly":
                            should_generate_report = today.weekday() == 0
                        elif settings.KEYWORD_REPORT_FREQUENCY == "monthly":
                            should_generate_report = today.day == 1

                        if should_generate_report:
                            logger.info("  生成关键词趋势报告...")
                            top_keywords = tracker.get_top_keywords()
                            trends = tracker.get_trends()
                            bar_chart = tracker.generate_bar_chart()
                            trend_chart = tracker.generate_trend_chart()

                            from report.keyword_trend import KeywordTrendReporter
                            kw_reporter = KeywordTrendReporter()
                            trend_paths = kw_reporter.render(
                                top_keywords=top_keywords,
                                trends=trends,
                                bar_chart=bar_chart,
                                trend_chart=trend_chart,
                                today=today,
                                days=tracker.default_days,
                            )
                            logger.info(f"  趋势报告已保存: {trend_paths.get('markdown', '')}")
                        else:
                            logger.info(
                                f"  跳过趋势报告生成 (频率设置: {settings.KEYWORD_REPORT_FREQUENCY})"
                            )

                except Exception as e:
                    logger.warning(f"关键词标准化失败: {e}")

            # ==================== 完成 ====================
            logger.info("=" * 80)
            logger.info("✅ 任务完成！")

            for source, scored_papers in scored_papers_by_source.items():
                logger.info(
                    "  [%s] 抓取: %s | 及格: %s | 深度分析: %s",
                    source,
                    len(scored_papers),
                    run_result.qualified_by_source[source],
                    run_result.analyzed_by_source[source],
                )

            logger.info(
                f"  - 总计: 抓取 {total_papers_count} | 及格 {run_result.total_qualified} | 深度分析 {run_result.total_analyzed}"
            )
            logger.info(f"  - 报告位置: {settings.REPORTS_DIR}")
            logger.info("=" * 80)

            print("\n" + "=" * 80)
            print("🎉 所有任务已完成！")
            print("=" * 80)
            print("📊 统计信息:")

            for source, scored_papers in scored_papers_by_source.items():
                source_qualified = run_result.qualified_by_source.get(source, 0)
                source_analyzed = run_result.analyzed_by_source.get(source, 0)
                pct = (source_qualified / len(scored_papers) * 100) if scored_papers else 0
                print(f"   [{source.upper()}]")
                print(f"     • 抓取: {len(scored_papers)} 篇")
                print(f"     • 及格: {source_qualified} 篇 ({pct:.1f}%)")
                if search_agent.can_download_pdf(source):
                    print(f"     • 深度分析: {source_analyzed} 篇")

            print("\n📁 报告位置:")
            for source, path in report_paths.items():
                print(f"   • [{source}] {path}")
            print("=" * 80 + "\n")

            # ==================== 阶段8: 持久化并发送通知 ====================
            if settings.ENABLE_NOTIFICATIONS:
                logger.info(">>> 阶段8: 写入通知 outbox 并发送...")
                notifier = notifier or NotifierAgent()
                if store and run_id:
                    try:
                        delivery_summary = notifier.deliver_pending_run_results(store)
                        logger.info(
                            "通知派发完成: 发送 %s，待补发 %s",
                            delivery_summary["sent"],
                            delivery_summary["deferred"],
                        )
                    except Exception as exc:
                        logger.error("通知 outbox 派发异常，已保留待补发记录: %s", exc)
                else:
                    # Explicitly preserve the non-persistence mode, where an
                    # outbox cannot survive restarts.
                    try:
                        notifier.notify(run_result)
                    except Exception as exc:
                        # Notification delivery is a follow-up concern. The
                        # report and compatibility history have already been
                        # committed, so a provider outage must not make the
                        # completed paper batch look retryable.
                        logger.warning("通知发送失败，日报状态仍保持已完成: %s", exc)

            return run_result

        except KeyboardInterrupt:
            logger.warning("\n用户中断程序执行")
            print("\n⚠️  程序已被用户中断")
            if store and run_id and not report_delivery_committed:
                store.fail_run(run_id, "用户中断程序执行")
            return RunResult(
                run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                success=False,
                interrupted=True,
                error_message="用户中断程序执行",
            )
        except Exception as e:
            logger.error(f"程序执行出错: {e}", exc_info=True)
            print(f"\n❌ 程序执行失败: {e}")
            print("详细错误信息已记录到日志文件")
            if store and run_id and not report_delivery_committed:
                store.fail_run(run_id, str(e))
            import traceback

            traceback.print_exc()

            if settings.ENABLE_NOTIFICATIONS:
                try:
                    fail_result = RunResult(
                        run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        success=False,
                        error_message=str(e),
                    )
                    NotifierAgent().notify(fail_result)
                except Exception:
                    pass

            raise
        finally:
            # 无论成功、失败还是中断，都把本次运行真实消耗的 token 落库；
            # 统计失败绝不影响主流程。
            if store and run_id:
                try:
                    usage = token_counter.get_summary()
                    by_model = usage.get("by_model") or {}
                    if by_model:
                        store.record_token_usage(
                            run_id, by_model, mode="daily_research"
                        )
                except Exception:
                    logger.debug("Token 用量记录失败", exc_info=True)

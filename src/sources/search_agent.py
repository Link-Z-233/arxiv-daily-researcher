"""
统一搜索调度器

管理多个论文数据源，根据配置调用相应的源进行论文抓取。
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List, Dict, Optional

from .base_source import BasePaperSource, PaperMetadata, normalize_arxiv_identifier
from .arxiv_source import ArxivSource, normalize_arxiv_domains
from .huggingface_papers_source import (
    HUGGINGFACE_PAPERS_SOURCE_NAME,
    HuggingFacePapersSource,
)
from .openalex_source import OpenAlexSource, JOURNAL_ISSN_MAP
from utils.source_registry import (
    merge_source_catalog,
    validate_source_definitions,
)
from .semantic_scholar_enricher import SemanticScholarEnricher

logger = logging.getLogger(__name__)


class SourceScanReceiptError(RuntimeError):
    """Raised when a non-arXiv source cannot persist its terminal scan receipt."""


class SearchAgent:
    """
    统一搜索调度器。

    职责：
    - 管理多个数据源（ArXiv、Crossref 等）
    - 根据配置初始化和调用相应的数据源
    - 返回统一格式的论文列表
    - 支持按数据源分组返回结果
    """

    def __init__(
        self,
        history_dir: Path,
        enabled_sources: List[str] = None,
        arxiv_domains: List[str] = None,
        journals: List[str] = None,
        max_results: Optional[int] = None,
        max_results_per_source: Dict[str, int] = None,
        openalex_api_key: str = None,
        enable_semantic_scholar: bool = True,
        semantic_scholar_api_key: str = None,
        use_legacy_history_filter: bool = True,
        extra_source_definitions: Optional[List[Dict[str, Any]]] = None,
    ):
        """
        初始化搜索调度器。

        参数:
            history_dir: 历史记录存储目录
            enabled_sources: 启用的数据源列表，如
                ["arxiv", "huggingface_papers", "prl", "pra"]。
                只接受内置来源或已知的期刊代码；未知代码属于配置错误。
            arxiv_domains: ArXiv 领域列表，如 ["quant-ph", "cs.AI"]
            journals: 期刊代码列表，如 ["prl", "pra"]
            max_results: 旧配置兼容字段。日报扫描不按数量截断，始终处理
                时间窗口内的全部论文。
            max_results_per_source: 旧配置兼容字段；不作为日报抓取预算。
            openalex_api_key: OpenAlex API Key
            enable_semantic_scholar: 是否启用 Semantic Scholar TLDR
            semantic_scholar_api_key: Semantic Scholar API Key
            use_legacy_history_filter: 是否让旧 JSON history 在抓取阶段
                跳过论文。SQLite 持久化日报应传 False，由精确交付账本
                统一去重，避免旧版过早写入的 history 造成永久漏报。
        """
        self.history_dir = history_dir
        self.history_dir.mkdir(parents=True, exist_ok=True)

        # ``None`` means use the defaults.  Explicitly empty lists must retain
        # their meaning so validation can fail closed instead of silently
        # querying a different scope (or no scope at all).
        self.enabled_sources = enabled_sources if enabled_sources is not None else ["arxiv"]
        self.arxiv_domains = arxiv_domains
        self.journals = journals or []
        # Kept in the public constructor so old integrations do not break.
        # Daily source scans intentionally ignore both values and exhaust the
        # configured time window instead of treating them as an item budget.
        self.max_results = max_results
        self.max_results_per_source = max_results_per_source or {}
        self.openalex_api_key = openalex_api_key
        self.use_legacy_history_filter = bool(use_legacy_history_filter)
        self.extra_source_definitions = validate_source_definitions(extra_source_definitions or [])
        self.journal_catalog = merge_source_catalog(JOURNAL_ISSN_MAP, self.extra_source_definitions)

        # 初始化 Semantic Scholar 增强器
        self.enable_semantic_scholar = enable_semantic_scholar
        self.semantic_scholar_enricher = None
        if enable_semantic_scholar:
            # 空字符串视为 None，使用公共 API（无需 API Key）
            api_key = semantic_scholar_api_key if semantic_scholar_api_key else None
            self.semantic_scholar_enricher = SemanticScholarEnricher(api_key=api_key)
            if api_key:
                logger.info("[SearchAgent] 已启用 Semantic Scholar TLDR 增强（使用 API Key）")
            else:
                logger.info(
                    "[SearchAgent] 已启用 Semantic Scholar TLDR 增强（公共 API，共享匿名额度；繁忙时可能限流）"
                )

        # 初始化数据源
        self.sources: Dict[str, BasePaperSource] = {}
        # A report's source label is not always the same as the object that
        # fetched it: OpenAlex emits one label per configured journal. Keep
        # this mapping explicit so a standalone source is never accidentally
        # written into OpenAlex history or denied PDF analysis.
        self._source_backends: Dict[str, str] = {}
        self._init_sources()
        self._configure_history_filtering()

    def _configure_history_filtering(self) -> None:
        """Keep JSON history as a compatibility cache when SQLite is authoritative."""
        for source in self.sources.values():
            source.set_history_filtering_enabled(self.use_legacy_history_filter)
        if not self.use_legacy_history_filter:
            logger.info(
                "[SearchAgent] SQLite 交付账本为权威状态；"
                "抓取阶段不以旧 JSON history 跳过论文"
            )

    def _init_sources(self):
        """根据配置初始化数据源"""
        from config import settings as _settings

        # Configuration must be fail-closed.  Silently ignoring a typo (for
        # example ``cs.AI`` accidentally placed in data_sources.enabled) makes
        # a daily report look complete while an intended source was never
        # queried.  Normalize once so UI/manual JSON configuration behaves the
        # same way as the OpenAlex source itself.
        if isinstance(self.enabled_sources, (str, bytes)):
            raise ValueError("数据源配置必须是非空列表，不能是单个字符串")

        normalized_sources = []
        seen_sources = set()
        for configured_source in self.enabled_sources:
            if not isinstance(configured_source, str) or not configured_source.strip():
                raise ValueError(
                    f"数据源代码必须是非空字符串: {configured_source!r}"
                )
            source = configured_source.strip().lower()
            if (
                source not in {"arxiv", HUGGINGFACE_PAPERS_SOURCE_NAME}
                and source not in self.journal_catalog
            ):
                raise ValueError(
                    f"未知数据源代码: {configured_source}。"
                    "请使用 arxiv、huggingface_papers 或已支持的 OpenAlex 期刊代码。"
                )
            if source not in seen_sources:
                normalized_sources.append(source)
                seen_sources.add(source)
        self.enabled_sources = normalized_sources

        if not self.enabled_sources:
            raise ValueError("至少启用一个论文数据源，不能生成空日报")

        normalized_journals = []
        seen_journals = set()
        for configured_journal in self.journals:
            if not isinstance(configured_journal, str) or not configured_journal.strip():
                raise ValueError(
                    f"期刊代码必须是非空字符串: {configured_journal!r}"
                )
            journal = configured_journal.strip().lower()
            if journal not in self.journal_catalog:
                raise ValueError(
                    f"未知 OpenAlex 期刊代码: {configured_journal}。"
                    "请使用内置支持的期刊代码。"
                )
            if journal not in seen_journals:
                normalized_journals.append(journal)
                seen_journals.add(journal)
        self.journals = normalized_journals

        # 检查是否启用 ArXiv
        if "arxiv" in self.enabled_sources:
            self.arxiv_domains = normalize_arxiv_domains(self.arxiv_domains)
            arxiv_proxy = _settings.get_proxy_dict("arxiv")
            arxiv_kwargs = {
                "history_dir": self.history_dir,
                "proxy_dict": arxiv_proxy,
                "announcement_lookback_grace_days": getattr(
                    _settings, "ARXIV_ANNOUNCEMENT_LOOKBACK_GRACE_DAYS", 2
                ),
            }
            if not self.use_legacy_history_filter:
                arxiv_kwargs["load_legacy_history"] = False
            self.sources["arxiv"] = ArxivSource(
                **arxiv_kwargs,
            )
            self._source_backends["arxiv"] = "arxiv"
            logger.info("[SearchAgent] 已启用 ArXiv 数据源")

        # Hugging Face Papers is a selected supplementary feed, not an arXiv
        # replacement.  It remains opt-in in config, but gets the same
        # fail-closed fetch and independent compatibility history semantics.
        if HUGGINGFACE_PAPERS_SOURCE_NAME in self.enabled_sources:
            hf_kwargs = {
                "history_dir": self.history_dir,
                "availability_lag_days": getattr(
                    _settings, "HUGGINGFACE_PAPERS_AVAILABILITY_LAG_DAYS", 2
                ),
                "lookback_grace_days": getattr(
                    _settings, "HUGGINGFACE_PAPERS_LOOKBACK_GRACE_DAYS", 2
                ),
                "request_timeout_seconds": getattr(
                    _settings, "HUGGINGFACE_PAPERS_REQUEST_TIMEOUT_SECONDS", 30
                ),
                "request_interval_seconds": getattr(
                    _settings, "HUGGINGFACE_PAPERS_REQUEST_INTERVAL_SECONDS", 0.25
                ),
            }
            if not self.use_legacy_history_filter:
                hf_kwargs["load_legacy_history"] = False
            hf_source = HuggingFacePapersSource(
                **hf_kwargs,
            )
            hf_proxy = _settings.get_proxy_dict(HUGGINGFACE_PAPERS_SOURCE_NAME)
            if hf_proxy:
                hf_source.session.proxies.update(hf_proxy)
                logger.info("[SearchAgent] Hugging Face Papers 已配置网络代理")
            self.sources[HUGGINGFACE_PAPERS_SOURCE_NAME] = hf_source
            self._source_backends[HUGGINGFACE_PAPERS_SOURCE_NAME] = (
                HUGGINGFACE_PAPERS_SOURCE_NAME
            )
            logger.info(
                "[SearchAgent] 已启用 Hugging Face Papers 补充数据源（非 arXiv 全量源）"
            )

        # 检查是否启用期刊（通过 OpenAlex）
        # 期刊代码可以直接作为 enabled_sources 的一部分
        journal_codes = []
        for source in self.enabled_sources:
            if source not in {"arxiv", HUGGINGFACE_PAPERS_SOURCE_NAME}:
                journal_codes.append(source)

        # 也支持通过 journals 参数指定
        for journal in self.journals:
            if journal not in journal_codes:
                journal_codes.append(journal)

        if journal_codes:
            openalex_kwargs = {
                "history_dir": self.history_dir,
                "journals": journal_codes,
                "journal_catalog": self.journal_catalog,
                "api_key": self.openalex_api_key,
            }
            if not self.use_legacy_history_filter:
                openalex_kwargs["load_legacy_history"] = False
            self.sources["openalex"] = OpenAlexSource(**openalex_kwargs)
            # 注入代理
            openalex_proxy = _settings.get_proxy_dict("openalex")
            if openalex_proxy:
                self.sources["openalex"].session.proxies.update(openalex_proxy)
                logger.info("[SearchAgent] OpenAlex 已配置网络代理")
            self._journal_codes = journal_codes
            for journal_code in journal_codes:
                self._source_backends[journal_code] = "openalex"
            logger.info(f"[SearchAgent] 已启用 OpenAlex 数据源，期刊: {journal_codes}")
        else:
            self._journal_codes = []

        # 注入代理到 Semantic Scholar
        if self.semantic_scholar_enricher:
            s2_proxy = _settings.get_proxy_dict("semantic_scholar")
            if s2_proxy:
                self.semantic_scholar_enricher.session.proxies.update(s2_proxy)
                logger.info("[SearchAgent] Semantic Scholar 已配置网络代理")

    @staticmethod
    def _source_scan_receipt(
        source: str, status: str, candidate_count: Optional[int] = None
    ) -> Dict[str, Any]:
        """Build a minimal terminal receipt for sources without arXiv's detail.

        ArXiv owns a richer per-domain/per-query receipt because it exposes
        two independent queries.  Hugging Face Papers and OpenAlex still need
        one durable source-level terminal record before their shared daily
        checkpoint can advance.  Do not put exception text, URLs, request
        parameters, or credentials into this payload.
        """
        payload: Dict[str, Any] = {
            "source": source,
            "status": status,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "receipt_kind": "source_summary_v1",
            "domain_receipts": [],
        }
        if candidate_count is not None:
            payload["total_new_candidates"] = max(0, int(candidate_count))
        return payload

    @classmethod
    def _emit_source_scan_receipt(
        cls,
        callback: Optional[Callable[[Dict[str, Any]], None]],
        source: str,
        status: str,
        candidate_count: Optional[int] = None,
    ) -> None:
        """Persist a terminal source receipt or fail closed before reporting."""
        if callback is None:
            return
        try:
            callback(cls._source_scan_receipt(source, status, candidate_count))
        except Exception as exc:
            raise SourceScanReceiptError(
                f"无法持久化 {source} 扫描收据"
            ) from exc

    def fetch_all_papers(
        self,
        days: int = 7,
        scan_receipt_callbacks: Optional[Dict[str, Callable[[Dict[str, Any]], None]]] = None,
    ) -> Dict[str, List[PaperMetadata]]:
        """
        从所有启用的数据源抓取论文。

        参数:
            days: 搜索最近 N 天的论文
            scan_receipt_callbacks: 可选的报告来源→回调映射。ArXiv 写入
                完整的领域级收据；Hugging Face Papers 和 OpenAlex 期刊写入
                最小的来源级终态收据。任一收据持久化失败都会中止本次扫描。

        返回:
            Dict[str, List[PaperMetadata]]: {数据源名: 论文列表}
            例如: {"arxiv": [...], "prl": [...], "pra": [...]}
        """
        results = {}
        receipt_callbacks = scan_receipt_callbacks or {}

        for source_name, source in self.sources.items():
            logger.info(f">>> 从 {source.display_name} 抓取论文...")
            receipt_sources = (
                list(getattr(self, "_journal_codes", []))
                if source_name == "openalex"
                else [source_name]
            )

            try:
                if source_name == "arxiv":
                    papers = source.fetch_papers(
                        days=days,
                        domains=self.arxiv_domains,
                        scan_receipt_callback=receipt_callbacks.get("arxiv"),
                    )
                    results["arxiv"] = papers

                elif source_name == "openalex":
                    # OpenAlex 返回的论文按期刊分组
                    papers = source.fetch_papers(days=days)
                    # 增强：获取 Semantic Scholar TLDR
                    if self.enable_semantic_scholar and self.semantic_scholar_enricher:
                        papers = self._enrich_with_semantic_scholar(papers)
                    # 按 source 字段分组（期刊代码）
                    for paper in papers:
                        if paper.source not in results:
                            results[paper.source] = []
                        results[paper.source].append(paper)
                    # OpenAlex queries all configured journals in one backend
                    # call.  Persist a terminal receipt for each report source
                    # (including a legitimate zero-result journal) before the
                    # shared daily watermark is allowed to advance.
                    for journal_code in receipt_sources:
                        self._emit_source_scan_receipt(
                            receipt_callbacks.get(journal_code),
                            journal_code,
                            "succeeded",
                            len(results.get(journal_code, [])),
                        )

                else:
                    papers = source.fetch_papers(days=days)
                    results[source_name] = papers
                    self._emit_source_scan_receipt(
                        receipt_callbacks.get(source_name),
                        source_name,
                        "succeeded",
                        len(papers),
                    )

            except Exception:
                # 抓取错误不能被转换为空列表，否则上层会生成看似成功但实际
                # 漏论文的日报。除 arXiv 外的来源由这里写失败终态收据；
                # arXiv 已在其两个查询/领域循环内写入更细的失败收据。
                if source_name != "arxiv":
                    for report_source in receipt_sources:
                        self._emit_source_scan_receipt(
                            receipt_callbacks.get(report_source),
                            report_source,
                            "failed",
                        )
                logger.exception(f"[{source_name}] 抓取失败")
                raise

        # 统计
        total = sum(len(papers) for papers in results.values())
        logger.info(f">>> 总计抓取 {total} 篇论文，来自 {len(results)} 个数据源")

        return results

    def _enrich_with_semantic_scholar(self, papers: List[PaperMetadata]) -> List[PaperMetadata]:
        """
        使用 Semantic Scholar 增强论文元数据（添加 TLDR 和 arXiv 信息）。

        参数:
            papers: 论文列表

        返回:
            List[PaperMetadata]: 增强后的论文列表
        """
        if not self.semantic_scholar_enricher:
            return papers

        logger.info("  正在从 Semantic Scholar 获取增强信息...")
        enriched_count = 0
        arxiv_found_count = 0

        for paper in papers:
            if paper.doi:
                # 获取完整的论文信息（TLDR + arXiv ID）
                paper_info = self.semantic_scholar_enricher.get_paper_info(paper.doi)
                if paper_info:
                    # 设置 TLDR
                    if paper_info.get("tldr"):
                        paper.semantic_scholar_tldr = paper_info["tldr"]
                        enriched_count += 1

                    # 设置 arXiv 信息（用于后续深度分析）
                    arxiv_id = normalize_arxiv_identifier(paper_info.get("arxiv_id"))
                    if arxiv_id:
                        paper.arxiv_id = arxiv_id
                        paper.arxiv_url = paper_info.get(
                            "arxiv_url", f"https://arxiv.org/abs/{arxiv_id}"
                        )
                        # 设置 PDF URL 以便下载
                        paper.pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
                        arxiv_found_count += 1
                        logger.debug(f"    找到 arXiv 版本: {arxiv_id}")
                    elif paper_info.get("arxiv_id") is not None:
                        logger.warning("    Semantic Scholar 返回无效 arXiv ID，已忽略 PDF 增强")

        if enriched_count > 0 or arxiv_found_count > 0:
            logger.info(f"    TLDR: {enriched_count}/{len(papers)} 篇")
            logger.info(f"    arXiv版本: {arxiv_found_count}/{len(papers)} 篇")
        else:
            logger.info("    未获取到增强信息")

        return papers

    def mark_as_processed(self, paper_id: str, source: str):
        """
        标记论文为已处理。

        参数:
            paper_id: 论文 ID
            source: 数据源名称（arxiv 或期刊代码）
        """
        backend_name = self._source_backends.get(source)
        source_obj = self.sources.get(backend_name) if backend_name else None
        if source_obj is None:
            raise ValueError(f"无法标记未知或未启用的数据源论文: {source}:{paper_id}")
        source_obj.mark_as_processed(paper_id)

    def mark_many_as_processed(self, paper_ids_by_source: Dict[str, List[str]]) -> None:
        """Flush compatibility histories in per-backend atomic batches."""
        paper_ids_by_backend: Dict[str, List[str]] = {}
        for source, paper_ids in paper_ids_by_source.items():
            backend_name = self._source_backends.get(source)
            if backend_name is None or backend_name not in self.sources:
                raise ValueError(f"无法批量标记未知或未启用的数据源: {source}")
            paper_ids_by_backend.setdefault(backend_name, []).extend(paper_ids)

        for backend_name, paper_ids in paper_ids_by_backend.items():
            if paper_ids:
                self.sources[backend_name].mark_many_as_processed(paper_ids)

    def get_previous_processed_version(self, paper_id: str, source: str):
        """Return legacy-history information for the previous arXiv version."""
        source_obj = self.get_source(source)
        if source_obj is None:
            return None
        return source_obj.get_previous_processed_version(paper_id)

    def get_source(self, source_name: str) -> Optional[BasePaperSource]:
        """获取指定的数据源实例"""
        backend_name = self._source_backends.get(source_name)
        if backend_name is None:
            return None
        # 期刊通过 openalex
        return self.sources.get(backend_name)

    def can_download_pdf(self, source: str) -> bool:
        """检查指定数据源是否支持 PDF 下载"""
        source_obj = self.get_source(source)
        return bool(source_obj and source_obj.can_download_pdf())

    def get_enabled_sources(self) -> List[str]:
        """获取所有启用的数据源名称"""
        sources = []
        if "arxiv" in self.sources:
            sources.append("arxiv")
        if HUGGINGFACE_PAPERS_SOURCE_NAME in self.sources:
            sources.append(HUGGINGFACE_PAPERS_SOURCE_NAME)
        if "openalex" in self.sources:
            # 添加具体的期刊代码
            sources.extend(self._journal_codes)
        return sources

    @staticmethod
    def get_available_journals() -> Dict[str, Dict]:
        """获取所有可用的期刊列表"""
        return JOURNAL_ISSN_MAP

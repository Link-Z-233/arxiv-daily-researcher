"""
统一搜索调度器

管理多个论文数据源，根据配置调用相应的源进行论文抓取。
"""

import logging
from pathlib import Path
from typing import List, Dict, Optional

from .base_source import BasePaperSource, PaperMetadata
from .arxiv_source import ArxivSource
from .openalex_source import OpenAlexSource, JOURNAL_ISSN_MAP
from .semantic_scholar_enricher import SemanticScholarEnricher

logger = logging.getLogger(__name__)


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
        max_results: int = 100,
        max_results_per_source: Dict[str, int] = None,
        openalex_email: str = None,
        openalex_api_key: str = None,
        enable_semantic_scholar: bool = True,
        semantic_scholar_api_key: str = None,
    ):
        """
        初始化搜索调度器。

        参数:
            history_dir: 历史记录存储目录
            enabled_sources: 启用的数据源列表，如 ["arxiv", "prl", "pra"]。
                只接受 ``arxiv`` 或已知的期刊代码；未知代码属于配置错误。
            arxiv_domains: ArXiv 领域列表，如 ["quant-ph", "cs.AI"]
            journals: 期刊代码列表，如 ["prl", "pra"]
            max_results: 旧配置兼容字段。日报扫描不按数量截断，始终处理
                时间窗口内的全部论文。
            max_results_per_source: 旧配置兼容字段；不作为日报抓取预算。
            openalex_email: OpenAlex 礼貌池邮箱
            openalex_api_key: OpenAlex API Key
            enable_semantic_scholar: 是否启用 Semantic Scholar TLDR
            semantic_scholar_api_key: Semantic Scholar API Key
        """
        self.history_dir = history_dir
        self.history_dir.mkdir(parents=True, exist_ok=True)

        self.enabled_sources = enabled_sources or ["arxiv"]
        self.arxiv_domains = arxiv_domains or []
        self.journals = journals or []
        self.max_results = max_results
        self.max_results_per_source = max_results_per_source or {}
        self.openalex_email = openalex_email
        self.openalex_api_key = openalex_api_key

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
                    "[SearchAgent] 已启用 Semantic Scholar TLDR 增强（公共 API，限速 100次/5分钟）"
                )

        # 初始化数据源
        self.sources: Dict[str, BasePaperSource] = {}
        self._init_sources()

    def _get_max_results(self, source: str) -> int:
        """获取指定数据源的最大结果数，优先使用单独配置，否则回退到全局默认值。"""
        return self.max_results_per_source.get(source, self.max_results)

    def _init_sources(self):
        """根据配置初始化数据源"""
        from config import settings as _settings

        # Configuration must be fail-closed.  Silently ignoring a typo (for
        # example ``cs.AI`` accidentally placed in data_sources.enabled) makes
        # a daily report look complete while an intended source was never
        # queried.  Normalize once so UI/manual JSON configuration behaves the
        # same way as the OpenAlex source itself.
        normalized_sources = []
        seen_sources = set()
        for configured_source in self.enabled_sources:
            if not isinstance(configured_source, str) or not configured_source.strip():
                raise ValueError(
                    f"数据源代码必须是非空字符串: {configured_source!r}"
                )
            source = configured_source.strip().lower()
            if source != "arxiv" and source not in JOURNAL_ISSN_MAP:
                raise ValueError(
                    f"未知数据源代码: {configured_source}。"
                    "请使用 arxiv 或已支持的 OpenAlex 期刊代码。"
                )
            if source not in seen_sources:
                normalized_sources.append(source)
                seen_sources.add(source)
        self.enabled_sources = normalized_sources

        normalized_journals = []
        seen_journals = set()
        for configured_journal in self.journals:
            if not isinstance(configured_journal, str) or not configured_journal.strip():
                raise ValueError(
                    f"期刊代码必须是非空字符串: {configured_journal!r}"
                )
            journal = configured_journal.strip().lower()
            if journal not in JOURNAL_ISSN_MAP:
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
            arxiv_proxy = _settings.get_proxy_dict("arxiv")
            self.sources["arxiv"] = ArxivSource(
                history_dir=self.history_dir,
                max_results=self._get_max_results("arxiv"),
                proxy_dict=arxiv_proxy,
            )
            logger.info("[SearchAgent] 已启用 ArXiv 数据源")

        # 检查是否启用期刊（通过 OpenAlex）
        # 期刊代码可以直接作为 enabled_sources 的一部分
        journal_codes = []
        for source in self.enabled_sources:
            if source != "arxiv":
                journal_codes.append(source)

        # 也支持通过 journals 参数指定
        for journal in self.journals:
            if journal not in journal_codes:
                journal_codes.append(journal)

        if journal_codes:
            # ``max_results`` only exists for backward configuration
            # compatibility.  OpenAlexSource deliberately does not use it as
            # a daily-scan cap; retaining this value avoids breaking callers
            # that instantiate it directly.
            openalex_max = max(
                (self._get_max_results(jc) for jc in journal_codes),
                default=self.max_results,
            )
            self.sources["openalex"] = OpenAlexSource(
                history_dir=self.history_dir,
                journals=journal_codes,
                max_results=openalex_max,
                email=self.openalex_email,
                api_key=self.openalex_api_key,
            )
            # 注入代理
            openalex_proxy = _settings.get_proxy_dict("openalex")
            if openalex_proxy:
                self.sources["openalex"].session.proxies.update(openalex_proxy)
                logger.info("[SearchAgent] OpenAlex 已配置网络代理")
            self._journal_codes = journal_codes
            logger.info(f"[SearchAgent] 已启用 OpenAlex 数据源，期刊: {journal_codes}")
        else:
            self._journal_codes = []

        # 注入代理到 Semantic Scholar
        if self.semantic_scholar_enricher:
            s2_proxy = _settings.get_proxy_dict("semantic_scholar")
            if s2_proxy:
                self.semantic_scholar_enricher.session.proxies.update(s2_proxy)
                logger.info("[SearchAgent] Semantic Scholar 已配置网络代理")

    def fetch_all_papers(self, days: int = 7) -> Dict[str, List[PaperMetadata]]:
        """
        从所有启用的数据源抓取论文。

        参数:
            days: 搜索最近 N 天的论文

        返回:
            Dict[str, List[PaperMetadata]]: {数据源名: 论文列表}
            例如: {"arxiv": [...], "prl": [...], "pra": [...]}
        """
        results = {}

        for source_name, source in self.sources.items():
            logger.info(f">>> 从 {source.display_name} 抓取论文...")

            try:
                if source_name == "arxiv":
                    papers = source.fetch_papers(days=days, domains=self.arxiv_domains)
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

                else:
                    papers = source.fetch_papers(days=days)
                    results[source_name] = papers

            except Exception:
                # 抓取错误不能被转换为空列表，否则上层会生成看似成功但实际
                # 漏论文的日报。让调用方决定是否重试/终止本次运行。
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
                    if paper_info.get("arxiv_id"):
                        paper.arxiv_id = paper_info["arxiv_id"]
                        paper.arxiv_url = paper_info.get(
                            "arxiv_url", f"https://arxiv.org/abs/{paper_info['arxiv_id']}"
                        )
                        # 设置 PDF URL 以便下载
                        paper.pdf_url = f"https://arxiv.org/pdf/{paper_info['arxiv_id']}.pdf"
                        arxiv_found_count += 1
                        logger.debug(f"    找到 arXiv 版本: {paper_info['arxiv_id']}")

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
        # ArXiv 论文
        if source == "arxiv" and "arxiv" in self.sources:
            self.sources["arxiv"].mark_as_processed(paper_id)
        # 期刊论文（都通过 openalex）
        elif "openalex" in self.sources:
            self.sources["openalex"].mark_as_processed(paper_id)

    def mark_many_as_processed(self, paper_ids_by_source: Dict[str, List[str]]) -> None:
        """Flush compatibility histories in per-backend atomic batches."""
        arxiv_ids = paper_ids_by_source.get("arxiv", [])
        if arxiv_ids and "arxiv" in self.sources:
            self.sources["arxiv"].mark_many_as_processed(arxiv_ids)

        openalex_ids = []
        for source, paper_ids in paper_ids_by_source.items():
            if source != "arxiv":
                openalex_ids.extend(paper_ids)
        if openalex_ids and "openalex" in self.sources:
            self.sources["openalex"].mark_many_as_processed(openalex_ids)

    def get_previous_processed_version(self, paper_id: str, source: str):
        """Return legacy-history information for the previous arXiv version."""
        source_obj = self.get_source(source)
        if source_obj is None:
            return None
        return source_obj.get_previous_processed_version(paper_id)

    def get_source(self, source_name: str) -> Optional[BasePaperSource]:
        """获取指定的数据源实例"""
        if source_name == "arxiv":
            return self.sources.get("arxiv")
        # 期刊通过 openalex
        return self.sources.get("openalex")

    def can_download_pdf(self, source: str) -> bool:
        """检查指定数据源是否支持 PDF 下载"""
        if source == "arxiv":
            return True
        return False  # 期刊默认不支持

    def get_enabled_sources(self) -> List[str]:
        """获取所有启用的数据源名称"""
        sources = []
        if "arxiv" in self.sources:
            sources.append("arxiv")
        if "openalex" in self.sources:
            # 添加具体的期刊代码
            sources.extend(self._journal_codes)
        return sources

    @staticmethod
    def get_available_journals() -> Dict[str, Dict]:
        """获取所有可用的期刊列表"""
        return JOURNAL_ISSN_MAP

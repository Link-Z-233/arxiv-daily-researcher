"""
论文数据源模块

提供多种论文数据源的统一接口：
- ArxivSource: ArXiv预印本数据源（支持PDF下载）
- HuggingFacePapersSource: Hugging Face Papers 可选精选补充源（支持 arXiv PDF）
- OpenAlexSource: OpenAlex期刊数据源（元数据 + 摘要）
- SemanticScholarEnricher: Semantic Scholar数据增强器（TLDR + arXiv链接）
- SearchAgent: 多源论文抓取编排
"""

from .base_source import BasePaperSource, PaperMetadata
from .arxiv_source import ArxivSource, ArxivFetchError
from .huggingface_papers_source import HuggingFacePapersFetchError, HuggingFacePapersSource
from .openalex_source import OpenAlexSource
from .semantic_scholar_enricher import SemanticScholarEnricher
from .search_agent import SearchAgent

__all__ = [
    "BasePaperSource",
    "PaperMetadata",
    "ArxivSource",
    "ArxivFetchError",
    "HuggingFacePapersSource",
    "HuggingFacePapersFetchError",
    "OpenAlexSource",
    "SemanticScholarEnricher",
    "SearchAgent",
]

"""
论文数据源抽象基类

定义所有论文数据源必须实现的统一接口。
"""

import json
import logging
import os
import re
import threading
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)

_ARXIV_VERSION_RE = re.compile(r"^(?P<canonical>.+?)(?:v(?P<version>[0-9]+))$")
# URL-producing callers need stricter validation than the generic identity
# splitter above, which intentionally keeps old database rows readable.
_VALID_ARXIV_IDENTIFIER_RE = re.compile(
    r"^(?P<canonical>(?:\d{4}\.\d{4,5}|[A-Za-z][A-Za-z0-9.-]*/\d{7}))"
    r"(?:v(?P<version>[1-9]\d*))?$"
)


class HistoryLoadError(RuntimeError):
    """Raised when a legacy history cache cannot safely be used for de-duplication."""


def split_arxiv_version(paper_id: str) -> tuple[str, Optional[int]]:
    """Return an arXiv canonical identifier and its explicit version."""
    value = str(paper_id or "").strip()
    match = _ARXIV_VERSION_RE.match(value)
    if not match:
        return value, None
    return match.group("canonical"), int(match.group("version"))


def normalize_arxiv_identifier(value: object) -> Optional[str]:
    """Return a safe, canonical arXiv identifier or ``None``.

    Third-party metadata services occasionally expose malformed ``ArXiv``
    external IDs.  Such values must not be interpolated into arxiv.org URLs
    or passed to the PDF-analysis path.  This accepts current and legacy IDs,
    but not URL fragments, prefixes, or arbitrary version-like strings.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    match = _VALID_ARXIV_IDENTIFIER_RE.fullmatch(candidate)
    if match is None:
        return None
    canonical = match.group("canonical")
    version = match.group("version")
    return f"{canonical}v{version}" if version is not None else canonical


def paper_identity(source: str, paper_id: str) -> tuple[str, Optional[int]]:
    """Return the stable identity tuple used by history and persistence."""
    if source == "arxiv":
        return split_arxiv_version(paper_id)
    return str(paper_id or ""), None


def history_key(source: str, paper_id: str) -> str:
    """Encode a source/canonical/version pair for legacy JSON history."""
    canonical, version = paper_identity(source, paper_id)
    if source == "arxiv" and version is not None:
        return f"{canonical}@v{version}"
    return canonical


@dataclass
class PaperMetadata:
    """
    统一的论文元数据格式。

    所有数据源返回的论文都使用这个统一格式，
    便于后续的评分、分析和报告生成。
    """

    paper_id: str  # 唯一标识符（ArXiv ID 或 DOI）
    title: str  # 论文标题
    authors: List[str]  # 作者列表
    abstract: str  # 摘要
    published_date: datetime  # 发布日期
    url: str  # 论文页面URL
    source: str  # 数据源标识（如 "arxiv", "prl", "pra"）
    pdf_url: Optional[str] = None  # PDF下载链接（如果可用）
    doi: Optional[str] = None  # DOI
    journal: Optional[str] = None  # 期刊名称
    categories: List[str] = field(default_factory=list)  # 分类/领域
    semantic_scholar_tldr: Optional[str] = None  # Semantic Scholar AI生成的TLDR
    arxiv_id: Optional[str] = None  # arXiv ID（期刊论文可能也有arXiv版本）
    arxiv_url: Optional[str] = None  # arXiv论文页面URL
    canonical_id: Optional[str] = None  # 稳定的论文标识（arXiv 去除 vN 后的 ID）
    version: Optional[int] = None  # arXiv 版本号，如 1、2
    updated_date: Optional[datetime] = None  # arXiv 最后更新时间

    def __post_init__(self):
        if self.source == "arxiv":
            parsed_canonical, parsed_version = split_arxiv_version(self.paper_id)
            self.canonical_id = self.canonical_id or parsed_canonical
            if self.version is None:
                self.version = parsed_version
        else:
            self.canonical_id = self.canonical_id or self.paper_id

    @property
    def identity(self) -> tuple[str, Optional[int]]:
        """Stable canonical/version identity for this paper."""
        return self.canonical_id or self.paper_id, self.version

    @property
    def version_label(self) -> str:
        return f"v{self.version}" if self.version is not None else ""

    def has_pdf_access(self) -> bool:
        """是否可以下载PDF进行深度分析"""
        # 优先使用原始PDF链接，否则使用arXiv PDF
        return (self.pdf_url is not None and self.pdf_url != "") or bool(
            normalize_arxiv_identifier(self.arxiv_id)
        )

    def get_arxiv_pdf_url(self) -> Optional[str]:
        """获取arXiv PDF下载链接"""
        arxiv_id = normalize_arxiv_identifier(self.arxiv_id)
        if arxiv_id:
            return f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        return None

    def get_best_pdf_url(self) -> Optional[str]:
        """获取最佳的PDF下载链接（优先原始PDF，否则arXiv）"""
        if self.pdf_url:
            return self.pdf_url
        return self.get_arxiv_pdf_url()

    def get_authors_string(self) -> str:
        """获取作者字符串（逗号分隔）"""
        return ", ".join(self.authors)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract,
            "published_date": self.published_date.isoformat() if self.published_date else None,
            "url": self.url,
            "source": self.source,
            "pdf_url": self.pdf_url,
            "doi": self.doi,
            "journal": self.journal,
            "categories": self.categories,
            "semantic_scholar_tldr": self.semantic_scholar_tldr,
            "arxiv_id": self.arxiv_id,
            "arxiv_url": self.arxiv_url,
            "canonical_id": self.canonical_id,
            "version": self.version,
            "updated_date": self.updated_date.isoformat() if self.updated_date else None,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "PaperMetadata":
        """Restore one internally persisted metadata record fail-closed.

        The daily queue is durable across process restarts, so records selected
        on a later day must be reconstructed from SQLite rather than from the
        current API response.  Reject malformed rows instead of silently
        dropping them and advancing a successful scan watermark.
        """
        if not isinstance(payload, dict):
            raise ValueError("paper metadata must be an object")

        def required_text(name: str, *, allow_empty: bool = False) -> str:
            value = payload.get(name)
            if not isinstance(value, str) or (not allow_empty and not value.strip()):
                raise ValueError(f"paper metadata field {name} must be a string")
            return value if allow_empty else value.strip()

        def optional_text(name: str) -> Optional[str]:
            value = payload.get(name)
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValueError(f"paper metadata field {name} must be a string or null")
            return value

        def string_list(name: str) -> List[str]:
            value = payload.get(name, [])
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError(f"paper metadata field {name} must be a string list")
            return list(value)

        def parsed_datetime(name: str, *, required: bool = False) -> Optional[datetime]:
            value = payload.get(name)
            if value is None and not required:
                return None
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"paper metadata field {name} must be an ISO timestamp")
            try:
                return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(
                    f"paper metadata field {name} must be an ISO timestamp"
                ) from exc

        version = payload.get("version")
        if version is not None:
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ValueError("paper metadata field version must be a positive integer or null")

        return cls(
            paper_id=required_text("paper_id"),
            title=required_text("title"),
            authors=string_list("authors"),
            abstract=required_text("abstract", allow_empty=True),
            published_date=parsed_datetime("published_date", required=True),
            url=required_text("url"),
            source=required_text("source"),
            pdf_url=optional_text("pdf_url"),
            doi=optional_text("doi"),
            journal=optional_text("journal"),
            categories=string_list("categories"),
            semantic_scholar_tldr=optional_text("semantic_scholar_tldr"),
            arxiv_id=optional_text("arxiv_id"),
            arxiv_url=optional_text("arxiv_url"),
            canonical_id=optional_text("canonical_id"),
            version=version,
            updated_date=parsed_datetime("updated_date"),
        )


class BasePaperSource(ABC):
    """
    论文数据源抽象基类。

    所有具体的数据源（ArXiv、Crossref等）都必须继承此类并实现抽象方法。

    职责：
    - 定义统一的论文抓取接口
    - 管理历史记录（避免重复处理）
    - 提供数据源元信息
    """

    def __init__(
        self,
        source_name: str,
        history_dir: Path,
        *,
        load_legacy_history: bool = True,
    ):
        """
        初始化数据源。

        参数:
            source_name: 数据源名称（如 "arxiv", "crossref"）
            history_dir: 历史记录存储目录
        """
        self.source_name = source_name
        self.history_dir = history_dir
        self.history_file = history_dir / f"{source_name}_history.json"
        self.history: Dict[str, str] = {}
        self._history_lock = threading.Lock()
        # JSON history is authoritative only in the explicit compatibility
        # mode without the SQLite delivery ledger.  The SearchAgent disables
        # this filter for normal persistent daily runs, where an old history
        # entry must never hide a paper that was not durably delivered.
        self.history_filtering_enabled = bool(load_legacy_history)
        self._history_load_error: Optional[str] = None
        if load_legacy_history:
            self._load_history()

    @abstractmethod
    def fetch_papers(self, days: int, **kwargs) -> List[PaperMetadata]:
        """
        抓取论文的抽象方法。

        参数:
            days: 搜索最近N天的论文
            **kwargs: 数据源特定的参数

        返回:
            List[PaperMetadata]: 统一格式的论文列表
        """
        pass

    @abstractmethod
    def can_download_pdf(self) -> bool:
        """
        该数据源是否支持PDF下载。

        返回:
            bool: True表示支持PDF下载，可以进行深度分析
        """
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """数据源的显示名称（用于报告）"""
        pass

    def is_processed(self, paper_id: str) -> bool:
        """检查论文是否已处理过（线程安全）"""
        with self._history_lock:
            if not self.history_filtering_enabled:
                return False
            if self._history_load_error:
                raise HistoryLoadError(
                    f"[{self.source_name}] 兼容历史不可用，拒绝以空历史继续去重: "
                    f"{self._history_load_error}"
                )
            return history_key(self.source_name, paper_id) in self.history

    def set_history_filtering_enabled(self, enabled: bool) -> None:
        """Choose whether legacy JSON history may suppress fetched papers.

        A persistent daily run uses the SQLite exact-version delivery ledger
        after fetching.  Disabling this compatibility filter means stale or
        corrupt JSON history cannot hide an unfinished old paper; the ledger
        remains responsible for preventing duplicate report delivery.
        """
        with self._history_lock:
            self.history_filtering_enabled = bool(enabled)

    def mark_as_processed(self, paper_id: str):
        """标记论文为已处理（线程安全）"""
        self.mark_many_as_processed([paper_id])

    def mark_many_as_processed(self, paper_ids: List[str]):
        """Atomically add a batch of successfully delivered paper versions.

        Keeping all related IDs in one history file replacement avoids a partial
        write where an interruption marks only some papers from a complete report.
        """
        if not paper_ids:
            return
        with self._history_lock:
            if self._history_load_error:
                # Do not overwrite forensic evidence of a damaged compatibility
                # cache with a partial fresh file.  SQLite-backed callers catch
                # this after their durable delivery transaction; compatibility
                # callers fail closed before they can duplicate a report.
                raise HistoryLoadError(
                    f"[{self.source_name}] 无法安全更新损坏的兼容历史: "
                    f"{self._history_load_error}"
                )
            previous_history = self.history.copy()
            processed_at = datetime.now().isoformat()
            for paper_id in paper_ids:
                self.history[history_key(self.source_name, paper_id)] = processed_at
            try:
                self._save_history()
            except Exception:
                self.history = previous_history
                raise

    def get_previous_processed_version(self, paper_id: str) -> Optional[Dict[str, Any]]:
        """Return the latest processed arXiv version before ``paper_id``."""
        if self.source_name != "arxiv":
            return None
        canonical, version = split_arxiv_version(paper_id)
        if version is None:
            return None
        candidates = []
        with self._history_lock:
            for key, processed_at in self.history.items():
                if key.startswith(f"{canonical}@v"):
                    try:
                        previous_version = int(key.rsplit("@v", 1)[1])
                    except ValueError:
                        continue
                    if previous_version < version:
                        candidates.append((previous_version, processed_at))
        if not candidates:
            return None
        previous_version, processed_at = max(candidates, key=lambda item: item[0])
        return {"version": previous_version, "processed_at": processed_at}

    def _load_history(self):
        """Load the compatibility history without silently trusting an empty fallback."""
        self._history_load_error = None
        if self.history_file.exists():
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    raw_history = json.load(f)
                    if not isinstance(raw_history, dict):
                        raise ValueError("历史文件根节点必须是对象")
                    normalized_history = {}
                    for paper_id, processed_at in raw_history.items():
                        if not isinstance(paper_id, str) or not paper_id.strip():
                            raise ValueError("历史文件包含无效论文标识")
                        if not isinstance(processed_at, str) or not processed_at.strip():
                            raise ValueError("历史文件包含无效处理时间")
                        normalized_history[
                            history_key(self.source_name, paper_id)
                        ] = processed_at
                    self.history = normalized_history
                logger.debug(f"[{self.source_name}] 加载历史记录: {len(self.history)} 条")
            except Exception as e:
                self._history_load_error = str(e)
                logger.error(
                    "[%s] 兼容历史加载失败；SQLite 持久化模式会改由交付账本去重，"
                    "关闭持久化时将拒绝继续运行: %s",
                    self.source_name,
                    e,
                )
                self.history = {}
        else:
            self.history = {}

    def _save_history(self):
        """Atomically save the compatibility history, propagating write failures."""
        temporary_path = None
        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.history_dir,
                prefix=f".{self.history_file.name}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                temporary_path = Path(f.name)
                json.dump(self.history, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary_path, self.history_file)
        except Exception:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("[%s] 无法清理历史临时文件: %s", self.source_name, temporary_path)
            raise

    def get_history_count(self) -> int:
        """获取历史记录数量"""
        return len(self.history)

    def clear_history(self):
        """清空历史记录（线程安全）"""
        with self._history_lock:
            self.history = {}
            try:
                self._save_history()
            except Exception:
                # Keep the in-memory cache aligned with the on-disk source of
                # truth when an attempted clear cannot be committed.
                self._load_history()
                raise
            self._history_load_error = None
        logger.info(f"[{self.source_name}] 历史记录已清空")

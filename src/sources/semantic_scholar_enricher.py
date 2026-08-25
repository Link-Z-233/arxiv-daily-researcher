"""
Semantic Scholar 数据增强器

通过 Semantic Scholar API 获取 AI 生成的 TLDR 和其他增强信息。
"""

import logging
import threading
import time
import requests
from typing import Optional, Dict
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log

from .base_source import normalize_arxiv_identifier

logger = logging.getLogger(__name__)


class SemanticScholarEnricher:
    """
    Semantic Scholar 数据增强器。

    功能：
    - 根据 DOI 获取论文的 AI 生成 TLDR
    - 获取其他补充信息（引用数、影响力评分等）
    """

    API_BASE_URL = "https://api.semanticscholar.org/graph/v1"
    # Semantic Scholar documents an introductory API-key allowance of one
    # request per second.  Keep a small, courteous pace for anonymous traffic
    # too: its quota is shared by all anonymous users and can be throttled
    # during busy periods.
    AUTHENTICATED_MIN_REQUEST_INTERVAL_SECONDS = 1.0
    ANONYMOUS_MIN_REQUEST_INTERVAL_SECONDS = 0.1

    def __init__(self, api_key: Optional[str] = None):
        """
        初始化 Semantic Scholar 增强器。

        参数:
            api_key: Semantic Scholar API Key（可选，提高速率限制）
        """
        self.api_key = api_key
        self.session = requests.Session()
        self._request_slot_lock = threading.Lock()
        self._next_request_at = 0.0
        self.request_interval_seconds = (
            self.AUTHENTICATED_MIN_REQUEST_INTERVAL_SECONDS
            if api_key
            else self.ANONYMOUS_MIN_REQUEST_INTERVAL_SECONDS
        )
        self.session.headers.update({
            "User-Agent": "ArxivDailyResearcher/2.0 (https://github.com/yzr278892/arxiv-daily-researcher; yzr278892@gmail.com)"
        })

        if api_key:
            self.session.headers.update({
                "x-api-key": api_key
            })

    def __enter__(self):
        """支持上下文管理器"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出时关闭Session"""
        self.close()

    def close(self):
        """关闭网络连接"""
        if self.session:
            self.session.close()
            logger.debug("SemanticScholar Session已关闭")

    def _wait_for_request_slot(self) -> None:
        """Serialize provider calls at the configured minimum interval.

        A single ``SearchAgent`` normally enriches papers serially, but the
        lock also makes the provider guarantee hold if a future caller uses an
        enricher from multiple threads.  Reserve the slot before issuing the
        request so retries follow the same rate policy.
        """
        with self._request_slot_lock:
            now = time.monotonic()
            wait_seconds = self._next_request_at - now
            if wait_seconds > 0:
                time.sleep(wait_seconds)
                now = time.monotonic()
            self._next_request_at = now + self.request_interval_seconds

    def _api_get(self, url: str, params: dict, timeout: int = 10) -> requests.Response:
        """发送 Semantic Scholar API GET 请求，带自动重试（跳过 404/429）。"""
        from config import settings as _settings

        @retry(
            stop=stop_after_attempt(_settings.RETRY_MAX_ATTEMPTS),
            wait=wait_exponential(min=_settings.RETRY_MIN_WAIT, max=_settings.RETRY_MAX_WAIT),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        def _do_get():
            self._wait_for_request_slot()
            resp = self.session.get(url, params=params, timeout=timeout)
            # 404 和 429 不重试，直接返回
            if resp.status_code in (404, 429):
                return resp
            resp.raise_for_status()
            return resp

        return _do_get()

    @staticmethod
    def _clean_doi(doi: object) -> Optional[str]:
        """Return a minimally safe DOI lookup key, or no key at all."""
        if not isinstance(doi, str):
            return None
        clean_doi = doi.replace("https://doi.org/", "").replace("DOI:", "").strip()
        if not clean_doi or any(ord(character) < 0x20 for character in clean_doi):
            return None
        return clean_doi

    @staticmethod
    def _external_arxiv_id(external_ids: object) -> Optional[str]:
        """Read only a valid arXiv ID from an untrusted provider response."""
        if not isinstance(external_ids, dict):
            return None
        return normalize_arxiv_identifier(external_ids.get("ArXiv"))

    def get_tldr(self, doi: str) -> Optional[str]:
        """
        获取论文的 AI 生成 TLDR。

        参数:
            doi: 论文的 DOI

        返回:
            Optional[str]: TLDR 文本，失败时返回 None
        """
        try:
            # 清理 DOI（移除可能的前缀）
            clean_doi = self._clean_doi(doi)
            if not clean_doi:
                logger.debug("Semantic Scholar 跳过无效 DOI TLDR 查询")
                return None

            # 构建请求 URL
            url = f"{self.API_BASE_URL}/paper/DOI:{clean_doi}"
            params = {
                "fields": "tldr"
            }

            response = self._api_get(url, params)

            # 如果找不到论文，静默返回 None
            if response.status_code == 404:
                logger.warning(f"⚠️  Semantic Scholar 未收录论文: DOI {clean_doi[:30]}... (可能因论文太新或未被索引)")
                return None

            if response.status_code == 429:
                logger.warning(f"⚠️  Semantic Scholar API 限速 (429)，建议申请免费 API Key")
                return None

            response.raise_for_status()
            data = response.json()

            # 提取 TLDR
            tldr_obj = data.get("tldr")
            if tldr_obj and isinstance(tldr_obj, dict):
                tldr_text = tldr_obj.get("text", "")
                if isinstance(tldr_text, str) and tldr_text.strip():
                    tldr_text = tldr_text.strip()
                    logger.debug(f"✅ 成功获取 TLDR: {clean_doi[:30]}...")
                    return tldr_text
                else:
                    logger.debug(f"ℹ️  论文无 AI TLDR: {clean_doi[:30]}...")
            else:
                logger.debug(f"ℹ️  论文无 AI TLDR: {clean_doi[:30]}...")

            return None

        except requests.exceptions.Timeout:
            logger.warning(f"⚠️  Semantic Scholar API 超时: {clean_doi[:30]}...")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"⚠️  Semantic Scholar API 请求失败: {e}")
            return None
        except Exception as e:
            logger.warning(f"⚠️  获取 TLDR 异常: {e}")
            return None

    def get_paper_info(self, doi: str) -> Optional[Dict]:
        """
        获取论文的完整信息（包括 TLDR、引用数、arXiv链接等）。

        参数:
            doi: 论文的 DOI

        返回:
            Optional[Dict]: 包含各种信息的字典，失败时返回 None
        """
        try:
            clean_doi = self._clean_doi(doi)
            if not clean_doi:
                logger.debug("Semantic Scholar 跳过无效 DOI 元数据查询")
                return None

            url = f"{self.API_BASE_URL}/paper/DOI:{clean_doi}"
            params = {
                "fields": "tldr,citationCount,influentialCitationCount,publicationTypes,externalIds"
            }

            response = self._api_get(url, params)

            if response.status_code == 404:
                return None

            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                logger.warning("Semantic Scholar 返回的论文元数据不是对象，已忽略")
                return None

            result = {}

            # 提取 TLDR
            tldr_obj = data.get("tldr")
            if tldr_obj and isinstance(tldr_obj, dict):
                tldr_text = tldr_obj.get("text")
                if isinstance(tldr_text, str) and tldr_text.strip():
                    result["tldr"] = tldr_text.strip()

            # 提取引用数
            if "citationCount" in data:
                result["citation_count"] = data["citationCount"]

            if "influentialCitationCount" in data:
                result["influential_citation_count"] = data["influentialCitationCount"]

            if "publicationTypes" in data:
                result["publication_types"] = data["publicationTypes"]

            # 提取 arXiv ID（关键新增功能）
            arxiv_id = self._external_arxiv_id(data.get("externalIds"))
            if arxiv_id:
                result["arxiv_id"] = arxiv_id
                result["arxiv_url"] = f"https://arxiv.org/abs/{arxiv_id}"
                logger.debug(f"找到 arXiv 版本: {arxiv_id}")
            elif isinstance(data.get("externalIds"), dict) and "ArXiv" in data["externalIds"]:
                logger.warning("Semantic Scholar 返回了无效 arXiv ID，已忽略该可选增强")

            return result if result else None

        except Exception as e:
            logger.warning(f"获取 Semantic Scholar 信息失败: {e}")
            return None

    def get_arxiv_id(self, doi: str) -> Optional[str]:
        """
        专门获取论文的 arXiv ID。

        参数:
            doi: 论文的 DOI

        返回:
            Optional[str]: arXiv ID，如 "2401.12345"，失败时返回 None
        """
        try:
            clean_doi = self._clean_doi(doi)
            if not clean_doi:
                return None

            url = f"{self.API_BASE_URL}/paper/DOI:{clean_doi}"
            params = {
                "fields": "externalIds"
            }

            response = self._api_get(url, params)

            if response.status_code == 404:
                return None

            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                return None

            return self._external_arxiv_id(data.get("externalIds"))

        except Exception as e:
            logger.debug(f"获取 arXiv ID 失败: {e}")
            return None

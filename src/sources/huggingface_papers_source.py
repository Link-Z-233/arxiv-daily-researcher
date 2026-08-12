"""Hugging Face Papers supplementary paper source.

Hugging Face's daily-paper endpoint is useful for discovering papers that its
community has surfaced, but it is *not* a replacement for arXiv category
searches.  This source is therefore optional and deliberately disabled by
default.  It exhausts every configured daily feed page and fails closed when
the endpoint cannot prove that a non-empty page has been fully paginated.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import requests
from requests.utils import parse_header_links
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from .base_source import BasePaperSource, PaperMetadata

logger = logging.getLogger(__name__)


HUGGINGFACE_PAPERS_SOURCE_NAME = "huggingface_papers"


class HuggingFacePapersFetchError(RuntimeError):
    """Raised when the configured HF daily feeds cannot be fetched completely."""


class HuggingFacePapersSource(BasePaperSource):
    """Fetch all papers from Hugging Face's dated daily-paper feeds.

    The endpoint does not expose a general arXiv search and represents a
    selected/displayed feed.  ``availability_lag_days`` avoids treating a
    not-yet-finalised current-day feed as an empty successful source.  The
    additional lookback is safe because delivery history removes overlaps.
    """

    API_BASE_URL = "https://huggingface.co"
    API_PATH = "/api/daily_papers"
    DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
    DEFAULT_REQUEST_INTERVAL_SECONDS = 0.25

    # Modern arXiv identifiers are what the endpoint currently returns.  Keep
    # legacy identifiers valid too, since old papers may appear in a curated
    # daily feed and must not be silently discarded.
    _ARXIV_ID_RE = re.compile(
        r"^(?P<canonical>(?:\d{4}\.\d{4,5}|[A-Za-z0-9.-]+/\d{7}))(?:v[1-9]\d*)?$"
    )

    def __init__(
        self,
        history_dir: Path,
        availability_lag_days: int = 2,
        lookback_grace_days: int = 2,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        request_interval_seconds: float = DEFAULT_REQUEST_INTERVAL_SECONDS,
    ):
        super().__init__(HUGGINGFACE_PAPERS_SOURCE_NAME, history_dir)
        self.availability_lag_days = max(0, int(availability_lag_days))
        self.lookback_grace_days = max(0, int(lookback_grace_days))
        self.request_timeout_seconds = max(1, int(request_timeout_seconds))
        self.request_interval_seconds = max(0.0, float(request_interval_seconds))
        self._last_request_started_at: Optional[float] = None

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "ArxivDailyResearcher/2.0 "
                    "(https://github.com/yzr278892/arxiv-daily-researcher)"
                ),
                "Accept": "application/json",
            }
        )

    @property
    def display_name(self) -> str:
        return "Hugging Face Papers"

    def can_download_pdf(self) -> bool:
        # Every accepted entry is required to expose an arXiv ID, from which a
        # stable PDF URL is constructed.
        return True

    def close(self) -> None:
        """Close the underlying HTTP session when the caller owns its lifetime."""
        if self.session:
            self.session.close()

    def _wait_for_request_slot(self) -> None:
        """Pace paging requests without imposing a result-count budget."""
        if self.request_interval_seconds <= 0 or self._last_request_started_at is None:
            return
        remaining = self.request_interval_seconds - (
            time.monotonic() - self._last_request_started_at
        )
        if remaining > 0:
            time.sleep(remaining)

    def _api_request(
        self, url: str, params: Optional[Dict[str, str]] = None
    ) -> requests.Response:
        """Issue one page request with project-wide retry/backoff settings."""
        from config import settings as _settings

        @retry(
            stop=stop_after_attempt(max(1, int(_settings.RETRY_MAX_ATTEMPTS))),
            wait=wait_exponential(
                min=max(0, int(_settings.RETRY_MIN_WAIT)),
                max=max(0, int(_settings.RETRY_MAX_WAIT)),
            ),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        def _do_request() -> requests.Response:
            self._wait_for_request_slot()
            self._last_request_started_at = time.monotonic()
            response = self.session.get(
                url,
                params=params,
                timeout=self.request_timeout_seconds,
            )
            response.raise_for_status()
            return response

        return _do_request()

    def _fetch_page(
        self, url: str, params: Optional[Dict[str, str]], feed_date: date, page: int
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Fetch and validate one API page before processing any of its rows."""
        try:
            response = self._api_request(url, params)
        except HuggingFacePapersFetchError:
            raise
        except Exception as exc:
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 第 {page} 页请求失败: {exc}"
            ) from exc

        try:
            payload = response.json()
        except Exception as exc:
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 第 {page} 页不是有效 JSON: {exc}"
            ) from exc
        if not isinstance(payload, list):
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 第 {page} 页响应不是列表"
            )
        if not all(isinstance(item, dict) for item in payload):
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 第 {page} 页含有非对象条目"
            )

        headers = getattr(response, "headers", None)
        if headers is None or not hasattr(headers, "get"):
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 第 {page} 页缺少响应头"
            )
        link_header = headers.get("Link", "")
        if link_header is None:
            link_header = ""
        if not isinstance(link_header, str):
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 第 {page} 页 Link 响应头无效"
            )
        return payload, link_header

    @classmethod
    def _parse_arxiv_id(cls, value: Any, feed_date: date, item_index: int) -> Tuple[str, str]:
        if not isinstance(value, str) or not value.strip():
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 条目 {item_index} 缺少 arXiv ID"
            )
        arxiv_id = value.strip()
        match = cls._ARXIV_ID_RE.fullmatch(arxiv_id)
        if match is None:
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 条目 {item_index} 的 arXiv ID 无效: "
                f"{arxiv_id!r}"
            )
        return arxiv_id, match.group("canonical")

    @staticmethod
    def _parse_timestamp(value: Any, fallback_date: date, field: str) -> datetime:
        """Parse an endpoint timestamp, with the queried feed date as fallback."""
        if value is None or value == "":
            return datetime.combine(fallback_date, datetime_time.min, tzinfo=timezone.utc)
        if not isinstance(value, str):
            raise ValueError(f"{field} 不是字符串")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} 时间格式无效: {value!r}") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _parse_authors(value: Any, feed_date: date, item_index: int) -> List[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 条目 {item_index} 的 authors 不是列表"
            )
        authors = []
        for author_index, author in enumerate(value):
            if isinstance(author, str):
                name = author.strip()
            elif isinstance(author, dict):
                raw_name = author.get("name")
                name = raw_name.strip() if isinstance(raw_name, str) else ""
            else:
                name = ""
            if not name:
                raise HuggingFacePapersFetchError(
                    "Hugging Face Papers "
                    f"{feed_date.isoformat()} 条目 {item_index} 的作者 {author_index} 无有效名称"
                )
            authors.append(name)
        return authors

    def _metadata_from_entry(
        self, entry: Dict[str, Any], feed_date: date, item_index: int
    ) -> PaperMetadata:
        paper = entry.get("paper")
        if not isinstance(paper, dict):
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 条目 {item_index} 缺少 paper 对象"
            )

        arxiv_id, _canonical_id = self._parse_arxiv_id(
            paper.get("id"), feed_date, item_index
        )
        raw_title = paper.get("title") or entry.get("title")
        if not isinstance(raw_title, str) or not raw_title.strip():
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 条目 {item_index} 缺少标题"
            )

        raw_abstract = paper.get("summary")
        if raw_abstract is None:
            raw_abstract = entry.get("summary", "")
        if raw_abstract is None:
            raw_abstract = ""
        if not isinstance(raw_abstract, str):
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 条目 {item_index} 的摘要不是字符串"
            )

        try:
            published_date = self._parse_timestamp(
                paper.get("publishedAt") or entry.get("publishedAt"),
                feed_date,
                "publishedAt",
            )
        except ValueError as exc:
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 条目 {item_index} 的日期无效: {exc}"
            ) from exc

        arxiv_url = f"https://arxiv.org/abs/{arxiv_id}"
        return PaperMetadata(
            # Keep this source's delivery identity independent from arXiv.
            # Cross-source mirror suppression uses ``arxiv_id`` explicitly,
            # so it never makes an arXiv v2/v3 look already delivered.
            paper_id=f"hf:{arxiv_id}",
            title=raw_title.strip(),
            authors=self._parse_authors(paper.get("authors"), feed_date, item_index),
            abstract=raw_abstract.strip(),
            published_date=published_date,
            url=arxiv_url,
            source=HUGGINGFACE_PAPERS_SOURCE_NAME,
            pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
            arxiv_id=arxiv_id,
            arxiv_url=arxiv_url,
        )

    def _validated_next_page(
        self, link_header: str, feed_date: date, current_page: int
    ) -> Tuple[str, int]:
        """Return one safe, strictly forward continuation from a Link header."""
        if not link_header.strip():
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 第 {current_page} 页非空但未提供 next 分页链接"
            )
        try:
            links = parse_header_links(link_header)
        except Exception as exc:
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 第 {current_page} 页 Link 解析失败: {exc}"
            ) from exc
        next_urls = [
            item.get("url")
            for item in links
            if "next" in str(item.get("rel", "")).lower().split()
        ]
        if len(next_urls) != 1 or not isinstance(next_urls[0], str):
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 第 {current_page} 页没有唯一的 next 分页链接"
            )

        next_url = next_urls[0]
        try:
            parsed = urlsplit(next_url)
            if (
                parsed.scheme.lower() != "https"
                or parsed.hostname != "huggingface.co"
                or parsed.port not in (None, 443)
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path != self.API_PATH
                or parsed.fragment
            ):
                raise ValueError("目标不属于 Hugging Face daily_papers HTTPS API")
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
            if set(query) != {"date", "p"} or query.get("date") != [feed_date.isoformat()]:
                raise ValueError("date 参数不匹配")
            page_values = query.get("p", [])
            if len(page_values) != 1 or not page_values[0].isdigit():
                raise ValueError("p 参数无效")
            next_page = int(page_values[0])
        except (TypeError, ValueError) as exc:
            raise HuggingFacePapersFetchError(
                f"Hugging Face Papers {feed_date.isoformat()} 第 {current_page} 页 next 分页链接无效: {exc}"
            ) from exc
        return next_url, next_page

    def _entries_for_feed_date(self, feed_date: date) -> Iterable[Dict[str, Any]]:
        """Yield all rows for one dated feed, never treating a truncation as EOF."""
        url = f"{self.API_BASE_URL}{self.API_PATH}"
        params: Optional[Dict[str, str]] = {"date": feed_date.isoformat()}
        current_page = 0
        visited_pages = {current_page}

        while True:
            entries, link_header = self._fetch_page(url, params, feed_date, current_page)
            if not entries:
                return
            yield from entries

            next_url, next_page = self._validated_next_page(
                link_header, feed_date, current_page
            )
            if next_page in visited_pages:
                # The forward-page validation above catches normal loops, but
                # retain an explicit guard for a malformed or cyclic Link
                # chain returned by an upstream intermediary.
                raise HuggingFacePapersFetchError(
                    f"Hugging Face Papers {feed_date.isoformat()} 分页循环"
                )
            # This is an offset-page endpoint, not a cursor API. Requiring the
            # exact successor prevents a malformed upstream/proxy Link header
            # from skipping an unseen page while still looking valid.
            if next_page != current_page + 1:
                raise HuggingFacePapersFetchError(
                    f"Hugging Face Papers {feed_date.isoformat()} 分页页码不是连续的下一页"
                )
            visited_pages.add(next_page)
            url = next_url
            params = None
            current_page = next_page

    def _feed_dates(self, days: int, now: Optional[datetime] = None) -> List[date]:
        normal_days = max(1, int(days))
        now_utc = now or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        else:
            now_utc = now_utc.astimezone(timezone.utc)
        newest_available = now_utc.date() - timedelta(days=self.availability_lag_days)
        total_days = normal_days + self.lookback_grace_days
        return [newest_available - timedelta(days=offset) for offset in range(total_days)]

    def fetch_papers(
        self, days: int, now: Optional[datetime] = None, **_kwargs: Any
    ) -> List[PaperMetadata]:
        """Fetch all unprocessed HF daily-paper entries in the safe date window."""
        feed_dates = self._feed_dates(days, now=now)
        logger.info("[Hugging Face Papers] 开始抓取补充论文流")
        logger.info(
            "  日榜日期: %s 至 %s（可用性滞后 %s 天，额外回看 %s 天）",
            feed_dates[-1].isoformat(),
            feed_dates[0].isoformat(),
            self.availability_lag_days,
            self.lookback_grace_days,
        )
        logger.info("  抓取策略: 每个日期完整跟随分页至空页（不受结果数量限制）")

        papers: List[PaperMetadata] = []
        seen_paper_ids = set()
        for feed_date in feed_dates:
            try:
                entries = self._entries_for_feed_date(feed_date)
                count_before = len(papers)
                for item_index, entry in enumerate(entries, start=1):
                    paper = self._metadata_from_entry(entry, feed_date, item_index)
                    if paper.paper_id in seen_paper_ids:
                        continue
                    seen_paper_ids.add(paper.paper_id)
                    if self.is_processed(paper.paper_id):
                        continue
                    papers.append(paper)
                logger.info(
                    "  %s: 发现 %s 篇未处理论文",
                    feed_date.isoformat(),
                    len(papers) - count_before,
                )
            except HuggingFacePapersFetchError:
                # A partial date range must never produce a seemingly complete
                # daily report.  Let the pipeline retain/retry the whole run.
                raise
            except Exception as exc:
                raise HuggingFacePapersFetchError(
                    f"Hugging Face Papers {feed_date.isoformat()} 抓取未完成: {exc}"
                ) from exc

        logger.info("[Hugging Face Papers] 总计发现 %s 篇新论文", len(papers))
        return papers

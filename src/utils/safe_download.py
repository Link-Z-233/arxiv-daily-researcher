"""Bounded, redirect-safe downloads for untrusted paper-related URLs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urljoin

from utils.safe_url import safe_external_http_url


class ExternalDownloadError(ValueError):
    """Raised before untrusted remote content reaches a parser or service."""


_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


def _header_value(response: Any, name: str) -> str:
    headers = getattr(response, "headers", None)
    if headers is None or not hasattr(headers, "get"):
        return ""
    value = headers.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _bounded_response_bytes(
    response: Any,
    *,
    max_bytes: int,
    required_magic: bytes | None,
    magic_search_bytes: int,
) -> bytes:
    content_length = _header_value(response, "Content-Length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise ExternalDownloadError("响应 Content-Length 无效") from exc
        if declared_size < 0:
            raise ExternalDownloadError("响应 Content-Length 不能为负数")
        if declared_size > max_bytes:
            raise ExternalDownloadError(
                f"响应大小 {declared_size} 字节超过允许上限 {max_bytes} 字节"
            )

    iterator = getattr(response, "iter_content", None)
    if not callable(iterator):
        raise ExternalDownloadError("下载响应不支持流式读取")

    chunks: list[bytes] = []
    prefix = bytearray()
    total_size = 0
    prefix_limit = max(1, int(magic_search_bytes))
    for chunk in iterator(chunk_size=64 * 1024):
        if not chunk:
            continue
        if not isinstance(chunk, (bytes, bytearray)):
            raise ExternalDownloadError("下载响应包含非字节内容")
        total_size += len(chunk)
        if total_size > max_bytes:
            raise ExternalDownloadError(f"下载内容超过允许上限 {max_bytes} 字节")
        if len(prefix) < prefix_limit:
            prefix.extend(chunk[: prefix_limit - len(prefix)])
        chunks.append(bytes(chunk))

    content = b"".join(chunks)
    if not content:
        raise ExternalDownloadError("下载内容为空")
    # A signature can straddle two transport chunks.  Inspect the complete
    # bounded prefix only after streaming finishes instead of rejecting after
    # the first chunk that happens not to contain it.
    if required_magic and required_magic not in prefix:
        raise ExternalDownloadError("下载内容不是预期的文件格式")
    return content


def download_external_bytes(
    url: object,
    request: Callable[..., Any],
    *,
    max_bytes: int,
    request_kwargs: Mapping[str, Any] | None = None,
    required_magic: bytes | None = None,
    magic_search_bytes: int = 1024,
    max_redirects: int = 5,
) -> bytes:
    """Fetch a bounded public HTTP(S) resource while validating every hop.

    ``requests`` follows redirects automatically by default.  That is unsafe
    for a worker receiving URL fields from metadata APIs because an apparently
    public first hop may redirect to a local address.  This helper follows a
    small number of redirects manually, then streams the final body with both
    declared-size and actual-byte limits.  It intentionally does not inspect
    DNS, so proxy-backed deployments continue to work; literal local IP forms
    are rejected by :func:`utils.safe_url.safe_external_http_url`.
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    current_url = safe_external_http_url(url)
    if not current_url:
        raise ExternalDownloadError("URL 不是允许的外部 HTTP(S) 地址")

    kwargs = dict(request_kwargs or {})
    redirect_limit = max(0, int(max_redirects))
    for redirect_count in range(redirect_limit + 1):
        response = request(current_url, stream=True, allow_redirects=False, **kwargs)
        try:
            status_code = getattr(response, "status_code", None)
            if status_code in _REDIRECT_STATUS_CODES:
                location = _header_value(response, "Location")
                if not location:
                    raise ExternalDownloadError("重定向响应缺少 Location")
                next_url = safe_external_http_url(urljoin(current_url, location))
                if not next_url:
                    raise ExternalDownloadError("重定向目标不是允许的外部 HTTP(S) 地址")
                if redirect_count >= redirect_limit:
                    raise ExternalDownloadError("重定向次数超过允许上限")
                current_url = next_url
                continue

            raise_for_status = getattr(response, "raise_for_status", None)
            if not callable(raise_for_status):
                raise ExternalDownloadError("下载响应缺少 HTTP 状态校验")
            raise_for_status()
            return _bounded_response_bytes(
                response,
                max_bytes=max_bytes,
                required_magic=required_magic,
                magic_search_bytes=magic_search_bytes,
            )
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    # The loop either returns or raises.  Keep an explicit guard so future
    # refactors cannot turn an exhausted redirect path into a silent None.
    raise ExternalDownloadError("无法完成外部下载")

"""Durable endpoint capability cache for OpenAI-compatible LLM gateways.

Most OpenAI-compatible relays expose ``/chat/completions`` but do not expose
the newer ``/responses`` endpoint.  The official SDK still provides a
``client.responses`` attribute, so feature detection has to happen from the
provider's actual HTTP response rather than from attribute presence.

The cache deliberately stores only a normalized base URL and a short,
redacted diagnostic.  API keys, model prompts and response bodies never leave
the request path.  Persisting a confirmed unsupported endpoint avoids paying
for a failing probe again after the worker/container is restarted.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from config import settings

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "llm_endpoint_capabilities.json"
_ENDPOINTS = frozenset({"chat_completions", "responses"})
_CACHE_LOCK = threading.Lock()
_CACHE: Optional[dict[str, dict[str, dict[str, str]]]] = None


def normalize_base_url(base_url: str) -> str:
    """Return a stable, credential-free cache key for a configured base URL."""
    value = str(base_url or "").strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme and parsed.netloc:
            # User info is never meaningful for an OpenAI client base URL and
            # must not leak into the durable cache if it was configured by
            # mistake. Query/fragment are similarly irrelevant to endpoints.
            hostname = parsed.hostname or ""
            netloc = hostname.lower()
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            return urlunsplit(
                (
                    parsed.scheme.lower(),
                    netloc,
                    parsed.path.rstrip("/"),
                    "",
                    "",
                )
            )
    except ValueError:
        pass
    return value.rstrip("/").lower()


def _cache_path() -> Path:
    return Path(settings.DATA_DIR) / _CACHE_FILENAME


def _sanitize_reason(value: Any) -> str:
    """Keep a compact provider diagnostic without persisting credentials."""
    text = str(value or "").strip()
    text = re.sub(
        r"(?i)(api[_-]?key|authorization|token|password)\\s*([:=])\\s*[^,;\\s]+",
        r"\\1\\2***",
        text,
    )
    text = re.sub(r"(?i)sk-[A-Za-z0-9_-]+", "sk-***", text)
    text = re.sub(r"(https?://)[^/@\\s]+@", r"\\1***@", text)
    return text[:320]


def _load_cache_locked() -> dict[str, dict[str, dict[str, str]]]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    loaded: dict[str, dict[str, dict[str, str]]] = {}
    path = _cache_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("LLM 端点能力缓存无法读取，将重新检测: %s", exc)
        raw = None

    if isinstance(raw, dict):
        for base_url, endpoints in raw.items():
            if not isinstance(base_url, str) or not isinstance(endpoints, dict):
                continue
            normalized: dict[str, dict[str, str]] = {}
            for endpoint, record in endpoints.items():
                if endpoint not in _ENDPOINTS or not isinstance(record, dict):
                    continue
                state = record.get("state")
                if state not in {"supported", "unsupported"}:
                    continue
                normalized[endpoint] = {
                    "state": state,
                    "observed_at": str(record.get("observed_at") or ""),
                    "reason": _sanitize_reason(record.get("reason")),
                }
            if normalized:
                loaded[base_url] = normalized
    _CACHE = loaded
    return _CACHE


def _write_cache_locked() -> None:
    if _CACHE is None:
        return
    # A successful request is useful for this process's diagnostic log, but
    # it does not need a durable record: normal calls already prefer Chat
    # Completions and never need a positive cache to avoid work. Persist only
    # confirmed unsupported endpoints, which are the observations that avoid
    # a paid/slow failing fallback after a restart.
    persisted = {
        base_url: {
            endpoint: record
            for endpoint, record in endpoints.items()
            if record.get("state") == "unsupported"
        }
        for base_url, endpoints in _CACHE.items()
    }
    persisted = {base_url: endpoints for base_url, endpoints in persisted.items() if endpoints}
    path = _cache_path()
    if not persisted:
        # Successful observations stay in memory only. Do not create an empty
        # data file for a provider that has never rejected an endpoint. A
        # later successful probe also clears a stale unsupported record.
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return
    temporary_path: Optional[Path] = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            os.chmod(temporary_path, 0o600)
            json.dump(persisted, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        os.chmod(path, 0o600)
    except OSError as exc:
        # Capability persistence improves efficiency but must never make a
        # paper-analysis request fail. The in-process cache remains useful.
        logger.debug("LLM 端点能力缓存写入失败: %s", exc)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def get_endpoint_capability(base_url: str, endpoint: str) -> Optional[dict[str, str]]:
    """Return a previously observed capability record, if any."""
    if endpoint not in _ENDPOINTS:
        raise ValueError(f"unsupported LLM endpoint key: {endpoint}")
    key = normalize_base_url(base_url)
    if not key:
        return None
    with _CACHE_LOCK:
        record = _load_cache_locked().get(key, {}).get(endpoint)
        return dict(record) if record else None


def endpoint_is_known_unsupported(base_url: str, endpoint: str) -> bool:
    """Whether this exact configured provider has rejected the endpoint."""
    record = get_endpoint_capability(base_url, endpoint)
    return bool(record and record.get("state") == "unsupported")


def record_endpoint_capability(
    base_url: str,
    endpoint: str,
    state: str,
    *,
    reason: Any = "",
) -> None:
    """Persist one actual endpoint observation from a provider request."""
    if endpoint not in _ENDPOINTS:
        raise ValueError(f"unsupported LLM endpoint key: {endpoint}")
    if state not in {"supported", "unsupported"}:
        raise ValueError(f"invalid LLM endpoint state: {state}")
    key = normalize_base_url(base_url)
    if not key:
        return
    sanitized_reason = _sanitize_reason(reason)
    record = {
        "state": state,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "reason": sanitized_reason,
    }
    with _CACHE_LOCK:
        cache = _load_cache_locked()
        previous = cache.setdefault(key, {}).get(endpoint)
        cache[key][endpoint] = record
        _write_cache_locked()

    # Only log a changed observation. Per-paper calls remain quiet after the
    # first probe, while operators get an explicit explanation in the run log.
    if previous is None or previous.get("state") != state:
        endpoint_label = {
            "chat_completions": "Chat Completions API",
            "responses": "Responses API",
        }[endpoint]
        if state == "unsupported":
            logger.warning(
                "LLM 端点探测：%s 不支持 %s%s；后续将跳过该端点",
                key,
                endpoint_label,
                f"（{sanitized_reason}）" if sanitized_reason else "",
            )
        else:
            logger.info("LLM 端点探测：%s 支持 %s", key, endpoint_label)


def is_unsupported_endpoint_error(exc: BaseException) -> bool:
    """Whether an exception is clear evidence that an API route is absent.

    Restrict this to route-shaped HTTP failures. A generic ``not found``
    string can also mean a bad model name, so it is insufficient on its own.
    """
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(exc, "status", None)
    if status in {404, 405, 501}:
        return True
    text = str(exc).casefold()
    return bool(
        re.search(
            r"(?:http\s*)?(?:404|405|501).*?(?:not found|method not allowed|unsupported endpoint)"
            r"|(?:not found|method not allowed|unsupported endpoint).*?(?:endpoint|method|api)",
            text,
        )
    )


def clear_endpoint_capability_cache_for_tests() -> None:
    """Clear only the process cache; unit tests patch ``settings.DATA_DIR``."""
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = None

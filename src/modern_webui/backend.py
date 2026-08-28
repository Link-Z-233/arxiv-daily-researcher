"""Shared read/write operations for the lightweight modern WebUI.

The Streamlit panel remains the compatibility implementation while the modern
panel is migrated.  This module deliberately talks to the same configuration
files, SQLite ledger and durable trigger queue instead of maintaining a second
set of settings or background processes.
"""

from __future__ import annotations

import base64
import inspect
import json
import mimetypes
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from utils.backup import (
    LOCAL_BACKUP_RETENTION_DAYS,
    LOCAL_BACKUP_SAME_DAY_MAX_COUNT,
    create_backup,
    export_backup_zip,
    list_local_backups,
    restore_backup_archive,
)
from utils.config_io import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_ENV_PATH,
    _resolve_project_relative_config_path,
    build_config_dict,
    flatten_config_dict,
    read_config_json,
    read_env,
    validate_llm_connection,
    validate_mineru_connection,
    validate_openalex_connection,
    validate_semantic_scholar_connection,
    validate_smtp_connection,
    write_config_json,
    write_env,
)
from utils.daily_research_store import DailyResearchStore
from utils.run_lock import is_lock_held
from utils.source_registry import (
    OPENALEX_JOURNAL_CATALOG,
    OPENALEX_JOURNAL_TYPE,
    builtin_extra_source_definitions,
    source_display_names,
)
from utils.webdav_sync import WebDAVSync
from utils.webui_trigger import (
    SUPPORTED_MODES,
    enqueue_trigger,
    read_trigger_payload,
    request_stop,
    sanitize_task_error_summary,
    trigger_directory,
    trigger_status_directory,
)
from webui.arxiv_categories import ARXIV_CATEGORIES, format_arxiv_category


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_REPORTS_DIR = DEFAULT_DATA_DIR / "reports"
DEFAULT_DB_RELATIVE_PATH = Path("daily_research") / "daily_research.db"
LOGS_DIR = PROJECT_ROOT / "logs"
TREND_PROMPT_TEMPLATES_PATH = DEFAULT_DATA_DIR / "trend_prompt_templates.json"
HISTORY_MODES = frozenset({"legacy_import", "history_data_repair", "history_omission_scan"})
OPERATING_MODES = frozenset({"daily_research", "backfill_run", "trend_research"})
LOCK_NAMES = (
    "daily_research.lock",
    "legacy_import.lock",
    "history_data_repair.lock",
    "history_omission_scan.lock",
    "supplement_run.lock",
    "backfill_run.lock",
)
# Lock names are also used for status presentation.  Trend jobs intentionally
# use parameterized filenames (``trend_research_<hash>.lock``), which are
# matched by prefix rather than appearing in the fixed compatibility tuple.
_LOCK_KIND_PREFIXES = {
    "daily": ("daily_research.lock", "supplement_run.lock"),
    "past": ("backfill_run.lock",),
    "trend": ("trend_research_",),
    "history": (
        "legacy_import.lock",
        "history_data_repair.lock",
        "history_omission_scan.lock",
    ),
}
_LIVE_LOG_PREFIXES = {
    "daily_research.lock": ("daily_", "cron_", "startup_"),
    "legacy_import.lock": ("legacy_import_",),
    "history_data_repair.lock": ("history_data_repair_",),
    "history_omission_scan.lock": ("history_omission_scan_",),
    "supplement_run.lock": ("supplement_run_", "supplement_"),
    "backfill_run.lock": ("backfill_run_", "backfill_"),
}
MODE_LABELS = {
    "daily_research": "每日研究",
    "backfill_run": "过去日报",
    "trend_research": "趋势任务",
    "legacy_import": "旧版本历史导入",
    "history_data_repair": "历史数据补全",
    "history_omission_scan": "历史遗漏扫描",
    "supplement_run": "补充报告",
}
PHASE_LABELS = {
    "prepare": "准备运行",
    "scan": "扫描数据源",
    "score": "评分筛选",
    "analyze": "深度分析",
    "report": "生成报告",
    "legacy_import": "导入旧历史",
    "legacy_history": "读取历史记录",
    "legacy_keywords": "整理关键词",
    "legacy_reports": "读取历史报告",
    "legacy_write": "写入 SQLite",
    "legacy_backlog": "整理补充任务",
    "legacy_scan": "扫描遗漏论文",
    "legacy_supplement": "生成补充报告",
    "history_repair": "补全历史数据",
    "history_omission_scan": "扫描历史遗漏",
    "history_omission_week": "生成周补充报告",
}
HISTORY_TASK_LABELS = {
    "legacy_import": "旧版本历史导入",
    "history_data_repair": "历史数据补全",
    "history_omission_scan": "历史遗漏扫描",
}
HISTORY_RUN_KINDS = {
    "legacy_import": "legacy_import",
    "history_data_repair": "history_data_repair",
    "history_omission_scan": "history_omission_scan",
}
_LIVE_TASK_STATES = frozenset({"queued", "starting", "running"})
_RETRYABLE_TASK_STATES = frozenset(
    {"failed", "rejected", "interrupted", "skipped_busy"}
)

# Secrets never leave the server.  An empty form input therefore keeps the
# existing value; explicit clearing is available through ``clear_env``.
SECRET_ENV_FIELDS = frozenset(
    {
        "CHEAP_LLM__API_KEY",
        "SMART_LLM__API_KEY",
        "MINERU_API_KEY",
        "OPENALEX_API_KEY",
        "SEMANTIC_SCHOLAR_API_KEY",
        "SMTP_PASSWORD",
        "WECHAT_WEBHOOK_URL",
        "DINGTALK_WEBHOOK_URL",
        "DINGTALK_SECRET",
        "TELEGRAM_BOT_TOKEN",
        "SLACK_WEBHOOK_URL",
        "GENERIC_WEBHOOK_URL",
        "WEBDAV_PASSWORD",
    }
)
PUBLIC_ENV_FIELDS = frozenset(
    {
        "CHEAP_LLM__BASE_URL",
        "CHEAP_LLM__MODEL_NAME",
        "CHEAP_LLM__TEMPERATURE",
        "SMART_LLM__BASE_URL",
        "SMART_LLM__MODEL_NAME",
        "SMART_LLM__TEMPERATURE",
        "ENABLE_OPENALEX",
        "ENABLE_SEMANTIC_SCHOLAR_TLDR",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_FROM",
        "SMTP_TO",
        "SMTP_USE_TLS",
        "TELEGRAM_CHAT_ID",
        "WEBDAV_URL",
        "WEBDAV_USERNAME",
    }
)
WRITABLE_ENV_FIELDS = SECRET_ENV_FIELDS | PUBLIC_ENV_FIELDS
_CONFIG_FIELDS = frozenset(inspect.signature(build_config_dict).parameters)
_PID_RE = re.compile(r"(?:^|\b)PID=(\d+)(?:\b|,)")


class ModernWebUIError(ValueError):
    """An expected, safe error to expose to an authenticated operator."""


def _coerce_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _ensure_json_value(value: Any, *, depth: int = 0) -> None:
    """Reject oversized/non-JSON setting payloads before they reach a file."""
    if depth > 5:
        raise ModernWebUIError("配置层级过深。")
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if len(value) > 12_000 or "\x00" in value:
            raise ModernWebUIError("配置字段长度无效。")
        return
    if isinstance(value, list):
        if len(value) > 500:
            raise ModernWebUIError("配置列表过长。")
        for item in value:
            _ensure_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 100:
            raise ModernWebUIError("配置对象过大。")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 100:
                raise ModernWebUIError("配置字段名无效。")
            _ensure_json_value(item, depth=depth + 1)
        return
    raise ModernWebUIError("配置包含不支持的数据类型。")


def flat_config() -> dict[str, Any]:
    raw = read_config_json()
    return flatten_config_dict(raw) if isinstance(raw, dict) else {}


def configured_data_dir(flat: Mapping[str, Any] | None = None) -> Path:
    values = flat or flat_config()
    try:
        return _resolve_project_relative_config_path(
            values.get("data_dir", "data"), label="paths.data_dir"
        )
    except (TypeError, ValueError):
        return DEFAULT_DATA_DIR


def configured_db_path(flat: Mapping[str, Any] | None = None) -> Path:
    values = flat or flat_config()
    raw = values.get("daily_research_db_path")
    if isinstance(raw, str) and raw.strip():
        try:
            return _resolve_project_relative_config_path(
                raw, label="daily_research.db_path"
            )
        except ValueError:
            pass
    return configured_data_dir(values) / DEFAULT_DB_RELATIVE_PATH


def configured_reports_dir(flat: Mapping[str, Any] | None = None) -> Path:
    values = flat or flat_config()
    raw = values.get("reports")
    if isinstance(raw, str) and raw.strip():
        try:
            return _resolve_project_relative_config_path(raw, label="paths.reports")
        except ValueError:
            pass
    return configured_data_dir(values) / "reports"


def open_store(
    flat: Mapping[str, Any] | None = None, *, create: bool = False
) -> DailyResearchStore | None:
    """Open the shared history store without changing ordinary empty-state UX.

    Read-only pages deliberately keep showing their existing ``no database``
    message until the worker has produced data.  A report's in-card preference
    controls are different: Streamlit initialises the small SQLite ledger on
    first use so an archived report can be marked before a daily run.  The
    explicit ``create`` flag keeps those two behaviours aligned without
    accidentally creating a database merely by opening a dashboard page.
    """
    path = configured_db_path(flat)
    if not create and not path.is_file():
        return None
    try:
        return DailyResearchStore(path)
    except Exception:
        return None


def public_settings() -> dict[str, Any]:
    """Return configuration plus redacted environment values for the UI."""
    env = read_env()
    return {
        "config": flat_config(),
        "env": {key: str(env.get(key) or "") for key in PUBLIC_ENV_FIELDS},
        "secrets": {key: bool(str(env.get(key) or "").strip()) for key in SECRET_ENV_FIELDS},
        "builtin_sources": [
            {
                "type": OPENALEX_JOURNAL_TYPE,
                "code": "prl",
                "display_name": OPENALEX_JOURNAL_CATALOG["prl"]["display_name"],
                "full_name": OPENALEX_JOURNAL_CATALOG["prl"]["full_name"],
                "issn": list(OPENALEX_JOURNAL_CATALOG["prl"]["issn"]),
            },
            *builtin_extra_source_definitions(),
        ],
        # Keep both modern selectors on the same complete arXiv catalog as
        # the Streamlit panel.  The display label is sent by the server so
        # category additions cannot silently diverge between two UIs.
        "arxiv_categories": [
            {"code": code, "label": format_arxiv_category(code)}
            for code in ARXIV_CATEGORIES
        ],
    }


def save_settings(
    config_updates: Mapping[str, Any] | None,
    env_updates: Mapping[str, Any] | None,
    clear_env: Iterable[object] | None = None,
) -> dict[str, Any]:
    """Persist a bounded partial update without exposing or dropping secrets."""
    if config_updates is not None and not isinstance(config_updates, Mapping):
        raise ModernWebUIError("配置更新必须是对象。")
    if env_updates is not None and not isinstance(env_updates, Mapping):
        raise ModernWebUIError("环境变量更新必须是对象。")
    current_flat = flat_config()
    incoming_config = config_updates or {}
    unknown = set(incoming_config).difference(_CONFIG_FIELDS)
    if unknown:
        raise ModernWebUIError("包含不支持的配置字段：" + ", ".join(sorted(unknown)))
    for key, value in incoming_config.items():
        _ensure_json_value(value)
        current_flat[key] = value

    # build_config_dict is deliberately the one portable round-trip path used
    # by the existing panel. It preserves validation and normalizes legacy
    # source definitions, backup policies and safe project-relative paths.
    config_args = {key: current_flat[key] for key in _CONFIG_FIELDS if key in current_flat}
    try:
        write_config_json(build_config_dict(**config_args))
    except (TypeError, ValueError) as exc:
        raise ModernWebUIError(str(exc)) from exc

    current_env = read_env()
    incoming_env = env_updates or {}
    unknown_env = set(incoming_env).difference(WRITABLE_ENV_FIELDS)
    if unknown_env:
        raise ModernWebUIError("包含不支持的环境变量字段。")
    for key, value in incoming_env.items():
        if not isinstance(value, (str, int, float, bool)):
            raise ModernWebUIError("环境变量值必须是文本、数字或开关。")
        text = str(value)
        if len(text) > 12_000 or "\x00" in text:
            raise ModernWebUIError("环境变量值长度无效。")
        # A blank secret field is the safe default: retain the saved key.
        if key in SECRET_ENV_FIELDS and not text:
            continue
        current_env[key] = text.lower() if isinstance(value, bool) else text
    requested_clears = set(clear_env or [])
    if not requested_clears.issubset(SECRET_ENV_FIELDS):
        raise ModernWebUIError("只能清除受管理的密钥字段。")
    for key in requested_clears:
        current_env[key] = ""
    try:
        write_env(current_env)
    except OSError as exc:
        raise ModernWebUIError(f"保存环境变量失败：{exc}") from exc
    return public_settings()


def request_worker_restart() -> None:
    marker = trigger_directory(DEFAULT_DATA_DIR) / "restart_worker.request"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            f"requested_at={datetime.now().isoformat()}\n", encoding="utf-8"
        )
    except OSError as exc:
        raise ModernWebUIError(f"无法写入重启请求：{exc}") from exc


def _read_lock_pid(path: Path) -> int | None:
    try:
        match = _PID_RE.search(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return int(match.group(1)) if match else None


def active_locks(flat: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return every currently held worker lock from both supported data roots.

    Most modes use a fixed name, while trend research deliberately derives a
    parameter-specific ``trend_research_<hash>.lock``.  Enumerating only a
    small static list made an active trend task invisible to the modern panel
    and could offer a conflicting launch button.  The Streamlit panel scans
    the run directory, so keep the same behaviour here.
    """
    data_dir = configured_data_dir(flat)
    results: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for directory in (data_dir / "run", DEFAULT_DATA_DIR / "run"):
        try:
            paths = sorted(directory.glob("*.lock"), key=lambda item: item.name)
        except OSError:
            continue
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            try:
                held = path.exists() and is_lock_held(path)
            except OSError:
                held = path.exists()
            if held:
                results.append({"name": path.name, "pid": _read_lock_pid(path)})
    return results


def _locks_for_kind(locks: Iterable[Mapping[str, Any]], kind: str) -> list[dict[str, Any]]:
    """Filter active lock metadata for one operation page."""
    prefixes = _LOCK_KIND_PREFIXES.get(kind, ())
    rows: list[dict[str, Any]] = []
    for lock in locks:
        name = str(lock.get("name") or "")
        if any(name == prefix or name.startswith(prefix) for prefix in prefixes):
            rows.append(dict(lock))
    return rows


def _is_history_lock(lock: Mapping[str, Any]) -> bool:
    """Whether a lock belongs exclusively to idle-time history maintenance."""
    return bool(_locks_for_kind((lock,), "history"))


def _label_for_lock(name: str) -> str:
    if name.startswith("trend_research_"):
        return MODE_LABELS["trend_research"]
    mapping = {
        "daily_research.lock": "daily_research",
        "supplement_run.lock": "supplement_run",
        "backfill_run.lock": "backfill_run",
        "legacy_import.lock": "legacy_import",
        "history_data_repair.lock": "history_data_repair",
        "history_omission_scan.lock": "history_omission_scan",
    }
    return MODE_LABELS.get(mapping.get(name, ""), "正在运行")


def _newest_log_with_prefixes(prefixes: tuple[str, ...]) -> Path | None:
    """Return the newest matching local run log without exposing a path."""
    if not LOGS_DIR.is_dir():
        return None
    try:
        matches = [
            path
            for path in LOGS_DIR.rglob("*.log")
            if path.is_file() and path.name.lower().startswith(prefixes)
        ]
        return max(matches, key=lambda path: path.stat().st_mtime) if matches else None
    except OSError:
        return None


def _live_log_tail(locks: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Build the bounded live-log payload used by a running status card."""
    selected: Path | None = None
    for lock in locks:
        name = str(lock.get("name") or "")
        prefixes = _LIVE_LOG_PREFIXES.get(name)
        if prefixes is None and name.startswith("trend_research_"):
            prefixes = ("trend_",)
        if prefixes:
            selected = _newest_log_with_prefixes(prefixes)
            if selected is not None:
                break
    if selected is None:
        return None
    try:
        relative = selected.relative_to(LOGS_DIR).as_posix()
        lines = selected.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return None
    max_lines = 80
    skipped = max(0, len(lines) - max_lines)
    visible = lines[-max_lines:]
    if skipped:
        visible.insert(0, f"… 已隐藏较早的 {skipped} 行 …")
    return {
        "name": relative,
        "content": "\n".join(visible),
        "truncated": bool(skipped),
    }


def _read_status_file(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("mode") not in SUPPORTED_MODES:
        return None
    request_id = str(raw.get("request_id") or "").strip()
    if not request_id:
        return None
    return {
        "request_id": request_id,
        "mode": str(raw["mode"]),
        "created_at": str(raw.get("created_at") or ""),
        "started_at": str(raw.get("started_at") or ""),
        "updated_at": str(raw.get("updated_at") or ""),
        "state": str(raw.get("state") or "unknown"),
        "issue": sanitize_task_error_summary(raw.get("error_summary") or raw.get("error")),
        "args": raw.get("args") if isinstance(raw.get("args"), dict) else {},
    }


def task_records(modes: Iterable[str] | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
    """Combine durable queue entries and worker receipts into one safe list."""
    allowed = set(modes or SUPPORTED_MODES)
    allowed.intersection_update(SUPPORTED_MODES)
    queue_dir = trigger_directory(DEFAULT_DATA_DIR)
    records: dict[str, dict[str, Any]] = {}
    try:
        for path in queue_dir.glob("*.json"):
            payload = read_trigger_payload(path)
            if payload.get("mode") not in allowed:
                continue
            request_id = str(payload["request_id"])
            records[request_id] = {
                "request_id": request_id,
                "mode": str(payload["mode"]),
                "created_at": str(payload.get("created_at") or ""),
                "started_at": "",
                "updated_at": "",
                "state": "queued",
                "issue": "",
                "args": payload.get("args") if isinstance(payload.get("args"), dict) else {},
            }
        for path in queue_dir.glob("*.running"):
            payload = read_trigger_payload(path)
            if payload.get("mode") not in allowed:
                continue
            request_id = str(payload["request_id"])
            records[request_id] = {
                "request_id": request_id,
                "mode": str(payload["mode"]),
                "created_at": str(payload.get("created_at") or ""),
                "started_at": "",
                "updated_at": "",
                "state": "starting",
                "issue": "",
                "args": payload.get("args") if isinstance(payload.get("args"), dict) else {},
            }
    except (OSError, ValueError):
        pass
    status_dir = trigger_status_directory(DEFAULT_DATA_DIR)
    try:
        paths = sorted(status_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        paths = []
    for path in paths:
        record = _read_status_file(path)
        if record is None or record["mode"] not in allowed:
            continue
        # A live queue entry has precedence over an older receipt with the
        # same ID. This makes worker hand-off visible without flickering.
        existing = records.get(record["request_id"])
        if existing is None or existing["state"] not in {"queued", "starting"}:
            records[record["request_id"]] = record
    rows = list(records.values())
    rows.sort(key=lambda item: (item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return rows[: max(1, min(int(limit), 500))]


def _latest_record(modes: Iterable[str]) -> dict[str, Any] | None:
    rows = task_records(modes, limit=100)
    return rows[0] if rows else None


def run_status(kind: str = "daily") -> dict[str, Any]:
    """Return the durable state for a modern run page without starting work."""
    flat = flat_config()
    locks = active_locks(flat)
    mode_map = {
        "daily": {"daily_research", "supplement_run"},
        "past": {"backfill_run"},
        "trend": {"trend_research"},
        "history": set(HISTORY_MODES),
    }
    wanted = mode_map.get(kind, mode_map["daily"])
    records = task_records(wanted)
    live_records = [row for row in records if row["state"] in {"queued", "starting", "running"}]
    # The watcher accepts one trigger at a time.  Daily and trend launchers
    # therefore follow the Streamlit guard and wait until any just-submitted
    # request is handed to a worker; past-date jobs remain queueable behind a
    # running job by design.
    all_live_records = [
        row
        for row in task_records(SUPPORTED_MODES)
        if row["state"] in {"queued", "starting", "running"}
    ]
    relevant_locks = _locks_for_kind(locks, kind)
    store = open_store(flat)
    progress = None
    queue: dict[str, Any] = {}
    backfill: dict[str, Any] = {}
    last_run: dict[str, Any] | None = None
    if store is not None:
        try:
            progress = store.active_run_progress()
            queue = store.count_pending_papers()
            backfill = store.backfill_queue_summary()
            recent = store.get_recent_runs(limit=1)
            last_run = recent[0] if recent else None
        except Exception:
            progress = None

    progress_kind = str((progress or {}).get("run_kind") or "")
    progress_matches = (
        (kind == "daily" and progress_kind in {"daily", "daily_research", "supplement", "supplement_run"})
        or (kind == "past" and progress_kind in {"backfill", "backfill_run"})
        or (kind == "history" and progress_kind in HISTORY_MODES)
        or (kind == "trend" and progress_kind in {"trend", "trend_research"})
    )
    if progress_matches:
        task = {
            "state": "running",
            "label": MODE_LABELS.get(progress_kind, "正在运行"),
            "phase": PHASE_LABELS.get(str(progress.get("phase") or ""), str(progress.get("phase") or "处理中")),
            "detail": sanitize_task_error_summary(progress.get("detail"), max_chars=260),
            "current": progress.get("current"),
            "total": progress.get("total"),
            "started_at": progress.get("started_at"),
            "counters": {
                "registered": int(progress.get("registered") or 0),
                "scored": int(progress.get("scored") or 0),
                "analyzed": int(progress.get("analyzed") or 0),
                "completed": int(progress.get("completed") or 0),
                "failed": int(progress.get("failed") or 0),
            },
        }
    elif live_records:
        latest = live_records[0]
        task = {
            "state": latest["state"],
            "label": MODE_LABELS.get(latest["mode"], latest["mode"]),
            "phase": "等待工作进程接手" if latest["state"] != "running" else "正在运行",
            "detail": latest.get("issue") or "",
            "current": None,
            "total": None,
            "started_at": latest.get("created_at") or "",
        }
    elif relevant_locks:
        primary = relevant_locks[0]
        task = {
            "state": "running",
            "label": _label_for_lock(str(primary.get("name") or "")),
            "phase": "正在运行，等待进度写入",
            "detail": "",
            "current": None,
            "total": None,
            "started_at": "",
        }
    else:
        latest = _latest_record(wanted)
        if latest and latest["state"] in {"failed", "rejected", "interrupted", "skipped_busy"}:
            task = {
                "state": latest["state"],
                "label": "上次任务未完成",
                "phase": "请查看问题摘要后重试",
                "detail": latest.get("issue") or "",
                "current": None,
                "total": None,
                "started_at": latest.get("updated_at") or "",
            }
        else:
            task = {
                "state": "idle",
                "label": "空闲",
                "phase": "可以开始任务",
                "detail": "",
                "current": None,
                "total": None,
                "started_at": "",
            }
    active = bool(live_records or progress_matches or relevant_locks)
    if kind == "past":
        can_start = not bool(all_live_records)
    elif kind in {"daily", "trend"}:
        can_start = not bool(locks or all_live_records)
    else:
        # History maintenance is intentionally allowed to enter the durable
        # idle-time queue behind normal research, but duplicate history work
        # remains disabled until its preceding request has finished.
        can_start = not bool(live_records)
    # The compatibility panel intentionally keeps history-maintenance status
    # out of the normal daily/previous-date cards.  Its locks still take part
    # in launch safety above, but operators inspect their details only from
    # the dedicated History Maintenance page.
    display_locks = locks if kind == "history" else [
        lock for lock in locks if not _is_history_lock(lock)
    ]
    return {
        "task": task,
        "is_active": active,
        "can_start": can_start,
        "queue": {
            "pending": int(queue.get("total") or 0),
            "retry": int(queue.get("failed_retry") or 0),
        },
        "backfill": backfill,
        "last_run": last_run,
        "active_locks": display_locks,
        "relevant_locks": relevant_locks,
        "has_relevant_lock": bool(relevant_locks),
        "live_log": _live_log_tail(relevant_locks) if active else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def enqueue_task(mode: str, args: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if mode not in SUPPORTED_MODES:
        raise ModernWebUIError("不支持的任务类型。")
    safe_args = dict(args or {})
    _ensure_json_value(safe_args)
    try:
        path = enqueue_trigger(DEFAULT_DATA_DIR, mode, **safe_args)
    except (TypeError, ValueError) as exc:
        raise ModernWebUIError(str(exc)) from exc
    return {"queued": True, "request_id": path.stem.rsplit("_", 1)[-1], "mode": mode}


def stop_active_tasks() -> list[int]:
    pids = [row["pid"] for row in active_locks() if isinstance(row.get("pid"), int)]
    if not pids:
        raise ModernWebUIError("没有可停止的 WebUI 任务。")
    for pid in pids:
        request_stop(DEFAULT_DATA_DIR, pid)
    return pids


def history_status() -> dict[str, Any]:
    """Return the focused, durable status model for history maintenance.

    History work deliberately runs through the ordinary worker trigger queue,
    but it is not part of the daily-run panel.  This response mirrors the
    Streamlit history panel: task receipts remain compact and safe, while a
    matching SQLite heartbeat supplies meaningful phase progress for the one
    task currently being processed.
    """
    flat = flat_config()
    store = open_store(flat)
    summary = None
    if store is not None:
        try:
            raw = store.get_app_state("legacy_import_summary")
            parsed = json.loads(raw) if raw else None
            summary = parsed if isinstance(parsed, dict) else None
        except Exception:
            summary = None
    active_progress: Mapping[str, Any] | None = None
    if store is not None:
        try:
            candidate = store.active_run_progress()
            active_progress = candidate if isinstance(candidate, Mapping) else None
        except Exception:
            active_progress = None
    records = [
        _history_task_row(row, active_progress)
        for row in task_records(HISTORY_MODES)
        if row["state"] != "succeeded"
    ]
    return {
        "status": run_status("history"),
        "last_import": summary,
        "tasks": records,
    }


def _history_task_progress(
    record: Mapping[str, Any], progress: Mapping[str, Any] | None
) -> str:
    """Turn a receipt plus optional SQLite heartbeat into concise task text."""
    state = str(record.get("state") or "")
    mode = str(record.get("mode") or "")
    if state == "queued":
        return "已加入闲时队列，等待其他研究任务完成"
    if state == "starting":
        return "工作进程正在接手任务"
    if state != "running":
        return ""
    if not isinstance(progress, Mapping) or progress.get("run_kind") != HISTORY_RUN_KINDS.get(mode):
        return "等待系统空闲后继续运行"

    phase = PHASE_LABELS.get(
        str(progress.get("phase") or ""), str(progress.get("phase") or "处理中")
    )
    detail = sanitize_task_error_summary(progress.get("detail"), max_chars=160)
    parts = [phase]
    if detail:
        parts.append(detail)
    current = progress.get("current")
    total = progress.get("total")
    if (
        isinstance(current, int)
        and not isinstance(current, bool)
        and isinstance(total, int)
        and not isinstance(total, bool)
        and total > 0
    ):
        parts.append(f"{max(0, current)}/{total}")
    return " · ".join(parts)


def _history_task_row(
    record: Mapping[str, Any], progress: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Provide the UI-only columns used by the history task table."""
    row = dict(record)
    state = str(row.get("state") or "unknown")
    row["label"] = HISTORY_TASK_LABELS.get(str(row.get("mode") or ""), str(row.get("mode") or "未知任务"))
    row["started_at"] = str(row.get("started_at") or "")
    row["completed_at"] = (
        str(row.get("updated_at") or "") if state not in _LIVE_TASK_STATES else ""
    )
    row["progress"] = _history_task_progress(row, progress)
    row["retryable"] = state in _RETRYABLE_TASK_STATES
    return row


def retry_history_task(request_id: str) -> dict[str, Any]:
    record = next((item for item in task_records(HISTORY_MODES) if item["request_id"] == request_id), None)
    if record is None:
        raise ModernWebUIError("未找到可重试的历史维护任务。")
    if record["state"] not in {"failed", "rejected", "interrupted", "skipped_busy"}:
        raise ModernWebUIError("该历史维护任务当前不能重试。")
    return enqueue_task(record["mode"], record.get("args") or {})


def _source_list(store: DailyResearchStore) -> list[str]:
    try:
        with store._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT source FROM daily_papers WHERE source != '' ORDER BY source"
            ).fetchall()
        return [str(row["source"]) for row in rows if row["source"]]
    except Exception:
        return []


def paper_search(filters: Mapping[str, Any]) -> dict[str, Any]:
    store = open_store()
    if store is None:
        return {"available": False, "sources": [], "total": 0, "items": []}
    query = str(filters.get("query") or "")[:500]
    source = str(filters.get("source") or "").strip().lower() or None
    completed_from = str(filters.get("completed_from") or "").strip() or None
    completed_to = str(filters.get("completed_to") or "").strip() or None
    try:
        min_score_raw = filters.get("min_score")
        min_score = float(min_score_raw) if min_score_raw not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        raise ModernWebUIError("最低分数必须是数字。")
    try:
        limit = max(5, min(int(filters.get("limit", 10)), 100))
        offset = max(0, int(filters.get("offset", 0)))
        result = store.search_papers(
            query=query,
            source=source,
            liked_only=_coerce_bool(filters.get("liked_only")),
            min_score=min_score,
            completed_from=completed_from,
            completed_to=completed_to,
            limit=limit,
            offset=offset,
        )
    except (TypeError, ValueError) as exc:
        raise ModernWebUIError(f"检索失败：{exc}") from exc
    return {"available": True, "sources": _source_list(store), **result}


def preferences_summary() -> dict[str, Any]:
    store = open_store()
    if store is None:
        return {"available": False, "counts": {"like": 0, "dislike": 0}, "liked": [], "authors": [], "keywords": []}
    try:
        aggregate = store.aggregate_liked_preferences()
        liked = store.list_preferences(preference="like", limit=500)
        urls = store.liked_paper_urls()
        for row in liked:
            row["url"] = urls.get(
                (str(row.get("source") or ""), str(row.get("paper_id") or ""))
            )
        return {
            "available": True,
            "counts": store.get_preference_counts(),
            "liked": liked,
            "authors": aggregate.get("authors") or [],
            "keywords": store.aggregate_liked_keywords(limit=500),
        }
    except Exception as exc:
        raise ModernWebUIError(f"读取收藏数据失败：{exc}") from exc


def set_preference(payload: Mapping[str, Any]) -> dict[str, Any]:
    # Match the Streamlit report viewer: preferences are usable for a saved
    # daily report even before a worker run has created the history database.
    store = open_store(create=True)
    if store is None:
        raise ModernWebUIError("SQLite 数据库尚不可用。")
    source = str(payload.get("source") or "").strip().lower()[:100]
    paper_id = str(payload.get("paper_id") or "").strip()[:500]
    title = str(payload.get("title") or paper_id).strip()[:4_000]
    preference = str(payload.get("preference") or "none")
    if not source or not paper_id:
        raise ModernWebUIError("论文来源和标识不能为空。")
    authors = payload.get("authors") if isinstance(payload.get("authors"), list) else []
    categories = payload.get("categories") if isinstance(payload.get("categories"), list) else []
    try:
        store.set_paper_preference(
            source,
            paper_id,
            preference=preference,
            title=title,
            canonical_id=str(payload.get("canonical_id") or "")[:500] or None,
            version=int(payload["version"]) if payload.get("version") not in (None, "") else None,
            authors=[str(item)[:500] for item in authors[:100]],
            categories=[str(item)[:100] for item in categories[:100]],
        )
    except (TypeError, ValueError) as exc:
        raise ModernWebUIError(str(exc)) from exc
    return {"ok": True, "preference": preference}


def learned_preference_terms() -> dict[str, list[dict[str, Any]]]:
    store = open_store()
    if store is None:
        return {"keywords": [], "authors": []}
    rows = store.get_learned_preference_terms(limit=500)
    return {
        "keywords": [row for row in rows if row.get("term_type") == "keyword"],
        "authors": [row for row in rows if row.get("term_type") == "author"],
    }


def extracted_keywords() -> list[dict[str, Any]]:
    path = configured_data_dir() / "keywords" / "keywords_cache.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    keywords = raw.get("keywords") if isinstance(raw, dict) else None
    if not isinstance(keywords, dict):
        return []
    rows = []
    for name, weight in keywords.items():
        if not isinstance(name, str) or not isinstance(weight, (int, float)):
            continue
        rows.append({"keyword": name, "weight": float(weight)})
    return sorted(rows, key=lambda item: (-item["weight"], item["keyword"]))


def _read_trend_prompt_templates() -> dict[str, str]:
    """Read the small user-owned template library without failing the UI."""
    try:
        raw = json.loads(TREND_PROMPT_TEMPLATES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        name.strip(): text.strip()
        for name, text in raw.items()
        if isinstance(name, str)
        and isinstance(text, str)
        and name.strip()
        and len(name.strip()) <= 120
        and len(text.strip()) <= 8_000
    }


def list_trend_prompt_templates() -> list[dict[str, str]]:
    return [
        {"name": name, "text": text}
        for name, text in sorted(_read_trend_prompt_templates().items(), key=lambda item: item[0].casefold())
    ]


def _write_trend_prompt_templates(templates: Mapping[str, str]) -> None:
    path = TREND_PROMPT_TEMPLATES_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(dict(templates), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise ModernWebUIError(f"保存趋势提示词模板失败：{exc}") from exc


def save_trend_prompt_template(name: object, text: object) -> list[dict[str, str]]:
    safe_name = str(name or "").strip()
    safe_text = str(text or "").strip()
    if not safe_name:
        raise ModernWebUIError("模板名称不能为空。")
    if len(safe_name) > 120 or any(char in safe_name for char in "\r\n\x00"):
        raise ModernWebUIError("模板名称长度或格式无效。")
    if not safe_text:
        raise ModernWebUIError("模板内容不能为空。")
    if len(safe_text) > 8_000 or "\x00" in safe_text:
        raise ModernWebUIError("模板内容长度或格式无效。")
    templates = _read_trend_prompt_templates()
    if safe_name not in templates and len(templates) >= 50:
        raise ModernWebUIError("最多保存 50 个趋势提示词模板。")
    templates[safe_name] = safe_text
    _write_trend_prompt_templates(templates)
    return list_trend_prompt_templates()


def delete_trend_prompt_template(name: object) -> list[dict[str, str]]:
    safe_name = str(name or "").strip()
    templates = _read_trend_prompt_templates()
    if safe_name not in templates:
        raise ModernWebUIError("未找到该趋势提示词模板。")
    del templates[safe_name]
    _write_trend_prompt_templates(templates)
    return list_trend_prompt_templates()


def diagnostics(days: int | None) -> dict[str, Any]:
    if days is not None and days not in {3, 7, 14, 30}:
        raise ModernWebUIError("诊断时间范围无效。")
    store = open_store()
    if store is None:
        return {"available": False, "llm": [], "sources": [], "runs": []}
    try:
        source_labels = source_display_names(flat_config().get("extra_source_definitions", []))
        sources = store.get_source_health_for_days(days)
        source_rows = []
        for source, value in sources.items():
            source_rows.append(
                {
                    "source": source,
                    "name": source_labels.get(source, source),
                    "last_status": value.get("last_status"),
                    "last_event_at": value.get("last_event_at"),
                    "last_success_at": value.get("last_success_at"),
                    "events": value.get("events_in_window", 0),
                    "success_rate": value.get("success_rate"),
                    "last_error": value.get("last_error"),
                    "last_error_at": value.get("last_error_at"),
                }
            )
        source_rows.sort(key=lambda item: str(item.get("last_event_at") or ""), reverse=True)
        return {
            "available": True,
            "llm": store.get_llm_health_by_model(days),
            "sources": source_rows,
            "runs": store.get_recent_operational_runs(limit=None, days=days),
        }
    except Exception as exc:
        raise ModernWebUIError(f"读取运行诊断失败：{exc}") from exc


def analytics(days: int | None) -> dict[str, Any]:
    if days is not None and days not in {7, 30, 90, 365}:
        raise ModernWebUIError("数据分析时间范围无效。")
    store = open_store()
    if store is None:
        return {"available": False, "daily": [], "models": []}
    try:
        return {
            "available": True,
            "daily": store.get_daily_token_totals(days),
            "models": store.get_token_usage_by_model(days),
            # The Streamlit dashboard always renders a one-year activity
            # heatmap independently of the selected trend range.
            "heatmap_daily": store.get_daily_token_totals(days=365),
        }
    except Exception as exc:
        raise ModernWebUIError(f"读取数据分析失败：{exc}") from exc


def _report_sort_key(path: Path) -> tuple[int, int, str]:
    stem = path.stem
    match = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})(?:_(\d+))?", stem)
    if match:
        micro = (match.group(5) or "").ljust(6, "0")
        digits = re.sub(r"\D", "", match.group(1) + match.group(2) + match.group(3) + match.group(4))
        return (1, int(digits + micro), path.name)
    try:
        return (0, int(path.stat().st_mtime), path.name)
    except OSError:
        return (0, 0, path.name)


def _report_token(path: Path, root: Path) -> str:
    relative = path.resolve().relative_to(root.resolve()).as_posix().encode("utf-8")
    return base64.urlsafe_b64encode(relative).decode("ascii").rstrip("=")


def _report_path(token: str, root: Path) -> Path:
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", token):
        raise ModernWebUIError("报告标识无效。")
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise ModernWebUIError("报告标识无效。") from None
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ModernWebUIError("报告路径无效。") from exc
    if candidate.suffix.lower() != ".html" or not candidate.is_file():
        raise ModernWebUIError("报告文件不存在。")
    return candidate


def list_reports(show_non_arxiv: bool = False) -> dict[str, list[dict[str, Any]]]:
    root = configured_reports_dir()
    groups: dict[str, list[dict[str, Any]]] = {"daily": [], "trend": [], "keyword_trend": []}
    if not root.is_dir():
        return groups
    daily_root = root / "daily_research" / "html"
    if daily_root.is_dir():
        for path in daily_root.rglob("*.html"):
            if not path.is_file():
                continue
            relative = path.relative_to(daily_root)
            source = relative.parts[0].lower() if len(relative.parts) > 1 else (re.match(r"(.+?)_Report_", path.stem, re.I).group(1).lower() if re.match(r"(.+?)_Report_", path.stem, re.I) else "unknown")
            if not show_non_arxiv and source != "arxiv":
                continue
            groups["daily"].append(_report_row(path, root, "daily", source))
    trend_root = root / "trend_research" / "html"
    if trend_root.is_dir():
        for path in trend_root.rglob("*.html"):
            if path.is_file():
                relative = path.relative_to(trend_root)
                source = relative.parts[0] if len(relative.parts) > 1 else "trend"
                groups["trend"].append(_report_row(path, root, "trend", source))
    keyword_root = root / "keyword_trend" / "html"
    if keyword_root.is_dir():
        for path in keyword_root.glob("*.html"):
            if path.is_file():
                groups["keyword_trend"].append(_report_row(path, root, "keyword_trend", "keyword_trend"))
    for name, values in groups.items():
        values.sort(key=lambda item: item["sort_key"], reverse=True)
        _disambiguate_report_labels(values)
        for row in values:
            row.pop("sort_key", None)
    return groups


def _report_row(path: Path, root: Path, report_type: str, source: str) -> dict[str, Any]:
    try:
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        size = stat.st_size
    except OSError:
        mtime, size = "", 0
    date_match = re.search(r"\d{4}-\d{2}-\d{2}", path.stem)
    source_key = str(source or "unknown").strip().lower() or "unknown"
    labels = _report_source_labels()
    return {
        "id": _report_token(path, root),
        "name": path.name,
        "label": _report_label(path, report_type),
        "source": source_key,
        "source_label": labels.get(source_key, source),
        "type": report_type,
        "date": date_match.group(0) if date_match else "",
        "modified_at": mtime,
        "size_bytes": size,
        "metadata": _trend_report_metadata(path) if report_type == "trend" else None,
        "sort_key": _report_sort_key(path),
    }


def _report_source_labels() -> dict[str, str]:
    """Use the same configured source names as Streamlit's report browser."""
    try:
        definitions = flat_config().get("extra_source_definitions", [])
        return source_display_names(definitions)
    except (TypeError, ValueError):
        return source_display_names()


def _report_label(path: Path, report_type: str) -> str:
    """Format report labels exactly like the Streamlit select boxes."""
    stem = path.stem
    if report_type == "daily":
        match = re.search(
            r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})(?:_\d+)?$", stem
        )
        if match:
            return f"{match.group(1)}  {match.group(2).replace('-', ':')}"
    elif report_type == "trend":
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})", stem)
        if match:
            return f"{match.group(1)} → {match.group(2)}"
    elif report_type == "keyword_trend":
        match = re.search(r"(\d{4}-\d{2}-\d{2})$", stem)
        if match:
            return match.group(1)
    return stem


def _disambiguate_report_labels(rows: list[dict[str, Any]]) -> None:
    """Keep same-source select-box labels unique without exposing noise.

    Daily report filenames keep a microsecond suffix so supplement and normal
    runs never overwrite each other.  The friendly label hides it until two
    reports would otherwise become indistinguishable, exactly as the
    Streamlit browser does.
    """
    counts: dict[tuple[str, str, str], int] = {}
    for row in rows:
        key = (str(row.get("type") or ""), str(row.get("source") or ""), str(row.get("label") or ""))
        counts[key] = counts.get(key, 0) + 1

    used: set[tuple[str, str, str]] = set()
    for row in rows:
        label = str(row.get("label") or "")
        key = (str(row.get("type") or ""), str(row.get("source") or ""), label)
        if counts.get(key, 0) <= 1:
            used.add(key)
            continue
        micro = re.search(r"_(\d+)$", str(row.get("name") or "").rsplit(".", 1)[0])
        suffix = f".{micro.group(1)}" if micro else " · duplicate"
        candidate = f"{label}{suffix}"
        duplicate_number = 2
        while (key[0], key[1], candidate) in used:
            candidate = f"{label}{suffix} · {duplicate_number}"
            duplicate_number += 1
        row["label"] = candidate
        used.add((key[0], key[1], candidate))


def _trend_report_metadata(path: Path) -> dict[str, Any] | None:
    """Load the optional trend metadata shown by the Streamlit expander."""
    metadata_path = path.parent.parent.parent / "markdown" / path.parent.name / f"{path.stem}_metadata.json"
    try:
        value = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    return {
        key: value[key]
        for key in ("keyword", "date_from", "date_to", "total_papers")
        if key in value and isinstance(value[key], (str, int, float))
    }


def _daily_report_source(path: Path, root: Path) -> str:
    """Recover the source used by a daily-report card.

    New reports may be nested below a source directory while v3/v4 arXiv
    reports sit directly in ``daily_research/html``.  Preference records must
    use the same source key as the SQLite delivery ledger, rather than the
    literal ``html`` directory name of an older report.
    """
    daily_root = root / "daily_research" / "html"
    try:
        relative = path.resolve().relative_to(daily_root.resolve())
    except ValueError:
        return "arxiv"
    if len(relative.parts) > 1:
        return relative.parts[0].strip().lower() or "arxiv"
    match = re.match(r"(.+?)_Report_", path.stem, re.IGNORECASE)
    return (match.group(1).strip().lower() if match else "arxiv") or "arxiv"


def report_file(token: str) -> tuple[Path, str]:
    root = configured_reports_dir()
    path = _report_path(token, root)
    return path, mimetypes.guess_type(path.name)[0] or "text/html"


def report_papers(token: str) -> list[dict[str, Any]]:
    """Expose daily-card identities and their stored preference state."""
    path, _ = report_file(token)
    source = _daily_report_source(path, configured_reports_dir())
    try:
        from utils.legacy_history import parse_legacy_report_file

        cards = parse_legacy_report_file(path, source=source)
    except Exception:
        return []
    rows = []
    for card in cards[:500]:
        paper_id = str(card.get("paper_id") or "").strip()
        title = str(card.get("title") or "").strip()
        if paper_id and title:
            rows.append(
                {
                    "source": str(card.get("source") or source).strip().lower(),
                    "paper_id": paper_id,
                    "canonical_id": card.get("canonical_id"),
                    "version": card.get("version"),
                    "title": title,
                    "authors": list(card.get("authors") or []),
                    "categories": [],
                }
            )
    # Creating the tiny local ledger here makes legacy reports immediately
    # markable, matching Streamlit's in-report controls.  This does not add
    # any paper-delivery history; it only stores an explicit user preference.
    store = open_store(create=True)
    preferences = store.get_preference_map(rows) if store is not None and rows else {}
    for row in rows:
        row["preference"] = preferences.get(
            (str(row["source"]), str(row["paper_id"])), "none"
        )
    return rows


def local_backups() -> list[dict[str, Any]]:
    try:
        return list_local_backups(configured_data_dir())
    except Exception as exc:
        raise ModernWebUIError(f"读取本地备份失败：{exc}") from exc


def _configured_webdav_client(
    settings: Mapping[str, Any] | None = None,
    env_values: Mapping[str, Any] | None = None,
    *,
    allow_unconfigured: bool = False,
) -> Any | None:
    """Build a WebDAV client from the current persisted panel values.

    ``config.settings`` is intentionally a long-lived worker snapshot.  The
    modern panel must instead use the just-saved JSON/.env values for a manual
    test, sync, or backup; otherwise an operator can save new credentials and
    still send the operation to the old endpoint until the container restarts.
    ``allow_unconfigured`` is used by local backup: an incomplete optional
    WebDAV setup must never prevent a healthy local archive from being made.
    """
    flat = dict(settings or flat_config())
    if not _coerce_bool(flat.get("webdav_enabled"), False):
        if allow_unconfigured:
            return None
        raise ModernWebUIError("请先启用 WebDAV 同步。")

    env = env_values if env_values is not None else read_env()
    url = str(env.get("WEBDAV_URL") or "").strip()
    username = str(env.get("WEBDAV_USERNAME") or "").strip()
    password = str(env.get("WEBDAV_PASSWORD") or "")
    remote_path = str(
        flat.get("webdav_remote_path") or "/arxiv-daily-researcher/"
    ).strip()
    if not url or not username:
        if allow_unconfigured:
            return None
        raise ModernWebUIError("WebDAV URL 或用户名尚未配置完整。")

    proxy_url = ""
    if _coerce_bool(flat.get("proxy_enabled"), False) and _coerce_bool(
        flat.get("proxy_webdav"), True
    ):
        proxy_url = str(flat.get("proxy_url") or "").strip()
    try:
        return WebDAVSync(
            url=url,
            username=username,
            password=password,
            remote_path=remote_path,
            proxy_url=proxy_url,
        )
    except (ImportError, OSError, TypeError, ValueError) as exc:
        raise ModernWebUIError(f"创建 WebDAV 客户端失败：{exc}") from exc


def create_local_backup() -> dict[str, Any]:
    """Create the same local snapshot and optional incremental mirror as Streamlit."""
    settings = flat_config()
    try:
        webdav_sync = _configured_webdav_client(settings, allow_unconfigured=True)
        result = create_backup(
            configured_data_dir(settings),
            database=configured_db_path(settings),
            retention_days=int(settings.get("backup_local_retention_days", LOCAL_BACKUP_RETENTION_DAYS)),
            same_day_max_count=int(settings.get("backup_local_same_day_max_count", LOCAL_BACKUP_SAME_DAY_MAX_COUNT)),
            webdav_sync=webdav_sync,
        )
        if _coerce_bool(settings.get("webdav_enabled"), False) and webdav_sync is None:
            result["webdav_skipped"] = "credentials_incomplete"
        return result
    except (OSError, ValueError) as exc:
        raise ModernWebUIError(f"创建本地备份失败：{exc}") from exc


def export_database_backup() -> tuple[bytes, str]:
    settings = flat_config()
    try:
        return export_backup_zip(
            configured_data_dir(settings), database=configured_db_path(settings)
        )
    except (OSError, ValueError) as exc:
        raise ModernWebUIError(f"导出备份失败：{exc}") from exc


def restore_database_backup(content: bytes, filename: str) -> dict[str, Any]:
    if not isinstance(content, bytes) or not content or len(content) > 1024 * 1024 * 1024:
        raise ModernWebUIError("备份文件为空或超过 1 GB 限制。")
    safe_name = Path(str(filename or "backup.zip")).name
    if safe_name.lower().split(".")[-1] not in {"zip", "gz", "db"}:
        raise ModernWebUIError("仅支持 zip、gz 或 db 备份文件。")
    settings = flat_config()
    try:
        return restore_backup_archive(
            configured_data_dir(settings),
            content,
            safe_name,
            database=configured_db_path(settings),
        )
    except (OSError, ValueError) as exc:
        raise ModernWebUIError(f"导入备份失败：{exc}") from exc


def export_configuration() -> tuple[bytes, str]:
    """Export config and .env exactly like the compatibility panel."""
    import io
    import zipfile

    files = [("config.json", DEFAULT_CONFIG_PATH), (".env", DEFAULT_ENV_PATH)]
    present = [(name, path) for name, path in files if path.is_file()]
    if not present:
        raise ModernWebUIError("未找到可导出的配置文件。")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in present:
            archive.write(path, name)
    return buffer.getvalue(), "arxiv_researcher_config.zip"


def webdav_operation(operation: str) -> dict[str, Any]:
    settings = flat_config()
    client = _configured_webdav_client(settings)
    try:
        if operation == "test":
            return {"ok": bool(client.test_connection())}
        if operation == "upload":
            return {
                "ok": True,
                "result": client.sync_all(
                    direction="upload",
                    include_reports=_coerce_bool(settings.get("webdav_sync_reports"), False),
                    include_configs=_coerce_bool(settings.get("webdav_sync_configs"), True),
                    include_history=_coerce_bool(settings.get("webdav_sync_history"), True),
                    include_keywords=_coerce_bool(settings.get("webdav_sync_keywords"), True),
                ),
            }
        if operation == "download":
            return {
                "ok": True,
                "result": client.sync_all(
                    direction="download",
                    include_reports=_coerce_bool(settings.get("webdav_sync_reports"), False),
                    include_configs=_coerce_bool(settings.get("webdav_sync_configs"), True),
                    include_history=_coerce_bool(settings.get("webdav_sync_history"), True),
                    include_keywords=_coerce_bool(settings.get("webdav_sync_keywords"), True),
                ),
            }
    except Exception as exc:
        raise ModernWebUIError(f"WebDAV {operation} 失败：{exc}") from exc
    raise ModernWebUIError("不支持的 WebDAV 操作。")


def connection_test(kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Run a one-off external connection test using the submitted secret.

    Nothing from this endpoint is persisted. This mirrors the Streamlit test
    buttons and avoids making a user press Save just to validate a new key.
    """
    settings = read_env()
    values = dict(payload)
    kind = str(kind)
    try:
        if kind == "cheap_llm":
            ok, message = validate_llm_connection(
                str(values.get("api_key") or settings.get("CHEAP_LLM__API_KEY") or ""),
                str(values.get("base_url") or settings.get("CHEAP_LLM__BASE_URL") or ""),
                str(values.get("model") or settings.get("CHEAP_LLM__MODEL_NAME") or ""),
            )
        elif kind == "smart_llm":
            ok, message = validate_llm_connection(
                str(values.get("api_key") or settings.get("SMART_LLM__API_KEY") or ""),
                str(values.get("base_url") or settings.get("SMART_LLM__BASE_URL") or ""),
                str(values.get("model") or settings.get("SMART_LLM__MODEL_NAME") or ""),
            )
        elif kind == "mineru":
            ok, message = validate_mineru_connection(
                str(values.get("api_key") or settings.get("MINERU_API_KEY") or "")
            )
        elif kind == "openalex":
            ok, message = validate_openalex_connection(
                str(values.get("api_key") or settings.get("OPENALEX_API_KEY") or "")
            )
        elif kind == "semantic_scholar":
            ok, message = validate_semantic_scholar_connection(
                str(values.get("api_key") or settings.get("SEMANTIC_SCHOLAR_API_KEY") or "")
            )
        elif kind == "smtp":
            ok, message = validate_smtp_connection(
                str(values.get("host") or settings.get("SMTP_HOST") or ""),
                int(values.get("port") or settings.get("SMTP_PORT") or 587),
                str(values.get("user") or settings.get("SMTP_USER") or ""),
                str(values.get("password") or settings.get("SMTP_PASSWORD") or ""),
                _coerce_bool(values.get("use_tls", settings.get("SMTP_USE_TLS", "true")), True),
            )
        else:
            raise ModernWebUIError("不支持的连接测试。")
    except (TypeError, ValueError) as exc:
        raise ModernWebUIError(f"连接测试参数无效：{exc}") from exc
    return {"ok": bool(ok), "message": sanitize_task_error_summary(message, max_chars=600)}


def _log_category(name: str) -> str:
    """Classify logs with the same three buckets as the Streamlit viewer."""
    lowered = name.lower()
    if lowered.startswith(("system", "arxiv_researcher")):
        return "system"
    if lowered.startswith(("manual_", "legacy_import_", "history_data_repair_", "history_omission_scan_", "supplement_", "backfill_", "daily_", "cron_", "startup_")):
        return "run"
    return "other"


def _log_group(name: str) -> str:
    category = _log_category(name)
    if category == "system":
        return "系统日志"
    if category == "run":
        return "运行日志"
    return "其他日志"


def list_logs() -> list[dict[str, Any]]:
    if not LOGS_DIR.is_dir():
        return []
    rows = []
    try:
        paths = [path for path in LOGS_DIR.rglob("*.log") if path.is_file()]
    except OSError:
        paths = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        relative = path.relative_to(LOGS_DIR).as_posix()
        rows.append(
            {
                "id": base64.urlsafe_b64encode(relative.encode("utf-8")).decode("ascii").rstrip("="),
                "name": relative,
                "group": _log_group(path.name),
                "category": _log_category(path.name),
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "size_bytes": stat.st_size,
            }
        )
    rows.sort(key=lambda row: row["modified_at"], reverse=True)
    return rows[:500]


def read_log(token: str, *, max_lines: int = 300) -> dict[str, Any]:
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", token):
        raise ModernWebUIError("日志标识无效。")
    try:
        relative = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise ModernWebUIError("日志标识无效。") from None
    path = (LOGS_DIR / relative).resolve()
    try:
        path.relative_to(LOGS_DIR.resolve())
    except ValueError as exc:
        raise ModernWebUIError("日志路径无效。") from exc
    if path.suffix != ".log" or not path.is_file():
        raise ModernWebUIError("日志文件不存在。")
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise ModernWebUIError(f"读取日志失败：{exc}") from exc
    max_lines = max(100, min(int(max_lines), 5_000))
    skipped = max(0, len(lines) - max_lines)
    selected = lines[-max_lines:]
    if skipped:
        selected.insert(0, f"… 已隐藏较早的 {skipped} 行 …")
    return {"name": relative, "content": "\n".join(selected), "truncated": bool(skipped)}

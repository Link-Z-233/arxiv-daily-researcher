"""ASGI backend for the parallel modern daily-research console.

It deliberately reuses the existing `.env` administrator account, validated
trigger queue, run locks, and SQLite ledger.  The UI is therefore a new view
over the same operational backend rather than a second scheduler.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette import status

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from utils.config_io import (  # noqa: E402
    _resolve_project_relative_config_path,
    flatten_config_dict,
    read_config_json,
    read_env,
)
from utils.daily_research_store import DailyResearchStore  # noqa: E402
from utils.run_lock import is_lock_held  # noqa: E402
from utils.webui_trigger import (  # noqa: E402
    enqueue_trigger,
    read_trigger_payload,
    request_stop,
    sanitize_task_error_summary,
    trigger_directory,
    trigger_status_directory,
)
from modern_webui.auth import (  # noqa: E402
    _clear_attempts,
    _configured,
    _record_failed_attempt,
    _remaining_retry_seconds,
    account_session_marker,
    find_account,
    read_auth_config,
    session_secret,
    verify_password_hash,
)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_DEFAULT_DATA_DIR = _PROJECT_ROOT / "data"
_DAILY_DB_RELATIVE_PATH = Path("daily_research") / "daily_research.db"
_DAILY_LOCK_NAMES = (
    "daily_research.lock",
    "legacy_import.lock",
    "history_data_repair.lock",
    "history_omission_scan.lock",
    "supplement_run.lock",
    "backfill_run.lock",
)
_PHASE_LABELS = {
    "prepare": "准备运行",
    "scan": "扫描数据源",
    "score": "评分筛选",
    "analyze": "深度分析",
    "report": "生成报告",
    "legacy_import": "导入旧历史",
    "history_repair": "补全历史数据",
    "history_omission_scan": "扫描历史遗漏",
}
_PID_RE = re.compile(r"(?:^|\b)PID=(\d+)(?:\b|,)")


def _session_secret() -> str:
    """Read the lightweight auth module's stable cookie signing secret."""
    return session_secret(read_auth_config(read_env()))


def _auth_config():
    return read_auth_config(read_env())


def _modern_session_authenticated(request: Request) -> bool:
    config = _auth_config()
    if not config.enabled:
        return True
    if not _configured(config):
        return False
    username = request.session.get("username")
    last_activity = request.session.get("last_activity")
    if not isinstance(last_activity, (int, float)):
        request.session.clear()
        return False
    if time.time() - last_activity > config.session_timeout_minutes * 60:
        request.session.clear()
        return False
    account = find_account(config, username)
    if account is None:
        request.session.clear()
        return False
    marker = request.session.get("account_marker")
    if not isinstance(marker, str) or marker != account_session_marker(account):
        request.session.clear()
        return False
    request.session["last_activity"] = time.time()
    return True


def _require_session(request: Request) -> None:
    config = _auth_config()
    if not config.enabled:
        return
    if not _configured(config):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="管理员账户尚未初始化，请先使用 Streamlit 面板创建账户。",
        )
    if not _modern_session_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")


def _flat_config() -> dict[str, Any]:
    raw = read_config_json()
    return flatten_config_dict(raw) if isinstance(raw, dict) else {}


def _configured_data_dir(flat: dict[str, Any]) -> Path:
    raw = flat.get("data_dir", "data")
    try:
        return _resolve_project_relative_config_path(raw, label="paths.data_dir")
    except (TypeError, ValueError):
        return _DEFAULT_DATA_DIR


def _configured_db_path(flat: dict[str, Any]) -> Path:
    configured = flat.get("daily_research_db_path")
    if isinstance(configured, str) and configured.strip():
        try:
            return _resolve_project_relative_config_path(
                configured, label="daily_research.db_path"
            )
        except ValueError:
            pass
    return _configured_data_dir(flat) / _DAILY_DB_RELATIVE_PATH


def _read_lock_pid(path: Path) -> int | None:
    try:
        match = _PID_RE.search(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return int(match.group(1)) if match else None


def _active_locks(data_dir: Path) -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    seen: set[Path] = set()
    # Trigger requests intentionally remain in the default shared data volume,
    # whereas a user may relocate the worker's real lock directory through
    # paths.data_dir. Inspect both, just like the Streamlit status panel.
    for lock_dir in (data_dir / "run", _DEFAULT_DATA_DIR / "run"):
        for name in _DAILY_LOCK_NAMES:
            path = lock_dir / name
            if path in seen:
                continue
            seen.add(path)
            try:
                held = path.exists() and is_lock_held(path)
            except OSError:
                # A temporarily unreadable shared volume is treated
                # conservatively so the UI never offers a conflicting launch.
                held = path.exists()
            if held:
                active.append({"name": name, "pid": _read_lock_pid(path)})
    return active


def _daily_requests(data_dir: Path) -> list[dict[str, Any]]:
    queue_dir = trigger_directory(data_dir)
    entries: list[dict[str, Any]] = []
    for pattern, state_name in (("*.json", "queued"), ("*.running", "starting")):
        try:
            paths = list(queue_dir.glob(pattern))
        except OSError:
            paths = []
        for path in paths:
            try:
                payload = read_trigger_payload(path)
            except (OSError, ValueError):
                continue
            if payload.get("mode") == "daily_research":
                entries.append(
                    {
                        "request_id": payload["request_id"],
                        "created_at": payload.get("created_at", ""),
                        "state": state_name,
                    }
                )
    return sorted(entries, key=lambda item: item["created_at"], reverse=True)


def _latest_daily_trigger_status(data_dir: Path) -> dict[str, Any] | None:
    status_dir = trigger_status_directory(data_dir)
    try:
        paths = sorted(
            status_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True
        )
    except OSError:
        return None
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("mode") != "daily_research":
            continue
        state_name = str(payload.get("state") or "unknown")
        return {
            "state": state_name,
            "updated_at": str(payload.get("updated_at") or ""),
            "issue": sanitize_task_error_summary(payload.get("error_summary")),
        }
    return None


def _run_status() -> dict[str, Any]:
    flat = _flat_config()
    data_dir = _configured_data_dir(flat)
    db_path = _configured_db_path(flat)
    locks = _active_locks(data_dir)
    pending_requests = _daily_requests(_DEFAULT_DATA_DIR)
    trigger_status = _latest_daily_trigger_status(_DEFAULT_DATA_DIR)

    progress: dict[str, Any] | None = None
    queue = {"total": 0, "failed_retry": 0}
    last_run: dict[str, Any] | None = None
    if db_path.is_file():
        try:
            store = DailyResearchStore(db_path)
            progress = store.active_run_progress()
            queue = store.count_pending_papers()
            recent_runs = store.get_recent_runs(limit=1)
            last_run = recent_runs[0] if recent_runs else None
        except Exception:
            # Status visibility must remain available when a hand-edited
            # database is temporarily unavailable or being restored.
            progress = None

    if isinstance(progress, dict):
        phase = str(progress.get("phase") or "")
        state_name = "running"
        state_label = "正在运行"
        detail = sanitize_task_error_summary(progress.get("detail"), max_chars=180)
        task = {
            "state": state_name,
            "label": state_label,
            "phase": _PHASE_LABELS.get(phase, phase or "处理中"),
            "detail": detail,
            "current": progress.get("current"),
            "total": progress.get("total"),
            "started_at": progress.get("started_at"),
            "run_kind": progress.get("run_kind"),
        }
    elif locks:
        task = {
            "state": "waiting",
            "label": "任务启动中或等待空闲",
            "phase": "正在协调运行锁",
            "detail": "",
            "current": None,
            "total": None,
            "started_at": "",
            "run_kind": "",
        }
    elif pending_requests:
        task = {
            "state": "queued",
            "label": "已加入启动队列",
            "phase": "等待工作进程接手",
            "detail": "",
            "current": None,
            "total": None,
            "started_at": pending_requests[0].get("created_at", ""),
            "run_kind": "daily",
        }
    elif trigger_status and trigger_status["state"] in {"failed", "rejected", "interrupted"}:
        task = {
            "state": trigger_status["state"],
            "label": "上次任务未完成",
            "phase": "请查看问题摘要后重试",
            "detail": trigger_status.get("issue", ""),
            "current": None,
            "total": None,
            "started_at": "",
            "run_kind": "daily",
        }
    else:
        task = {
            "state": "idle",
            "label": "空闲",
            "phase": "可以开始每日研究",
            "detail": "",
            "current": None,
            "total": None,
            "started_at": "",
            "run_kind": "",
        }

    started_at = str(task.get("started_at") or "")
    if last_run and isinstance(last_run.get("completed_at"), str):
        last_run_info = {
            "completed_at": last_run["completed_at"],
            "status": str(last_run.get("status") or ""),
            "total_papers": int(last_run.get("total_papers") or 0),
        }
    else:
        last_run_info = None
    return {
        "task": task,
        "is_active": bool(locks or pending_requests),
        "can_start": not locks and not pending_requests,
        "queue": {
            "pending": int(queue.get("total") or 0),
            "retry": int(queue.get("failed_retry") or 0),
        },
        "last_run": last_run_info,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active_locks": locks,
        "started_at": started_at,
    }


async def health(_request: Request) -> Response:
    """Unauthenticated liveness endpoint for the compose health check."""
    # Reuse the established WebUI readiness contract: the existing health
    # checker deliberately expects this short literal rather than JSON.
    return Response("ok", media_type="text/plain")


async def auth_status(request: Request) -> JSONResponse:
    config = _auth_config()
    configured = _configured(config) if config.enabled else True
    return JSONResponse(
        {
            "enabled": config.enabled,
            "configured": configured,
            "authenticated": _modern_session_authenticated(request) if configured else False,
        }
    )


async def login(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    username = str(payload.get("username") or "")[:64] if isinstance(payload, dict) else ""
    password = str(payload.get("password") or "")[:512] if isinstance(payload, dict) else ""
    config = _auth_config()
    if not config.enabled:
        request.session.clear()
        request.session["username"] = "local"
        request.session["last_activity"] = time.time()
        return JSONResponse({"ok": True})
    if not _configured(config):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="管理员账户尚未初始化，请先使用 Streamlit 面板创建账户。",
        )
    normalized_username = username.strip()
    remaining = _remaining_retry_seconds(normalized_username or config.username)
    if remaining:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"登录尝试过于频繁，请在 {remaining} 秒后重试。",
        )
    account = find_account(config, normalized_username)
    password_ok = (
        verify_password_hash(account.password_hash, password)
        if account is not None
        else False
    )
    if account is None or password_ok is not True:
        _record_failed_attempt(normalized_username or config.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误。")
    _clear_attempts(account.username)
    request.session.clear()
    request.session["username"] = account.username
    request.session["account_marker"] = account_session_marker(account)
    request.session["last_activity"] = time.time()
    return JSONResponse({"ok": True})


async def logout(request: Request) -> Response:
    request.session.clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def daily_status(request: Request) -> JSONResponse:
    _require_session(request)
    return JSONResponse(_run_status())


async def start_daily_research(request: Request) -> JSONResponse:
    _require_session(request)
    state = _run_status()
    if not state["can_start"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="已有任务正在运行或等待工作进程接手。",
        )
    request_path = enqueue_trigger(_DEFAULT_DATA_DIR, "daily_research")
    return JSONResponse(
        {"queued": True, "request_id": request_path.stem.rsplit("_", 1)[-1]}
    )


async def stop_daily_research(request: Request) -> JSONResponse:
    _require_session(request)
    flat = _flat_config()
    locks = _active_locks(_configured_data_dir(flat))
    pids = [lock["pid"] for lock in locks if isinstance(lock.get("pid"), int)]
    if not pids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="没有可停止的 WebUI 任务。",
        )
    data_dir = _DEFAULT_DATA_DIR
    for pid in pids:
        request_stop(data_dir, pid)
    return JSONResponse({"requested": True, "pids": pids})


async def frontend(_request: Request) -> FileResponse:
    """Serve the single-page prototype without exposing filesystem paths."""
    return FileResponse(_STATIC_DIR / "index.html")


app = Starlette(
    routes=[
        Route("/api/health", health, methods=["GET"]),
        Route("/api/auth/status", auth_status, methods=["GET"]),
        Route("/api/auth/login", login, methods=["POST"]),
        Route("/api/auth/logout", logout, methods=["POST"]),
        Route("/api/daily/status", daily_status, methods=["GET"]),
        Route("/api/daily/run", start_daily_research, methods=["POST"]),
        Route("/api/daily/stop", stop_daily_research, methods=["POST"]),
        Mount("/assets", app=StaticFiles(directory=_STATIC_DIR), name="assets"),
        Route("/{path:path}", frontend, methods=["GET"]),
    ]
)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    session_cookie="adr_modern_session",
    max_age=7 * 24 * 60 * 60,
    same_site="strict",
    https_only=False,
)

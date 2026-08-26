"""Validated, durable WebUI-to-worker trigger protocol.

The Streamlit container deliberately contains no worker dependencies.  It
therefore places a small JSON request in the shared data volume and the worker
container's existing watcher executes it.  Requests are written atomically and
validated again in the worker so malformed files cannot turn into shell input.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence


TRIGGER_SCHEMA_VERSION = 1
TRIGGER_DIRECTORY_NAME = "webui_triggers"
TRIGGER_STATUS_DIRECTORY_NAME = "status"
SUPPORTED_MODES = frozenset(
    {
        "daily_research",
        "trend_research",
        "legacy_import",
        "history_data_repair",
        "history_omission_scan",
        "supplement_run",
        "backfill_run",
    }
)
# 面板触发的后台作业：不接受任何参数。
_NO_ARGS_MODES = frozenset(
    {"daily_research", "history_data_repair", "history_omission_scan", "supplement_run"}
)
# 最早可补跑的日期（arXiv 上线年份）。
_BACKFILL_EARLIEST = date(1991, 1, 1)
_CATEGORY_RE = re.compile(r"^[A-Za-z0-9.-]{1,64}$")
_MAX_REQUEST_BYTES = 32 * 1024
_MAX_KEYWORDS = 32
_MAX_KEYWORD_LENGTH = 500
_MAX_CATEGORIES = 64
_MAX_RESULTS = 5000
_MAX_ANALYSIS_PROMPT = 8000


class TriggerValidationError(ValueError):
    """Raised when a WebUI trigger request is not safe to execute."""


def trigger_directory(data_dir: Path) -> Path:
    """Return the queue directory shared by WebUI and the worker."""
    return Path(data_dir) / "run" / TRIGGER_DIRECTORY_NAME


def trigger_status_directory(data_dir: Path) -> Path:
    """Return the small, durable status directory for consumed requests."""
    return trigger_directory(data_dir) / TRIGGER_STATUS_DIRECTORY_NAME


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write one JSON document without exposing a partially written request."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
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
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        os.chmod(path, 0o600)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _validate_text_list(
    value: Any,
    *,
    field: str,
    max_count: int,
    max_length: int,
) -> list[str]:
    if not isinstance(value, list) or not value:
        raise TriggerValidationError(f"{field} must be a non-empty list")
    if len(value) > max_count:
        raise TriggerValidationError(f"{field} contains too many values")

    values: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TriggerValidationError(f"{field} entries must be strings")
        normalized = item.strip()
        if not normalized or len(normalized) > max_length or "\x00" in normalized:
            raise TriggerValidationError(f"{field} contains an invalid value")
        values.append(normalized)
    return values


def _validate_optional_date(value: Any, field: str) -> Optional[str]:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise TriggerValidationError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise TriggerValidationError(f"{field} must be an ISO date") from exc


def _validate_backfill_date(value: Any, field: str) -> str:
    """Validate one past-date queue item accepted by ``backfill_run``."""
    if not isinstance(value, str) or not value.strip():
        raise TriggerValidationError(f"{field} must be an ISO date (YYYY-MM-DD)")
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError as exc:
        raise TriggerValidationError(
            f"{field} must be an ISO date (YYYY-MM-DD)"
        ) from exc
    if parsed >= date.today():
        raise TriggerValidationError(f"{field} must be in the past")
    if parsed < _BACKFILL_EARLIEST:
        raise TriggerValidationError(f"{field} is unreasonably old")
    return parsed.isoformat()


def _validate_request_id(value: Any) -> str:
    if not isinstance(value, str):
        raise TriggerValidationError("request_id must be a UUID")
    try:
        return uuid.UUID(value).hex
    except (ValueError, AttributeError) as exc:
        raise TriggerValidationError("request_id must be a UUID") from exc


def validate_trigger_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a normalized trigger payload or raise before executing anything."""
    if not isinstance(payload, Mapping):
        raise TriggerValidationError("trigger payload must be an object")
    if payload.get("schema_version") != TRIGGER_SCHEMA_VERSION:
        raise TriggerValidationError("unsupported trigger schema version")

    mode = payload.get("mode")
    if mode not in SUPPORTED_MODES:
        raise TriggerValidationError(f"unsupported trigger mode: {mode!r}")

    args = payload.get("args", {})
    if not isinstance(args, Mapping):
        raise TriggerValidationError("trigger args must be an object")

    normalized: Dict[str, Any] = {
        "schema_version": TRIGGER_SCHEMA_VERSION,
        "request_id": _validate_request_id(payload.get("request_id")),
        "created_at": str(payload.get("created_at", "")),
        "mode": mode,
        "args": {},
    }

    if mode in _NO_ARGS_MODES:
        if args:
            raise TriggerValidationError(f"{mode} does not accept trigger arguments")
        return normalized

    if mode == "legacy_import":
        unexpected = set(args).difference({"full_repair"})
        if unexpected:
            raise TriggerValidationError("legacy_import contains unsupported arguments")
        # Empty arguments retain compatibility with queued requests created by
        # earlier v4 releases and let the worker use its saved configuration.
        # The current WebUI always sends an explicit value so a click honors
        # an unsaved toggle state as well.
        if "full_repair" not in args:
            return normalized
        enabled = args.get("full_repair")
        if not isinstance(enabled, bool):
            raise TriggerValidationError("legacy_import.full_repair must be a boolean")
        normalized["args"] = {"full_repair": enabled}
        return normalized

    if mode == "backfill_run":
        allowed = {"target_date", "date_from", "date_to"}
        unexpected = set(args).difference(allowed)
        if unexpected:
            raise TriggerValidationError("backfill_run contains unsupported arguments")

        target_date = args.get("target_date")
        date_from = args.get("date_from")
        date_to = args.get("date_to")
        has_target = target_date not in (None, "")
        has_range = date_from not in (None, "") or date_to not in (None, "")
        if has_target and has_range:
            raise TriggerValidationError(
                "backfill_run cannot mix target_date with date_from/date_to"
            )
        if has_target:
            normalized["args"] = {
                "target_date": _validate_backfill_date(target_date, "target_date")
            }
            return normalized
        if not has_range:
            raise TriggerValidationError(
                "backfill_run requires target_date or date_from/date_to"
            )
        start = _validate_backfill_date(date_from, "date_from")
        end = _validate_backfill_date(date_to, "date_to")
        if start > end:
            raise TriggerValidationError("date_from must not be after date_to")
        normalized["args"] = {"date_from": start, "date_to": end}
        return normalized

    keywords = _validate_text_list(
        args.get("keywords"),
        field="keywords",
        max_count=_MAX_KEYWORDS,
        max_length=_MAX_KEYWORD_LENGTH,
    )
    categories_raw = args.get("categories", [])
    if not isinstance(categories_raw, list) or len(categories_raw) > _MAX_CATEGORIES:
        raise TriggerValidationError("categories must be a bounded list")
    categories: list[str] = []
    for category in categories_raw:
        if not isinstance(category, str) or not _CATEGORY_RE.fullmatch(category.strip()):
            raise TriggerValidationError("categories contains an invalid category")
        categories.append(category.strip())

    sort_order = args.get("sort_order", "ascending")
    if sort_order not in {"ascending", "descending"}:
        raise TriggerValidationError("sort_order must be ascending or descending")

    max_results = args.get("max_results")
    if isinstance(max_results, bool) or not isinstance(max_results, int):
        raise TriggerValidationError("max_results must be an integer")
    if not 1 <= max_results <= _MAX_RESULTS:
        raise TriggerValidationError(f"max_results must be between 1 and {_MAX_RESULTS}")

    date_from = _validate_optional_date(args.get("date_from"), "date_from")
    date_to = _validate_optional_date(args.get("date_to"), "date_to")
    if date_from and date_to and date_from > date_to:
        raise TriggerValidationError("date_from must not be after date_to")

    analysis_prompt = args.get("analysis_prompt", "")
    if analysis_prompt is None:
        analysis_prompt = ""
    if not isinstance(analysis_prompt, str):
        raise TriggerValidationError("analysis_prompt must be a string")
    analysis_prompt = analysis_prompt.strip()
    if len(analysis_prompt) > _MAX_ANALYSIS_PROMPT:
        raise TriggerValidationError(
            f"analysis_prompt must be at most {_MAX_ANALYSIS_PROMPT} characters"
        )

    normalized["args"] = {
        "keywords": keywords,
        "date_from": date_from,
        "date_to": date_to,
        "categories": categories,
        "sort_order": sort_order,
        "max_results": max_results,
        "analysis_prompt": analysis_prompt,
    }
    return normalized


def build_trigger_payload(mode: str, **args: Any) -> Dict[str, Any]:
    """Create a normalized request payload for the Streamlit container."""
    payload: Dict[str, Any] = {
        "schema_version": TRIGGER_SCHEMA_VERSION,
        "request_id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "args": args,
    }
    return validate_trigger_payload(payload)


def enqueue_trigger(data_dir: Path, mode: str, **args: Any) -> Path:
    """Atomically enqueue one validated worker request and return its path."""
    payload = build_trigger_payload(mode, **args)
    queue_dir = trigger_directory(Path(data_dir))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    request_path = queue_dir / f"{timestamp}_{payload['request_id']}.json"
    _atomic_write_json(request_path, payload)
    return request_path


def read_trigger_payload(request_path: Path) -> Dict[str, Any]:
    """Load and validate a queued request with a strict size limit."""
    request_path = Path(request_path)
    try:
        if request_path.stat().st_size > _MAX_REQUEST_BYTES:
            raise TriggerValidationError("trigger request exceeds size limit")
        with request_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise TriggerValidationError("trigger request is not valid JSON") from exc
    return validate_trigger_payload(payload)


def build_main_command(payload: Mapping[str, Any], project_root: Path) -> list[str]:
    """Build a list-only command; untrusted request text is never shell-expanded."""
    request = validate_trigger_payload(payload)
    command = [sys.executable, str(Path(project_root) / "main.py"), "--mode", request["mode"]]
    if request["mode"] == "legacy_import":
        if "full_repair" in request["args"]:
            command.append(
                "--legacy-full-repair"
                if request["args"]["full_repair"]
                else "--no-legacy-full-repair"
            )
    if request["mode"] == "backfill_run":
        args = request["args"]
        if args.get("target_date"):
            command.extend(["--target-date", args["target_date"]])
        else:
            command.extend(["--date-from", args["date_from"], "--date-to", args["date_to"]])
    if request["mode"] == "trend_research":
        args = request["args"]
        command.extend(["--keywords", *args["keywords"]])
        if args["date_from"]:
            command.extend(["--date-from", args["date_from"]])
        if args["date_to"]:
            command.extend(["--date-to", args["date_to"]])
        if args["categories"]:
            command.extend(["--categories", *args["categories"]])
        command.extend(["--sort-order", args["sort_order"], "--max-results", str(args["max_results"])])
        if args.get("analysis_prompt"):
            command.extend(["--analysis-prompt", args["analysis_prompt"]])
    return command


def _write_status(data_dir: Path, payload: Mapping[str, Any], state: str, **details: Any) -> Path:
    status_payload: Dict[str, Any] = {
        "request_id": payload["request_id"],
        "mode": payload["mode"],
        "created_at": payload.get("created_at", ""),
        "state": state,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **details,
    }
    status_path = trigger_status_directory(data_dir) / f"{payload['request_id']}.json"
    _atomic_write_json(status_path, status_payload)
    return status_path


def _write_pid_file(pid_file: Path, pid: int) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = pid_file.with_name(f".{pid_file.name}.{uuid.uuid4().hex}.tmp")
    try:
        # Include a process-start token so another namespace cannot mistake a
        # recycled numeric PID for this worker child.  Local UI code uses this
        # only for status; the worker still owns all lifecycle decisions.
        started_at = datetime.now(timezone.utc).isoformat()
        temporary_path.write_text(
            f"PID={pid}, started={started_at}\n", encoding="utf-8"
        )
        os.replace(temporary_path, pid_file)
    finally:
        temporary_path.unlink(missing_ok=True)


def _remove_own_pid_file(pid_file: Optional[Path], pid: Optional[int]) -> None:
    if pid_file is None or pid is None:
        return
    try:
        content = pid_file.read_text(encoding="utf-8").strip()
        if content == str(pid) or re.search(rf"(?:^|\b)PID={re.escape(str(pid))}(?:\b|,)", content):
            pid_file.unlink(missing_ok=True)
    except OSError:
        pass


def stop_request_directory(data_dir: Path) -> Path:
    """Stop requests are small JSON files under ``<data>/run/stop_requests``."""
    directory = Path(data_dir) / "run" / "stop_requests"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def request_stop(data_dir: Path, pid: int) -> Path:
    """Atomically ask the worker to stop the run owning ``pid`` (best effort)."""
    target = stop_request_directory(data_dir) / f"stop_{int(pid)}.json"
    _atomic_write_json(
        target,
        {
            "schema_version": 1,
            "pid": int(pid),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return target


def _monitor_stop_requests(
    child: subprocess.Popen,
    data_dir: Path,
    *,
    poll_seconds: float = 2.0,
) -> None:
    """Watch the shared stop-request directory and SIGTERM the child on match.

    Runs as a daemon thread next to ``child.wait()``.  ``main.py`` maps
    SIGTERM to its interrupt path, so the pipeline records an interrupted
    state and its durable queue keeps already-completed stages.  The consumed
    request is removed; requests for other PIDs are left for their owners.
    """
    directory = stop_request_directory(data_dir)
    while child.poll() is None:
        try:
            for request in directory.glob("stop_*.json"):
                try:
                    payload = json.loads(request.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if payload.get("pid") == child.pid:
                    request.unlink(missing_ok=True)
                    print(
                        f"[webui-trigger] Stop requested for PID {child.pid}; sending SIGTERM",
                        file=sys.stderr,
                    )
                    child.send_signal(signal.SIGTERM)
                    return
        except OSError:
            pass
        time.sleep(poll_seconds)


def execute_trigger_request(
    request_path: Path,
    *,
    project_root: Optional[Path] = None,
    pid_file: Optional[Path] = None,
) -> int:
    """Execute one claimed request and persist a terminal status for the UI.

    ``request_path`` is expected to have been atomically renamed from ``.json``
    to ``.running`` by the worker watcher.  The request is removed only after a
    terminal status has been written, so a completed action has visible audit
    evidence without leaving queue files to be executed twice.
    """
    request_path = Path(request_path)
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[2]
    data_dir = request_path.parent.parent.parent
    payload: Optional[Dict[str, Any]] = None
    child: Optional[subprocess.Popen] = None

    try:
        payload = read_trigger_payload(request_path)
        main_path = root / "main.py"
        if not main_path.is_file():
            raise RuntimeError(f"worker entrypoint is unavailable: {main_path}")

        command = build_main_command(payload, root)
        _write_status(data_dir, payload, "running", command=command)
        child = subprocess.Popen(command, cwd=str(root))
        stop_monitor = threading.Thread(
            target=_monitor_stop_requests,
            args=(child, data_dir),
            daemon=True,
            name=f"stop-monitor-{child.pid}",
        )
        stop_monitor.start()
        if pid_file is not None:
            _write_pid_file(Path(pid_file), child.pid)
        return_code = child.wait()
        if return_code == 0:
            state = "succeeded"
        elif return_code == 130:
            # main.py maps SIGTERM to its interrupt path; distinguish it from
            # a genuine failure so the UI can explain what happened.
            state = "interrupted"
        elif return_code == 75:
            # run_lock: the same task was already active, so nothing ran.
            state = "skipped_busy"
        else:
            state = "failed"
        _write_status(data_dir, payload, state, return_code=return_code, command=command)
        return return_code
    except KeyboardInterrupt:
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if payload is not None:
            _write_status(data_dir, payload, "interrupted", return_code=130)
        return 130
    except Exception as exc:
        if payload is not None:
            _write_status(data_dir, payload, "rejected", error=str(exc)[:4000])
        else:
            # A malformed request has no trustworthy request_id.  Keep a small
            # status record for diagnostics, then consume it so the watcher
            # cannot spin forever on the same invalid input.  Keep it outside
            # the queue directory: a ``*.json`` record next to the request
            # would itself be mistaken for another request by the watcher.
            error_path = trigger_status_directory(data_dir) / f"rejected_{uuid.uuid4().hex}.json"
            _atomic_write_json(
                error_path,
                {
                    "state": "rejected",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "error": str(exc)[:4000],
                },
            )
        print(f"[webui-trigger] Request failed: {exc}", file=sys.stderr)
        return 1
    finally:
        _remove_own_pid_file(Path(pid_file) if pid_file is not None else None, child.pid if child else None)
        request_path.unlink(missing_ok=True)


def _parse_cli(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute one validated WebUI trigger request")
    parser.add_argument("request_path", type=Path, help="Claimed .running request file")
    parser.add_argument("--pid-file", type=Path, default=None, help="Shared current worker PID file")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_cli(argv)
    return execute_trigger_request(args.request_path, pid_file=args.pid_file)


if __name__ == "__main__":
    sys.exit(main())

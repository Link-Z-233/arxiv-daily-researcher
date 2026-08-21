"""
任务并发锁 — 防止完全相同的任务同时执行。

使用文件独占锁（fcntl.LOCK_EX | LOCK_NB），进程退出后锁自动释放。
锁文件本身会保留为稳定的锁锚点；不能根据其中的 PID 删除它，也不能
为“超龄”任务发送信号，因为 PID 可能已经被无关进程复用。

锁文件目录：data/run/
  daily_research.lock                — 每日研究（同时只允许一个）
  trend_research_<params_hash>.lock  — 趋势研究（相同参数同时只允许一个）
"""

import fcntl
import hashlib
import os
import re
import signal
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# 专用退出码：相同任务已在运行而被跳过（沿用 EX_TEMPFAIL 惯例取值）。
# WebUI 触发链路据此区分“真的跑完”和“被锁跳过”；cron 直接调用时
# 非零返回码只进日志，无副作用。
LOCK_SKIPPED_EXIT_CODE = 75


def _lock_dir() -> Path:
    try:
        from config import settings

        d = Path(settings.DATA_DIR) / "run"
    except Exception:
        d = Path("data/run")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _params_hash(
    keywords: List[str],
    date_from,
    date_to,
    categories: Optional[List[str]],
) -> str:
    key = "|".join(
        [
            ",".join(sorted(str(k) for k in keywords)),
            str(date_from),
            str(date_to),
            ",".join(sorted(str(c) for c in (categories or []))),
        ]
    )
    return hashlib.md5(key.encode()).hexdigest()[:8]


def _parse_lock_info(content: str):
    """Parse diagnostic PID and start time; neither is an authority to kill."""
    pid = None
    started_at = None

    m_pid = re.search(r"PID=(\d+)", content or "")
    if m_pid:
        pid = int(m_pid.group(1))

    started_pattern = r"started=([0-9]{4}-[0-9]{2}-[0-9]{2} " r"[0-9]{2}:[0-9]{2}:[0-9]{2})"
    m_started = re.search(started_pattern, content or "")
    if m_started:
        try:
            started_at = datetime.strptime(m_started.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            started_at = None

    return pid, started_at


def _expired_lock_message(lock_file, task_desc: str, max_age_hours: int) -> Optional[str]:
    """Return a safe diagnostic message for an unusually long held lock.

    ``flock`` is the sole source of truth for mutual exclusion.  A timestamp
    and PID are only human-readable diagnostics: a PID can be reused and a
    valid paper/LLM job can legitimately outlive its estimated duration.
    """
    if max_age_hours <= 0:
        return None

    try:
        lock_file.seek(0)
        info = lock_file.read().strip()
    except Exception:
        return None

    pid, started_at = _parse_lock_info(info)
    if not started_at:
        return None

    age_seconds = (datetime.now() - started_at).total_seconds()
    if age_seconds <= max_age_hours * 3600:
        return None

    pid_detail = f" PID={pid}" if pid is not None else ""
    return (
        f"⚠️  检测到超龄运行锁（>{max_age_hours}h），任务仍持有内核锁，"
        f"本次不启动: {task_desc}{pid_detail}。为避免 PID 复用误杀，不会自动终止进程。"
    )


def is_lock_held(lock_path: Path) -> bool:
    """Safely check a lock's current kernel state without trusting its PID.

    This is useful for diagnostics/UI.  Do not delete a lock file based on the
    result: retaining one stable inode avoids a race where a new process locks
    a replacement file while another process still owns the old inode.
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.open("a+")
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            return True
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            return False
    finally:
        lock_file.close()


@contextmanager
def run_lock(
    mode: str,
    keywords: Optional[List[str]] = None,
    date_from=None,
    date_to=None,
    categories: Optional[List[str]] = None,
):
    """
    获取运行锁；若相同任务已在运行则打印提示并以 exit(0) 退出。

    用法:
        with run_lock("daily_research"):
            DailyResearchPipeline().run()

        with run_lock(
            "trend_research", keywords=[...], date_from=..., date_to=..., categories=[...]
        ):
            TrendResearchPipeline(...).run()
    """
    if mode == "trend_research" and keywords:
        h = _params_hash(keywords, date_from, date_to, categories)
        fname = f"trend_research_{h}.lock"
        task_desc = f"trend_research [keywords={keywords}, {date_from}~{date_to}"
        if categories:
            task_desc += f", categories={categories}"
        task_desc += "]"
    else:
        fname = f"{mode}.lock"
        task_desc = mode

    lock_path = _lock_dir() / fname

    try:
        from config import settings

        max_age_hours = int(getattr(settings, "RUN_LOCK_MAX_AGE_HOURS", 12))
    except Exception:
        max_age_hours = 12

    # Keep the path stable across runs.  The operating system releases flock
    # automatically after a SIGKILL, so a leftover diagnostic file is harmless.
    lock_file = open(lock_path, "a+")

    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        try:
            lock_file.seek(0)
            info = lock_file.read().strip()
        except Exception:
            info = ""
        expired_message = _expired_lock_message(lock_file, task_desc, max_age_hours)
        lock_file.close()

        print("\n⚠️  相同任务正在运行中，跳过本次执行")
        print(f"   任务: {task_desc}")
        if info:
            print(f"   运行信息: {info}")
        if expired_message:
            print(f"   {expired_message}")
        print(f"   锁文件: {lock_path}\n")
        sys.exit(LOCK_SKIPPED_EXIT_CODE)

    # 写入诊断信息方便排查
    try:
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(
            f"PID={os.getpid()}, started={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        lock_file.flush()
    except Exception:
        pass

    # Turn SIGTERM into KeyboardInterrupt so pipeline-level interruption
    # handling can persist failed state before the context manager releases the
    # flock.  ``main.py`` maps an uncaught interruption to exit code 130 rather
    # than incorrectly reporting a successful scheduled run.
    _old_sigterm = signal.getsignal(signal.SIGTERM)

    def _sigterm_handler(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, _old_sigterm)
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
        except Exception:
            pass

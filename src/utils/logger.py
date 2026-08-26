import logging
import sys
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

# 尝试从配置中导入settings，以获取绝对路径
# 如果导入失败（比如单独测试这个文件时），则回退到当前目录
try:
    from config import settings

    LOG_DIR = settings.PROJECT_ROOT / "logs"
except ImportError:
    LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


# ``setup_logger`` intentionally gives the main pipelines their own console
# and system-log handlers with ``propagate=False``. Keep track of those named
# loggers so a per-run log can receive their records too; otherwise a
# ``daily_*.log`` only contains child loggers that happened to propagate to
# root, while the useful pipeline stage messages stay in ``system.log``.
_CONFIGURED_LOGGER_NAMES: set[str] = set()
_ACTIVE_RUN_HANDLERS: list[logging.Handler] = []


def _attach_active_run_handlers(logger: logging.Logger) -> None:
    """Attach the current run handlers to one dedicated non-propagating logger."""
    for handler in _ACTIVE_RUN_HANDLERS:
        if handler not in logger.handlers:
            logger.addHandler(handler)


def _clear_active_run_handlers() -> None:
    """Detach tracked per-run handlers before a later run in the same process."""
    if not _ACTIVE_RUN_HANDLERS:
        return
    targets = [logging.getLogger()]
    targets.extend(logging.getLogger(name) for name in _CONFIGURED_LOGGER_NAMES)
    for handler in _ACTIVE_RUN_HANDLERS:
        for target in targets:
            if handler in target.handlers:
                target.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    _ACTIVE_RUN_HANDLERS.clear()


def _get_log_config():
    """从 settings 获取日志配置，失败时返回默认值。"""
    try:
        from config import settings as _s

        return _s.LOG_ROTATION_TYPE, _s.LOG_KEEP_DAYS
    except Exception:
        return "time", 30


def _add_console_handler(logger: logging.Logger, formatter: logging.Formatter) -> None:
    """Attach stdout logging before attempting optional file logging."""
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)


def setup_logger(name: str = "ArxivResearcher"):
    """
    配置并返回一个具有控制台和文件输出的Logger实例。

    参数:
        name (str): 日志记录器的名称，默认为"ArxivResearcher"

    返回值:
        logging.Logger: 配置好的Logger对象

    功能说明:
        - 日志会同时输出到控制台和文件
        - 控制台输出为INFO级别及以上
        - 文件日志支持两种轮转模式（通过 LOG_ROTATION_TYPE 配置）:
          - "time": 按天轮转，保留 LOG_KEEP_DAYS 天（默认）
          - "size": 按大小轮转，单个文件最大5MB，保留3个备份
        - 日志格式包含时间、级别、模块名和消息内容
    """
    # 1. 创建Logger对象
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _CONFIGURED_LOGGER_NAMES.add(name)

    # 防止重复添加Handler（Jupyter或多次调用时稀有问题）
    if logger.handlers:
        _attach_active_run_handlers(logger)
        return logger

    # 2. 定义日志格式
    # 格式：[时间] [日志级别] [模块名] - 消息
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 3. 控制台日志必须始终可用；文件日志只是附加能力。
    _add_console_handler(logger, formatter)

    # 4. 文件日志（支持按时间或按大小轮转）。容器以 root 写入
    # 挂载目录后，宿主用户可能无法再打开 system.log；这不能导致
    # 日报流程在模块导入阶段直接失败。
    rotation_type, keep_days = _get_log_config()
    log_file_path = LOG_DIR / "system.log"
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if rotation_type == "time":
            file_handler = TimedRotatingFileHandler(
                log_file_path,
                when="midnight",
                backupCount=keep_days,
                encoding="utf-8",
            )
            file_handler.suffix = "%Y-%m-%d"
        else:
            file_handler = RotatingFileHandler(
                log_file_path,
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning("无法写入系统日志 %s，已降级为仅控制台日志: %s", log_file_path, exc)

    _attach_active_run_handlers(logger)

    return logger


def setup_run_log(mode: str = "daily_research") -> Optional[Path]:
    """
    创建一次运行专用的日志文件，并为根 logger 添加对应的 FileHandler。

    命名规则（与 entrypoint.sh 的 cron/startup 日志对齐）:
      - daily_research  → logs/daily_YYYYMMDD_HHMMSS.log
      - trend_research  → logs/trend_YYYYMMDD_HHMMSS.log

    返回:
        日志文件路径；不可写时返回 None。
    """
    prefix_map = {
        "daily_research": "daily",
        "trend_research": "trend",
    }
    prefix = prefix_map.get(mode, mode)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"{prefix}_{timestamp}.log"

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(formatter)
        handler.setLevel(logging.INFO)

        # Add to root for ordinary child loggers, then explicitly attach it
        # to pipeline loggers created by ``setup_logger``. Those loggers use
        # propagate=False to avoid duplicating system logs, so root alone is
        # insufficient for a complete per-run diagnostic file.
        _clear_active_run_handlers()
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(handler)
        _ACTIVE_RUN_HANDLERS.append(handler)
        for logger_name in _CONFIGURED_LOGGER_NAMES:
            _attach_active_run_handlers(logging.getLogger(logger_name))
    except OSError as exc:
        logging.getLogger("Main").warning(
            "无法创建本次运行日志 %s，已继续执行并仅输出到控制台: %s", log_file, exc
        )
        return None

    # 抑制第三方库的噪音日志，只保留警告及以上
    for noisy in ("httpx", "httpcore", "arxiv", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return log_file

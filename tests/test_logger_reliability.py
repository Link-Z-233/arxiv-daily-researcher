import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils import logger as logger_module  # noqa: E402


class LoggerReliabilityTests(unittest.TestCase):
    def test_system_logger_falls_back_to_console_when_file_is_not_writable(self):
        logger_name = "test_logger_read_only_file"
        logging.getLogger(logger_name).handlers.clear()

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            logger_module, "LOG_DIR", Path(temp_dir)
        ), patch.object(
            logger_module.TimedRotatingFileHandler,
            "__init__",
            side_effect=PermissionError("read-only log file"),
        ):
            logger = logger_module.setup_logger(logger_name)

        self.assertTrue(any(type(handler) is logging.StreamHandler for handler in logger.handlers))
        self.assertFalse(any(isinstance(handler, logging.FileHandler) for handler in logger.handlers))
        logger.handlers.clear()

    def test_run_log_failure_does_not_block_the_process(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            logger_module, "LOG_DIR", Path(temp_dir)
        ), patch.object(logger_module.logging, "FileHandler", side_effect=PermissionError("read-only")):
            self.assertIsNone(logger_module.setup_run_log("daily_research"))

    def test_run_log_captures_dedicated_pipeline_loggers(self):
        before_name = "test_pipeline_logger_before_run_handler"
        after_name = "test_pipeline_logger_after_run_handler"
        for name in (before_name, after_name):
            test_logger = logging.getLogger(name)
            for handler in list(test_logger.handlers):
                test_logger.removeHandler(handler)
                handler.close()

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            logger_module, "LOG_DIR", Path(temp_dir)
        ):
            before = logger_module.setup_logger(before_name)
            log_path = logger_module.setup_run_log("daily_research")
            self.assertIsNotNone(log_path)
            before.info("message from an already configured pipeline")

            after = logger_module.setup_logger(after_name)
            after.info("message from a later configured pipeline")
            for handler in logger_module._ACTIVE_RUN_HANDLERS:
                handler.flush()
            content = Path(log_path).read_text(encoding="utf-8")

        self.assertIn("already configured pipeline", content)
        self.assertIn("later configured pipeline", content)
        logger_module._clear_active_run_handlers()
        for name in (before_name, after_name):
            test_logger = logging.getLogger(name)
            for handler in list(test_logger.handlers):
                test_logger.removeHandler(handler)
                handler.close()


if __name__ == "__main__":
    unittest.main()

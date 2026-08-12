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


if __name__ == "__main__":
    unittest.main()

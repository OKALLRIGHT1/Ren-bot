from pathlib import Path
from unittest.mock import Mock

import pytest

from core.logger import AppLogger


@pytest.mark.parametrize(
    "level",
    ["debug", "info", "warning", "error", "critical", "exception"],
)
def test_app_logger_forwards_standard_logging_format_args(level: str):
    logger = AppLogger()
    logger.logger = Mock()

    getattr(logger, level)("embedding unavailable: %s", "offline")

    getattr(logger.logger, level).assert_called_once_with(
        "embedding unavailable: %s",
        "offline",
    )


def test_application_coroutine_error_label_is_readable_chinese():
    source = (
        Path(__file__).resolve().parents[1] / "core" / "application.py"
    ).read_text(encoding="utf-8")

    assert "协程异常" in source
    assert "鍗忕▼寮傚父" not in source

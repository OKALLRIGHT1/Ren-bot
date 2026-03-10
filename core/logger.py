"""
Unified logging setup with safe fallbacks.
"""
import logging
import sys
from pathlib import Path
from typing import Optional
from logging.handlers import RotatingFileHandler


class AppLogger:
    """Application logger manager."""

    def __init__(self, log_dir: str = "./logs", log_name: str = "app", level: str = "INFO"):
        self.log_dir = Path(log_dir)
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            # If log dir is not writable, keep running with cwd fallback.
            self.log_dir = Path(".")

        self.log_name = log_name
        self.level = getattr(logging, level.upper(), logging.INFO)

        self.logger = None
        self.log_file = None

    def setup(self) -> str:
        """Configure logging and never fail application startup."""
        logging.raiseExceptions = False

        log_file = self.log_dir / f"{self.log_name}.log"
        self.log_file = str(log_file)

        self.logger = logging.getLogger(self.log_name)
        self.logger.setLevel(self.level)

        # Prevent duplicate handlers across repeated setup calls.
        if self.logger.handlers:
            return self.log_file

        file_handler = None
        try:
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setLevel(self.level)
        except Exception as e:
            print(f"[Log] file logging disabled: {e}")

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(self.level)

        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        console_handler.setFormatter(formatter)
        if file_handler:
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

        return self.log_file

    def info(self, msg: str, **kwargs):
        """Log INFO level message."""
        if self.logger:
            self.logger.info(msg, **kwargs)

    def warning(self, msg: str, **kwargs):
        """Log WARNING level message."""
        if self.logger:
            self.logger.warning(msg, **kwargs)

    def error(self, msg: str, **kwargs):
        """Log ERROR level message."""
        if self.logger:
            self.logger.error(msg, **kwargs)

    def debug(self, msg: str, **kwargs):
        """Log DEBUG level message."""
        if self.logger:
            self.logger.debug(msg, **kwargs)

    def critical(self, msg: str, **kwargs):
        """Log CRITICAL level message."""
        if self.logger:
            self.logger.critical(msg, **kwargs)


def setup_logging(log_dir: str = "./logs", log_name: str = "app", level: str = "INFO") -> AppLogger:
    """Convenience function to initialize logging."""
    logger_manager = AppLogger(log_dir, log_name, level)
    log_file = logger_manager.setup()
    print(f"[Log] logging initialized: {log_file}")
    return logger_manager


# Default logger instance
default_logger = None


def get_logger(name: Optional[str] = None) -> AppLogger:
    """Get default AppLogger instance."""
    global default_logger

    if default_logger is None:
        default_logger = AppLogger()
        default_logger.setup()

    return default_logger


def set_logger(logger: AppLogger) -> AppLogger:
    """Set global default logger instance for compatibility."""
    global default_logger
    if isinstance(logger, AppLogger):
        default_logger = logger
    return default_logger

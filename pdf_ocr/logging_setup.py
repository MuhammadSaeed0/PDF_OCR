"""Logging configuration: rotating log file, optional console output and extra handlers (GUI)."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

from .config import AppConfig, ConfigError

LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_OWNED = "_pdf_ocr_owned"


def make_formatter() -> logging.Formatter:
    return logging.Formatter(LOG_FORMAT, DATE_FORMAT)


def setup_logging(
    config: AppConfig,
    console: bool = True,
    verbose: bool = False,
    extra_handlers: Iterable[logging.Handler] = (),
) -> Path:
    """(Re)configure the root logger. Safe to call more than once (the GUI does on every start)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _OWNED, False):
            root.removeHandler(handler)
            handler.close()

    root.setLevel(logging.DEBUG if verbose else getattr(logging, config.log_level, logging.INFO))
    try:
        config.log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"Cannot create log folder {config.log_dir}: {exc}") from exc

    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            config.log_file,
            maxBytes=config.max_log_size_mb * 1024 * 1024,
            backupCount=config.log_backup_count,
            encoding="utf-8",
            delay=True,
        )
    ]
    if console and sys.stdout is not None:
        try:
            # Never crash on file names the console code page cannot display (e.g. Arabic).
            sys.stdout.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
        handlers.append(logging.StreamHandler(sys.stdout))
    handlers.extend(extra_handlers)

    formatter = make_formatter()
    for handler in handlers:
        handler.setFormatter(formatter)
        setattr(handler, _OWNED, True)
        root.addHandler(handler)
    return config.log_file

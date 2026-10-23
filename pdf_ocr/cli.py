"""Command-line entry point (used by main.py and run.bat)."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

from . import __version__
from .config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from .logging_setup import setup_logging
from .monitor import Monitor

logger = logging.getLogger("pdf_ocr")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Watch a folder of scanned PDFs and save searchable (OCR) copies to another folder.",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH),
                        help="path of the configuration file (default: config.ini next to main.py)")
    parser.add_argument("--gui", action="store_true", help="open the graphical interface")
    parser.add_argument("--once", action="store_true",
                        help="process everything that is pending, then exit instead of monitoring")
    parser.add_argument("--retry-failed", action="store_true",
                        help="retry files that failed before, without waiting for their retry time")
    parser.add_argument("--source", help="override the source folder from config.ini")
    parser.add_argument("--dest", help="override the destination folder from config.ini")
    parser.add_argument("-v", "--verbose", action="store_true", help="log debug details")
    parser.add_argument("--version", action="version", version=f"PDF OCR {__version__}")
    return parser


def _install_signal_handlers(monitor: Monitor) -> None:
    def handle(signum, _frame) -> None:
        logger.info("Signal %s received; stopping...", signum)
        monitor.stop()

    for name in ("SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, handle)
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.gui:
        from .gui import run_gui

        return run_gui(Path(args.config))

    try:
        config = load_config(args.config, source_override=args.source, destination_override=args.dest)
        setup_logging(config, console=True, verbose=args.verbose)
    except ConfigError as exc:
        if sys.stderr is not None:
            print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logger.info("PDF OCR %s starting (config: %s, log: %s)", __version__, config.config_path, config.log_file)
    monitor = Monitor(config, once=args.once, retry_failed=args.retry_failed)
    _install_signal_handlers(monitor)
    monitor.start()
    try:
        while monitor.is_running():
            monitor.join(0.5)
    except KeyboardInterrupt:
        logger.info("Ctrl+C received; stopping. A file being processed now will be redone on the next start.")
        monitor.stop()
        while monitor.is_running():
            try:
                monitor.join(0.5)
            except KeyboardInterrupt:
                pass
    return monitor.exit_code

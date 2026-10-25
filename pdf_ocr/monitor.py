"""Background monitoring: periodic scans of the source folder, one-at-a-time OCR,
status tracking for the command line and GUI, and graceful shutdown."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from .config import AppConfig, ConfigError
from .dependencies import preflight
from .processor import HOLD, PROCESS, REASONS, SKIP, WAIT, Decision, Outcome, Processor, SourceFile
from .state import InstanceLock, StateStore

logger = logging.getLogger(__name__)

WAITING = "waiting"
PROCESSING = "processing"
COMPLETED = "completed"
FAILED = "failed"
STATUSES = (WAITING, PROCESSING, COMPLETED, FAILED)

# State records of deleted source files are kept this long before being purged.
STALE_RECORD_SECONDS = 30 * 86400

# Log prefixes per reason, so the log reads like a status report.
_REASON_TAGS = {
    "new": "WAITING",
    "source_changed": "REPROCESS",
    "output_deleted": "REGENERATE",
    "output_invalid": "REGENERATE",
    "ocr_upgraded": "REPROCESS",
    "retry": "RETRY",
}


@dataclass
class FileView:
    name: str
    status: str
    message: str
    reason: str = ""
    updated: float = field(default_factory=time.time)


@dataclass
class Snapshot:
    running: bool
    phase: str  # starting, scanning, processing, idle, stopping, stopped, error
    counts: dict[str, int]
    files: dict[str, FileView]
    current_file: str | None
    current_started: float | None
    current_pages: tuple[int, int] | None  # (pages done, pages to OCR) of the current file
    batch_done: int
    batch_total: int
    fatal_error: str | None


class StartupError(Exception):
    """Monitoring cannot start (bad folders, missing dependencies, second instance)."""


class _FoldersUnavailable(Exception):
    """The source or destination folder cannot be reached right now (e.g. network drive)."""


class Monitor:
    """Runs the scan/process loop in a background thread."""

    def __init__(self, config: AppConfig, once: bool = False, retry_failed: bool = False) -> None:
        self.config = config
        self.once = once
        self.stop_event = threading.Event()
        self._wake = threading.Event()
        self._retry_requested = threading.Event()
        if retry_failed:
            self._retry_requested.set()
        self._lock = threading.Lock()
        self._views: dict[str, FileView] = {}
        self._thread: threading.Thread | None = None
        self._instance_lock = InstanceLock(config.lock_file)
        self._state: StateStore | None = None
        self._processor: Processor | None = None
        self._phase = "stopped"
        self._current: str | None = None
        self._current_started: float | None = None
        self._current_pages: tuple[int, int] | None = None
        self._batch_done = 0
        self._batch_total = 0
        self._last_scan_problem: str | None = None
        self.fatal_error: str | None = None
        self.exit_code = 0

    # ------------------------------------------------------------------ control

    def start(self) -> None:
        if self.is_running():
            return
        self.stop_event.clear()
        self._phase = "starting"
        self._thread = threading.Thread(target=self._run, name="pdf-ocr-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Request a stop. A running OCR job is cancelled; it will be redone on the next start."""
        if self.is_running():
            self._phase = "stopping"
        self.stop_event.set()
        self._wake.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def request_retry_failed(self) -> None:
        self._retry_requested.set()
        self._wake.set()

    def snapshot(self) -> Snapshot:
        with self._lock:
            files = dict(self._views)
            current, current_started, current_pages = self._current, self._current_started, self._current_pages
        counts = {status: 0 for status in STATUSES}
        for view in files.values():
            counts[view.status] = counts.get(view.status, 0) + 1
        return Snapshot(
            running=self.is_running(),
            phase=self._phase,
            counts=counts,
            files=files,
            current_file=current,
            current_started=current_started,
            current_pages=current_pages,
            batch_done=self._batch_done,
            batch_total=self._batch_total,
            fatal_error=self.fatal_error,
        )

    # ------------------------------------------------------------------ main loop

    def _run(self) -> None:
        started = False
        try:
            try:
                self._startup()
                started = True
            except StartupError as exc:
                self.fatal_error = str(exc)
                self.exit_code = 2
                self._phase = "error"
                logger.error("Cannot start monitoring:\n%s", exc)
                return

            interval = self.config.scan_interval_seconds
            while not self.stop_event.is_set():
                pending = True
                self._phase = "scanning"
                try:
                    pending = self._scan_cycle()
                    if self._last_scan_problem:
                        logger.info("Folders are available again.")
                    self._last_scan_problem = None
                except _FoldersUnavailable as exc:
                    if str(exc) != self._last_scan_problem:
                        logger.warning("%s — retrying every %d s", exc, interval)
                    self._last_scan_problem = str(exc)
                except Exception:
                    logger.exception("Unexpected error during the scan; retrying in %d s", interval)

                if self.stop_event.is_set():
                    break
                if self.once and not pending:
                    if self.snapshot().counts[FAILED]:
                        self.exit_code = 1
                    logger.info("Nothing left to process; exiting (--once).")
                    break
                self._phase = "idle"
                delay = min(interval, max(2, self.config.stable_seconds)) if pending else interval
                self._wake.wait(delay)
                self._wake.clear()
        except Exception:
            logger.exception("The monitor stopped because of an unexpected error")
            self.fatal_error = "unexpected internal error (see the log file)"
            self.exit_code = 3
        finally:
            self._instance_lock.release()
            with self._lock:
                self._current, self._current_pages = None, None
            if self._phase != "error":
                self._phase = "stopped"
            if started:
                logger.info("Monitoring stopped.")

    def _startup(self) -> None:
        cfg = self.config
        try:
            cfg.validate_folders()
        except ConfigError as exc:
            raise StartupError(str(exc)) from exc

        report = preflight(cfg)
        for line in report.info:
            logger.info(line)
        for line in report.warnings:
            logger.warning(line)
        if report.errors:
            raise StartupError("\n".join(f"- {error}" for error in report.errors))

        if not self._instance_lock.acquire():
            raise StartupError(
                f"Another PDF OCR process is already working on {cfg.source_folder} -> "
                f"{cfg.destination_folder}. Stop it first (lock file: {cfg.lock_file})."
            )

        self._state = StateStore(cfg.state_file, cfg.source_folder, cfg.destination_folder)
        self._state.load()
        self._processor = Processor(cfg, self._state, self.stop_event)
        removed = self._processor.cleanup_temp_dir()
        if removed:
            logger.info("Removed %d leftover temporary file(s) from an interrupted run.", removed)

        logger.info("Source folder:      %s", cfg.source_folder)
        logger.info("Destination folder: %s", cfg.destination_folder)
        logger.info("OCR languages: %s | resolution: %d dpi | scan every %d s", cfg.ocr.languages,
                    cfg.ocr.resolution, cfg.scan_interval_seconds)
        logger.info("State file: %s", cfg.state_file)
        logger.info("Monitoring started.")

    def _ensure_destination(self) -> bool:
        folder = self.config.destination_folder
        if folder.is_dir():
            return True
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        logger.warning("The destination folder was missing and has been recreated: %s", folder)
        return True

    def _scan_cycle(self) -> bool:
        """Scan, then process ready files one by one, re-scanning after each file.

        Returns True while files are still waiting (so the next scan comes sooner).
        """
        assert self._processor is not None and self._state is not None
        processor, cfg = self._processor, self.config

        if self._retry_requested.is_set():
            self._retry_requested.clear()
            count = self._state.reset_failures()
            logger.info("Retry requested: %d failed file(s) will be retried now.", count)

        if not cfg.source_folder.is_dir():
            raise _FoldersUnavailable(f"Source folder is not available: {cfg.source_folder}")
        if not self._ensure_destination():
            raise _FoldersUnavailable(f"Destination folder is not available: {cfg.destination_folder}")

        self._batch_done = 0
        pending = False
        while not self.stop_event.is_set():
            try:
                sources = processor.list_sources()
            except OSError as exc:
                raise _FoldersUnavailable(f"Cannot read the source folder {cfg.source_folder}: {exc}") from exc
            self._forget_missing(sources)

            candidates: list[SourceFile] = []
            pending = False
            for sf in sources:
                if self.stop_event.is_set():
                    return True
                try:
                    decision = processor.evaluate(sf)
                except Exception as exc:
                    logger.debug("Evaluation of %s failed", sf.rel_path, exc_info=True)
                    decision = Decision(WAIT, "new", f"could not be checked yet: {exc}")
                self._apply_decision(sf, decision)
                if decision.action == PROCESS:
                    candidates.append(sf)
                elif decision.action == WAIT:
                    pending = True

            self._batch_total = self._batch_done + len(candidates)
            if not candidates:
                return pending
            self._process_one(candidates[0])
        return True

    def _forget_missing(self, sources: list[SourceFile]) -> None:
        """Handle source files that no longer exist.

        Their state record is kept (marked missing) for a while: if a file with the same
        name comes back, its content is compared with the processed version instead of
        trusting the old output. Records missing for STALE_RECORD_SECONDS are purged.
        """
        assert self._processor is not None and self._state is not None
        current = {sf.key for sf in sources}
        with self._lock:
            gone = [key for key in self._views if key not in current]
            for key in gone:
                del self._views[key]
        for key in gone:
            self._processor.forget(key)
        now = time.time()
        for key in self._state.keys():
            if key in current:
                continue
            record = self._state.get(key) or {}
            missing_since = record.get("missing_since")
            if missing_since is None:
                record["missing_since"] = now
                self._state.set(key, record)
                logger.info("REMOVED    %s — source file is gone (its output, if any, is kept).",
                            record.get("rel_path", key))
            elif now - missing_since > STALE_RECORD_SECONDS:
                self._state.remove(key)

    # ------------------------------------------------------------------ status

    def _set_view(
        self,
        sf: SourceFile,
        status: str,
        message: str,
        reason: str,
        log_text: str | None = None,
        level: int = logging.INFO,
    ) -> None:
        """Update a file's status; log only when something actually changed."""
        view = FileView(sf.rel_path, status, message, reason)
        with self._lock:
            previous = self._views.get(sf.key)
            self._views[sf.key] = view
        changed = previous is None or (previous.status, previous.reason, previous.message) != (
            status, reason, message,
        )
        if log_text and changed:
            logger.log(level, log_text)

    def _apply_decision(self, sf: SourceFile, decision: Decision) -> None:
        name = sf.rel_path
        with self._lock:
            previous = self._views.get(sf.key)

        if decision.action == SKIP:
            if previous is not None and previous.status == COMPLETED:
                return
            message = "valid output already exists"
            if decision.reason == "adopted":
                message += " (existing output recognised)"
            self._set_view(sf, COMPLETED, message, decision.reason,
                           f"SKIPPED    {name} — processing skipped because a valid output already exists")
        elif decision.action == HOLD:
            self._set_view(sf, FAILED, decision.detail, "failed", f"FAILED     {name} — {decision.detail}",
                           logging.WARNING)
        else:
            reason_text = REASONS.get(decision.reason, decision.reason)
            message = f"{reason_text} — {decision.detail}" if decision.detail else reason_text
            if (
                decision.action == PROCESS
                and previous is not None
                and previous.status == WAITING
                and previous.reason == decision.reason
            ):
                # Already announced while it was waiting; just update the view.
                self._set_view(sf, WAITING, message, decision.reason)
                return
            tag = _REASON_TAGS.get(decision.reason, "WAITING")
            self._set_view(sf, WAITING, message, decision.reason, f"{tag:<10} {name} — {message}")

    def _process_one(self, sf: SourceFile) -> None:
        assert self._processor is not None
        processor, name = self._processor, sf.rel_path
        with self._lock:
            previous = self._views.get(sf.key)
            reason = previous.reason if previous else "new"
            self._current, self._current_started = name, time.time()
            self._views[sf.key] = FileView(name, PROCESSING, "OCR in progress", reason)
        self._phase = "processing"
        logger.info("STARTED    %s — processing started (%s)", name, REASONS.get(reason, reason))

        def on_progress(done: int, total: int) -> None:
            with self._lock:
                self._current_pages = (done, total)
                self._views[sf.key] = FileView(name, PROCESSING, f"OCR page {done} of {total}", reason)
            if total and 0 < done < total and done % max(25, total // 10) == 0:
                logger.info("PROGRESS   %s — %d of %d pages", name, done, total)

        try:
            outcome = processor.process(sf, on_progress)
        except Exception as exc:
            logger.exception("Unexpected error while processing %s", name)
            outcome = Outcome("failed", f"unexpected error: {exc}")
        finally:
            with self._lock:
                self._current, self._current_started, self._current_pages = None, None, None

        if outcome.status == "completed":
            self._batch_done += 1
            self._set_view(sf, COMPLETED, outcome.message, "done", f"COMPLETED  {name} — {outcome.message}")
        elif outcome.status == "failed":
            self._batch_done += 1
            try:
                message = processor.describe_failure(processor.record_failure(sf, outcome))
            except Exception:
                logger.exception("Could not record the failure of %s", name)
                message = outcome.message
            self._set_view(sf, FAILED, message, "failed", f"FAILED     {name} — {message}", logging.ERROR)
        elif outcome.status == "wait":
            processor.forget(sf.key)  # must be observed as stable again
            self._set_view(sf, WAITING, outcome.message, "wait", f"WAITING    {name} — {outcome.message}")
        else:  # cancelled
            self._set_view(
                sf, WAITING, "interrupted; it will be processed again on the next start", "cancelled",
                f"CANCELLED  {name} — {outcome.message}; it will be processed again on the next start",
                logging.WARNING,
            )

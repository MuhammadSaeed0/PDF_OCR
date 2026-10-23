"""Decides what to do with each source PDF and performs the OCR of a single file.

A source PDF counts as done only when ALL of these hold:
  * the state file has a "completed" record for it,
  * the record's source fingerprint (size + modification time, confirmed by SHA-256
    when those differ) matches the current source file, and
  * the output PDF exists and is valid (unchanged since it was written, or
    re-validated with PyMuPDF if it was touched).
Anything else (new file, changed source, deleted or damaged output, retry due)
leads to (re)processing. Files are only processed when they are stable and not
locked, i.e. no longer being copied.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import TEMP_DIR_NAME, AppConfig, same_path
from .ocr_engine import OcrEngine
from .pdf_utils import (
    PdfCheckError,
    copy_with_sha256,
    file_sha256,
    inspect_pdf,
    quick_markers,
    validate_output,
)
from .state import STATUS_COMPLETED, STATUS_FAILED, StateStore, now_iso

logger = logging.getLogger(__name__)

# Decision actions
SKIP = "skip"        # output is valid and up to date
WAIT = "wait"        # needs processing, but the file is not ready yet
PROCESS = "process"  # process now
HOLD = "hold"        # failed earlier; waiting for the retry time or for the source to change

REASONS = {
    "new": "new PDF detected",
    "source_changed": "source file changed since it was processed; scheduled for reprocessing",
    "output_deleted": "output PDF was deleted from the destination folder; scheduled for regeneration",
    "output_invalid": "existing output PDF is outdated, incomplete or corrupted; scheduled for regeneration",
    "retry": "retrying after an earlier failure",
}

STABILIZING = "waiting until the file stops changing (it may still be copying)"
LOCKED = "file is locked by another program (still being copied or open elsewhere)"
INCOMPLETE = "file looks incomplete (no PDF end marker yet); waiting in case it is still being copied"
DISAPPEARED = "file disappeared"

_IGNORED_PREFIXES = ("~", ".", "$")
_IGNORED_ATTRIBUTES = (
    getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 2)
    | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 4)
    | getattr(stat, "FILE_ATTRIBUTE_TEMPORARY", 256)
)
EOF_GRACE_SECONDS = 120
MB = 1024 * 1024


@dataclass
class SourceFile:
    key: str          # case-normalised relative path (state key)
    rel_path: str     # relative path with original spelling (shown to the user)
    path: Path
    output_path: Path
    size: int
    mtime_ns: int


@dataclass
class Decision:
    action: str
    reason: str = ""
    detail: str = ""


@dataclass
class Outcome:
    status: str  # "completed", "failed", "wait" or "cancelled"
    message: str
    permanent: bool = False


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.debug("Could not remove temporary file %s: %s", path, exc)


def _hide_folder(path: Path) -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)  # FILE_ATTRIBUTE_HIDDEN
        except Exception:
            pass


class Processor:
    """Evaluates source files against the state/output folder and OCRs them one at a time."""

    def __init__(self, config: AppConfig, state: StateStore, stop_event: threading.Event) -> None:
        self.config = config
        self.state = state
        self.stop_event = stop_event
        self.engine = OcrEngine(config, stop_event)
        # key -> (size, mtime_ns, monotonic time this fingerprint was first seen)
        self._seen: dict[str, tuple[int, int, float]] = {}
        # key -> (size, mtime_ns, sha256)
        self._hash_cache: dict[str, tuple[int, int, str]] = {}

    # ------------------------------------------------------------------ discovery

    def list_sources(self) -> list[SourceFile]:
        """All eligible PDFs in the source folder. Raises OSError if the folder cannot be read."""
        results: list[SourceFile] = []
        dest = self.config.destination_folder

        def walk(folder: Path, prefix: str, top: bool) -> None:
            try:
                entries = list(os.scandir(folder))
            except OSError as exc:
                if top:
                    raise
                logger.warning("Cannot read subfolder %s: %s", folder, exc)
                return
            for entry in entries:
                name = entry.name
                if name.startswith(_IGNORED_PREFIXES):
                    continue
                rel = f"{prefix}{os.sep}{name}" if prefix else name
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if (
                            self.config.include_subfolders
                            and name != TEMP_DIR_NAME
                            and not same_path(entry.path, dest)
                        ):
                            walk(Path(entry.path), rel, False)
                        continue
                    if not entry.is_file() or not name.lower().endswith(".pdf"):
                        continue
                    st = entry.stat()
                except OSError:
                    continue
                if getattr(st, "st_file_attributes", 0) & _IGNORED_ATTRIBUTES:
                    continue
                results.append(
                    SourceFile(
                        key=os.path.normcase(rel),
                        rel_path=rel,
                        path=Path(entry.path),
                        output_path=dest / rel,
                        size=st.st_size,
                        mtime_ns=st.st_mtime_ns,
                    )
                )

        walk(self.config.source_folder, "", True)
        results.sort(key=lambda s: s.key)
        return results

    def forget(self, key: str) -> None:
        """Drop cached observations of a file (it vanished, or must re-stabilise)."""
        self._seen.pop(key, None)
        self._hash_cache.pop(key, None)

    # ------------------------------------------------------------------ evaluation

    def _stable_for(self, sf: SourceFile) -> float:
        """Seconds the file's size and modification time have been unchanged (0 on first sight)."""
        now = time.monotonic()
        previous = self._seen.get(sf.key)
        if previous is None or previous[:2] != (sf.size, sf.mtime_ns):
            self._seen[sf.key] = (sf.size, sf.mtime_ns, now)
            return 0.0
        return now - previous[2]

    def _source_hash(self, sf: SourceFile) -> str:
        cached = self._hash_cache.get(sf.key)
        if cached and cached[:2] == (sf.size, sf.mtime_ns):
            return cached[2]
        digest = file_sha256(sf.path)
        self._hash_cache[sf.key] = (sf.size, sf.mtime_ns, digest)
        return digest

    def evaluate(self, sf: SourceFile) -> Decision:
        """Decide whether a source file is up to date, must wait, must be processed, or is on hold."""
        record = self.state.get(sf.key)
        reason = "new"
        unchanged = False

        if record is not None:
            if record.pop("missing_since", None) is not None:
                self.state.set(sf.key, record)  # the file is back; its content is compared below
            source = record.get("source") or {}
            unchanged = source.get("size") == sf.size and source.get("mtime_ns") == sf.mtime_ns
            if not unchanged:
                # Size or time differ. Only content counts, so compare hashes once the file is stable.
                if self._stable_for(sf) < self.config.stable_seconds:
                    return Decision(WAIT, "source_changed", STABILIZING)
                try:
                    digest = self._source_hash(sf)
                except PermissionError:
                    return Decision(WAIT, "source_changed", LOCKED)
                except FileNotFoundError:
                    return Decision(WAIT, "source_changed", DISAPPEARED)
                if source.get("sha256") and digest == source["sha256"]:
                    record["source"].update(size=sf.size, mtime_ns=sf.mtime_ns)
                    self.state.set(sf.key, record)
                    unchanged = True
                else:
                    reason = "source_changed"

            if unchanged:
                if record.get("status") == STATUS_COMPLETED:
                    output = self._check_output(sf, record)
                    if output == "ok":
                        return Decision(SKIP, "up_to_date")
                    reason = "output_deleted" if output == "missing" else "output_invalid"
                else:
                    if record.get("permanent") or time.time() < record.get("next_retry_at", 0):
                        return Decision(HOLD, "failed", self.describe_failure(record))
                    reason = "retry"
        elif sf.output_path.exists():
            # No record (first run, or state file lost) but an output exists: adopt it if it is valid.
            if self._stable_for(sf) < self.config.stable_seconds:
                return Decision(WAIT, "new", STABILIZING)
            try:
                if self._try_adopt(sf):
                    return Decision(SKIP, "adopted")
            except PermissionError:
                return Decision(WAIT, "new", LOCKED)
            reason = "output_invalid"

        ready, detail = self._readiness(sf, known_complete=unchanged)
        if not ready:
            return Decision(WAIT, reason, detail)
        return Decision(PROCESS, reason)

    def _check_output(self, sf: SourceFile, record: dict[str, Any]) -> str:
        """'ok', 'missing' or 'invalid' for the output of a completed record."""
        try:
            st = sf.output_path.stat()
        except FileNotFoundError:
            return "missing"
        except OSError:
            return "ok"  # e.g. a network hiccup: never regenerate on uncertain information
        written = record.get("output") or {}
        if st.st_size == written.get("size") and st.st_mtime_ns == written.get("mtime_ns"):
            return "ok"
        # The output was touched or replaced since we wrote it: validate its content.
        try:
            with open(sf.output_path, "rb"):
                pass
        except OSError:
            return "ok"  # locked by another program; check again on a later scan
        try:
            validate_output(sf.output_path, record.get("pages"))
        except PdfCheckError as exc:
            logger.debug("Output check failed for %s: %s", sf.rel_path, exc)
            return "invalid"
        record["output"] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
        self.state.set(sf.key, record)
        return "ok"

    def _try_adopt(self, sf: SourceFile) -> bool:
        """Accept an existing output that was produced earlier (e.g. before the state file existed).

        Only accepted when it is newer than the source, has the same pages and contains text.
        """
        try:
            out_stat = sf.output_path.stat()
        except FileNotFoundError:
            return False
        if out_stat.st_mtime_ns < sf.mtime_ns:
            return False
        with open(sf.path, "rb"):  # raises PermissionError while the source is locked
            pass
        try:
            source_info = inspect_pdf(sf.path)
            output_info = validate_output(
                sf.output_path, source_info.page_count, source_info.page_sizes, count_text=True
            )
        except PdfCheckError:
            return False
        if output_info.text_chars == 0:
            return False
        digest = self._source_hash(sf)
        self.state.set(
            sf.key,
            {
                "rel_path": sf.rel_path,
                "status": STATUS_COMPLETED,
                "source": {"size": sf.size, "mtime_ns": sf.mtime_ns, "sha256": digest},
                "pages": source_info.page_count,
                "output": {"size": out_stat.st_size, "mtime_ns": out_stat.st_mtime_ns},
                "text_chars": output_info.text_chars,
                "completed_at": now_iso(),
                "adopted": True,
            },
        )
        return True

    def _readiness(self, sf: SourceFile, known_complete: bool) -> tuple[bool, str]:
        """Is the source file completely written and readable?

        `known_complete` means the file is byte-identical to a version processed before,
        so only the lock check is needed.
        """
        if not known_complete:
            if sf.size == 0:
                self._stable_for(sf)
                return False, "file is empty (it may still be being created)"
            stable_for = self._stable_for(sf)
            if stable_for < self.config.stable_seconds:
                return False, STABILIZING
        try:
            has_header, has_eof = quick_markers(sf.path)
        except PermissionError:
            return False, LOCKED
        except FileNotFoundError:
            return False, DISAPPEARED
        except OSError as exc:
            return False, f"file cannot be read yet ({exc.strerror or exc})"
        if not known_complete and has_header and not has_eof:
            # Some copy tools pre-allocate the full size; give them time to finish.
            if stable_for < max(EOF_GRACE_SECONDS, 6 * self.config.stable_seconds):
                return False, INCOMPLETE
        return True, ""

    # ------------------------------------------------------------------ failures

    def describe_failure(self, record: dict[str, Any]) -> str:
        error = record.get("error") or "unknown error"
        if record.get("permanent"):
            return f"{error} — will be retried when the source file changes (or use Retry failed)"
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.get("next_retry_at", 0)))
        return f"{error} — attempt {record.get('attempts', 1)}, next retry at {when}"

    def record_failure(self, sf: SourceFile, outcome: Outcome) -> dict[str, Any]:
        """Store a failed attempt with an exponential back-off for transient errors."""
        previous = self.state.get(sf.key) or {}
        previous_source = previous.get("source") or {}
        same_version = (
            previous.get("status") == STATUS_FAILED
            and previous_source.get("size") == sf.size
            and previous_source.get("mtime_ns") == sf.mtime_ns
        )
        attempts = previous.get("attempts", 0) + 1 if same_version else 1
        delay = min(self.config.retry_delay_seconds * 2 ** min(attempts - 1, 20), self.config.max_retry_delay_seconds)
        source: dict[str, Any] = {"size": sf.size, "mtime_ns": sf.mtime_ns}
        cached = self._hash_cache.get(sf.key)
        if cached and cached[:2] == (sf.size, sf.mtime_ns):
            source["sha256"] = cached[2]
        record = {
            "rel_path": sf.rel_path,
            "status": STATUS_FAILED,
            "source": source,
            "error": outcome.message,
            "permanent": outcome.permanent,
            "attempts": attempts,
            "last_attempt_at": now_iso(),
            "next_retry_at": time.time() + delay,
        }
        self.state.set(sf.key, record)
        return record

    # ------------------------------------------------------------------ processing

    def cleanup_temp_dir(self) -> int:
        """Delete work files left behind by an interrupted run. Returns the number removed."""
        folder = self.config.temp_dir
        if not folder.is_dir():
            return 0
        removed = 0
        for item in folder.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("Could not remove leftover temporary file %s: %s", item, exc)
        return removed

    def _disk_space_problem(self, sf: SourceFile) -> str | None:
        reserve = self.config.min_free_space_mb * MB
        checks = (
            ("destination", self.config.destination_folder, sf.size * 3 + reserve),
            ("temporary-files", Path(tempfile.gettempdir()), sf.size * 4 + reserve),
        )
        for label, folder, needed in checks:
            try:
                free = shutil.disk_usage(folder).free
            except OSError:
                continue
            if free < needed:
                return (
                    f"insufficient disk space on the {label} drive ({folder}): "
                    f"{free // MB} MB free, about {needed // MB} MB needed"
                )
        return None

    def _keep_failure_log(self, sf: SourceFile, ocr_log: Path) -> None:
        """Keep OCRmyPDF's full output of a failed file in logs\\failed for troubleshooting."""
        if not ocr_log.is_file():
            return
        try:
            folder = self.config.log_dir / "failed"
            folder.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r'[\\/:*?"<>|]', "_", sf.rel_path)
            shutil.copyfile(ocr_log, folder / f"{safe_name}.ocrmypdf.log")
        except OSError:
            pass

    @staticmethod
    def _install_output(tmp_output: Path, final_output: Path) -> None:
        """Atomically move the finished PDF into place (retrying while a viewer locks the old one)."""
        final_output.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(10):
            try:
                os.replace(tmp_output, final_output)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(1.0)

    def process(self, sf: SourceFile) -> Outcome:
        """OCR one source file into its output. The state is updated only after success."""
        cfg = self.config
        job = uuid.uuid4().hex[:12]
        tmp_dir = cfg.temp_dir
        tmp_in = tmp_dir / f"{job}.in.pdf"
        tmp_out = tmp_dir / f"{job}.out.pdf"
        tmp_log = tmp_dir / f"{job}.log"
        started = time.monotonic()
        try:
            try:
                tmp_dir.mkdir(parents=True, exist_ok=True)
                _hide_folder(tmp_dir)
            except OSError as exc:
                return Outcome("failed", f"cannot create the temporary folder {tmp_dir}: {exc}")

            problem = self._disk_space_problem(sf)
            if problem:
                return Outcome("failed", problem)

            # Work on a private copy: the original is never touched, it cannot change
            # mid-OCR, and the short ASCII name avoids path problems in Ghostscript.
            try:
                before = sf.path.stat()
                digest = copy_with_sha256(sf.path, tmp_in)
                after = sf.path.stat()
            except PermissionError:
                return Outcome("wait", LOCKED)
            except FileNotFoundError:
                return Outcome("wait", DISAPPEARED)
            except OSError as exc:
                if exc.errno == errno.ENOSPC:
                    return Outcome("failed", "insufficient disk space while copying the source file")
                return Outcome("failed", f"cannot read the source file: {exc}")
            fingerprint = (before.st_size, before.st_mtime_ns)
            if fingerprint != (after.st_size, after.st_mtime_ns) or fingerprint != (sf.size, sf.mtime_ns):
                return Outcome("wait", "file changed while it was being read; waiting until it is stable")
            self._hash_cache[sf.key] = (before.st_size, before.st_mtime_ns, digest)

            try:
                info = inspect_pdf(tmp_in)
            except PdfCheckError as exc:
                return Outcome("failed", f"source PDF {exc}", permanent=exc.permanent)
            logger.info("           %s: %d page(s), languages %s", sf.rel_path, info.page_count, cfg.ocr.languages)

            result = self.engine.run(tmp_in, tmp_out, tmp_log)
            if result.status == "cancelled":
                return Outcome("cancelled", result.message)
            if result.status != "ok":
                self._keep_failure_log(sf, tmp_log)
                return Outcome("failed", result.message, permanent=result.permanent)

            try:
                out_info = validate_output(tmp_out, info.page_count, info.page_sizes, count_text=True)
            except PdfCheckError as exc:
                self._keep_failure_log(sf, tmp_log)
                return Outcome("failed", str(exc))

            try:
                self._install_output(tmp_out, sf.output_path)
                out_stat = sf.output_path.stat()
            except OSError as exc:
                return Outcome(
                    "failed",
                    f"could not save the output PDF {sf.output_path} ({exc}). "
                    "Close it if it is open in a PDF viewer.",
                )

            # Only now, with a validated output in place, is the file marked as completed.
            self.state.set(
                sf.key,
                {
                    "rel_path": sf.rel_path,
                    "status": STATUS_COMPLETED,
                    "source": {"size": before.st_size, "mtime_ns": before.st_mtime_ns, "sha256": digest},
                    "pages": info.page_count,
                    "output": {"size": out_stat.st_size, "mtime_ns": out_stat.st_mtime_ns},
                    "text_chars": out_info.text_chars,
                    "completed_at": now_iso(),
                },
            )
            message = (
                f"{info.page_count} page(s), {out_info.text_chars} characters of text, "
                f"{time.monotonic() - started:.1f} s"
            )
            if result.message:
                message += f" ({result.message})"
            if out_info.text_chars == 0:
                message += " — no text was recognised (blank pages or wrong OCR language?)"
            return Outcome("completed", message)
        finally:
            for path in (tmp_in, tmp_out, tmp_log):
                _remove_quietly(path)

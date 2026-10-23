"""Runs OCRmyPDF in a child process and turns its exit status into an OcrResult.

A separate process (instead of calling ocrmypdf.ocr() in-process) keeps the
monitor isolated from crashes in Tesseract/Ghostscript, allows a hard per-file
timeout, and lets a stop request kill the whole OCR process tree immediately.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import OCR_MODES, AppConfig
from .dependencies import build_env, find_ghostscript, find_tesseract, has_console, hidden_window_flags

logger = logging.getLogger(__name__)

# OCRmyPDF exit codes (ocrmypdf.ExitCode) -> (explanation, permanent?)
# "permanent" failures are not retried until the source file changes.
EXIT_CODES: dict[int, tuple[str, bool]] = {
    1: ("OCRmyPDF rejected the arguments; check the [ocr] section of config.ini", False),
    2: ("the PDF is invalid or damaged and cannot be processed", True),
    3: ("a required program or OCR language data is missing (Tesseract, Ghostscript or traineddata)", False),
    4: ("OCRmyPDF produced an invalid output file", False),
    5: ("a file could not be read or written (file access error)", False),
    6: ("the PDF already contains text; set mode = skip_text or redo_ocr in config.ini", True),
    7: ("an OCR helper program (Tesseract or Ghostscript) failed", False),
    8: ("the PDF is encrypted or password-protected", True),
    9: ("invalid Tesseract configuration (check the languages and Tesseract options)", False),
    10: ("PDF/A conversion failed", False),
    15: ("an unexpected OCRmyPDF error occurred", False),
}
PDFA_CONVERSION_FAILED = 10
# 130 = OCRmyPDF's own Ctrl+C code; 0xC000013A = Windows STATUS_CONTROL_C_EXIT.
CTRL_C_EXIT_CODES = {130, 0xC000013A, -1073741510}


@dataclass
class OcrResult:
    status: str  # "ok", "failed" or "cancelled"
    message: str = ""
    permanent: bool = False
    exit_code: int | None = None


def python_console_executable() -> str:
    """python.exe of the running interpreter (OCRmyPDF needs a console interpreter, not pythonw)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console_exe = exe.with_name("python.exe")
        if console_exe.is_file():
            return str(console_exe)
    return str(exe)


def _log_excerpt(log_path: Path, max_lines: int = 6, max_chars: int = 800) -> str:
    """The most useful lines of an OCRmyPDF log for an error message."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    errors = [line for line in lines if "error" in line.lower() or "exception" in line.lower()]
    excerpt = " | ".join((errors or lines)[-max_lines:])
    return excerpt[-max_chars:]


class OcrEngine:
    """Builds and runs the OCRmyPDF command for one file at a time."""

    def __init__(self, config: AppConfig, stop_event: threading.Event) -> None:
        self.config = config
        self.stop_event = stop_event
        self._tesseract = find_tesseract(config)
        self._ghostscript = find_ghostscript(config)

    def build_command(self, input_pdf: Path, output_pdf: Path) -> list[str]:
        ocr = self.config.ocr
        cmd = [
            python_console_executable(), "-m", "ocrmypdf",
            "--language", ocr.languages,
            OCR_MODES[ocr.mode],
            "--output-type", ocr.output_type,
            "--optimize", str(ocr.optimize),
            "--pdf-renderer", ocr.pdf_renderer,
            "--tesseract-timeout", str(ocr.tesseract_timeout),
        ]
        if ocr.jobs > 0:
            cmd += ["--jobs", str(ocr.jobs)]
        if ocr.rotate_pages:
            cmd.append("--rotate-pages")
        if ocr.deskew:
            cmd.append("--deskew")
        if ocr.oversample > 0:
            cmd += ["--oversample", str(ocr.oversample)]
        cmd += ocr.extra_args
        cmd += [str(input_pdf), str(output_pdf)]
        return cmd

    def run(self, input_pdf: Path, output_pdf: Path, log_path: Path) -> OcrResult:
        """OCR input_pdf into output_pdf. OCRmyPDF's console output is written to log_path."""
        cmd = self.build_command(input_pdf, output_pdf)
        env = build_env(self.config, self._tesseract, self._ghostscript)
        # With a console, OCR shares it so closing the window / Ctrl+C also ends OCR.
        flags = 0 if has_console() else hidden_window_flags()
        timeout = self.config.ocr.file_timeout_minutes * 60
        logger.debug("Running: %s", subprocess.list2cmdline(cmd))

        try:
            with open(log_path, "wb") as log_file:
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        env=env,
                        cwd=str(Path(input_pdf).parent),
                        creationflags=flags,
                    )
                except OSError as exc:
                    return OcrResult("failed", f"could not start OCRmyPDF: {exc}")
                exit_code, interruption = self._wait(proc, timeout)
        except OSError as exc:
            return OcrResult("failed", f"cannot write the OCR log file {log_path}: {exc}")

        if interruption == "cancelled":
            return OcrResult("cancelled", "OCR was interrupted because the application is stopping")
        if interruption == "timeout":
            return OcrResult(
                "failed",
                f"OCR did not finish within {self.config.ocr.file_timeout_minutes} minutes and was stopped "
                "(increase [ocr] file_timeout_minutes for very large files)",
            )
        assert exit_code is not None
        if exit_code == 0:
            return OcrResult("ok", exit_code=0)
        if exit_code in CTRL_C_EXIT_CODES:
            # Ctrl+C reaches the child and the parent at the same time; give the
            # parent a moment to register the stop so this is not counted as a failure.
            if self.stop_event.wait(3):
                return OcrResult("cancelled", "OCR was interrupted (Ctrl+C)")
            return OcrResult("failed", "OCRmyPDF was interrupted", exit_code=exit_code)
        if exit_code == PDFA_CONVERSION_FAILED and output_pdf.is_file():
            logger.warning("PDF/A conversion failed for %s; a regular PDF was produced instead.", input_pdf.name)
            return OcrResult("ok", "PDF/A conversion failed; regular PDF produced", exit_code=exit_code)

        description, permanent = EXIT_CODES.get(exit_code, (f"OCRmyPDF exited with code {exit_code}", False))
        excerpt = _log_excerpt(log_path)
        message = f"{description} (exit code {exit_code})" + (f": {excerpt}" if excerpt else "")
        return OcrResult("failed", message, permanent=permanent, exit_code=exit_code)

    def _wait(self, proc: subprocess.Popen, timeout: int) -> tuple[int | None, str | None]:
        started = time.monotonic()
        while True:
            try:
                return proc.wait(timeout=0.5), None
            except subprocess.TimeoutExpired:
                pass
            if self.stop_event.is_set():
                self._kill_tree(proc)
                return None, "cancelled"
            if timeout and time.monotonic() - started > timeout:
                self._kill_tree(proc)
                return None, "timeout"

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        """Kill OCRmyPDF together with its worker processes, Tesseract and Ghostscript."""
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=hidden_window_flags(),
                    timeout=30,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            logger.warning("OCR process %s did not exit after being killed", proc.pid)

"""Checks every dependency of PDF OCR and runs a real OCR test.

    .venv\\Scripts\\python.exe verify_install.py        (or double-click verify.bat)

Exit code 0 = everything works, 1 = something needs attention.
"""

from __future__ import annotations

import argparse
import dataclasses
import shutil
import sys
import tempfile
import threading
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

OK, FAIL, WARN = "[  OK  ]", "[ FAIL ]", "[ WARN ]"


class Checker:
    def __init__(self) -> None:
        self.failures = 0

    def ok(self, text: str) -> None:
        print(f"{OK} {text}")

    def warn(self, text: str) -> None:
        print(f"{WARN} {text}")

    def fail(self, text: str, hint: str | None = None) -> None:
        self.failures += 1
        print(f"{FAIL} {text}")
        if hint:
            print(f"         -> {hint}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the PDF OCR installation.")
    parser.add_argument("--config", default=str(APP_DIR / "config.ini"))
    parser.add_argument("--skip-ocr-test", action="store_true", help="only check the installation")
    args = parser.parse_args()
    check = Checker()

    print("PDF OCR - installation check\n")

    # 1. Python
    version = sys.version_info
    text = f"Python {version.major}.{version.minor}.{version.micro} ({sys.executable})"
    if (3, 11) <= version[:2] <= (3, 13):
        check.ok(text)
    elif version[:2] >= (3, 10):
        check.warn(text + " - tested with 3.11-3.13")
    else:
        check.fail(text, "Install Python 3.12 and run install.bat again.")

    # 2. Python packages
    try:
        from pdf_ocr.pdf_utils import pymupdf

        check.ok(f"PyMuPDF {pymupdf.__version__}")
    except Exception as exc:
        check.fail(f"PyMuPDF cannot be imported: {exc}", "Run install.bat (pip install -r requirements.txt).")
        return finish(check)
    try:
        import numpy
        import PIL

        check.ok(f"numpy {numpy.__version__}, Pillow {PIL.__version__}")
    except Exception as exc:
        check.fail(f"numpy/Pillow cannot be imported: {exc}", "Run install.bat (pip install -r requirements.txt).")
        return finish(check)

    # 3. Configuration
    from pdf_ocr.config import ConfigError, load_config

    try:
        config = load_config(args.config)
        check.ok(f"Configuration {config.config_path}")
    except ConfigError as exc:
        check.fail(f"Configuration: {exc}")
        return finish(check)
    if config.source_folder.is_dir():
        check.ok(f"Source folder exists: {config.source_folder}")
    else:
        check.fail(f"Source folder does not exist: {config.source_folder}",
                   "Create it or change source_folder in config.ini.")
    check.ok(f"Destination folder: {config.destination_folder}"
             + ("" if config.destination_folder.is_dir() else " (will be created on start)"))

    # 4. Tesseract and language data
    from pdf_ocr.dependencies import (
        build_env, effective_tessdata_dir, find_tesseract, tesseract_languages, tesseract_version,
    )

    tesseract = find_tesseract(config)
    env = build_env(config, tesseract)
    if tesseract is None:
        check.fail("Tesseract OCR not found",
                   "Install it from https://github.com/UB-Mannheim/tesseract/wiki or set [paths] tesseract_dir.")
    else:
        version_text = tesseract_version(tesseract, env)
        if version_text:
            check.ok(f"Tesseract: {tesseract} ({version_text})")
        else:
            check.fail(f"Tesseract at {tesseract} does not start")
        tessdata = effective_tessdata_dir(config)
        print(f"         language data folder: {tessdata or 'Tesseract default (' + str(tesseract.parent / 'tessdata') + ')'}")
        installed = tesseract_languages(tesseract, env) or []
        for lang in config.ocr.language_list:
            if lang in installed:
                check.ok(f"OCR language '{lang}' installed")
            else:
                check.fail(f"OCR language '{lang}' is missing (installed: {', '.join(installed) or 'none'})",
                           "Run: .venv\\Scripts\\python tools\\setup_tessdata.py")

    from pdf_ocr.ocr_engine import OcrEngine

    # 5. Real OCR test
    if args.skip_ocr_test:
        return finish(check)
    if check.failures:
        print("\nSkipping the OCR test until the problems above are fixed.")
        return finish(check)

    from pdf_ocr.pdf_utils import inspect_pdf
    from pdf_ocr.samples import make_scanned_pdf

    print(f"\nRunning an OCR test with languages '{config.ocr.languages}' (this can take up to a minute)...")
    work = Path(tempfile.mkdtemp(prefix="pdf_ocr_verify_"))
    try:
        scanned, searchable = work / "scan.pdf", work / "searchable.pdf"
        make_scanned_pdf(scanned, ["OCR VERIFICATION TEST\n\nHello World 12345\n\nThe quick brown fox."])
        if inspect_pdf(scanned, count_text=True).text_chars != 0:
            check.warn("The test scan unexpectedly contains text already")
        # The test always runs in the foreground with a fixed timeout.
        test_config = dataclasses.replace(config, ocr=dataclasses.replace(config.ocr, file_timeout_minutes=10))
        result = OcrEngine(test_config, threading.Event()).run(scanned, searchable, work / "ocr.log")
        if result.status != "ok":
            check.fail(f"OCR test failed: {result.message}")
            return finish(check)
        from pdf_ocr.pdf_utils import pymupdf

        with pymupdf.open(str(searchable)) as doc:
            text = " ".join(page.get_text("text") for page in doc)
        if "VERIFICATION" in text.upper():
            check.ok(f"OCR test passed - recognised text: {' '.join(text.split())[:80]!r}")
        else:
            check.fail(f"OCR ran but the expected words were not found (got {text.strip()[:80]!r})")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return finish(check)


def finish(check: Checker) -> int:
    print()
    if check.failures:
        print(f"{check.failures} problem(s) found. See README.md, section 'Troubleshooting'.")
        return 1
    print("Everything is installed correctly. Start the application with run.bat or run_gui.bat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

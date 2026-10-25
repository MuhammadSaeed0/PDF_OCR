"""PDF inspection and validation (PyMuPDF) plus small, safe file helpers."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

try:
    import pymupdf
except ImportError:  # PyMuPDF < 1.24.3 only provides the legacy module name
    import fitz as pymupdf  # type: ignore[no-redef]

try:
    # Keep MuPDF's own warnings out of the console; problems are reported via exceptions.
    pymupdf.TOOLS.mupdf_display_errors(False)
except Exception:
    pass

CHUNK_SIZE = 1024 * 1024
PAGE_SIZE_TOLERANCE_PT = 2.0


class PdfCheckError(Exception):
    """A PDF could not be used. `permanent` means retrying the same file will not help."""

    def __init__(self, message: str, permanent: bool = True) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass
class PdfInfo:
    page_count: int
    page_sizes: list[tuple[float, float]]
    text_chars: int = 0


def inspect_pdf(path: Path, count_text: bool = False) -> PdfInfo:
    """Open a PDF and return its page count and page sizes (and optionally its amount of text).

    Raises PdfCheckError for damaged, empty, password-protected or encrypted files.
    """
    try:
        doc = pymupdf.open(str(path), filetype="pdf")
    except Exception as exc:
        # MuPDF's message names our temporary copy, so it is not repeated to the user.
        raise PdfCheckError("cannot be opened as a PDF (damaged or not a PDF file)") from exc
    try:
        if not doc.is_pdf:
            raise PdfCheckError("is not a PDF document")
        if doc.needs_pass:
            raise PdfCheckError(
                "is password-protected. Save a copy without a password and put that copy in the source folder."
            )
        if doc.page_count < 1:
            raise PdfCheckError("contains no pages")
        sizes: list[tuple[float, float]] = []
        chars = 0
        for page in doc:
            rect = page.rect
            sizes.append((round(rect.width, 2), round(rect.height, 2)))
            if count_text:
                chars += len(page.get_text("text").strip())
        return PdfInfo(page_count=doc.page_count, page_sizes=sizes, text_chars=chars)
    except PdfCheckError:
        raise
    except Exception as exc:
        raise PdfCheckError(f"is damaged and could not be read: {exc}") from exc
    finally:
        doc.close()


def _same_size(a: tuple[float, float], b: tuple[float, float]) -> bool:
    # Sorted so that a page rotated by --rotate-pages still matches.
    (a1, a2), (b1, b2) = sorted(a), sorted(b)
    return abs(a1 - b1) <= PAGE_SIZE_TOLERANCE_PT and abs(a2 - b2) <= PAGE_SIZE_TOLERANCE_PT


def validate_output(
    path: Path,
    expected_pages: int | None,
    expected_sizes: list[tuple[float, float]] | None = None,
    count_text: bool = False,
) -> PdfInfo:
    """Confirm that an OCR output opens, is complete and matches the source page count/sizes."""
    try:
        info = inspect_pdf(path, count_text=count_text)
    except PdfCheckError as exc:
        raise PdfCheckError(f"output PDF {exc}", permanent=False) from exc
    if expected_pages is not None and info.page_count != expected_pages:
        raise PdfCheckError(
            f"output PDF has {info.page_count} page(s) but the source has {expected_pages}", permanent=False
        )
    if expected_sizes:
        for number, (out_size, src_size) in enumerate(zip(info.page_sizes, expected_sizes), start=1):
            if not _same_size(out_size, src_size):
                raise PdfCheckError(
                    f"output page {number} is {out_size} pt but the source page is {src_size} pt",
                    permanent=False,
                )
    return info


def quick_markers(path: Path) -> tuple[bool, bool]:
    """Return (has '%PDF-' header, has '%%EOF' near the end). Raises PermissionError if locked."""
    with open(path, "rb") as fh:
        head = fh.read(1024)
        size = fh.seek(0, os.SEEK_END)
        fh.seek(max(0, size - 2048))
        tail = fh.read()
    return b"%PDF-" in head, b"%%EOF" in tail


def copy_with_sha256(src: Path, dst: Path) -> str:
    """Copy src to dst (flushed to disk) and return the SHA-256 of the copied bytes."""
    digest = hashlib.sha256()
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        while True:
            chunk = fin.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            fout.write(chunk)
        fout.flush()
        os.fsync(fout.fileno())
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()

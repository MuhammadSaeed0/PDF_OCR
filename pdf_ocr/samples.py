"""Generates image-only ("scanned") PDFs for testing and for the installation check."""

from __future__ import annotations

import os
from pathlib import Path

from .pdf_utils import pymupdf

A4_WIDTH, A4_HEIGHT = 595, 842


def make_scanned_pdf(path: Path, pages: list[str], dpi: int = 200, user_password: str | None = None) -> None:
    """Write a PDF whose pages are pictures of the given texts (no text layer), like a scanner would.

    The file is written under a temporary name first, so a monitored folder never sees it half-written.
    """
    out = pymupdf.open()
    for text in pages:
        draft = pymupdf.open()
        page = draft.new_page(width=A4_WIDTH, height=A4_HEIGHT)
        page.insert_textbox(pymupdf.Rect(60, 60, A4_WIDTH - 60, A4_HEIGHT - 60), text, fontsize=16, fontname="helv")
        pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        draft.close()
        target = out.new_page(width=A4_WIDTH, height=A4_HEIGHT)
        target.insert_image(target.rect, pixmap=pixmap)

    tmp = path.with_name(path.name + ".tmp")
    save_options: dict = {"garbage": 3, "deflate": True}
    if user_password:
        save_options.update(
            encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=user_password, owner_pw=user_password + "-owner"
        )
    out.save(str(tmp), **save_options)
    out.close()
    os.replace(tmp, path)


def sample_page_text(document_number: int, page_number: int, page_count: int) -> str:
    return (
        f"Sample scanned document {document_number}\n\n"
        f"Page {page_number} of {page_count}\n\n"
        "The quick brown fox jumps over the lazy dog.\n"
        f"Search keyword: ZEBRA{document_number:03d}\n\n"
        "This page is an image without any text layer. After OCR the words on it\n"
        "can be searched, selected and copied in any PDF reader."
    )

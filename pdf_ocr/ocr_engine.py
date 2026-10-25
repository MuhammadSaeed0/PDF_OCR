"""OCR engine tuned for Arabic scans: Tesseract on cleaned-up page images, with the text
layer laid over the original, untouched pages.

For every page that has no text yet:
  1. render it so its long side matches A4 at the OCR resolution (300 dpi by default),
     whatever page size the PDF declares (many scanned books declare huge 96 dpi pages,
     which made Tesseract report "Empty page");
  2. keep the darkest colour channel, so coloured print (pink, blue) becomes as dark as
     black, divide by the local paper colour (tinted panels stay light) and stretch the contrast;
  3. measure the skew of the text lines and straighten the image (a clearly tilted page
     is also read unstraightened, and the reading with more words is kept, because the
     skew estimate can be fooled by watermarks or diagrams);
  4. let Tesseract recognise the page and write an invisible text layer (its own PDF
     renderer, which stores Arabic in correct right-to-left order);
  5. straighten the page itself by the same angle - only a rotation instruction is
     added around the page content, the scanned image bytes stay exactly the same -
     and lay the text layer horizontally over it.

The text layer must stay horizontal: PDF readers (PDFium in Edge/Chrome) split a rotated
Arabic line into pieces and read them left to right, scrambling the word order.
Pages are OCR'd in parallel by separate Tesseract processes; a stop request kills them.
"""

from __future__ import annotations

import logging
import math
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

import numpy as np
from PIL import Image, ImageFilter

from .config import AppConfig
from .dependencies import build_env, find_tesseract, hidden_window_flags
from .pdf_utils import pymupdf

logger = logging.getLogger(__name__)

# Bump when the OCR pipeline changes in a way that improves results: every file
# processed with an older version is then reprocessed automatically.
ENGINE_VERSION = 3

A4_LONG_SIDE_INCHES = 11.69
MIN_EXISTING_TEXT = 20          # a page with this many characters already has a text layer
MIN_SKEW_DEGREES = 0.2
MAX_SKEW_DEGREES = 15.0
# Only clearly dark ink is used to measure skew: large light-grey diagonal watermarks
# ("https://t.me/...") otherwise pull the estimate towards their own angle.
SKEW_INK_LEVEL = 64
# Pages estimated to be tilted at least this much are also read unstraightened (see _check_job).
VERIFY_SKEW_DEGREES = 1.5

# languages = auto: a book is treated as English when at least this share of its
# recognised words (on a few sample pages) is written in Latin letters.
AUTO_ARABIC = "ara"
AUTO_ENGLISH = "eng+ara"
AUTO_SAMPLE_PAGES = 3
AUTO_ENGLISH_RATIO = 0.35

ProgressCallback = Callable[[int, int], None]


@dataclass
class OcrResult:
    status: str  # "ok", "failed" or "cancelled"
    message: str = ""
    permanent: bool = False


def settings_signature(config: AppConfig) -> str:
    """Identifies the OCR settings an output was made with (stored in the state file)."""
    ocr = config.ocr
    return (f"engine{ENGINE_VERSION}|lang={ocr.languages}|dpi={ocr.resolution}"
            f"|psm={ocr.page_segmentation_mode}|skip_text={ocr.skip_pages_with_text}"
            f"|straighten={ocr.straighten_pages}")


# --------------------------------------------------------------------------- image preparation


def skew_angle(gray: np.ndarray) -> float:
    """Angle in degrees (PIL rotate convention) that makes the text lines horizontal.

    Uses a projection profile: when the lines are horizontal, the row histogram of
    dark pixels has the sharpest peaks.
    """
    small = gray[::4, ::4]
    ys, xs = np.nonzero(small < SKEW_INK_LEVEL)
    if len(xs) < 500:
        return 0.0
    if len(xs) > 150_000:
        pick = np.random.default_rng(0).choice(len(xs), 150_000, replace=False)
        ys, xs = ys[pick], xs[pick]
    ys = ys - small.shape[0] / 2.0
    xs = xs - small.shape[1] / 2.0

    def sharpness(angle: float) -> float:
        rad = math.radians(angle)
        rows = -xs * math.sin(rad) + ys * math.cos(rad)
        hist = np.bincount((rows - rows.min()).astype(np.int64)).astype(np.float64)
        return float((hist ** 2).sum())

    coarse = max(np.arange(-MAX_SKEW_DEGREES, MAX_SKEW_DEGREES + 0.01, 0.5), key=sharpness)
    return float(max(np.arange(coarse - 0.5, coarse + 0.51, 0.1), key=sharpness))


def normalise_background(gray: np.ndarray) -> np.ndarray:
    """Divide by the local paper colour, so ink is dark on white even on tinted or coloured
    backgrounds (taking the darkest colour channel alone would turn a pink panel dark too)."""
    height, width = gray.shape
    small = Image.fromarray(gray.astype(np.uint8)).resize((max(1, width // 16), max(1, height // 16)), Image.BOX)
    paper = small.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(2))
    paper = np.asarray(paper.resize((width, height), Image.BILINEAR), np.float32)
    return gray / np.maximum(paper, 1.0) * 255.0


def prepare_page_image(page, long_side: int, straighten: bool = True) -> tuple[Image.Image, float]:
    """Render a page for OCR: normalised size, coloured ink darkened, contrast stretched, deskewed.

    Returns the image and the angle it was rotated by (0 when not straightened). Pages with a
    /Rotate setting are never straightened.
    """
    zoom = long_side / max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    gray = normalise_background(rgb.min(axis=2).astype(np.float32))
    low, high = np.percentile(gray[::8, ::8], (1, 99))
    if high - low > 10:
        gray = (gray - low) * (255.0 / (high - low))
    gray = np.clip(gray, 0, 255).astype(np.uint8)
    angle = skew_angle(gray) if straighten and page.rotation == 0 else 0.0
    image = Image.fromarray(gray, "L")
    if abs(angle) >= MIN_SKEW_DEGREES:
        image = image.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
    else:
        angle = 0.0
    return image, angle


def straighten_page(page, angle: float) -> None:
    """Rotate the page's content counter-clockwise by `angle` degrees around the page centre.

    The original content streams are left untouched: two new streams are added around
    them ("q <rotation> cm" ... "Q"), so the scanned image is not re-encoded.
    """
    doc = page.parent
    centre = ((page.rect.tl + page.rect.br) / 2) * ~page.transformation_matrix  # page -> PDF space
    rad = math.radians(angle)
    cos, sin = math.cos(rad), math.sin(rad)
    # T(centre) . R(angle) . T(-centre), with y pointing up as in PDF space
    e = centre.x - cos * centre.x + sin * centre.y
    f = centre.y - sin * centre.x - cos * centre.y
    head = doc.get_new_xref()
    doc.update_object(head, "<<>>")
    doc.update_stream(head, f"q {cos:.6f} {sin:.6f} {-sin:.6f} {cos:.6f} {e:.4f} {f:.4f} cm\n".encode())
    tail = doc.get_new_xref()
    doc.update_object(tail, "<<>>")
    doc.update_stream(tail, b"\nQ\n")
    streams = [head, *page.get_contents(), tail]
    doc.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in streams) + "]")


def overlay_text_layer(page, text_pdf: Path) -> None:
    """Place Tesseract's text-only page (horizontal) over the whole page."""
    with pymupdf.open(str(text_pdf)) as text_doc:
        if text_doc[0].rect.width > 0 and text_doc[0].rect.height > 0:
            page.show_pdf_page(page.rect, text_doc, 0, keep_proportion=False, overlay=True)


def count_scripts(text: str) -> tuple[int, int]:
    """(Latin words, Arabic words) among words of 3+ letters."""
    latin = arabic = 0
    for word in text.split():
        latin_letters = len(re.findall(r"[A-Za-z]", word))
        arabic_letters = len(re.findall(r"[؀-ۿ]", word))
        if max(latin_letters, arabic_letters) < 3:
            continue
        if latin_letters > arabic_letters:
            latin += 1
        else:
            arabic += 1
    return latin, arabic


# --------------------------------------------------------------------------- engine


@dataclass
class _Attempt:
    """A finished straightened reading of a page, kept to compare with the unstraightened one."""
    text_pdf: Path
    angle: float
    words: int


@dataclass
class _PageJob:
    index: int
    angle: float
    base: Path
    stderr: Path
    proc: subprocess.Popen
    started: float
    straightened: _Attempt | None = None  # set on the second (unstraightened) reading


def count_words(text_pdf: Path) -> int:
    """Words with at least two Arabic or Latin letters in a Tesseract text-only PDF."""
    try:
        with pymupdf.open(str(text_pdf)) as text_doc:
            text = text_doc[0].get_text("text")
    except Exception:
        return 0
    return sum(1 for word in text.split() if len(re.findall(r"[A-Za-z؀-ۿ]", word)) >= 2)


class OcrEngine:
    """OCRs one PDF at a time; pages run in parallel Tesseract processes."""

    def __init__(self, config: AppConfig, stop_event: threading.Event) -> None:
        self.config = config
        self.stop_event = stop_event
        self._tesseract = find_tesseract(config)
        self._env = build_env(config, self._tesseract)
        self._env["OMP_THREAD_LIMIT"] = "1"  # one thread per Tesseract; parallelism comes from `jobs`
        self._jobs = config.ocr.jobs or max(1, os.cpu_count() or 1)
        self._dpi = config.ocr.resolution
        self._long_side = round(A4_LONG_SIDE_INCHES * self._dpi)

    def run(
        self, input_pdf: Path, output_pdf: Path, log_path: Path, progress: ProgressCallback | None = None
    ) -> OcrResult:
        """OCR input_pdf into output_pdf. Per-page details are written to log_path."""
        if self._tesseract is None:
            return OcrResult("failed", "Tesseract OCR was not found")
        work = log_path.with_name(log_path.stem + "_pages")
        try:
            work.mkdir(parents=True, exist_ok=True)
            with open(log_path, "w", encoding="utf-8") as log:
                return self._run(Path(input_pdf), Path(output_pdf), work, log, progress)
        except OSError as exc:
            return OcrResult("failed", f"file error during OCR: {exc}")
        finally:
            for item in work.glob("*"):
                try:
                    item.unlink()
                except OSError:
                    pass
            try:
                work.rmdir()
            except OSError:
                pass

    def _run(self, input_pdf: Path, output_pdf: Path, work: Path, log: TextIO,
             progress: ProgressCallback | None) -> OcrResult:
        started = time.monotonic()
        timeout = self.config.ocr.file_timeout_minutes * 60
        doc = pymupdf.open(str(input_pdf))
        running: list[_PageJob] = []
        try:
            todo = [
                i for i, page in enumerate(doc)
                if not (self.config.ocr.skip_pages_with_text
                        and len(page.get_text("text").strip()) >= MIN_EXISTING_TEXT)
            ]
            log.write(f"{doc.page_count} pages, {len(todo)} without text\n")
            languages = self._choose_languages(doc, todo, work, log)
            if languages is None:
                return OcrResult("cancelled", "OCR was interrupted because the application is stopping")

            pending = list(todo)
            failed: list[int] = []
            done = 0
            if progress:
                progress(0, len(todo))
            while pending or running:
                if self.stop_event.is_set():
                    return OcrResult("cancelled", "OCR was interrupted because the application is stopping")
                if timeout and time.monotonic() - started > timeout:
                    return OcrResult(
                        "failed",
                        f"OCR did not finish within {self.config.ocr.file_timeout_minutes} minutes "
                        "(increase [ocr] file_timeout_minutes for very large files)",
                    )
                while pending and len(running) < self._jobs and not self.stop_event.is_set():
                    index = pending.pop(0)
                    try:
                        running.append(self._start_page(doc, index, languages, work))
                    except Exception as exc:  # a damaged page must not stop the others
                        failed.append(index + 1)
                        done += 1
                        log.write(f"page {index + 1}: could not be prepared: {exc}\n")

                still_running: list[_PageJob] = []
                for job in running:
                    state = self._check_job(job, doc, log)
                    if state == "running":
                        still_running.append(job)
                        continue
                    if state == "verify":
                        attempt = _Attempt(job.base.with_suffix(".pdf"), job.angle, count_words(job.base.with_suffix(".pdf")))
                        try:
                            still_running.append(self._start_page(doc, job.index, languages, work, straightened=attempt))
                            continue
                        except Exception as exc:
                            log.write(f"page {job.index + 1}: second reading could not be prepared: {exc}\n")
                            state = self._apply(doc, job.index, attempt.text_pdf, attempt.angle, log)
                    if state == "failed":
                        failed.append(job.index + 1)
                    done += 1
                    if progress:
                        progress(done, len(todo))
                running = still_running
                if running:
                    self.stop_event.wait(0.2)

            doc.save(str(output_pdf), garbage=3, deflate=True)
            message = f"language {languages}"
            if failed:
                message += f"; OCR failed on page(s) {', '.join(map(str, sorted(failed)[:20]))}"
            log.write(message + "\n")
            return OcrResult("ok", message)
        finally:
            for job in running:
                self._kill(job.proc)
            doc.close()

    def _start_page(self, doc, index: int, languages: str, work: Path,
                    straightened: _Attempt | None = None) -> _PageJob:
        """Start Tesseract on a page. With `straightened`, this is the second reading, without straightening."""
        straighten = self.config.ocr.straighten_pages and straightened is None
        image, angle = prepare_page_image(doc[index], self._long_side, straighten)
        name = f"p{index:05d}" + ("u" if straightened else "")
        png = work / f"{name}.png"
        image.save(png, dpi=(self._dpi, self._dpi))
        base = work / name
        stderr_path = work / f"{name}.err"
        with open(stderr_path, "wb") as stderr:
            proc = subprocess.Popen(
                [str(self._tesseract), str(png), str(base), "-l", languages,
                 "--psm", str(self.config.ocr.page_segmentation_mode), "--dpi", str(self._dpi),
                 "-c", "textonly_pdf=1", "pdf"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr,
                env=self._env, creationflags=hidden_window_flags(),
            )
        return _PageJob(index, angle, base, stderr_path, proc, time.monotonic(), straightened)

    def _check_job(self, job: _PageJob, doc, log: TextIO) -> str:
        """'running', 'done' (text layer added), 'failed', or 'verify' (also read it unstraightened)."""
        code = job.proc.poll()
        if code is None:
            if time.monotonic() - job.started <= self.config.ocr.tesseract_timeout:
                return "running"
            self._kill(job.proc)
            log.write(f"page {job.index + 1}: Tesseract timed out after {self.config.ocr.tesseract_timeout} s\n")
            if job.straightened:
                return self._apply(doc, job.index, job.straightened.text_pdf, job.straightened.angle, log)
            return "failed"
        text_pdf = job.base.with_suffix(".pdf")
        if code != 0 or not text_pdf.is_file():
            try:
                detail = job.stderr.read_text(encoding="utf-8", errors="replace").strip()[-300:]
            except OSError:
                detail = ""
            log.write(f"page {job.index + 1}: Tesseract failed (exit code {code}): {detail}\n")
            if job.straightened:  # the second reading failed: keep the first one
                return self._apply(doc, job.index, job.straightened.text_pdf, job.straightened.angle, log)
            return "failed"

        if job.straightened is None:
            # The skew estimate can be fooled (watermarks, frames, diagrams). For a clearly
            # tilted page, or when straightening found almost nothing, the page is read a
            # second time as it is and the reading with more words wins.
            if abs(job.angle) >= VERIFY_SKEW_DEGREES or (job.angle and count_words(text_pdf) < 3):
                return "verify"
            return self._apply(doc, job.index, text_pdf, job.angle, log)

        first, plain = job.straightened, count_words(text_pdf)
        if first.words > plain:
            log.write(f"page {job.index + 1}: straightened reading kept ({first.words} vs {plain} words)\n")
            return self._apply(doc, job.index, first.text_pdf, first.angle, log)
        log.write(f"page {job.index + 1}: page left as it is ({plain} words vs {first.words} when "
                  f"straightened by {first.angle:.1f} degrees)\n")
        return self._apply(doc, job.index, text_pdf, 0.0, log)

    @staticmethod
    def _apply(doc, index: int, text_pdf: Path, angle: float, log: TextIO) -> str:
        """Straighten the page (if angle) and add the text layer. Returns 'done' or 'failed'."""
        try:
            if angle:
                straighten_page(doc[index], angle)
            overlay_text_layer(doc[index], text_pdf)
        except Exception as exc:
            log.write(f"page {index + 1}: text layer could not be added: {exc}\n")
            return "failed"
        if angle:
            log.write(f"page {index + 1}: straightened by {angle:.1f} degrees\n")
        return "done"

    def _choose_languages(self, doc, todo: list[int], work: Path, log: TextIO) -> str | None:
        """The configured languages, or for `auto` Arabic vs English detected from sample pages."""
        setting = self.config.ocr.languages
        if setting != "auto":
            return setting
        if not todo:
            return AUTO_ARABIC
        count = min(AUTO_SAMPLE_PAGES, len(todo))
        sample = sorted({todo[int(len(todo) * (k + 1) / (count + 1))] for k in range(count)})
        latin = arabic = 0
        for index in sample:
            if self.stop_event.is_set():
                return None
            try:
                image, _ = prepare_page_image(doc[index], self._long_side)
                png = work / f"lang{index:05d}.png"
                image.save(png, dpi=(self._dpi, self._dpi))
                completed = subprocess.run(
                    [str(self._tesseract), str(png), "stdout", "-l", "ara+eng", "--dpi", str(self._dpi)],
                    stdin=subprocess.DEVNULL, capture_output=True, env=self._env,
                    timeout=self.config.ocr.tesseract_timeout, creationflags=hidden_window_flags(),
                )
                page_latin, page_arabic = count_scripts(completed.stdout.decode("utf-8", errors="replace"))
                latin += page_latin
                arabic += page_arabic
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                log.write(f"language detection on page {index + 1} failed: {exc}\n")
        ratio = latin / (latin + arabic) if latin + arabic else 0.0
        chosen = AUTO_ENGLISH if ratio >= AUTO_ENGLISH_RATIO else AUTO_ARABIC
        log.write(f"language detection: {latin} Latin / {arabic} Arabic words on pages "
                  f"{[i + 1 for i in sample]} -> {chosen}\n")
        return chosen

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:
            proc.kill()
            proc.wait(timeout=10)
        except (OSError, subprocess.SubprocessError):
            logger.warning("Tesseract process %s did not exit after being killed", proc.pid)

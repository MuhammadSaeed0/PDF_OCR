"""Creates scanned-looking test PDFs (pages are images, no text layer).

Examples (run from the project folder):
    .venv\\Scripts\\python tools\\make_test_pdfs.py                       3 PDFs into the source folder
    .venv\\Scripts\\python tools\\make_test_pdfs.py --start 4 --count 1   a 4th PDF
    .venv\\Scripts\\python tools\\make_test_pdfs.py --corrupted           a damaged "PDF"
    .venv\\Scripts\\python tools\\make_test_pdfs.py --encrypted           a password-protected PDF
    .venv\\Scripts\\python tools\\make_test_pdfs.py --large 150 --out D:\\Projects\\PDF_OCR\\Staging
                                                                         a large PDF to copy in by hand
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from pdf_ocr.config import ConfigError, load_config  # noqa: E402
from pdf_ocr.samples import make_scanned_pdf, sample_page_text  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", help="output folder (default: source_folder from config.ini)")
    parser.add_argument("--count", type=int, default=3, help="number of normal sample PDFs (default 3)")
    parser.add_argument("--start", type=int, default=1, help="number of the first sample (default 1)")
    parser.add_argument("--pages", type=int, default=2, help="pages per sample PDF (default 2)")
    parser.add_argument("--corrupted", action="store_true", help="only create corrupted_sample.pdf")
    parser.add_argument("--encrypted", action="store_true", help="only create password_protected_sample.pdf")
    parser.add_argument("--large", type=int, metavar="PAGES", help="only create large_sample.pdf with PAGES pages")
    parser.add_argument("--dpi", type=int, default=200, help="scan resolution (default 200)")
    args = parser.parse_args()

    if args.out:
        out_dir = Path(args.out)
    else:
        try:
            out_dir = load_config(APP_DIR / "config.ini").source_folder
        except ConfigError as exc:
            print(f"Configuration error: {exc}")
            return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.corrupted:
        path = out_dir / "corrupted_sample.pdf"
        path.write_bytes(b"This is not a real PDF file. " * 200)
        print(f"Created {path}")
        return 0
    if args.encrypted:
        path = out_dir / "password_protected_sample.pdf"
        make_scanned_pdf(path, [sample_page_text(900, 1, 1)], args.dpi, user_password="secret")
        print(f"Created {path} (password: secret)")
        return 0
    if args.large:
        path = out_dir / "large_sample.pdf"
        print(f"Creating {path} with {args.large} pages at 300 dpi (this takes a while)...")
        make_scanned_pdf(path, [sample_page_text(999, p, args.large) for p in range(1, args.large + 1)], 300)
        print(f"Created {path} ({path.stat().st_size // (1024 * 1024)} MB)")
        return 0

    for number in range(args.start, args.start + args.count):
        path = out_dir / f"sample_scan_{number}.pdf"
        make_scanned_pdf(path, [sample_page_text(number, p, args.pages) for p in range(1, args.pages + 1)], args.dpi)
        print(f"Created {path}  (search for ZEBRA{number:03d} after OCR)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

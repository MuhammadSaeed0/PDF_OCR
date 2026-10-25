"""Makes sure Tesseract has the language data for the configured OCR languages (e.g. Arabic).

    .venv\\Scripts\\python.exe tools\\setup_tessdata.py [--variant best|fast|standard] [--force]

If a language is missing, this creates the project folder .\\tessdata (no administrator
rights needed), copies Tesseract's existing data files and configs into it, and
downloads the missing *.traineddata files from the official tesseract-ocr GitHub
repositories. PDF OCR uses .\\tessdata automatically when it exists.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from pdf_ocr.config import ConfigError, load_config  # noqa: E402
from pdf_ocr.dependencies import build_env, effective_tessdata_dir, find_tesseract, tesseract_languages  # noqa: E402

REPOSITORIES = {
    "best": "tessdata_best",       # most accurate (recommended for Arabic)
    "fast": "tessdata_fast",       # faster, slightly less accurate
    "standard": "tessdata",        # what the Windows installer ships
}
MIN_TRAINEDDATA_BYTES = 50_000


def download(lang: str, variant: str, target: Path) -> None:
    repo = REPOSITORIES[variant]
    errors = []
    for branch in ("main", "master"):
        url = f"https://raw.githubusercontent.com/tesseract-ocr/{repo}/{branch}/{lang}.traineddata"
        tmp = target.with_name(target.name + ".part")
        try:
            print(f"  downloading {url}")
            with urllib.request.urlopen(url, timeout=120) as response, open(tmp, "wb") as fh:
                shutil.copyfileobj(response, fh)
            if tmp.stat().st_size < MIN_TRAINEDDATA_BYTES:
                raise OSError("downloaded file is too small to be language data")
            os.replace(tmp, target)
            print(f"  saved {target} ({target.stat().st_size // 1024} KB)")
            return
        except OSError as exc:  # urllib errors are OSError subclasses
            errors.append(f"{url}: {exc}")
            try:
                tmp.unlink()
            except OSError:
                pass
    raise RuntimeError("could not download '%s':\n    %s" % (lang, "\n    ".join(errors)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(APP_DIR / "config.ini"))
    parser.add_argument("--variant", choices=sorted(REPOSITORIES), default="best",
                        help="which model set to download for missing languages (default: best)")
    parser.add_argument("--langs", help="languages to ensure, e.g. eng+ara (default: from config.ini)")
    parser.add_argument("--force", action="store_true", help="download the languages even if present")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2
    tesseract = find_tesseract(config)
    if tesseract is None:
        print("Tesseract OCR is not installed (or not found). Install it first - see README.md.")
        return 1

    if args.langs:
        wanted = [x for x in args.langs.replace(",", "+").split("+") if x]
    else:
        wanted = config.ocr.language_list  # languages = auto needs ara and eng

    env = build_env(config, tesseract)
    installed = set(tesseract_languages(tesseract, env) or [])
    missing = [lang for lang in wanted if args.force or lang not in installed]
    if not missing:
        print(f"All languages are available: {', '.join(wanted)}")
        return 0

    target_dir = config.tessdata_dir or APP_DIR / "tessdata"
    current_dir = effective_tessdata_dir(config)
    print(f"Missing language data: {', '.join(missing)}")
    print(f"Language data folder: {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)

    if current_dir is None and config.tessdata_dir is None:
        # First use of .\tessdata: copy Tesseract's own data and configs so nothing is lost
        # (TESSDATA_PREFIX replaces the default folder; 'configs' and pdf.ttf are needed for PDF output).
        system_dir = tesseract.parent / "tessdata"
        if system_dir.is_dir():
            print(f"Copying existing language data from {system_dir}")
            for item in system_dir.iterdir():
                destination = target_dir / item.name
                if item.is_dir():
                    if item.name in ("configs", "tessconfigs"):
                        shutil.copytree(item, destination, dirs_exist_ok=True)
                elif not destination.exists():
                    shutil.copy2(item, destination)
        else:
            print(f"WARNING: Tesseract's data folder {system_dir} was not found; only downloaded files will exist.")

    failed = []
    for lang in missing:
        try:
            download(lang, args.variant, target_dir / f"{lang}.traineddata")
        except RuntimeError as exc:
            failed.append(lang)
            print(f"ERROR: {exc}")

    env = build_env(config, tesseract)
    installed = set(tesseract_languages(tesseract, env) or [])
    still_missing = [lang for lang in wanted if lang not in installed]
    if still_missing or failed:
        print(f"\nStill missing: {', '.join(still_missing or failed)}")
        print("Download the .traineddata files manually (see README.md, 'OCR language data').")
        return 1
    print(f"\nDone. Available languages: {', '.join(sorted(installed))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

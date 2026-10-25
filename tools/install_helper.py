"""Small helper used by install.bat.

    python tools\\install_helper.py find tesseract    -> prints the path, exit 1 if not found
    python tools\\install_helper.py make-folders      -> creates the source/destination folders
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from pdf_ocr.config import ConfigError, load_config  # noqa: E402
from pdf_ocr.dependencies import find_tesseract  # noqa: E402


def main(argv: list[str]) -> int:
    try:
        config = load_config(APP_DIR / "config.ini")
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2

    if argv == ["find", "tesseract"]:
        found = find_tesseract(config)
        if found is None:
            return 1
        print(found)
        return 0

    if argv == ["make-folders"]:
        for folder in (config.source_folder, config.destination_folder):
            try:
                folder.mkdir(parents=True, exist_ok=True)
                print(f"Folder ready: {folder}")
            except OSError as exc:
                print(f"Could not create {folder}: {exc}")
                return 1
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Small helper used by install.bat.

    python tools\\install_helper.py find tesseract|ghostscript   -> prints the path, exit 1 if not found
    python tools\\install_helper.py make-folders                  -> creates the source/destination folders
    python tools\\install_helper.py install-ghostscript           -> downloads the latest official 64-bit
                                                                    Ghostscript installer and runs it silently
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from pdf_ocr.config import ConfigError, load_config  # noqa: E402
from pdf_ocr.dependencies import find_ghostscript, find_tesseract  # noqa: E402

GHOSTSCRIPT_RELEASES_API = "https://api.github.com/repos/ArtifexSoftware/ghostpdl-downloads/releases/latest"


def install_ghostscript() -> int:
    """Download the newest gsXXXXXw64.exe from Artifex's official GitHub releases and install it.

    Ghostscript is not available from winget, so install.bat uses this instead.
    """
    try:
        request = urllib.request.Request(GHOSTSCRIPT_RELEASES_API, headers={"User-Agent": "pdf-ocr-installer"})
        with urllib.request.urlopen(request, timeout=60) as response:
            release = json.load(response)
        asset = next(a for a in release["assets"] if a["name"].lower().endswith("w64.exe"))
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        print(f"Could not find the Ghostscript download: {exc}")
        return 1

    folder = Path(tempfile.mkdtemp(prefix="pdf_ocr_gs_"))
    installer = folder / asset["name"]
    try:
        print(f"Downloading {asset['browser_download_url']} ({asset['size'] // (1024 * 1024)} MB)...")
        with urllib.request.urlopen(asset["browser_download_url"], timeout=300) as response, \
                open(installer, "wb") as fh:
            shutil.copyfileobj(response, fh)
        print("Installing Ghostscript silently. Accept the Windows administrator prompt...")
        # /S = silent NSIS install into C:\Program Files\gs\; RunAs shows the UAC prompt.
        command = f"Start-Process -FilePath '{installer}' -ArgumentList '/S' -Verb RunAs -Wait"
        completed = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command])
        if completed.returncode != 0:
            print("The Ghostscript installer did not run (was the administrator prompt declined?).")
            return 1
    except OSError as exc:
        print(f"Ghostscript download/installation failed: {exc}")
        return 1
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return 0


def main(argv: list[str]) -> int:
    try:
        config = load_config(APP_DIR / "config.ini")
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2

    if len(argv) == 2 and argv[0] == "find" and argv[1] in ("tesseract", "ghostscript"):
        found = find_tesseract(config) if argv[1] == "tesseract" else find_ghostscript(config)
        if found is None:
            return 1
        print(found)
        return 0

    if argv == ["install-ghostscript"]:
        result = install_ghostscript()
        if result == 0 and find_ghostscript(config) is None:
            print("Ghostscript was installed but gswin64c.exe was not found under C:\\Program Files\\gs.")
            return 1
        return result

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

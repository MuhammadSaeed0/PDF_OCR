"""Locating and checking the external programs: Tesseract OCR, Ghostscript and OCR language data.

pip does NOT install these programs. They are found in this order:
  1. the folder configured in config.ini ([paths] tesseract_dir / ghostscript_dir),
  2. the PATH environment variable,
  3. the standard Windows install locations (Program Files, %LOCALAPPDATA%\\Programs).
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import APP_DIR, AppConfig

CREATE_NO_WINDOW = 0x08000000
IS_WINDOWS = os.name == "nt"


def hidden_window_flags() -> int:
    """creationflags that stop child processes from opening console windows on Windows."""
    return CREATE_NO_WINDOW if IS_WINDOWS else 0


def has_console() -> bool:
    """True when this process is attached to a console window (python.exe, not pythonw.exe)."""
    if not IS_WINDOWS:
        return sys.stdout is not None
    try:
        import ctypes

        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return False


def _install_roots() -> list[Path]:
    roots: list[Path] = []
    for var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(var)
        if value:
            roots.append(Path(value))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots += [Path(local) / "Programs", Path(local)]
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def _find_in_dir(folder: Path, names: list[str]) -> Path | None:
    if folder.is_file():
        return folder
    for sub in (folder, folder / "bin"):
        for name in names:
            candidate = sub / name
            if candidate.is_file():
                return candidate
    return None


def _version_key(path: Path) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", path.name)) or (0,)


def find_tesseract(config: AppConfig) -> Path | None:
    """Return the path of tesseract(.exe), or None when it cannot be found."""
    names = ["tesseract.exe"] if IS_WINDOWS else ["tesseract"]
    if config.tesseract_dir:
        return _find_in_dir(config.tesseract_dir, names)
    found = shutil.which("tesseract")
    if found:
        return Path(found)
    if IS_WINDOWS:
        for root in _install_roots():
            candidate = root / "Tesseract-OCR" / "tesseract.exe"
            if candidate.is_file():
                return candidate
    return None


def find_ghostscript(config: AppConfig) -> Path | None:
    """Return the path of the Ghostscript console executable, or None."""
    names = ["gswin64c.exe", "gswin32c.exe"] if IS_WINDOWS else ["gs"]
    if config.ghostscript_dir:
        return _find_in_dir(config.ghostscript_dir, names)
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    if IS_WINDOWS:
        for root in _install_roots():
            gs_root = root / "gs"
            if not gs_root.is_dir():
                continue
            for version_dir in sorted(gs_root.glob("gs*"), key=_version_key, reverse=True):
                found_exe = _find_in_dir(version_dir, names)
                if found_exe:
                    return found_exe
    return None


def effective_tessdata_dir(config: AppConfig) -> Path | None:
    """The tessdata folder to use: the configured one, else the project's .\\tessdata if populated."""
    if config.tessdata_dir:
        return config.tessdata_dir
    local = APP_DIR / "tessdata"
    if local.is_dir() and any(local.glob("*.traineddata")):
        return local
    return None


def build_env(
    config: AppConfig, tesseract: Path | None = None, ghostscript: Path | None = None
) -> dict[str, str]:
    """Environment for child processes: Tesseract/Ghostscript on PATH, TESSDATA_PREFIX if needed."""
    env = dict(os.environ)
    extra = [str(p.parent) for p in (tesseract, ghostscript) if p]
    if extra:
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    tessdata = effective_tessdata_dir(config)
    if tessdata:
        env["TESSDATA_PREFIX"] = str(tessdata)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run_quiet(cmd: list[str], env: dict[str, str] | None = None, timeout: float = 60) -> tuple[int, str]:
    """Run a short command without a console window; return (exit code, combined output)."""
    try:
        completed = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=env,
            timeout=timeout,
            creationflags=hidden_window_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)
    output = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
    return completed.returncode, output


def tesseract_version(exe: Path, env: dict[str, str]) -> str | None:
    code, output = run_quiet([str(exe), "--version"], env)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[0] if code == 0 and lines else None


def tesseract_languages(exe: Path, env: dict[str, str]) -> list[str] | None:
    code, output = run_quiet([str(exe), "--list-langs"], env)
    if code != 0:
        return None
    langs = []
    for line in output.splitlines():
        text = line.strip()
        # Skip the header ("List of available languages in ...") and any error lines.
        if not text or " " in text or text.lower().startswith("list"):
            continue
        langs.append(text)
    return langs


def ghostscript_version(exe: Path, env: dict[str, str]) -> str | None:
    code, output = run_quiet([str(exe), "--version"], env)
    return output.strip().splitlines()[0] if code == 0 and output.strip() else None


@dataclass
class PreflightReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def preflight(config: AppConfig) -> PreflightReport:
    """Check every runtime dependency before monitoring starts."""
    report = PreflightReport()

    if sys.version_info < (3, 10):
        report.errors.append(f"Python 3.11 or newer is required (running {sys.version.split()[0]}).")
    if importlib.util.find_spec("ocrmypdf") is None:
        report.errors.append(
            "The Python package 'ocrmypdf' is not installed. Run install.bat "
            "(or: .venv\\Scripts\\python -m pip install -r requirements.txt)."
        )
    if importlib.util.find_spec("pymupdf") is None and importlib.util.find_spec("fitz") is None:
        report.errors.append("The Python package 'PyMuPDF' is not installed. Run install.bat.")

    tesseract = find_tesseract(config)
    ghostscript = find_ghostscript(config)
    env = build_env(config, tesseract, ghostscript)

    if tesseract is None:
        where = f" in the configured folder {config.tesseract_dir}" if config.tesseract_dir else ""
        report.errors.append(
            f"Tesseract OCR was not found{where}. Install it (install.bat does this, or see README: "
            "'Installing Tesseract') or set [paths] tesseract_dir in config.ini."
        )
    else:
        version = tesseract_version(tesseract, env)
        if version is None:
            report.errors.append(f"Tesseract was found at {tesseract} but could not be started.")
        else:
            report.info.append(f"Tesseract: {tesseract} ({version})")
            match = re.search(r"(\d+)\.(\d+)", version)
            if match and int(match.group(1)) < 5:
                report.warnings.append(f"Tesseract {match.group(0)} is old; version 5.x is recommended.")
            languages = tesseract_languages(tesseract, env) or []
            needed = config.ocr.language_list + (["osd"] if config.ocr.rotate_pages else [])
            missing = [lang for lang in needed if lang not in languages]
            if missing:
                report.errors.append(
                    f"Tesseract language data is missing for: {', '.join(missing)} "
                    f"(installed: {', '.join(languages) or 'none'}). Run "
                    "'.venv\\Scripts\\python tools\\setup_tessdata.py' or see README: 'OCR language data'."
                )
            else:
                report.info.append(f"OCR languages available: {', '.join(needed)}")

    if ghostscript is None:
        where = f" in the configured folder {config.ghostscript_dir}" if config.ghostscript_dir else ""
        report.errors.append(
            f"Ghostscript was not found{where}. Install it (install.bat does this, or see README: "
            "'Installing Ghostscript') or set [paths] ghostscript_dir in config.ini."
        )
    else:
        version = ghostscript_version(ghostscript, env)
        if version is None:
            report.errors.append(f"Ghostscript was found at {ghostscript} but could not be started.")
        else:
            report.info.append(f"Ghostscript: {ghostscript} (version {version})")

    tessdata = effective_tessdata_dir(config)
    if tessdata:
        report.info.append(f"Tesseract language data folder: {tessdata}")
    return report

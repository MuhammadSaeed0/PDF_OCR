"""Loading, validating and updating the application configuration (config.ini)."""

from __future__ import annotations

import configparser
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = APP_DIR / "config.ini"

# Hidden folder inside the destination folder that holds work files. It lives on
# the same drive as the outputs so the finished PDF can be moved into place atomically.
TEMP_DIR_NAME = ".pdf_ocr_tmp"

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
AUTO_LANGUAGES = "auto"

_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9_/\-]+$")


class ConfigError(Exception):
    """Raised when the configuration is missing, malformed or invalid."""


@dataclass
class OcrSettings:
    """OCR options (see the [ocr] section of config.ini)."""

    languages: str = AUTO_LANGUAGES
    resolution: int = 300
    page_segmentation_mode: int = 3
    skip_pages_with_text: bool = True
    straighten_pages: bool = True
    jobs: int = 0
    tesseract_timeout: int = 300
    file_timeout_minutes: int = 360

    @property
    def language_list(self) -> list[str]:
        """Tesseract languages that must be installed."""
        if self.languages == AUTO_LANGUAGES:
            return ["ara", "eng"]
        return [lang for lang in self.languages.split("+") if lang]


@dataclass
class AppConfig:
    """All settings of the application, with paths already resolved."""

    config_path: Path
    source_folder: Path
    destination_folder: Path
    include_subfolders: bool
    ocr: OcrSettings
    scan_interval_seconds: int
    stable_seconds: int
    retry_delay_seconds: int
    max_retry_delay_seconds: int
    min_free_space_mb: int
    tesseract_dir: Path | None
    tessdata_dir: Path | None
    state_dir: Path
    log_dir: Path
    log_level: str
    max_log_size_mb: int
    log_backup_count: int

    @property
    def instance_id(self) -> str:
        """Short id of the (source, destination) pair; names the state and lock files."""
        key = f"{_norm(self.source_folder)}|{_norm(self.destination_folder)}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]

    @property
    def state_file(self) -> Path:
        return self.state_dir / f"state_{self.instance_id}.json"

    @property
    def lock_file(self) -> Path:
        return self.state_dir / f"instance_{self.instance_id}.lock"

    @property
    def temp_dir(self) -> Path:
        return self.destination_folder / TEMP_DIR_NAME

    @property
    def log_file(self) -> Path:
        return self.log_dir / "pdf_ocr.log"

    def validate_folders(self) -> None:
        """Check the source folder, create/check the destination, state and log folders.

        Raises ConfigError with a user-friendly message when something is wrong.
        """
        src, dst = self.source_folder, self.destination_folder
        if not src.exists():
            raise ConfigError(
                f"Source folder does not exist: {src}\n"
                f"Create it, or change 'source_folder' in {self.config_path}."
            )
        if not src.is_dir():
            raise ConfigError(f"Source path is not a folder: {src}")
        if same_path(src, dst):
            raise ConfigError("Source and destination folders must be different folders.")
        if self.include_subfolders and is_within(dst, src):
            raise ConfigError(
                "The destination folder is inside the source folder while include_subfolders = true; "
                "outputs would be picked up as new inputs. Choose a destination outside the source folder."
            )
        try:
            dst.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(f"Cannot create destination folder {dst}: {exc}") from exc
        probe = dst / f".pdf_ocr_write_test_{os.getpid()}"
        try:
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError as exc:
            raise ConfigError(f"Destination folder is not writable: {dst} ({exc})") from exc
        for folder in (self.state_dir, self.log_dir):
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ConfigError(f"Cannot create folder {folder}: {exc}") from exc


def _norm(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def same_path(a: Path | str, b: Path | str) -> bool:
    """True when both paths point to the same location (case-insensitive on Windows)."""
    return _norm(a) == _norm(b)


def is_within(child: Path | str, parent: Path | str) -> bool:
    """True when `child` is `parent` itself or located somewhere below it."""
    c, p = _norm(child), _norm(parent)
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:  # different drives
        return False


# --------------------------------------------------------------------------- parsing


def _get_str(cp: configparser.ConfigParser, section: str, key: str, default: str = "") -> str:
    value = cp.get(section, key, fallback=default)
    # Accept paths pasted with quotes (Explorer's "Copy as path").
    return value.strip().strip('"').strip()


def _get_int(
    cp: configparser.ConfigParser,
    section: str,
    key: str,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = _get_str(cp, section, key, str(default))
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"[{section}] {key} must be a whole number, got {raw!r}") from None
    if minimum is not None and value < minimum:
        raise ConfigError(f"[{section}] {key} must be at least {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"[{section}] {key} must be at most {maximum}, got {value}")
    return value


def _get_bool(cp: configparser.ConfigParser, section: str, key: str, default: bool) -> bool:
    raw = _get_str(cp, section, key, "").lower()
    if raw == "":
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"[{section}] {key} must be true or false, got {raw!r}")


def _get_choice(cp: configparser.ConfigParser, section: str, key: str, default: str, choices) -> str:
    raw = _get_str(cp, section, key, default).lower() or default
    if raw not in choices:
        raise ConfigError(f"[{section}] {key} must be one of {', '.join(choices)}; got {raw!r}")
    return raw


def _to_path(raw: str, base_dir: Path) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.is_absolute():
        path = base_dir / path
    return Path(os.path.abspath(path))


def _get_path(
    cp: configparser.ConfigParser, section: str, key: str, base_dir: Path, default: str = ""
) -> Path | None:
    raw = _get_str(cp, section, key, default)
    return _to_path(raw, base_dir) if raw else None


def _parse_languages(raw: str) -> str:
    if raw.strip().lower() in ("", AUTO_LANGUAGES):
        return AUTO_LANGUAGES
    langs = [part for part in re.split(r"[+,\s]+", raw) if part]
    if not langs:
        raise ConfigError("[ocr] languages must name at least one Tesseract language, e.g. eng+ara")
    for lang in langs:
        if not _LANGUAGE_RE.match(lang):
            raise ConfigError(f"[ocr] languages contains an invalid language code: {lang!r}")
    return "+".join(dict.fromkeys(langs))  # remove duplicates, keep order


def load_config(
    path: Path | str | None = None,
    source_override: str | None = None,
    destination_override: str | None = None,
) -> AppConfig:
    """Read and validate config.ini. Relative paths are resolved against the config file's folder."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    config_path = Path(os.path.abspath(config_path))
    if not config_path.is_file():
        raise ConfigError(f"Configuration file not found: {config_path}")

    cp = configparser.ConfigParser(interpolation=None, comment_prefixes=(";", "#"), strict=True)
    try:
        with open(config_path, encoding="utf-8-sig") as fh:
            cp.read_file(fh)
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{config_path} must be saved as UTF-8 text ({exc})") from exc
    except configparser.Error as exc:
        raise ConfigError(f"Cannot parse {config_path}: {exc}") from exc

    base = config_path.parent

    source = _to_path(source_override, Path.cwd()) if source_override else _get_path(
        cp, "folders", "source_folder", base
    )
    destination = _to_path(destination_override, Path.cwd()) if destination_override else _get_path(
        cp, "folders", "destination_folder", base
    )
    if source is None:
        raise ConfigError("[folders] source_folder is not set in config.ini")
    if destination is None:
        raise ConfigError("[folders] destination_folder is not set in config.ini")

    ocr = OcrSettings(
        languages=_parse_languages(_get_str(cp, "ocr", "languages", AUTO_LANGUAGES)),
        resolution=_get_int(cp, "ocr", "resolution", 300, 150, 600),
        page_segmentation_mode=_get_int(cp, "ocr", "page_segmentation_mode", 3, 1, 13),
        skip_pages_with_text=_get_bool(cp, "ocr", "skip_pages_with_text", True),
        straighten_pages=_get_bool(cp, "ocr", "straighten_pages", True),
        jobs=_get_int(cp, "ocr", "jobs", 0, 0, 256),
        tesseract_timeout=_get_int(cp, "ocr", "tesseract_timeout", 300, 10, 86400),
        file_timeout_minutes=_get_int(cp, "ocr", "file_timeout_minutes", 360, 0, 10080),
    )

    return AppConfig(
        config_path=config_path,
        source_folder=source,
        destination_folder=destination,
        include_subfolders=_get_bool(cp, "folders", "include_subfolders", False),
        ocr=ocr,
        scan_interval_seconds=_get_int(cp, "monitor", "scan_interval_seconds", 15, 2, 86400),
        stable_seconds=_get_int(cp, "monitor", "stable_seconds", 10, 1, 3600),
        retry_delay_seconds=_get_int(cp, "monitor", "retry_delay_seconds", 120, 10, 86400),
        max_retry_delay_seconds=_get_int(cp, "monitor", "max_retry_delay_seconds", 3600, 10, 7 * 86400),
        min_free_space_mb=_get_int(cp, "monitor", "min_free_space_mb", 1024, 0, 10**7),
        tesseract_dir=_get_path(cp, "paths", "tesseract_dir", base),
        tessdata_dir=_get_path(cp, "paths", "tessdata_dir", base),
        state_dir=_get_path(cp, "paths", "state_dir", base, "data") or base / "data",
        log_dir=_get_path(cp, "logging", "log_dir", base, "logs") or base / "logs",
        log_level=_get_choice(cp, "logging", "log_level", "info", tuple(x.lower() for x in LOG_LEVELS)).upper(),
        max_log_size_mb=_get_int(cp, "logging", "max_log_size_mb", 10, 1, 1000),
        log_backup_count=_get_int(cp, "logging", "backup_count", 5, 0, 100),
    )


# --------------------------------------------------------------------------- updating


def update_ini_values(path: Path | str, section: str, values: dict[str, str]) -> None:
    """Change `key = value` lines inside one section of config.ini, keeping all comments.

    Keys that do not exist yet are appended to the section (the section is created if needed).
    The file is replaced atomically.
    """
    path = Path(path)
    section = section.lower()
    pending = {k.lower(): str(v) for k, v in values.items()}
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    out: list[str] = []
    current: str | None = None

    def flush_pending() -> None:
        for key, value in pending.items():
            out.append(f"{key} = {value}")
        pending.clear()

    for line in lines:
        header = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if header:
            if current == section and pending:
                flush_pending()
            current = header.group(1).strip().lower()
            out.append(line)
            continue
        if current == section and pending:
            match = re.match(r"^\s*([^=:;#\s][^=:]*?)\s*[=:]", line)
            if match and match.group(1).strip().lower() in pending:
                key = match.group(1).strip()
                out.append(f"{key} = {pending.pop(key.lower())}")
                continue
        out.append(line)

    if pending:
        if current != section:
            out.extend(["", f"[{section}]"])
        flush_pending()

    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("\n".join(out) + "\n")
    os.replace(tmp, path)

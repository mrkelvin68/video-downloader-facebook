from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
import zipfile
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings

SETTINGS_ORGANIZATION = "VideoDownloader"
SETTINGS_APPLICATION = "VideoDownloaderFacebook"

LOG_CATEGORIES = ("app", "api", "download", "publishing", "error")
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_BACKUP_COUNT = 5
DEFAULT_RETENTION_DAYS = 30


class Redactor:
    """Thread-safe sanitizer that strips sensitive tokens and credentials."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.secrets: set[str] = set()

    def add(self, value: Any) -> None:
        val = str(value or "").strip()
        # Avoid treating short strings, digits, or common words as secrets
        if len(val) >= 8:
            with self._lock:
                self.secrets.add(val)

    def clean(self, value: Any) -> str:
        text = str(value if value is not None else "")
        if not text:
            return ""

        # Replace registered dynamic secrets (longer first)
        with self._lock:
            current_secrets = sorted(self.secrets, key=len, reverse=True)

        for secret in current_secrets:
            if secret in text:
                text = text.replace(secret, "[REDACTED]")

        # Redact Authorization: Bearer tokens
        text = re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1[REDACTED]", text)

        # Redact Facebook Graph API access tokens (EAA...)
        text = re.sub(r"EAA[A-Za-z0-9_\-]{15,}", "[REDACTED_FB_TOKEN]", text)

        # Redact Apify API tokens (apify_api_...)
        text = re.sub(r"apify_api_[A-Za-z0-9_\-]{15,}", "[REDACTED_APIFY_TOKEN]", text)

        # Redact key-value pairs (json, query params, headers, cli flags)
        text = re.sub(
            r"(?i)((?:access[_-]?token|api[_-]?key|client[_-]?secret|password|passwd|auth[_-]?token|refresh[_-]?token|session_key|secret)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,}]+",
            r"\1[REDACTED]",
            text,
        )

        # Redact URL query parameters for sensitive media signatures or tokens
        text = re.sub(
            r"(?<=[?&])((?:access_token|token|api_key|client_secret|sig|signature|_nc_ohc|_nc_oc|_nc_sig|oh|oe|token_hash)=)[^&#\s]+",
            r"\1[REDACTED]",
            text,
        )

        # Redact cookie parameters in strings
        text = re.sub(
            r"(?i)(--cookies(?:-from-browser)?\s+)[^\s]+",
            r"\1[REDACTED_COOKIE_PATH]",
            text,
        )
        text = re.sub(
            r"(?i)((?:c_user|xs|fr|sb|datr|sessionid)=)[^;\s&]+",
            r"\1[REDACTED]",
            text,
        )

        return text


redactor = Redactor()


class SafeFormatter(logging.Formatter):
    """Logging formatter that cleans secrets from messages and tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            formatted = super().format(record)
            return redactor.clean(formatted)
        except Exception:
            return "[FORMATTING_ERROR]"


class SafeRotatingFileHandler(RotatingFileHandler):
    """Rotating file handler that never crashes caller on I/O error."""

    def handleError(self, record: logging.LogRecord) -> None:
        try:
            if hasattr(sys, "stderr") and sys.stderr:
                sys.stderr.write(f"VDF Logging error on {self.baseFilename}\n")
        except Exception:
            pass


def get_default_log_dir() -> Path:
    """Return default log directory (%LOCALAPPDATA%\\VideoDownloaderFacebook\\logs)."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    elif os.name == "nt":
        base = Path.home() / "AppData" / "Local"
    else:
        base = Path.home() / ".local" / "share"
    return base / "VideoDownloaderFacebook" / "logs"


def validate_log_dir_writable(path: Path) -> tuple[bool, str | None]:
    """Test if a path is writable and valid for logging."""
    try:
        resolved = Path(path).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        test_file = resolved / f".perm_test_{os.getpid()}_{time.time_ns()}.tmp"
        test_file.write_text("test", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        return True, None
    except Exception as exc:
        return False, str(exc)


@dataclass
class LoggingConfig:
    enabled: bool = True
    level: str = "INFO"
    directory: Path = get_default_log_dir()
    max_bytes: int = DEFAULT_MAX_BYTES
    backup_count: int = DEFAULT_BACKUP_COUNT
    retention_days: int = DEFAULT_RETENTION_DAYS


class LoggingService:
    """Centralized diagnostic and security-compliant logging service."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.config = LoggingConfig()
        self.handlers: dict[str, SafeRotatingFileHandler] = {}
        self._error_broadcaster: SafeRotatingFileHandler | None = None
        self._initialized = False
        self._excepthook_installed = False

    def initialize(self, custom_config: LoggingConfig | None = None) -> Path:
        """Initialize or reconfigure logging from settings or custom config."""
        with self._lock:
            if custom_config is not None:
                self.config = custom_config
            else:
                self._load_from_settings()

            # Validate log directory
            is_valid, _ = validate_log_dir_writable(self.config.directory)
            if not is_valid:
                default_dir = get_default_log_dir()
                self.config.directory = default_dir
                validate_log_dir_writable(default_dir)

            self.config.directory.mkdir(parents=True, exist_ok=True)
            self._setup_handlers()
            self._install_excepthook()
            self._initialized = True
            return self.config.directory

    def _load_from_settings(self) -> None:
        settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
        enabled = settings.value("logging/enabled", True, type=bool)
        level = settings.value("logging/level", "INFO", type=str)
        if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            level = "INFO"
        dir_val = str(settings.value("logging/directory", "", type=str) or "").strip()
        custom_dir = Path(dir_val) if dir_val else get_default_log_dir()

        try:
            max_mb = int(settings.value("logging/max_size_mb", 10))
        except (ValueError, TypeError):
            max_mb = 10
        max_bytes = max(1, min(max_mb, 100)) * 1024 * 1024

        try:
            retention = int(settings.value("logging/retention_days", DEFAULT_RETENTION_DAYS))
        except (ValueError, TypeError):
            retention = DEFAULT_RETENTION_DAYS

        self.config = LoggingConfig(
            enabled=enabled,
            level=level,
            directory=custom_dir,
            max_bytes=max_bytes,
            backup_count=DEFAULT_BACKUP_COUNT,
            retention_days=max(1, retention),
        )

    def _setup_handlers(self) -> None:
        formatter = SafeFormatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
        log_dir = self.config.directory
        log_dir.mkdir(parents=True, exist_ok=True)

        target_level = getattr(logging, self.config.level.upper(), logging.INFO)
        diag_level = target_level if self.config.enabled else logging.ERROR

        # Clean existing handlers
        for category in LOG_CATEGORIES:
            logger = logging.getLogger(f"vdf.{category}")
            for h in list(logger.handlers):
                logger.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        self.handlers.clear()

        if self._error_broadcaster is not None:
            try:
                self._error_broadcaster.close()
            except Exception:
                pass
            self._error_broadcaster = None

        # 1. Error log handler (always active for WARNING & ERROR, regardless of diagnostic toggle)
        error_file = log_dir / "error.log"
        error_handler = SafeRotatingFileHandler(
            error_file,
            maxBytes=self.config.max_bytes,
            backupCount=self.config.backup_count,
            encoding="utf-8",
        )
        error_handler.setFormatter(formatter)
        error_handler.setLevel(logging.WARNING)
        self.handlers["error"] = error_handler
        self._error_broadcaster = error_handler

        error_logger = logging.getLogger("vdf.error")
        error_logger.setLevel(logging.WARNING)
        error_logger.propagate = False
        error_logger.addHandler(error_handler)

        # 2. Category handlers (app, api, download, publishing)
        for category in ("app", "api", "download", "publishing"):
            log_file = log_dir / f"{category}.log"
            handler = SafeRotatingFileHandler(
                log_file,
                maxBytes=self.config.max_bytes,
                backupCount=self.config.backup_count,
                encoding="utf-8",
            )
            handler.setFormatter(formatter)
            handler.setLevel(diag_level)
            self.handlers[category] = handler

            logger = logging.getLogger(f"vdf.{category}")
            logger.setLevel(diag_level)
            logger.propagate = False
            logger.addHandler(handler)
            # Route warnings/errors from every category into error.log
            logger.addHandler(self._error_broadcaster)

    def _install_excepthook(self) -> None:
        if self._excepthook_installed:
            return
        original_hook = sys.excepthook

        def uncaught_exception_handler(exc_type, exc_value, exc_traceback):
            try:
                err_logger = logging.getLogger("vdf.error")
                err_logger.critical(
                    "Unhandled exception",
                    exc_info=(exc_type, exc_value, exc_traceback),
                )
            except Exception:
                pass
            if callable(original_hook):
                original_hook(exc_type, exc_value, exc_traceback)

        sys.excepthook = uncaught_exception_handler
        self._excepthook_installed = True

    def close(self) -> None:
        """Close and detach all file handlers."""
        with self._lock:
            for category in LOG_CATEGORIES:
                logger = logging.getLogger(f"vdf.{category}")
                for h in list(logger.handlers):
                    logger.removeHandler(h)
                    try:
                        h.close()
                    except Exception:
                        pass
            self.handlers.clear()
            if self._error_broadcaster is not None:
                try:
                    self._error_broadcaster.close()
                except Exception:
                    pass
                self._error_broadcaster = None

    def get_logger(self, category: str = "app") -> logging.Logger:
        """Return named category logger."""
        cat = category if category in LOG_CATEGORIES else "app"
        return logging.getLogger(f"vdf.{cat}")

    def get_log_dir(self) -> Path:
        return self.config.directory

    def set_log_dir(self, new_dir: Path) -> tuple[bool, str | None]:
        """Switch logging directory, persisting setting."""
        resolved = Path(new_dir).resolve()
        is_valid, err = validate_log_dir_writable(resolved)
        if not is_valid:
            return False, err

        with self._lock:
            self.config.directory = resolved
            self._setup_handlers()
            settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
            settings.setValue("logging/directory", str(resolved))
            settings.sync()
            return True, None

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self.config.enabled = bool(enabled)
            self._setup_handlers()
            settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
            settings.setValue("logging/enabled", self.config.enabled)
            settings.sync()

    def set_level(self, level: str) -> None:
        normalized = level.upper() if level in ("DEBUG", "INFO", "WARNING", "ERROR") else "INFO"
        with self._lock:
            self.config.level = normalized
            self._setup_handlers()
            settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
            settings.setValue("logging/level", normalized)
            settings.sync()

    def set_max_size_mb(self, mb: int) -> None:
        max_bytes = max(1, min(int(mb), 100)) * 1024 * 1024
        with self._lock:
            self.config.max_bytes = max_bytes
            self._setup_handlers()
            settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
            settings.setValue("logging/max_size_mb", int(mb))
            settings.sync()

    def set_retention_days(self, days: int) -> None:
        retention = max(1, min(int(days), 365))
        with self._lock:
            self.config.retention_days = retention
            settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
            settings.setValue("logging/retention_days", retention)
            settings.sync()

    def get_directory_size_bytes(self) -> tuple[int, int]:
        """Return (total_bytes, file_count) of logs in active log directory."""
        log_dir = self.config.directory
        if not log_dir.is_dir():
            return 0, 0
        total_size = 0
        count = 0
        try:
            for item in log_dir.iterdir():
                if item.is_file() and (".log" in item.name.lower() or item.name.endswith(".zip")):
                    try:
                        total_size += item.stat().st_size
                        count += 1
                    except OSError:
                        pass
        except OSError:
            pass
        return total_size, count

    def export_logs_zip(self, output_path: Path) -> int:
        """Export all current log files into a sanitized zip file."""
        log_dir = self.config.directory
        if not log_dir.is_dir():
            return 0

        target = Path(output_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        exported_count = 0
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for item in sorted(log_dir.iterdir()):
                if item.is_file() and ".log" in item.name.lower():
                    try:
                        raw_content = item.read_text(encoding="utf-8", errors="replace")
                        sanitized = redactor.clean(raw_content)
                        zf.writestr(item.name, sanitized.encode("utf-8"))
                        exported_count += 1
                    except Exception:
                        pass
        return exported_count

    def clean_old_logs(self, max_days: int | None = None) -> int:
        """Delete rotated or expired log files older than retention policy."""
        log_dir = self.config.directory
        if not log_dir.is_dir():
            return 0

        retention = max_days if max_days is not None else self.config.retention_days
        threshold = time.time() - (retention * 86400)
        deleted_count = 0

        try:
            for item in list(log_dir.iterdir()):
                if not item.is_file():
                    continue
                name = item.name.lower()
                # Target rotated logs or old zip exports: e.g. app.log.1, error.log.2, or old .log files
                is_log = ".log" in name or name.endswith(".zip")
                if not is_log:
                    continue
                try:
                    mtime = item.stat().st_mtime
                    # Delete rotated backups older than retention, or if force cleanup:
                    if mtime < threshold or (max_days == 0 and any(name.endswith(f".{i}") for i in range(1, 10))):
                        item.unlink(missing_ok=True)
                        deleted_count += 1
                except OSError:
                    pass
        except OSError:
            pass
        return deleted_count


# Global singleton
logging_service = LoggingService()


# Public helper functions
def get_logger(category: str = "app") -> logging.Logger:
    return logging_service.get_logger(category)


def log_app(msg: str, level: int = logging.INFO, **kwargs: Any) -> None:
    try:
        logging_service.get_logger("app").log(level, msg, **kwargs)
    except Exception:
        pass


def log_download(msg: str, level: int = logging.INFO, **kwargs: Any) -> None:
    try:
        logging_service.get_logger("download").log(level, msg, **kwargs)
    except Exception:
        pass


def log_publishing(msg: str, level: int = logging.INFO, **kwargs: Any) -> None:
    try:
        logging_service.get_logger("publishing").log(level, msg, **kwargs)
    except Exception:
        pass


def log_error(msg: str, exc: Exception | None = None, **kwargs: Any) -> None:
    try:
        logging_service.get_logger("error").error(msg, exc_info=exc, **kwargs)
    except Exception:
        pass


def log_api_call(
    *,
    provider: str,
    method: str,
    endpoint: str,
    status: int | None = None,
    duration_ms: float | None = None,
    correlation_id: str | None = None,
    job_id: str | None = None,
    retry_count: int = 0,
    error_message: str | None = None,
    exc: Exception | None = None,
) -> None:
    """Record sanitized diagnostic metadata for third-party HTTP/API calls."""
    try:
        sanitized_endpoint = redactor.clean(endpoint)
        parts = [
            f"provider={provider}",
            f"method={method.upper()}",
            f"endpoint={sanitized_endpoint}",
        ]
        if status is not None:
            parts.append(f"status={status}")
        if duration_ms is not None:
            parts.append(f"duration={duration_ms:.1f}ms")
        if correlation_id:
            parts.append(f"cid={correlation_id}")
        if job_id:
            parts.append(f"job_id={job_id}")
        if retry_count > 0:
            parts.append(f"retry={retry_count}")
        if error_message:
            parts.append(f"error={redactor.clean(error_message)}")

        summary = " | ".join(parts)
        api_logger = logging_service.get_logger("api")

        if exc is not None or (status is not None and status >= 400):
            api_logger.warning(summary, exc_info=exc)
        else:
            api_logger.info(summary)
    except Exception:
        pass

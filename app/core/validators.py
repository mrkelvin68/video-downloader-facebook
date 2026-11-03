import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

INVALID_WINDOWS_NAME_CHARS = '<>:"/\\|?*'
RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def validate_windows_name(value: str, field: str) -> str:
    raw_value = value
    value = value.strip()
    if not value:
        raise ValueError(f"{field} không được để trống")
    if len(value) > 120:
        raise ValueError(f"{field} tối đa 120 ký tự")
    if raw_value != value or value[-1] in ". " or any(
        char in INVALID_WINDOWS_NAME_CHARS or ord(char) < 32 for char in value
    ):
        raise ValueError(f"{field} chứa ký tự không hợp lệ trên Windows")
    if value.split(".", 1)[0].upper() in RESERVED_WINDOWS_NAMES:
        raise ValueError(f"{field} dùng tên dành riêng của Windows")
    return value


def validate_output_dir(value: str, *, may_exist: bool) -> Path:
    path = Path(value).expanduser()
    if not value.strip() or not path.is_absolute():
        raise ValueError("Thư mục xuất phải là đường dẫn tuyệt đối")
    if path.exists() and (not path.is_dir() or not may_exist):
        raise ValueError("Thư mục xuất đã tồn tại hoặc không phải thư mục")
    writable_path = path if path.exists() else path.parent
    if not writable_path.is_dir() or not os.access(writable_path, os.W_OK):
        raise ValueError("Thư mục cha không tồn tại hoặc không có quyền ghi")
    return path.resolve()


def normalize_video_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), host, path, parts.query, ""))


def validate_facebook_url(url: str) -> bool:
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        _ = parts.port
        if parts.scheme.lower() not in {"http", "https"} or parts.username or parts.password:
            return False
        is_facebook_host = (
            host == "fb.watch"
            or host == "facebook.com"
            or host.endswith(".facebook.com")
        )
        return is_facebook_host and bool(parts.path.strip("/") or parts.query)
    except ValueError:
        return False

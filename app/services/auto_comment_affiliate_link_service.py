from __future__ import annotations

from datetime import UTC, datetime
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit
import uuid


class AffiliateLinkError(ValueError):
    pass


_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)
_TLD_RE = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9]+)$", re.IGNORECASE)
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".intranet", ".lan", ".home", ".corp")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _validate_scope(project_id: str, page_id: str) -> tuple[str, str]:
    clean_project = str(project_id or "").strip()
    clean_page = str(page_id or "").strip()
    if not clean_project or not clean_page:
        raise AffiliateLinkError("Hãy chọn Dự án và Fanpage hợp lệ.")
    return clean_project, clean_page


def _validate_name(name: str) -> str:
    if name is None:
        raise AffiliateLinkError("Tên link sản phẩm không được để trống.")
    clean_name = str(name).strip()
    if not clean_name:
        raise AffiliateLinkError("Tên link sản phẩm không được để trống.")
    return clean_name


def validate_affiliate_url(url: str) -> str:
    """Validate an HTTPS Affiliate URL without modifying or expanding the original URL string."""
    if not isinstance(url, str) or not url or not url.strip():
        raise AffiliateLinkError("URL sản phẩm không được để trống.")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise AffiliateLinkError("URL sản phẩm không được chứa khoảng trắng hoặc ký tự điều khiển.")
    if "\\" in url:
        raise AffiliateLinkError("URL sản phẩm không hợp lệ.")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise AffiliateLinkError("URL sản phẩm không hợp lệ.") from exc

    scheme = (parsed.scheme or "").lower()
    if scheme != "https" or not url[:8].lower() == "https://":
        raise AffiliateLinkError("Chỉ chấp nhận URL bắt đầu bằng https://.")

    if parsed.username is not None or parsed.password is not None or "@" in (parsed.netloc or ""):
        raise AffiliateLinkError("URL sản phẩm không được chứa thông tin đăng nhập.")

    try:
        port = parsed.port
    except ValueError as exc:
        raise AffiliateLinkError("Cổng (port) trong URL không hợp lệ.") from exc
    if port is not None and port <= 0:
        raise AffiliateLinkError("Cổng (port) trong URL không hợp lệ.")

    hostname = parsed.hostname
    if not hostname:
        raise AffiliateLinkError("Tên miền trong URL không hợp lệ.")

    host_lower = hostname.lower()
    if host_lower == "localhost" or host_lower.endswith(_LOCAL_SUFFIXES):
        raise AffiliateLinkError("Không chấp nhận địa chỉ nội bộ hoặc localhost.")

    try:
        ip_obj = ipaddress.ip_address(host_lower)
    except ValueError:
        ip_obj = None

    if ip_obj is not None:
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            raise AffiliateLinkError("Không chấp nhận địa chỉ IP nội bộ hoặc cục bộ.")
        raise AffiliateLinkError("URL sản phẩm phải sử dụng tên miền công khai hợp lệ.")

    if hostname.startswith(".") or hostname.endswith(".") or ".." in hostname or len(hostname) > 253:
        raise AffiliateLinkError("Tên miền trong URL không hợp lệ.")

    labels = hostname.split(".")
    if len(labels) < 2:
        raise AffiliateLinkError("Tên miền trong URL không hợp lệ.")
    if any(not _DNS_LABEL_RE.match(label) for label in labels):
        raise AffiliateLinkError("Tên miền trong URL không hợp lệ.")
    if not _TLD_RE.match(labels[-1]):
        raise AffiliateLinkError("Tên miền trong URL không hợp lệ.")

    return url


def normalize_affiliate_url_key(url: str) -> str:
    """Return a conservative comparison key that only lowercases scheme and hostname."""
    validated = validate_affiliate_url(url)
    parsed = urlsplit(validated)
    host_lower = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = host_lower if (port is None or port == 443) else f"{host_lower}:{port}"
    return urlunsplit(("https", netloc, parsed.path, parsed.query, parsed.fragment))


class AutoCommentAffiliateLinkService:
    def __init__(self, database):
        self.database = database

    def _find_conflicting_url(
        self,
        project_id: str,
        page_id: str,
        url: str,
        exclude_link_id: str | None = None,
    ) -> dict | None:
        target_key = normalize_affiliate_url_key(url)
        rows = self.database.connection.execute(
            "SELECT * FROM auto_comment_affiliate_links WHERE project_id=? AND page_id=?",
            (project_id, page_id),
        ).fetchall()
        for row in rows:
            if exclude_link_id is not None and row["id"] == exclude_link_id:
                continue
            try:
                existing_key = normalize_affiliate_url_key(row["url"])
            except AffiliateLinkError:
                existing_key = row["url"]
            if existing_key == target_key:
                return dict(row)
        return None

    def create_link(
        self,
        project_id: str,
        page_id: str,
        name: str,
        url: str,
        description: str | None = None,
        enabled: bool = True,
    ) -> str:
        clean_project, clean_page = _validate_scope(project_id, page_id)
        clean_name = _validate_name(name)
        exact_url = validate_affiliate_url(url)
        clean_description = str(description).strip() if description is not None else ""
        stored_description = clean_description or None

        if self._find_conflicting_url(clean_project, clean_page, exact_url) is not None:
            raise AffiliateLinkError("URL sản phẩm này đã tồn tại trong Fanpage đang chọn.")

        now = _now()
        link_id = str(uuid.uuid4())
        try:
            with self.database.connection:
                self.database.connection.execute(
                    "INSERT INTO auto_comment_affiliate_links VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        link_id,
                        clean_project,
                        clean_page,
                        clean_name,
                        exact_url,
                        stored_description,
                        int(bool(enabled)),
                        now,
                        now,
                    ),
                )
        except Exception as exc:
            raise AffiliateLinkError("URL sản phẩm này đã tồn tại trong Fanpage đang chọn.") from exc
        return link_id

    def list_links(
        self,
        project_id: str,
        page_id: str,
        search: str = "",
        enabled_only: bool = False,
    ) -> list[dict]:
        clean_project, clean_page = _validate_scope(project_id, page_id)
        sql = "SELECT * FROM auto_comment_affiliate_links WHERE project_id=? AND page_id=?"
        params: list[object] = [clean_project, clean_page]
        if enabled_only:
            sql += " AND enabled=1"
        sql += " ORDER BY updated_at DESC, name COLLATE NOCASE ASC"
        rows = [dict(row) for row in self.database.connection.execute(sql, tuple(params)).fetchall()]
        keyword = str(search or "").strip().casefold()
        if not keyword:
            return rows
        return [
            row
            for row in rows
            if keyword in (row.get("name") or "").casefold()
            or keyword in (row.get("url") or "").casefold()
            or keyword in (row.get("description") or "").casefold()
        ]

    def get_link(self, project_id: str, page_id: str, link_id: str) -> dict:
        clean_project, clean_page = _validate_scope(project_id, page_id)
        clean_id = str(link_id or "").strip()
        row = self.database.connection.execute(
            "SELECT * FROM auto_comment_affiliate_links WHERE id=? AND project_id=? AND page_id=?",
            (clean_id, clean_project, clean_page),
        ).fetchone()
        if row is None:
            raise AffiliateLinkError("Không tìm thấy link sản phẩm trong Fanpage đang chọn.")
        return dict(row)

    def update_link(
        self,
        project_id: str,
        page_id: str,
        link_id: str,
        name: str,
        url: str,
        description: str | None = None,
        enabled: bool = True,
    ) -> None:
        clean_project, clean_page = _validate_scope(project_id, page_id)
        clean_id = str(link_id or "").strip()
        self.get_link(clean_project, clean_page, clean_id)
        clean_name = _validate_name(name)
        exact_url = validate_affiliate_url(url)
        clean_description = str(description).strip() if description is not None else ""
        stored_description = clean_description or None

        if self._find_conflicting_url(clean_project, clean_page, exact_url, exclude_link_id=clean_id) is not None:
            raise AffiliateLinkError("URL sản phẩm này đã tồn tại trong Fanpage đang chọn.")

        try:
            with self.database.connection:
                cur = self.database.connection.execute(
                    """UPDATE auto_comment_affiliate_links
                       SET name=?, url=?, description=?, enabled=?, updated_at=?
                       WHERE id=? AND project_id=? AND page_id=?""",
                    (
                        clean_name,
                        exact_url,
                        stored_description,
                        int(bool(enabled)),
                        _now(),
                        clean_id,
                        clean_project,
                        clean_page,
                    ),
                )
        except Exception as exc:
            raise AffiliateLinkError("URL sản phẩm này đã tồn tại trong Fanpage đang chọn.") from exc
        if cur.rowcount != 1:
            raise AffiliateLinkError("Không tìm thấy link sản phẩm trong Fanpage đang chọn.")

    def delete_link(self, project_id: str, page_id: str, link_id: str) -> None:
        clean_project, clean_page = _validate_scope(project_id, page_id)
        clean_id = str(link_id or "").strip()
        with self.database.connection:
            cur = self.database.connection.execute(
                "DELETE FROM auto_comment_affiliate_links WHERE id=? AND project_id=? AND page_id=?",
                (clean_id, clean_project, clean_page),
            )
        if cur.rowcount != 1:
            raise AffiliateLinkError("Không tìm thấy link sản phẩm trong Fanpage đang chọn.")

    def set_link_enabled(self, project_id: str, page_id: str, link_id: str, enabled: bool) -> None:
        row = self.get_link(project_id, page_id, link_id)
        self.update_link(
            project_id,
            page_id,
            link_id,
            row["name"],
            row["url"],
            row.get("description"),
            enabled,
        )

"""Secure Meta Page connection and project-scoped Page selection."""
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.services.credential_service import (
    delete_meta_token, delete_page_token, get_meta_token, get_page_token,
    set_meta_token, set_page_token,
)
from app.services.facebook_publishing_service import FacebookPublishingError, FacebookPublishingService

REQUIRED_PERMISSIONS = {"pages_show_list", "pages_read_engagement", "pages_manage_posts"}


class FacebookConnectionError(FacebookPublishingError):
    def __init__(self, message: str, *, category: str = "api", code: str | None = None,
                 subcode: str | None = None, error_type: str | None = None,
                 http_status: int | None = None, fbtrace_id: str | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.code = code
        self.subcode = subcode
        self.error_type = error_type
        self.http_status = http_status
        self.fbtrace_id = fbtrace_id


class MetaFacebookConnectionClient:
    def __init__(self, token: str, *, api_version: str = "v26.0", timeout: float = 30.0) -> None:
        self.token = token.strip()
        self.base_url = f"https://graph.facebook.com/{api_version}"
        self.timeout = timeout
        self.diagnostics: list[dict[str, Any]] = []
        if not self.token:
            raise FacebookConnectionError("Vui lòng nhập Access Token hợp lệ.")

    def validate_and_list_pages(self) -> list[dict[str, Any]]:
        identity = self._get("me", fields="id,name")
        try:
            payload = self._get("me/accounts", fields="id,name,link,access_token,tasks,perms", limit=100)
        except FacebookConnectionError as discovery_error:
            page = self._get_page_identity(str(identity.get("id") or ""))
            if page is None:
                raise discovery_error
            return [page]
        pages = []
        for item in payload.get("data", []):
            page_id = str(item.get("id") or "")
            if not page_id:
                continue
            permissions = item.get("perms") or item.get("tasks") or []
            pages.append({
                "id": page_id,
                "name": str(item.get("name") or page_id),
                "url": str(item.get("link") or ""),
                "permissions": permissions,
                "access_token": str(item.get("access_token") or ""),
            })
        if not pages:
            page = self._get_page_identity(str(identity.get("id") or ""))
            if page is not None:
                return [page]
            raise FacebookConnectionError("Token hợp lệ nhưng không có Fanpage được cấp quyền.")
        return pages

    def _get_page_identity(self, page_id: str, *, raise_errors: bool = False) -> dict[str, Any] | None:
        if not page_id:
            return None
        try:
            page = self._get(page_id, fields="id,name,link")
        except FacebookConnectionError:
            if raise_errors:
                raise
            return None
        if str(page.get("id") or "") != page_id or not page.get("name"):
            return None
        permissions: list[Any] = []
        try:
            permission_payload = self._get(page_id, fields="tasks,perms")
            permissions = permission_payload.get("perms") or permission_payload.get("tasks") or []
        except FacebookConnectionError:
            # Page identity is sufficient for connection. Publishing access is
            # reported separately and must not turn into token expiration.
            permissions = []
        return {
            "id": page_id,
            "name": str(page["name"]),
            "url": str(page.get("link") or ""),
            "permissions": permissions,
            "access_token": self.token,
        }

    def test_page(self, page_id: str, page_token: str) -> dict[str, Any]:
        return MetaFacebookConnectionClient(page_token, api_version=self.base_url.rsplit("/", 1)[-1], timeout=self.timeout)._get(
            page_id, fields="id,name,link,tasks,perms"
        )

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        query = {**params, "access_token": self.token}
        request = Request(f"{self.base_url}/{path}?{urlencode(query)}", headers={"Accept": "application/json"})
        endpoint = f"/{path}"
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                self.diagnostics.append({"method": "GET", "endpoint": endpoint, "http_status": response.status, "result": "PASS"})
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            message = payload.get("error", {}).get("message", "Meta Graph API request failed")
            error_code = str(payload.get("error", {}).get("code"))
            category = "expired" if exc.code == 401 or error_code == "190" else "permission" if exc.code in {403} or error_code in {"10", "200"} else "api"
            error = payload.get("error", {})
            self.diagnostics.append({"method": "GET", "endpoint": endpoint, "http_status": exc.code, "meta_code": error_code, "meta_subcode": str(error.get("error_subcode") or "") or None, "meta_type": error.get("type"), "fbtrace_id": error.get("fbtrace_id"), "result": "FAILED"})
            friendly = {
                "expired": "Access Token đã hết hạn hoặc bị thu hồi.",
                "permission": "Token hợp lệ nhưng không có quyền đăng bài hoặc lấy Fanpage.",
            }.get(category, str(message))
            raise FacebookConnectionError(friendly, category=category, code=error_code, subcode=str(error.get("error_subcode") or "") or None, error_type=error.get("type"), http_status=exc.code, fbtrace_id=error.get("fbtrace_id")) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            self.diagnostics.append({"method": "GET", "endpoint": endpoint, "result": "FAILED", "error": "network"})
            raise FacebookConnectionError("Không thể kết nối Meta Graph API.", category="network") from exc
        if "error" in payload:
            error = payload["error"]
            error_code = str(error.get("code"))
            category = "expired" if error_code == "190" else "permission" if error_code in {"10", "200"} else "api"
            self.diagnostics.append({"method": "GET", "endpoint": endpoint, "http_status": 200, "meta_code": error_code, "meta_subcode": str(error.get("error_subcode") or "") or None, "meta_type": error.get("type"), "fbtrace_id": error.get("fbtrace_id"), "result": "FAILED"})
            friendly = {
                "expired": "Access Token đã hết hạn hoặc bị thu hồi.",
                "permission": "Token hợp lệ nhưng không có quyền đăng bài hoặc lấy Fanpage.",
            }.get(category, str(error.get("message", "Meta Graph API error")))
            raise FacebookConnectionError(friendly, category=category, code=error_code, subcode=str(error.get("error_subcode") or "") or None, error_type=error.get("type"), http_status=200, fbtrace_id=error.get("fbtrace_id"))
        return payload


class FacebookConnectionService:
    def __init__(self, database, client_factory=MetaFacebookConnectionClient) -> None:
        self.database = database
        self.client_factory = client_factory
        self.publishing = FacebookPublishingService(database)

    def connect(self, project_id: str, token: str) -> list[dict[str, Any]]:
        client = self.client_factory(token)
        pages = client.validate_and_list_pages()
        set_meta_token(token)
        self.publishing.save_pages(project_id, pages)
        for page in pages:
            if page.get("access_token"):
                set_page_token(project_id, page["id"], page["access_token"])
        return pages

    def test_connection(self, project_id: str) -> list[dict[str, Any]]:
        stored_pages = self.publishing.list_pages(project_id)
        if not stored_pages:
            token = get_meta_token()
            if not token:
                raise FacebookConnectionError("Chưa lưu thông tin kết nối Facebook.")
            return self.client_factory(token).validate_and_list_pages()
        validated: list[dict[str, Any]] = []
        for stored in stored_pages:
            token = get_page_token(project_id, stored["page_id"])
            if token:
                page = self.client_factory(token)._get_page_identity(stored["page_id"], raise_errors=True)
                if page is None:
                    raise FacebookConnectionError("Không thể xác thực Facebook. Vui lòng xem chi tiết lỗi API.", category="api")
                validated.append(page)
        if validated:
            return validated
        token = get_meta_token()
        if not token:
            raise FacebookConnectionError("Chưa lưu thông tin kết nối Facebook.")
        return self.client_factory(token).validate_and_list_pages()

    def set_default_page(self, project_id: str, page_id: str) -> None:
        pages = self.publishing.list_pages(project_id)
        if page_id not in {row["page_id"] for row in pages}:
            raise FacebookConnectionError("Fanpage không thuộc kết nối của dự án.")
        self.publishing.save_pages(project_id, [{"id": row["page_id"], "name": row["page_name"], "url": row["page_url"], "permissions": json.loads(row["permissions_json"])} for row in pages], page_id)

    def disconnect(self, project_id: str) -> None:
        for page in self.publishing.list_pages(project_id):
            delete_page_token(project_id, page["page_id"])
        with self.database.connection:
            self.database.connection.execute("DELETE FROM facebook_pages WHERE project_id=?", (project_id,))
        delete_meta_token()

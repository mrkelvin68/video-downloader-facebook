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
    pass


class MetaFacebookConnectionClient:
    def __init__(self, token: str, *, api_version: str = "v26.0", timeout: float = 30.0) -> None:
        self.token = token.strip()
        self.base_url = f"https://graph.facebook.com/{api_version}"
        self.timeout = timeout
        if not self.token:
            raise FacebookConnectionError("Vui lòng nhập Access Token hợp lệ.")

    def validate_and_list_pages(self) -> list[dict[str, Any]]:
        payload = self._get("me/accounts", fields="id,name,link,access_token,tasks,perms", limit=100)
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
            raise FacebookConnectionError("Token hợp lệ nhưng không có Fanpage được cấp quyền.")
        return pages

    def test_page(self, page_id: str, page_token: str) -> dict[str, Any]:
        return MetaFacebookConnectionClient(page_token, api_version=self.base_url.rsplit("/", 1)[-1], timeout=self.timeout)._get(
            page_id, fields="id,name,link,tasks,perms"
        )

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        query = {**params, "access_token": self.token}
        request = Request(f"{self.base_url}/{path}?{urlencode(query)}", headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            message = payload.get("error", {}).get("message", "Meta Graph API request failed")
            raise FacebookConnectionError(str(message)) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise FacebookConnectionError("Không thể kết nối Meta Graph API.") from exc
        if "error" in payload:
            raise FacebookConnectionError(str(payload["error"].get("message", "Meta Graph API error")))
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
        token = get_meta_token()
        if not token:
            raise FacebookConnectionError("Chưa lưu thông tin kết nối Facebook.")
        pages = self.client_factory(token).validate_and_list_pages()
        stored = {row["page_id"] for row in self.publishing.list_pages(project_id)}
        return [page for page in pages if page["id"] in stored] or pages

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

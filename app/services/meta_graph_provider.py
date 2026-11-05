from __future__ import annotations

import json
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from app.services.viral_reels_service import (
    DiscoveryRequest, PageCandidate, PageDiscoveryProvider, ReelMetadata, ReelsProvider, ProviderAuthenticationError,
    ProviderError, ProviderRateLimitError, normalize_facebook_url, stable_reel_id,
)

API_VERSION = "v26.0"


class MetaGraphReelsProvider(ReelsProvider, PageDiscoveryProvider):
    name = "Meta Graph API"
    requires_credentials = True
    paid = False
    capabilities = frozenset({"find_pages", "scan_by_page"})

    def __init__(self, token: str, page_id: str | None = None, timeout: float = 30.0) -> None:
        self.token = token.strip()
        self.page_id = page_id.strip() if page_id else None
        self.timeout = timeout
        if not self.token:
            raise ProviderAuthenticationError("Meta Graph API token is required")

    def _get(self, path: str, **params):
        query = urlencode(params)
        url = path if path.startswith("https://") else f"https://graph.facebook.com/{API_VERSION}/{path}"
        if query:
            url += ("&" if "?" in url else "?") + query
        request = Request(url, headers={"Authorization": "Bearer " + self.token})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode())
        except HTTPError as exc:
            details = {}
            try:
                details = json.loads(exc.read().decode("utf-8"))
            except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
                pass
            error = details.get("error", {}) if isinstance(details, dict) else {}
            message = str(error.get("message") or "Meta Graph API request failed")
            if exc.code in {401, 403} or str(error.get("code")) in {"10", "190", "200"}:
                raise ProviderAuthenticationError(message) from exc
            if exc.code == 429:
                raise ProviderRateLimitError("Meta Graph API rate limit reached") from exc
            raise ProviderError(f"Meta Graph API HTTP {exc.code}: {message}") from exc
        except (TimeoutError, URLError, json.JSONDecodeError) as exc:
            raise ProviderError("Meta Graph API request failed") from exc
        if "error" in payload:
            error = payload["error"]
            raise ProviderError(str(error.get("message", "Meta Graph API error")))
        return payload

    def discover_pages(self, request: DiscoveryRequest):
        found: dict[str, PageCandidate] = {}
        for keyword in request.keywords:
            path = "pages/search"
            params = {"q": keyword.strip(), "fields": "id,name,link", "limit": min(request.maximum_pages, 100)}
            while path and len(found) < request.maximum_pages:
                payload = self._get(path, **params)
                params = {}
                for item in payload.get("data", []):
                    page_id = str(item.get("id") or "")
                    name = str(item.get("name") or "")
                    page_url = item.get("link") or item.get("url")
                    if not page_id or not name or not page_url:
                        continue
                    try:
                        normalized = normalize_facebook_url(str(page_url))
                    except ValueError:
                        continue
                    found.setdefault(normalized, PageCandidate(
                        page_id, name, normalized,
                        request.country if request.country != "All" else "Unknown",
                        "Unknown", "", self.name,
                    ))
                path = payload.get("paging", {}).get("next") or ""
            if len(found) >= request.maximum_pages:
                break
        return list(found.values())[:request.maximum_pages]

    def test_connection(self) -> dict:
        account = self._get("me", fields="id,name")
        if self.page_id:
            page = self._get(self.page_id, fields="id,name")
            if str(page.get("id")) != self.page_id:
                raise ProviderError("Configured Page ID does not match returned Page ID")
            return {"account": account, "page": page}
        return {"account": account}

    def collect_reels(self, page: PageCandidate):
        if not page.page_id:
            raise ProviderError("Facebook Page ID is required for Meta Graph API scan")
        path = f"{page.page_id}/video_reels"
        while path:
            payload = self._get(
                path,
                fields="id,permalink_url,created_time,description,views,likes.summary(true),comments.summary(true),shares",
            )
            for item in payload.get("data", []):
                url = item.get("permalink_url")
                if not url:
                    continue
                normalized = normalize_facebook_url(url)
                timestamp = item.get("created_time")
                published = datetime.fromisoformat(timestamp.replace("Z", "+00:00")) if isinstance(timestamp, str) else None
                likes = item.get("likes") or {}
                comments = item.get("comments") or {}
                shares = item.get("shares") or {}
                yield ReelMetadata(
                    reel_id=str(item.get("id") or stable_reel_id(normalized)), reel_url=normalized,
                    page_id=page.page_id, page_name=page.page_name, page_url=page.page_url,
                    caption=str(item.get("description") or ""),
                    views=item.get("views"),
                    likes=(likes.get("summary") or {}).get("total_count") if isinstance(likes, dict) else None,
                    comments=(comments.get("summary") or {}).get("total_count") if isinstance(comments, dict) else None,
                    shares=shares.get("count") if isinstance(shares, dict) else None,
                    published_at=published, country=page.country, language=page.language,
                    topic=page.topic, data_source=self.name,
                )
            next_url = payload.get("paging", {}).get("next")
            path = next_url.rsplit("/", 1)[-1].split("?", 1)[0] if next_url else ""

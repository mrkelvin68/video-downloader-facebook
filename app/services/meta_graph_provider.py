from __future__ import annotations

import json
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from app.services.viral_reels_service import (
    PageCandidate, ReelMetadata, ReelsProvider, ProviderAuthenticationError,
    ProviderError, ProviderRateLimitError, normalize_facebook_url, stable_reel_id,
)

API_VERSION = "v26.0"


class MetaGraphReelsProvider(ReelsProvider):
    name = "Meta Graph API"
    requires_credentials = True
    paid = False
    capabilities = frozenset({"scan_by_page"})

    def __init__(self, token: str, page_id: str | None = None, timeout: float = 30.0) -> None:
        self.token = token.strip()
        self.page_id = page_id.strip() if page_id else None
        self.timeout = timeout
        if not self.token:
            raise ProviderAuthenticationError("Meta Graph API token is required")

    def _get(self, path: str, **params):
        query = urlencode(params)
        url = f"https://graph.facebook.com/{API_VERSION}/{path}?{query}" if query else f"https://graph.facebook.com/{API_VERSION}/{path}"
        request = Request(url, headers={"Authorization": "Bearer " + self.token})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode())
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise ProviderAuthenticationError("Meta token is invalid, expired, or lacks permission") from exc
            if exc.code == 429:
                raise ProviderRateLimitError("Meta Graph API rate limit reached") from exc
            raise ProviderError(f"Meta Graph API HTTP {exc.code}") from exc
        except (TimeoutError, URLError, json.JSONDecodeError) as exc:
            raise ProviderError("Meta Graph API request failed") from exc
        if "error" in payload:
            error = payload["error"]
            raise ProviderError(str(error.get("message", "Meta Graph API error")))
        return payload

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
            payload = self._get(path, fields="id,permalink_url,created_time")
            for item in payload.get("data", []):
                url = item.get("permalink_url")
                if not url:
                    continue
                normalized = normalize_facebook_url(url)
                timestamp = item.get("created_time")
                published = datetime.fromisoformat(timestamp.replace("Z", "+00:00")) if isinstance(timestamp, str) else None
                yield ReelMetadata(
                    reel_id=str(item.get("id") or stable_reel_id(normalized)), reel_url=normalized,
                    page_id=page.page_id, page_name=page.page_name, page_url=page.page_url,
                    caption="", published_at=published, country=page.country, language=page.language,
                    topic=page.topic, data_source=self.name,
                )
            next_url = payload.get("paging", {}).get("next")
            path = next_url.rsplit("/", 1)[-1].split("?", 1)[0] if next_url else ""

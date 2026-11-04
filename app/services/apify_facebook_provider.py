from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.services.viral_reels_service import (
    DiscoveryRequest, PageCandidate, PageDiscoveryProvider, ReelMetadata, ReelsProvider,
    ProviderAuthenticationError, ProviderError, normalize_facebook_url, stable_reel_id,
)

ACTOR_ID = "apify~facebook-reels-scraper"
PAGE_SEARCH_ACTOR_ID = "apify~facebook-search-scraper"


class ApifyFacebookPageSearchProvider(PageDiscoveryProvider):
    name = "Apify Facebook Search Scraper"
    requires_credentials = True
    paid = True
    capabilities = frozenset({"find_pages"})

    def __init__(self, token: str, timeout: float = 180.0) -> None:
        self.token = token.strip()
        self.timeout = timeout
        if not self.token:
            raise ProviderAuthenticationError("Apify API token is required")

    @staticmethod
    def build_payload(keywords: list[str] | tuple[str, ...], maximum: int, country: str = "All") -> dict:
        terms = [item.strip() for item in keywords if item.strip()][:10]
        return {"searchType": "pages", "categories": terms, "resultsLimit": max(1, min(int(maximum), 100))}

    def test_connection(self) -> None:
        request = Request("https://api.apify.com/v2/users/me", headers={"Authorization": "Bearer " + self.token})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise ProviderAuthenticationError("Apify authentication failed")
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise ProviderAuthenticationError("Apify authentication failed") from exc
            raise ProviderError(f"Apify connection test failed with HTTP {exc.code}") from exc
        except (TimeoutError, URLError) as exc:
            raise ProviderError("Apify connection test failed: network timeout or error") from exc

    def discover_pages(self, request: DiscoveryRequest) -> list[PageCandidate]:
        keywords = [item.strip() for item in request.keywords if item.strip()]
        if not keywords:
            return []
        maximum = max(1, min(request.maximum_pages, 100))
        payload = self.build_payload(keywords, maximum, request.country)
        url = f"https://api.apify.com/v2/acts/{quote(PAGE_SEARCH_ACTOR_ID, safe='~')}/run-sync-get-dataset-items"
        req = Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token}, method="POST")
        try:
            with urlopen(req, timeout=self.timeout) as response:
                items = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise ProviderAuthenticationError("Apify authentication failed") from exc
            raise ProviderError(f"Apify page search failed with HTTP {exc.code}") from exc
        except (TimeoutError, URLError, json.JSONDecodeError) as exc:
            raise ProviderError("Apify page search failed: network or response error") from exc
        found: dict[str, PageCandidate] = {}
        for item in items if isinstance(items, list) else []:
            page_url = item.get("url") or item.get("profile_url") or item.get("pageUrl")
            name = item.get("name") or item.get("pageName") or item.get("title")
            if not page_url or not name:
                continue
            try:
                normalized = normalize_facebook_url(str(page_url))
            except ValueError:
                continue
            found.setdefault(normalized, PageCandidate(str(item.get("id") or item.get("facebookId") or ""), str(name), normalized, request.country if request.country != "All" else "Unknown", "Unknown", "", self.name))
        return list(found.values())[:maximum]


class ApifyFacebookReelsProvider(ReelsProvider):
    name = "Apify Facebook Reels Scraper"
    requires_credentials = True
    paid = True
    capabilities = frozenset({"scan_by_page"})

    def __init__(self, token: str, timeout: float = 30.0) -> None:
        self.token = token.strip()
        self.timeout = timeout
        if not self.token:
            raise ProviderAuthenticationError("Apify API token is required")

    def test_connection(self) -> None:
        """Validate token without starting a paid actor run."""
        request = Request(
            "https://api.apify.com/v2/users/me",
            headers={"Authorization": "Bearer " + self.token},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise ProviderAuthenticationError("Apify authentication failed")
        except ProviderAuthenticationError:
            raise
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise ProviderAuthenticationError("Apify authentication failed") from exc
            if exc.code == 429:
                raise ProviderError("Apify rate limit reached") from exc
            raise ProviderError(f"Apify connection test failed with HTTP {exc.code}") from exc
        except (TimeoutError, URLError) as exc:
            raise ProviderError("Apify connection test failed: network timeout or error") from exc
        except Exception as exc:
            raise ProviderError("Apify connection test failed") from exc

    def _run(self, page_url: str) -> list[dict]:
        payload = json.dumps({"startUrls": [{"url": page_url}], "maxItems": 100}).encode()
        url = f"https://api.apify.com/v2/acts/{quote(ACTOR_ID, safe='~')}/run-sync-get-dataset-items"
        request = Request(url, data=payload, headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except Exception as exc:
            message = str(exc)
            if "401" in message or "403" in message:
                raise ProviderAuthenticationError("Apify authentication failed") from exc
            raise ProviderError(f"Apify request failed: {message}") from exc

    def collect_reels(self, page: PageCandidate):
        for item in self._run(page.page_url):
            url = item.get("shareable_url") or item.get("topLevelReelUrl") or item.get("reelUrl")
            if not url:
                continue
            normalized = normalize_facebook_url(url)
            timestamp = item.get("time") or item.get("creation_time")
            published = datetime.fromisoformat(timestamp.replace("Z", "+00:00")) if isinstance(timestamp, str) else None
            yield ReelMetadata(
                reel_id=str(item.get("video_id") or item.get("post_id") or stable_reel_id(normalized)),
                reel_url=normalized, page_id=page.page_id, page_name=page.page_name,
                page_url=page.page_url, caption=str(item.get("text") or ""),
                views=item.get("playCountRounded"), published_at=published,
                country=page.country, language=page.language, topic=page.topic,
                data_source=self.name,
            )



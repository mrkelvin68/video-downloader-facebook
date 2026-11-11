from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.services.logging_service import log_api_call, redactor
from app.services.viral_reels_service import (
    DiscoveryRequest, PageCandidate, PageDiscoveryProvider, ReelMetadata, ReelsProvider,
    ProviderAuthenticationError, ProviderError, normalize_facebook_url, normalize_view_count, stable_reel_id,
)

ACTOR_ID = "apify~facebook-reels-scraper"
PAGE_SEARCH_ACTOR_ID = "apify~facebook-search-scraper"
ACTIVE_RUN_STATUSES = frozenset({"READY", "RUNNING"})
TERMINAL_OK_STATUSES = frozenset({"SUCCEEDED", "ABORTED", "TIMED-OUT"})


class ApifyFacebookPageSearchProvider(PageDiscoveryProvider):
    name = "Apify Facebook Search Scraper"
    requires_credentials = True
    paid = True
    capabilities = frozenset({"find_pages"})

    def __init__(self, token: str, timeout: float = 180.0) -> None:
        self.token = token.strip()
        if self.token:
            redactor.add(self.token)
        self.timeout = timeout
        if not self.token:
            raise ProviderAuthenticationError("Apify API token is required")

    @staticmethod
    def build_payload(keywords: list[str] | tuple[str, ...], maximum: int, country: str = "All") -> dict:
        terms = [item.strip() for item in keywords if item.strip()][:10]
        return {"searchType": "pages", "categories": terms, "resultsLimit": max(1, min(int(maximum), 100))}

    def test_connection(self) -> None:
        start_time = time.perf_counter()
        endpoint = "https://api.apify.com/v2/users/me"
        request = Request(endpoint, headers={"Authorization": "Bearer " + self.token})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                duration_ms = (time.perf_counter() - start_time) * 1000
                log_api_call(provider="apify", method="GET", endpoint="/v2/users/me", status=response.status, duration_ms=duration_ms)
                if response.status != 200:
                    raise ProviderAuthenticationError("Apify authentication failed")
        except HTTPError as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_api_call(provider="apify", method="GET", endpoint="/v2/users/me", status=exc.code, duration_ms=duration_ms, error_message=f"HTTP {exc.code}", exc=exc)
            if exc.code in {401, 403}:
                raise ProviderAuthenticationError("Apify authentication failed") from exc
            raise ProviderError(f"Apify connection test failed with HTTP {exc.code}") from exc
        except (TimeoutError, URLError) as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_api_call(provider="apify", method="GET", endpoint="/v2/users/me", duration_ms=duration_ms, error_message="Network error", exc=exc)
            raise ProviderError("Apify connection test failed: network timeout or error") from exc

    def discover_pages(self, request: DiscoveryRequest) -> list[PageCandidate]:
        keywords = [item.strip() for item in request.keywords if item.strip()]
        if not keywords:
            return []
        maximum = max(1, min(request.maximum_pages, 100))
        payload = self.build_payload(keywords, maximum, request.country)
        endpoint = f"https://api.apify.com/v2/acts/{quote(PAGE_SEARCH_ACTOR_ID, safe='~')}/run-sync-get-dataset-items"
        req = Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token}, method="POST")
        start_time = time.perf_counter()
        try:
            with urlopen(req, timeout=self.timeout) as response:
                duration_ms = (time.perf_counter() - start_time) * 1000
                log_api_call(provider="apify", method="POST", endpoint="/v2/acts/page-search/run-sync-get-dataset-items", status=response.status, duration_ms=duration_ms)
                items = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_api_call(provider="apify", method="POST", endpoint="/v2/acts/page-search/run-sync-get-dataset-items", status=exc.code, duration_ms=duration_ms, error_message=f"HTTP {exc.code}", exc=exc)
            if exc.code in {401, 403}:
                raise ProviderAuthenticationError("Apify authentication failed") from exc
            raise ProviderError(f"Apify page search failed with HTTP {exc.code}") from exc
        except (TimeoutError, URLError, json.JSONDecodeError) as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_api_call(provider="apify", method="POST", endpoint="/v2/acts/page-search/run-sync-get-dataset-items", duration_ms=duration_ms, error_message="Network/JSON error", exc=exc)
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
    MAX_RESULTS_LIMIT = 100

    def __init__(
        self,
        token: str,
        timeout: float = 30.0,
        *,
        results_limit: int = 30,
        poll_interval: float = 3.0,
        max_wait_seconds: float = 600.0,
        store: Any = None,
        cancel_event: threading.Event | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
        resume_run_id: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.token = token.strip()
        self.timeout = timeout
        self.results_limit = max(1, min(int(results_limit or 30), self.MAX_RESULTS_LIMIT))
        self.poll_interval = poll_interval
        self.max_wait_seconds = max_wait_seconds
        self.store = store
        self.cancel_event = cancel_event
        self.progress_callback = progress_callback
        self.resume_run_id = (resume_run_id or "").strip() or None
        self.sleep = sleep
        self.last_run_metadata: dict[str, Any] = {}
        if self.token:
            redactor.add(self.token)
        if not self.token:
            raise ProviderAuthenticationError("Apify API token is required")

    @classmethod
    def build_reels_payload(cls, page_urls: Sequence[str] | str, results_limit: int = 30) -> dict[str, Any]:
        urls = [page_urls] if isinstance(page_urls, str) else list(page_urls)
        clean_urls = [normalize_facebook_url(u) for u in urls if str(u).strip()]
        limit = max(1, min(int(results_limit or 30), cls.MAX_RESULTS_LIMIT))
        return {
            "startUrls": [{"url": u} for u in clean_urls],
            "resultsLimit": limit,
        }

    def _request_json(self, url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
        headers = {"Authorization": "Bearer " + self.token}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        elif method == "POST":
            data = b""
        request = Request(url, data=data, headers=headers, method=method)
        start_time = time.perf_counter()
        try:
            with urlopen(request, timeout=self.timeout) as response:
                duration_ms = (time.perf_counter() - start_time) * 1000
                log_api_call(provider="apify", method=method, endpoint=url, status=response.status, duration_ms=duration_ms)
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_api_call(provider="apify", method=method, endpoint=url, status=exc.code, duration_ms=duration_ms, error_message=f"HTTP {exc.code}: {exc.reason}", exc=exc)
            if exc.code in {401, 403}:
                raise ProviderAuthenticationError("Apify authentication failed") from exc
            if exc.code == 429:
                raise ProviderError("Apify rate limit reached") from exc
            raise ProviderError(f"Apify HTTP {exc.code}: {exc.reason}") from exc
        except (TimeoutError, URLError, json.JSONDecodeError) as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_api_call(provider="apify", method=method, endpoint=url, duration_ms=duration_ms, error_message="Network/JSON error", exc=exc)
            raise ProviderError(f"Apify network timeout or error: {exc}") from exc

    def test_connection(self) -> None:
        """Validate token without starting a paid actor run."""
        endpoint = "https://api.apify.com/v2/users/me"
        request = Request(
            endpoint,
            headers={"Authorization": "Bearer " + self.token},
        )
        start_time = time.perf_counter()
        try:
            with urlopen(request, timeout=self.timeout) as response:
                duration_ms = (time.perf_counter() - start_time) * 1000
                log_api_call(provider="apify", method="GET", endpoint="/v2/users/me", status=response.status, duration_ms=duration_ms)
                if response.status != 200:
                    raise ProviderAuthenticationError("Apify authentication failed")
        except ProviderAuthenticationError:
            raise
        except HTTPError as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_api_call(provider="apify", method="GET", endpoint="/v2/users/me", status=exc.code, duration_ms=duration_ms, error_message=f"HTTP {exc.code}", exc=exc)
            if exc.code in {401, 403}:
                raise ProviderAuthenticationError("Apify authentication failed") from exc
            if exc.code == 429:
                raise ProviderError("Apify rate limit reached") from exc
            raise ProviderError(f"Apify connection test failed with HTTP {exc.code}") from exc
        except (TimeoutError, URLError) as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_api_call(provider="apify", method="GET", endpoint="/v2/users/me", duration_ms=duration_ms, error_message="Network error", exc=exc)
            raise ProviderError("Apify connection test failed: network timeout or error") from exc
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_api_call(provider="apify", method="GET", endpoint="/v2/users/me", duration_ms=duration_ms, error_message="General error", exc=exc)
            raise ProviderError("Apify connection test failed") from exc

    def get_run(self, run_id: str) -> dict[str, Any]:
        response = self._request_json(f"https://api.apify.com/v2/actor-runs/{quote(run_id.strip())}")
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, dict) or not data.get("id"):
            raise ProviderError(f"Apify run '{run_id}' not found")
        self.last_run_metadata = data
        self._persist_run(data)
        return data

    def list_recent_runs(self, limit: int = 5) -> list[dict[str, Any]]:
        url = f"https://api.apify.com/v2/acts/{quote(ACTOR_ID, safe='~')}/runs?desc=1&limit={max(1, min(int(limit), 20))}"
        response = self._request_json(url)
        items = ((response.get("data") or {}).get("items") or []) if isinstance(response, dict) else []
        return [item for item in items if isinstance(item, dict)]

    def start_run(self, page_urls: Sequence[str] | str, results_limit: int | None = None) -> dict[str, Any]:
        # Guard against duplicate active runs on Apify before starting a paid Actor
        for existing in self.list_recent_runs(limit=3):
            if existing.get("status") in ACTIVE_RUN_STATUSES:
                run_id = str(existing.get("id") or "")
                raise ProviderError(
                    f"Apify Actor is already running (Run ID: {run_id}). Wait for it or recover its dataset instead of starting a duplicate run."
                )
        payload = self.build_reels_payload(page_urls, results_limit or self.results_limit)
        url = f"https://api.apify.com/v2/acts/{quote(ACTOR_ID, safe='~')}/runs"
        response = self._request_json(url, method="POST", payload=payload)
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, dict) or not data.get("id"):
            raise ProviderError("Apify failed to start Actor run")
        self.last_run_metadata = data
        self._persist_run(data, page_urls=[u["url"] for u in payload["startUrls"]])
        return data

    def abort_run(self, run_id: str) -> dict[str, Any]:
        try:
            response = self._request_json(f"https://api.apify.com/v2/actor-runs/{quote(run_id.strip())}/abort", method="POST")
            data = response.get("data") if isinstance(response, dict) else {}
            if isinstance(data, dict) and data.get("id"):
                self.last_run_metadata = data
                self._persist_run(data)
            return data if isinstance(data, dict) else {}
        except ProviderError:
            return {}

    def fetch_dataset_items(self, dataset_id: str) -> list[dict[str, Any]]:
        if not dataset_id or not str(dataset_id).strip():
            return []
        url = f"https://api.apify.com/v2/datasets/{quote(str(dataset_id).strip())}/items?format=json&clean=true"
        items = self._request_json(url)
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    def poll_run_until_done(self, run_id: str) -> dict[str, Any]:
        started = time.monotonic()
        consecutive_Timeouts = 0
        last_data: dict[str, Any] = {"id": run_id}
        while True:
            if self.cancel_event is not None and self.cancel_event.is_set():
                # Only stop local polling; never auto-abort a remote Actor run without explicit user confirmation.
                return last_data
            try:
                data = self.get_run(run_id)
                last_data = data
                consecutive_Timeouts = 0
            except ProviderAuthenticationError:
                raise
            except ProviderError as exc:
                consecutive_Timeouts += 1
                if consecutive_Timeouts >= 4:
                    raise ProviderError(
                        f"Network error while polling Apify Run {run_id} (Actor may still be running on Apify): {exc}"
                    ) from exc
                self.sleep(self.poll_interval)
                continue

            status = str(data.get("status") or "UNKNOWN")
            status_msg = str(data.get("statusMessage") or status)
            if self.progress_callback is not None:
                self.progress_callback(0, 1, f"Apify Run {run_id} [{status}]: {status_msg[:70]}")
            if status in TERMINAL_OK_STATUSES:
                return data
            if status not in ACTIVE_RUN_STATUSES:
                raise ProviderError(f"Apify Run {run_id} ended with status {status}: {status_msg}")
            if (time.monotonic() - started) >= self.max_wait_seconds:
                raise ProviderError(
                    f"Apify Run {run_id} is still running after {int(self.max_wait_seconds)}s. "
                    f"You can recover its results later using Run ID {run_id}."
                )
            self.sleep(self.poll_interval)

    def _persist_run(self, data: Mapping[str, Any], page_urls: list[str] | None = None) -> None:
        if self.store is None or not hasattr(self.store, "provider_configuration"):
            return
        try:
            current = dict(self.store.provider_configuration("apify_reels_last_run") or {})
            run_id = str(data.get("id") or "")
            same_run = bool(run_id and run_id == current.get("run_id"))
            entry = {
                "task": "scan_by_page",
                "actor_id": ACTOR_ID,
                "run_id": run_id or str(current.get("run_id") or ""),
                "dataset_id": str(data.get("defaultDatasetId") or (current.get("dataset_id") if same_run else "") or ""),
                "status": str(data.get("status") or (current.get("status") if same_run else "") or ""),
                "status_message": str(data.get("statusMessage") or (current.get("status_message") if same_run else "") or ""),
                "started_at": str(data.get("startedAt") or (current.get("started_at") if same_run else "") or ""),
                "finished_at": str(data.get("finishedAt") or (current.get("finished_at") if same_run else "") or ""),
                "page_urls": page_urls if page_urls is not None else (current.get("page_urls", []) if same_run else []),
            }
            history = dict(current.get("runs_by_id") or {})
            if entry["run_id"]:
                history[entry["run_id"]] = {k: v for k, v in entry.items() if k != "runs_by_id"}
            entry["runs_by_id"] = history
            self.store.set_provider_configuration("apify_reels_last_run", entry, True)
        except Exception:
            pass

    def _run(self, page_url: str) -> list[dict]:
        if self.resume_run_id:
            run_data = self.poll_run_until_done(self.resume_run_id)
        else:
            started = self.start_run(page_url, self.results_limit)
            run_data = self.poll_run_until_done(str(started["id"]))
        dataset_id = str(run_data.get("defaultDatasetId") or "")
        return self.fetch_dataset_items(dataset_id)

    @classmethod
    def parse_reel_items(cls, items: Sequence[Mapping[str, Any]], fallback_page: PageCandidate, pages_by_url: Mapping[str, PageCandidate] | None = None) -> list[ReelMetadata]:
        lookup = pages_by_url or {}
        seen_ids: set[str] = set()
        results: list[ReelMetadata] = []
        for item in items:
            url = item.get("shareable_url") or item.get("topLevelReelUrl") or item.get("reelUrl") or item.get("url")
            if not url:
                continue
            try:
                normalized = normalize_facebook_url(str(url))
                reel_id = str(item.get("video_id") or item.get("post_id") or item.get("id") or stable_reel_id(normalized))
            except ValueError:
                continue
            if reel_id in seen_ids:
                continue
            seen_ids.add(reel_id)

            raw_page_url = item.get("inputUrl") or item.get("facebookUrl") or fallback_page.page_url
            try:
                norm_page_url = normalize_facebook_url(str(raw_page_url))
            except ValueError:
                norm_page_url = fallback_page.page_url
            target_page = lookup.get(norm_page_url, fallback_page)

            timestamp = item.get("time") or item.get("creation_time")
            published = None
            if isinstance(timestamp, str) and timestamp.strip():
                try:
                    published = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                except ValueError:
                    published = None
            views_raw = item.get("playCountRounded")
            if views_raw is None:
                views_raw = item.get("videoViewCount")
            if views_raw is None:
                views_raw = item.get("playCount")
            views = normalize_view_count(views_raw)
            results.append(
                ReelMetadata(
                    reel_id=reel_id,
                    reel_url=normalized,
                    page_id=target_page.page_id,
                    page_name=target_page.page_name,
                    page_url=target_page.page_url,
                    caption=str(item.get("text") or ""),
                    views=views,
                    likes=int(item["likeCount"]) if isinstance(item.get("likeCount"), (int, float)) else None,
                    comments=int(item["commentCount"]) if isinstance(item.get("commentCount"), (int, float)) else None,
                    shares=int(item["shareCount"]) if isinstance(item.get("shareCount"), (int, float)) else None,
                    published_at=published,
                    country=target_page.country,
                    language=target_page.language,
                    topic=target_page.topic,
                    data_source=cls.name,
                )
            )
        return results

    def collect_reels(self, page: PageCandidate):
        items = self._run(page.page_url)
        yield from self.parse_reel_items(items, page)
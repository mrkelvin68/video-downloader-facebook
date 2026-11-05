from __future__ import annotations

import math
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

COUNTRIES = ("All", "Vietnam", "China", "United States", "Thailand", "South Korea", "Japan", "Other", "Unknown")


class ProviderError(RuntimeError):
    pass


class ProviderAuthenticationError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class PageCandidate:
    page_id: str
    page_name: str
    page_url: str
    country: str = "Unknown"
    language: str = "Unknown"
    topic: str = ""
    source: str = "manual"


@dataclass(frozen=True)
class ReelMetadata:
    reel_id: str
    reel_url: str
    page_id: str
    page_name: str
    page_url: str
    caption: str = ""
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    published_at: datetime | None = None
    country: str | None = None
    language: str | None = None
    topic: str | None = None
    data_source: str = ""
    status: str = "collected"


@dataclass(frozen=True)
class DiscoveryRequest:
    country: str = "All"
    language: str = "All"
    topic: str = ""
    keywords: tuple[str, ...] = ()
    maximum_pages: int = 50


@dataclass(frozen=True)
class ReelFilters:
    minimum_views: int | None = None
    country: str = "All"
    language: str = "All"
    topic: str = ""
    published_from: datetime | None = None
    published_to: datetime | None = None
    minimum_engagement: int | None = None
    maximum_results: int = 100


@dataclass
class CollectionProgress:
    pages_total: int = 0
    pages_scanned: int = 0
    reels_collected: int = 0
    qualified_reels: int = 0
    duplicates_skipped: int = 0
    errors: int = 0
    messages: list[str] = field(default_factory=list)


class PageDiscoveryProvider(ABC):
    name: str
    requires_credentials = False
    paid = False
    capabilities = frozenset({"find_pages"})

    @abstractmethod
    def discover_pages(self, request: DiscoveryRequest) -> Iterable[PageCandidate]:
        raise NotImplementedError


class ReelsProvider(ABC):
    name: str
    requires_credentials = False
    paid = False
    minimum_interval_seconds = 0.0
    capabilities = frozenset({"scan_by_page"})

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    @abstractmethod
    def collect_reels(self, page: PageCandidate) -> Iterable[ReelMetadata]:
        raise NotImplementedError


def normalize_facebook_url(url: str) -> str:
    value = url.strip()
    if not value:
        raise ValueError("Facebook URL is required")
    if value.startswith(("/", "profile.php")):
        value = f"https://www.facebook.com/{value.lstrip('/')}"
    elif "://" not in value:
        value = f"https://{value}"
    parts = urlsplit(value)
    host = parts.netloc.lower().removeprefix("www.").removeprefix("m.")
    if host not in {"facebook.com", "fb.com", "fb.watch"}:
        raise ValueError("Only Facebook URLs are supported")
    path = re.sub(r"/+", "/", parts.path).rstrip("/") or "/"

    query = ""
    if path == "/profile.php":
        qs = parse_qs(parts.query)
        id_values = qs.get("id")
        if not id_values or not id_values[0] or not id_values[0].isdigit():
            raise ValueError("Facebook profile.php URL requires a valid numeric 'id' parameter")
        query = f"id={id_values[0]}"

    return urlunsplit(("https", "www.facebook.com" if host in {"facebook.com", "fb.com"} else host, path, query, ""))


def extract_page_identity(url: str) -> tuple[str, str, str]:
    normalized = normalize_facebook_url(url)
    parts = urlsplit(normalized)
    path = parts.path
    if path == "/profile.php":
        qs = parse_qs(parts.query)
        page_id = qs.get("id", [""])[0]
        if not page_id or not page_id.isdigit():
            raise ValueError("Facebook profile.php URL requires a valid numeric 'id' parameter")
        return page_id, f"Page {page_id}", normalized

    segments = [unquote(part) for part in path.split("/") if part]
    if not segments:
        raise ValueError("Facebook URL has no Page name")
    numeric = next((part for part in reversed(segments) if part.isdigit()), "")
    name = next((part for part in reversed(segments) if not part.isdigit()), segments[-1])
    page_id = numeric or f"manual:{normalized}"
    return page_id, name, normalized


def stable_reel_id(url: str) -> str:
    normalized = normalize_facebook_url(url)
    match = re.search(r"/(?:reel|reels|videos)/(\d+)(?:/|$)", urlsplit(normalized).path)
    if not match:
        raise ValueError("Reel URL has no stable numeric ID")
    return match.group(1)


def classify_language(text: str) -> tuple[str, float]:
    sample = text.strip()
    if not sample:
        return "Unknown", 0.0
    if re.search(r"[가-힣]", sample):
        return "Korean", 0.95
    if re.search(r"[ぁ-んァ-ン]", sample):
        return "Japanese", 0.95
    if re.search(r"[一-鿿]", sample):
        return "Chinese", 0.75
    lowered = sample.lower()
    vietnamese = sum(lowered.count(char) for char in "ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")
    if vietnamese:
        return "Vietnamese", min(0.99, 0.75 + vietnamese / max(20, len(sample)))
    thai = len(re.findall(r"[ก-๛]", sample))
    if thai:
        return "Thai", min(0.99, 0.7 + thai / max(20, len(sample)))
    latin = len(re.findall(r"[A-Za-z]", sample))
    return ("English", min(0.9, 0.55 + latin / max(100, len(sample)))) if latin else ("Unknown", 0.0)


def viral_score(reel: Mapping[str, object], snapshots: Sequence[Mapping[str, object]] = (), now: datetime | None = None) -> float:
    current = now or datetime.now(UTC)
    views = reel.get("views")
    likes = reel.get("likes")
    comments = reel.get("comments")
    shares = reel.get("shares")
    view_component = math.log10(max(0, int(views)) + 1) * 16 if views is not None else 0.0
    engagement = sum(int(value) for value in (likes, comments, shares) if value is not None)
    engagement_component = math.log10(engagement + 1) * 8
    published = reel.get("published_at")
    if isinstance(published, str):
        published = datetime.fromisoformat(published.replace("Z", "+00:00"))
    freshness = 0.0
    if isinstance(published, datetime):
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        freshness = max(0.0, 20.0 * (1.0 - max(0.0, (current - published).total_seconds()) / (30 * 86400)))
    growth = 0.0
    usable = [item for item in snapshots if item.get("views") is not None and item.get("measured_at")]
    if len(usable) >= 2:
        first, last = usable[0], usable[-1]
        start = datetime.fromisoformat(str(first["measured_at"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(last["measured_at"]).replace("Z", "+00:00"))
        hours = (end - start).total_seconds() / 3600
        if hours > 0:
            growth = min(15.0, math.log10(max(0.0, (int(last["views"]) - int(first["views"])) / hours) + 1) * 5)
    return round(view_component + engagement_component + freshness + growth, 4)


def normalize_view_count(val: Any) -> int | None:
    if val is None or val == "" or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        if math.isnan(val) or math.isinf(val) or val < 0:
            return None
        return int(val)
    if isinstance(val, str):
        cleaned = val.strip().replace(",", "")
        if not cleaned:
            return None
        try:
            float_val = float(cleaned)
            if math.isnan(float_val) or math.isinf(float_val) or float_val < 0:
                return None
            return int(float_val)
        except ValueError:
            pass
        upper = cleaned.upper()
        if upper.endswith("K"):
            try:
                num = float(upper[:-1])
                return int(num * 1_000) if num >= 0 else None
            except ValueError:
                return None
        if upper.endswith("M"):
            try:
                num = float(upper[:-1])
                return int(num * 1_000_000) if num >= 0 else None
            except ValueError:
                return None
        if upper.endswith("B"):
            try:
                num = float(upper[:-1])
                return int(num * 1_000_000_000) if num >= 0 else None
            except ValueError:
                return None
    return None


def reel_matches(reel: Mapping[str, object], filters: ReelFilters) -> bool:
    views = normalize_view_count(reel.get("views"))
    if filters.minimum_views is not None and filters.minimum_views > 0:
        if views is None or views < filters.minimum_views:
            return False
    if filters.country != "All" and reel.get("country") != filters.country:
        return False
    if filters.language != "All" and reel.get("language") != filters.language:
        return False
    if filters.topic and reel.get("topic") != filters.topic:
        return False
    engagement = sum(int(reel.get(key) or 0) for key in ("likes", "comments", "shares"))
    if filters.minimum_engagement is not None and engagement < filters.minimum_engagement:
        return False
    published = reel.get("published_at")
    if isinstance(published, str):
        published = datetime.fromisoformat(published.replace("Z", "+00:00"))
    if filters.published_from and (not isinstance(published, datetime) or published < filters.published_from):
        return False
    if filters.published_to and (not isinstance(published, datetime) or published > filters.published_to):
        return False
    return True


class PageDiscoveryService:
    def __init__(self, store: object, providers: Sequence[PageDiscoveryProvider]) -> None:
        self.store = store
        self.providers = providers

    def discover(self, request: DiscoveryRequest, seeds: Iterable[PageCandidate] = ()) -> list[PageCandidate]:
        if request.maximum_pages < 1:
            return []
        found: dict[str, PageCandidate] = {}
        for page in seeds:
            normalized = normalize_facebook_url(page.page_url)
            found[normalized] = PageCandidate(**{**page.__dict__, "page_url": normalized, "source": page.source or "manual"})
        for provider in self.providers:
            for page in provider.discover_pages(request):
                normalized = normalize_facebook_url(page.page_url)
                found.setdefault(normalized, PageCandidate(**{**page.__dict__, "page_url": normalized, "source": provider.name}))
                if len(found) >= request.maximum_pages:
                    break
            if len(found) >= request.maximum_pages:
                break
        result = list(found.values())[: request.maximum_pages]
        for page in result:
            self.store.upsert_page(page)
        return result


class ReelsCollectionService:
    def __init__(self, store: object, provider: ReelsProvider, sleep: Callable[[float], None] = time.sleep) -> None:
        self.store = store
        self.provider = provider
        self.sleep = sleep

    def collect(self, pages: Sequence[PageCandidate], filters: ReelFilters, cancel: threading.Event | None = None, progress_callback: Callable[[CollectionProgress], None] | None = None) -> tuple[list[dict[str, object]], CollectionProgress]:
        state = CollectionProgress(pages_total=len(pages))
        results: list[dict[str, object]] = []
        last_request = 0.0
        for page in pages:
            if cancel and cancel.is_set():
                break
            wait = self.provider.minimum_interval_seconds - (time.monotonic() - last_request)
            if wait > 0:
                self.sleep(wait)
            try:
                last_request = time.monotonic()
                for raw in self.provider.collect_reels(page):
                    if cancel and cancel.is_set():
                        break
                    reel_url = normalize_facebook_url(raw.reel_url)
                    reel_id = raw.reel_id or stable_reel_id(reel_url)
                    language, confidence = classify_language(raw.caption)
                    norm_views = normalize_view_count(raw.views)
                    record = {
                        **raw.__dict__,
                        "reel_id": reel_id,
                        "reel_url": reel_url,
                        "views": norm_views,
                        "published_at": raw.published_at.isoformat() if raw.published_at else None,
                        "country": raw.country or page.country or "Unknown",
                        "language": raw.language or language,
                        "language_confidence": confidence,
                    }
                    is_match = reel_matches(record, filters)
                    record["status"] = "qualified" if is_match else "unqualified"
                    inserted = self.store.upsert_reel(record)
                    self.store.record_metrics_snapshot(reel_id, norm_views, raw.likes, raw.comments, raw.shares)
                    if inserted:
                        state.reels_collected += 1
                    else:
                        state.duplicates_skipped += 1
                    record["viral_score"] = viral_score(record, self.store.metrics_history(reel_id))
                    self.store.update_viral_score(reel_id, float(record["viral_score"]))
                    if is_match:
                        results.append(record)
                        state.qualified_reels += 1
                        if len(results) >= filters.maximum_results:
                            break
                if len(results) >= filters.maximum_results:
                    break
            except ProviderRateLimitError as exc:
                state.errors += 1
                state.messages.append(str(exc))
                if exc.retry_after:
                    self.sleep(exc.retry_after)
            except (ProviderError, ValueError) as exc:
                state.errors += 1
                state.messages.append(f"{page.page_name}: {exc}")
            state.pages_scanned += 1
            if progress_callback:
                progress_callback(state)
        results.sort(key=lambda item: float(item["viral_score"]), reverse=True)
        return results[: filters.maximum_results], state

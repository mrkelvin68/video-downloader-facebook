import tempfile
import threading
import unittest
from pathlib import Path
from datetime import UTC, datetime, timedelta

from app.database.connection import Database
from app.database.viral_reels_store import ViralReelsStore
from app.services.apify_facebook_provider import ApifyFacebookPageSearchProvider
from app.services.viral_reels_service import (
    DiscoveryRequest,
    PageCandidate,
    PageDiscoveryProvider,
    PageDiscoveryService,
    ProviderError,
    ProviderRateLimitError,
    ReelFilters,
    ReelMetadata,
    ReelsCollectionService,
    ReelsProvider,
    classify_language,
    normalize_facebook_url,
    viral_score,
)


class DiscoveryProvider(PageDiscoveryProvider):
    name = "authorized-test"

    def __init__(self, pages=()):
        self.pages = pages

    def discover_pages(self, request):
        if request.country == "Atlantis":
            raise ProviderError("Country filter unsupported")
        return self.pages


class CollectionProvider(ReelsProvider):
    name = "authorized-test"
    minimum_interval_seconds = 0.01

    def __init__(self, reels=(), error=None):
        self.reels = reels
        self.error = error

    def collect_reels(self, page):
        if self.error:
            raise self.error
        return self.reels


class ViralReelsTests(unittest.TestCase):
    def setUp(self):
        self.database = Database(":memory:")
        self.store = ViralReelsStore(self.database)
        self.page = PageCandidate("p1", "Page", "https://facebook.com/page", "Vietnam", "Vietnamese", "Food")

    def tearDown(self):
        self.database.close()

    def test_apify_page_search_payload_uses_actor_schema(self):
        payload = ApifyFacebookPageSearchProvider.build_payload((" phim ngắn ",), 5, "Vietnam")
        self.assertEqual(payload, {
            "searchType": "pages",
            "categories": ["phim ngắn"],
            "resultsLimit": 5,
        })
        self.assertNotIn("searchQueries", payload)
        self.assertNotIn("maxItems", payload)

    def test_discovery_deduplicates_and_handles_empty(self):
        provider = DiscoveryProvider([self.page, self.page])
        result = PageDiscoveryService(self.store, [provider]).discover(DiscoveryRequest(maximum_pages=5))
        self.assertEqual(1, len(result))
        self.assertEqual([], PageDiscoveryService(self.store, [DiscoveryProvider()]).discover(DiscoveryRequest(maximum_pages=5)))

    def test_unsupported_country_reports_limitation(self):
        with self.assertRaisesRegex(ProviderError, "unsupported"):
            PageDiscoveryService(self.store, [DiscoveryProvider()]).discover(DiscoveryRequest(country="Atlantis"))

    def test_collection_missing_views_deduplicates_and_records_history(self):
        raw = ReelMetadata("10", "https://facebook.com/reel/10?x=1", "p1", "Page", self.page.page_url, "Xin chào", None, 4, 2, 1, datetime.now(UTC), data_source="test")
        self.store.upsert_page(self.page)
        service = ReelsCollectionService(self.store, CollectionProvider([raw]), sleep=lambda _: None)
        first, state1 = service.collect([self.page], ReelFilters())
        second, state2 = service.collect([self.page], ReelFilters())
        self.assertEqual("https://www.facebook.com/reel/10", first[0]["reel_url"])
        self.assertEqual(1, state1.reels_collected)
        self.assertEqual(1, state2.duplicates_skipped)
        self.assertEqual(2, len(self.store.metrics_history("10")))
        self.assertEqual(first[0]["reel_id"], second[0]["reel_id"])

    def test_provider_failure_and_rate_limit_are_reported(self):
        failed = ReelsCollectionService(self.store, CollectionProvider(error=ProviderError("down")))
        _, state = failed.collect([self.page], ReelFilters())
        self.assertEqual(1, state.errors)
        waits = []
        limited = ReelsCollectionService(self.store, CollectionProvider(error=ProviderRateLimitError("slow", 2)), sleep=waits.append)
        _, state = limited.collect([self.page], ReelFilters())
        self.assertEqual([2], waits)
        self.assertEqual(1, state.errors)

    def test_score_filters_unknown_and_growth_requirements(self):
        now = datetime(2025, 1, 2, tzinfo=UTC)
        reel = {"views": 1000, "likes": 10, "comments": 2, "shares": 1, "published_at": now - timedelta(days=1)}
        score = viral_score(reel, now=now)
        self.assertEqual(score, viral_score(reel, now=now))
        one = [{"views": 1000, "measured_at": now.isoformat()}]
        two = one + [{"views": 2000, "measured_at": (now + timedelta(hours=1)).isoformat()}]
        self.assertEqual(score, viral_score(reel, one, now))
        self.assertGreater(viral_score(reel, two, now), score)
        raw = ReelMetadata("11", "https://facebook.com/reel/11", "p1", "Page", self.page.page_url, views=None)
        self.store.upsert_page(self.page)
        result, _ = ReelsCollectionService(self.store, CollectionProvider([raw])).collect([self.page], ReelFilters(minimum_views=1))
        self.assertEqual([], result)

    def test_language_country_and_cancellation(self):
        self.assertEqual("Vietnamese", classify_language("Xin chào Việt Nam")[0])
        self.assertEqual("Unknown", classify_language("")[0])
        cancel = threading.Event()
        cancel.set()
        result, state = ReelsCollectionService(self.store, CollectionProvider()).collect([self.page], ReelFilters(), cancel)
        self.assertEqual([], result)
        self.assertEqual(0, state.pages_scanned)

    def test_schedule_persists_and_rejects_unknown_values(self):
        self.store.set_schedule("6h")
        self.assertEqual("6h", self.store.schedule())
        self.assertFalse(self.store.job_running())
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.store.set_schedule("weekly")

    def test_worker_opens_and_closes_its_own_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "worker.sqlite3")
            store = ViralReelsStore(database)
            store.upsert_page(self.page)
            errors = []

            def work():
                worker_store = ViralReelsStore.for_thread(database.path)
                try:
                    self.assertEqual(1, len(worker_store.list_pages()))
                except Exception as error:
                    errors.append(error)
                finally:
                    worker_store.close()

            thread = threading.Thread(target=work)
            thread.start()
            thread.join()
            self.assertEqual([], errors)
            database.close()

    def test_normalization_rejects_non_facebook(self):
        self.assertEqual("https://www.facebook.com/reel/123", normalize_facebook_url("https://m.facebook.com/reel/123/?foo=bar"))
        with self.assertRaises(ValueError):
            normalize_facebook_url("https://example.com/reel/123")


if __name__ == "__main__":
    unittest.main()

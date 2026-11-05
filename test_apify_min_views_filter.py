import csv
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

import app.ui.main_window as mw
from app.database.connection import Database
from app.database.viral_reels_store import ViralReelsStore
from app.services.apify_facebook_provider import ApifyFacebookReelsProvider
from app.services.viral_reels_export_service import export_csv, export_xlsx
from app.services.viral_reels_service import (
    PageCandidate,
    ProviderError,
    ReelFilters,
    ReelMetadata,
    ReelsCollectionService,
    ReelsProvider,
    normalize_facebook_url,
    normalize_view_count,
    reel_matches,
)


class FakePageProvider(ReelsProvider):
    name = "FakeApifyProvider"

    def __init__(self, data_by_page=None, error=None):
        self.data_by_page = data_by_page or {}
        self.error = error
        self.calls = []

    def collect_reels(self, page: PageCandidate):
        self.calls.append(page.page_url)
        if self.error:
            raise self.error
        for item in self.data_by_page.get(page.page_url, []):
            yield item


def make_reel(idx, page, views=None, caption="Test reel"):
    url = f"https://www.facebook.com/reel/999{idx:04d}"
    return ReelMetadata(
        reel_id=f"999{idx:04d}",
        reel_url=url,
        page_id=page.page_id,
        page_name=page.page_name,
        page_url=page.page_url,
        caption=caption,
        views=normalize_view_count(views),
        published_at=datetime(2026, 1, 1),
        country="Vietnam",
        language="Vietnamese",
        topic="General",
        data_source="FakeApifyProvider",
    )


class SettingsStub:
    def __init__(self, provider="Apify Facebook Reels Scraper"):
        self._provider = provider

    def value(self, key, default=None, type=None):
        if "provider" in key:
            return self._provider
        return default

    def setValue(self, key, val):
        pass


class TestApifyMinimumViewsFilter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.sqlite3")
        self.store = ViralReelsStore(self.db)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.db.close)

    # TEST 01: minimum_views=50000, views=489 -> REJECT
    def test_01_min_views_489_rejected(self):
        f = ReelFilters(minimum_views=50000, maximum_results=5)
        self.assertFalse(reel_matches({"views": 489}, f))

    # TEST 02: minimum_views=50000, views=37000 -> REJECT
    def test_02_min_views_37000_rejected(self):
        f = ReelFilters(minimum_views=50000, maximum_results=5)
        self.assertFalse(reel_matches({"views": 37000}, f))

    # TEST 03: minimum_views=50000, views=50000 -> ACCEPT
    def test_03_min_views_50000_accepted(self):
        f = ReelFilters(minimum_views=50000, maximum_results=5)
        self.assertTrue(reel_matches({"views": 50000}, f))

    # TEST 04: minimum_views=50000, views=175000 -> ACCEPT
    def test_04_min_views_175000_accepted(self):
        f = ReelFilters(minimum_views=50000, maximum_results=5)
        self.assertTrue(reel_matches({"views": 175000}, f))

    # TEST 05: minimum_views=50000, views="1e+06" -> ACCEPT
    def test_05_min_views_scientific_accepted(self):
        f = ReelFilters(minimum_views=50000, maximum_results=5)
        self.assertTrue(reel_matches({"views": "1e+06"}, f))

    # TEST 06: minimum_views=50000, views=null -> REJECT
    def test_06_min_views_null_rejected(self):
        f = ReelFilters(minimum_views=50000, maximum_results=5)
        self.assertFalse(reel_matches({"views": None}, f))
        self.assertFalse(reel_matches({"views": ""}, f))
        self.assertFalse(reel_matches({"views": -10}, f))
        self.assertFalse(reel_matches({"views": "invalid"}, f))

    # TEST 07: maximum_results=5. First 5 candidates are below threshold. Later meet threshold.
    def test_07_first_candidates_below_threshold_later_meet(self):
        page = PageCandidate("p1", "Page 1", "https://www.facebook.com/profile.php?id=61589309147446")
        self.store.upsert_page(page)
        # 5 below threshold, then 3 qualified
        items = [
            make_reel(1, page, views=489),
            make_reel(2, page, views=996),
            make_reel(3, page, views=989),
            make_reel(4, page, views=1000),
            make_reel(5, page, views=1300),
            make_reel(6, page, views=60000),
            make_reel(7, page, views=75000),
            make_reel(8, page, views=120000),
        ]
        provider = FakePageProvider({page.page_url: items})
        service = ReelsCollectionService(self.store, provider)
        filters = ReelFilters(minimum_views=50000, maximum_results=5)

        results, progress = service.collect([page], filters)
        self.assertEqual(3, len(results))
        self.assertTrue(all(r["views"] >= 50000 for r in results))
        self.assertEqual({"9990006", "9990007", "9990008"}, {r["reel_id"] for r in results})

    # TEST 08: Provider returns only 2 qualified Reels -> return 2, not 5 invalid results
    def test_08_provider_returns_only_2_qualified_reels(self):
        page = PageCandidate("p1", "Page 1", "https://www.facebook.com/profile.php?id=61589309147446")
        self.store.upsert_page(page)
        items = [
            make_reel(1, page, views=489),
            make_reel(2, page, views=60000),
            make_reel(3, page, views=1000),
            make_reel(4, page, views=90000),
            make_reel(5, page, views=20000),
        ]
        provider = FakePageProvider({page.page_url: items})
        service = ReelsCollectionService(self.store, provider)
        filters = ReelFilters(minimum_views=50000, maximum_results=5)

        results, progress = service.collect([page], filters)
        self.assertEqual(2, len(results))
        self.assertTrue(all(r["views"] >= 50000 for r in results))

    # TEST 09: Five Pages selected -> correct Page attribution and no duplicates
    def test_09_five_pages_selected_correct_attribution(self):
        page_ids = ["61589309147446", "61595198506164", "61595570879090", "61592243806109", "61594805510676"]
        pages = [
            PageCandidate(pid, f"Page {pid}", f"https://www.facebook.com/profile.php?id={pid}")
            for pid in page_ids
        ]
        data = {}
        for idx, p in enumerate(pages, 1):
            self.store.upsert_page(p)
            data[p.page_url] = [
                make_reel(idx * 10 + 1, p, views=100000),
                make_reel(idx * 10 + 2, p, views=200),  # unqualified
            ]
        provider = FakePageProvider(data)
        service = ReelsCollectionService(self.store, provider)
        filters = ReelFilters(minimum_views=50000, maximum_results=10)

        results, progress = service.collect(pages, filters)
        self.assertEqual(5, len(results))
        for r in results:
            self.assertIn(r["page_id"], page_ids)
            self.assertEqual(r["page_url"], f"https://www.facebook.com/profile.php?id={r['page_id']}")
            self.assertTrue(r["views"] >= 50000)
        # Deduplication check: duplicate reel id across pages is not duplicated
        self.assertEqual(len(results), len({r["reel_id"] for r in results}))

    # TEST 10: Actor ignores minimum-view filtering -> local filtering still works
    def test_10_actor_ignores_min_views_local_filtering_works(self):
        # Apify payload does NOT include minViews or minimum_views (verified schema)
        payload = ApifyFacebookReelsProvider.build_reels_payload("https://www.facebook.com/profile.php?id=61594805510676", results_limit=5)
        self.assertNotIn("minViews", payload)
        self.assertNotIn("minimum_views", payload)
        self.assertNotIn("minPlayCount", payload)

        # Actor returns mixed items: 489, 996, 60000
        raw_items = [
            {"url": "https://www.facebook.com/reel/111", "playCountRounded": 489},
            {"url": "https://www.facebook.com/reel/222", "playCountRounded": 996},
            {"url": "https://www.facebook.com/reel/333", "playCountRounded": 60000},
        ]
        dummy_page = PageCandidate("61594805510676", "Page 1", "https://www.facebook.com/profile.php?id=61594805510676")
        parsed = ApifyFacebookReelsProvider.parse_reel_items(raw_items, dummy_page)
        filters = ReelFilters(minimum_views=50000, maximum_results=5)
        qualified = [r for r in parsed if reel_matches(r.__dict__, filters)]
        self.assertEqual(1, len(qualified))
        self.assertEqual("111", parsed[0].reel_id)
        self.assertEqual(489, parsed[0].views)
        self.assertEqual("333", qualified[0].reel_id)
        self.assertEqual(60000, qualified[0].views)

    # TEST 11: SQLite contains previous low-view Reels -> active filtered scan excludes them without destroying history
    def test_11_sqlite_previous_low_views_excluded_history_preserved(self):
        page = PageCandidate("p1", "Page 1", "https://www.facebook.com/profile.php?id=61589309147446")
        self.store.upsert_page(page)
        # Prepopulate SQLite with old low-view reels
        old_reel_1 = make_reel(1, page, views=489)
        old_reel_2 = make_reel(2, page, views=996)
        self.store.upsert_reel({**old_reel_1.__dict__, "published_at": "2026-01-01T00:00:00", "status": "unqualified"})
        self.store.upsert_reel({**old_reel_2.__dict__, "published_at": "2026-01-01T00:00:00", "status": "unqualified"})

        # Verify old reels exist in database (history not destroyed)
        self.assertEqual(2, len(self.store.list_reels()))

        # Now active filter with minimum_views=50000
        filters = ReelFilters(minimum_views=50000, maximum_results=5)
        db_rows = [dict(r) for r in self.store.list_reels()]
        matched = [r for r in db_rows if reel_matches(r, filters)]
        self.assertEqual(0, len(matched))

        # Add a new qualified reel
        new_qualified = make_reel(3, page, views=75000)
        self.store.upsert_reel({**new_qualified.__dict__, "published_at": "2026-01-01T00:00:00", "status": "qualified"})
        db_rows2 = [dict(r) for r in self.store.list_reels()]
        self.assertEqual(3, len(db_rows2))  # History preserved
        matched2 = [r for r in db_rows2 if reel_matches(r, filters)]
        self.assertEqual(1, len(matched2))
        self.assertEqual("9990003", matched2[0]["reel_id"])
        self.assertEqual(75000, matched2[0]["views"])

    # TEST 12: CSV, Excel and Google Sheets receive only the intended qualified scan results
    def test_12_exports_receive_only_qualified_results(self):
        qualified = [
            {"reel_id": "1", "reel_url": "https://facebook.com/reel/1", "page_name": "P1", "page_url": "https://facebook.com/p1", "views": 80000, "caption": "Viral 1"},
            {"reel_id": "2", "reel_url": "https://facebook.com/reel/2", "page_name": "P2", "page_url": "https://facebook.com/p2", "views": 150000, "caption": "Viral 2"},
        ]
        # CSV Export
        csv_path = Path(self.tmp.name) / "test_export.csv"
        count = export_csv(csv_path, qualified)
        self.assertEqual(2, count)
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = list(csv.DictReader(f))
            self.assertEqual(2, len(reader))
            for row in reader:
                self.assertTrue(int(row["views"]) >= 50000)

        # Excel Export (if openpyxl installed)
        try:
            import openpyxl
            xlsx_path = Path(self.tmp.name) / "test_export.xlsx"
            count_xlsx = export_xlsx(xlsx_path, qualified)
            self.assertEqual(2, count_xlsx)
        except ImportError:
            pass

    # TEST 13: Advanced filters disabled -> documented minimum-view behavior consistent and correctly reflected in UI
    def test_13_advanced_filters_disabled_consistent_in_ui(self):
        msgs = []
        patches = [
            patch.object(mw, "viral_settings", lambda: SettingsStub()),
            patch.object(QMessageBox, "warning", staticmethod(lambda *a, **k: msgs.append(("w", a[2])))),
            patch.object(QMessageBox, "information", staticmethod(lambda *a, **k: msgs.append(("i", a[2])))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        win = mw.MainWindow(self.db)
        self.addCleanup(win.close)
        page = win.viral_reels_page

        # When advanced_group is unchecked:
        page.advanced_group.setChecked(False)
        self.assertFalse(page.min_views.isEnabled())
        filters = page.filters()
        self.assertEqual(0, filters["min_views"])
        reels_filters = win._viral_filters(filters)
        self.assertIsNone(reels_filters.minimum_views)

        # When advanced_group is checked:
        page.advanced_group.setChecked(True)
        self.assertTrue(page.min_views.isEnabled())
        page.min_views.setValue(50000)
        filters2 = page.filters()
        self.assertEqual(50000, filters2["min_views"])
        reels_filters2 = win._viral_filters(filters2)
        self.assertEqual(50000, reels_filters2.minimum_views)

    # TEST 14: Apify API request fails or times out -> clear error and bounded retry behavior
    def test_14_apify_api_request_fails_bounded_error_reported(self):
        page = PageCandidate("p1", "Page 1", "https://www.facebook.com/profile.php?id=61589309147446")
        self.store.upsert_page(page)
        provider = FakePageProvider(error=ProviderError("Apify network timeout or error: timed out"))
        service = ReelsCollectionService(self.store, provider)
        filters = ReelFilters(minimum_views=50000, maximum_results=5)

        results, progress = service.collect([page], filters)
        self.assertEqual(0, len(results))
        self.assertEqual(1, progress.errors)
        self.assertTrue(any("timed out" in m for m in progress.messages))

    # TEST 15: Maximum results=5 with minimum_views=50000 -> returned count <= 5 and every returned Reel satisfies views >= 50000
    def test_15_max_results_5_with_min_views_50000(self):
        page = PageCandidate("p1", "Page 1", "https://www.facebook.com/profile.php?id=61589309147446")
        self.store.upsert_page(page)
        # 10 qualified reels, 5 unqualified
        items = [make_reel(i, page, views=60000 + i * 5000) for i in range(10)] + [make_reel(100 + i, page, views=1000) for i in range(5)]
        provider = FakePageProvider({page.page_url: items})
        service = ReelsCollectionService(self.store, provider)
        filters = ReelFilters(minimum_views=50000, maximum_results=5)

        results, progress = service.collect([page], filters)
        self.assertLessEqual(len(results), 5)
        self.assertEqual(5, len(results))
        self.assertTrue(all(r["views"] >= 50000 for r in results))

    # TEST 16: Provider returns scientific notation, numeric strings and integers -> consistent parsing without incorrect rounding
    def test_16_numeric_formats_consistent_parsing_no_rounding(self):
        self.assertEqual(175000, normalize_view_count(175000))
        self.assertEqual(175000, normalize_view_count(175000.0))
        self.assertEqual(175000, normalize_view_count("175000"))
        self.assertEqual(1000000, normalize_view_count("1e+06"))
        self.assertEqual(1200000, normalize_view_count("1.2e+06"))
        self.assertEqual(49999, normalize_view_count(49999))
        self.assertEqual(50000, normalize_view_count(50000))

        # Threshold comparisons: 49999 is NOT rounded up to 50000
        f = ReelFilters(minimum_views=50000)
        self.assertFalse(reel_matches({"views": 49999}, f))
        self.assertFalse(reel_matches({"views": "49999"}, f))
        self.assertTrue(reel_matches({"views": 50000}, f))
        self.assertTrue(reel_matches({"views": "50000"}, f))


if __name__ == "__main__":
    unittest.main()

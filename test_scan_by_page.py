"""Scan by Page flow tests using recorded (fake) provider data. No network, no Apify runs."""
import csv
import os
import tempfile
import unittest
from datetime import datetime, UTC
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.database.connection import Database
from app.database.viral_reels_store import ViralReelsStore
from app.services.viral_reels_service import ReelMetadata, ReelsProvider
from app.ui import main_window as mw

PAGE = "https://www.facebook.com/PhimHay"


class Settings:
    def __init__(self, provider="Apify Facebook Reels Scraper"):
        self.provider = provider

    def value(self, key, default=None, type=None):
        return self.provider


def reel(i, views=1000, caption="Phim hay Việt Nam"):
    return ReelMetadata(reel_url=f"https://www.facebook.com/reel/{1000 + i}", reel_id=str(1000 + i), page_id=f"manual:{PAGE}", page_name="PhimHay",
                        page_url=PAGE, caption=caption, views=views, likes=1, comments=1, shares=1,
                        published_at=datetime(2026, 1, 1, tzinfo=UTC))


class RecordedProvider(ReelsProvider):
    name = "Recorded"
    calls: list = []
    data: dict = {}
    error: Exception | None = None

    def __init__(self, token):
        pass

    def collect_reels(self, page):
        RecordedProvider.calls.append(page.page_url)
        if RecordedProvider.error:
            raise RecordedProvider.error
        return RecordedProvider.data.get(page.page_url, [])


class ScanByPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.sqlite3")
        RecordedProvider.calls, RecordedProvider.data, RecordedProvider.error = [], {}, None
        self.msgs = []
        patches = [
            patch.object(mw, "viral_settings", lambda: Settings()),
            patch.object(mw, "get_apify_token", lambda: "tok"),
            patch.object(mw, "get_meta_token", lambda: "tok"),
            patch.object(mw, "ApifyFacebookReelsProvider", RecordedProvider),
            patch.object(mw, "MetaGraphReelsProvider", RecordedProvider),
            patch.object(QMessageBox, "question", staticmethod(lambda *a, **k: self.msgs.append(("q", a[2])) or QMessageBox.StandardButton.Yes)),
            patch.object(QMessageBox, "information", staticmethod(lambda *a, **k: self.msgs.append(("i", a[2])))),
            patch.object(QMessageBox, "warning", staticmethod(lambda *a, **k: self.msgs.append(("w", a[2])))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.win = mw.MainWindow(self.db)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.db.close)
        self.addCleanup(self.win.close)
        self.page = self.win.viral_reels_page
        self.page.mode_page.setChecked(True)

    def wait(self):
        from PySide6.QtCore import QThreadPool
        for _ in range(200):
            QCoreApplication.processEvents()
            if self.page._worker is None:
                break
            QThreadPool.globalInstance().waitForDone(50)
        QCoreApplication.processEvents()

    def add(self, url):
        self.page.page_url_input.setText(url)
        self.page.add_page_button.click()

    def test_add_valid_autoselect_dedupe_unicode_invalid(self):
        self.add(PAGE + "/")
        self.assertEqual(self.page.checked_page_urls(), [PAGE])
        self.add("facebook.com/PhimHay")
        self.assertEqual(self.page.pages_model.rowCount(), 1)
        self.add("https://www.facebook.com/Phim-Hộ-Linh")
        self.assertEqual(self.page.pages_model.rowCount(), 2)
        self.add("https://example.com/x")
        self.assertTrue(any(kind == "w" for kind, _ in self.msgs))
        self.assertEqual(self.page.pages_model.rowCount(), 2)
        row = [r for r in ViralReelsStore(self.db).list_pages(limit=10) if r["page_url"] == PAGE][0]
        self.assertTrue(row["page_id"].startswith("manual:"))  # slug is never treated as numeric ID
        self.assertEqual(len(ViralReelsStore(self.db).list_pages(limit=10)), 2)

    def test_scan_uses_provider_only_for_selected_and_exports(self):
        self.add(PAGE)
        self.add("https://www.facebook.com/Other")
        for r in range(self.page.pages_model.rowCount()):
            if self.page.pages_model.item(r, 0).data(Qt.ItemDataRole.UserRole) != PAGE:
                self.page.pages_model.item(r, 0).setCheckState(Qt.CheckState.Unchecked)
        RecordedProvider.data = {PAGE: [reel(1), reel(2, views=None, caption="Tiếng Việt, có dấu"), reel(1)]}
        self.page.scan_reels_button.click()
        self.wait()
        self.assertEqual(RecordedProvider.calls, [PAGE])  # only the selected Page
        self.assertTrue(any(kind == "q" and "Pages to scan: 1" in text for kind, text in self.msgs))
        self.assertEqual(len(self.page._records), 2)
        path = Path(self.tmp.name) / "reels.csv"
        with patch.object(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), ""))):
            self.page.export_csv_button.click()
        rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
        self.assertEqual({r["reel_id"] for r in rows}, {"1001", "1002"})
        self.assertIn("Tiếng Việt, có dấu", [r["caption"] for r in rows])
        self.assertEqual(path.read_bytes()[:3], b"\xef\xbb\xbf")
        self.assertTrue(any("Exported 2 Reels" in str(t) for _, t in self.msgs))
        with patch.object(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", ""))):
            before = len(self.msgs)
            self.page.export_csv_button.click()
        self.assertEqual(len(self.msgs), before)

    def test_no_selection_no_provider_call(self):
        self.add(PAGE)
        self.page.pages_model.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
        self.page.scan_reels_button.click()
        self.wait()
        self.assertEqual(RecordedProvider.calls, [])

    def test_missing_token_blocks(self):
        self.add(PAGE)
        with patch.object(mw, "get_apify_token", lambda: ""):
            self.page.scan_reels_button.click()
            self.wait()
        self.assertEqual(RecordedProvider.calls, [])
        self.assertTrue(any(kind == "i" and "token" in text for kind, text in self.msgs))

    def test_decline_charge_makes_no_call(self):
        self.add(PAGE)
        with patch.object(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)):
            self.page.scan_reels_button.click()
            self.wait()
        self.assertEqual(RecordedProvider.calls, [])
        self.assertTrue(self.page.scan_reels_button.isEnabled())

    def test_empty_and_error_results_reported(self):
        self.add(PAGE)
        self.page.scan_reels_button.click()
        self.wait()
        self.assertTrue(any("no Reels" in str(t) for _, t in self.msgs))
        from app.services.viral_reels_service import ProviderError
        RecordedProvider.error = ProviderError("boom")
        self.page.scan_reels_button.click()
        self.wait()
        self.assertTrue(any(k == "w" and "boom" in str(t) for k, t in self.msgs))
        self.assertEqual(len(self.page._records), 0)

    def test_meta_provider_selected_no_charge_prompt(self):
        self.add(PAGE)
        RecordedProvider.data = {PAGE: [reel(3)]}
        with patch.object(mw, "viral_settings", lambda: Settings("Meta Graph API")):
            self.page.scan_reels_button.click()
            self.wait()
        self.assertFalse(any(k == "q" for k, _ in self.msgs))
        self.assertEqual(len(self.page._records), 1)

    def test_advanced_filters_do_not_hide_selected_page(self):
        self.add(PAGE)
        self.page.advanced_group.setChecked(False)
        self.page.country.setCurrentText("Japan")
        RecordedProvider.data = {PAGE: [reel(4)]}
        self.page.scan_reels_button.click()
        self.wait()
        self.assertEqual(RecordedProvider.calls, [PAGE])
        self.assertEqual(len(self.page._records), 1)

    def test_cancellation_preserves_completed_data_prevents_duplicates_and_safe_close(self):
        import time
        self.add(PAGE)
        gate = __import__("threading").Event()

        class SlowProvider(ReelsProvider):
            name = "Slow"
            def __init__(self, token): pass
            def collect_reels(self, page):
                yield reel(10, views=5000, caption="Reel đã xong trước khi hủy")
                gate.set()
                for _ in range(100):
                    time.sleep(0.01)
                yield reel(11, views=6000, caption="Reel sau khi hủy")

        with patch.object(mw, "ApifyFacebookReelsProvider", SlowProvider):
            self.page.scan_reels_button.click()
            # Cannot start a duplicate worker while busy
            self.assertFalse(self.page.run_operation(lambda c, p: None, lambda r: None))
            gate.wait(timeout=2.0)
            time.sleep(0.05)  # allow first reel to be saved to SQLite
            self.page.stop_button.click()
            self.wait()

        # Completed first reel is preserved in SQLite and loaded into UI on cancel
        self.assertEqual(len(self.page._records), 1)
        self.assertEqual(self.page._records[0]["reel_id"], "1010")
        self.assertTrue(self.page.scan_reels_button.isEnabled())
        self.assertFalse(self.page.stop_button.isEnabled())

        # Closing widget while worker is active disconnects signals without crashing
        gate.clear()
        with patch.object(mw, "ApifyFacebookReelsProvider", SlowProvider):
            self.page.scan_reels_button.click()
            gate.wait(timeout=2.0)
            self.page.close()
            from PySide6.QtCore import QThreadPool
            QThreadPool.globalInstance().waitForDone(2000)

    def test_export_selected_vs_export_all(self):
        self.add(PAGE)
        RecordedProvider.data = {
            PAGE: [
                reel(21, views=1000, caption="Reel Một"),
                reel(22, views=2000, caption="Reel Hai"),
                reel(23, views=3000, caption="Reel Ba"),
            ]
        }
        self.page.scan_reels_button.click()
        self.wait()
        self.assertEqual(len(self.page._records), 3)

        # Select row 0 only -> Export CSV exports selected row
        self.page.table.selectRow(0)
        sel_path = Path(self.tmp.name) / "selected_reels.csv"
        with patch.object(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(sel_path), ""))):
            self.page.export_csv_button.click()
        sel_rows = list(csv.DictReader(sel_path.open(encoding="utf-8-sig")))
        self.assertEqual(len(sel_rows), 1)

        # Clear selection -> Export CSV exports all 3 rows
        self.page.table.clearSelection()
        all_path = Path(self.tmp.name) / "all_reels.csv"
        with patch.object(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(all_path), ""))):
            self.page.export_csv_button.click()
        all_rows = list(csv.DictReader(all_path.open(encoding="utf-8-sig")))
        self.assertEqual(len(all_rows), 3)


    def test_apify_reels_payload_uses_results_limit_and_async_poll_persist(self):
        from app.services.apify_facebook_provider import ApifyFacebookReelsProvider
        from app.services.viral_reels_service import PageCandidate, ProviderError

        payload = ApifyFacebookReelsProvider.build_reels_payload(PAGE, results_limit=30)
        self.assertEqual(payload, {"startUrls": [{"url": PAGE}], "resultsLimit": 30})
        self.assertNotIn("maxItems", payload)

        store = ViralReelsStore(self.db)
        provider = ApifyFacebookReelsProvider("fake_tok", results_limit=30, store=store, poll_interval=0.0, sleep=lambda _: None)

        calls = []
        responses = {
            ("GET", "https://api.apify.com/v2/acts/apify~facebook-reels-scraper/runs?desc=1&limit=3"): {"data": {"items": []}},
            ("POST", "https://api.apify.com/v2/acts/apify~facebook-reels-scraper/runs"): {
                "data": {"id": "run_abc", "status": "READY", "defaultDatasetId": "ds_abc", "startedAt": "2026-11-04T08:00:00Z"}
            },
            ("GET", "https://api.apify.com/v2/actor-runs/run_abc"): [
                ProviderError("Apify network timeout or error: timed out"),
                {"data": {"id": "run_abc", "status": "RUNNING", "defaultDatasetId": "ds_abc", "statusMessage": "Crawled 1/3 pages"}},
                {"data": {"id": "run_abc", "status": "SUCCEEDED", "defaultDatasetId": "ds_abc", "finishedAt": "2026-11-04T08:01:00Z"}},
            ],
            ("GET", "https://api.apify.com/v2/datasets/ds_abc/items?format=json&clean=true"): [
                {"shareable_url": "https://www.facebook.com/reel/777001", "text": "Reel thử nghiệm", "playCountRounded": 12000, "time": "2026-11-01T00:00:00.000Z"},
                {"shareable_url": "https://facebook.com/reel/777001/", "text": "Trùng ID", "playCountRounded": 12000},
            ],
        }

        def fake_req(url, *, method="GET", payload=None):
            calls.append((method, url, payload))
            val = responses[(method, url)]
            if isinstance(val, list) and method == "GET" and "actor-runs/run_abc" in url:
                item = val.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            return val

        provider._request_json = fake_req
        cand = PageCandidate("p1", "PhimHay", PAGE, "Vietnam", "Vietnamese", "")
        reels = list(provider.collect_reels(cand))
        self.assertEqual(len(reels), 1)
        self.assertEqual(reels[0].reel_id, "777001")
        self.assertEqual(reels[0].views, 12000)
        # Only 1 POST to start run despite transient poll timeout
        post_calls = [c for c in calls if c[0] == "POST"]
        self.assertEqual(len(post_calls), 1)
        # Run ID persisted in SQLite
        persisted = store.provider_configuration("apify_reels_last_run")
        self.assertEqual(persisted.get("run_id"), "run_abc")
        self.assertEqual(persisted.get("dataset_id"), "ds_abc")
        self.assertEqual(persisted.get("status"), "SUCCEEDED")

        # Duplicate active run guard prevents starting a second paid Actor
        responses[("GET", "https://api.apify.com/v2/acts/apify~facebook-reels-scraper/runs?desc=1&limit=3")] = {
            "data": {"items": [{"id": "run_active", "status": "RUNNING"}]}
        }
        with self.assertRaisesRegex(ProviderError, "already running"):
            provider.start_run(PAGE, 30)

    def test_recover_existing_run_in_main_window_without_starting_actor(self):
        from PySide6.QtWidgets import QInputDialog
        self.add(PAGE)

        class FakeRecoverProvider:
            started_runs = 0
            def __init__(self, token, **kwargs):
                self.store = kwargs.get("store")
            def poll_run_until_done(self, run_id):
                return {"id": run_id, "status": "SUCCEEDED", "defaultDatasetId": "ds_rec"}
            def fetch_dataset_items(self, dataset_id):
                return [
                    {"shareable_url": "https://www.facebook.com/reel/888001", "inputUrl": PAGE, "text": "Khôi phục thành công", "playCountRounded": 9500, "time": "2026-11-02T00:00:00.000Z"},
                    {"shareable_url": "https://www.facebook.com/reel/888002", "inputUrl": PAGE, "text": "Reel số 2", "playCountRounded": 4200, "time": "2026-11-03T00:00:00.000Z"},
                ]
            def parse_reel_items(self, items, fallback_page, pages_by_url=None):
                from app.services.apify_facebook_provider import ApifyFacebookReelsProvider
                return ApifyFacebookReelsProvider.parse_reel_items(items, fallback_page, pages_by_url)

        with (
            patch.object(mw, "ApifyFacebookReelsProvider", FakeRecoverProvider),
            patch.object(QInputDialog, "getText", staticmethod(lambda *a, **k: ("run_existing_123", True))),
        ):
            self.page.recover_run_button.click()
            self.wait()

        self.assertEqual(FakeRecoverProvider.started_runs, 0)
        self.assertEqual(len(self.page._records), 2)
        self.assertEqual({r["reel_id"] for r in self.page._records}, {"888001", "888002"})


    def test_stop_never_auto_aborts_on_cancel_or_close_and_keeps_run_history(self):
        import threading
        from app.services.apify_facebook_provider import ApifyFacebookReelsProvider

        store = ViralReelsStore(self.db)
        cancel_ev = threading.Event()
        cancel_ev.set()
        provider = ApifyFacebookReelsProvider("fake_tok", store=store, cancel_event=cancel_ev, poll_interval=0.0, sleep=lambda _: None)

        abort_called = []
        provider.abort_run = lambda rid: abort_called.append(rid) or {}
        provider._persist_run({"id": "run_first", "defaultDatasetId": "ds_1", "status": "SUCCEEDED"}, page_urls=[PAGE])
        provider._persist_run({"id": "run_second", "defaultDatasetId": "ds_2", "status": "RUNNING"}, page_urls=["https://www.facebook.com/SecondPage"])

        # Cancelling poll_run_until_done must NOT call abort_run automatically
        res = provider.poll_run_until_done("run_second")
        self.assertEqual(abort_called, [])
        self.assertEqual(res.get("id"), "run_second")

        # Both runs are preserved in runs_by_id without overwriting the first run's metadata
        cfg = store.provider_configuration("apify_reels_last_run")
        self.assertEqual(cfg.get("task"), "scan_by_page")
        self.assertEqual(cfg.get("actor_id"), "apify~facebook-reels-scraper")
        self.assertIn("run_first", cfg.get("runs_by_id", {}))
        self.assertIn("run_second", cfg.get("runs_by_id", {}))
        self.assertEqual(cfg["runs_by_id"]["run_first"]["dataset_id"], "ds_1")
        self.assertEqual(cfg["runs_by_id"]["run_first"]["page_urls"], [PAGE])
        self.assertEqual(cfg["runs_by_id"]["run_second"]["dataset_id"], "ds_2")

        # Clicking Stop in MainWindow when user declines abort -> no abort_run call
        with patch.object(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)):
            with patch.object(mw, "ApifyFacebookReelsProvider", lambda *a, **k: provider):
                self.win.stop_viral_operation()
        self.assertEqual(abort_called, [])

        # Clicking Stop in MainWindow when user explicitly confirms Yes -> abort_run called once
        with patch.object(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)):
            with patch.object(mw, "ApifyFacebookReelsProvider", lambda *a, **k: provider):
                self.win.stop_viral_operation()
        self.assertEqual(abort_called, ["run_second"])


if __name__ == "__main__":
    unittest.main()


import csv
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.database.connection import Database
from app.database.viral_reels_store import ViralReelsStore
from app.services.apify_facebook_provider import ApifyFacebookReelsProvider
from app.services.viral_reels_service import (
    PageCandidate,
    extract_page_identity,
    normalize_facebook_url,
)


class TestProfilePhpUrlHotfix(unittest.TestCase):
    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(self.temp_db_fd)
        self.db = Database(self.temp_db_path)
        self.store = ViralReelsStore(self.db)

    def tearDown(self):
        self.store.close()
        try:
            os.remove(self.temp_db_path)
        except OSError:
            pass

    # 1. profile.php?id=61594805510676
    def test_profile_php_with_id_without_domain(self):
        normalized = normalize_facebook_url("profile.php?id=61594805510676")
        self.assertEqual("https://www.facebook.com/profile.php?id=61594805510676", normalized)
        page_id, name, url = extract_page_identity("profile.php?id=61594805510676")
        self.assertEqual("61594805510676", page_id)
        self.assertEqual("Page 61594805510676", name)
        self.assertEqual("https://www.facebook.com/profile.php?id=61594805510676", url)

    # 2. www.facebook.com/profile.php?id=61594805510676
    def test_profile_php_with_www(self):
        normalized = normalize_facebook_url("www.facebook.com/profile.php?id=61594805510676")
        self.assertEqual("https://www.facebook.com/profile.php?id=61594805510676", normalized)
        page_id, name, url = extract_page_identity("www.facebook.com/profile.php?id=61594805510676")
        self.assertEqual("61594805510676", page_id)
        self.assertEqual("Page 61594805510676", name)
        self.assertEqual("https://www.facebook.com/profile.php?id=61594805510676", url)

    # 3. m.facebook.com/profile.php?id=61594805510676
    def test_profile_php_with_m_subdomain(self):
        normalized = normalize_facebook_url("m.facebook.com/profile.php?id=61594805510676")
        self.assertEqual("https://www.facebook.com/profile.php?id=61594805510676", normalized)
        page_id, name, url = extract_page_identity("m.facebook.com/profile.php?id=61594805510676")
        self.assertEqual("61594805510676", page_id)
        self.assertEqual("Page 61594805510676", name)
        self.assertEqual("https://www.facebook.com/profile.php?id=61594805510676", url)

    # 4. profile.php?id=61594805510676&fbclid=abc
    def test_profile_php_drops_tracking_params_preserves_id(self):
        normalized = normalize_facebook_url("profile.php?id=61594805510676&fbclid=abc")
        self.assertEqual("https://www.facebook.com/profile.php?id=61594805510676", normalized)
        page_id, name, url = extract_page_identity("profile.php?id=61594805510676&fbclid=abc")
        self.assertEqual("61594805510676", page_id)
        self.assertEqual("Page 61594805510676", name)
        self.assertEqual("https://www.facebook.com/profile.php?id=61594805510676", url)

    # 5. facebook.com/examplepage
    def test_username_page_url(self):
        normalized = normalize_facebook_url("facebook.com/examplepage")
        self.assertEqual("https://www.facebook.com/examplepage", normalized)
        page_id, name, url = extract_page_identity("facebook.com/examplepage")
        self.assertEqual("manual:https://www.facebook.com/examplepage", page_id)
        self.assertEqual("examplepage", name)
        self.assertEqual("https://www.facebook.com/examplepage", url)

    # 6. facebook.com/pages/example/123456789
    def test_pages_format_url(self):
        normalized = normalize_facebook_url("facebook.com/pages/example/123456789")
        self.assertEqual("https://www.facebook.com/pages/example/123456789", normalized)
        page_id, name, url = extract_page_identity("facebook.com/pages/example/123456789")
        self.assertEqual("123456789", page_id)
        self.assertEqual("example", name)
        self.assertEqual("https://www.facebook.com/pages/example/123456789", url)

    # 7. profile.php không có ID phải bị từ chối
    def test_profile_php_without_id_rejected(self):
        with self.assertRaises(ValueError):
            normalize_facebook_url("profile.php")
        with self.assertRaises(ValueError):
            normalize_facebook_url("https://www.facebook.com/profile.php")
        with self.assertRaises(ValueError):
            normalize_facebook_url("https://www.facebook.com/profile.php?foo=bar")
        with self.assertRaises(ValueError):
            extract_page_identity("profile.php")

    # 8. profile.php?id=abc phải bị từ chối
    def test_profile_php_non_numeric_id_rejected(self):
        with self.assertRaises(ValueError):
            normalize_facebook_url("profile.php?id=abc")
        with self.assertRaises(ValueError):
            normalize_facebook_url("https://www.facebook.com/profile.php?id=abc")
        with self.assertRaises(ValueError):
            extract_page_identity("profile.php?id=abc")

    # 9. Hai Page khác ID không bị gộp thành một
    def test_two_different_page_ids_not_merged(self):
        page_id1, name1, url1 = extract_page_identity("profile.php?id=61594805510676")
        page_id2, name2, url2 = extract_page_identity("profile.php?id=99999999999999")
        self.store.upsert_page(PageCandidate(page_id1, name1, url1, "Unknown", "Unknown", ""))
        self.store.upsert_page(PageCandidate(page_id2, name2, url2, "Unknown", "Unknown", ""))

        rows = self.store.list_pages(limit=10)
        self.assertEqual(2, len(rows))
        ids = {r["page_id"] for r in rows}
        urls = {r["page_url"] for r in rows}
        self.assertIn("61594805510676", ids)
        self.assertIn("99999999999999", ids)
        self.assertIn("https://www.facebook.com/profile.php?id=61594805510676", urls)
        self.assertIn("https://www.facebook.com/profile.php?id=99999999999999", urls)

    # 10. Thêm trùng Page ID không tạo duplicate
    def test_duplicate_page_id_no_duplicates(self):
        page_id, name, url = extract_page_identity("https://www.facebook.com/profile.php?id=61594805510676")
        self.store.upsert_page(PageCandidate(page_id, name, url, "Unknown", "Unknown", ""))
        # Upsert same ID again with tracking param
        page_id2, name2, url2 = extract_page_identity("profile.php?id=61594805510676&fbclid=xyz")
        self.store.upsert_page(PageCandidate(page_id2, name2, url2, "Vietnam", "Vietnamese", ""))

        rows = self.store.list_pages(limit=10)
        self.assertEqual(1, len(rows))
        self.assertEqual("61594805510676", rows[0]["page_id"])
        self.assertEqual("https://www.facebook.com/profile.php?id=61594805510676", rows[0]["page_url"])

    # 11. Lưu SQLite rồi restart phải giữ nguyên ID
    def test_sqlite_persistence_across_restarts(self):
        page_id, name, url = extract_page_identity("https://www.facebook.com/profile.php?id=61594805510676")
        self.store.upsert_page(PageCandidate(page_id, name, url, "Unknown", "Unknown", ""))
        self.store.close()

        # Reopen connection (simulating app restart)
        reopened_db = Database(self.temp_db_path)
        reopened_store = ViralReelsStore(reopened_db)
        try:
            rows = reopened_store.list_pages(limit=10)
            self.assertEqual(1, len(rows))
            self.assertEqual("61594805510676", rows[0]["page_id"])
            self.assertEqual("Page 61594805510676", rows[0]["page_name"])
            self.assertEqual("https://www.facebook.com/profile.php?id=61594805510676", rows[0]["page_url"])
        finally:
            reopened_store.close()

    # 12. Import danh sách Page phải giữ nguyên ID
    def test_import_page_list_preserves_id(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["https://www.facebook.com/profile.php?id=61594805510676"])
            writer.writerow(["profile.php?id=61594805510676&fbclid=dup"])
            writer.writerow(["facebook.com/pages/test/987654321"])
            csv_path = f.name

        try:
            with open(csv_path, newline="", encoding="utf-8-sig") as handle:
                for row in csv.reader(handle):
                    if row and row[0].strip():
                        pid, pname, p_url = extract_page_identity(row[0].strip())
                        pages = self.store.list_pages(limit=1000)
                        if not any(str(r["page_url"]) == p_url or (pid and str(r["page_id"]) == pid) for r in pages):
                            self.store.upsert_page(PageCandidate(pid, pname, p_url, "Unknown", "Unknown", ""))

            rows = self.store.list_pages(limit=10)
            self.assertEqual(2, len(rows))
            rows_by_id = {r["page_id"]: r for r in rows}
            self.assertIn("61594805510676", rows_by_id)
            self.assertEqual("https://www.facebook.com/profile.php?id=61594805510676", rows_by_id["61594805510676"]["page_url"])
            self.assertEqual("Page 61594805510676", rows_by_id["61594805510676"]["page_name"])
            self.assertIn("987654321", rows_by_id)
            self.assertEqual("https://www.facebook.com/pages/test/987654321", rows_by_id["987654321"]["page_url"])
        finally:
            try:
                os.remove(csv_path)
            except OSError:
                pass

    # 13. Mock Apify để xác nhận URL truyền đi chính xác
    def test_mock_apify_receives_exact_url_with_id(self):
        # 13a. build_reels_payload preserves profile.php with id
        payload = ApifyFacebookReelsProvider.build_reels_payload("https://www.facebook.com/profile.php?id=61594805510676")
        self.assertEqual(
            [{"url": "https://www.facebook.com/profile.php?id=61594805510676"}],
            payload["startUrls"],
        )

        # 13b. start_run calls Apify API with correct payload containing ID
        provider = ApifyFacebookReelsProvider("fake_token", store=self.store, poll_interval=0.0, sleep=lambda _: None)
        captured_payloads = []

        def fake_request_json(url, *, method="GET", payload=None):
            if method == "POST" and "runs" in url:
                captured_payloads.append(payload)
                return {"data": {"id": "run_test_123", "defaultDatasetId": "ds_test_123"}}
            return {}

        with patch.object(provider, "_request_json", side_effect=fake_request_json):
            with patch.object(provider, "list_recent_runs", return_value=[]):
                run_data = provider.start_run("https://www.facebook.com/profile.php?id=61594805510676", results_limit=15)

        self.assertEqual("run_test_123", run_data["id"])
        self.assertEqual(1, len(captured_payloads))
        self.assertEqual(
            [{"url": "https://www.facebook.com/profile.php?id=61594805510676"}],
            captured_payloads[0]["startUrls"],
        )
        self.assertEqual(15, captured_payloads[0]["resultsLimit"])

    # Legacy database checks: only fix when ID recoverable from reliable source, do not guess
    def test_legacy_data_without_id_is_not_guessed(self):
        # Insert legacy corrupted record without ID
        with self.db.connection:
            self.db.connection.execute(
                "INSERT INTO discovered_pages (page_id, page_name, page_url, country, language, topic, source, discovered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("manual:https://www.facebook.com/profile.php", "profile.php", "https://www.facebook.com/profile.php", "Unknown", "Unknown", "", "manual", "2026-11-04T00:00:00Z"),
            )
        repaired = self.store.repair_legacy_pages()
        self.assertEqual(0, repaired)
        rows = self.store.list_pages(limit=10)
        self.assertEqual(1, len(rows))
        # Ensure we did NOT guess an ID
        self.assertEqual("manual:https://www.facebook.com/profile.php", rows[0]["page_id"])
        self.assertEqual("https://www.facebook.com/profile.php", rows[0]["page_url"])

    def test_legacy_data_with_recoverable_id_is_repaired(self):
        # Insert legacy record that has ID in page_id or URL
        with self.db.connection:
            self.db.connection.execute(
                "INSERT INTO discovered_pages (page_id, page_name, page_url, country, language, topic, source, discovered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("manual:https://www.facebook.com/profile.php?id=555666", "profile.php", "https://www.facebook.com/profile.php?id=555666", "Unknown", "Unknown", "", "manual", "2026-11-04T00:00:00Z"),
            )
        repaired = self.store.repair_legacy_pages()
        self.assertEqual(1, repaired)
        rows = self.store.list_pages(limit=10)
        self.assertEqual(1, len(rows))
        self.assertEqual("555666", rows[0]["page_id"])
        self.assertEqual("Page 555666", rows[0]["page_name"])
        self.assertEqual("https://www.facebook.com/profile.php?id=555666", rows[0]["page_url"])


if __name__ == "__main__":
    unittest.main()


from pathlib import Path
from PySide6.QtWidgets import QApplication, QMessageBox
import app.ui.main_window as mw


class SettingsStub:
    def __init__(self):
        self._d = {}

    def value(self, key, default=None, type=None):
        return self._d.get(key, default)

    def setValue(self, key, val):
        self._d[key] = val


class TestProfilePhpUiIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test_ui.sqlite3")
        self.msgs = []
        patches = [
            patch.object(mw, "viral_settings", lambda: SettingsStub()),
            patch.object(QMessageBox, "warning", staticmethod(lambda *a, **k: self.msgs.append(("w", a[2])))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.win = mw.MainWindow(self.db)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.db.close)
        self.addCleanup(self.win.close)

    def test_ui_add_profile_php_flow(self):
        # 1. Add sample URL
        self.win.add_viral_page("https://www.facebook.com/profile.php?id=61594805510676")

        # 2. Check UI display
        found = self.win.viral_reels_page.found_records()
        self.assertEqual(1, len(found))
        self.assertEqual("Page 61594805510676", found[0]["page_name"])
        self.assertEqual("https://www.facebook.com/profile.php?id=61594805510676", found[0]["page_url"])
        self.assertEqual("61594805510676", found[0]["page_id"])

        # 3. Check SQLite store
        store = ViralReelsStore(self.db)
        pages = store.list_pages(limit=10)
        self.assertEqual(1, len(pages))
        self.assertEqual("61594805510676", pages[0]["page_id"])
        self.assertEqual("Page 61594805510676", pages[0]["page_name"])
        self.assertEqual("https://www.facebook.com/profile.php?id=61594805510676", pages[0]["page_url"])

        # 4. Adding duplicate does not duplicate
        self.win.add_viral_page("profile.php?id=61594805510676&fbclid=tracking")
        self.assertEqual(1, len(store.list_pages(limit=10)))
        self.assertEqual(1, len(self.win.viral_reels_page.found_records()))

        # 5. Simulate restart
        self.win.close()
        win2 = mw.MainWindow(self.db)
        try:
            found2 = win2.viral_reels_page.found_records()
            self.assertEqual(1, len(found2))
            self.assertEqual("Page 61594805510676", found2[0]["page_name"])
            self.assertEqual("https://www.facebook.com/profile.php?id=61594805510676", found2[0]["page_url"])
            self.assertEqual("61594805510676", found2[0]["page_id"])
        finally:
            win2.close()

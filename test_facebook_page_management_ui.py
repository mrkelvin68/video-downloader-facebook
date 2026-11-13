import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from app.database.connection import Database
from app.services.facebook_connection_service import FacebookConnectionService
from app.services.facebook_publishing_service import FacebookPublishingService
from app.services.project_service import ProjectService
from app.ui.pages.facebook_page_management_page import FacebookPageManagementPage


class _SyncClient:
    def __init__(self, token): self.token = token
    def validate_and_list_pages(self):
        return [{"id": "page-c", "name": "Page C", "permissions": ["pages_manage_posts"], "access_token": "page-token"}]


class _TestClient:
    def __init__(self, token): self.token = token
    def _get_page_identity(self, page_id, *, raise_errors=False):
        return {"id": page_id, "name": "Verified Page", "url": "", "permissions": ["pages_read_engagement"], "access_token": self.token}
    def validate_and_list_pages(self):
        return [{"id": "page-a", "name": "Verified Page", "permissions": ["pages_read_engagement"], "access_token": self.token}]


class FacebookPageManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "test.sqlite3")
        self.project_id = ProjectService(self.database).create("Project", "p", str(Path(self.temp.name) / "videos"))
        FacebookPublishingService(self.database).save_pages(self.project_id, [
            {"id": "page-a", "name": "Page A", "permissions": ["pages_manage_posts"]},
            {"id": "page-b", "name": "Page B", "permissions": []},
        ], "page-a")

    def tearDown(self):
        self.database.close(); self.temp.cleanup()

    def test_page_management_lists_local_pages_and_marks_unverified_permissions(self):
        page = FacebookPageManagementPage(self.database)
        page.project.setCurrentIndex(page.project.findData(self.project_id))
        self.assertEqual(page.table.rowCount(), 2)
        self.assertEqual(page.table.item(0, 4).text(), "Chưa xác minh")
        self.assertEqual(page.table.item(0, 3).text(), "Đã kết nối (chưa xác minh)")
        page.set_verified(self.project_id, [{"id": "page-a"}])
        self.assertEqual(page.table.item(0, 5).text(), "Chưa xác minh")
        self.assertEqual(page.table.item(0, 6).text(), "Chưa xác minh")

    def test_default_and_single_page_disconnect_preserve_other_page(self):
        service = FacebookConnectionService(self.database)
        service.set_default_page(self.project_id, "page-b")
        pages = FacebookPublishingService(self.database).list_pages(self.project_id)
        self.assertEqual(next(page for page in pages if page["is_default"])["page_id"], "page-b")
        with patch("app.services.facebook_connection_service.delete_page_token") as delete_token:
            service.disconnect_page(self.project_id, "page-a")
        delete_token.assert_called_once_with(self.project_id, "page-a")
        self.assertEqual([page["page_id"] for page in FacebookPublishingService(self.database).list_pages(self.project_id)], ["page-b"])

    def test_sync_uses_existing_meta_credential_and_updates_local_pages(self):
        service = FacebookConnectionService(self.database, _SyncClient)
        with patch("app.services.facebook_connection_service.get_meta_token", return_value="meta-token"), patch("app.services.facebook_connection_service.set_page_token"):
            pages = service.sync_pages(self.project_id)
        self.assertEqual(pages[0]["id"], "page-c")
        self.assertIn("page-c", [page["page_id"] for page in FacebookPublishingService(self.database).list_pages(self.project_id)])

    def test_token_check_persists_verified_page_permissions_for_auto_comment(self):
        service = FacebookConnectionService(self.database, _TestClient)
        with patch("app.services.facebook_connection_service.get_page_token", return_value="page-token"):
            service.test_connection(self.project_id)
        page = next(row for row in FacebookPublishingService(self.database).list_pages(self.project_id) if row["page_id"] == "page-a")
        self.assertIn("pages_read_engagement", page["permissions_json"])


if __name__ == "__main__":
    unittest.main()

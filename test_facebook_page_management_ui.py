import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from app.database.connection import Database
from app.services.facebook_connection_service import FacebookConnectionError, FacebookConnectionService
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
    def verify_page_read_access(self, page_id):
        return self._get_page_identity(page_id)
    def validate_and_list_pages(self):
        return [{"id": "page-a", "name": "Verified Page", "permissions": ["pages_read_engagement"], "access_token": self.token}]


class _ExpiredClient:
    def __init__(self, token): self.token = token
    def verify_page_read_access(self, page_id):
        raise FacebookConnectionError("expired", category="expired")


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
        self.assertEqual(page["read_access_status"], "GRANTED")
        ui = FacebookPageManagementPage(self.database)
        ui.project.setCurrentIndex(ui.project.findData(self.project_id))
        self.assertEqual(ui.table.item(0, 3).text(), "Đã xác minh")
        self.assertEqual(ui.table.item(0, 5).text(), "Có")

    def test_token_check_persists_expired_state(self):
        service = FacebookConnectionService(self.database, _ExpiredClient)
        with patch("app.services.facebook_connection_service.get_page_token", return_value="page-token"):
            with self.assertRaises(FacebookConnectionError):
                service.test_connection(self.project_id)
        page = next(row for row in FacebookPublishingService(self.database).list_pages(self.project_id) if row["page_id"] == "page-a")
        self.assertEqual(page["read_access_status"], "TOKEN_EXPIRED")

    def test_central_disconnect_and_delete_remove_from_db_and_stay_gone_after_restart(self):
        revoked_tokens = []

        class _RevokeClient:
            def __init__(self, token):
                self.token = token

            def revoke_token(self):
                revoked_tokens.append(self.token)
                return True

        service = FacebookConnectionService(self.database, _RevokeClient)
        with patch("app.services.facebook_connection_service.get_page_token", return_value="tok-a"), patch(
            "app.services.facebook_connection_service.delete_page_token"
        ) as mock_del_tok:
            service.disconnect_central_page("page-a", revoke_remote=True)

        self.assertEqual(revoked_tokens, ["tok-a"])
        self.assertEqual(mock_del_tok.call_count, 2)  # both central page_id and legacy (project_id, page_id)
        # Verify page-a is removed from both facebook_page_connections and facebook_pages
        self.assertEqual(
            [r["page_id"] for r in FacebookPublishingService(self.database).list_connected_pages()],
            ["page-b"],
        )
        self.assertEqual(
            [r["page_id"] for r in FacebookPublishingService(self.database).list_pages(self.project_id)],
            ["page-b"],
        )

        # Simulate app exit and restart (re-opening Database runs migrate() again)
        db_path = Path(self.temp.name) / "test.sqlite3"
        self.database.close()
        self.database = Database(db_path)

        ui = FacebookPageManagementPage(self.database)
        self.assertEqual(ui.table.rowCount(), 1)
        self.assertEqual(ui.table.item(0, 1).text(), "page-b")

        # Verify action buttons in column 8 ("Kiểm tra lại", "Ngắt kết nối", "Thu hồi / Xóa DB")
        actions_widget = ui.table.cellWidget(0, 8)
        button_texts = [b.text() for b in actions_widget.findChildren(type(ui.connect_button))]
        self.assertEqual(button_texts, ["Kiểm tra lại", "Ngắt kết nối", "Thu hồi / Xóa DB"])

        # Now delete page-b directly from DB without remote revoke
        service2 = FacebookConnectionService(self.database, _RevokeClient)
        with patch("app.services.facebook_connection_service.delete_page_token"), patch(
            "app.services.facebook_connection_service.delete_meta_token"
        ) as mock_del_meta:
            service2.disconnect_central_page("page-b", revoke_remote=False)
            mock_del_meta.assert_called_once()

        ui.refresh_pages()
        self.assertEqual(ui.table.rowCount(), 0)

    def test_auto_comment_dropdown_uses_central_page_management_as_single_source_of_truth(self):
        from app.services.auto_comment_service import AutoCommentPageAccessError, AutoCommentService
        from app.ui.pages.auto_comment_page import AutoCommentPage

        # Clear legacy project-scoped facebook_pages so only central facebook_page_connections is used
        with self.database.connection:
            self.database.connection.execute("DELETE FROM facebook_pages")
            self.database.connection.execute("DELETE FROM facebook_page_connections")

        class _QuynhBeoClient:
            def __init__(self, token):
                self.token = token

            def validate_and_list_pages(self):
                return [
                    {
                        "id": "1458043644054753",
                        "name": "Quỳnh Béo",
                        "permissions": ["pages_read_engagement"],  # comment perm unverified
                        "access_token": "central-page-token-1458043644054753",
                    },
                    {
                        "id": "104934391155107",
                        "name": "Dino Review",
                        "permissions": ["CREATE_CONTENT"],
                        "access_token": "central-page-token-104934391155107",
                    },
                ]

            def verify_page_read_access(self, page_id):
                return {
                    "id": page_id,
                    "name": "Quỳnh Béo" if page_id == "1458043644054753" else "Dino Review",
                    "url": "",
                    "permissions": ["pages_read_engagement"],
                    "access_token": self.token,
                }

        conn_service = FacebookConnectionService(self.database, _QuynhBeoClient)
        token_store: dict[str, str] = {}

        def fake_set_page_token(arg1, arg2, arg3=None):
            page_id = arg2 if arg3 is not None else arg1
            tok = arg3 if arg3 is not None else arg2
            token_store[str(page_id)] = str(tok)

        def fake_get_page_token(arg1, arg2=None):
            page_id = arg2 if arg2 is not None else arg1
            return token_store.get(str(page_id), "")

        def fake_delete_page_token(arg1, arg2=None):
            page_id = arg2 if arg2 is not None else arg1
            token_store.pop(str(page_id), None)

        with (
            patch("app.services.facebook_connection_service.set_meta_token"),
            patch("app.services.facebook_connection_service.set_page_token", side_effect=fake_set_page_token),
            patch("app.services.facebook_connection_service.get_page_token", side_effect=fake_get_page_token),
            patch("app.services.facebook_connection_service.delete_page_token", side_effect=fake_delete_page_token),
            patch("app.services.auto_comment_service.get_page_token", side_effect=fake_get_page_token),
            patch("app.services.auto_comment_comment_service.get_page_token", side_effect=fake_get_page_token),
        ):
            # 1 & 2. Connect via Central Page Management (no facebook_pages rows) -> Quỳnh Béo (1458043644054753) and Dino Review (CREATE_CONTENT only) both appear
            conn_service.connect_central("user-token")
            conn_service.test_central()

            auto_page = AutoCommentPage(self.database)
            dropdown_items = [
                (auto_page.page.itemText(i), auto_page.page.itemData(i))
                for i in range(1, auto_page.page.count())
            ]
            page_ids = [data[1] for _text, data in dropdown_items]
            self.assertIn("1458043644054753", page_ids)
            self.assertIn("104934391155107", page_ids)
            self.assertEqual(len(page_ids), len(set(page_ids)))

            # Select Quỳnh Béo (1458043644054753)
            quynh_idx = next(
                i
                for i in range(1, auto_page.page.count())
                if auto_page.page.itemData(i)[1] == "1458043644054753"
            )
            auto_page.page.setCurrentIndex(quynh_idx)
            self.assertEqual(auto_page.page.currentText(), "Quỳnh Béo")
            selected_project, selected_page_id = auto_page.page.currentData()
            self.assertEqual(selected_page_id, "1458043644054753")

            # Verify access state is CONNECTED / GRANTED and Step 2 library creation works without facebook_pages row
            ac_service = AutoCommentService(self.database)
            access = ac_service.page_access(selected_project, "1458043644054753")
            self.assertEqual(access["connection_state"], "CONNECTED")
            self.assertEqual(access["read_status"], "GRANTED")
            self.assertTrue(access["has_token"])
            lib_id = ac_service.create_owned_library(selected_project, "1458043644054753", "Thư viện Quỳnh Béo")
            self.assertTrue(lib_id)

            # 6. Switch tabs / refresh_pages -> selection of Page 1458043644054753 is preserved
            auto_page.refresh_pages()
            self.assertEqual(auto_page.page.currentData()[1], "1458043644054753")

            # 8. Temporary network/API error during check -> does NOT mark token expired and does NOT hide Page
            class _TransientNetworkClient:
                def __init__(self, token):
                    self.token = token

                def verify_page_read_access(self, page_id):
                    raise FacebookConnectionError("Network timeout", category="network")

            FacebookConnectionService(self.database, _TransientNetworkClient).test_central()
            auto_page.refresh_pages()
            self.assertIn(
                "1458043644054753",
                [auto_page.page.itemData(i)[1] for i in range(1, auto_page.page.count())],
            )
            net_access = ac_service.page_access(selected_project, "1458043644054753")
            self.assertEqual(net_access["read_status"], "NETWORK_ERROR")
            self.assertNotEqual(net_access["connection_state"], "TOKEN_EXPIRED")

            # 3. Confirmed token expiration on Dino Review (104934391155107) -> filtered out from Auto Comment dropdown, while valid Quỳnh Béo (1458043644054753) remains
            class _SelectiveExpiredClient:
                def __init__(self, token):
                    self.token = token

                def verify_page_read_access(self, page_id):
                    if page_id == "104934391155107":
                        raise FacebookConnectionError("expired", category="expired")
                    return {
                        "id": page_id,
                        "name": "Quỳnh Béo",
                        "url": "",
                        "permissions": ["pages_read_engagement"],
                        "access_token": self.token,
                    }

            FacebookConnectionService(self.database, _SelectiveExpiredClient).test_central()
            auto_page.refresh_pages()
            dropdown_ids_after_expire = [
                auto_page.page.itemData(i)[1] for i in range(1, auto_page.page.count())
            ]
            self.assertIn("1458043644054753", dropdown_ids_after_expire)
            self.assertNotIn("104934391155107", dropdown_ids_after_expire)
            exp_access = ac_service.page_access(selected_project, "104934391155107")
            self.assertEqual(exp_access["connection_state"], "TOKEN_EXPIRED")
            with self.assertRaisesRegex(AutoCommentPageAccessError, "hết hạn"):
                ac_service.fetch_posts(selected_project, "104934391155107", "2026-11-01", "2026-11-16", "all")

            # 4. Disconnect Dino Review and Quỳnh Béo -> dropdown updates
            conn_service.disconnect_central_page("104934391155107")
            auto_page.refresh_pages()
            self.assertEqual(
                [auto_page.page.itemData(i)[1] for i in range(1, auto_page.page.count())],
                ["1458043644054753"],
            )
            self.assertEqual(auto_page.page.currentData()[1], "1458043644054753")

            conn_service.disconnect_central_page("1458043644054753")
            auto_page.refresh_pages()
            self.assertEqual(auto_page.page.count(), 1)  # Only "Chọn Fanpage"
            self.assertIsNone(auto_page.page.currentData())

            # 5. Reconnect -> automatically refreshes and shows Quỳnh Béo again
            conn_service.connect_central("user-token")
            conn_service.test_central()
            auto_page.refresh_pages()
            self.assertIn(
                "1458043644054753",
                [auto_page.page.itemData(i)[1] for i in range(1, auto_page.page.count())],
            )
            auto_page.close()


if __name__ == "__main__":
    unittest.main()

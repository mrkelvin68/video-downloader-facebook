from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database.connection import Database
from app.database.migrations import migrate
from app.services.auto_comment_affiliate_link_service import (
    AffiliateLinkError,
    AutoCommentAffiliateLinkService,
    normalize_affiliate_url_key,
    validate_affiliate_url,
)
from app.services.auto_comment_template_service import AutoCommentTemplateService


class AffiliateLinkTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db_path = Path(path)
        self.db = Database(self.db_path)
        self.service = AutoCommentAffiliateLinkService(self.db)
        self.project_id = "proj-1"
        self.page_id = "page-1"

    def tearDown(self):
        self.db.close()
        self.db_path.unlink(missing_ok=True)

    # --- Migration (1-6) ---

    def test_01_fresh_database_migration_creates_table_and_indexes(self):
        columns = {
            row["name"]
            for row in self.db.connection.execute(
                "PRAGMA table_info(auto_comment_affiliate_links)"
            ).fetchall()
        }
        self.assertEqual(
            columns,
            {
                "id",
                "project_id",
                "page_id",
                "name",
                "url",
                "description",
                "enabled",
                "created_at",
                "updated_at",
            },
        )
        indexes = {
            row["name"]
            for row in self.db.connection.execute(
                "PRAGMA index_list(auto_comment_affiliate_links)"
            ).fetchall()
        }
        self.assertIn("ix_auto_comment_affiliate_links_scope", indexes)
        self.assertIn("ux_auto_comment_affiliate_links_url", indexes)
        self.assertIn("ix_auto_comment_affiliate_links_name", indexes)

    def test_02_existing_database_upgrade_migration_adds_table_safely(self):
        self.db.connection.execute("DROP TABLE auto_comment_affiliate_links")
        self.db.connection.commit()
        tables_before = {
            row["name"]
            for row in self.db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertNotIn("auto_comment_affiliate_links", tables_before)

        migrate(self.db.connection)
        tables_after = {
            row["name"]
            for row in self.db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("auto_comment_affiliate_links", tables_after)

    def test_03_idempotent_repeated_migration_runs(self):
        link_id = self.service.create_link(
            self.project_id,
            self.page_id,
            "Áo khoác nỉ",
            "https://s.shopee.vn/7fAbCdEf12",
            "Freeship",
        )
        migrate(self.db.connection)
        migrate(self.db.connection)
        loaded = self.service.get_link(self.project_id, self.page_id, link_id)
        self.assertEqual(loaded["name"], "Áo khoác nỉ")
        self.assertEqual(loaded["url"], "https://s.shopee.vn/7fAbCdEf12")

    def test_04_existing_auto_comment_templates_preserved(self):
        template_service = AutoCommentTemplateService(self.db)
        tid = template_service.create_template(
            self.project_id,
            self.page_id,
            "Mẫu săn sale",
            "Mua ngay tại {affiliate_url}",
            "Sale",
        )
        migrate(self.db.connection)
        templates = template_service.list_templates(self.project_id, self.page_id)
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]["id"], tid)
        self.assertEqual(templates[0]["body_text"], "Mua ngay tại {affiliate_url}")

    def test_05_existing_comment_library_rows_preserved(self):
        with self.db.connection:
            self.db.connection.execute(
                "INSERT INTO auto_comment_libraries VALUES (?,?,?,?)",
                ("lib-1", "Thư viện mặc định", "2026-11-14T10:00:00Z", "2026-11-14T10:00:00Z"),
            )
            self.db.connection.execute(
                "INSERT INTO auto_comment_library_items VALUES (?,?,?,?,?,?,?,?)",
                (
                    "item-1",
                    "lib-1",
                    "row-1",
                    "Comment cố định https://s.shopee.vn/legacy123",
                    1,
                    0,
                    "2026-11-14T10:00:00Z",
                    "2026-11-14T10:00:00Z",
                ),
            )
        migrate(self.db.connection)
        item = self.db.connection.execute(
            "SELECT * FROM auto_comment_library_items WHERE id='item-1'"
        ).fetchone()
        self.assertIsNotNone(item)
        self.assertEqual(item["comment_text"], "Comment cố định https://s.shopee.vn/legacy123")
        # Legacy comment library items are never automatically backfilled into affiliate links
        self.assertEqual(self.service.list_links(self.project_id, self.page_id), [])

    def test_06_existing_campaigns_and_history_preserved(self):
        with self.db.connection:
            self.db.connection.execute(
                "INSERT INTO auto_comment_campaigns VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "camp-1",
                    "Chiến dịch tháng 11",
                    self.page_id,
                    "2026-11-01",
                    "2026-11-14",
                    "ALL",
                    "RANDOM",
                    1,
                    1,
                    "DRAFT",
                    "2026-11-14T10:00:00Z",
                    "2026-11-14T10:00:00Z",
                ),
            )
            self.db.connection.execute(
                "INSERT INTO auto_comment_submission_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "att-1",
                    "op-1",
                    self.project_id,
                    self.page_id,
                    "src-1",
                    "tgt-1",
                    "Bình luận đã gửi",
                    "remote-1",
                    "PUBLISHED",
                    None,
                    None,
                    "VERIFIED",
                    "2026-11-14T10:00:00Z",
                    "2026-11-14T10:00:00Z",
                ),
            )
        migrate(self.db.connection)
        camp = self.db.connection.execute(
            "SELECT * FROM auto_comment_campaigns WHERE id='camp-1'"
        ).fetchone()
        attempt = self.db.connection.execute(
            "SELECT * FROM auto_comment_submission_attempts WHERE id='att-1'"
        ).fetchone()
        self.assertEqual(camp["name"], "Chiến dịch tháng 11")
        self.assertEqual(attempt["comment_text"], "Bình luận đã gửi")

    # --- CRUD (7-13) ---

    def test_07_create_affiliate_link(self):
        link_id = self.service.create_link(
            self.project_id,
            self.page_id,
            "Tai nghe Bluetooth",
            "https://s.shopee.vn/9zXyWvUt88",
            "Chống ồn chủ động",
            enabled=True,
        )
        self.assertTrue(link_id)
        item = self.service.get_link(self.project_id, self.page_id, link_id)
        self.assertEqual(item["name"], "Tai nghe Bluetooth")
        self.assertEqual(item["url"], "https://s.shopee.vn/9zXyWvUt88")
        self.assertEqual(item["description"], "Chống ồn chủ động")
        self.assertEqual(item["enabled"], 1)

    def test_08_list_affiliate_links_with_search_and_enabled_filter(self):
        self.service.create_link(
            self.project_id,
            self.page_id,
            "Bàn phím cơ",
            "https://s.shopee.vn/kb001",
            "Switch đỏ êm ái",
            enabled=True,
        )
        self.service.create_link(
            self.project_id,
            self.page_id,
            "Chuột không dây",
            "https://s.shopee.vn/mouse002",
            "Pin sạc Type-C",
            enabled=False,
        )
        all_links = self.service.list_links(self.project_id, self.page_id)
        self.assertEqual(len(all_links), 2)

        enabled_links = self.service.list_links(self.project_id, self.page_id, enabled_only=True)
        self.assertEqual(len(enabled_links), 1)
        self.assertEqual(enabled_links[0]["name"], "Bàn phím cơ")

        by_name = self.service.list_links(self.project_id, self.page_id, search="chuột")
        self.assertEqual(len(by_name), 1)
        self.assertEqual(by_name[0]["url"], "https://s.shopee.vn/mouse002")

        by_desc = self.service.list_links(self.project_id, self.page_id, search="switch đỏ")
        self.assertEqual(len(by_desc), 1)
        self.assertEqual(by_desc[0]["name"], "Bàn phím cơ")

        by_url = self.service.list_links(self.project_id, self.page_id, search="mouse002")
        self.assertEqual(len(by_url), 1)

    def test_09_get_affiliate_link(self):
        link_id = self.service.create_link(
            self.project_id,
            self.page_id,
            "Sạc dự phòng 20000mAh",
            "https://shopee.vn/product/12345/67890",
        )
        got = self.service.get_link(self.project_id, self.page_id, link_id)
        self.assertEqual(got["id"], link_id)
        self.assertIsNone(got["description"])
        with self.assertRaises(AffiliateLinkError):
            self.service.get_link(self.project_id, self.page_id, "non-existent")

    def test_10_update_affiliate_link(self):
        link_id = self.service.create_link(
            self.project_id,
            self.page_id,
            "Áo thun trơn",
            "https://s.shopee.vn/shirt1",
            "Cotton 100%",
        )
        self.service.update_link(
            self.project_id,
            self.page_id,
            link_id,
            "Áo thun oversize",
            "https://s.shopee.vn/shirt2",
            "Cotton 2 chiều dày dặn",
            enabled=False,
        )
        updated = self.service.get_link(self.project_id, self.page_id, link_id)
        self.assertEqual(updated["name"], "Áo thun oversize")
        self.assertEqual(updated["url"], "https://s.shopee.vn/shirt2")
        self.assertEqual(updated["description"], "Cotton 2 chiều dày dặn")
        self.assertEqual(updated["enabled"], 0)

    def test_11_delete_affiliate_link(self):
        link_id = self.service.create_link(
            self.project_id,
            self.page_id,
            "Túi đeo chéo",
            "https://s.shopee.vn/bag99",
        )
        self.service.delete_link(self.project_id, self.page_id, link_id)
        self.assertEqual(self.service.list_links(self.project_id, self.page_id), [])
        with self.assertRaises(AffiliateLinkError):
            self.service.get_link(self.project_id, self.page_id, link_id)

    def test_12_enable_disable_affiliate_link(self):
        link_id = self.service.create_link(
            self.project_id,
            self.page_id,
            "Đèn bàn học",
            "https://s.shopee.vn/lamp01",
            enabled=True,
        )
        self.service.set_link_enabled(self.project_id, self.page_id, link_id, False)
        self.assertEqual(self.service.get_link(self.project_id, self.page_id, link_id)["enabled"], 0)
        self.service.set_link_enabled(self.project_id, self.page_id, link_id, True)
        self.assertEqual(self.service.get_link(self.project_id, self.page_id, link_id)["enabled"], 1)

    def test_13_persistence_across_database_reopen(self):
        link_id = self.service.create_link(
            self.project_id,
            self.page_id,
            "Bình giữ nhiệt",
            "https://s.shopee.vn/FlaskAbc_123?utm_source=fb&sub_id=Reels01",
            "Giữ đá 24h",
        )
        self.db.close()

        reopened_db = Database(self.db_path)
        try:
            reopened_service = AutoCommentAffiliateLinkService(reopened_db)
            loaded = reopened_service.get_link(self.project_id, self.page_id, link_id)
            self.assertEqual(loaded["name"], "Bình giữ nhiệt")
            self.assertEqual(
                loaded["url"],
                "https://s.shopee.vn/FlaskAbc_123?utm_source=fb&sub_id=Reels01",
            )
            self.assertEqual(loaded["description"], "Giữ đá 24h")
        finally:
            self.db = reopened_db

    # --- Validation (14-24) ---

    def test_14_valid_shopee_https_short_or_product_url(self):
        for url in (
            "https://s.shopee.vn/4L2xYzAbCd",
            "https://shope.ee/8pQrStUvWx",
            "https://shopee.vn/Áo-Khoác-i.123456.7890123?sp_atk=abc-123",
        ):
            self.assertEqual(validate_affiliate_url(url), url)

    def test_15_valid_ordinary_https_url(self):
        url = "https://www.lazada.vn/products/tai-nghe-i123456.html?aff_sub=reels"
        self.assertEqual(validate_affiliate_url(url), url)

    def test_16_empty_url_rejected(self):
        for bad_url in ("", "   ", None):
            with self.assertRaises(AffiliateLinkError):
                self.service.create_link(self.project_id, self.page_id, "Sản phẩm A", bad_url)  # type: ignore[arg-type]

    def test_17_empty_name_rejected(self):
        for bad_name in ("", "   ", "\t\n"):
            with self.assertRaises(AffiliateLinkError):
                self.service.create_link(
                    self.project_id,
                    self.page_id,
                    bad_name,
                    "https://s.shopee.vn/valid123",
                )

    def test_18_invalid_schemes_rejected(self):
        for bad_url in (
            "http://s.shopee.vn/abc123",
            "javascript:alert(1)",
            "file:///C:/Windows/System32/cmd.exe",
            "data:text/html;base64,PHNjcmlwdD4=",
            "ftp://files.example.com/item.zip",
            "s.shopee.vn/abc123",
        ):
            with self.assertRaises(AffiliateLinkError, msg=f"Should reject {bad_url}"):
                self.service.create_link(self.project_id, self.page_id, "Link lỗi", bad_url)

    def test_19_invalid_hostnames_rejected(self):
        for bad_url in (
            "https:///path-only",
            "https://singlelabel/item",
            "https://.shopee.vn/item",
            "https://shopee..vn/item",
            "https://shopee.vn./item",
            "https://-bad.com/item",
            "https://bad-.com/item",
            "https://shopee.123/item",
            "https://shopee.vn:0/item",
            "https://shopee.vn:99999/item",
        ):
            with self.assertRaises(AffiliateLinkError, msg=f"Should reject {bad_url}"):
                validate_affiliate_url(bad_url)

    def test_20_embedded_credentials_rejected(self):
        for bad_url in (
            "https://user:pass@s.shopee.vn/abc",
            "https://admin@s.shopee.vn/abc",
            "https://:secret@shopee.vn/item",
        ):
            with self.assertRaises(AffiliateLinkError, msg=f"Should reject {bad_url}"):
                validate_affiliate_url(bad_url)

    def test_21_whitespace_and_control_characters_rejected(self):
        for bad_url in (
            " https://s.shopee.vn/abc",
            "https://s.shopee.vn/abc ",
            "https://s.shopee.vn/ab c",
            "https://s.shopee.vn/abc\n",
            "https://s.shopee.vn/abc\r",
            "https://s.shopee.vn/abc\t",
            "https://s.shopee.vn/abc\x00",
            "https://s.shopee.vn/abc\x7f",
            "https://s.shopee.vn\\abc",
        ):
            with self.assertRaises(AffiliateLinkError, msg=f"Should reject {bad_url!r}"):
                validate_affiliate_url(bad_url)

    def test_22_local_and_private_network_addresses_rejected(self):
        for bad_url in (
            "https://localhost/item",
            "https://localhost:8443/item",
            "https://shop.local/item",
            "https://api.internal/item",
            "https://dev.localhost/item",
            "https://127.0.0.1/item",
            "https://10.0.0.1/item",
            "https://172.16.0.5/item",
            "https://192.168.1.100/item",
            "https://169.254.169.254/latest/meta-data",
            "https://0.0.0.0/item",
            "https://[::1]/item",
            "https://[fe80::1]/item",
            "https://8.8.8.8/item",
        ):
            with self.assertRaises(AffiliateLinkError, msg=f"Should reject {bad_url}"):
                validate_affiliate_url(bad_url)

    def test_23_exact_original_url_preserved_on_save(self):
        raw_url = "https://S.Shopee.VN/AbCdEfGh_99?Sub_ID=Reel_01&utm_campaign=Sale11_11#TopSection"
        link_id = self.service.create_link(
            self.project_id,
            self.page_id,
            "Giày chạy bộ",
            raw_url,
        )
        saved = self.service.get_link(self.project_id, self.page_id, link_id)
        self.assertEqual(saved["url"], raw_url)

    def test_24_affiliate_query_string_preserved_intact(self):
        raw_url = (
            "https://shopee.vn/universal-link/product/111/222"
            "?gads_t_sig=VTJGc2RHVmtYMTlxTFVSVVRrdENkWVp3"
            "&uls_trackid=51abcdef0123"
            "&utm_content=----Reels_Fanpage_A"
            "&utm_medium=affiliates"
            "&utm_source=an_17300000000"
        )
        link_id = self.service.create_link(
            self.project_id,
            self.page_id,
            " Kem chống nắng ",
            raw_url,
        )
        saved = self.service.get_link(self.project_id, self.page_id, link_id)
        self.assertEqual(saved["name"], "Kem chống nắng")
        self.assertEqual(saved["url"], raw_url)

    # --- Duplicates (25-29) ---

    def test_25_exact_duplicate_url_in_same_page_rejected(self):
        url = "https://s.shopee.vn/dupExact01"
        self.service.create_link(self.project_id, self.page_id, "Link 1", url)
        with self.assertRaises(AffiliateLinkError):
            self.service.create_link(self.project_id, self.page_id, "Link 2", url)

    def test_26_scheme_and_hostname_case_duplicate_rejected(self):
        self.service.create_link(
            self.project_id,
            self.page_id,
            "Link gốc",
            "https://s.shopee.vn/CaseToken123?aff=AbC",
        )
        with self.assertRaises(AffiliateLinkError):
            self.service.create_link(
                self.project_id,
                self.page_id,
                "Link trùng hoa thường domain",
                "HTTPS://S.SHOPEE.VN/CaseToken123?aff=AbC",
            )
        # Path case difference on short links is NOT collapsed because short-link tokens are case-sensitive
        distinct_token_id = self.service.create_link(
            self.project_id,
            self.page_id,
            "Link khác token",
            "https://s.shopee.vn/casetoken123?aff=AbC",
        )
        self.assertTrue(distinct_token_id)

    def test_27_different_affiliate_parameters_are_not_collapsed(self):
        id1 = self.service.create_link(
            self.project_id,
            self.page_id,
            "Sub ID 1",
            "https://shopee.vn/product/10/20?utm_source=aff&sub_id=campA",
        )
        id2 = self.service.create_link(
            self.project_id,
            self.page_id,
            "Sub ID 2",
            "https://shopee.vn/product/10/20?utm_source=aff&sub_id=campB",
        )
        self.assertNotEqual(id1, id2)
        self.assertEqual(len(self.service.list_links(self.project_id, self.page_id)), 2)

    def test_28_same_url_on_another_page_is_allowed(self):
        url = "https://s.shopee.vn/sharedAcrossPages"
        id_page1 = self.service.create_link(self.project_id, "page-1", "SP Page 1", url)
        id_page2 = self.service.create_link(self.project_id, "page-2", "SP Page 2", url)
        self.assertNotEqual(id_page1, id_page2)

    def test_29_different_short_links_kept_distinct(self):
        id1 = self.service.create_link(
            self.project_id,
            self.page_id,
            "Short 1",
            "https://s.shopee.vn/shortA1",
        )
        id2 = self.service.create_link(
            self.project_id,
            self.page_id,
            "Short 2",
            "https://s.shopee.vn/shortB2",
        )
        self.assertNotEqual(id1, id2)

    # --- Ownership (30-35) ---

    def test_30_same_project_different_pages_isolated(self):
        self.service.create_link("proj-1", "page-A", "Link A", "https://s.shopee.vn/pageA")
        self.service.create_link("proj-1", "page-B", "Link B", "https://s.shopee.vn/pageB")
        links_a = self.service.list_links("proj-1", "page-A")
        links_b = self.service.list_links("proj-1", "page-B")
        self.assertEqual([x["name"] for x in links_a], ["Link A"])
        self.assertEqual([x["name"] for x in links_b], ["Link B"])

    def test_31_different_projects_isolated(self):
        self.service.create_link("proj-1", "page-1", "Link P1", "https://s.shopee.vn/proj1")
        self.service.create_link("proj-2", "page-1", "Link P2", "https://s.shopee.vn/proj1")
        self.assertEqual([x["name"] for x in self.service.list_links("proj-1", "page-1")], ["Link P1"])
        self.assertEqual([x["name"] for x in self.service.list_links("proj-2", "page-1")], ["Link P2"])

    def test_32_wrong_page_get_rejected(self):
        link_id = self.service.create_link("proj-1", "page-1", "SP", "https://s.shopee.vn/get1")
        with self.assertRaises(AffiliateLinkError):
            self.service.get_link("proj-1", "page-2", link_id)
        with self.assertRaises(AffiliateLinkError):
            self.service.get_link("proj-2", "page-1", link_id)

    def test_33_wrong_page_update_rejected(self):
        link_id = self.service.create_link("proj-1", "page-1", "SP", "https://s.shopee.vn/upd1")
        with self.assertRaises(AffiliateLinkError):
            self.service.update_link(
                "proj-1",
                "page-2",
                link_id,
                "Bị sửa trái phép",
                "https://s.shopee.vn/upd2",
            )
        original = self.service.get_link("proj-1", "page-1", link_id)
        self.assertEqual(original["name"], "SP")
        self.assertEqual(original["url"], "https://s.shopee.vn/upd1")

    def test_34_wrong_page_delete_rejected(self):
        link_id = self.service.create_link("proj-1", "page-1", "SP", "https://s.shopee.vn/del1")
        with self.assertRaises(AffiliateLinkError):
            self.service.delete_link("proj-1", "page-2", link_id)
        self.assertEqual(len(self.service.list_links("proj-1", "page-1")), 1)

    def test_35_wrong_page_enable_disable_rejected(self):
        link_id = self.service.create_link(
            "proj-1",
            "page-1",
            "SP",
            "https://s.shopee.vn/toggle1",
            enabled=True,
        )
        with self.assertRaises(AffiliateLinkError):
            self.service.set_link_enabled("proj-1", "page-2", link_id, False)
        self.assertEqual(self.service.get_link("proj-1", "page-1", link_id)["enabled"], 1)


if __name__ == "__main__":
    unittest.main()

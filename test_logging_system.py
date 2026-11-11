from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings

from app.services.credential_service import (
    get_apify_token,
    get_meta_token,
    set_apify_token,
    set_meta_token,
)
from app.services.logging_service import (
    DEFAULT_BACKUP_COUNT,
    LoggingConfig,
    LoggingService,
    get_default_log_dir,
    log_api_call,
    log_app,
    log_download,
    log_error,
    log_publishing,
    redactor,
    validate_log_dir_writable,
)


class LoggingSystemTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="vdf_test_logs_")
        self.temp_log_dir = Path(self.temp_dir)
        self.service = LoggingService()
        cfg = LoggingConfig(
            enabled=True,
            level="DEBUG",
            directory=self.temp_log_dir,
            max_bytes=10 * 1024 * 1024,
            backup_count=DEFAULT_BACKUP_COUNT,
            retention_days=30,
        )
        self.service.initialize(cfg)

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_1_default_log_dir_outside_repo(self):
        default_dir = get_default_log_dir()
        repo_root = Path(__file__).resolve().parent

        self.assertNotIn(repo_root, default_dir.parents)
        self.assertNotEqual(default_dir, repo_root)
        self.assertIn("VideoDownloaderFacebook", str(default_dir))
        self.assertIn("logs", str(default_dir))

    def test_2_log_files_created_automatically(self):
        self.service.get_logger("app").info("Testing app log")
        self.service.get_logger("api").info("Testing api log")
        self.service.get_logger("download").info("Testing download log")
        self.service.get_logger("publishing").info("Testing publishing log")
        self.service.get_logger("error").error("Testing error log")

        for cat in ("app", "api", "download", "publishing", "error"):
            log_file = self.temp_log_dir / f"{cat}.log"
            self.assertTrue(log_file.is_file(), f"{cat}.log must exist")
            content = log_file.read_text(encoding="utf-8")
            self.assertIn(f"Testing {cat} log", content)

    def test_3_utf8_vietnamese_preserved(self):
        vietnamese_msg = (
            "Đang tải video: Full | Trọng Sinh, Tôi Bỏ Nhà Sau 4 Năm Bị Hắt Hủi, Đến Khi Thành Công Họ Mới Hối Hận!"
        )
        self.service.get_logger("download").info(vietnamese_msg)

        content = (self.temp_log_dir / "download.log").read_text(encoding="utf-8")
        self.assertIn(vietnamese_msg, content)

    def test_4_api_diagnostics_formatting(self):
        with patch("app.services.logging_service.logging_service", self.service):
            log_api_call(
                provider="meta_graph",
                method="POST",
                endpoint="/v26.0/123456789/videos?access_token=EAAtest1234567890abcdef12345",
                status=200,
                duration_ms=250.5,
                correlation_id="cid-xyz-789",
                job_id="job-abc-123",
                retry_count=1,
            )

        content = (self.temp_log_dir / "api.log").read_text(encoding="utf-8")
        self.assertIn("provider=meta_graph", content)
        self.assertIn("method=POST", content)
        self.assertIn("status=200", content)
        self.assertIn("duration=250.5ms", content)
        self.assertIn("cid=cid-xyz-789", content)
        self.assertIn("job_id=job-abc-123", content)
        self.assertIn("retry=1", content)
        self.assertNotIn("EAAtest1234567890abcdef12345", content)
        self.assertIn("[REDACTED", content)

    def test_5_sensitive_values_redacted(self):
        raw_fb_token = "EAAB1234567890abcdef1234567890xyz"
        raw_apify_token = "apify_api_abcdef1234567890abcdef12345"
        raw_secret = "super_secret_user_password_99"

        redactor.add(raw_secret)

        with patch("app.services.logging_service.logging_service", self.service):
            log_app("Calling API with header: Authorization: Bearer secret_bearer_token_xyz")
            log_publishing(f"Publishing with token {raw_fb_token}")
            log_app(f"Connecting to Apify with key: {raw_apify_token}")
            log_app(f"User authentication with: {raw_secret}")
            log_download("Using cookies: c_user=10001234; xs=secr3txs; --cookies-from-browser chrome")
            try:
                raise ValueError(f"Failed to fetch media using token={raw_fb_token}")
            except Exception as exc:
                log_error("Exception occurred during fetch", exc=exc)

        all_logs = "".join(
            (self.temp_log_dir / f"{c}.log").read_text(encoding="utf-8")
            for c in ("app", "publishing", "error", "download")
            if (self.temp_log_dir / f"{c}.log").is_file()
        )

        self.assertNotIn(raw_fb_token, all_logs)
        self.assertNotIn(raw_apify_token, all_logs)
        self.assertNotIn(raw_secret, all_logs)
        self.assertNotIn("secret_bearer_token_xyz", all_logs)
        self.assertNotIn("10001234", all_logs)
        self.assertNotIn("secr3txs", all_logs)

    def test_6_worker_exceptions_captured(self):
        with patch("app.services.logging_service.logging_service", self.service):
            try:
                raise RuntimeError("Fatal worker failure in background thread")
            except RuntimeError as exc:
                log_error("Worker crashed", exc=exc)

        err_content = (self.temp_log_dir / "error.log").read_text(encoding="utf-8")
        self.assertIn("Fatal worker failure in background thread", err_content)
        self.assertIn("Traceback (most recent call last)", err_content)

    def test_7_log_rotation_works(self):
        cfg = LoggingConfig(
            enabled=True,
            level="DEBUG",
            directory=self.temp_log_dir,
            max_bytes=1024,
            backup_count=3,
            retention_days=30,
        )
        self.service.initialize(cfg)

        logger = self.service.get_logger("app")
        for i in range(100):
            logger.info(f"Writing rotation line {i:04d} with extra padding text to exceed byte limits.")

        for h in list(self.service.handlers.values()):
            h.flush()

        app_files = list(self.temp_log_dir.glob("app.log*"))
        self.assertGreater(len(app_files), 1)

    def test_8_settings_survive_restart(self):
        settings = QSettings("VideoDownloader", "VideoDownloaderFacebook")
        settings.setValue("logging/enabled", False)
        settings.setValue("logging/level", "WARNING")
        settings.setValue("logging/directory", str(self.temp_log_dir))
        settings.setValue("logging/max_size_mb", 20)
        settings.setValue("logging/retention_days", 14)
        settings.sync()

        service = LoggingService()
        service.initialize()

        self.assertFalse(service.config.enabled)
        self.assertEqual(service.config.level, "WARNING")
        self.assertEqual(service.config.directory.resolve(), self.temp_log_dir.resolve())
        self.assertEqual(service.config.max_bytes, 20 * 1024 * 1024)
        self.assertEqual(service.config.retention_days, 14)

        for h in list(service.handlers.values()):
            h.close()
        settings.remove("logging")
        settings.sync()

    def test_9_custom_directory_selection(self):
        custom_dir = self.temp_log_dir / "custom_subdir"
        ok, err = self.service.set_log_dir(custom_dir)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(self.service.get_log_dir().resolve(), custom_dir.resolve())
        self.assertTrue(custom_dir.is_dir())

        self.service.get_logger("app").info("Testing log in custom dir")
        for h in list(self.service.handlers.values()):
            h.close()

        self.assertTrue((custom_dir / "app.log").is_file())

    def test_10_invalid_custom_directory_fallback(self):
        invalid_path = Path("Z:\\non_existent_drive_12345\\impossible\\logs")
        is_writable, err = validate_log_dir_writable(invalid_path)
        self.assertFalse(is_writable)
        self.assertIsNotNone(err)

        service = LoggingService()
        init_dir = service.initialize(LoggingConfig(directory=invalid_path))
        self.assertEqual(init_dir, get_default_log_dir())
        for h in list(service.handlers.values()):
            h.close()

    def test_11_zip_export_sanitizes_content(self):
        secret_token = "EAABsuper_secret_token_1234567890123"
        self.service.get_logger("app").info(f"Token is {secret_token}")
        for h in list(self.service.handlers.values()):
            h.flush()

        zip_file = self.temp_log_dir / "exported_logs.zip"
        exported_count = self.service.export_logs_zip(zip_file)
        self.assertGreater(exported_count, 0)
        self.assertTrue(zip_file.is_file())

        with zipfile.ZipFile(zip_file, "r") as zf:
            namelist = zf.namelist()
            self.assertIn("app.log", namelist)
            content = zf.read("app.log").decode("utf-8")
            self.assertNotIn(secret_token, content)
            self.assertIn("[REDACTED", content)

    def test_12_cleanup_never_deletes_unrelated_files(self):
        user_file = self.temp_log_dir / "my_precious_data.csv"
        user_file.write_text("id,title\n1,Video 1", encoding="utf-8")

        user_subfolder = self.temp_log_dir / "my_project_folder"
        user_subfolder.mkdir()
        (user_subfolder / "video.mp4").write_bytes(b"dummy")

        old_rotated_log = self.temp_log_dir / "app.log.1"
        old_rotated_log.write_text("old logs", encoding="utf-8")
        past_time = time.time() - (40 * 86400)
        os.utime(old_rotated_log, (past_time, past_time))

        deleted = self.service.clean_old_logs(max_days=30)
        self.assertGreaterEqual(deleted, 1)
        self.assertFalse(old_rotated_log.exists())
        self.assertTrue(user_file.is_file())
        self.assertTrue((user_subfolder / "video.mp4").is_file())

    def test_13_packaged_environment_compatibility(self):
        with patch.object(sys, "frozen", True, create=True):
            default_dir = get_default_log_dir()
            self.assertIn("VideoDownloaderFacebook", str(default_dir))
            self.assertIn("logs", str(default_dir))
            writable, _ = validate_log_dir_writable(default_dir)
            self.assertTrue(writable)

    def test_14_credential_service_registers_secrets(self):
        test_meta_token = "EAABtest_token_registered_automatically_999"
        set_meta_token(test_meta_token)
        self.assertIn(test_meta_token, redactor.secrets)

        test_apify_token = "apify_api_test_secret_registered_999"
        set_apify_token(test_apify_token)
        self.assertIn(test_apify_token, redactor.secrets)

    def test_15_settings_dialog_ui_controls(self):
        from PySide6.QtWidgets import QApplication
        from app.ui.dialogs.settings_dialog import SettingsDialog

        app = QApplication.instance() or QApplication([])

        dialog = SettingsDialog()
        self.assertEqual(dialog.tabs.count(), 2)
        self.assertEqual(dialog.tabs.tabText(0), "Xác thực tải video")
        self.assertEqual(dialog.tabs.tabText(1), "Quản lý Log")

        self.assertIsNotNone(dialog.log_enabled_cb)
        self.assertEqual(dialog.log_level_combo.count(), 4)
        self.assertNotEqual(dialog.log_dir_edit.text(), "")
        self.assertGreaterEqual(dialog.max_size_spin.value(), 1)
        self.assertGreaterEqual(dialog.retention_spin.value(), 1)
        self.assertIn("Dung lượng log hiện tại:", dialog.dir_size_label.text())
        dialog.close()


if __name__ == "__main__":
    unittest.main()

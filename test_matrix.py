import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

from app.database.connection import Database
from app.services.matrix_service import (
    DEFAULT_PAGES,
    DEFAULT_SLOTS,
    NO_REPEAT,
    REPEAT_BETWEEN_PAGES,
    MatrixService,
)
from app.services.project_service import ProjectService
from app.services.video_service import VideoService
from app.services.download_queue_service import DownloadQueueService
from app.ui.main_window import MainWindow
from app.ui.dialogs.matrix_configuration_dialog import MatrixConfigurationDialog


class MatrixServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "projects.sqlite3"
        self.database = Database(self.database_path)
        self.projects = ProjectService(self.database)
        self.project_id = self.projects.create(
            "Matrix Project", "matrix", str(self.root / "videos")
        )
        self.other_project_id = self.projects.create(
            "Other Project", "other", str(self.root / "other-videos")
        )
        self.videos = VideoService(self.database)
        self.service = MatrixService(self.database)

    def tearDown(self):
        self.database.close()
        self.temp.cleanup()

    def import_videos(self, count: int, project_id: str | None = None) -> None:
        project_id = project_id or self.project_id
        prefix = "other" if project_id == self.other_project_id else "matrix"
        rows = "\n".join(
            f"{number},Video {number},https://www.facebook.com/watch/?v={prefix}-{number}"
            for number in range(1, count + 1)
        )
        self.videos.import_csv(
            project_id, f"stt,ten_video,url\n{rows}\n"
        )

    def create_matrix(self, **kwargs) -> str:
        configuration = {"start_date": "2026-10-25", **kwargs}
        return self.service.create_matrix(
            self.project_id, "Schedule", **configuration
        )

    def test_default_configuration_creates_four_by_four_by_five_grid(self):
        self.import_videos(20)
        before = [
            row["download_status"]
            for row in self.database.list_videos(self.project_id)
        ]
        matrix_id = self.create_matrix()
        configuration = self.service.get_configuration(matrix_id, self.project_id)
        entries = self.service.list_entries(matrix_id, self.project_id)
        self.assertEqual(configuration["pages"], list(DEFAULT_PAGES))
        self.assertEqual(configuration["slots"], list(DEFAULT_SLOTS))
        self.assertEqual(configuration["day_count"], 5)
        self.assertEqual(len(entries), 4 * 4 * 5)
        assigned = [entry["video_id"] for entry in entries if entry["video_id"]]
        self.assertEqual(len(assigned), 20)
        self.assertEqual(len(set(assigned)), 20)
        self.assertTrue(all(entry["publish_status"] == "pending" for entry in entries))
        self.assertEqual(
            before,
            [row["download_status"] for row in self.database.list_videos(self.project_id)],
        )

    def test_multiple_matrices_and_project_isolation(self):
        first = self.create_matrix()
        second = self.service.create_matrix(
            self.project_id,
            "Second",
            pages=["Page A"],
            slots=["07:00"],
            start_date="2026-10-25",
            day_count=1,
        )
        other = self.service.create_matrix(
            self.other_project_id,
            "Other",
            pages=["Only other"],
            slots=["08:00"],
            start_date="2026-10-25",
            day_count=1,
        )
        self.assertEqual(len(self.service.list_matrices(self.project_id)), 2)
        self.assertEqual(len(self.service.list_matrices(self.other_project_id)), 1)
        self.assertNotEqual(first, second)
        with self.assertRaisesRegex(ValueError, "dự án này"):
            self.service.list_entries(other, self.project_id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.connection.execute(
                """INSERT INTO matrix_entries
                   (entry_id, matrix_id, project_id, page_id, video_id,
                    scheduled_date, scheduled_time, caption, publish_status,
                    created_at, updated_at)
                   VALUES ('bad-entry', ?, ?, ?, NULL, '2026-10-25', '09:00',
                           '', 'pending', 'now', 'now')""",
                (
                    first,
                    self.other_project_id,
                    self.service.list_pages(first, self.project_id)[0]["page_id"],
                ),
            )

    def test_completed_existing_mp4_is_prioritized_for_assignment(self):
        self.import_videos(2)
        videos = self.database.list_videos(self.project_id)
        completed_path = self.root / "completed.mp4"
        completed_path.write_bytes(b"media")
        self.database.connection.execute(
            """UPDATE videos SET download_status='completed', local_video_path=?
               WHERE id=?""",
            (str(completed_path), videos[1]["id"]),
        )
        self.database.connection.commit()
        matrix_id = self.create_matrix(
            pages=["Page 1"],
            slots=["05:00"],
            day_count=1,
        )
        entry = self.service.list_entries(matrix_id, self.project_id)[0]
        self.assertEqual(entry["video_id"], videos[1]["id"])

    def test_facebook_uploaded_status_persists_and_is_project_scoped(self):
        self.import_videos(1)
        video = self.service.list_project_videos(self.project_id)[0]
        self.assertFalse(self.videos.list_videos(self.project_id)[0].facebook_uploaded)
        self.videos.set_facebook_uploaded(self.project_id, video["id"], True)
        self.assertTrue(self.videos.list_videos(self.project_id)[0].facebook_uploaded)
        refreshed = self.service.list_project_videos(self.project_id)[0]
        self.assertEqual(refreshed["facebook_uploaded"], 1)

        with self.assertRaisesRegex(ValueError, "dự án này"):
            self.videos.set_facebook_uploaded(
                self.other_project_id, video["id"], False
            )

        self.database.close()
        self.database = Database(self.database_path)
        self.service = MatrixService(self.database)
        self.videos = VideoService(self.database)
        persisted = self.service.list_project_videos(self.project_id)[0]
        self.assertEqual(persisted["facebook_uploaded"], 1)
        self.videos.set_facebook_uploaded(self.project_id, video["id"], False)
        self.assertEqual(
            self.service.list_project_videos(self.project_id)[0]["facebook_uploaded"],
            0,
        )

    def test_video_code_title_and_thumbnail_can_be_updated_safely(self):
        self.import_videos(2)
        videos = self.videos.list_videos(self.project_id)
        video = videos[0]
        image_path = self.root / "custom-thumbnail.png"
        image = QImage(240, 120, QImage.Format.Format_RGB32)
        image.fill(QColor("#2563eb"))
        self.assertTrue(image.save(str(image_path)))

        self.videos.update_video_field(
            self.project_id, video.id, "video_code", "edited-code"
        )
        self.videos.update_video_field(
            self.project_id, video.id, "title", "Tên đã sửa"
        )
        self.videos.update_video_field(
            self.project_id, video.id, "thumbnail_path", str(image_path)
        )
        saved = self.database.connection.execute(
            """SELECT video_code, title, thumbnail_path FROM videos
               WHERE id=? AND project_id=?""",
            (video.id, self.project_id),
        ).fetchone()
        self.assertEqual(saved["video_code"], "edited-code")
        self.assertEqual(saved["title"], "Tên đã sửa")
        self.assertEqual(saved["thumbnail_path"], str(image_path))

        with self.assertRaisesRegex(ValueError, "không hợp lệ"):
            self.videos.update_video_field(
                self.project_id, video.id, "video_code", "../outside"
            )
        with self.assertRaisesRegex(ValueError, "không được để trống"):
            self.videos.update_video_field(
                self.project_id, video.id, "title", "  "
            )
        with self.assertRaisesRegex(ValueError, "dự án này"):
            self.videos.update_video_field(
                self.other_project_id, video.id, "title", "Không đúng dự án"
            )

    def test_video_code_cannot_change_during_download_or_duplicate(self):
        self.import_videos(2)
        videos = self.videos.list_videos(self.project_id)
        with self.assertRaisesRegex(ValueError, "đang tải"):
            self.database.connection.execute(
                "UPDATE videos SET download_status='downloading' WHERE id=?",
                (videos[0].id,),
            )
            self.database.connection.commit()
            self.videos.update_video_field(
                self.project_id, videos[0].id, "video_code", "active-edit"
            )

        self.database.connection.execute(
            "UPDATE videos SET download_status='pending' WHERE id=?",
            (videos[0].id,),
        )
        self.database.connection.commit()
        with self.assertRaisesRegex(ValueError, "đã được sử dụng"):
            self.videos.update_video_field(
                self.project_id,
                videos[0].id,
                "video_code",
                videos[1].video_code,
            )
        DownloadQueueService(self.database).enqueue(
            self.project_id, [videos[0].id]
        )
        with self.assertRaisesRegex(ValueError, "trong hàng đợi"):
            self.videos.update_video_field(
                self.project_id, videos[0].id, "video_code", "queued-edit"
            )

    def test_no_repeat_and_repeat_between_pages_are_deterministic(self):
        self.import_videos(2)
        no_repeat = self.create_matrix(
            pages=["Page 1", "Page 2"],
            slots=["05:00", "10:00"],
            day_count=1,
            repeat_mode=NO_REPEAT,
        )
        no_repeat_rows = self.service.list_entries(no_repeat, self.project_id)
        assigned = [entry["video_id"] for entry in no_repeat_rows if entry["video_id"]]
        self.assertEqual(len(assigned), 2)
        self.assertEqual(len(set(assigned)), 2)
        self.assertEqual(sum(entry["video_id"] is None for entry in no_repeat_rows), 2)

        repeat = self.create_matrix(
            pages=["Page 1", "Page 2"],
            slots=["05:00", "10:00"],
            day_count=2,
            repeat_mode=REPEAT_BETWEEN_PAGES,
        )
        rows = self.service.list_entries(repeat, self.project_id)
        first_slot = [
            entry for entry in rows
            if entry["scheduled_date"] == "2026-10-25"
            and entry["scheduled_time"] == "05:00"
        ]
        self.assertEqual(len(first_slot), 2)
        self.assertNotEqual(first_slot[0]["video_id"], first_slot[1]["video_id"])
        self.assertTrue(
            any(
                left["video_id"] == right["video_id"]
                and left["page_id"] != right["page_id"]
                for left in rows
                for right in rows
            )
        )
        per_page_day = {}
        for entry in rows:
            if entry["video_id"]:
                key = (entry["page_id"], entry["scheduled_date"])
                self.assertNotIn(entry["video_id"], per_page_day.setdefault(key, set()))
                per_page_day[key].add(entry["video_id"])

    def test_duplicate_page_slot_date_is_rejected_by_unique_constraint(self):
        matrix_id = self.create_matrix(
            pages=["Page"], slots=["05:00"], day_count=1
        )
        entry = self.service.list_entries(matrix_id, self.project_id)[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.connection.execute(
                """INSERT INTO matrix_entries
                   (entry_id, matrix_id, project_id, page_id, video_id,
                    scheduled_date, scheduled_time, caption, publish_status,
                    created_at, updated_at)
                   VALUES ('duplicate', ?, ?, ?, NULL, ?, ?, '', 'pending', 'now', 'now')""",
                (
                    matrix_id, self.project_id, entry["page_id"],
                    entry["scheduled_date"], entry["scheduled_time"],
                ),
            )

    def test_validation_rejects_duplicate_pages_slots_and_invalid_dates(self):
        invalid = (
            {"pages": ["Page", " page "]},
            {"slots": ["5:00"]},
            {"slots": ["05:00", "05:00"]},
            {"start_date": "2026-02-30"},
        )
        for values in invalid:
            with self.subTest(values=values):
                configuration = {
                    "pages": ["Page"],
                    "slots": ["05:00"],
                    "start_date": "2026-10-25",
                    "day_count": 1,
                    "repeat_mode": NO_REPEAT,
                }
                configuration.update(values)
                with self.assertRaises(ValueError):
                    self.create_matrix(**configuration)

    def test_manual_video_caption_and_publish_state_survive_regeneration(self):
        self.import_videos(4)
        matrix_id = self.create_matrix(
            pages=["Page"], slots=["05:00"], day_count=1
        )
        entry = self.service.list_entries(matrix_id, self.project_id)[0]
        video = self.database.list_videos(self.project_id)[2]
        self.service.update_entry(
            matrix_id, self.project_id, entry["entry_id"], video["id"], "Custom caption"
        )
        self.service.set_publish_status(
            matrix_id, self.project_id, entry["entry_id"], True
        )
        self.service.regenerate(matrix_id, self.project_id)
        updated = self.service.list_entries(matrix_id, self.project_id)[0]
        self.assertEqual(updated["video_id"], video["id"])
        self.assertEqual(updated["caption"], "Custom caption")
        self.assertEqual(updated["publish_status"], "posted")
        self.service.set_publish_status(
            matrix_id, self.project_id, entry["entry_id"], False
        )
        self.assertEqual(
            self.service.list_entries(matrix_id, self.project_id)[0]["publish_status"],
            "pending",
        )

    def test_manual_edit_invalidates_undo_to_prevent_lost_changes(self):
        self.import_videos(2)
        matrix_id = self.create_matrix(
            pages=["Page"], slots=["05:00"], day_count=1
        )
        self.service.regenerate(matrix_id, self.project_id)
        entry = self.service.list_entries(matrix_id, self.project_id)[0]
        self.assertTrue(self.service.can_undo(matrix_id, self.project_id))
        self.service.update_entry(
            matrix_id, self.project_id, entry["entry_id"], entry["video_id"], "Keep me"
        )
        self.assertFalse(self.service.can_undo(matrix_id, self.project_id))

    def test_manual_video_assignment_cannot_cross_project(self):
        self.import_videos(1, self.other_project_id)
        matrix_id = self.create_matrix(
            pages=["Page"], slots=["05:00"], day_count=1
        )
        entry = self.service.list_entries(matrix_id, self.project_id)[0]
        other_video = self.database.connection.execute(
            "SELECT id FROM videos WHERE project_id=?", (self.other_project_id,)
        ).fetchone()["id"]
        with self.assertRaisesRegex(ValueError, "không thuộc dự án"):
            self.service.update_entry(
                matrix_id, self.project_id, entry["entry_id"], other_video, "caption"
            )

    def test_manual_assignment_preserves_each_distribution_rule(self):
        self.import_videos(3)
        matrix_id = self.create_matrix(
            pages=["Page"], slots=["05:00", "10:00"], day_count=1
        )
        entries = self.service.list_entries(matrix_id, self.project_id)
        with self.assertRaisesRegex(ValueError, "đã được phân bổ"):
            self.service.update_entry(
                matrix_id, self.project_id, entries[1]["entry_id"],
                entries[0]["video_id"], "duplicate",
            )
        repeated_matrix = self.service.create_matrix(
            self.project_id, "Repeat", ["Page"], ["05:00", "10:00"],
            "2026-10-25", 1, REPEAT_BETWEEN_PAGES,
        )
        repeated = self.service.list_entries(repeated_matrix, self.project_id)
        with self.assertRaisesRegex(ValueError, "đã được phân bổ"):
            self.service.update_entry(
                repeated_matrix, self.project_id, repeated[1]["entry_id"],
                repeated[0]["video_id"], "duplicate on same page/day",
            )

    def test_video_replacement_nulls_matrix_reference_without_blocking_import(self):
        self.import_videos(1)
        matrix_id = self.create_matrix(
            pages=["Page"], slots=["05:00"], day_count=1
        )
        entry = self.service.list_entries(matrix_id, self.project_id)[0]
        self.assertIsNotNone(entry["video_id"])
        self.assertTrue(self.service.can_undo(matrix_id, self.project_id))
        self.videos.import_csv(
            self.project_id,
            "stt,ten_video,url\n2,Replacement,https://www.facebook.com/watch/?v=new\n",
            replace_confirmed=True,
        )
        refreshed = self.service.list_entries(matrix_id, self.project_id)[0]
        self.assertIsNone(refreshed["video_id"])
        self.assertEqual(refreshed["caption"], "Video 1")
        self.assertFalse(self.service.can_undo(matrix_id, self.project_id))
        self.assertEqual(len(self.database.list_videos(self.project_id)), 1)

    def test_regeneration_can_be_undone_and_restores_configuration(self):
        self.import_videos(3)
        matrix_id = self.create_matrix(
            pages=["Original"], slots=["05:00"], day_count=1
        )
        original_entry = self.service.list_entries(matrix_id, self.project_id)[0]
        videos = self.database.list_videos(self.project_id)
        self.service.update_entry(
            matrix_id, self.project_id, original_entry["entry_id"], videos[2]["id"], "before"
        )
        self.service.update_configuration(
            matrix_id, self.project_id, "Changed", ["New Page"], ["07:00"],
            "2026-10-26", 2, REPEAT_BETWEEN_PAGES,
        )
        self.assertEqual(len(self.service.list_entries(matrix_id, self.project_id)), 2)
        self.assertTrue(self.service.can_undo(matrix_id, self.project_id))
        self.service.undo_regeneration(matrix_id, self.project_id)
        configuration = self.service.get_configuration(matrix_id, self.project_id)
        restored = self.service.list_entries(matrix_id, self.project_id)[0]
        self.assertEqual(configuration["name"], "Schedule")
        self.assertEqual(configuration["pages"], ["Original"])
        self.assertEqual(configuration["slots"], ["05:00"])
        self.assertEqual(configuration["start_date"], "2026-10-25")
        self.assertEqual(restored["video_id"], videos[2]["id"])
        self.assertEqual(restored["caption"], "before")
        self.assertFalse(self.service.can_undo(matrix_id, self.project_id))

    def test_posted_entry_cannot_be_removed_by_configuration(self):
        self.import_videos(1)
        matrix_id = self.create_matrix(
            pages=["Page"], slots=["05:00"], day_count=1
        )
        entry = self.service.list_entries(matrix_id, self.project_id)[0]
        self.service.set_publish_status(
            matrix_id, self.project_id, entry["entry_id"], True
        )
        with self.assertRaisesRegex(ValueError, "Đã đăng"):
            self.service.update_configuration(
                matrix_id, self.project_id, "Schedule", ["Page"], ["07:00"],
                "2026-10-25", 1, NO_REPEAT,
            )
        self.service.update_configuration(
            matrix_id, self.project_id, "Schedule", ["Renamed Page"], ["05:00"],
            "2026-10-25", 1, NO_REPEAT,
        )
        retained = self.service.list_entries(matrix_id, self.project_id)[0]
        self.assertEqual(retained["entry_id"], entry["entry_id"])
        self.assertEqual(retained["page_name"], "Renamed Page")
        self.assertEqual(retained["publish_status"], "posted")
        self.assertEqual(retained["scheduled_time"], "05:00")
        self.assertEqual(
            retained["scheduled_time"],
            "05:00",
        )

    def test_matrix_persists_after_database_restart(self):
        self.import_videos(2)
        matrix_id = self.create_matrix(
            pages=["One", "Two"], slots=["07:00"], day_count=2
        )
        self.database.close()
        self.database = Database(self.database_path)
        self.service = MatrixService(self.database)
        self.assertEqual(
            self.service.get_matrix(matrix_id, self.project_id)["name"], "Schedule"
        )
        self.assertEqual(len(self.service.list_entries(matrix_id, self.project_id)), 4)

    def test_delete_matrix_keeps_project_videos_and_media(self):
        self.import_videos(2)
        video_path = self.root / "keep.mp4"
        video_path.write_bytes(b"keep")
        self.database.connection.execute(
            "UPDATE videos SET local_video_path=? WHERE project_id=?",
            (str(video_path), self.project_id),
        )
        self.database.connection.commit()
        matrix_id = self.create_matrix(
            pages=["Page"], slots=["05:00"], day_count=1
        )
        video_count = len(self.database.list_videos(self.project_id))
        self.service.delete_matrix(matrix_id, self.project_id)
        self.assertEqual(len(self.database.list_videos(self.project_id)), video_count)
        self.assertTrue(video_path.is_file())
        self.assertFalse(self.service.list_matrices(self.project_id))

    def test_matrix_csv_is_excel_compatible_and_contains_all_fields(self):
        self.import_videos(1)
        matrix_id = self.create_matrix(
            pages=["Page CSV"], slots=["06:30"], day_count=1
        )
        entry = self.service.list_entries(matrix_id, self.project_id)[0]
        video_path = self.root / "clip.mp4"
        thumbnail_path = self.root / "cover.jpg"
        video_path.write_bytes(b"media")
        thumbnail_path.write_bytes(b"image")
        video = self.database.list_videos(self.project_id)[0]
        self.database.connection.execute(
            """UPDATE videos SET local_video_path=?, thumbnail_path=?
               WHERE id=?""",
            (str(video_path), str(thumbnail_path), video["id"]),
        )
        self.database.connection.commit()
        self.service.update_entry(
            matrix_id, self.project_id, entry["entry_id"], video["id"], "CSV caption"
        )
        output = self.root / "matrix.csv"
        self.service.export_csv(matrix_id, self.project_id, output)
        raw = output.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        with output.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(
            list(rows[0]),
            [
                "Ngày", "Giờ", "Tên Page", "Mã video", "Title",
                "Đường dẫn video", "Thumbnail", "Caption", "Trạng thái",
            ],
        )
        self.assertEqual(rows[0]["Mã video"], "matrix-1")
        self.assertEqual(rows[0]["Title"], "Video 1")
        self.assertEqual(rows[0]["Đường dẫn video"], str(video_path))
        self.assertEqual(rows[0]["Thumbnail"], str(thumbnail_path))
        self.assertEqual(rows[0]["Caption"], "CSV caption")


class MatrixUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = Database(self.root / "ui.sqlite3")
        self.projects = ProjectService(self.database)
        self.project_id = self.projects.create(
            "Matrix UI", "ui", str(self.root / "ui-videos")
        )
        self.other_project_id = self.projects.create(
            "Another UI", "other", str(self.root / "other-videos")
        )
        csv_rows = "\n".join(
            f"{index},Video {index},https://www.facebook.com/watch/?v=ui-{index}"
            for index in range(1, 21)
        )
        VideoService(self.database).import_csv(
            self.project_id, f"stt,ten_video,url\n{csv_rows}\n"
        )
        self.service = MatrixService(self.database)
        self.matrix_id = self.service.create_matrix(
            self.project_id,
            "UI matrix",
            [f"Page {number}" for number in range(1, 9)],
            ["05:00", "10:00", "17:00", "22:00"],
            "2026-10-25",
            10,
        )
        self.window = MainWindow(self.database)

    def tearDown(self):
        self.window.close()
        self.database.close()
        self.temp.cleanup()

    def test_project_cards_show_matrix_counts_and_open_selected_project(self):
        projects = self.projects.list_projects()
        self.assertEqual(
            {project.uuid: project.matrix_count for project in projects},
            {self.project_id: 1, self.other_project_id: 0},
        )
        self.window.show_matrices(next(p for p in projects if p.uuid == self.project_id))
        self.assertEqual(self.window.pages.currentIndex(), 3)
        self.assertEqual(self.window.current_project.uuid, self.project_id)
        self.assertEqual(self.window.matrix_page.model.rowCount(), 70)
        self.assertEqual(self.window.matrix_page.model.columnCount(), 25)
        self.assertEqual(
            self.window.matrix_page.model.data(
                self.window.matrix_page.model.index(1, 1)
            ),
            "Page 1",
        )
        self.assertEqual(self.window.matrix_page.video_table.video_model.rowCount(), 20)

    def test_project_video_tab_only_shows_current_project_videos(self):
        VideoService(self.database).import_csv(
            self.other_project_id,
            "stt,ten_video,url\n1,Other project video,"
            "https://www.facebook.com/watch/?v=other-video\n",
        )
        self.window.show_matrices(
            next(
                project
                for project in self.projects.list_projects()
                if project.uuid == self.project_id
            )
        )
        page = self.window.matrix_page
        page.content_tabs.setCurrentWidget(page.video_list_view)
        self.assertEqual(page.video_table.video_model.rowCount(), 20)
        self.assertIn("20 video", page.video_count_label.text())

        self.window.show_matrices(
            next(
                project
                for project in self.projects.list_projects()
                if project.uuid == self.other_project_id
            )
        )
        self.assertEqual(page.video_table.video_model.rowCount(), 1)
        self.assertEqual(
            page.video_table.video_model.data(page.video_table.video_model.index(0, 1)),
            "other-1",
        )

    def test_video_actions_copy_path_and_title_and_toggle_facebook_status(self):
        project = next(
            project
            for project in self.projects.list_projects()
            if project.uuid == self.project_id
        )
        video = self.service.list_project_videos(self.project_id)[0]
        local_path = self.root / "video-1.mp4"
        local_path.write_bytes(b"synthetic test media")
        self.database.connection.execute(
            "UPDATE videos SET local_video_path=? WHERE id=?",
            (str(local_path), video["id"]),
        )
        self.database.connection.commit()

        self.window.show_matrices(project)
        page = self.window.matrix_page
        page.video_table.selectRow(0)
        page._update_video_actions()
        self.assertEqual(
            page.video_table.video_model.data(
                page.video_table.video_model.index(0, 9),
                Qt.ItemDataRole.ToolTipRole,
            ),
            str(local_path),
        )

        image_path = self.root / "small.png"
        image = QImage(240, 120, QImage.Format.Format_RGB32)
        image.fill(QColor("#2563eb"))
        self.assertTrue(image.save(str(image_path)))
        page.content_tabs.setCurrentWidget(page.video_list_view)
        self.window.show()
        self.application.processEvents()
        image_action = page.video_table.video_model.index(0, 3)
        page.video_table.scrollTo(image_action)
        self.application.processEvents()
        with patch(
            "app.ui.main_window.QFileDialog.getOpenFileName",
            return_value=(str(image_path), "PNG"),
        ):
            QTest.mouseClick(
                page.video_table.viewport(),
                Qt.MouseButton.LeftButton,
                pos=page.video_table.visualRect(image_action).center(),
            )
        self.assertEqual(
            self.database.list_videos(self.project_id)[0]["thumbnail_path"],
            str(image_path),
        )
        thumbnail = page.video_table.video_model.data(
            page.video_table.video_model.index(0, 2),
            Qt.ItemDataRole.DecorationRole,
        )
        self.assertEqual((thumbnail.width(), thumbnail.height()), (160, 80))

        path_action = page.video_table.video_model.index(0, 10)
        page.video_table.scrollTo(path_action)
        self.application.processEvents()
        QTest.mouseClick(
            page.video_table.viewport(),
            Qt.MouseButton.LeftButton,
            pos=page.video_table.visualRect(path_action).center(),
        )
        self.assertEqual(self.application.clipboard().text(), str(local_path))
        title_action = page.video_table.video_model.index(0, 5)
        page.video_table.scrollTo(title_action)
        self.application.processEvents()
        QTest.mouseClick(
            page.video_table.viewport(),
            Qt.MouseButton.LeftButton,
            pos=page.video_table.visualRect(title_action).center(),
        )
        self.assertEqual(self.application.clipboard().text(), "Video 1")
        self.assertTrue(
            page.video_table.video_model.flags(
                page.video_table.video_model.index(0, 1)
            )
            & Qt.ItemFlag.ItemIsEditable
        )
        self.assertTrue(
            page.video_table.video_model.setData(
                page.video_table.video_model.index(0, 1), "edited-1"
            )
        )
        self.assertTrue(
            page.video_table.video_model.setData(
                page.video_table.video_model.index(0, 4), "Edited video title"
            )
        )
        updated = self.database.list_videos(self.project_id)[0]
        self.assertEqual(updated["video_code"], "edited-1")
        self.assertEqual(updated["title"], "Edited video title")

        page.facebook_uploaded_button.click()
        self.assertEqual(
            self.service.list_project_videos(self.project_id)[0]["facebook_uploaded"],
            1,
        )
        self.assertEqual(
            page.facebook_uploaded_button.text(),
            "Đánh dấu chưa tải lên Facebook",
        )
        self.assertEqual(
            page.video_table.video_model.data(
                page.video_table.video_model.index(0, 8)
            ),
            "Đã tải lên Facebook",
        )

    def test_video_code_and_title_can_be_edited_from_the_table(self):
        project = next(
            project for project in self.projects.list_projects()
            if project.uuid == self.project_id
        )
        self.window.show_matrices(project)
        page = self.window.matrix_page
        page.content_tabs.setCurrentWidget(page.video_list_view)
        self.window.show()
        self.application.processEvents()

        for column, value, field in (
            (1, "inline-code", "video_code"),
            (4, "Inline title", "title"),
        ):
            index = page.video_table.video_model.index(0, column)
            page.video_table.scrollTo(index)
            self.application.processEvents()
            click_position = page.video_table.visualRect(index).center()
            QTest.mouseClick(
                page.video_table.viewport(),
                Qt.MouseButton.LeftButton,
                pos=click_position,
            )
            QTest.mouseDClick(
                page.video_table.viewport(),
                Qt.MouseButton.LeftButton,
                pos=click_position,
            )
            self.application.processEvents()
            editor = page.video_table.findChild(QLineEdit)
            self.assertIsNotNone(editor)
            editor.selectAll()
            QTest.keyClicks(editor, value)
            QTest.keyClick(editor, Qt.Key.Key_Return)
            self.application.processEvents()
            self.assertEqual(
                self.database.list_videos(self.project_id)[0][field], value
            )

    def test_tabs_switch_between_matrices_in_the_same_project(self):
        second_id = self.service.create_matrix(
            self.project_id,
            "Second matrix",
            ["Only page"],
            ["09:00"],
            "2026-10-25",
            1,
        )
        project = next(
            project for project in self.projects.list_projects()
            if project.uuid == self.project_id
        )
        self.window.show_matrices(project)
        tab_index = next(
            index
            for index in range(self.window.matrix_page.tabs.count())
            if self.window.matrix_page.tabs.tabData(index) == second_id
        )
        self.window.matrix_page.tabs.setCurrentIndex(tab_index)
        self.application.processEvents()
        self.assertEqual(self.window.current_matrix_id, second_id)
        self.assertEqual(self.window.matrix_page.model.columnCount(), 4)

    def test_configuration_dialog_has_defaults_and_list_controls(self):
        dialog = MatrixConfigurationDialog(self.window)
        self.assertEqual(dialog.page_names.toPlainText().splitlines(), list(DEFAULT_PAGES))
        self.assertEqual(dialog.slot_times.toPlainText().splitlines(), list(DEFAULT_SLOTS))
        self.assertEqual(dialog.day_count.value(), 5)
        buttons = dialog.findChildren(QPushButton)
        add_page = next(button for button in buttons if button.text() == "Thêm Page")
        add_page.click()
        self.assertEqual(len(dialog.page_names.toPlainText().splitlines()), 5)

    def test_matrix_grid_scrolls_and_renders_at_supported_window_sizes(self):
        project = next(
            project for project in self.projects.list_projects()
            if project.uuid == self.project_id
        )
        self.window.show_matrices(project)
        self.window.show()
        for size in ((1366, 768), (1920, 1080)):
            self.window.resize(*size)
            self.application.processEvents()
            self.assertEqual(self.window.size().width(), size[0])
            self.assertGreater(self.window.matrix_page.table.verticalScrollBar().maximum(), 0)
            self.assertGreater(self.window.matrix_page.table.horizontalScrollBar().maximum(), 0)
            self.assertEqual(self.window.matrix_page.model.rowCount(), 70)
        cards = self.window.project_page.findChildren(QPushButton, "secondary")
        self.assertTrue(any("Ma trận" in card.text() for card in cards))

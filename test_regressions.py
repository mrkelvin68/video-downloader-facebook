import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app import ProjectStore, parse_video_csv


def csv_with(rows, delimiter=","):
    return delimiter.join(("stt", "ten_video", "url")) + "\n" + "\n".join(
        delimiter.join(row) for row in rows
    )


class CsvRegressionTests(unittest.TestCase):
    def test_parser_package_import_does_not_load_pyside6(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from app.services.csv_service import parse_video_csv; "
                "assert 'PySide6' not in sys.modules",
            ],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_parser_returns_no_rows_when_any_row_is_invalid(self):
        text = csv_with(
            [
                ("1", "Valid", "https://www.facebook.com/watch/?v=1"),
                ("2", "Invalid", "https://example.com/video"),
            ]
        )
        rows, errors = parse_video_csv(text)
        self.assertEqual(rows, [])
        self.assertEqual(errors, ["Dòng 3: URL Facebook không hợp lệ"])

    def test_empty_header_only_and_wrong_column_count_are_reported(self):
        for text, expected in (
            ("", "CSV rỗng"),
            ("stt,ten_video,url\n", "không có dữ liệu"),
            (
                "stt,ten_video,url\n1,Video,https://www.facebook.com/a,extra\n",
                "Dòng 2: thừa cột",
            ),
            ("stt,ten_video,url\n1,Video\n", "Dòng 2: thiếu cột"),
        ):
            with self.subTest(text=text):
                _, errors = parse_video_csv(text)
                self.assertIn(expected, " ".join(errors))

    def test_semicolon_bom_quoted_fields_and_aliases(self):
        rows, errors = parse_video_csv(
            "\ufeffsequence;title;url\n"
            '1;"Một; video, tiếng Việt";https://www.facebook.com/a\n'
        )
        self.assertEqual(errors, [])
        self.assertEqual(
            rows,
            [
                {
                    "stt": 1,
                    "ten_video": "Một; video, tiếng Việt",
                    "url": "https://www.facebook.com/a",
                }
            ],
        )

    def test_title_and_url_are_imported_without_stt(self):
        rows, errors = parse_video_csv(
            "title,url\nVideo mẫu,https://www.facebook.com/watch/?v=1\n"
        )
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["stt"], 1)

    def test_excel_crlf_and_quoted_comma(self):
        rows, errors = parse_video_csv(
            'stt,ten_video,url\r\n'
            '1,"Video, có dấu phẩy",https://www.facebook.com/watch/?v=1\r\n'
        )
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["ten_video"], "Video, có dấu phẩy")

    def test_multiline_quoted_field_keeps_following_error_line_accurate(self):
        rows, errors = parse_video_csv(
            "stt,ten_video,url\n"
            '1,"Tiêu đề\n'
            'trên hai dòng",https://www.facebook.com/a\n'
            "x,Không hợp lệ,https://www.facebook.com/b\n"
        )
        self.assertEqual(rows, [])
        self.assertEqual(errors, ["Dòng 4: stt phải là số nguyên dương"])

    def test_malformed_quoted_row_and_integer_overflow_are_reported(self):
        _, malformed_errors = parse_video_csv(
            'stt,ten_video,url\n1,"Chưa đóng,https://www.facebook.com/a\n'
        )
        self.assertTrue(malformed_errors[0].startswith("Dòng 2: CSV không hợp lệ"))
        _, number_errors = parse_video_csv(
            f"stt,ten_video,url\n{'9' * 5000},Video,https://www.facebook.com/a\n"
        )
        self.assertIn("stt vượt giới hạn số nguyên", number_errors[0])

    def test_duplicate_sequence_and_url_are_rejected(self):
        text = csv_with(
            [
                ("1", "Video 1", "https://www.facebook.com/a"),
                ("1", "Video 2", "https://www.facebook.com/b"),
                ("3", "Video 3", "https://www.facebook.com/a/"),
            ]
        )
        rows, errors = parse_video_csv(text)
        self.assertEqual(rows, [])
        self.assertEqual(len(errors), 2)

    def test_twenty_and_one_thousand_rows_parse(self):
        for size in (20, 1000):
            text = csv_with(
                [
                    (
                        str(index),
                        f"Video {index}",
                        f"https://www.facebook.com/watch/?v={index}",
                    )
                    for index in range(size, 0, -1)
                ]
            )
            rows, errors = parse_video_csv(text)
            self.assertEqual(errors, [])
            self.assertEqual(len(rows), size)
            self.assertEqual(rows[0]["stt"], size)


class ImportPersistenceRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "projects.sqlite3"
        self.store = ProjectStore(self.database_path)
        self.project_id = self.store.create(
            "Dự án A", "ep1-6", str(self.root / "videos")
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    @staticmethod
    def video_csv(name, video_id="1"):
        return csv_with(
            [(video_id, name, f"https://www.facebook.com/watch/?v={video_id}")]
        )

    def test_failed_replacement_preserves_all_existing_rows(self):
        self.store.import_csv(self.project_id, self.video_csv("CSV A"))
        with self.assertRaises(ValueError):
            self.store.import_csv(
                self.project_id,
                csv_with(
                    [
                        ("2", "Valid", "https://www.facebook.com/watch/?v=2"),
                        ("3", "Invalid", "https://example.com/video"),
                    ]
                ),
            )
        rows = self.store.list_videos(self.project_id)
        self.assertEqual([(row["title"], row["video_code"]) for row in rows], [("CSV A", "ep1-6-1")])

    def test_import_replacement_is_blocked_after_download_or_file_link(self):
        self.store.import_csv(self.project_id, self.video_csv("CSV A"))
        self.store.connection.execute(
            "UPDATE videos SET download_status='completed', local_video_path='saved.mp4'"
        )
        self.store.connection.commit()
        with self.assertRaises(ValueError):
            self.store.import_csv(self.project_id, self.video_csv("CSV B", "2"))
        row = self.store.list_videos(self.project_id)[0]
        self.assertEqual((row["title"], row["local_video_path"]), ("CSV A", "saved.mp4"))

    def test_replacement_error_identifies_the_blocking_video(self):
        self.store.import_csv(self.project_id, self.video_csv("CSV A"))
        self.store.connection.execute(
            "UPDATE videos SET download_status='completed', local_video_path='saved.mp4'"
        )
        self.store.connection.commit()
        with self.assertRaisesRegex(
            ValueError, r"ep1-6-1.*CSV A.*đã tải.*đã có tệp video"
        ):
            self.store.import_csv(
                self.project_id, self.video_csv("CSV B", "2"), replace_confirmed=True
            )

    def test_prefix_and_output_directory_cannot_change_after_import(self):
        self.store.import_csv(self.project_id, self.video_csv("CSV A"))
        with self.assertRaises(ValueError):
            self.store.update(
                self.project_id, "Dự án A", "changed", str(self.root / "videos")
            )
        with self.assertRaises(ValueError):
            self.store.update(
                self.project_id, "Dự án A", "ep1-6", str(self.root / "new-videos")
            )
        row = self.store.list_videos(self.project_id)[0]
        self.assertEqual(row["video_code"], "ep1-6-1")

    def test_import_order_and_project_isolation(self):
        other_id = self.store.create(
            "Dự án B", "other", str(self.root / "other-videos")
        )
        self.store.import_csv(
            self.project_id,
            csv_with(
                [
                    ("20", "Hai mươi", "https://www.facebook.com/watch/?v=20"),
                    ("1", "Một", "https://www.facebook.com/watch/?v=1"),
                ]
            ),
        )
        self.assertEqual(
            [row["video_code"] for row in self.store.list_videos(self.project_id)],
            ["ep1-6-1", "ep1-6-20"],
        )
        self.assertEqual(self.store.list_videos(other_id), [])

    def test_cancelled_csv_preview_does_not_change_persisted_data(self):
        self.store.import_csv(self.project_id, self.video_csv("CSV A"))
        self.store.close()
        reopened = ProjectStore(self.database_path)
        self.assertEqual(reopened.list_videos(self.project_id)[0]["title"], "CSV A")
        reopened.close()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import time
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QItemSelectionModel, QThreadPool, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from app.ui.pages.viral_reels_page import CancellableWorker, ViralReelsPage


class TestViralReelsUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self) -> None:
        self.page = ViralReelsPage()

    def tearDown(self) -> None:
        self.page.close()

    def test_filter_fields_exist_and_return_expected_dict(self) -> None:
        self.page.country.setCurrentText("Vietnam")
        self.page.language.setCurrentText("Vietnamese")
        self.page.topic.setText("Gaming")
        self.page.keywords.setText("funny reel")
        self.page.min_views.setValue(50000)
        self.page.max_results.setValue(50)
        self.page.data_source.setCurrentText("Provider")

        filters = self.page.filters()
        self.assertEqual(filters["country"], "Vietnam")
        self.assertEqual(filters["language"], "Vietnamese")
        self.assertEqual(filters["topic"], "Gaming")
        self.assertEqual(filters["keywords"], "funny reel")
        self.assertEqual(filters["min_views"], 50000)
        self.assertEqual(filters["max_results"], 50)
        self.assertEqual(filters["data_source"], "Provider")

    def test_signals_emitted_when_buttons_clicked(self) -> None:
        find_mock = MagicMock()
        scan_mock = MagicMock()
        stop_mock = MagicMock()
        csv_mock = MagicMock()
        excel_mock = MagicMock()
        sheets_mock = MagicMock()

        self.page.findPagesRequested.connect(find_mock)
        self.page.scanReelsRequested.connect(scan_mock)
        self.page.stopRequested.connect(stop_mock)
        self.page.exportCsvRequested.connect(csv_mock)
        self.page.exportExcelRequested.connect(excel_mock)
        self.page.syncGoogleSheetsRequested.connect(sheets_mock)

        self.page.find_pages_button.click()
        find_mock.assert_called_once()
        self.assertTrue(self.page.stop_button.isEnabled())

        self.page.set_busy(False)
        self.page.mode_discover.setChecked(True)
        self.page.scan_reels_button.click()
        scan_mock.assert_called_once()

        self.page.stop_button.click()
        stop_mock.assert_called_once()

        records = [{"reel_url": "https://facebook.com/reel/100", "views": 1000}]
        self.page.set_results(records)

        self.page.export_csv_button.click()
        csv_mock.assert_called_once_with(records)

        self.page.export_excel_button.click()
        excel_mock.assert_called_once_with(records)

        self.page.sync_sheets_button.click()
        sheets_mock.assert_called_once_with(records)

    def test_set_results_populates_13_columns_and_sorting(self) -> None:
        records = [
            {
                "page_name": "Page Alpha",
                "reel_url": "https://facebook.com/reel/1",
                "caption": "A funny reel",
                "views": 5000,
                "likes": 500,
                "comments": 50,
                "shares": 10,
                "published_at": "2026-01-01",
                "country": "Vietnam",
                "language": "Vietnamese",
                "topic": "Entertainment",
                "viral_score": 85.5,
                "data_source": "Database",
            },
            {
                "page_name": "Page Beta",
                "reel_url": "https://facebook.com/reel/2",
                "caption": "Another reel",
                "views": 20000,
                "likes": 2000,
                "comments": 200,
                "shares": 40,
                "published_at": "2026-01-02",
                "country": "United States",
                "language": "English",
                "topic": "News",
                "viral_score": 92.0,
                "data_source": "Provider",
            },
        ]
        self.page.set_results(records)
        self.assertEqual(self.page.model.rowCount(), 2)
        self.assertEqual(self.page.model.columnCount(), 13)

        self.page.table.sortByColumn(3, Qt.SortOrder.DescendingOrder)
        top_views = self.page.model.index(0, 3).data(Qt.ItemDataRole.DisplayRole)
        self.assertEqual(top_views, 20000.0)

    def test_multiselect_remove_and_clear_confirmation(self) -> None:
        records = [
            {"reel_url": "https://facebook.com/reel/1", "page_name": "P1"},
            {"reel_url": "https://facebook.com/reel/2", "page_name": "P2"},
            {"reel_url": "https://facebook.com/reel/3", "page_name": "P3"},
        ]
        self.page.set_results(records)
        selection = self.page.table.selectionModel()
        for row in (0, 1):
            selection.select(
                self.page.model.index(row, 0),
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
            )

        selected = self.page.selected_records()
        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0]["reel_url"], "https://facebook.com/reel/1")

        remove_mock = MagicMock()
        self.page.removeRequested.connect(remove_mock)
        self.page.remove_button.click()
        remove_mock.assert_called_once()
        self.assertEqual(self.page.model.rowCount(), 1)

        with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.page.clear_button.click()
        self.assertEqual(self.page.model.rowCount(), 1)

        with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.page.clear_button.click()
        self.assertEqual(self.page.model.rowCount(), 0)

    def test_open_and_copy_url(self) -> None:
        records = [{"reel_url": "https://facebook.com/reel/999", "page_name": "Target"}]
        self.page.set_results(records)
        self.page.table.selectRow(0)

        with patch("webbrowser.open") as open_mock:
            self.page.open_selected_url()
            open_mock.assert_called_once_with("https://facebook.com/reel/999")

        self.page.copy_selected_url()
        self.assertEqual(QGuiApplication.clipboard().text(), "https://facebook.com/reel/999")

    def test_cancellable_worker_responsiveness_and_cancellation(self) -> None:
        progress_reports: list[tuple[int, int, str]] = []
        state = {"finished": False, "cancelled": False}

        def long_task(cancel_event, progress_cb):
            for step in range(1, 10):
                if cancel_event.is_set():
                    return "cancelled"
                progress_cb(step, 10, f"Step {step}")
                time.sleep(0.01)
            return "done"

        worker = CancellableWorker(long_task)
        worker.signals.progress.connect(lambda d, t, m: progress_reports.append((d, t, m)))
        worker.signals.finished.connect(lambda: state.update(finished=True))
        worker.signals.cancelled.connect(lambda: state.update(cancelled=True))

        pool = QThreadPool.globalInstance()
        pool.start(worker)

        time.sleep(0.02)
        worker.cancel()
        pool.waitForDone(1000)
        deadline = time.monotonic() + 1
        while not state["finished"] and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)

        self.assertTrue(state["finished"])
        self.assertTrue(state["cancelled"])
        self.assertTrue(len(progress_reports) > 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import csv
import threading
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from PySide6.QtCore import QDate, QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QStyledItemDelegate, QListView
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
    QScrollArea,
    QRadioButton,
    QButtonGroup,
)


class _DropdownDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), 36))
        return size


class WorkerSignals(QObject):
    progress = Signal(int, int, str)
    result = Signal(object)
    error = Signal(str)
    cancelled = Signal()
    finished = Signal()


class CancellableWorker(QRunnable):
    """Run injected backend work off UI thread; callable receives cancel event and progress callback."""

    def __init__(
        self,
        operation: Callable[[threading.Event, Callable[[int, int, str], None]], Any],
    ) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self._operation = operation
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @Slot()
    def run(self) -> None:
        try:
            result = self._operation(self._cancel_event, self.signals.progress.emit)
            if self.is_cancelled:
                self.signals.cancelled.emit()
            else:
                self.signals.result.emit(result)
        except Exception as error:
            self.signals.error.emit(str(error))
        finally:
            self.signals.finished.emit()


class ViralReelsPage(QWidget):
    findPagesRequested = Signal(dict)
    providerRequested = Signal()
    scanReelsRequested = Signal(dict)
    stopRequested = Signal()
    exportCsvRequested = Signal(list)
    exportExcelRequested = Signal(list)
    syncGoogleSheetsRequested = Signal(list)
    removeRequested = Signal(list)
    pagesChanged = Signal()
    pageAddRequested = Signal(str)
    pagesImportRequested = Signal(str)
    pagesDeleteRequested = Signal(list)

    COLUMNS = (
        ("page_name", "Page"),
        ("reel_url", "Reel URL"),
        ("caption", "Caption"),
        ("views", "Views"),
        ("likes", "Likes"),
        ("comments", "Comments"),
        ("shares", "Shares"),
        ("published_at", "Published"),
        ("country", "Country"),
        ("language", "Language"),
        ("topic", "Topic"),
        ("viral_score", "Viral Score"),
        ("data_source", "Data Source"),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._records: list[dict[str, Any]] = []
        self._worker: CancellableWorker | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(12)
        title = QLabel("Viral Reels Finder")
        title.setObjectName("title")
        layout.addWidget(title)

        filters = QFormLayout()
        filters.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.country = self._dropdown(["All", "Vietnam", "United States", "China", "Thailand", "South Korea", "Japan", "Other"])
        self.language = self._dropdown(["All", "Vietnamese", "English", "Chinese", "Thai", "Korean", "Japanese", "Other"])
        self.topic = QLineEdit()
        self.keywords = QLineEdit()
        self.min_views = QSpinBox()
        self.min_views.setRange(0, 2_000_000_000)
        self.min_views.setSingleStep(10_000)
        self.date_preset = self._dropdown(["Any time", "Last 24 hours", "Last 3 days", "Last 7 days", "Last 30 days", "Custom range"])
        self.date_from = self._date_edit()
        self.date_to = self._date_edit()
        self._preset_timestamps: tuple[str, str] | None = None
        self.date_preset.currentTextChanged.connect(self._apply_date_preset)
        self.date_from.dateChanged.connect(self._date_edited)
        self.date_to.dateChanged.connect(self._date_edited)
        self.max_results = QSpinBox()
        self.max_results.setRange(1, 10_000)
        self.max_results.setValue(100)
        self.mode_page = QRadioButton("Scan by Page")
        self.mode_discover = QRadioButton("Discover trending Reels")
        self.mode_page.setChecked(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.mode_page)
        self.mode_group.addButton(self.mode_discover)
        self.mode_page.toggled.connect(self._mode_changed)
        self.data_source = self._dropdown(["All", "Database", "Provider", "Meta Graph API"])
        self.data_source.hide()
        self.provider_status = QLabel("Provider: not configured")
        self.provider_status.setWordWrap(True)
        self.provider_button = self._button("Configure provider", "secondary", self.providerRequested.emit)
        self.schedule = QComboBox()
        self.schedule.addItems(["Manual", "Every 6 hours", "Every 12 hours", "Daily"])
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.mode_page)
        mode_row.addWidget(self.mode_discover)
        mode_row.addStretch()
        layout.addLayout(mode_row)
        provider_card = QVBoxLayout()
        provider_card.addWidget(QLabel("Provider"))
        provider_card.addWidget(self.provider_status)
        provider_card.addWidget(self.provider_button)
        layout.addLayout(provider_card)

        basic_filters = QFormLayout()
        basic_filters.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for label, widget in (("Country", self.country), ("Language", self.language), ("Topic", self.topic), ("Keywords", self.keywords), ("Maximum results", self.max_results)):
            basic_filters.addRow(label, widget)
        layout.addLayout(basic_filters)

        advanced_filters = QFormLayout()
        advanced_filters.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for label, widget in (("Date preset", self.date_preset), ("Minimum views", self.min_views), ("Date from", self.date_from), ("Date to", self.date_to), ("Schedule", self.schedule)):
            advanced_filters.addRow(label, widget)
        advanced_widget = QWidget()
        advanced_widget.setLayout(advanced_filters)
        advanced_scroll = QScrollArea()
        advanced_scroll.setWidgetResizable(True)
        advanced_scroll.setMaximumHeight(150)
        advanced_scroll.setWidget(advanced_widget)
        layout.addWidget(QLabel("Advanced filters"))
        layout.addWidget(advanced_scroll)

        page_actions = QHBoxLayout()
        self.page_url_input = QLineEdit()
        self.page_url_input.setPlaceholderText("Facebook Page URL")
        page_actions.addWidget(self.page_url_input)
        self.add_page_button = self._button("Add Page", "secondary", self._add_page)
        self.import_pages_button = self._button("Import CSV", "secondary", self._import_pages)
        self.delete_pages_button = self._button("Delete Pages", "secondary", self._delete_pages)
        page_actions.addWidget(self.add_page_button)
        page_actions.addWidget(self.import_pages_button)
        page_actions.addWidget(self.delete_pages_button)
        layout.addLayout(page_actions)
        self.pages = QListView()
        self.pages_model = QStandardItemModel(self)
        self.pages.setModel(self.pages_model)
        self.pages.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.pages)
        actions = QHBoxLayout()
        self.find_pages_button = self._button("Find Pages", "primary", self._find_pages)
        self.scan_reels_button = self._button("Scan Reels", "primary", self._scan_reels)
        self.stop_button = self._button("Stop", "danger", self.stopRequested.emit)
        self.stop_button.setEnabled(False)
        self.export_csv_button = self._button("Export CSV", "secondary", self._export_csv)
        self.export_excel_button = self._button("Export Excel", "secondary", self._export_excel)
        self.sync_sheets_button = self._button("Sync Google Sheets", "secondary", self._sync_sheets)
        for button in (self.find_pages_button, self.scan_reels_button, self.stop_button,
                       self.export_csv_button, self.export_excel_button, self.sync_sheets_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)

        self.progress_label = QLabel("Pages 0/0 · Reels 0/0 · Results 0")
        self.progress_label.setObjectName("detailMeta")
        layout.addWidget(self.progress_label)
        self.table = QTableView()
        self.model = QStandardItemModel(0, len(self.COLUMNS), self)
        self.model.setHorizontalHeaderLabels([label for _, label in self.COLUMNS])
        self.table.setModel(self.model)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_menu)
        self.table.doubleClicked.connect(self._open_index_url)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setMinimumSectionSize(90)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        row_actions = QHBoxLayout()
        self.remove_button = self._button("Remove selected", "danger", self.remove_selected)
        self.clear_button = self._button("Clear", "danger", self.clear_results)
        row_actions.addWidget(self.remove_button)
        row_actions.addWidget(self.clear_button)
        row_actions.addStretch()
        layout.addLayout(row_actions)
        self._update_result_actions()
        self.table.selectionModel().selectionChanged.connect(self._update_result_actions)

    def _dropdown(self, items: Sequence[str]) -> QComboBox:
        combo = QComboBox()
        combo.addItems(items)
        combo.setView(QListView())
        combo.view().setItemDelegate(_DropdownDelegate(combo.view()))
        combo.view().setUniformItemSizes(True)
        combo.setMaxVisibleItems(8)
        combo.setStyleSheet("QComboBox { padding: 6px 8px; } QComboBox QAbstractItemView { padding: 4px; selection-background-color: #40506b; }")
        return combo

    def _date_edit(self) -> QDateEdit:
        edit = QDateEdit()
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("yyyy-MM-dd")
        edit.setMinimumDate(QDate(2000, 1, 1))
        edit.setSpecialValueText("Any")
        edit.setDate(edit.minimumDate())
        return edit

    def _button(self, text: str, name: str, callback: Callable) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(name)
        button.clicked.connect(callback)
        return button

    def _date_edited(self) -> None:
        if self.date_preset.currentText() != "Custom range":
            self.date_preset.setCurrentText("Custom range")
        if self.date_from.date() != self.date_from.minimumDate() and self.date_to.date() != self.date_to.minimumDate() and self.date_from.date() > self.date_to.date():
            self.date_to.setDate(self.date_from.date())

    def _apply_date_preset(self, preset: str) -> None:
        if preset == "Custom range":
            return
        if preset == "Any time":
            self.date_from.setDate(self.date_from.minimumDate())
            self.date_to.setDate(self.date_to.minimumDate())
            return
        now = datetime.now().astimezone()
        seconds = {"Last 24 hours": 1, "Last 3 days": 3, "Last 7 days": 7, "Last 30 days": 30}[preset]
        self._preset_timestamps = ((now - timedelta(days=seconds)).isoformat(), now.isoformat())
        self.date_from.setDate(QDate.fromString(self._preset_timestamps[0][:10], Qt.DateFormat.ISODate))
        self.date_to.setDate(QDate.currentDate())

    def filters(self) -> dict[str, Any]:
        return {
            "country": self.country.currentText(), "language": self.language.currentText(),
            "topic": self.topic.text().strip(), "keywords": self.keywords.text().strip(),
            "min_views": self.min_views.value(),
            "date_from": self._preset_timestamps[0] if self._preset_timestamps else ("" if self.date_from.date() == self.date_from.minimumDate() else self.date_from.date().toString(Qt.DateFormat.ISODate)),
            "date_to": self._preset_timestamps[1] if self._preset_timestamps else ("" if self.date_to.date() == self.date_to.minimumDate() else self.date_to.date().toString(Qt.DateFormat.ISODate)),
            "max_results": self.max_results.value(), "data_source": self.data_source.currentText(),
            "schedule": self.schedule.currentText(),
        }

    def _add_page(self) -> None:
        url = self.page_url_input.text().strip()
        if url:
            self.pageAddRequested.emit(url)
            self.page_url_input.clear()

    def _import_pages(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Pages", "", "CSV files (*.csv)")
        if path:
            self.pagesImportRequested.emit(path)

    def _delete_pages(self) -> None:
        urls = [str(index.data(Qt.ItemDataRole.UserRole)) for index in self.pages.selectionModel().selectedIndexes()]
        if urls:
            self.pagesDeleteRequested.emit(urls)

    def set_provider_status(self, provider_name: str, status: str, capabilities: str) -> None:
        self.provider_status.setText(f"Provider: {provider_name}\nStatus: {status}\nCapabilities: {capabilities}")

    def _mode_changed(self, page_mode: bool) -> None:
        self.page_url_input.setEnabled(page_mode)
        self.pages.setEnabled(page_mode)
        self.find_pages_button.setEnabled(page_mode and self._worker is None)
        self.scan_reels_button.setText("Quét Reels theo Page" if page_mode else "Tìm Reels xu hướng")

    def _find_pages(self) -> None:
        self.set_busy(True)
        self.findPagesRequested.emit(self.filters())

    def _scan_reels(self) -> None:
        selected = [str(index.data(Qt.ItemDataRole.UserRole)) for index in self.pages.selectionModel().selectedIndexes()]
        values = self.filters()
        values["mode"] = "scan_by_page" if self.mode_page.isChecked() else "discover_trending"
        if self.mode_page.isChecked():
            if not selected:
                self.set_busy(False)
                self.progress_label.setText("Pages required: nhập hoặc chọn ít nhất một Facebook Page URL để quét Reels.")
                return
            values["selected_page_urls"] = selected
        self.set_busy(True)
        self.scanReelsRequested.emit(values)

    def run_operation(
        self,
        operation: Callable[[threading.Event, Callable[[int, int, str], None]], Any],
        result_handler: Callable[[Any], None],
    ) -> bool:
        if self._worker is not None:
            return False
        self.set_busy(True)
        worker = CancellableWorker(operation)
        self._worker = worker
        worker.signals.progress.connect(
            lambda done, total, message: self.progress_label.setText(f"{message}: {done}/{total}")
        )
        worker.signals.result.connect(result_handler)
        worker.signals.error.connect(lambda message: QMessageBox.warning(self, "Viral Reels Finder", message))
        worker.signals.finished.connect(self._operation_finished)
        QThreadPool.globalInstance().start(worker)
        return True

    def cancel_operation(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def closeEvent(self, event) -> None:
        worker = self._worker
        if worker is not None:
            worker.cancel()
            # Remove queued callbacks before QWidget destruction; network cancellation remains cooperative.
            for signal in (worker.signals.progress, worker.signals.result, worker.signals.error,
                           worker.signals.cancelled, worker.signals.finished):
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
            self._worker = None
        event.accept()

    def _operation_finished(self) -> None:
        self._worker = None
        self.set_busy(False)

    def set_busy(self, busy: bool) -> None:
        self.find_pages_button.setEnabled(not busy)
        self.scan_reels_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)

    def set_progress(self, pages_done: int, pages_total: int, reels_done: int, reels_total: int) -> None:
        self.progress_label.setText(
            f"Pages {pages_done}/{pages_total} · Reels {reels_done}/{reels_total} · Results {len(self._records)}"
        )

    def set_results(self, records: Sequence[Mapping[str, Any]]) -> None:
        self._records = [dict(record) for record in records]
        self.model.setRowCount(0)
        for index, record in enumerate(self._records):
            items = []
            for key, _ in self.COLUMNS:
                value = record.get(key, "")
                item = QStandardItem(str(value if value is not None else ""))
                if key in {"views", "likes", "comments", "shares", "viral_score"}:
                    item.setData(float(value or 0), Qt.ItemDataRole.DisplayRole)
                item.setData(record.get("reel_url", ""), Qt.ItemDataRole.UserRole)
                item.setData(index, Qt.ItemDataRole.UserRole + 1)
                items.append(item)
            self.model.appendRow(items)
        self.set_progress(0, 0, 0, 0)
        self._update_result_actions()

    def selected_records(self) -> list[dict[str, Any]]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [dict(self._records[self.model.item(row, 0).data(Qt.ItemDataRole.UserRole + 1)])
                if self.model.item(row, 0).data(Qt.ItemDataRole.UserRole + 1) is not None
                else self._record_for_url(self.model.item(row, 0).data(Qt.ItemDataRole.UserRole))
                for row in rows]

    def _record_for_url(self, url: str) -> dict[str, Any]:
        return dict(next((record for record in self._records if record.get("reel_url", "") == url), {}))

    def _export_rows(self) -> list[dict[str, Any]]:
        return self.selected_records() or [dict(record) for record in self._records]

    def _export_csv(self) -> None: self.exportCsvRequested.emit(self._export_rows())
    def _export_excel(self) -> None: self.exportExcelRequested.emit(self._export_rows())
    def _sync_sheets(self) -> None: self.syncGoogleSheetsRequested.emit(self._export_rows())

    def remove_selected(self) -> None:
        selected = self.selected_records()
        if not selected:
            return
        urls = {record.get("reel_url") for record in selected}
        self.removeRequested.emit(selected)
        self.set_results([record for record in self._records if record.get("reel_url") not in urls])

    def clear_results(self) -> None:
        if not self._records:
            return
        answer = QMessageBox.question(self, "Clear results", "Remove all results?",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.removeRequested.emit([dict(record) for record in self._records])
            self.set_results([])

    def _update_result_actions(self, *_args: Any) -> None:
        has_rows = bool(self._records)
        self.remove_button.setEnabled(bool(self.table.selectionModel().selectedRows()))
        self.clear_button.setEnabled(has_rows)
        for button in (self.export_csv_button, self.export_excel_button, self.sync_sheets_button):
            button.setEnabled(has_rows)

    def _show_menu(self, position: Any) -> None:
        index = self.table.indexAt(position)
        if index.isValid() and not self.table.selectionModel().isSelected(index):
            self.table.selectRow(index.row())
        menu = QMenu(self)
        menu.addAction("Open URL", self.open_selected_url)
        menu.addAction("Copy URL", self.copy_selected_url)
        menu.addSeparator()
        menu.addAction("Remove selected", self.remove_selected)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _selected_url(self) -> str:
        indexes = self.table.selectionModel().selectedRows()
        return str(indexes[0].data(Qt.ItemDataRole.UserRole)) if indexes else ""

    def open_selected_url(self) -> None:
        if url := self._selected_url():
            webbrowser.open(url)

    def copy_selected_url(self) -> None:
        if url := self._selected_url():
            clipboard = QGuiApplication.clipboard()
            clipboard.setText(url, clipboard.Mode.Clipboard)
            QGuiApplication.processEvents()

    def _open_index_url(self, index: Any) -> None:
        if url := str(index.data(Qt.ItemDataRole.UserRole) or ""):
            webbrowser.open(url)

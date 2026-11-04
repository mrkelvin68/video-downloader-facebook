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
    QStackedWidget,
    QCheckBox,
    QGroupBox,
    QRadioButton,
    QButtonGroup,
)


class _DropdownDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), 36))
        return size


def _arrow_icon_path() -> str:
    """Render the combo chevron once to a temp PNG so QSS can reference it (works frozen)."""
    import os
    import tempfile

    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

    path = os.path.join(tempfile.gettempdir(), "vdf_combo_chevron.png")
    if not os.path.exists(path):
        pixmap = QPixmap(12, 12)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#475569"), 1.8))
        painter.drawPolyline([QPointF(2.5, 4.0), QPointF(6.0, 7.5), QPointF(9.5, 4.0)])
        painter.end()
        pixmap.save(path)
    return path.replace("\\", "/")


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
    exportPagesCsvRequested = Signal(list)
    recoverRunRequested = Signal()

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
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 6, 24, 6)
        root.setSpacing(4)
        title = QLabel("Viral Reels Finder")
        title.setObjectName("title")
        root.addWidget(title)
        self.stack = QStackedWidget()
        reels_widget = QWidget()
        layout = QVBoxLayout(reels_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

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
        self.max_results.setValue(30)
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
        self.mode_find = QRadioButton("Find Pages")
        self.mode_group.addButton(self.mode_find)
        self.mode_find.toggled.connect(self._find_mode_toggled)
        mode_row.addWidget(self.mode_find)
        mode_row.addWidget(self.mode_page)
        mode_row.addWidget(self.mode_discover)
        mode_row.addStretch()
        root.addLayout(mode_row)
        self.stack.addWidget(self._build_find_view())
        self.stack.addWidget(reels_widget)
        root.addWidget(self.stack, 1)
        # A. Provider bar
        provider_bar = QHBoxLayout()
        self.provider_status.setWordWrap(False)
        provider_bar.addWidget(self.provider_status, 1)
        provider_bar.addWidget(self.provider_button)
        layout.addLayout(provider_bar)

        # B. Page selection
        add_row = QHBoxLayout()
        self.page_url_input = QLineEdit()
        self.page_url_input.setPlaceholderText("Facebook Page URL, e.g. https://www.facebook.com/PageName")
        self.page_url_input.setMinimumHeight(36)
        self.page_url_input.returnPressed.connect(self._add_page)
        self.add_page_button = self._button("Add Page", "secondary", self._add_page)
        self.import_pages_button = self._button("Import CSV", "secondary", self._import_pages)
        for widget in (self.page_url_input, self.add_page_button, self.import_pages_button):
            add_row.addWidget(widget, 1 if widget is self.page_url_input else 0)
        layout.addLayout(add_row)
        self.pages_model = QStandardItemModel(0, 3, self)
        self.pages_model.setHorizontalHeaderLabels(["", "Page", "Facebook Page URL"])
        self.pages = QTableView()
        self.pages.setModel(self.pages_model)
        self.pages.setAlternatingRowColors(True)
        self.pages.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pages.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pages.verticalHeader().hide()
        self.pages.horizontalHeader().setStretchLastSection(True)
        self.pages.setColumnWidth(0, 36)
        self.pages.setColumnWidth(1, 260)
        self.pages.verticalHeader().setDefaultSectionSize(26)
        self.pages.setMinimumHeight(68)
        self.pages.setMaximumHeight(82)
        self.pages_model.itemChanged.connect(self._update_scan_pages_state)
        layout.addWidget(self.pages, 1)
        select_row = QHBoxLayout()
        self.scan_select_all = QCheckBox("Select all")
        self.scan_select_all.clicked.connect(self._select_all_scan_pages)
        self.scan_selected_label = QLabel("Selected: 0 / 0")
        self.delete_pages_button = self._button("Delete selected Pages", "secondary", self._delete_pages)
        select_row.addWidget(self.scan_select_all)
        select_row.addWidget(self.scan_selected_label)
        select_row.addStretch()
        select_row.addWidget(self.delete_pages_button)
        layout.addLayout(select_row)

        # C. Scan options (+ optional advanced filters)
        options = QHBoxLayout()
        self.min_views.setMinimumHeight(36)
        self.max_results.setMinimumHeight(36)
        for label, widget in (("Maximum results", self.max_results), ("Minimum views", self.min_views), ("Date", self.date_preset)):
            options.addWidget(QLabel(label))
            options.addWidget(widget, 1)
        layout.addLayout(options)
        self.advanced_group = QGroupBox("Advanced filters (apply only when enabled)")
        self.advanced_group.setCheckable(True)
        self.advanced_group.setChecked(False)
        advanced_filters = QFormLayout(self.advanced_group)
        for label, widget in (("Country", self.country), ("Language", self.language), ("Topic", self.topic), ("Date from", self.date_from), ("Date to", self.date_to), ("Schedule", self.schedule)):
            advanced_filters.addRow(label, widget)
        self.advanced_group.toggled.connect(lambda on: [advanced_filters.itemAt(i).widget().setVisible(on) for i in range(advanced_filters.count()) if advanced_filters.itemAt(i).widget()])
        self.advanced_group.toggled.connect(lambda on: self.advanced_group.setMaximumHeight(16777215 if on else 22))
        self.advanced_group.toggled.emit(False)
        layout.addWidget(self.advanced_group)

        # D. Actions
        actions = QHBoxLayout()
        self.find_pages_button = self._button("Find Pages", "primary", self._find_pages)
        self.scan_reels_button = self._button("Quét Reels theo Page", "primary", self._scan_reels)
        self.recover_run_button = self._button("Khôi phục Run cũ", "secondary", self.recoverRunRequested.emit)
        self.stop_button = self._button("Stop", "danger", self.stopRequested.emit)
        self.stop_button.setEnabled(False)
        self.export_csv_button = self._button("Export CSV", "secondary", self._export_csv)
        self.export_excel_button = self._button("Export Excel", "secondary", self._export_excel)
        self.sync_sheets_button = self._button("Sync Google Sheets", "secondary", self._sync_sheets)
        for button in (self.scan_reels_button, self.recover_run_button, self.stop_button, self.export_csv_button, self.export_excel_button, self.sync_sheets_button):
            actions.addWidget(button)
        self.find_pages_button.hide()
        actions.addStretch()
        layout.addLayout(actions)

        self.progress_label = QLabel("Pages 0/0 · Reels 0/0 · Results 0")
        self.progress_label.setObjectName("detailMeta")
        
        # E. Results
        self.table = QTableView()
        self.table.verticalHeader().setDefaultSectionSize(28)
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
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(70)
        header.setStretchLastSection(True)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        keys = [key for key, _ in self.COLUMNS]
        for key in ("country", "language", "topic", "data_source"):
            self.table.setColumnHidden(keys.index(key), True)
        header.setSectionsMovable(True)
        header.moveSection(keys.index("reel_url"), 0)
        for key, width in (("reel_url", 300), ("page_name", 160), ("caption", 260), ("views", 80), ("likes", 80), ("comments", 90), ("shares", 80), ("published_at", 150)):
            self.table.setColumnWidth(keys.index(key), width)
        layout.addWidget(self.table, 4)

        row_actions = QHBoxLayout()
        self.remove_button = self._button("Remove selected", "danger", self.remove_selected)
        self.clear_button = self._button("Clear", "danger", self.clear_results)
        row_actions.addWidget(self.remove_button)
        row_actions.addWidget(self.clear_button)
        row_actions.insertWidget(0, self.progress_label, 1)
        layout.addLayout(row_actions)
        self._update_result_actions()
        self._update_scan_pages_state()
        self.mode_find.setChecked(True)
        self.table.selectionModel().selectionChanged.connect(self._update_result_actions)

    COMBO_QSS = (
        "QComboBox { min-height: 24px; padding: 5px 30px 5px 10px; border: 1px solid #3d4a63; border-radius: 6px; }"
        "QComboBox:hover, QComboBox:focus { border-color: #6f8fd8; }"
        "QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 26px; border: none; }"
        "QComboBox::down-arrow { image: url({ARROW}); width: 12px; height: 12px; margin-right: 8px; }"
        "QComboBox QAbstractItemView { padding: 4px; outline: 0; selection-background-color: #40506b; }"
        "QComboBox QAbstractItemView::item { min-height: 28px; padding: 2px 8px; }"
        "QComboBox QAbstractItemView::item:hover { background: #33415c; }"
    )

    def _dropdown(self, items: Sequence[str]) -> QComboBox:
        combo = QComboBox()
        combo.addItems(items)
        combo.setMaxVisibleItems(8)
        combo.setMinimumHeight(36)
        combo.setMinimumContentsLength(max((len(item) for item in items), default=8))
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setStyleSheet(self.COMBO_QSS.replace("{ARROW}", _arrow_icon_path()))
        return combo

    PAGE_COLUMNS = (("", ""), ("Page Name", "page_name"), ("Facebook Page URL", "page_url"), ("Page ID", "page_id"), ("Data Source", "data_source"))

    def _build_find_view(self) -> QWidget:
        view = QWidget()
        box = QVBoxLayout(view)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        self.find_keywords = QLineEdit()
        self.find_keywords.setPlaceholderText("Keywords, comma separated")
        self.find_country = self._dropdown(["All", "Vietnam", "United States", "China", "Thailand", "South Korea", "Japan", "Other"])
        self.find_max = QSpinBox()
        self.find_max.setRange(1, 100)
        self.find_max.setValue(50)
        self.find_max.setMinimumHeight(36)
        self.find_keywords.setMinimumHeight(36)
        self.find_provider_status = QLabel("Provider: not configured")
        self.find_provider_status.setWordWrap(True)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.addRow("Keywords", self.find_keywords)
        form.addRow("Country", self.find_country)
        form.addRow("Maximum results", self.find_max)
        box.addLayout(form)
        provider_row = QHBoxLayout()
        provider_row.addWidget(self.find_provider_status, 1)
        provider_row.addWidget(self._button("Configure provider", "secondary", self.providerRequested.emit))
        self.find_pages_button2 = self._button("Find Pages", "primary", self._find_pages)
        provider_row.addWidget(self.find_pages_button2)
        box.addLayout(provider_row)
        self.found_model = QStandardItemModel(0, len(self.PAGE_COLUMNS), self)
        self.found_model.setHorizontalHeaderLabels([label for label, _ in self.PAGE_COLUMNS])
        self.found_table = QTableView()
        self.found_table.setModel(self.found_model)
        self.found_table.setAlternatingRowColors(True)
        self.found_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.found_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.found_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.found_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.found_table.verticalHeader().hide()
        header = self.found_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for column, width in enumerate((36, 260, 420, 160, 120)):
            self.found_table.setColumnWidth(column, width)
        self.found_table.doubleClicked.connect(self._open_found_url)
        self.found_model.itemChanged.connect(self._update_found_actions)
        self.found_table.selectionModel().selectionChanged.connect(self._update_found_actions)
        box.addWidget(self.found_table, 1)
        actions = QHBoxLayout()
        self.found_count = QLabel("Pages: 0")
        self.select_all_box = QCheckBox("Select all")
        self.select_all_box.clicked.connect(self._select_all_found)
        self.copy_url_button = self._button("Copy Page URL", "secondary", self.copy_found_urls)
        self.export_pages_button = self._button("Export CSV", "secondary", self._export_pages_csv)
        self.export_selected_button = self._button("Export Selected", "secondary", lambda: self._export_pages_csv(True))
        for widget in (self.found_count, self.select_all_box):
            actions.addWidget(widget)
        actions.addStretch()
        for widget in (self.copy_url_button, self.export_selected_button, self.export_pages_button):
            actions.addWidget(widget)
        box.addLayout(actions)
        self._update_found_actions()
        return view

    def set_found_pages(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self._bulk = True
        self.found_model.removeRows(0, self.found_model.rowCount())
        seen: set[str] = set()
        for row in rows:
            url = str(row.get("page_url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            check = QStandardItem()
            check.setCheckable(True)
            check.setEditable(False)
            cells = [check]
            for _, key in self.PAGE_COLUMNS[1:]:
                item = QStandardItem(str(row.get(key) or ""))
                item.setEditable(False)
                item.setToolTip(item.text())
                cells.append(item)
            self.found_model.appendRow(cells)
        self._bulk = False
        self.select_all_box.setChecked(False)
        self._update_found_actions()

    def found_records(self, selected_only: bool = False) -> list[dict[str, str]]:
        rows = range(self.found_model.rowCount())
        if selected_only:
            checked = {r for r in rows if self.found_model.item(r, 0).checkState() == Qt.CheckState.Checked}
            highlighted = {i.row() for i in self.found_table.selectionModel().selectedRows()}
            rows = sorted(checked | highlighted)
        return [{key: self.found_model.item(r, c).text() for c, (_, key) in enumerate(self.PAGE_COLUMNS) if c} for r in rows]

    def _update_found_actions(self, *_args: Any) -> None:
        if getattr(self, "_bulk", False):
            return
        total = self.found_model.rowCount()
        selected = len(self.found_records(True)) if total else 0
        self.found_count.setText(f"Pages: {total}" + (f" · Selected: {selected}" if selected else ""))
        self.export_pages_button.setEnabled(total > 0)
        self.export_selected_button.setEnabled(selected > 0)
        self.copy_url_button.setEnabled(total > 0)

    def _select_all_found(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._bulk = True
        for row in range(self.found_model.rowCount()):
            self.found_model.item(row, 0).setCheckState(state)
        self._bulk = False
        self._update_found_actions()

    def copy_found_urls(self) -> None:
        rows = self.found_records(True) or self.found_records()
        QGuiApplication.clipboard().setText("\n".join(row["page_url"] for row in rows))

    def _open_found_url(self, index: Any) -> None:
        url = self.found_model.item(index.row(), 2).text()
        if url:
            webbrowser.open(url)

    def _export_pages_csv(self, selected_only: bool = False) -> None:
        rows = self.found_records(selected_only is True)
        if rows:
            self.exportPagesCsvRequested.emit(rows)

    def _find_mode_toggled(self, checked: bool) -> None:
        self.stack.setCurrentIndex(0 if checked else 1)
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
            "country": self.country.currentText() if self.advanced_group.isChecked() else "All", "language": self.language.currentText() if self.advanced_group.isChecked() else "All",
            "topic": self.topic.text().strip() if self.advanced_group.isChecked() else "", "keywords": self.keywords.text().strip(),
            "min_views": self.min_views.value(),
            "date_from": self._preset_timestamps[0] if self._preset_timestamps else ("" if self.date_from.date() == self.date_from.minimumDate() else self.date_from.date().toString(Qt.DateFormat.ISODate)),
            "date_to": self._preset_timestamps[1] if self._preset_timestamps else ("" if self.date_to.date() == self.date_to.minimumDate() else self.date_to.date().toString(Qt.DateFormat.ISODate)),
            "max_results": self.max_results.value(), "data_source": self.data_source.currentText(),
            "schedule": self.schedule.currentText(),
        }

    def set_scan_pages(self, rows: Sequence[Mapping[str, Any]], select_urls: Sequence[str] = ()) -> None:
        keep = set(select_urls) | set(self.checked_page_urls())
        self.pages_model.blockSignals(True)
        self.pages_model.removeRows(0, self.pages_model.rowCount())
        for row in rows:
            url = str(row.get("page_url") or "")
            check = QStandardItem()
            check.setCheckable(True)
            check.setCheckState(Qt.CheckState.Checked if url in keep else Qt.CheckState.Unchecked)
            check.setData(url, Qt.ItemDataRole.UserRole)
            cells = [check]
            for text in (str(row.get("page_name") or url), url):
                item = QStandardItem(text)
                item.setToolTip(text)
                item.setData(url, Qt.ItemDataRole.UserRole)
                cells.append(item)
            self.pages_model.appendRow(cells)
        self.pages_model.blockSignals(False)
        self.pages_model.layoutChanged.emit()
        self._update_scan_pages_state()

    def checked_page_urls(self) -> list[str]:
        return [str(self.pages_model.item(r, 0).data(Qt.ItemDataRole.UserRole)) for r in range(self.pages_model.rowCount())
                if self.pages_model.item(r, 0).checkState() == Qt.CheckState.Checked]

    def _update_scan_pages_state(self, *_args: Any) -> None:
        total, chosen = self.pages_model.rowCount(), len(self.checked_page_urls())
        self.scan_selected_label.setText(f"Selected: {chosen} / {total}")
        self.delete_pages_button.setEnabled(chosen > 0)
        self.scan_select_all.setChecked(total > 0 and chosen == total)

    def _select_all_scan_pages(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.pages_model.blockSignals(True)
        for row in range(self.pages_model.rowCount()):
            self.pages_model.item(row, 0).setCheckState(state)
        self.pages_model.blockSignals(False)
        self.pages_model.layoutChanged.emit()
        self._update_scan_pages_state()

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
        urls = self.checked_page_urls()
        if urls and QMessageBox.question(self, "Delete Pages", f"Delete {len(urls)} selected Page(s) from the saved list?") == QMessageBox.StandardButton.Yes:
            self.pagesDeleteRequested.emit(urls)

    def set_provider_status(self, provider_name: str, status: str, capabilities: str) -> None:
        self.find_provider_status.setText(f"Provider: {provider_name} · {status} · {capabilities}")
        self.provider_status.setText(f"Provider: {provider_name} · {status} · {capabilities}")

    def _mode_changed(self, page_mode: bool) -> None:
        if self.mode_find.isChecked():
            return
        self.page_url_input.setEnabled(page_mode)
        self.pages.setEnabled(page_mode)
        self.find_pages_button.setEnabled(page_mode and self._worker is None)
        self.scan_reels_button.setText("Quét Reels theo Page" if page_mode else "Tìm Reels xu hướng")

    def _find_pages(self) -> None:
        self.set_busy(True)
        values = self.filters()
        values.update(keywords=self.find_keywords.text().strip(), country=self.find_country.currentText(), max_results=self.find_max.value())
        self.findPagesRequested.emit(values)

    def _scan_reels(self) -> None:
        selected = self.checked_page_urls()
        values = self.filters()
        values["mode"] = "scan_by_page" if self.mode_page.isChecked() else "discover_trending"
        if self.mode_page.isChecked():
            if not selected:
                self.set_busy(False)
                self.progress_label.setText("Pages required: tick ít nhất một Facebook Page trong danh sách (hoặc Add Page) để quét Reels.")
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
        worker.signals.cancelled.connect(lambda: self.progress_label.setText("Operation cancelled."))
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
        self.find_pages_button2.setEnabled(not busy)
        self.scan_reels_button.setEnabled(not busy)
        self.recover_run_button.setEnabled(not busy)
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
                if key in {"views", "likes", "comments", "shares", "viral_score"} and value not in (None, ""):
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

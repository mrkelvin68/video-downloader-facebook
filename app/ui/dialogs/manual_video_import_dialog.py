from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile, QSignalBlocker, QStandardPaths, QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.services.csv_service import parse_video_csv
from app.services.video_download_provider import provider_for_url


class ManualVideoImportDialog(QDialog):
    """Modal editor for Title/URL rows, using the existing CSV validation pipeline."""

    TITLE_COLUMN = 0
    URL_COLUMN = 1
    COLUMN_COUNT = 2
    TITLE_WIDTH_RATIO = 0.45

    def __init__(
        self,
        parent=None,
        rows: list[dict[str, object]] | None = None,
        *,
        project_id: str | None = None,
        draft_directory: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nhập danh sách video")
        self.resize(820, 520)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.csv_text = ""
        self.rows: list[dict[str, object]] = []
        self._project_id = project_id or "default"
        base = Path(draft_directory) if draft_directory else Path(
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation
            )
        )
        self._draft_path = base / "manual-video-drafts" / f"{self._project_id}.json"
        self._restoring = True
        self._draft_corrupt = False
        self._save_requested = False
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(400)
        self._autosave.timeout.connect(self.save_draft)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, self.COLUMN_COUNT, self)
        self.table.setHorizontalHeaderLabels(("Title", "URL"))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(self.TITLE_COLUMN, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(self.URL_COLUMN, QHeaderView.ResizeMode.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, 1)

        row_actions = QHBoxLayout()
        self.add_row_button = QPushButton("＋ Thêm dòng")
        self.delete_row_button = QPushButton("Xóa dòng")
        self.add_row_button.clicked.connect(self.add_row)
        self.delete_row_button.clicked.connect(self.delete_rows)
        self.draft_status_label = QLabel("Bản nháp: Tự động lưu")
        row_actions.addWidget(self.add_row_button)
        row_actions.addWidget(self.delete_row_button)
        row_actions.addStretch()
        row_actions.addWidget(self.draft_status_label)
        layout.addLayout(row_actions)

        footer = QHBoxLayout()
        self.cancel_button = QPushButton("Hủy")
        self.save_button = QPushButton("Lưu")
        self.save_button.setObjectName("primary")
        self.cancel_button.clicked.connect(self._discard_draft)
        self.save_button.clicked.connect(self.save_rows)
        footer.addStretch()
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.save_button)
        layout.addLayout(footer)

        QShortcut(QKeySequence("F11"), self, activated=self._toggle_maximized)
        paste_shortcut = QShortcut(QKeySequence.StandardKey.Paste, self.table)
        paste_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        paste_shortcut.activated.connect(self.paste_from_clipboard)

        draft_rows = self.restore_draft()
        source_rows = draft_rows if draft_rows is not None else rows
        if source_rows:
            for row in source_rows:
                self.add_row(schedule=False)
                current = self.table.rowCount() - 1
                self.table.item(current, self.TITLE_COLUMN).setText(
                    str(row.get("ten_video") or row.get("title") or "")
                )
                self.table.item(current, self.URL_COLUMN).setText(
                    str(row.get("url") or "")
                )
        else:
            self.add_row(schedule=False)
        self._apply_column_widths()
        self._restoring = False
        if draft_rows is not None:
            self.draft_status_label.setText("Bản nháp: Đã khôi phục")
            self._show_draft_restored_notice()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_column_widths()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_column_widths()

    def _apply_column_widths(self) -> None:
        viewport_width = self.table.viewport().width()
        if viewport_width < 200:
            viewport_width = max(self.width() - 60, 400)
        title_width = max(int(viewport_width * self.TITLE_WIDTH_RATIO), 160)
        self.table.setColumnWidth(self.TITLE_COLUMN, title_width)

    def _show_draft_restored_notice(self) -> None:
        QMessageBox.information(self, "Bản nháp", "Đã khôi phục bản nháp chưa lưu.")

    def restore_draft(self) -> list[dict[str, str]] | None:
        if not self._draft_path.is_file():
            return None
        try:
            payload = json.loads(self._draft_path.read_text(encoding="utf-8"))
            rows = payload.get("rows") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError("Thiếu danh sách dòng")
            return [
                {
                    "ten_video": str(row.get("ten_video") or row.get("title") or ""),
                    "url": str(row.get("url") or ""),
                }
                for row in rows
                if isinstance(row, dict)
            ]
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            self._draft_corrupt = True
            self.draft_status_label.setText("Bản nháp: Lỗi đọc file")
            QMessageBox.warning(
                self,
                "Không thể khôi phục bản nháp",
                "Bản nháp bị lỗi và được giữ nguyên để kiểm tra.",
            )
            return None

    _load_draft = restore_draft

    def serialize_draft(self) -> dict[str, object]:
        return {
            "rows": [
                {
                    "ten_video": self._cell_text(row, self.TITLE_COLUMN),
                    "url": self._cell_text(row, self.URL_COLUMN),
                }
                for row in range(self.table.rowCount())
            ],
        }

    _draft_payload = serialize_draft

    def _cell_text(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return item.text() if item is not None else ""

    def _ensure_cell_item(self, row: int, column: int) -> QTableWidgetItem:
        item = self.table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            self.table.setItem(row, column, item)
        return item

    def _schedule_draft_save(self) -> None:
        if not self._restoring:
            self.draft_status_label.setText("Bản nháp: Đang lưu...")
            self._autosave.start()

    def save_draft(self) -> None:
        if self._restoring or self._draft_corrupt:
            return
        payload = json.dumps(self.serialize_draft(), ensure_ascii=False).encode("utf-8")
        try:
            self._draft_path.parent.mkdir(parents=True, exist_ok=True)
            output = QSaveFile(str(self._draft_path))
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(output.errorString())
            if output.write(payload) != len(payload) or not output.commit():
                raise OSError(output.errorString())
            self.draft_status_label.setText("Bản nháp: Đã lưu tự động")
        except OSError:
            # Keep editing responsive; the next change retries the atomic write.
            return

    _save_draft = save_draft

    def clear_draft(self) -> None:
        self._autosave.stop()
        try:
            self._draft_path.unlink(missing_ok=True)
            self.draft_status_label.setText("Bản nháp: Đã xóa")
        except OSError:
            pass

    def _on_item_changed(self, _item: QTableWidgetItem) -> None:
        self._schedule_draft_save()

    def _toggle_maximized(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def reject(self) -> None:
        self._autosave.stop()
        self.save_draft()
        super().reject()

    def closeEvent(self, event) -> None:
        self._autosave.stop()
        self.save_draft()
        super().closeEvent(event)

    def add_row(self, *, schedule: bool = True) -> None:
        row = self.table.rowCount()
        with QSignalBlocker(self.table):
            self.table.insertRow(row)
            self.table.setItem(row, self.TITLE_COLUMN, QTableWidgetItem())
            self.table.setItem(row, self.URL_COLUMN, QTableWidgetItem())
        self.table.setCurrentCell(row, self.TITLE_COLUMN)
        if schedule:
            self._schedule_draft_save()

    @staticmethod
    def _looks_like_url(value: str) -> bool:
        cleaned = value.strip()
        if not cleaned or any(ch.isspace() for ch in cleaned):
            return False
        lowered = cleaned.lower()
        return (
            lowered.startswith(("http://", "https://"))
            or provider_for_url(cleaned) is not None
        )

    @classmethod
    def _parse_clipboard_rows(cls, text: str) -> list[list[str]]:
        if not text or not text.strip():
            return []
        try:
            parsed = list(csv.reader(io.StringIO(text), delimiter="\t"))
        except csv.Error:
            parsed = [line.split("\t") for line in text.splitlines()]
        rows: list[list[str]] = []
        for raw_row in parsed:
            if not raw_row or not any(cell.strip() for cell in raw_row):
                continue
            cells = [cell.strip() for cell in raw_row]
            rows.append(cells)
        if len(rows) >= 2 and len(rows[0]) >= 2:
            lowered = [cell.lower() for cell in rows[0][:3]]
            if lowered[:2] in (["title", "url"], ["ten_video", "url"]) or lowered[:3] in (
                ["stt", "title", "url"],
                ["sequence", "title", "url"],
                ["stt", "ten_video", "url"],
            ):
                rows = rows[1:]
        return rows

    def paste_from_clipboard(self, text: str | None = None) -> int:
        raw_text = (
            QApplication.clipboard().text() if text is None else str(text)
        )
        rows = self._parse_clipboard_rows(raw_text)
        if not rows:
            return 0
        start_row = self.table.currentRow()
        start_column = self.table.currentColumn()
        if start_row < 0:
            start_row = max(self.table.rowCount() - 1, 0)
        if start_column < 0:
            start_column = self.TITLE_COLUMN

        has_multi_column = any(len(row) > 1 for row in rows)
        all_single_urls = not has_multi_column and all(
            len(row) == 1 and self._looks_like_url(row[0]) for row in rows
        )

        with QSignalBlocker(self.table):
            while self.table.rowCount() < start_row + len(rows):
                new_row = self.table.rowCount()
                self.table.insertRow(new_row)
                self.table.setItem(new_row, self.TITLE_COLUMN, QTableWidgetItem())
                self.table.setItem(new_row, self.URL_COLUMN, QTableWidgetItem())

            for offset, cells in enumerate(rows):
                target_row = start_row + offset
                if len(cells) == 1:
                    target_column = (
                        self.URL_COLUMN if all_single_urls else start_column
                    )
                    self._ensure_cell_item(target_row, target_column).setText(cells[0])
                elif len(cells) >= 3 and cells[0].isdigit() and self._looks_like_url(cells[2]):
                    self._ensure_cell_item(target_row, self.TITLE_COLUMN).setText(cells[1])
                    self._ensure_cell_item(target_row, self.URL_COLUMN).setText(cells[2])
                else:
                    self._ensure_cell_item(target_row, self.TITLE_COLUMN).setText(cells[0])
                    self._ensure_cell_item(target_row, self.URL_COLUMN).setText(cells[1])

        last_row = start_row + len(rows) - 1
        last_column = (
            self.URL_COLUMN
            if (all_single_urls or has_multi_column)
            else start_column
        )
        self.table.setCurrentCell(last_row, last_column)
        self._schedule_draft_save()
        return len(rows)

    def delete_rows(self) -> None:
        selection = self.table.selectionModel()
        rows = sorted(
            {index.row() for index in selection.selectedIndexes()} if selection else set(),
            reverse=True,
        )
        if not rows and self.table.currentRow() >= 0:
            rows = [self.table.currentRow()]
        for row in rows:
            self.table.removeRow(row)
        if rows:
            self._schedule_draft_save()

    def _has_draft_data(self) -> bool:
        return any(
            self._cell_text(row, column).strip()
            for row in range(self.table.rowCount())
            for column in (self.TITLE_COLUMN, self.URL_COLUMN)
        )

    def _discard_draft(self) -> None:
        if self._has_draft_data():
            result = QMessageBox.question(
                self,
                "Xóa bản nháp",
                "Bạn có muốn xóa bản nháp hiện tại? Dữ liệu chưa lưu sẽ bị mất.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                return
        self.clear_draft()
        super().reject()

    def save_rows(self) -> None:
        if self._save_requested:
            return
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(("stt", "title", "url"))
        for row in range(self.table.rowCount()):
            title = self._cell_text(row, self.TITLE_COLUMN).strip()
            url = self._cell_text(row, self.URL_COLUMN).strip()
            if not title and not url:
                continue
            writer.writerow((row + 1, title, url))

        csv_text = output.getvalue()
        rows, errors = parse_video_csv(csv_text)
        if errors:
            QMessageBox.warning(self, "Dữ liệu không hợp lệ", "\n".join(errors[:20]))
            return
        if not rows:
            QMessageBox.information(self, "Chưa có dữ liệu", "Hãy thêm ít nhất một dòng Title và URL.")
            return
        self.csv_text = csv_text
        self.rows = rows
        self._save_requested = True
        self.accept()


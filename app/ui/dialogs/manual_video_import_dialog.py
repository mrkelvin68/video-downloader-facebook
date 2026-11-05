from __future__ import annotations

import csv
import io

from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.services.csv_service import parse_video_csv


class ManualVideoImportDialog(QDialog):
    """Modal editor for Title/URL rows, using the existing CSV validation pipeline."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nhập danh sách video")
        self.resize(820, 520)
        self.csv_text = ""
        self.rows: list[dict[str, object]] = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Nhập Title và URL Facebook. STT sẽ được tự động tăng theo thứ tự dòng."))
        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(("Title", "URL"))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)

        row_actions = QHBoxLayout()
        add = QPushButton("＋ Thêm dòng")
        delete = QPushButton("Xóa dòng")
        add.clicked.connect(self.add_row)
        delete.clicked.connect(self.delete_rows)
        row_actions.addWidget(add)
        row_actions.addWidget(delete)
        row_actions.addStretch()
        layout.addLayout(row_actions)

        footer = QHBoxLayout()
        cancel = QPushButton("Hủy")
        save = QPushButton("Lưu")
        save.setObjectName("primary")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.save_rows)
        footer.addStretch()
        footer.addWidget(cancel)
        footer.addWidget(save)
        layout.addLayout(footer)
        self.add_row()

    def add_row(self) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem())
        self.table.setItem(row, 1, QTableWidgetItem())
        self.table.setCurrentCell(row, 0)

    def delete_rows(self) -> None:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def save_rows(self) -> None:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(("stt", "title", "url"))
        for row in range(self.table.rowCount()):
            title = self.table.item(row, 0).text().strip() if self.table.item(row, 0) else ""
            url = self.table.item(row, 1).text().strip() if self.table.item(row, 1) else ""
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
        self.accept()

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)


class CsvPreviewModel(QAbstractTableModel):
    HEADERS = ("STT", "Mã video dự kiến", "Tên video", "URL nguồn")

    def __init__(
        self, rows: list[dict], project_prefix: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._rows = list(rows)
        self._project_prefix = project_prefix

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        sequence = row["stt"]
        url = str(row["url"])
        if role == Qt.ItemDataRole.ToolTipRole:
            if index.column() == 3:
                return url
            if index.column() == 1:
                return f"{self._project_prefix}-{sequence}"
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        values = (
            sequence,
            f"{self._project_prefix}-{sequence}",
            row["ten_video"],
            self._compact_url(url),
        )
        return str(values[index.column()])

    @staticmethod
    def _compact_url(url: str) -> str:
        if len(url) <= 68:
            return url
        return f"{url[:42]}…{url[-22:]}"

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class CsvPreviewDialog(QDialog):
    def __init__(
        self,
        filename: str,
        rows: list[dict],
        errors: list[str],
        project_prefix: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Xem trước CSV")
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.WindowModal)

        screen = (parent.screen() if parent is not None else None) or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        screen_width = available.width() if available is not None else 1366
        screen_height = available.height() if available is not None else 768
        dialog_width = min(1100, max(600, int(screen_width * 0.9)))
        max_height = max(280, int(screen_height * 0.82))
        visible_rows = min(len(rows), 14)
        desired_height = 230 + visible_rows * 36 + (100 if errors else 0)
        dialog_height = min(max_height, max(300, desired_height))
        self.resize(dialog_width, dialog_height)
        self.setMaximumSize(dialog_width, max_height)
        self.setMinimumSize(min(600, dialog_width), min(300, dialog_height))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        heading = QHBoxLayout()
        title = QLabel("Xem trước danh sách video")
        title.setObjectName("cardTitle")
        heading.addWidget(title)
        heading.addStretch()
        layout.addLayout(heading)

        summary = QLabel(
            f"{filename} · Prefix: {project_prefix} · "
            f"{len(rows)} dòng hợp lệ · {len(errors)} lỗi"
        )
        summary.setObjectName("detailMeta")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        self.table = QTableView(self)
        self.table.setObjectName("csvPreviewTable")
        self.model = CsvPreviewModel(rows, project_prefix, self.table)
        self.table.setModel(self.model)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(36)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        if errors:
            error_view = QPlainTextEdit(self)
            error_view.setObjectName("csvPreviewErrors")
            error_view.setReadOnly(True)
            error_view.setMaximumHeight(96)
            error_view.setPlainText("\n".join(errors))
            layout.addWidget(error_view)

        footer = QFrame(self)
        footer.setObjectName("dialogFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 8, 10, 8)
        footer_layout.addStretch()
        cancel = QPushButton("Hủy")
        confirm = QPushButton("Xác nhận import")
        confirm.setObjectName("primary")
        confirm.setEnabled(bool(rows) and not errors)
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self.accept)
        footer_layout.addWidget(cancel)
        footer_layout.addWidget(confirm)
        layout.addWidget(footer)

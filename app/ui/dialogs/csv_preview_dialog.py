from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.widgets.video_table import VideoTable


class CsvPreviewDialog(QDialog):
    def __init__(
        self,
        filename: str,
        rows: list[dict],
        errors: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Xem trước CSV")
        self.resize(900, 520)
        layout = QVBoxLayout(self)
        status = QLabel(f"{filename}: {len(rows)} dòng hợp lệ · {len(errors)} lỗi")
        status.setObjectName("cardTitle")
        layout.addWidget(status)
        table = VideoTable(self)
        table.set_videos(rows)
        layout.addWidget(table, 1)
        if errors:
            error_label = QLabel("\n".join(errors))
            error_label.setObjectName("error")
            error_label.setWordWrap(True)
            layout.addWidget(error_label)
        footer = QHBoxLayout()
        footer.addStretch()
        close = QPushButton("Đóng")
        close.setObjectName("primary")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)

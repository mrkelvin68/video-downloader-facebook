from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from app.models.project import Project
from app.ui.widgets.video_table import VideoTable
from app.ui.widgets.sidebar import button_icon


class ProjectDetailPage(QWidget):
    backRequested = Signal()
    csvChosen = Signal()
    csvDropped = Signal(object)
    sampleRequested = Signal()
    exportRequested = Signal()
    importRequested = Signal()
    downloadRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        back = QPushButton("Quay lại danh sách dự án")
        back.setObjectName("back")
        back.setIcon(button_icon(self, QStyle.StandardPixmap.SP_ArrowBack))
        back.clicked.connect(self.backRequested.emit)
        layout.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)
        self.detail_title = QLabel()
        self.detail_title.setObjectName("title")
        self.detail_meta = QLabel()
        self.detail_meta.setObjectName("detailMeta")
        layout.addSpacing(12)
        layout.addWidget(self.detail_title)
        layout.addWidget(self.detail_meta)
        layout.addSpacing(22)
        steps = QHBoxLayout()
        steps.setSpacing(12)
        for index, label in enumerate(("Tạo dự án", "Import CSV", "Tải video", "Hoàn tất"), 1):
            step = QFrame()
            step.setObjectName("stepActive" if index == 2 else "step")
            step_layout = QVBoxLayout(step)
            step_layout.setContentsMargins(16, 15, 16, 15)
            number = QLabel(str(index))
            number.setObjectName("stepNumberActive" if index == 2 else "stepNumber")
            number.setAlignment(Qt.AlignmentFlag.AlignCenter)
            text = QLabel(label)
            text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            text.setObjectName("stepText")
            step_layout.addWidget(number)
            step_layout.addWidget(text)
            steps.addWidget(step, 1)
        layout.addLayout(steps)
        layout.addSpacing(18)
        import_box = QFrame()
        import_box.setObjectName("detailNotice")
        import_layout = QVBoxLayout(import_box)
        section_title = QLabel("Bước 2: Import danh sách video từ CSV")
        section_title.setObjectName("cardTitle")
        import_layout.addWidget(section_title)
        import_layout.addWidget(
            QLabel("CSV gồm các cột stt, ten_video, url (có hỗ trợ sequence, title).")
        )
        self.drop_zone = _DropZone()
        self.drop_zone.setObjectName("dropZone")
        self.drop_zone.setMinimumHeight(74)
        self.drop_zone.fileDropped.connect(self.csvDropped)
        import_layout.addWidget(self.drop_zone)
        actions = QHBoxLayout()
        choose = QPushButton("Chọn CSV")
        choose.setObjectName("primary")
        choose.clicked.connect(self.csvChosen.emit)
        sample = QPushButton("Tải CSV mẫu")
        sample.setObjectName("secondary")
        sample.clicked.connect(self.sampleRequested.emit)
        export = QPushButton("Xuất CSV")
        export.setObjectName("secondary")
        export.clicked.connect(self.exportRequested.emit)
        self.confirm = QPushButton("Xác nhận import")
        self.confirm.setObjectName("primary")
        self.confirm.setEnabled(False)
        self.confirm.clicked.connect(self.importRequested.emit)
        actions.addWidget(choose)
        actions.addWidget(sample)
        actions.addWidget(export)
        actions.addStretch()
        actions.addWidget(self.confirm)
        import_layout.addLayout(actions)
        self.csv_summary = QLabel("Chưa chọn tệp CSV")
        self.csv_summary.setObjectName("detailMeta")
        import_layout.addWidget(self.csv_summary)
        self.csv_errors = QLabel()
        self.csv_errors.setObjectName("error")
        self.csv_errors.setWordWrap(True)
        self.csv_errors.hide()
        import_layout.addWidget(self.csv_errors)
        self.video_table = VideoTable(self)
        import_layout.addWidget(self.video_table, 1)
        download_actions = QHBoxLayout()
        self.download_button = QPushButton("Tải video đã chọn")
        self.download_button.setObjectName("primary")
        self.download_button.clicked.connect(self.downloadRequested.emit)
        self.download_status = QLabel("Chưa tải")
        download_actions.addWidget(self.download_button)
        download_actions.addWidget(self.download_status, 1)
        layout.addWidget(import_box, 1)
        layout.addLayout(download_actions)

    def set_project(self, project: Project) -> None:
        self.detail_title.setText(project.name)
        self.detail_meta.setText(
            f"Prefix video: {project.prefix}    |    Thư mục: {project.output_dir}"
        )

    def set_saved_videos(self, videos: list[dict]) -> None:
        self.video_table.set_videos(videos)
        self.csv_errors.clear()
        self.csv_errors.hide()
        if videos:
            self.csv_summary.setText(f"Hiện có {len(videos)} video đã lưu trong dự án.")
        else:
            self.csv_summary.setText(
                "Chưa có danh sách video trong dự án. Kéo thả hoặc chọn tệp CSV."
            )
        self.confirm.setEnabled(False)

    def show_preview(
        self, filename: str, row_count: int, errors: list[str]
    ) -> None:
        self.csv_summary.setText(
            f"{filename}: {row_count} dòng hợp lệ · {len(errors)} lỗi (chưa lưu)"
        )
        if errors:
            self.csv_errors.setText("\n".join(errors))
            self.csv_errors.show()
        else:
            self.csv_errors.clear()
            self.csv_errors.hide()
        self.confirm.setEnabled(row_count > 0 and not errors)

class _DropZone(QLabel):
    fileDropped = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Kéo thả CSV vào đây hoặc chọn tệp", parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls:
            self.fileDropped.emit(Path(urls[0].toLocalFile()))
            event.acceptProposedAction()
        else:
            event.ignore()

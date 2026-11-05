from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from app.models.project import Project
from app.ui.widgets.video_table import VideoTable
from app.ui.widgets.sidebar import button_icon
from app.ui.widgets.loading_button import LoadingButton


class ProjectDetailPage(QWidget):
    backRequested = Signal()
    csvChosen = Signal()
    csvDropped = Signal(object)
    googleSheetsRequested = Signal()
    manualImportRequested = Signal()
    sampleRequested = Signal()
    exportRequested = Signal()
    runNowRequested = Signal()
    enqueueRequested = Signal()
    queueRequested = Signal()
    selectAllRequested = Signal()
    clearSelectionRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 20, 34, 18)
        back = QPushButton("Quay lại danh sách dự án")
        back.setObjectName("back")
        back.setIcon(button_icon(self, QStyle.StandardPixmap.SP_ArrowBack))
        back.clicked.connect(self.backRequested.emit)
        layout.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)
        self.detail_title = QLabel()
        self.detail_title.setObjectName("title")
        self.detail_meta = QLabel()
        self.detail_meta.setObjectName("detailMeta")
        layout.addSpacing(6)
        layout.addWidget(self.detail_title)
        layout.addWidget(self.detail_meta)
        layout.addSpacing(10)
        steps = QHBoxLayout()
        steps.setSpacing(12)
        for index, label in enumerate(("Tạo dự án", "Import CSV", "Tải video", "Hoàn tất"), 1):
            step = QFrame()
            step.setObjectName("stepActive" if index == 2 else "step")
            step_layout = QVBoxLayout(step)
            step_layout.setContentsMargins(12, 8, 12, 8)
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
        layout.addSpacing(8)
        import_box = QFrame()
        import_box.setObjectName("detailNotice")
        import_layout = QVBoxLayout(import_box)
        section_title = QLabel("Bước 2: Import danh sách video từ CSV")
        section_title.setObjectName("cardTitle")
        import_layout.addWidget(section_title)
        import_layout.addWidget(
            QLabel("CSV gồm các cột stt, title, url (có hỗ trợ tên cũ ten_video).")
        )
        self.drop_zone = _DropZone()
        self.drop_zone.setObjectName("dropZone")
        self.drop_zone.setMinimumHeight(58)
        self.drop_zone.fileDropped.connect(self.csvDropped)
        self.drop_zone.clicked.connect(self.csvChosen)
        import_layout.addWidget(self.drop_zone)
        actions = QHBoxLayout()
        choose = QPushButton("Chọn CSV")
        choose.setObjectName("primary")
        choose.clicked.connect(self.csvChosen.emit)
        sheets = QPushButton("Nhập Google Sheets")
        sheets.setObjectName("secondary")
        sheets.clicked.connect(self.googleSheetsRequested.emit)
        sample = QPushButton("Tải CSV mẫu")
        sample.setObjectName("secondary")
        sample.clicked.connect(self.sampleRequested.emit)
        self.manual_import_button = QPushButton("Nhập danh sách video")
        self.manual_import_button.setObjectName("primary")
        self.manual_import_button.clicked.connect(self.manualImportRequested.emit)
        export = QPushButton("Xuất CSV")
        export.setObjectName("secondary")
        export.clicked.connect(self.exportRequested.emit)
        actions.addWidget(choose)
        actions.addWidget(sheets)
        actions.addWidget(sample)
        actions.addWidget(self.manual_import_button)
        actions.addWidget(export)
        actions.addStretch()
        import_layout.addLayout(actions)
        self.csv_summary = QLabel("Chưa chọn tệp CSV")
        self.csv_summary.setObjectName("detailMeta")
        import_layout.addWidget(self.csv_summary)
        self.csv_errors = QLabel()
        self.csv_errors.setObjectName("error")
        self.csv_errors.setWordWrap(True)
        self.csv_errors.hide()
        import_layout.addWidget(self.csv_errors)
        video_actions = QHBoxLayout()
        video_actions.addWidget(QLabel("Lọc trạng thái:"))
        self.status_filter = QComboBox()
        self.status_filter.addItems(("Tất cả", "Đã tải", "Chưa tải"))
        video_actions.addWidget(self.status_filter)
        select_all = QPushButton("Chọn tất cả")
        select_all.setObjectName("secondary")
        select_all.clicked.connect(self.selectAllRequested.emit)
        clear_selection = QPushButton("Bỏ chọn tất cả")
        clear_selection.setObjectName("secondary")
        clear_selection.clicked.connect(self.clearSelectionRequested.emit)
        video_actions.addWidget(select_all)
        video_actions.addWidget(clear_selection)
        video_actions.addStretch()
        import_layout.addLayout(video_actions)
        self.video_table = VideoTable(self)
        self.status_filter.currentTextChanged.connect(self.video_table.set_status_filter)
        import_layout.addWidget(self.video_table, 1)
        download_actions = QHBoxLayout()
        self.run_now_button = LoadingButton("▶ Chạy ngay")
        self.run_now_button.setObjectName("primary")
        self.run_now_button.clicked.connect(self.runNowRequested.emit)
        self.enqueue_button = QPushButton("＋ Thêm vào hàng đợi")
        self.enqueue_button.setObjectName("secondary")
        self.enqueue_button.clicked.connect(self.enqueueRequested.emit)
        self.queue_button = QPushButton("Xem hàng đợi")
        self.queue_button.setObjectName("secondary")
        self.queue_button.clicked.connect(self.queueRequested.emit)
        self.queue_status = QLabel("Hàng đợi đang dừng")
        self.queue_status.setObjectName("detailMeta")
        download_actions.addWidget(self.run_now_button)
        download_actions.addWidget(self.enqueue_button)
        download_actions.addWidget(self.queue_button)
        download_actions.addWidget(self.queue_status, 1)
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

    def discard_preview(self) -> None:
        self.csv_summary.setText(
            "Đã hủy xem trước CSV. Dữ liệu đã lưu không thay đổi."
        )
        self.csv_errors.clear()
        self.csv_errors.hide()


class _DropZone(QLabel):
    fileDropped = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Kéo thả CSV vào đây hoặc chọn tệp", parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    clicked = Signal()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

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

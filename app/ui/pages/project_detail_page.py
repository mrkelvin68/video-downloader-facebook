from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QStyle,
    QTabWidget,
    QTextEdit,
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
    projectEditRequested = Signal()
    facebookConnectRequested = Signal(str)
    facebookTestRequested = Signal()
    facebookRefreshRequested = Signal()
    facebookDisconnectRequested = Signal()
    facebookDefaultPageRequested = Signal(str)
    facebookSaveDraftRequested = Signal(object)
    facebookPrepareRequested = Signal(object)
    facebookPublishNowRequested = Signal(object)

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
        import_box = QFrame()
        import_box.setObjectName("detailNotice")
        import_layout = QVBoxLayout(import_box)
        section_title = QLabel("Quản lý danh sách video")
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
        tabs = QTabWidget()
        tabs.setObjectName("projectTabs")
        project_tab = QWidget()
        project_layout = QVBoxLayout(project_tab)
        project_layout.addWidget(QLabel("Thông tin cấu hình dự án sẽ được giữ nguyên khi quay lại workspace."))
        self.project_config = QLabel()
        self.project_config.setObjectName("detailMeta")
        self.project_config.setWordWrap(True)
        project_layout.addWidget(self.project_config)
        edit_project = QPushButton("Chỉnh sửa cấu hình dự án")
        edit_project.setObjectName("secondary")
        edit_project.clicked.connect(self.projectEditRequested.emit)
        project_layout.addWidget(edit_project, alignment=Qt.AlignmentFlag.AlignLeft)
        project_layout.addStretch()
        publish_tab = QWidget()
        publish_layout = QVBoxLayout(publish_tab)
        self._build_facebook_editor(publish_layout)
        tabs.addTab(project_tab, "Thông tin dự án")
        tabs.addTab(import_box, "Quản lý video")
        tabs.addTab(publish_tab, "Đăng Facebook")
        import_layout.addLayout(download_actions)
        layout.addWidget(tabs, 1)

    def _build_facebook_editor(self, layout: QVBoxLayout) -> None:
        connection = QFrame(); connection.setObjectName("detailNotice")
        connection_layout = QVBoxLayout(connection)
        connection_layout.addWidget(QLabel("FACEBOOK CONNECTION"))
        self.facebook_status = QLabel("Chưa kết nối Facebook")
        self.facebook_status.setObjectName("detailMeta"); connection_layout.addWidget(self.facebook_status)
        form = QFormLayout()
        self.facebook_token = QLineEdit(); self.facebook_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.facebook_token.setPlaceholderText("Nhập User/Page Access Token"); form.addRow("Access Token:", self.facebook_token)
        self.facebook_page_combo = QComboBox(); self.facebook_page_combo.currentIndexChanged.connect(self._default_page_changed)
        form.addRow("Fanpage mặc định:", self.facebook_page_combo); connection_layout.addLayout(form)
        actions = QHBoxLayout()
        self.facebook_connect_button = LoadingButton("Kết nối Facebook"); self.facebook_connect_button.clicked.connect(lambda: self.facebookConnectRequested.emit(self.facebook_token.text()))
        self.facebook_test_button = LoadingButton("Kiểm tra"); self.facebook_test_button.clicked.connect(self.facebookTestRequested.emit)
        self.facebook_refresh_button = LoadingButton("Làm mới Fanpage"); self.facebook_refresh_button.clicked.connect(self.facebookRefreshRequested.emit)
        disconnect = QPushButton("Ngắt kết nối"); disconnect.setObjectName("danger"); disconnect.clicked.connect(self.facebookDisconnectRequested.emit)
        for button in (self.facebook_connect_button, self.facebook_test_button, self.facebook_refresh_button, disconnect): actions.addWidget(button)
        actions.addStretch(); connection_layout.addLayout(actions); layout.addWidget(connection)
        self.facebook_table = QTableWidget(0, 9)
        self._facebook_selected_video_id: str | None = None
        self.facebook_table.setHorizontalHeaderLabels(("", "STT", "Mã video", "Tên video", "File local", "Fanpage", "Chuẩn bị", "Đăng", "Link Facebook"))
        self.facebook_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.facebook_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.facebook_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        header = self.facebook_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, header.ResizeMode.Stretch)
        header.setSectionResizeMode(4, header.ResizeMode.Stretch)
        header.setSectionResizeMode(5, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(8, header.ResizeMode.Stretch)
        self.facebook_table.itemSelectionChanged.connect(self._load_selected_facebook_draft); layout.addWidget(self.facebook_table, 1)
        editor = QFrame(); editor.setObjectName("detailNotice"); editor_layout = QVBoxLayout(editor)
        editor_layout.addWidget(QLabel("NỘI DUNG ĐĂNG")); editor_form = QFormLayout()
        self.facebook_video_title = QLabel("-"); editor_form.addRow("Video title:", self.facebook_video_title)
        self.facebook_title = QLineEdit(); editor_form.addRow("Facebook title:", self.facebook_title)
        self.facebook_description = QTextEdit(); self.facebook_description.setMinimumHeight(70); editor_form.addRow("Caption / Description:", self.facebook_description)
        self.facebook_hashtags = QLineEdit(); editor_form.addRow("Hashtags:", self.facebook_hashtags)
        self.facebook_local_path = QLabel("-"); editor_form.addRow("File local:", self.facebook_local_path); editor_layout.addLayout(editor_form)
        editor_actions = QHBoxLayout()
        save = QPushButton("Lưu nội dung"); save.clicked.connect(self._save_facebook_draft)
        restore = QPushButton("Khôi phục nội dung đã lưu"); restore.clicked.connect(self._load_selected_facebook_draft)
        self.facebook_prepare_button = LoadingButton("Kiểm tra video"); self.facebook_prepare_button.clicked.connect(self._prepare_selected_video)
        self.facebook_publish_button = LoadingButton("Đăng ngay")
        self.facebook_publish_button.setObjectName("primary")
        self.facebook_publish_button.setEnabled(False)
        self.facebook_publish_button.clicked.connect(self._publish_selected_video)
        for button in (save, restore, self.facebook_prepare_button, self.facebook_publish_button): editor_actions.addWidget(button)
        editor_actions.addStretch(); editor_layout.addLayout(editor_actions); layout.addWidget(editor)

    def _default_page_changed(self, index: int) -> None:
        self._update_publish_button()
        if index >= 0:
            self.facebookDefaultPageRequested.emit(str(self.facebook_page_combo.itemData(index) or ""))

    def _selected_facebook_row(self) -> int:
        rows = self.facebook_table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _save_facebook_draft(self) -> None:
        row = self._selected_facebook_row()
        if row >= 0:
            item = self.facebook_table.item(row, 2)
            video_id = item.data(Qt.ItemDataRole.UserRole) if item else None
            if video_id:
                self.facebookSaveDraftRequested.emit({"video_id": str(video_id), "title": self.facebook_title.text(), "description": self.facebook_description.toPlainText(), "hashtags": self.facebook_hashtags.text(), "page_id": self.facebook_page_combo.currentData()})

    def _prepare_selected_video(self) -> None:
        row = self._selected_facebook_row()
        if row >= 0:
            item = self.facebook_table.item(row, 2)
            self.facebookPrepareRequested.emit({"row": row, "video_id": item.data(Qt.ItemDataRole.UserRole)})

    def _publish_selected_video(self) -> None:
        row = self._selected_facebook_row()
        if row < 0:
            return
        item = self.facebook_table.item(row, 2)
        video_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if video_id:
            self.facebookPublishNowRequested.emit({"video_id": str(video_id), "page_id": self.facebook_page_combo.currentData()})

    def _load_selected_facebook_draft(self) -> None:
        row = self._selected_facebook_row()
        if row < 0: return
        video_item = self.facebook_table.item(row, 2)
        video_id = video_item.data(Qt.ItemDataRole.UserRole) if video_item else None
        if not video_id:
            return
        self._facebook_selected_video_id = str(video_id)
        self.facebook_video_title.setText(self.facebook_table.item(row, 3).text())
        self.facebook_local_path.setText(self.facebook_table.item(row, 4).text())
        title_item = self.facebook_table.item(row, 3)
        self.facebook_title.setText(title_item.data(Qt.ItemDataRole.UserRole) or title_item.text())
        self.facebook_description.setPlainText(title_item.data(Qt.ItemDataRole.UserRole + 1) or "")
        self.facebook_hashtags.setText(title_item.data(Qt.ItemDataRole.UserRole + 2) or "")
        self._update_publish_button()

    def set_facebook_data(self, pages: list[dict], drafts: list[dict]) -> None:
        self.facebook_page_combo.blockSignals(True); self.facebook_page_combo.clear()
        for page in pages: self.facebook_page_combo.addItem(str(page.get("page_name", page.get("name", ""))), str(page.get("page_id", page.get("id", ""))))
        self.facebook_page_combo.blockSignals(False); self.facebook_status.setText("Đã kết nối Facebook" if pages else "Chưa kết nối Facebook")
        selected_id = self._facebook_selected_video_id
        self.facebook_table.blockSignals(True)
        self.facebook_table.setRowCount(0)
        for draft in drafts:
            row = self.facebook_table.rowCount(); self.facebook_table.insertRow(row)
            source_title = str(draft.get("video_title") or draft.get("title") or "")
            page_label = draft.get("page_name") or draft.get("page_id") or "-"
            values = ("", str(draft.get("sequence_number", row + 1)), str(draft.get("video_code", "")), source_title, str(draft.get("local_video_path") or "Không tìm thấy file video"), str(page_label), str(draft.get("preparation_status", "NOT_PREPARED")), "Chưa đăng", "")
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 2: item.setData(Qt.ItemDataRole.UserRole, draft.get("video_id"))
                if col == 3:
                    item.setData(Qt.ItemDataRole.UserRole, draft.get("facebook_title") or source_title)
                    item.setData(Qt.ItemDataRole.UserRole + 1, draft.get("description") or "")
                    item.setData(Qt.ItemDataRole.UserRole + 2, draft.get("hashtags") or "")
                self.facebook_table.setItem(row, col, item)
        self.facebook_table.blockSignals(False)
        if selected_id:
            for row in range(self.facebook_table.rowCount()):
                item = self.facebook_table.item(row, 2)
                if item and str(item.data(Qt.ItemDataRole.UserRole)) == selected_id:
                    self.facebook_table.selectRow(row)
                    self._load_selected_facebook_draft()
                    break
        elif self.facebook_table.rowCount():
            self.facebook_table.clearSelection()
        self._update_publish_button()

    def _update_publish_button(self) -> None:
        row = self._selected_facebook_row()
        ready = row >= 0 and bool(self.facebook_page_combo.currentData())
        if ready:
            ready = self.facebook_table.item(row, 6).text() == "READY"
            ready = ready and self.facebook_table.item(row, 4).text() != "Không tìm thấy file video"
        self.facebook_publish_button.setEnabled(ready and not self.facebook_publish_button.is_loading)

    def set_project(self, project: Project) -> None:
        self.detail_title.setText(project.name)
        self.detail_meta.setText(
            f"Prefix video: {project.prefix}    |    Thư mục: {project.output_dir}"
        )
        self.project_config.setText(
            f"Tên dự án: {project.name}\nPrefix video: {project.prefix}\nThư mục đầu ra: {project.output_dir}"
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

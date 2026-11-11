from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QDialog,
    QFormLayout,
    QFileDialog,
    QLineEdit,
    QSizePolicy,
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
    deleteVideosRequested = Signal()
    projectEditRequested = Signal()
    facebookConnectRequested = Signal(str)
    facebookTestRequested = Signal()
    facebookRefreshRequested = Signal()
    facebookDisconnectRequested = Signal()
    facebookDefaultPageRequested = Signal(str)
    facebookSaveDraftRequested = Signal(object)
    facebookPrepareRequested = Signal(object)
    facebookPublishNowRequested = Signal(object)
    facebookJobReconcileRequested = Signal(str)
    facebookJobRetryRequested = Signal(str)
    facebookJobErrorRequested = Signal(str)
    facebookJobCancelRequested = Signal(str)
    facebookJobOpenRequested = Signal(str)
    facebookJobDeleteRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        back = QPushButton("Quay lại danh sách dự án")
        back.setObjectName("back")
        back.setIcon(button_icon(self, QStyle.StandardPixmap.SP_ArrowBack))
        back.clicked.connect(self.backRequested.emit)
        layout.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)
        page_title = QLabel("Quản lý chi tiết dự án")
        page_title.setObjectName("pageTitle")
        layout.addSpacing(10)
        layout.addWidget(page_title)
        self.detail_title = QLabel()
        self.detail_title.setObjectName("projectName")
        self.detail_meta = QLabel()
        self.detail_meta.setObjectName("detailMeta")
        layout.addSpacing(4)
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
            QLabel("CSV gồm các cột title, url (có hỗ trợ tên cũ ten_video).")
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
        video_actions.addWidget(QLabel("Tìm:"))
        self.video_search_field = QComboBox()
        self.video_search_field.addItem("Tên video", "title")
        self.video_search_field.addItem("Mã video", "video_code")
        video_actions.addWidget(self.video_search_field)
        self.video_search = QLineEdit()
        self.video_search.setPlaceholderText("Nhập từ khóa")
        self.video_search.setClearButtonEnabled(True)
        self.video_search.setMaximumWidth(220)
        video_actions.addWidget(self.video_search)
        select_all = QPushButton("Chọn tất cả")
        select_all.setObjectName("secondary")
        select_all.clicked.connect(self.selectAllRequested.emit)
        clear_selection = QPushButton("Bỏ chọn tất cả")
        clear_selection.setObjectName("secondary")
        clear_selection.clicked.connect(self.clearSelectionRequested.emit)
        self.delete_videos_button = QPushButton("Xóa video")
        self.delete_videos_button.setObjectName("danger")
        self.delete_videos_button.clicked.connect(self.deleteVideosRequested.emit)
        video_actions.addWidget(select_all)
        video_actions.addWidget(clear_selection)
        video_actions.addWidget(self.delete_videos_button)
        video_actions.addStretch()
        import_layout.addLayout(video_actions)
        self.video_table = VideoTable(self)
        self.status_filter.currentTextChanged.connect(self.video_table.set_status_filter)
        self.video_search.textChanged.connect(self._apply_video_search)
        self.video_search_field.currentIndexChanged.connect(self._apply_video_search)
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
        self.delete_videos_bottom_button = QPushButton("Xóa video đã chọn")
        self.delete_videos_bottom_button.setObjectName("danger")
        self.delete_videos_bottom_button.clicked.connect(self.deleteVideosRequested.emit)
        self.queue_status = QLabel("Hàng đợi đang dừng")
        self.queue_status.setObjectName("detailMeta")
        download_actions.addWidget(self.run_now_button)
        download_actions.addWidget(self.enqueue_button)
        download_actions.addWidget(self.queue_button)
        download_actions.addWidget(self.delete_videos_bottom_button)
        download_actions.addWidget(self.queue_status, 1)
        tabs = QTabWidget()
        tabs.setObjectName("projectTabs")
        publish_tab = QWidget()
        publish_layout = QVBoxLayout(publish_tab)
        publish_layout.setContentsMargins(10, 14, 10, 0)
        publish_layout.setSpacing(12)
        self._build_facebook_editor(publish_layout)
        # Project configuration remains editable through the existing project-list
        # flow; omit this duplicate tab without changing that backend/edit logic.
        tabs.addTab(import_box, "Quản lý video")
        tabs.addTab(publish_tab, "Đăng Facebook")
        tabs.setCurrentIndex(0)
        import_layout.addLayout(download_actions)
        layout.addWidget(tabs, 1)

    def _apply_video_search(self) -> None:
        self.video_table.set_text_filter(
            str(self.video_search_field.currentData()), self.video_search.text()
        )


    def _build_facebook_editor(self, layout: QVBoxLayout) -> None:
        connection = QFrame(); connection.setObjectName("facebookCard")
        connection_layout = QVBoxLayout(connection)
        connection_layout.setContentsMargins(12, 8, 12, 8)
        connection_layout.setSpacing(6)
        toolbar = QHBoxLayout()
        self.facebook_status = QLabel("Chưa kết nối Facebook")
        self.facebook_status.setObjectName("detailMeta")
        toolbar.addWidget(self.facebook_status)
        toolbar.addWidget(QLabel("Fanpage:"))
        self.facebook_page_combo = QComboBox(); self.facebook_page_combo.currentIndexChanged.connect(self._default_page_changed)
        self.facebook_page_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        toolbar.addWidget(self.facebook_page_combo, 1)
        self.facebook_connection_config_button = QPushButton("Cấu hình kết nối")
        self.facebook_connection_config_button.setObjectName("secondary")
        self.facebook_connection_config_button.clicked.connect(self._toggle_facebook_connection_form)
        toolbar.addWidget(self.facebook_connection_config_button)
        self.facebook_test_button = LoadingButton("Kiểm tra"); self.facebook_test_button.setObjectName("secondary"); self.facebook_test_button.clicked.connect(self.facebookTestRequested.emit)
        self.facebook_refresh_button = LoadingButton("Làm mới Fanpage"); self.facebook_refresh_button.setObjectName("secondary"); self.facebook_refresh_button.clicked.connect(self.facebookRefreshRequested.emit)
        self.facebook_disconnect_button = QPushButton("Ngắt kết nối"); self.facebook_disconnect_button.setObjectName("danger"); self.facebook_disconnect_button.clicked.connect(self.facebookDisconnectRequested.emit)
        for button in (self.facebook_test_button, self.facebook_refresh_button, self.facebook_disconnect_button): toolbar.addWidget(button)
        connection_layout.addLayout(toolbar)
        self.facebook_connection_form = QWidget()
        form = QHBoxLayout(self.facebook_connection_form)
        form.setContentsMargins(0, 0, 0, 0)
        form.addWidget(QLabel("Access Token:"))
        self.facebook_token = QLineEdit(); self.facebook_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.facebook_token.setPlaceholderText("Nhập User/Page Access Token")
        form.addWidget(self.facebook_token, 1)
        self.facebook_connect_button = LoadingButton("Kết nối Facebook"); self.facebook_connect_button.setObjectName("primary"); self.facebook_connect_button.clicked.connect(lambda: self.facebookConnectRequested.emit(self.facebook_token.text()))
        form.addWidget(self.facebook_connect_button)
        connection_layout.addWidget(self.facebook_connection_form)
        layout.addWidget(connection)
        self._set_facebook_connection_state(False)
        self.facebook_table = QTableWidget(0, 10)
        self.facebook_table.setObjectName("facebookVideoTable")
        self.facebook_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._facebook_selected_video_id: str | None = None
        self.facebook_file_paths: dict[str, str] = {}
        self.facebook_jobs_by_video: dict[str, list[dict]] = {}
        self._facebook_pages_cache: list[dict] = []
        self._facebook_drafts_cache: list[dict] = []
        self._facebook_jobs_cache: dict[str, list[dict]] = {}
        self.facebook_table.setHorizontalHeaderLabels(("", "STT", "Mã video", "Tên video", "File local", "Fanpage", "Chuẩn bị", "Đăng", "Link Facebook", "Thao tác"))
        self.facebook_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.facebook_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.facebook_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        header = self.facebook_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, header.ResizeMode.Stretch)
        header.setSectionResizeMode(4, header.ResizeMode.Interactive)
        self.facebook_table.setColumnWidth(4, 220)
        header.setSectionResizeMode(5, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(8, header.ResizeMode.Stretch)
        header.setSectionResizeMode(9, header.ResizeMode.ResizeToContents)
        self.facebook_table.itemSelectionChanged.connect(self._load_selected_facebook_draft)
        facebook_tabs = QTabWidget()
        facebook_tabs.setObjectName("facebookTabs")
        compose_tab = QWidget()
        compose_layout = QVBoxLayout(compose_tab)
        compose_layout.setContentsMargins(0, 12, 0, 0)
        compose_layout.setSpacing(12)
        publishing_toolbar = QHBoxLayout()
        self.facebook_video_total = QLabel("0 video")
        self.facebook_video_total.setObjectName("detailMeta")
        publishing_toolbar.addWidget(self.facebook_video_total)
        publishing_toolbar.addStretch()
        publishing_toolbar.addWidget(QLabel("Chuẩn bị:"))
        self.facebook_prepare_filter = QComboBox()
        self.facebook_prepare_filter.addItem("Tất cả", "")
        self.facebook_prepare_filter.addItem("Chưa chuẩn bị", "NOT_PREPARED")
        self.facebook_prepare_filter.addItem("Sẵn sàng", "READY")
        self.facebook_prepare_filter.currentIndexChanged.connect(self._apply_facebook_filters)
        publishing_toolbar.addWidget(self.facebook_prepare_filter)
        publishing_toolbar.addWidget(QLabel("Đăng:"))
        self.facebook_publish_filter = QComboBox()
        self.facebook_publish_filter.addItem("Tất cả", "")
        self.facebook_publish_filter.addItem("Chưa đăng", "NOT_POSTED")
        self.facebook_publish_filter.addItem("Đang đăng", "PROCESSING")
        self.facebook_publish_filter.addItem("Đã đăng", "PUBLISHED")
        self.facebook_publish_filter.currentIndexChanged.connect(self._apply_facebook_filters)
        publishing_toolbar.addWidget(self.facebook_publish_filter)
        self.facebook_list_refresh_button = QPushButton("Làm mới danh sách")
        self.facebook_list_refresh_button.setObjectName("secondary")
        self.facebook_list_refresh_button.clicked.connect(self._refresh_facebook_table)
        publishing_toolbar.addWidget(self.facebook_list_refresh_button)
        compose_layout.addLayout(publishing_toolbar)
        compose_layout.addWidget(self.facebook_table, 1)
        self.facebook_table.itemDoubleClicked.connect(lambda _item: self._open_facebook_composer())
        editor = QFrame(); editor.setObjectName("facebookCard"); editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(18, 16, 18, 16); editor_layout.setSpacing(10)
        editor_title = QLabel("Soạn nội dung đăng")
        editor_title.setObjectName("facebookSectionTitle")
        editor_layout.addWidget(editor_title)
        self.facebook_video_title = QLabel("-")
        self.facebook_video_title.setObjectName("dialogSubtitle")
        editor_layout.addWidget(self.facebook_video_title)
        editor_form = QFormLayout()
        self.facebook_title = QLineEdit(); self.facebook_title.setPlaceholderText("Nhập tiêu đề hiển thị trên Facebook"); editor_form.addRow("Tiêu đề video", self.facebook_title)
        self.facebook_description = QTextEdit(); self.facebook_description.setMinimumHeight(100); self.facebook_description.setPlaceholderText("Viết nội dung bài đăng..."); editor_form.addRow("Nội dung / Caption", self.facebook_description)
        self.facebook_hashtags = QLineEdit(); self.facebook_hashtags.setPlaceholderText("Ví dụ: #phimai #review"); editor_form.addRow("Thẻ / Hashtags", self.facebook_hashtags)
        editor_layout.addLayout(editor_form)
        file_row = QHBoxLayout()
        self.facebook_local_path = QLabel("-")
        self.facebook_local_path.setObjectName("dialogSubtitle")
        file_row.addWidget(QLabel("File:")); file_row.addWidget(self.facebook_local_path, 1)
        editor_layout.addLayout(file_row)
        editor_actions = QHBoxLayout()
        save = QPushButton("Lưu nội dung"); save.setObjectName("secondary"); save.clicked.connect(self._save_facebook_draft)
        restore = QPushButton("Khôi phục nội dung đã lưu"); restore.setObjectName("secondary"); restore.clicked.connect(self._load_selected_facebook_draft)
        self.facebook_prepare_button = LoadingButton("Kiểm tra video"); self.facebook_prepare_button.setObjectName("secondary"); self.facebook_prepare_button.clicked.connect(self._prepare_selected_video)
        self.facebook_select_file_button = QPushButton("Chọn file đăng")
        self.facebook_select_file_button.setObjectName("secondary")
        self.facebook_select_file_button.clicked.connect(self._select_facebook_file)
        self.facebook_publish_button = LoadingButton("Đăng ngay")
        self.facebook_publish_button.setObjectName("primary")
        self.facebook_publish_button.setEnabled(False)
        self.facebook_publish_button.clicked.connect(self._publish_selected_video)
        for button in (save, restore, self.facebook_select_file_button, self.facebook_prepare_button, self.facebook_publish_button): editor_actions.addWidget(button)
        close_composer = QPushButton("Đóng")
        close_composer.setObjectName("secondary")
        editor_actions.addWidget(close_composer)
        editor_actions.addStretch(); editor_layout.addLayout(editor_actions)
        self.facebook_composer_dialog = QDialog(self)
        self.facebook_composer_dialog.setObjectName("facebookComposerDialog")
        self.facebook_composer_dialog.setWindowTitle("Soạn nội dung đăng Facebook")
        self.facebook_composer_dialog.setModal(True)
        self.facebook_composer_dialog.resize(680, 430)
        close_composer.clicked.connect(self.facebook_composer_dialog.reject)
        dialog_layout = QVBoxLayout(self.facebook_composer_dialog)
        dialog_layout.setContentsMargins(0, 0, 0, 0)
        dialog_layout.addWidget(editor)
        history = QFrame(); history.setObjectName("facebookCard"); history_layout = QVBoxLayout(history)
        history_layout.setContentsMargins(18, 16, 18, 16); history_layout.setSpacing(10)
        history_title = QLabel("Lịch sử đăng Facebook")
        history_title.setObjectName("facebookSectionTitle")
        history_layout.addWidget(history_title)
        self.facebook_job_table = QTableWidget(0, 10)
        self.facebook_job_table.setObjectName("facebookJobTable")
        self.facebook_job_table.setMinimumHeight(220)
        self.facebook_job_table.setHorizontalHeaderLabels(("Job ID", "Video ID", "File", "Fanpage", "Trạng thái", "Upload phase", "Remote Video ID", "Tạo lúc", "Lỗi gần nhất", "URL"))
        self.facebook_job_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.facebook_job_table.itemSelectionChanged.connect(self._update_job_actions)
        history_layout.addWidget(self.facebook_job_table, 1)
        job_actions = QHBoxLayout()
        self.facebook_reconcile_button = QPushButton("Kiểm tra trạng thái Meta")
        self.facebook_reconcile_button.setObjectName("secondary")
        self.facebook_reconcile_button.clicked.connect(self._reconcile_selected_job)
        self.facebook_error_button = QPushButton("Xem lỗi")
        self.facebook_error_button.setObjectName("secondary")
        self.facebook_error_button.clicked.connect(self._show_selected_job_error)
        self.facebook_retry_button = QPushButton("Đăng lại")
        self.facebook_retry_button.setObjectName("secondary")
        self.facebook_retry_button.clicked.connect(self._retry_selected_job)
        self.facebook_cancel_job_button = QPushButton("Hủy job")
        self.facebook_cancel_job_button.setObjectName("danger")
        self.facebook_cancel_job_button.clicked.connect(self._cancel_selected_job)
        self.facebook_open_job_button = QPushButton("Mở bài đăng")
        self.facebook_open_job_button.setObjectName("secondary")
        self.facebook_open_job_button.clicked.connect(self._open_selected_job)
        self.facebook_delete_job_button = QPushButton("Xóa lịch sử")
        self.facebook_delete_job_button.setObjectName("danger")
        self.facebook_delete_job_button.clicked.connect(self._delete_selected_job)
        for button in (self.facebook_reconcile_button, self.facebook_error_button, self.facebook_retry_button, self.facebook_cancel_job_button, self.facebook_open_job_button, self.facebook_delete_job_button): job_actions.addWidget(button)
        job_actions.addStretch(); history_layout.addLayout(job_actions)
        facebook_tabs.addTab(compose_tab, "Soạn & đăng video")
        facebook_tabs.addTab(history, "Lịch sử đăng")
        layout.addWidget(facebook_tabs, 1)
        self._update_job_actions()

    def _set_facebook_connection_state(self, connected: bool) -> None:
        self.facebook_connection_form.setVisible(not connected)
        self.facebook_connection_config_button.setVisible(connected)
        self.facebook_test_button.setVisible(connected)
        self.facebook_refresh_button.setVisible(connected)
        self.facebook_disconnect_button.setVisible(connected)
        self.facebook_connection_config_button.setText("Cấu hình kết nối")

    def _toggle_facebook_connection_form(self) -> None:
        visible = not self.facebook_connection_form.isVisible()
        self.facebook_connection_form.setVisible(visible)
        self.facebook_connection_config_button.setText(
            "Ẩn cấu hình" if visible else "Cấu hình kết nối"
        )

    @staticmethod
    def _facebook_preparation_label(status: str) -> str:
        return {
            "READY": "Sẵn sàng",
            "PREPARING": "Đang chuẩn bị",
            "FAILED": "Lỗi chuẩn bị",
            "NOT_PREPARED": "Chưa chuẩn bị",
        }.get(status, status or "Chưa chuẩn bị")

    @staticmethod
    def _facebook_publish_label(status: str) -> str:
        return {
            "PUBLISHED": "Đã đăng",
            "PROCESSING": "Đang đăng",
            "PUBLISHING": "Đang đăng",
            "FAILED": "Lỗi đăng",
            "NOT_POSTED": "Chưa đăng",
            "PENDING": "Chưa đăng",
        }.get(status, status or "Chưa đăng")

    def _apply_facebook_filters(self) -> None:
        preparation = str(self.facebook_prepare_filter.currentData() or "")
        publishing = str(self.facebook_publish_filter.currentData() or "")
        for row in range(self.facebook_table.rowCount()):
            prepare_item = self.facebook_table.item(row, 6)
            publish_item = self.facebook_table.item(row, 7)
            prepare_state = str(prepare_item.data(Qt.ItemDataRole.UserRole + 10) or "") if prepare_item else ""
            publish_state = str(publish_item.data(Qt.ItemDataRole.UserRole + 10) or "") if publish_item else ""
            preparation_matches = not preparation or prepare_state == preparation
            publishing_matches = (
                not publishing
                or publish_state == publishing
                or (publishing == "NOT_POSTED" and publish_state in {"", "PENDING", "NOT_POSTED"})
            )
            self.facebook_table.setRowHidden(row, not (preparation_matches and publishing_matches))

    def _refresh_facebook_table(self) -> None:
        self.set_facebook_data(
            self._facebook_pages_cache,
            self._facebook_drafts_cache,
            self._facebook_jobs_cache,
        )

    def _default_page_changed(self, index: int) -> None:
        self._update_publish_button()
        if index >= 0:
            self.facebookDefaultPageRequested.emit(str(self.facebook_page_combo.itemData(index) or ""))

    def _selected_facebook_row(self) -> int:
        rows = self.facebook_table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _open_facebook_composer(self) -> None:
        if self._selected_facebook_row() < 0:
            return
        self._load_selected_facebook_draft()
        self.facebook_composer_dialog.exec()

    def _open_facebook_composer_for_row(self, row: int) -> None:
        self.facebook_table.selectRow(row)
        self._open_facebook_composer()

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
            video_id = str(item.data(Qt.ItemDataRole.UserRole))
            self.facebookPrepareRequested.emit({"row": row, "video_id": video_id, "file_path": self.facebook_file_paths.get(video_id)})

    def _select_facebook_file(self) -> None:
        row = self._selected_facebook_row()
        if row < 0:
            return
        item = self.facebook_table.item(row, 2)
        video_id = str(item.data(Qt.ItemDataRole.UserRole)) if item else ""
        if not video_id:
            return
        current = self.facebook_file_paths.get(video_id, "")
        selected, _ = QFileDialog.getOpenFileName(
            self, "Chọn file video để đăng", current,
            "Video (*.mp4 *.mov *.mkv *.webm);;All files (*.*)",
        )
        if selected:
            self.facebook_file_paths[video_id] = selected
            self.facebook_local_path.setText(selected)
            self._update_publish_button()

    def _publish_selected_video(self) -> None:
        row = self._selected_facebook_row()
        if row < 0:
            return
        item = self.facebook_table.item(row, 2)
        video_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if video_id:
            video_id = str(video_id)
            jobs = self.facebook_jobs_by_video.get(video_id, [])
            if jobs:
                latest = jobs[0]
                state = str(latest.get("display_status") or latest.get("status") or "")
                self.facebook_job_table.selectRow(0)
                if state in {"PROCESSING", "PUBLISHING", "UNRESOLVED"}:
                    self.facebookJobReconcileRequested.emit(str(latest["id"]))
                    return
                if state == "FAILED":
                    self.facebookJobRetryRequested.emit(str(latest["id"]))
                    return
                if state == "PUBLISHED" and latest.get("published_url"):
                    self.facebookJobOpenRequested.emit(str(latest["published_url"]))
                    return
            self.facebookPublishNowRequested.emit({"video_id": video_id, "page_id": self.facebook_page_combo.currentData(), "file_path": self.facebook_file_paths.get(video_id)})

    def _load_selected_facebook_draft(self) -> None:
        row = self._selected_facebook_row()
        if row < 0: return
        video_item = self.facebook_table.item(row, 2)
        video_id = video_item.data(Qt.ItemDataRole.UserRole) if video_item else None
        if not video_id:
            return
        self._facebook_selected_video_id = str(video_id)
        self.facebook_video_title.setText(self.facebook_table.item(row, 3).text())
        original_path = self.facebook_table.item(row, 4).text()
        self.facebook_local_path.setText(self.facebook_file_paths.get(str(video_id), original_path))
        file_name = self.facebook_local_path.text().replace("\\", "/").rsplit("/", 1)[-1]
        self.facebook_composer_dialog.setWindowTitle(
            f"Soạn nội dung đăng Facebook — {file_name or video_id}"
        )
        title_item = self.facebook_table.item(row, 3)
        self.facebook_title.setText(title_item.data(Qt.ItemDataRole.UserRole) or title_item.text())
        self.facebook_description.setPlainText(title_item.data(Qt.ItemDataRole.UserRole + 1) or "")
        self.facebook_hashtags.setText(title_item.data(Qt.ItemDataRole.UserRole + 2) or "")
        self._render_facebook_jobs(self.facebook_jobs_by_video.get(str(video_id), []))
        self._update_publish_button()

    def _selected_job_id(self) -> str | None:
        rows = self.facebook_job_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.facebook_job_table.item(rows[0].row(), 0)
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _selected_job(self) -> dict | None:
        job_id = self._selected_job_id()
        if not job_id:
            return None
        return next((job for job in self.facebook_jobs_by_video.get(self._facebook_selected_video_id or "", []) if str(job.get("id")) == job_id), None)

    def _update_job_actions(self) -> None:
        job = self._selected_job()
        state = str((job or {}).get("display_status") or (job or {}).get("status") or "")
        self.facebook_reconcile_button.setEnabled(state in {"PROCESSING", "PUBLISHING", "UNRESOLVED"})
        self.facebook_error_button.setEnabled(bool(job and job.get("last_error")))
        self.facebook_retry_button.setEnabled(state in {"FAILED", "UNRESOLVED"})
        self.facebook_cancel_job_button.setEnabled(state == "PENDING")
        self.facebook_open_job_button.setEnabled(bool(job and job.get("published_url")))
        self.facebook_delete_job_button.setEnabled(state in {"FAILED", "CANCELLED"})

    def _reconcile_selected_job(self) -> None:
        job_id = self._selected_job_id()
        if job_id:
            self.facebookJobReconcileRequested.emit(job_id)

    def _show_selected_job_error(self) -> None:
        job_id = self._selected_job_id()
        if job_id:
            self.facebookJobErrorRequested.emit(job_id)

    def _retry_selected_job(self) -> None:
        job_id = self._selected_job_id()
        if job_id:
            self.facebookJobRetryRequested.emit(job_id)

    def _cancel_selected_job(self) -> None:
        job_id = self._selected_job_id()
        if job_id:
            self.facebookJobCancelRequested.emit(job_id)

    def _open_selected_job(self) -> None:
        job = self._selected_job()
        if job and job.get("published_url"):
            self.facebookJobOpenRequested.emit(str(job["published_url"]))

    def _delete_selected_job(self) -> None:
        job_id = self._selected_job_id()
        if job_id:
            self.facebookJobDeleteRequested.emit(job_id)

    def _render_facebook_jobs(self, jobs: list[dict]) -> None:
        self.facebook_job_table.setRowCount(0)
        for job in jobs:
            row = self.facebook_job_table.rowCount(); self.facebook_job_table.insertRow(row)
            error = str(job.get("last_error") or "")
            values = (str(job.get("id", "")), str(job.get("video_id", "")), str(job.get("local_file_path", "")), str(job.get("page_id", "")), str(job.get("display_status") or job.get("status", "")), str(job.get("upload_phase") or ""), str(job.get("remote_video_id") or ""), str(job.get("created_at") or ""), error[:160], str(job.get("published_url") or ""))
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0: item.setData(Qt.ItemDataRole.UserRole, job.get("id"))
                self.facebook_job_table.setItem(row, col, item)
        self._update_job_actions()

    def set_facebook_data(self, pages: list[dict], drafts: list[dict], jobs_by_video: dict[str, list[dict]] | None = None) -> None:
        self._facebook_pages_cache = list(pages)
        self._facebook_drafts_cache = list(drafts)
        self._facebook_jobs_cache = dict(jobs_by_video or {})
        self.facebook_jobs_by_video = jobs_by_video or {}
        self.facebook_page_combo.blockSignals(True); self.facebook_page_combo.clear()
        for page in pages: self.facebook_page_combo.addItem(str(page.get("page_name", page.get("name", ""))), str(page.get("page_id", page.get("id", ""))))
        self.facebook_page_combo.blockSignals(False); self.facebook_status.setText("Đã kết nối Facebook" if pages else "Chưa kết nối Facebook")
        self._set_facebook_connection_state(bool(pages))
        selected_id = self._facebook_selected_video_id
        self.facebook_table.blockSignals(True)
        self.facebook_table.setRowCount(0)
        for draft in drafts:
            row = self.facebook_table.rowCount(); self.facebook_table.insertRow(row)
            source_title = str(draft.get("video_title") or draft.get("title") or "")
            page_label = draft.get("page_name") or draft.get("page_id") or "-"
            video_id = str(draft.get("video_id") or "")
            local_path = self.facebook_file_paths.get(video_id) or str(draft.get("local_video_path") or "Không tìm thấy file video")
            jobs = self.facebook_jobs_by_video.get(video_id, [])
            latest = jobs[0] if jobs else {}
            preparation_status = str(draft.get("preparation_status", "NOT_PREPARED"))
            publishing_status = str(latest.get("display_status") or latest.get("status") or "NOT_POSTED")
            values = ("", str(draft.get("sequence_number", row + 1)), str(draft.get("video_code", "")), source_title, local_path, str(page_label), self._facebook_preparation_label(preparation_status), self._facebook_publish_label(publishing_status), str(latest.get("published_url") or ""))
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 2: item.setData(Qt.ItemDataRole.UserRole, draft.get("video_id"))
                if col == 4: item.setToolTip(local_path)
                if col == 6: item.setData(Qt.ItemDataRole.UserRole + 10, preparation_status)
                if col == 7: item.setData(Qt.ItemDataRole.UserRole + 10, publishing_status)
                if col == 3:
                    item.setData(Qt.ItemDataRole.UserRole, draft.get("facebook_title") or source_title)
                    item.setData(Qt.ItemDataRole.UserRole + 1, draft.get("description") or "")
                    item.setData(Qt.ItemDataRole.UserRole + 2, draft.get("hashtags") or "")
                self.facebook_table.setItem(row, col, item)
            compose_button = QPushButton("⋯")
            compose_button.setObjectName("primary")
            compose_button.setToolTip("Soạn nội dung đăng")
            compose_button.clicked.connect(
                lambda _checked=False, target_row=row: self._open_facebook_composer_for_row(target_row)
            )
            self.facebook_table.setCellWidget(row, 9, compose_button)
        self.facebook_table.blockSignals(False)
        self.facebook_video_total.setText(f"{self.facebook_table.rowCount()} video")
        self._apply_facebook_filters()
        if selected_id:
            for row in range(self.facebook_table.rowCount()):
                item = self.facebook_table.item(row, 2)
                if item and str(item.data(Qt.ItemDataRole.UserRole)) == selected_id:
                    self.facebook_table.selectRow(row)
                    self._load_selected_facebook_draft()
                    break
        elif self.facebook_table.rowCount():
            self.facebook_table.clearSelection()
        if selected_id:
            self._render_facebook_jobs(self.facebook_jobs_by_video.get(selected_id, []))
        self._update_publish_button()

    def _update_publish_button(self) -> None:
        row = self._selected_facebook_row()
        ready = row >= 0 and bool(self.facebook_page_combo.currentData())
        state = ""
        if ready:
            preparation_item = self.facebook_table.item(row, 6)
            ready = bool(preparation_item and preparation_item.data(Qt.ItemDataRole.UserRole + 10) == "READY")
            ready = ready and self.facebook_table.item(row, 4).text() != "Không tìm thấy file video"
            video_item = self.facebook_table.item(row, 2)
            video_id = str(video_item.data(Qt.ItemDataRole.UserRole)) if video_item else ""
            jobs = self.facebook_jobs_by_video.get(video_id, [])
            latest = jobs[0] if jobs else {}
            state = str(latest.get("display_status") or latest.get("status") or "")
        if state in {"PROCESSING", "PUBLISHING", "UNRESOLVED"}:
            self.facebook_publish_button.setText("Kiểm tra trạng thái Meta")
        elif state == "FAILED":
            self.facebook_publish_button.setText("Đăng lại")
        elif state == "PUBLISHED":
            self.facebook_publish_button.setText("Mở bài đăng")
        else:
            self.facebook_publish_button.setText("Đăng ngay")
        self.facebook_publish_button.setEnabled(ready and not self.facebook_publish_button.is_loading)

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

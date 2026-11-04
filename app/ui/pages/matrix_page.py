from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QProgressBar,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStyleOptionViewItem,
    QTabBar,
    QTabWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.models.project import Project
class MatrixScheduleModel(QAbstractTableModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.pages: list[dict] = []
        self.entries: dict[tuple[str, str, str], dict] = {}
        self.rows: list[tuple[str, str, str]] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.pages) * 3 + 1

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section == 0:
                return "Khung giờ"
            return ("Tên file", "Ảnh", "Caption & TT")[
                (section - 1) % 3
            ]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row_type, scheduled_date, scheduled_time = self.rows[index.row()]
        if row_type == "pages":
            if role == Qt.ItemDataRole.DisplayRole:
                if index.column() == 0:
                    return "Khung giờ"
                if (index.column() - 1) % 3 == 0:
                    return self.pages[(index.column() - 1) // 3]["name"]
            if role == Qt.ItemDataRole.BackgroundRole:
                return QColor("#14233b")
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor("#93c5fd")
            return None
        if row_type == "fields":
            if role == Qt.ItemDataRole.DisplayRole:
                if index.column() == 0:
                    return "Khung giờ"
                return ("Tên file", "Ảnh", "Caption & TT")[
                    (index.column() - 1) % 3
                ]
            if role == Qt.ItemDataRole.BackgroundRole:
                return QColor("#101c30")
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor("#8ea1bb")
            return None
        if row_type == "day":
            if role == Qt.ItemDataRole.DisplayRole and index.column() == 0:
                day_number = (
                    date.fromisoformat(scheduled_date)
                    - date.fromisoformat(self.rows[0][1])
                ).days + 1
                return f"NGÀY {day_number}  ·  {date.fromisoformat(scheduled_date):%d/%m/%Y}"
            if role == Qt.ItemDataRole.BackgroundRole:
                return QColor("#18345a")
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor("#bfdbfe")
            return None
        if index.column() == 0:
            if role == Qt.ItemDataRole.DisplayRole:
                return scheduled_time
            if role == Qt.ItemDataRole.BackgroundRole:
                return QColor("#101c30")
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor("#fbbf24")
            return None
        page_index = (index.column() - 1) // 3
        page = self.pages[page_index]
        entry = self.entries.get((page["page_id"], scheduled_date, scheduled_time))
        if role == Qt.ItemDataRole.UserRole:
            return entry
        if role == Qt.ItemDataRole.ToolTipRole and entry is not None:
            return (
                f"{page['name']} · {scheduled_date} {scheduled_time}\n"
                f"{entry.get('video_code') or 'Chưa chọn video'}\n"
                f"{entry.get('caption') or ''}"
            )
        return None

    def set_schedule(
        self, configuration: dict, pages: list[dict], entries: list[dict]
    ) -> None:
        self.beginResetModel()
        self.pages = pages
        self.entries = {
            (entry["page_id"], entry["scheduled_date"], entry["scheduled_time"]): entry
            for entry in entries
        }
        self.rows = []
        first_date = date.fromisoformat(configuration["start_date"])
        slots = configuration["slots"]
        for day_offset in range(configuration["day_count"]):
            scheduled_date = (first_date + timedelta(days=day_offset)).isoformat()
            self.rows.append(("day", scheduled_date, ""))
            self.rows.append(("pages", scheduled_date, ""))
            self.rows.append(("fields", scheduled_date, ""))
            self.rows.extend(
                ("slot", scheduled_date, slot) for slot in slots
            )
        self.endResetModel()

    def entry_at(self, index: QModelIndex) -> dict | None:
        if (
            not index.isValid()
            or index.column() == 0
            or self.rows[index.row()][0] != "slot"
        ):
            return None
        _, scheduled_date, scheduled_time = self.rows[index.row()]
        page = self.pages[(index.column() - 1) // 3]
        return self.entries.get((page["page_id"], scheduled_date, scheduled_time))

    def row_is_day_header(self, row: int) -> bool:
        return self.rows[row][0] == "day"


class MatrixCellDelegate(QStyledItemDelegate):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thumbnails: dict[str, QPixmap] = {}

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        if (
            index.column() == 0
            or index.model().rows[index.row()][0] != "slot"
        ):
            super().paint(painter, option, index)
            return
        entry = index.data(Qt.ItemDataRole.UserRole)
        column_type = (index.column() - 1) % 3
        rect = option.rect.adjusted(1, 1, -1, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        background = QColor("#163158") if selected else QColor("#0f2034")
        if entry and entry["publish_status"] == "posted":
            background = QColor("#10352f") if not selected else QColor("#145c4d")
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(rect, background)
        painter.setPen(QColor("#263b58"))
        painter.drawRect(rect)

        if entry is None or not entry.get("video_id"):
            if column_type == 0:
                painter.setPen(QColor("#71829d"))
                painter.drawText(
                    rect.adjusted(6, 6, -6, -6),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    "Chưa chọn video",
                )
            elif column_type == 2:
                painter.setPen(QColor("#71829d"))
                painter.drawText(
                    rect.adjusted(6, 6, -6, -6),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    "Chưa có caption",
                )
            elif entry is None:
                painter.fillRect(rect.adjusted(4, 4, -4, -4), QColor("#17263d"))
            painter.restore()
            return

        if column_type == 0:
            painter.setPen(QColor("#7dd3fc"))
            painter.drawText(
                rect.adjusted(7, 7, -7, -7),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                self._elide(
                    painter,
                    entry.get("video_code") or "Video",
                    rect.width() - 14,
                ),
            )
            title_rect = rect.adjusted(7, 27, -7, -7)
            painter.setPen(QColor("#dbe7f7"))
            painter.drawText(
                title_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                self._elide(
                    painter,
                    entry.get("video_title") or "",
                    title_rect.width(),
                ),
            )
        elif column_type == 1:
            image_rect = rect.adjusted(4, 4, -4, -4)
            pixmap = self._thumbnail(entry.get("thumbnail_path"))
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    image_rect.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                target = image_rect
                target.setSize(scaled.size())
                target.moveCenter(image_rect.center())
                painter.drawPixmap(target, scaled)
            else:
                painter.fillRect(image_rect, QColor("#17263d"))
                painter.setPen(QColor("#71829d"))
                painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter, "VIDEO")
        else:
            caption_rect = rect.adjusted(7, 6, -7, -29)
            painter.setPen(QColor("#9fb0c7"))
            painter.drawText(
                caption_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                | Qt.TextFlag.TextWordWrap,
                entry.get("caption") or "Chưa có caption",
            )
            status_rect = rect.adjusted(4, 0, -4, -4)
            posted = entry["publish_status"] == "posted"
            painter.setPen(QColor("#34d399") if posted else QColor("#fbbf24"))
            painter.drawText(
                status_rect,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                "✓ ĐÃ ĐĂNG" if posted else "CHƯA ĐĂNG",
            )
        painter.restore()

    def _thumbnail(self, path: str | None) -> QPixmap:
        if not path or not Path(path).is_file():
            return QPixmap()
        if path not in self._thumbnails:
            reader = QImageReader(path)
            reader.setScaledSize(QSize(128, 96))
            self._thumbnails[path] = QPixmap.fromImage(reader.read())
        return self._thumbnails[path]

    @staticmethod
    def _elide(
        painter: QPainter,
        text: str,
        width: int,
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
    ) -> str:
        return painter.fontMetrics().elidedText(text, mode, max(0, width))


class ProjectVideoListModel(QAbstractTableModel):
    editRequested = Signal(str, str, object)
    actionRequested = Signal(int, int)
    HEADERS = (
        "STT",
        "Mã video",
        "Ảnh",
        "Chọn ảnh",
        "Tên video",
        "Copy title",
        "Link Facebook",
        "Trạng thái tải",
        "Tải lên Facebook",
        "Tệp MP4",
        "Copy đường dẫn",
    )
    CODE_COLUMN = 1
    THUMBNAIL_COLUMN = 2
    CHOOSE_THUMBNAIL_COLUMN = 3
    TITLE_COLUMN = 4
    COPY_TITLE_COLUMN = 5
    COPY_PATH_COLUMN = 10
    EDITABLE_FIELDS = {CODE_COLUMN: "video_code", TITLE_COLUMN: "title"}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.videos: list[dict] = []
        self._thumbnails: dict[str, QPixmap] = {}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.videos)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        video = self.videos[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.ToolTipRole:
            if column == 6:
                return video.get("source_url", "")
            if column == 9:
                return video.get("local_video_path", "") or "Chưa tải"
            if column == self.THUMBNAIL_COLUMN:
                return "Ảnh thu nhỏ. Nhấn “Chọn ảnh” để thay đổi."
            if column == self.CHOOSE_THUMBNAIL_COLUMN:
                return "Chọn hoặc thay ảnh thumbnail."
            if column == self.COPY_TITLE_COLUMN:
                return "Sao chép tên video."
            if column == self.COPY_PATH_COLUMN:
                return "Sao chép đường dẫn MP4."
            if column in (self.CODE_COLUMN, self.TITLE_COLUMN):
                return "Nhấp đúp để chỉnh sửa."
        if column == self.THUMBNAIL_COLUMN and role == Qt.ItemDataRole.DecorationRole:
            path = video.get("thumbnail_path")
            if not path or not Path(path).is_file():
                return None
            if path not in self._thumbnails:
                reader = QImageReader(path)
                image_size = reader.size()
                if image_size.isValid():
                    reader.setScaledSize(
                        image_size.scaled(
                            QSize(160, 100), Qt.AspectRatioMode.KeepAspectRatio
                        )
                    )
                image = reader.read()
                self._thumbnails[path] = QPixmap.fromImage(image)
            return self._thumbnails[path]
        if role == Qt.ItemDataRole.EditRole:
            field = self.EDITABLE_FIELDS.get(column)
            return video.get(field, "") if field else None
        if role == Qt.ItemDataRole.UserRole:
            if column == self.COPY_TITLE_COLUMN:
                return bool(video.get("title"))
            if column == self.COPY_PATH_COLUMN:
                return bool(video.get("local_video_path"))
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        values = (
            video.get("sequence_number", ""),
            video.get("video_code", ""),
            "",
            "Chọn",
            video.get("title", ""),
            "Copy",
            video.get("source_url", ""),
            {
                "completed": "Đã tải",
                "failed": "Lỗi",
                "waiting": "Chờ tải",
                "queued": "Trong hàng đợi",
                "downloading": "Đang tải",
                "interrupted": "Bị gián đoạn",
                "cancelled": "Đã hủy",
            }.get(video.get("download_status"), "Chờ tải"),
            "Đã tải lên Facebook"
            if video.get("facebook_uploaded")
            else "Chưa đăng",
            video.get("local_video_path") or "Chưa có file",
            "Copy",
        )
        return str(values[column])

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        flags = super().flags(index)
        if index.isValid() and index.column() in self.EDITABLE_FIELDS:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(
        self,
        index: QModelIndex,
        value: object,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        field = self.EDITABLE_FIELDS.get(index.column())
        if field is None:
            return False
        text = str(value).strip()
        if not text:
            return False
        video = self.videos[index.row()]
        if text == video.get(field):
            return True
        video[field] = text
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])
        self.editRequested.emit(video["id"], field, text)
        return True

    def trigger_action(self, row: int, column: int) -> None:
        self.actionRequested.emit(row, column)

    def set_videos(self, videos: list[dict]) -> None:
        self.beginResetModel()
        self.videos = list(videos)
        self._thumbnails.clear()
        self.endResetModel()


class ProjectVideoCellDelegate(QStyledItemDelegate):
    actionTriggered = Signal(int, int)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        column = index.column()
        if column == ProjectVideoListModel.THUMBNAIL_COLUMN:
            item_option = QStyleOptionViewItem(option)
            self.initStyleOption(item_option, index)
            pixmap = index.data(Qt.ItemDataRole.DecorationRole)
            item_option.text = ""
            item_option.icon = QIcon()
            widget = item_option.widget
            if widget is not None:
                widget.style().drawControl(
                    QStyle.ControlElement.CE_ItemViewItem,
                    item_option,
                    painter,
                    widget,
                )
            if pixmap is not None and not pixmap.isNull():
                image_rect = option.rect.adjusted(5, 5, -5, -5)
                scaled = pixmap.scaled(
                    image_rect.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                target = scaled.rect()
                target.moveCenter(image_rect.center())
                painter.drawPixmap(target, scaled)
            else:
                placeholder = option.rect.adjusted(7, 7, -7, -7)
                painter.save()
                painter.setPen(QColor("#71829d"))
                painter.drawText(
                    placeholder,
                    Qt.AlignmentFlag.AlignCenter,
                    "Chưa có ảnh",
                )
                painter.restore()
            return
        if column not in (
            ProjectVideoListModel.CHOOSE_THUMBNAIL_COLUMN,
            ProjectVideoListModel.COPY_TITLE_COLUMN,
            ProjectVideoListModel.COPY_PATH_COLUMN,
        ):
            super().paint(painter, option, index)
            return

        item_option = QStyleOptionViewItem(option)
        self.initStyleOption(item_option, index)
        item_option.text = ""
        item_option.icon = QIcon()
        widget = item_option.widget
        if widget is not None:
            widget.style().drawControl(
                QStyle.ControlElement.CE_ItemViewItem,
                item_option,
                painter,
                widget,
            )
        button = QStyleOptionButton()
        button.rect = option.rect.adjusted(7, 29, -7, -29)
        button.text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        button.state = QStyle.StateFlag.State_Raised
        enabled = bool(index.flags() & Qt.ItemFlag.ItemIsEnabled)
        if column in (
            ProjectVideoListModel.COPY_TITLE_COLUMN,
            ProjectVideoListModel.COPY_PATH_COLUMN,
        ) and not index.data(Qt.ItemDataRole.UserRole):
            enabled = False
        if enabled:
            button.state |= QStyle.StateFlag.State_Enabled
        if option.state & QStyle.StateFlag.State_MouseOver:
            button.state |= QStyle.StateFlag.State_MouseOver
        if option.state & QStyle.StateFlag.State_Selected:
            button.state |= QStyle.StateFlag.State_On
        if widget is not None:
            widget.style().drawControl(
                QStyle.ControlElement.CE_PushButton, button, painter, widget
            )

    def editorEvent(self, event, model, option, index: QModelIndex) -> bool:
        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
            and option.rect.contains(event.position().toPoint())
        ):
            column = index.column()
            if column in (
                ProjectVideoListModel.CHOOSE_THUMBNAIL_COLUMN,
                ProjectVideoListModel.COPY_TITLE_COLUMN,
                ProjectVideoListModel.COPY_PATH_COLUMN,
            ):
                if (
                    column == ProjectVideoListModel.CHOOSE_THUMBNAIL_COLUMN
                    or index.data(Qt.ItemDataRole.UserRole)
                ):
                    self.actionTriggered.emit(index.row(), column)
                return True
        return super().editorEvent(event, model, option, index)


class ProjectVideoListTable(QTableView):
    actionRequested = Signal(int, int)
    editRequested = Signal(str, str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("projectVideoListTable")
        self.video_model = ProjectVideoListModel(self)
        self.setModel(self.video_model)
        self.cell_delegate = ProjectVideoCellDelegate(self)
        self.setItemDelegate(self.cell_delegate)
        self.cell_delegate.actionTriggered.connect(self.video_model.trigger_action)
        self.video_model.actionRequested.connect(self.actionRequested)
        self.video_model.editRequested.connect(self.editRequested)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(92)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setStretchLastSection(False)
        for column, width in enumerate(
            (58, 140, 126, 92, 240, 94, 280, 112, 145, 270, 120)
        ):
            self.setColumnWidth(column, width)
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.setEditTriggers(
            QTableView.EditTrigger.DoubleClicked | QTableView.EditTrigger.EditKeyPressed
        )
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setMinimumHeight(260)

    def set_videos(self, videos: list[dict]) -> None:
        selected = self.selected_video()
        selected_id = selected["id"] if selected is not None else None
        self.video_model.set_videos(videos)
        if selected_id is not None:
            row = next(
                (
                    index
                    for index, video in enumerate(self.video_model.videos)
                    if video["id"] == selected_id
                ),
                -1,
            )
            if row >= 0:
                self.setCurrentIndex(self.video_model.index(row, 0))

    def selected_video(self) -> dict | None:
        row = self.currentIndex().row()
        if 0 <= row < len(self.video_model.videos):
            return self.video_model.videos[row]
        return None


class MatrixPage(QWidget):
    backRequested = Signal()
    matrixChanged = Signal(str)
    newMatrixRequested = Signal()
    configureRequested = Signal()
    exportRequested = Signal()
    regenerateRequested = Signal()
    undoRequested = Signal()
    renameRequested = Signal()
    deleteRequested = Signal()
    editEntryRequested = Signal(object)
    publishStatusRequested = Signal(object, bool)
    facebookUploadStatusRequested = Signal(str, bool)
    videoEditRequested = Signal(str, str, object)
    videoImageRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project: Project | None = None
        self.matrices: list[dict] = []
        self.matrix: dict | None = None
        self.videos: list[dict] = []
        self._updating_tabs = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 18, 26, 20)
        layout.setSpacing(12)
        header = QFrame()
        header.setObjectName("matrixHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 12)
        title_row = QVBoxLayout()
        self.project_title = QLabel("Ma trận lịch đăng")
        self.project_title.setObjectName("title")
        self.matrix_title = QLabel("Lịch đăng video lưu cục bộ · Không tự động đăng")
        self.matrix_title.setObjectName("detailMeta")
        title_row.addWidget(self.project_title)
        title_row.addWidget(self.matrix_title)
        header_layout.addLayout(title_row)
        actions = QHBoxLayout()
        self.configure_button = QPushButton("Cấu hình slot && page")
        self.configure_button.setObjectName("secondary")
        self.configure_button.setMinimumWidth(178)
        self.configure_button.clicked.connect(self.configureRequested.emit)
        self.export_button = QPushButton("Xuất file CSV")
        self.export_button.setObjectName("primary")
        self.export_button.clicked.connect(self.exportRequested.emit)
        self.new_header_button = QPushButton("Tạo thêm ma trận")
        self.new_header_button.setObjectName("secondary")
        self.new_header_button.clicked.connect(self.newMatrixRequested.emit)
        actions.addStretch()
        actions.addWidget(self.configure_button)
        actions.addWidget(self.export_button)
        actions.addWidget(self.new_header_button)
        header_layout.addLayout(actions)
        layout.addWidget(header)

        self.content_tabs = QTabWidget()
        self.content_tabs.setObjectName("matrixContentTabs")
        self.schedule_view = QWidget()
        schedule_layout = QVBoxLayout(self.schedule_view)
        schedule_layout.setContentsMargins(0, 0, 0, 0)
        schedule_layout.setSpacing(10)

        tabs_row = QVBoxLayout()
        tabs_label = QLabel("DANH SÁCH MA TRẬN")
        tabs_label.setObjectName("navSection")
        tabs_row.addWidget(tabs_label)
        self.tabs = QTabBar()
        self.tabs.setObjectName("matrixTabs")
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.currentChanged.connect(self._tab_changed)
        tabs_row.addWidget(self.tabs)
        self.add_tab_button = QPushButton("＋ Thêm ma trận mới")
        self.add_tab_button.setObjectName("matrixAddTab")
        self.add_tab_button.clicked.connect(self.newMatrixRequested.emit)
        tabs_row.addWidget(self.add_tab_button, alignment=Qt.AlignmentFlag.AlignLeft)
        schedule_layout.addLayout(tabs_row)

        toolbar = QVBoxLayout()
        statistics = QHBoxLayout()
        self.count_label = QLabel("Tổng số ô: 0 · Đã đăng: 0 · Chưa đăng: 0")
        self.count_label.setObjectName("detailMeta")
        statistics.addWidget(self.count_label)
        self.progress = QProgressBar()
        self.progress.setObjectName("matrixProgress")
        self.progress.setFixedWidth(150)
        self.progress.setFixedHeight(10)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        statistics.addWidget(self.progress)
        statistics.addStretch()
        self.regenerate_button = self._tool_button("Sinh lại", self.regenerateRequested)
        self.undo_button = self._tool_button("Hoàn tác", self.undoRequested)
        self.rename_button = self._tool_button("Đổi tên", self.renameRequested)
        self.delete_button = self._tool_button("Xóa", self.deleteRequested)
        for button in (
            self.regenerate_button,
            self.undo_button,
            self.rename_button,
            self.delete_button,
        ):
            statistics.addWidget(button)
        toolbar.addLayout(statistics)

        cell_actions = QHBoxLayout()
        self.edit_button = QPushButton("Chọn video / sửa caption")
        self.edit_button.setObjectName("secondary")
        self.edit_button.setEnabled(False)
        self.edit_button.clicked.connect(
            lambda: self._edit_current(self.table.currentIndex())
        )
        self.copy_button = QPushButton("Copy caption")
        self.copy_button.setObjectName("secondary")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(lambda: self._copy_current_caption())
        self.status_button = QPushButton("Đánh dấu Đã đăng")
        self.status_button.setObjectName("primary")
        self.status_button.setEnabled(False)
        self.status_button.clicked.connect(self._toggle_current_status)
        for button in (
            self.edit_button,
            self.copy_button,
            self.status_button,
        ):
            cell_actions.addWidget(button)
        cell_actions.addStretch()
        toolbar.addLayout(cell_actions)
        schedule_layout.addLayout(toolbar)

        self.table = QTableView()
        self.table.setObjectName("matrixTable")
        self.model = MatrixScheduleModel(self.table)
        self.delegate = MatrixCellDelegate(self.table)
        self.table.setModel(self.model)
        self.table.selectionModel().currentChanged.connect(
            lambda *_: self._update_status_button()
        )
        self.table.setItemDelegateForColumn(0, None)
        self.table.setItemDelegate(self.delegate)
        self.table.setAlternatingRowColors(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(96)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.doubleClicked.connect(self._edit_current)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setMinimumHeight(280)
        schedule_layout.addWidget(self.table, 1)

        self.empty_state = QLabel("Dự án này chưa có ma trận. Tạo ma trận để bắt đầu.")
        self.empty_state.setObjectName("matrixEmpty")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.hide()
        schedule_layout.addWidget(self.empty_state)
        self.content_tabs.addTab(self.schedule_view, "Lịch đăng")

        self.video_list_view = QWidget()
        video_layout = QVBoxLayout(self.video_list_view)
        video_layout.setContentsMargins(0, 10, 0, 0)
        video_layout.setSpacing(10)
        self.video_count_label = QLabel("Video của dự án")
        self.video_count_label.setObjectName("detailMeta")
        video_actions = QHBoxLayout()
        video_actions.addWidget(self.video_count_label)
        video_actions.addStretch()
        edit_hint = QLabel("Nhấp đúp mã/tên để sửa · Ảnh, copy nằm ngay cạnh video")
        edit_hint.setObjectName("detailMeta")
        video_actions.addWidget(edit_hint)
        self.facebook_uploaded_button = QPushButton("Đánh dấu đã tải lên Facebook")
        self.facebook_uploaded_button.setObjectName("primary")
        self.facebook_uploaded_button.setEnabled(False)
        self.facebook_uploaded_button.clicked.connect(
            self._toggle_selected_facebook_uploaded
        )
        video_actions.addWidget(self.facebook_uploaded_button)
        video_layout.addLayout(video_actions)
        self.video_table = ProjectVideoListTable(self.video_list_view)
        self.video_table.selectionModel().currentChanged.connect(
            lambda *_: self._update_video_actions()
        )
        self.video_table.actionRequested.connect(self._handle_video_action)
        self.video_table.editRequested.connect(self.videoEditRequested)
        video_layout.addWidget(self.video_table, 1)
        self.content_tabs.addTab(self.video_list_view, "Video theo dự án")
        layout.addWidget(self.content_tabs, 1)

    def _tool_button(self, text: str, signal: Signal) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("secondary")
        button.clicked.connect(signal.emit)
        return button

    def set_data(
        self,
        project: Project,
        matrices: list[dict],
        matrix: dict | None,
        configuration: dict | None,
        pages: list[dict],
        entries: list[dict],
        videos: list[dict],
        can_undo: bool,
        selected_id: str | None,
    ) -> None:
        self.project = project
        self.matrices = matrices
        self.matrix = matrix
        self.videos = videos
        self.video_table.set_videos(videos)
        self.video_count_label.setText(
            f"{len(videos)} video trong dự án “{project.name}”"
        )
        self._update_video_actions()
        self.project_title.setText(f"{project.name} · Ma trận lịch đăng")
        self._updating_tabs = True
        while self.tabs.count():
            self.tabs.removeTab(0)
        selected_index = -1
        for index, item in enumerate(matrices):
            self.tabs.addTab(
                f"{item['name']}  ·  {item['page_count']} Page"
            )
            self.tabs.setTabData(index, item["matrix_id"])
            if item["matrix_id"] == selected_id:
                selected_index = index
        if selected_index >= 0:
            self.tabs.setCurrentIndex(selected_index)
        self._updating_tabs = False
        has_matrix = matrix is not None and configuration is not None
        for button in (
            self.configure_button,
            self.export_button,
            self.regenerate_button,
            self.rename_button,
            self.delete_button,
            self.edit_button,
            self.copy_button,
            self.status_button,
        ):
            button.setEnabled(has_matrix)
        self.undo_button.setEnabled(has_matrix and can_undo)
        self.table.setVisible(has_matrix)
        self.empty_state.setVisible(not has_matrix)
        self.new_header_button.setVisible(True)
        self.add_tab_button.setVisible(True)
        if not has_matrix:
            self.matrix_title.setText(
                "Lịch đăng video lưu cục bộ · Không tự động đăng"
            )
            self.count_label.setText("Tổng số ô: 0 · Đã đăng: 0 · Chưa đăng: 0")
            self.progress.setValue(0)
            self.model.set_schedule(
                {"start_date": date.today().isoformat(), "day_count": 0, "slots": []},
                [],
                [],
            )
            self._update_status_button()
            return
        assert matrix is not None and configuration is not None
        self.matrix_title.setText(
            f"{matrix['name']}  ·  {configuration['day_count']} ngày  ·  "
            f"{len(pages)} Page  ·  {len(configuration['slots'])} khung giờ"
        )
        total = len(entries)
        posted = sum(entry["publish_status"] == "posted" for entry in entries)
        self.count_label.setText(
            f"Tổng số ô: {total}  ·  Đã đăng: {posted}  ·  Chưa đăng: {total - posted}"
        )
        self.progress.setValue(round(posted * 100 / total) if total else 0)
        self.model.set_schedule(configuration, pages, entries)
        self.table.clearSpans()
        self.table.verticalHeader().setDefaultSectionSize(96)
        for column in range(self.model.columnCount()):
            if column == 0:
                self.table.setColumnWidth(column, 72)
            else:
                field = (column - 1) % 3
                self.table.setColumnWidth(column, (78, 52, 112)[field])
        for row in range(self.model.rowCount()):
            if self.model.row_is_day_header(row):
                self.table.setSpan(row, 0, 1, self.model.columnCount())
                self.table.setRowHeight(row, 27)
            elif self.model.rows[row][0] == "pages":
                self.table.setSpan(row, 0, 1, 1)
                for page_index in range(len(pages)):
                    self.table.setSpan(row, 1 + page_index * 3, 1, 3)
                self.table.setRowHeight(row, 26)
            elif self.model.rows[row][0] == "fields":
                self.table.setRowHeight(row, 24)
        self._update_status_button()

    def _tab_changed(self, index: int) -> None:
        if not self._updating_tabs and index >= 0:
            matrix_id = self.tabs.tabData(index)
            if matrix_id:
                self.matrixChanged.emit(matrix_id)

    def _context_menu(self, position) -> None:
        index = self.table.indexAt(position)
        entry = self.model.entry_at(index)
        if entry is None:
            return
        menu = QMenu(self)
        edit_action = menu.addAction("Chọn video / sửa caption")
        copy_action = menu.addAction("Copy caption")
        menu.addSeparator()
        status_text = (
            "Đánh dấu Chưa đăng"
            if entry["publish_status"] == "posted"
            else "Đánh dấu Đã đăng"
        )
        status_action = menu.addAction(status_text)
        status_action.setEnabled(
            bool(entry.get("video_id")) or entry["publish_status"] == "posted"
        )
        action = menu.exec(self.table.viewport().mapToGlobal(position))
        if action == edit_action:
            self.editEntryRequested.emit(entry)
        elif action == copy_action:
            from PySide6.QtWidgets import QApplication

            QApplication.clipboard().setText(entry.get("caption") or "")
        elif action == status_action:
            self.publishStatusRequested.emit(
                entry, entry["publish_status"] != "posted"
            )

    def _edit_current(self, index: QModelIndex) -> None:
        if entry := self.model.entry_at(index):
            self.editEntryRequested.emit(entry)

    def selected_entry(self) -> dict | None:
        return self.model.entry_at(self.table.currentIndex())

    def _update_status_button(self) -> None:
        entry = self.selected_entry()
        self.edit_button.setEnabled(entry is not None)
        self.copy_button.setEnabled(entry is not None)
        self.status_button.setEnabled(entry is not None)
        if entry is not None:
            self.status_button.setText(
                "Đánh dấu Chưa đăng"
                if entry["publish_status"] == "posted"
                else "Đánh dấu Đã đăng"
            )
        self.status_button.setEnabled(
            entry is not None
            and (bool(entry.get("video_id")) or entry["publish_status"] == "posted")
        )

    def _toggle_current_status(self) -> None:
        if entry := self.selected_entry():
            self.publishStatusRequested.emit(
                entry, entry["publish_status"] != "posted"
            )

    def _copy_current_caption(self) -> None:
        if entry := self.selected_entry():
            from PySide6.QtWidgets import QApplication

            QApplication.clipboard().setText(entry.get("caption") or "")

    def _update_video_actions(self) -> None:
        video = self.video_table.selected_video()
        self.facebook_uploaded_button.setEnabled(video is not None)
        if video is not None:
            self.facebook_uploaded_button.setText(
                "Đánh dấu chưa tải lên Facebook"
                if video.get("facebook_uploaded")
                else "Đánh dấu đã tải lên Facebook"
            )

    def _handle_video_action(self, row: int, column: int) -> None:
        if not 0 <= row < len(self.video_table.video_model.videos):
            return
        video = self.video_table.video_model.videos[row]
        self.video_table.setCurrentIndex(
            self.video_table.video_model.index(row, column)
        )
        if column == ProjectVideoListModel.CHOOSE_THUMBNAIL_COLUMN:
            self.videoImageRequested.emit(video["id"])
        elif column == ProjectVideoListModel.COPY_TITLE_COLUMN:
            if video.get("title"):
                QApplication.clipboard().setText(video["title"])
        elif column == ProjectVideoListModel.COPY_PATH_COLUMN:
            if video.get("local_video_path"):
                QApplication.clipboard().setText(video["local_video_path"])

    def _toggle_selected_facebook_uploaded(self) -> None:
        video = self.video_table.selected_video()
        if video is not None:
            self.facebookUploadStatusRequested.emit(
                video["id"], not bool(video.get("facebook_uploaded"))
            )

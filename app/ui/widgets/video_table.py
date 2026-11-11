from collections.abc import Mapping

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QWidget,
)


class VideoStatusBadgeDelegate(QStyledItemDelegate):
    """Renders column 5 (Trạng thái) as modern rounded badges/tags with semantic colors."""

    BADGE_STYLES = {
        "Trong hàng đợi": {
            "bg": QColor("#132036"),
            "border": QColor("#223c63"),
            "text": QColor("#93c5fd"),
        },
        "Đang tải": {
            "bg": QColor("#083344"),
            "border": QColor("#0891b2"),
            "text": QColor("#38bdf8"),
        },
        "Đã tải": {
            "bg": QColor("#063b2c"),
            "border": QColor("#059669"),
            "text": QColor("#34d399"),
        },
        "Lỗi": {
            "bg": QColor("#3c151b"),
            "border": QColor("#991b1b"),
            "text": QColor("#f87171"),
        },
        "Bị gián đoạn": {
            "bg": QColor("#382310"),
            "border": QColor("#b45309"),
            "text": QColor("#fbbf24"),
        },
        "Chờ tải": {
            "bg": QColor("#141f2e"),
            "border": QColor("#283b54"),
            "text": QColor("#8fa2be"),
        },
    }

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if not text:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor("#1d3c66"))
        elif option.features & QStyleOptionViewItem.ViewItemFeature.Alternate:
            painter.fillRect(option.rect, QColor("#101c30"))
        else:
            painter.fillRect(option.rect, QColor("#0c1729"))

        style = self.BADGE_STYLES.get(
            text,
            {
                "bg": QColor("#141f2e"),
                "border": QColor("#283b54"),
                "text": QColor("#8fa2be"),
            },
        )

        font = painter.font()
        font.setPointSize(9)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)

        fm = painter.fontMetrics()
        text_width = fm.horizontalAdvance(text)
        badge_w = max(text_width + 18, 76)
        badge_h = 22
        badge_x = option.rect.x() + (option.rect.width() - badge_w) // 2
        badge_y = option.rect.y() + (option.rect.height() - badge_h) // 2
        badge_rect = QRectF(badge_x, badge_y, badge_w, badge_h)

        painter.setBrush(QBrush(style["bg"]))
        painter.setPen(QPen(style["border"], 1.0))
        painter.drawRoundedRect(badge_rect, 5.0, 5.0)

        painter.setPen(style["text"])
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, text)

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(96, 36)


class VideoProgressDelegate(QStyledItemDelegate):
    """
    Renders column 6 (Tiến độ tải):
    - Queued: "◌ Chờ tải" with spinner arc + label
    - Downloading: mini gradient progress bar
    - Completed: "100%" in mint green
    - Muted dash "—" otherwise
    """

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor("#1d3c66"))
        elif option.features & QStyleOptionViewItem.ViewItemFeature.Alternate:
            painter.fillRect(option.rect, QColor("#101c30"))
        else:
            painter.fillRect(option.rect, QColor("#0c1729"))

        display_text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        status = str(index.data(Qt.ItemDataRole.UserRole + 1) or "")
        progress = index.data(Qt.ItemDataRole.UserRole)
        try:
            progress_val = float(progress) if progress is not None else 0.0
        except (ValueError, TypeError):
            progress_val = 0.0

        is_downloading = (
            status == "downloading"
            or (progress_val > 0 and status != "completed")
            or "Đang tải" in display_text
        )
        is_completed = status == "completed" or display_text == "100%"
        is_queued = status == "queued" or "Chờ tải" in display_text

        if is_downloading:
            # Render sleek mini progress bar with cyan/blue gradient
            bar_w = min(option.rect.width() - 16, 80)
            bar_h = 6
            bar_x = option.rect.x() + (option.rect.width() - bar_w) // 2
            bar_y = option.rect.y() + (option.rect.height() - bar_h) // 2

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#16263d"))
            painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 3.0, 3.0)

            fill_ratio = min(100.0, max(0.0, progress_val)) / 100.0
            fill_w = max(4.0, bar_w * fill_ratio) if progress_val > 0 else 0
            if fill_w > 0:
                grad = QLinearGradient(bar_x, bar_y, bar_x + bar_w, bar_y)
                grad.setColorAt(0.0, QColor("#06b6d4"))
                grad.setColorAt(1.0, QColor("#3b82f6"))
                painter.setBrush(grad)
                painter.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 3.0, 3.0)

        elif is_completed:
            font = painter.font()
            font.setPointSize(9)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor("#34d399"))
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, "100%")

        elif is_queued:
            font = painter.font()
            font.setPointSize(9)
            painter.setFont(font)

            text = "Chờ tải"
            fm = painter.fontMetrics()
            text_w = fm.horizontalAdvance(text)
            icon_size = 11
            gap = 6
            total_w = icon_size + gap + text_w

            start_x = option.rect.x() + (option.rect.width() - total_w) // 2
            center_y = option.rect.y() + option.rect.height() // 2

            icon_rect = QRectF(start_x, center_y - icon_size // 2, icon_size, icon_size)
            pen = QPen(QColor("#38bdf8"), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(icon_rect, 45 * 16, 270 * 16)

            text_rect = QRectF(
                start_x + icon_size + gap,
                option.rect.y(),
                text_w + 4,
                option.rect.height(),
            )
            painter.setPen(QColor("#8fa2be"))
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                text,
            )

        else:
            font = painter.font()
            font.setPointSize(10)
            painter.setFont(font)
            painter.setPen(QColor("#64748b"))
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, display_text or "—")

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(100, 36)


class VideoTableModel(QAbstractTableModel):
    HEADERS = ("", "STT", "Mã video", "Tên video", "URL nguồn", "Trạng thái", "Tiến độ tải")
    SORTABLE_COLUMNS = {1: "sequence_number", 2: "video_code", 3: "title"}

    def __init__(self, videos: list[object] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._all_videos = list(videos or [])
        self._videos = list(self._all_videos)
        self._status_filter = "all"
        self._text_filter = ""
        self._text_field = "title"
        self._sort_field = "sequence_number"
        self._sort_descending = False
        self._checked: set[str] = set()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._videos)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            label = self.HEADERS[section]
            field = self.SORTABLE_COLUMNS.get(section)
            if field is None:
                return label
            if field == self._sort_field:
                return f"{label}  {'▼' if self._sort_descending else '▲'}"
            return f"{label}  ⇅"
        return None

    @staticmethod
    def _value(video: object, key: str, default=""):
        if isinstance(video, Mapping):
            return video.get(key, default)
        return getattr(video, key, default)

    def _video_key(self, video: object, row: int) -> str:
        return str(self._value(video, "id", row))

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        video = self._videos[index.row()]
        column = index.column()
        if column == 0 and role == Qt.ItemDataRole.CheckStateRole:
            checked = self._video_key(video, index.row()) in self._checked
            return Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.ToolTipRole:
            if column == 4:
                return self._value(video, "source_url")
            if column == 5:
                return self._value(video, "download_error") or None

        if role == Qt.ItemDataRole.UserRole:
            if column == 5:
                return str(
                    self._value(video, "queue_status")
                    or self._value(video, "download_status", "pending")
                )
            if column == 6:
                try:
                    return float(self._value(video, "queue_progress", 0) or 0.0)
                except (ValueError, TypeError):
                    return 0.0

        if role == Qt.ItemDataRole.UserRole + 1:
            if column == 6:
                return str(
                    self._value(video, "queue_status")
                    or self._value(video, "download_status", "pending")
                )

        if role != Qt.ItemDataRole.DisplayRole:
            return None

        q_status = self._value(video, "queue_status")
        d_status = self._value(video, "download_status", "pending")
        status_key = str(q_status or d_status)
        values = (
            "",
            self._value(video, "sequence_number", self._value(video, "stt")),
            self._value(video, "video_code"),
            self._value(video, "title", self._value(video, "ten_video")),
            self._compact_url(str(self._value(video, "source_url", self._value(video, "url")))),
            {
                "queued": "Trong hàng đợi",
                "downloading": "Đang tải",
                "completed": "Đã tải",
                "failed": "Lỗi",
                "interrupted": "Bị gián đoạn",
                "cancelled": "Chờ tải",
            }.get(
                status_key,
                "Chờ tải",
            ),
            (
                "Đang tải…"
                if q_status == "downloading" and self._value(video, "queue_total_bytes") is None
                else f"{float(self._value(video, 'queue_progress', 0)):.0f}%"
                if q_status == "downloading"
                else "100%"
                if d_status == "completed"
                else "◌ Chờ tải"
                if q_status == "queued"
                else "—"
            ),
        )
        return str(values[column])

    @staticmethod
    def _compact_url(url: str) -> str:
        if len(url) <= 68:
            return url
        return f"{url[:42]}…{url[-22:]}"

    def flags(self, index: QModelIndex):
        flags = super().flags(index)
        if index.isValid() and index.column() == 0:
            flags |= Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
        return flags

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if (
            not index.isValid()
            or index.column() != 0
            or role != Qt.ItemDataRole.CheckStateRole
        ):
            return False
        key = self._video_key(self._videos[index.row()], index.row())
        if value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value):
            self._checked.add(key)
        else:
            self._checked.discard(key)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        return True

    def update_video_progress(
        self, video_id: str, progress: float, status: str = "downloading"
    ) -> None:
        for row, video in enumerate(self._videos):
            if self._video_key(video, row) == str(video_id):
                if isinstance(video, dict):
                    video["queue_status"] = status
                    video["queue_progress"] = progress
                elif hasattr(video, "__dict__"):
                    setattr(video, "queue_status", status)
                    setattr(video, "queue_progress", progress)
                index_5 = self.index(row, 5)
                index_6 = self.index(row, 6)
                self.dataChanged.emit(
                    index_5,
                    index_6,
                    [
                        Qt.ItemDataRole.DisplayRole,
                        Qt.ItemDataRole.UserRole,
                        Qt.ItemDataRole.UserRole + 1,
                    ],
                )
                break

    def selected_video_ids(self) -> list[str]:
        return [
            self._video_key(video, row)
            for row, video in enumerate(self._videos)
            if self._video_key(video, row) in self._checked
        ]

    def set_videos(self, videos: list[object]) -> None:
        self._all_videos = list(videos)
        self._refresh_view()

    def set_status_filter(self, value: str) -> None:
        normalized = {"Tất cả": "all", "Đã tải": "downloaded", "Chưa tải": "not_downloaded"}.get(value, value)
        if normalized == self._status_filter:
            return
        self._status_filter = normalized
        self._refresh_view()

    def set_text_filter(self, field: str, value: str) -> None:
        self._text_field = field
        self._text_filter = value.strip().casefold()
        self._refresh_view()

    def set_sort(self, field: str, descending: bool) -> None:
        self._sort_field = field
        self._sort_descending = descending
        self._refresh_view()
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 1, 3)

    def _refresh_view(self) -> None:
        selected = self._checked
        self.beginResetModel()
        self._videos = self._filtered_videos()
        self._checked = {
            self._video_key(video, row)
            for row, video in enumerate(self._videos)
            if self._video_key(video, row) in selected
        }
        self.endResetModel()

    def _filtered_videos(self) -> list[object]:
        filtered = []
        for video in self._all_videos:
            status = str(self._value(video, "download_status", "pending"))
            completed = status == "completed"
            if self._status_filter == "downloaded" and not completed:
                continue
            if self._status_filter == "not_downloaded" and completed:
                continue
            if self._text_filter:
                field_value = str(self._value(video, self._text_field, "")).casefold()
                if self._text_filter not in field_value:
                    continue
            filtered.append(video)
        if self._sort_field == "sequence_number":
            key = lambda video: int(self._value(video, "sequence_number", 0))
        else:
            key = lambda video: str(self._value(video, self._sort_field, "")).casefold()
        return sorted(filtered, key=key, reverse=self._sort_descending)

    def video_id_at(self, row: int) -> str | None:
        if not 0 <= row < len(self._videos):
            return None
        return self._video_key(self._videos[row], row)

    def select_all(self) -> None:
        self._checked = {
            self._video_key(video, row) for row, video in enumerate(self._videos)
        }
        if self._videos:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._videos) - 1, 0),
                [Qt.ItemDataRole.CheckStateRole],
            )

    def clear_selection(self) -> None:
        self._checked.clear()
        if self._videos:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._videos) - 1, 0),
                [Qt.ItemDataRole.CheckStateRole],
            )


class VideoTable(QTableView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.video_model = VideoTableModel(parent=self)
        self.setModel(self.video_model)
        self.setObjectName("videoTable")
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setSortingEnabled(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(38)
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header = self.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(False)
        header.sectionClicked.connect(self._sort_from_header)
        self._sort_field = "sequence_number"
        self._sort_descending = False
        self.setMinimumHeight(190)

        # Custom delegates for modern Status Badges and Progress indicators
        self.setItemDelegateForColumn(5, VideoStatusBadgeDelegate(self))
        self.setItemDelegateForColumn(6, VideoProgressDelegate(self))

    def selected_video_ids(self) -> list[str]:
        return self.video_model.selected_video_ids()

    def set_videos(self, videos: list[object]) -> None:
        self.video_model.set_videos(videos)

    def update_video_progress(
        self, video_id: str, progress: float, status: str = "downloading"
    ) -> None:
        self.video_model.update_video_progress(video_id, progress, status)

    def set_status_filter(self, value: str) -> None:
        self.video_model.set_status_filter(value)

    def set_text_filter(self, field: str, value: str) -> None:
        self.video_model.set_text_filter(field, value)

    def set_sort(self, field: str, descending: bool) -> None:
        self.video_model.set_sort(field, descending)

    def _sort_from_header(self, section: int) -> None:
        field = VideoTableModel.SORTABLE_COLUMNS.get(section)
        if field is None:
            return
        self._sort_descending = not self._sort_descending if field == self._sort_field else False
        self._sort_field = field
        self.video_model.set_sort(field, self._sort_descending)

    def select_all(self) -> None:
        self.video_model.select_all()

    def clear_selection(self) -> None:
        self.video_model.clear_selection()

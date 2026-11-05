from collections.abc import Mapping

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import QHeaderView, QTableView, QWidget


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
        if role != Qt.ItemDataRole.DisplayRole:
            return None
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
                str(
                    self._value(
                        video,
                        "queue_status",
                        self._value(video, "download_status", "pending"),
                    )
                ),
                "Chờ tải",
            ),
            (
                "Đang tải…"
                if self._value(video, "queue_status") == "downloading"
                and self._value(video, "queue_total_bytes") is None
                else f"{float(self._value(video, 'queue_progress', 0)):.0f}%"
                if self._value(video, "queue_status") == "downloading"
                else "100%"
                if self._value(video, "download_status") == "completed"
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

    def selected_video_ids(self) -> list[str]:
        return self.video_model.selected_video_ids()

    def set_videos(self, videos: list[object]) -> None:
        self.video_model.set_videos(videos)

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

from collections.abc import Mapping

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import QHeaderView, QTableView, QWidget


class VideoTableModel(QAbstractTableModel):
    HEADERS = ("", "STT", "Mã video", "Tên video", "URL nguồn", "Trạng thái", "Tiến độ tải")

    def __init__(self, videos: list[object] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._videos = videos or []
        self._checked: set[str] = set()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._videos)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
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
        if role == Qt.ItemDataRole.ToolTipRole and column == 4:
            return self._value(video, "source_url")
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        values = (
            "",
            self._value(video, "sequence_number", self._value(video, "stt")),
            self._value(video, "video_code"),
            self._value(video, "title", self._value(video, "ten_video")),
            self._compact_url(str(self._value(video, "source_url", self._value(video, "url")))),
            {"pending": "Chờ tải", "waiting": "Chờ tải", "downloading": "Đang tải", "completed": "Đã tải", "failed": "Lỗi"}.get(str(self._value(video, "download_status", "pending")), "Không rõ"),
            "100%" if self._value(video, "download_status") == "completed" else "—",
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
        self.beginResetModel()
        self._videos = list(videos)
        self._checked.clear()
        self.endResetModel()

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
        self.setMinimumHeight(190)

    def selected_video_ids(self) -> list[str]:
        return self.video_model.selected_video_ids()

    def set_videos(self, videos: list[object]) -> None:
        self.video_model.set_videos(videos)

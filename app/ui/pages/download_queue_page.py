from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import (
    QAbstractTableModel,
    QItemSelectionModel,
    QModelIndex,
    QRect,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return "—"


class DownloadQueueTableModel(QAbstractTableModel):
    HEADERS = (
        "Dự án",
        "Mã video",
        "Tên video",
        "Trạng thái",
        "Tiến độ",
        "Tốc độ",
        "Dung lượng",
        "Còn lại",
        "Lỗi gần nhất",
    )

    def __init__(self, jobs: list[Mapping], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._jobs = list(jobs)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._jobs)

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
        job = self._jobs[index.row()]
        status = str(job["status"])
        if role == Qt.ItemDataRole.UserRole and index.column() == 4:
            if status == "downloading" and job["total_bytes"] is None:
                return {"indeterminate": True, "value": 0, "text": "Đang tải…"}
            if status == "queued":
                return {"indeterminate": False, "value": 0, "text": "Đang chờ"}
            if status in ("failed", "interrupted", "cancelled"):
                return {
                    "indeterminate": False,
                    "value": int(job["progress"] or 0),
                    "text": "—" if job["total_bytes"] is None else f"{job['progress']:.0f}%",
                }
            return {
                "indeterminate": False,
                "value": int(job["progress"] or 0),
                "text": f"{job['progress']:.0f}%",
            }
        if role == Qt.ItemDataRole.ToolTipRole:
            tooltips = (
                job["project_name"],
                job["video_code"],
                job["title"],
                status,
                None,
                None,
                None,
                None,
                job["error_message"] or "",
            )
            return tooltips[index.column()]
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        speed = job["speed"]
        total_bytes = job["total_bytes"]
        downloaded = int(job["downloaded_bytes"] or 0)
        eta = "—"
        if job["eta_seconds"] is not None:
            seconds = int(job["eta_seconds"])
            eta = f"{seconds // 60:02d}:{seconds % 60:02d}"
        elif speed and total_bytes and speed > 0:
            remaining = max(0, total_bytes - downloaded) / speed
            eta = f"{int(remaining // 60):02d}:{int(remaining % 60):02d}"
        if status == "downloading" and total_bytes is None:
            progress_text = "Đang tải…"
        elif status == "queued":
            progress_text = "Đang chờ"
        elif status in ("failed", "interrupted", "cancelled") and total_bytes is None:
            progress_text = "—"
        else:
            progress_text = f"{float(job['progress'] or 0):.0f}%"
        values = (
            job["project_name"],
            job["video_code"],
            job["title"],
            {
                "queued": "Đang chờ",
                "downloading": "Đang tải",
                "completed": "Hoàn tất",
                "failed": "Thất bại",
                "interrupted": "Bị gián đoạn",
                "cancelled": "Đã hủy",
            }.get(status, status),
            progress_text,
            f"{_format_bytes(int(speed))}/s" if speed else "—",
            (
                f"{_format_bytes(downloaded)} / {_format_bytes(total_bytes)}"
                if total_bytes
                else _format_bytes(downloaded)
            ),
            eta,
            job["error_message"] or "—",
        )
        return str(values[index.column()])

    def job_id(self, row: int) -> str:
        return str(self._jobs[row]["job_id"])

    def row_for_job(self, job_id: str) -> int | None:
        for row, job in enumerate(self._jobs):
            if str(job["job_id"]) == job_id:
                return row
        return None

    def set_jobs(self, jobs: list[Mapping]) -> None:
        self.beginResetModel()
        self._jobs = list(jobs)
        self.endResetModel()

    def update_progress(
        self,
        job_id: str,
        percent: float,
        downloaded: int,
        total: int | None,
        speed: float | None,
        eta: int | None,
    ) -> None:
        row = self.row_for_job(job_id)
        if row is None:
            return
        job = dict(self._jobs[row])
        job.update(
            progress=percent,
            downloaded_bytes=downloaded,
            total_bytes=total,
            speed=speed,
            eta_seconds=eta,
        )
        self._jobs[row] = job
        self.dataChanged.emit(self.index(row, 4), self.index(row, 7))


class DownloadQueueTable(QTableView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.queue_model = DownloadQueueTableModel([], self)
        self.setModel(self.queue_model)
        self.setObjectName("downloadQueueTable")
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setWordWrap(False)
        self.verticalHeader().hide()
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in {
            0: 155,
            1: 80,
            3: 85,
            4: 105,
            5: 75,
            6: 155,
            7: 55,
            8: 145,
        }.items():
            self.setColumnWidth(column, width)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.setItemDelegateForColumn(4, _ProgressBarDelegate(self))

    def selected_job_ids(self) -> list[str]:
        rows = sorted({index.row() for index in self.selectionModel().selectedRows()})
        return [self.queue_model.job_id(row) for row in rows]

    def set_jobs(self, jobs: list[Mapping]) -> None:
        selected_ids = self.selected_job_ids()
        self.queue_model.set_jobs(jobs)
        selection = self.selectionModel()
        for job_id in selected_ids:
            row = self.queue_model.row_for_job(job_id)
            if row is not None:
                selection.select(
                    self.queue_model.index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )


class DownloadQueuePage(QWidget):
    backRequested = Signal()
    startRequested = Signal()
    pauseRequested = Signal()
    resumeRequested = Signal()
    retryRequested = Signal()
    cancelRequested = Signal()
    prioritizeRequested = Signal()
    deleteSelectedRequested = Signal()
    deleteFailedRequested = Signal()
    deleteCompletedRequested = Signal()
    deleteInactiveRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        back = QPushButton("Quay lại")
        back.setObjectName("back")
        back.clicked.connect(self.backRequested.emit)
        layout.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)
        title = QLabel("Hàng đợi tải video")
        title.setObjectName("title")
        layout.addWidget(title)
        self.summary = QLabel("Các job được lưu trong SQLite.")
        self.summary.setObjectName("detailMeta")
        layout.addWidget(self.summary)

        controls = QHBoxLayout()
        self.start_button = QPushButton("Bắt đầu hàng đợi")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.startRequested.emit)
        self.pause_button = QPushButton("Tạm dừng sau video hiện tại")
        self.pause_button.setObjectName("secondary")
        self.pause_button.clicked.connect(self.pauseRequested.emit)
        self.resume_button = QPushButton("Tiếp tục")
        self.resume_button.setObjectName("secondary")
        self.resume_button.clicked.connect(self.resumeRequested.emit)
        controls.addWidget(self.start_button)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.resume_button)
        controls.addStretch()
        self.retry_button = QPushButton("Retry video lỗi")
        self.retry_button.setObjectName("secondary")
        self.retry_button.clicked.connect(self.retryRequested.emit)
        self.prioritize_button = QPushButton("Ưu tiên video đã chọn")
        self.prioritize_button.setObjectName("secondary")
        self.prioritize_button.clicked.connect(self.prioritizeRequested.emit)
        self.cancel_button = QPushButton("Xóa job đang chờ")
        self.cancel_button.setObjectName("secondary")
        self.cancel_button.clicked.connect(self.cancelRequested.emit)
        controls.addWidget(self.retry_button)
        controls.addWidget(self.prioritize_button)
        controls.addWidget(self.cancel_button)
        layout.addLayout(controls)

        delete_controls = QHBoxLayout()
        self.delete_selected_button = QPushButton("🗑 Xóa job đã chọn")
        self.delete_selected_button.setObjectName("danger")
        self.delete_selected_button.clicked.connect(
            self.deleteSelectedRequested.emit
        )
        self.delete_failed_button = QPushButton("Xóa job thất bại")
        self.delete_failed_button.setObjectName("danger")
        self.delete_failed_button.clicked.connect(self.deleteFailedRequested.emit)
        self.delete_completed_button = QPushButton("Xóa lịch sử hoàn tất")
        self.delete_completed_button.setObjectName("danger")
        self.delete_completed_button.clicked.connect(
            self.deleteCompletedRequested.emit
        )
        self.delete_inactive_button = QPushButton("Xóa toàn bộ job không hoạt động")
        self.delete_inactive_button.setObjectName("danger")
        self.delete_inactive_button.clicked.connect(
            self.deleteInactiveRequested.emit
        )
        delete_controls.addWidget(self.delete_selected_button)
        delete_controls.addWidget(self.delete_failed_button)
        delete_controls.addWidget(self.delete_completed_button)
        delete_controls.addWidget(self.delete_inactive_button)
        delete_controls.addStretch()
        layout.addLayout(delete_controls)

        self.queue_status = QLabel("Hàng đợi đang dừng")
        self.queue_status.setObjectName("detailMeta")
        layout.addWidget(self.queue_status)
        self.table = DownloadQueueTable(self)
        self.table.setMinimumHeight(190)
        layout.addWidget(self.table, 1)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.selectionModel().selectionChanged.connect(
            self._update_delete_buttons
        )
        self._job_counts = {}
        self._update_delete_buttons()

    def _show_context_menu(self, position) -> None:
        index = self.table.indexAt(position)
        if index.isValid() and not self.table.selectionModel().isSelected(index):
            self.table.selectRow(index.row())
        self._create_context_menu().exec(self.table.viewport().mapToGlobal(position))

    def _create_context_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("Xóa job đã chọn", self.deleteSelectedRequested.emit)
        menu.addSeparator()
        menu.addAction("Xóa tất cả job thất bại", self.deleteFailedRequested.emit)
        menu.addAction(
            "Xóa lịch sử job hoàn tất", self.deleteCompletedRequested.emit
        )
        menu.addAction(
            "Xóa toàn bộ job không hoạt động", self.deleteInactiveRequested.emit
        )
        return menu

    def _update_delete_buttons(self, *_args) -> None:
        self.delete_selected_button.setEnabled(
            bool(self.table.selected_job_ids())
        )
        self.delete_failed_button.setEnabled(self._job_counts.get("failed", 0) > 0)
        self.delete_completed_button.setEnabled(
            self._job_counts.get("completed", 0) > 0
        )
        self.delete_inactive_button.setEnabled(
            self._job_counts.get("inactive", 0) > 0
        )

    def set_jobs(self, jobs: list[Mapping]) -> None:
        self.table.set_jobs(jobs)
        completed = sum(job["status"] == "completed" for job in jobs)
        failed = sum(job["status"] in ("failed", "interrupted") for job in jobs)
        waiting = sum(job["status"] == "queued" for job in jobs)
        active = sum(job["status"] == "downloading" for job in jobs)
        self._job_counts = {
            "failed": failed,
            "completed": completed,
            "inactive": sum(
                job["status"]
                in ("completed", "failed", "interrupted", "cancelled")
                for job in jobs
            ),
        }
        self.summary.setText(
            f"{len(jobs)} job · Hoàn tất {completed} · Lỗi {failed} · "
            f"Đang tải {active} · Đang chờ {waiting}"
        )
        self._update_delete_buttons()


class _ProgressBarDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index) -> None:
        progress = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(progress, dict):
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(6, 6, -6, -6)
        painter.setPen(QColor("#304766"))
        painter.setBrush(QColor("#0c1729"))
        painter.drawRoundedRect(rect, 4, 4)

        inner = rect.adjusted(1, 1, -1, -1)
        if progress["indeterminate"]:
            width = max(18, int(inner.width() * 0.28))
            left = inner.left() + (inner.width() - width) // 2
        else:
            width = int(inner.width() * max(0, min(progress["value"], 100)) / 100)
            left = inner.left()
        if width:
            chunk = QRect(left, inner.top(), width, inner.height())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#2563eb"))
            painter.drawRoundedRect(chunk, 3, 3)

        painter.setPen(QColor("#e2e8f0"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, progress["text"])
        painter.restore()

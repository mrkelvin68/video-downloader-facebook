from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.services.matrix_service import (
    DEFAULT_PAGES,
    DEFAULT_SLOTS,
    NO_REPEAT,
    REPEAT_BETWEEN_PAGES,
    validate_matrix_configuration,
)


class MatrixConfigurationDialog(QDialog):
    def __init__(
        self, parent: QWidget | None = None, configuration: dict | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cấu hình slot & page")
        self.setMinimumSize(620, 560)
        configuration = configuration or {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        heading = QLabel("Cấu hình slot & page")
        heading.setObjectName("dialogTitle")
        layout.addWidget(heading)
        description = QLabel(
            "Lịch chỉ được lưu trong SQLite trên máy này. Ứng dụng không tự đăng video."
        )
        description.setObjectName("dialogSubtitle")
        description.setWordWrap(True)
        layout.addWidget(description)

        form = QFormLayout()
        self.name = QLineEdit(configuration.get("name", "Ma trận 1"))
        form.addRow("Tên ma trận", self.name)
        self.page_names = QTextEdit()
        self.page_names.setObjectName("matrixListEditor")
        self.page_names.setPlainText(
            "\n".join(configuration.get("pages") or DEFAULT_PAGES)
        )
        self.page_names.setMinimumHeight(100)
        self.page_names.setMaximumHeight(145)
        form.addRow("Danh sách Page", self._list_editor(self.page_names, "Page"))
        self.slot_times = QTextEdit()
        self.slot_times.setObjectName("matrixListEditor")
        self.slot_times.setPlainText(
            "\n".join(configuration.get("slots") or DEFAULT_SLOTS)
        )
        self.slot_times.setMinimumHeight(85)
        self.slot_times.setMaximumHeight(125)
        form.addRow("Khung giờ (HH:MM)", self._list_editor(self.slot_times, "khung giờ"))

        self.start_date = QDateEdit(calendarPopup=True)
        self.start_date.setDisplayFormat("dd/MM/yyyy")
        if configuration.get("start_date"):
            self.start_date.setDate(QDate.fromString(configuration["start_date"], "yyyy-MM-dd"))
        else:
            self.start_date.setDate(QDate.currentDate())
        form.addRow("Ngày bắt đầu", self.start_date)
        self.day_count = QSpinBox()
        self.day_count.setRange(1, 365)
        self.day_count.setValue(int(configuration.get("day_count", 5)))
        form.addRow("Số ngày tạo lịch", self.day_count)
        self.repeat_mode = QComboBox()
        self.repeat_mode.addItem("Không lặp video trong ma trận", NO_REPEAT)
        self.repeat_mode.addItem(
            "Cho phép lặp giữa các Page", REPEAT_BETWEEN_PAGES
        )
        selected_mode = configuration.get("repeat_mode", NO_REPEAT)
        self.repeat_mode.setCurrentIndex(
            max(0, self.repeat_mode.findData(selected_mode))
        )
        form.addRow("Quy tắc phân phối", self.repeat_mode)
        layout.addLayout(form)

        self.error = QLabel()
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        layout.addStretch()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _list_editor(editor: QTextEdit, label: str) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        layout.addWidget(editor)
        actions = QHBoxLayout()
        add = QPushButton(f"Thêm {label}")
        remove = QPushButton(f"Xóa dòng cuối")
        add.setObjectName("secondary")
        remove.setObjectName("secondary")
        def append_line() -> None:
            lines = editor.toPlainText().splitlines()
            maximum = 50 if label == "Page" else 24
            if len(lines) >= maximum:
                return
            current = "\n".join(lines).rstrip()
            if label == "Page":
                existing = {line.strip().casefold() for line in lines}
                value = next(
                    (
                        f"Page {number}"
                        for number in range(1, 52)
                        if f"page {number}" not in existing
                    ),
                    None,
                )
            else:
                existing = {line.strip() for line in lines}
                value = next(
                    (
                        f"{hour:02d}:00"
                        for hour in range(24)
                        if f"{hour:02d}:00" not in existing
                    ),
                    None,
                )
            if value is None:
                return
            editor.setPlainText(
                f"{current}\n{value}" if current else value
            )

        add.clicked.connect(append_line)
        remove.clicked.connect(
            lambda: editor.setPlainText(
                "\n".join(editor.toPlainText().splitlines()[:-1])
            )
        )
        actions.addWidget(add)
        actions.addWidget(remove)
        actions.addStretch()
        layout.addLayout(actions)
        return wrapper

    def configuration(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "pages": self.page_names.toPlainText().splitlines(),
            "slots": self.slot_times.toPlainText().splitlines(),
            "start_date": self.start_date.date().toString("yyyy-MM-dd"),
            "day_count": self.day_count.value(),
            "repeat_mode": self.repeat_mode.currentData(),
        }

    def _accept_if_valid(self) -> None:
        configuration = self.configuration()
        try:
            validate_matrix_configuration(**configuration)
        except ValueError as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        self.accept()


class MatrixEntryDialog(QDialog):
    def __init__(
        self,
        entry: dict,
        videos: list[dict],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Chỉnh sửa ô lịch")
        self.setMinimumSize(520, 390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.addWidget(QLabel(f"{entry['page_name']} · {entry['scheduled_date']} · {entry['scheduled_time']}"))

        form = QFormLayout()
        self.video = QComboBox()
        self.video.addItem("Không chọn video", None)
        for video in videos:
            download_status = (
                "Đã tải"
                if video["download_status"] == "completed"
                else video["download_status"]
            )
            self.video.addItem(
                f"{video['video_code']} · {video['title']} ({download_status})",
                video["id"],
            )
        if entry.get("video_id"):
            index = self.video.findData(entry["video_id"])
            if index >= 0:
                self.video.setCurrentIndex(index)
        form.addRow("Video", self.video)
        self.caption = QTextEdit(entry.get("caption", ""))
        self.caption.setPlaceholderText("Nhập caption cho video")
        form.addRow("Caption", self.caption)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str | None, str]:
        return self.video.currentData(), self.caption.toPlainText()

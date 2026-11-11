from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class VideoDeleteDialog(QDialog):
    def __init__(
        self,
        project_name: str,
        selected_count: int,
        total_count: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Xóa video")
        self.setModal(True)
        self.setMinimumWidth(520)

        self.selected_count = selected_count
        self.total_count = total_count

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 20, 24, 20)

        heading = QLabel("Xác nhận xóa video")
        heading.setObjectName("dialogTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(heading)

        info_label = QLabel(
            f"Dự án: <b>{project_name}</b> &nbsp;|&nbsp; Tổng số: <b>{total_count}</b> video"
        )
        layout.addWidget(info_label)

        scope_box = QGroupBox("Phạm vi xóa")
        scope_layout = QVBoxLayout(scope_box)
        scope_layout.setSpacing(8)

        self.scope_selected_radio = QRadioButton(
            f"Chỉ xóa các video đang chọn ({selected_count} video)"
        )
        self.scope_all_radio = QRadioButton(
            f"Chọn tất cả video trong dự án ({total_count} video)"
        )

        if selected_count > 0:
            self.scope_selected_radio.setChecked(True)
        else:
            self.scope_all_radio.setChecked(True)
            self.scope_selected_radio.setEnabled(False)
            self.scope_selected_radio.setText(
                "Chỉ xóa các video đang chọn (chưa tick chọn video nào)"
            )

        scope_layout.addWidget(self.scope_selected_radio)
        scope_layout.addWidget(self.scope_all_radio)
        layout.addWidget(scope_box)

        mode_box = QGroupBox("Tùy chọn phương thức xóa")
        mode_layout = QVBoxLayout(mode_box)
        mode_layout.setSpacing(8)

        self.mode_db_only_radio = QRadioButton(
            "Chỉ xóa trong cơ sở dữ liệu (Database)"
        )
        self.mode_db_only_radio.setChecked(True)
        self.mode_db_only_radio.setObjectName("modeDbOnly")
        note_db = QLabel("  • Giữ nguyên toàn bộ tệp video và thumbnail đã tải trên máy tính.")
        note_db.setObjectName("detailMeta")

        self.mode_db_and_local_radio = QRadioButton(
            "Xóa cả cơ sở dữ liệu và tệp video trên máy (Local files)"
        )
        self.mode_db_and_local_radio.setObjectName("modeDbAndLocal")
        note_local = QLabel("  • Xóa bản ghi trong database và xóa vĩnh viễn tệp video/thumbnail trên ổ đĩa.")
        note_local.setObjectName("error")
        note_local.setWordWrap(True)

        mode_layout.addWidget(self.mode_db_only_radio)
        mode_layout.addWidget(note_db)
        mode_layout.addSpacing(4)
        mode_layout.addWidget(self.mode_db_and_local_radio)
        mode_layout.addWidget(note_local)
        layout.addWidget(mode_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Hủy")
        delete_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        delete_btn.setText("Xác nhận xóa")
        delete_btn.setObjectName("danger")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def is_delete_all(self) -> bool:
        return self.scope_all_radio.isChecked()

    def is_delete_local_files(self) -> bool:
        return self.mode_db_and_local_radio.isChecked()

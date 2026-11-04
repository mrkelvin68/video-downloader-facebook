from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class ProjectDeleteDialog(QDialog):
    def __init__(
        self,
        name: str,
        output_dir: str,
        video_count: int,
        job_count: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Xóa dự án")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.delete_metadata = QRadioButton("Chỉ xóa dự án khỏi ứng dụng")
        self.delete_metadata.setChecked(True)
        self.delete_metadata.setObjectName("deleteProjectMetadata")
        self.delete_files_radio = QRadioButton(
            "Xóa dự án và toàn bộ video trên ổ đĩa"
        )
        self.delete_files_radio.setObjectName("deleteProjectFiles")
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        heading = QLabel("Xác nhận xóa dự án")
        heading.setObjectName("dialogTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(heading)
        layout.addWidget(QLabel(f"Tên dự án: {name}"))
        path = QLabel(f"Thư mục: {output_dir}")
        path.setWordWrap(True)
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(path)
        layout.addWidget(QLabel(f"Số video: {video_count}"))
        layout.addWidget(QLabel(f"Số job tải liên quan: {job_count}"))
        layout.addSpacing(6)
        layout.addWidget(self.delete_metadata)
        layout.addWidget(QLabel("Giữ nguyên toàn bộ tệp trên ổ đĩa."))
        layout.addWidget(self.delete_files_radio)
        warning = QLabel(
            "Xóa tệp sẽ xóa vĩnh viễn nội dung trong thư mục dự án. "
            "Symlink và junction sẽ bị từ chối."
        )
        warning.setObjectName("error")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Hủy")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Tiếp tục")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

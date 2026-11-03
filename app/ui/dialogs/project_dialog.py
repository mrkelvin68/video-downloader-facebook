from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyle,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.validators import validate_output_dir, validate_windows_name
from app.models.project import Project
from app.ui.widgets.sidebar import button_icon


class ProjectDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, project: Project | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Chỉnh sửa dự án" if project else "Tạo dự án mới")
        self.setModal(True)
        self.setMinimumWidth(610)
        self.setMaximumWidth(680)

        self.name = QLineEdit(project.name if project else "")
        self.prefix = QLineEdit(project.prefix if project else "")
        self.output_dir = QLineEdit(project.output_dir if project else "")
        self.notes = QTextEdit(project.notes if project else "")
        self.name.setPlaceholderText("Ví dụ: Series review phim")
        self.prefix.setPlaceholderText("Ví dụ: ep1-6")
        self.output_dir.setPlaceholderText(r"D:\Videos\ep1_6")
        self.notes.setPlaceholderText("Ghi chú cho dự án (không bắt buộc)")
        self.notes.setFixedHeight(78)

        header = QFrame()
        header.setObjectName("dialogHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 20, 24, 18)
        icon = QLabel()
        icon.setPixmap(button_icon(self, QStyle.StandardPixmap.SP_FileDialogNewFolder).pixmap(QSize(22, 22)))
        icon.setObjectName("dialogIcon")
        heading = QVBoxLayout()
        title = QLabel("Chỉnh sửa dự án" if project else "Tạo dự án mới")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("Thông tin này được lưu an toàn trong SQLite.")
        subtitle.setObjectName("dialogSubtitle")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header_layout.addWidget(icon)
        header_layout.addLayout(heading)
        header_layout.addStretch()

        self.error = QLabel()
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.hide()
        browse = QPushButton("Duyệt thư mục")
        browse.setObjectName("secondary")
        browse.setIcon(button_icon(self, QStyle.StandardPixmap.SP_DirOpenIcon))
        browse.clicked.connect(self.pick_folder)
        folder_layout = QHBoxLayout()
        folder_layout.setSpacing(8)
        folder_layout.addWidget(self.output_dir, 1)
        folder_layout.addWidget(browse)
        prefix_box = QVBoxLayout()
        prefix_box.setSpacing(5)
        prefix_box.addWidget(self.prefix)
        example = QLabel("Ví dụ mã video: ep1-6-1")
        example.setObjectName("fieldHint")
        prefix_box.addWidget(example)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(18)
        form.addRow("Tên dự án *", self.name)
        form.addRow("Prefix video *", prefix_box)
        form.addRow("Thư mục lưu video *", folder_layout)
        form.addRow("Ghi chú", self.notes)
        form_area = QWidget()
        form_layout = QVBoxLayout(form_area)
        form_layout.setContentsMargins(24, 20, 24, 16)
        form_layout.addLayout(form)
        form_layout.addWidget(self.error)
        footer = QFrame()
        footer.setObjectName("dialogFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 14, 24, 14)
        footer_layout.addStretch()
        cancel = QPushButton("Hủy")
        save = QPushButton("Tạo dự án" if not project else "Lưu thay đổi")
        save.setObjectName("primary")
        save.setIcon(button_icon(self, QStyle.StandardPixmap.SP_DialogApplyButton))
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.validate_and_accept)
        footer_layout.addWidget(cancel)
        footer_layout.addWidget(save)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(form_area)
        layout.addWidget(footer)
        self.name.setFocus()

    def pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục cha")
        if folder:
            self.output_dir.setText(str(Path(folder) / self.prefix.text().strip()))

    def validate_and_accept(self) -> None:
        try:
            same_folder = bool(
                self.project
                and self.output_dir.text()
                and Path(self.output_dir.text()).resolve()
                == Path(self.project.output_dir).resolve()
            )
            validate_windows_name(self.name.text(), "Tên dự án")
            validate_windows_name(self.prefix.text(), "Tiền tố")
            validate_output_dir(self.output_dir.text(), may_exist=same_folder)
        except (OSError, ValueError) as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        self.accept()

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from app.models.project import Project
from app.ui.widgets.sidebar import button_icon


class ProjectCard(QFrame):
    editRequested = Signal(object)
    openRequested = Signal(object)
    matrixRequested = Signal(object)

    def __init__(self, project: Project, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 19, 22, 18)
        layout.setSpacing(13)
        top = QHBoxLayout()
        title = QLabel(project.name)
        title.setObjectName("cardTitle")
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tag = QLabel(project.prefix)
        tag.setObjectName("tag")
        status = QLabel("Chờ tải")
        status.setObjectName("status")
        matrix_count = QLabel(f"Ma trận: {project.matrix_count}")
        matrix_count.setObjectName("matrixCount")
        top.addWidget(title)
        top.addWidget(tag)
        top.addWidget(matrix_count)
        top.addWidget(status)
        layout.addLayout(top)
        path = QLabel(project.output_dir)
        path.setObjectName("path")
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path.setToolTip(project.output_dir)
        layout.addWidget(path)
        bottom = QHBoxLayout()
        created = QLabel(f"Tạo ngày {project.created_at[:10]}")
        created.setObjectName("muted")
        bottom.addWidget(created)
        bottom.addStretch()
        edit = QPushButton("Chỉnh sửa")
        edit.setObjectName("secondary")
        edit.setIcon(button_icon(self, QStyle.StandardPixmap.SP_FileDialogContentsView))
        edit.clicked.connect(lambda: self.editRequested.emit(project))
        open_project = QPushButton("Mở dự án")
        open_project.setObjectName("primary")
        open_project.setIcon(button_icon(self, QStyle.StandardPixmap.SP_ArrowForward))
        open_project.clicked.connect(lambda: self.openRequested.emit(project))
        matrix = QPushButton("Ma trận")
        matrix.setObjectName("secondary")
        matrix.setIcon(button_icon(self, QStyle.StandardPixmap.SP_FileDialogListView))
        matrix.clicked.connect(lambda: self.matrixRequested.emit(project))
        bottom.addWidget(edit)
        bottom.addWidget(open_project)
        bottom.addWidget(matrix)
        layout.addLayout(bottom)

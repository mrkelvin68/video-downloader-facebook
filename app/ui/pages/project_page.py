from PySide6.QtCore import Signal, QSize, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from app.models.project import Project
from app.ui.widgets.project_card import ProjectCard
from app.ui.widgets.sidebar import button_icon


class ProjectPage(QWidget):
    createRequested = Signal()
    editRequested = Signal(object)
    openRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(0)
        heading = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        title = QLabel("Quản lý dự án")
        title.setObjectName("title")
        title_col.addWidget(title)
        title_col.addWidget(
            QLabel("Mỗi dự án dùng một thư mục video riêng, không ghi đè dữ liệu cũ.")
        )
        heading.addLayout(title_col)
        heading.addStretch()
        add = QPushButton("Tạo dự án mới")
        add.setObjectName("primary")
        add.setIcon(button_icon(self, QStyle.StandardPixmap.SP_FileDialogNewFolder))
        add.clicked.connect(self.createRequested.emit)
        heading.addWidget(add)
        layout.addLayout(heading)
        layout.addSpacing(26)
        self.project_scroll = QScrollArea()
        self.project_scroll.setWidgetResizable(True)
        self.project_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.project_container = QWidget()
        self.project_layout = QVBoxLayout(self.project_container)
        self.project_layout.setContentsMargins(0, 0, 8, 0)
        self.project_layout.setSpacing(12)
        self.project_scroll.setWidget(self.project_container)
        layout.addWidget(self.project_scroll, 1)

    def set_projects(self, projects: list[Project]) -> None:
        while self.project_layout.count():
            item = self.project_layout.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()
        if not projects:
            self.project_layout.addStretch()
            empty = QFrame()
            empty.setObjectName("emptyState")
            empty_layout = QVBoxLayout(empty)
            empty_layout.setContentsMargins(40, 44, 40, 44)
            empty_layout.setSpacing(10)
            icon = QLabel()
            icon.setPixmap(
                button_icon(self, QStyle.StandardPixmap.SP_FileDialogNewFolder).pixmap(
                    QSize(34, 34)
                )
            )
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            heading = QLabel("Chưa có dự án nào")
            heading.setObjectName("emptyTitle")
            heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
            description = QLabel("Tạo dự án đầu tiên để đăng ký thư mục lưu video.")
            description.setAlignment(Qt.AlignmentFlag.AlignCenter)
            create = QPushButton("Tạo dự án mới")
            create.setObjectName("primary")
            create.clicked.connect(self.createRequested.emit)
            empty_layout.addWidget(icon)
            empty_layout.addWidget(heading)
            empty_layout.addWidget(description)
            empty_layout.addSpacing(8)
            empty_layout.addWidget(create, alignment=Qt.AlignmentFlag.AlignCenter)
            self.project_layout.addWidget(empty)
            self.project_layout.addStretch(2)
            return
        for project in projects:
            card = ProjectCard(project, self.project_container)
            card.editRequested.connect(self.editRequested)
            card.openRequested.connect(self.openRequested)
            self.project_layout.addWidget(card)
        self.project_layout.addStretch()

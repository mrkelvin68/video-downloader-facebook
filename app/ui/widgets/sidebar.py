from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)


def button_icon(widget: QWidget, icon: QStyle.StandardPixmap):
    return widget.style().standardIcon(icon)


class Sidebar(QFrame):
    projectsRequested = Signal()
    queueRequested = Signal()
    viralReelsRequested = Signal()
    autoCommentRequested = Signal()
    settingsRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(240)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 22, 16, 18)
        layout.setSpacing(5)
        brand_row = QHBoxLayout()
        logo = QLabel()
        logo.setObjectName("brandMark")
        logo.setPixmap(
            button_icon(self, QStyle.StandardPixmap.SP_MediaPlay).pixmap(QSize(22, 22))
        )
        brand = QLabel("Video Downloader")
        brand.setObjectName("brand")
        brand_row.addWidget(logo)
        brand_row.addWidget(brand)
        layout.addLayout(brand_row)
        layout.addSpacing(30)
        section = QLabel("ĐIỀU HƯỚNG CHÍNH")
        section.setObjectName("navSection")
        layout.addWidget(section)
        layout.addSpacing(7)
        for text, icon, active, available in (
            ("Dashboard", QStyle.StandardPixmap.SP_ComputerIcon, False, False),
            ("Quản lý dự án", QStyle.StandardPixmap.SP_FileDialogContentsView, True, True),
            ("Hàng đợi tải video", QStyle.StandardPixmap.SP_ArrowDown, False, True),
            ("🔥 Viral Reels Finder", QStyle.StandardPixmap.SP_DialogYesButton, False, True),
            ("Auto Comment", QStyle.StandardPixmap.SP_MessageBoxInformation, False, True),
            ("Live Logs", QStyle.StandardPixmap.SP_FileDialogDetailedView, False, False),
            ("Cài đặt", QStyle.StandardPixmap.SP_FileDialogInfoView, False, True),
        ):
            button = QPushButton(text)
            button.setIcon(button_icon(self, icon))
            button.setProperty("active", active)
            button.setProperty("comingSoon", not available)
            button.setEnabled(available)
            if text == "Auto Comment":
                self.auto_comment_button = button
                button.clicked.connect(self.autoCommentRequested.emit)
            if not available:
                button.setToolTip("Chức năng sẽ có ở giai đoạn sau")
            elif text == "Quản lý dự án":
                self.projects_button = button
                button.clicked.connect(self.projectsRequested.emit)
            elif text == "Hàng đợi tải video":
                self.queue_button = button
                button.clicked.connect(self.queueRequested.emit)
            elif text == "🔥 Viral Reels Finder":
                self.viral_reels_button = button
                button.clicked.connect(self.viralReelsRequested.emit)
            elif text == "Cài đặt":
                self.settings_button = button
                button.clicked.connect(self.settingsRequested.emit)
            layout.addWidget(button)
        layout.addStretch()
        status = QFrame()
        status.setObjectName("storageStatus")
        status_layout = QVBoxLayout(status)
        status_layout.setContentsMargins(12, 11, 12, 11)
        status_layout.setSpacing(3)
        live = QLabel("SQLite đang hoạt động")
        live.setObjectName("storageTitle")
        status_layout.addWidget(live)
        status_layout.addWidget(QLabel("Phase 1–3.2 · Quản lý tải video"))
        layout.addWidget(status)

    def set_active(self, section: str) -> None:
        self.projects_button.setProperty("active", section == "projects")
        self.queue_button.setProperty("active", section == "queue")
        self.viral_reels_button.setProperty("active", section == "viral-reels")
        self.auto_comment_button.setProperty("active", section == "auto-comment")
        for button in (self.projects_button, self.queue_button, self.viral_reels_button, self.auto_comment_button):
            button.style().unpolish(button)
            button.style().polish(button)

from PySide6.QtCore import QSize
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
            ("Hàng đợi tải video", QStyle.StandardPixmap.SP_ArrowDown, False, False),
            ("Live Logs", QStyle.StandardPixmap.SP_FileDialogDetailedView, False, False),
            ("Cài đặt", QStyle.StandardPixmap.SP_FileDialogInfoView, False, False),
        ):
            button = QPushButton(text)
            button.setIcon(button_icon(self, icon))
            button.setProperty("active", active)
            button.setProperty("comingSoon", not available)
            button.setEnabled(available)
            if not available:
                button.setToolTip("Chức năng sẽ có ở giai đoạn sau")
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
        status_layout.addWidget(QLabel("Phase 1–3 · Quản lý & tải video"))
        layout.addWidget(status)

import re
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LoadingSpinner(QWidget):
    """Smooth rotating circular spinner for download status."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(40)  # 25 fps
        self._timer.timeout.connect(self._rotate)

    def _rotate(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def paintEvent(self, event) -> None:
        if not self._timer.isActive():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        radius = (min(self.width(), self.height()) - 4) / 2.0

        painter.translate(center)
        painter.rotate(self._angle)

        pen = QPen(QColor("#38bdf8"), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        rect = QRectF(-radius, -radius, radius * 2.0, radius * 2.0)
        painter.drawArc(rect, 0, 270 * 16)
        painter.end()


class ElidedLabel(QLabel):
    """Label that elides text on the right if it overflows."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._full_text = text

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self._update_elided()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_elided()

    def _update_elided(self) -> None:
        fm = self.fontMetrics()
        width = max(self.width(), 140)
        elided = fm.elidedText(self._full_text, Qt.TextElideMode.ElideRight, width)
        super().setText(elided)
        self.setToolTip(self._full_text if elided != self._full_text else "")


class DownloadToastCard(QFrame):
    """
    Modern SaaS-grade floating/docked download toast card.
    Displays active video name, rotating spinner, cancel button [X],
    animated gradient progress bar, and speed/ETA statistics.
    """

    cancelRequested = Signal()

    _PROGRESS_REGEX = re.compile(
        r"^Đang tải\s+([^:]+):\s*([\d\.]+)%\s*·\s*(.+)$", re.IGNORECASE
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("downloadToastCard")
        self.setMinimumWidth(320)
        self.setMaximumWidth(400)
        self.setFixedHeight(76)

        self._raw_text = "Hàng đợi đang dừng"
        self._is_active = False

        self._init_ui()
        self.set_idle("Hàng đợi đang dừng")

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 8, 12, 8)
        main_layout.setSpacing(5)

        # Header: Video title + Spinner + Cancel button
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self.title_label = ElidedLabel("Chưa có video tải", self)
        self.title_label.setObjectName("toastTitle")
        font = self.title_label.font()
        font.setPointSize(10)
        font.setWeight(QFont.Weight.DemiBold)
        self.title_label.setFont(font)

        self.spinner = LoadingSpinner(self)

        self.cancel_button = QPushButton("✕", self)
        self.cancel_button.setObjectName("toastCancelButton")
        self.cancel_button.setToolTip("Hủy tải xuống")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.cancelRequested.emit)

        header_layout.addWidget(self.title_label, 1)
        header_layout.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignVCenter)
        main_layout.addLayout(header_layout)

        # Progress bar
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setObjectName("toastProgressBar")
        self.progress_bar.setFixedHeight(5)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        main_layout.addWidget(self.progress_bar)

        # Bottom stats row: Progress % on left, Speed & ETA on right
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)

        self.progress_label = QLabel("Đang tải: 0%", self)
        self.progress_label.setObjectName("toastProgressLabel")
        font_sub = self.progress_label.font()
        font_sub.setPointSize(9)
        self.progress_label.setFont(font_sub)

        self.stats_label = QLabel("", self)
        self.stats_label.setObjectName("toastStatsLabel")
        self.stats_label.setFont(font_sub)

        bottom_layout.addWidget(self.progress_label, 0, Qt.AlignmentFlag.AlignLeft)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self.stats_label, 0, Qt.AlignmentFlag.AlignRight)
        main_layout.addLayout(bottom_layout)

    def setText(self, text: str) -> None:
        """Compatibility method matching QLabel.setText."""
        self._raw_text = text
        clean = text.strip()

        # Check for progress pattern: "Đang tải {code}: {percent}% · {rate}{remaining}"
        match = self._PROGRESS_REGEX.match(clean)
        if match:
            code = match.group(1).strip()
            try:
                percent = float(match.group(2))
            except ValueError:
                percent = 0.0
            rest = match.group(3).strip()
            # Normalize rest: "1.2 MB/s · còn 30s" -> "1.2 MB/s • Còn 30s"
            rest_clean = (
                rest.replace("·", "•")
                .replace("còn", "Còn")
                .replace("  ", " ")
                .strip()
            )
            self.update_download_progress(code, percent, rest_clean)
            return

        # Check for start of download: "Đang tải {code}…"
        if clean.startswith("Đang tải "):
            code = clean[9:].rstrip("…").rstrip(".").strip()
            self.update_download_progress(code, 0.0, "Đang kết nối…")
            return

        # Completion
        if "hoàn tất" in clean.lower():
            self.set_completed(clean)
            return

        # Paused
        if "tạm dừng" in clean.lower():
            self.set_paused(clean)
            return

        # Error
        if "lỗi" in clean.lower():
            self.set_error(clean)
            return

        # Idle or general status
        self.set_idle(clean)

    def text(self) -> str:
        """Compatibility method matching QLabel.text."""
        return self._raw_text

    def update_download_progress(
        self, code: str, percent: float, stats: str = ""
    ) -> None:
        """Update with live download telemetry."""
        self._is_active = True
        self.title_label.set_full_text(f"Đang tải: {code}")
        self.spinner.start()
        self.cancel_button.show()

        int_val = min(100, max(0, int(round(percent))))
        self.progress_bar.setValue(int_val)
        self.progress_label.setText(f"Đang tải: {int_val}%")
        self.stats_label.setText(stats)

    def set_idle(self, message: str = "Hàng đợi đang dừng") -> None:
        self._is_active = False
        self.title_label.set_full_text(message)
        self.spinner.stop()
        self.cancel_button.hide()
        self.progress_bar.setValue(0)
        self.progress_label.setText("Sẵn sàng")
        self.stats_label.setText("")

    def set_paused(self, message: str = "Hàng đợi đang tạm dừng.") -> None:
        self._is_active = False
        self.title_label.set_full_text(message)
        self.spinner.stop()
        self.cancel_button.hide()
        self.progress_label.setText("Tạm dừng")
        self.stats_label.setText("")

    def set_completed(self, message: str = "Hàng đợi đã hoàn tất.") -> None:
        self._is_active = False
        self.title_label.set_full_text(message)
        self.spinner.stop()
        self.cancel_button.hide()
        self.progress_bar.setValue(100)
        self.progress_label.setText("Hoàn tất 100%")
        self.stats_label.setText("Đã tải xong")

    def set_error(self, message: str) -> None:
        self._is_active = False
        self.title_label.set_full_text(message)
        self.spinner.stop()
        self.cancel_button.hide()
        self.progress_label.setText("Lỗi")
        self.stats_label.setText("Kiểm tra nhật ký")

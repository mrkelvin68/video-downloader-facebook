from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionButton, QPushButton, QWidget


class LoadingSpinner(QWidget):
    _timer: QTimer | None = None
    _app: QApplication | None = None
    _instances: set["LoadingSpinner"] = set()
    _angle = 0

    def __init__(self, size: int = 16, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = int(size)
        self.setFixedSize(self._size, self._size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    @classmethod
    def _tick(cls) -> None:
        cls._angle = (cls._angle + 6) % 360
        for instance in tuple(cls._instances):
            try:
                if instance.isVisible():
                    instance.update()
            except RuntimeError:
                cls._instances.discard(instance)
        if not cls._instances and cls._timer is not None:
            cls._timer.stop()

    @classmethod
    def _shutdown(cls) -> None:
        cls._instances.clear()
        if cls._timer is not None:
            cls._timer.stop()
            cls._timer.deleteLater()
        cls._timer = None
        cls._app = None

    def set_running(self, running: bool) -> None:
        spinner_cls = type(self)
        if running:
            spinner_cls._instances.add(self)
            if spinner_cls._timer is None:
                app = QApplication.instance()
                if app is None:
                    return
                if spinner_cls._app is not app:
                    spinner_cls._shutdown()
                    spinner_cls._app = app
                    app.aboutToQuit.connect(spinner_cls._shutdown)
                spinner_cls._timer = QTimer(app)
                spinner_cls._timer.setInterval(25)
                spinner_cls._timer.timeout.connect(spinner_cls._tick)
            if not spinner_cls._timer.isActive():
                spinner_cls._timer.start()
            self.show()
            self.raise_()
            self.update()
            return
        spinner_cls._instances.discard(self)
        self.hide()
        if not spinner_cls._instances and spinner_cls._timer is not None:
            spinner_cls._timer.stop()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = self._size / 2
        painter.translate(center, center)
        painter.rotate(self._angle)
        parent = self.parentWidget()
        color = parent.palette().color(parent.foregroundRole()) if parent else QColor("white")
        pen = QPen(QColor(color), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        radius = max(4.0, center - 4.0)
        painter.drawArc(int(-radius), int(-radius), int(radius * 2), int(radius * 2), 35 * 16, 285 * 16)
        painter.end()


class LoadingButton(QPushButton):
    """A QPushButton with an in-place spinner that does not affect its size hint."""

    def __init__(self, *args, loading_text: str | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._loading = False
        self._enabled_before_loading = True
        self._loading_text: str | None = loading_text
        self._original_text: str | None = None
        self._spinner = LoadingSpinner(16, self)

    @property
    def is_loading(self) -> bool:
        return self._loading

    def set_loading_text(self, text: str | None) -> None:
        self._loading_text = text

    def set_loading(self, loading: bool, loading_text: str | None = None) -> None:
        loading = bool(loading)
        if loading == self._loading:
            return
        effective_loading_text = loading_text or self._loading_text
        if loading:
            self._enabled_before_loading = self.isEnabled()
            self._loading = True
            self.setEnabled(False)
            if effective_loading_text:
                self._original_text = self.text()
                self.setText(effective_loading_text)
                self._spinner.set_running(False)
            else:
                self._position_spinner()
                self._spinner.set_running(True)
        else:
            self._loading = False
            self._spinner.set_running(False)
            if self._original_text is not None:
                self.setText(self._original_text)
                self._original_text = None
            self.setEnabled(self._enabled_before_loading)

    def setText(self, text: str) -> None:
        super().setText(text)
        if self._loading:
            self._position_spinner()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_spinner()

    def _position_spinner(self) -> None:
        if self.width() <= 0:
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        contents = self.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, self)
        text_width = self.fontMetrics().horizontalAdvance(self.text())
        x = max(contents.left() + 4, contents.center().x() - (text_width // 2) - 22)
        self._spinner.move(x, contents.center().y() - 8)


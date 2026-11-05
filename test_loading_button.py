import unittest

from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.ui.widgets.loading_button import LoadingButton


class LoadingButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_loading_preserves_text_icon_size_and_state(self):
        button = LoadingButton("Xử lý")
        icon = QIcon(QPixmap(8, 8))
        button.setIcon(icon)
        button.show()
        self.app.processEvents()
        original_size = button.sizeHint()
        original_icon = button.icon().cacheKey()

        button.set_loading(True)
        self.app.processEvents()
        self.assertTrue(button.is_loading)
        self.assertFalse(button.isEnabled())
        self.assertTrue(button._spinner.isVisible())
        self.assertEqual(button.text(), "Xử lý")
        self.assertEqual(button.icon().cacheKey(), original_icon)
        self.assertEqual(button.sizeHint(), original_size)
        angle = button._spinner._angle
        QTest.qWait(80)
        self.assertNotEqual(button._spinner._angle, angle)

        button.set_loading(False)
        self.assertFalse(button.is_loading)
        self.assertTrue(button.isEnabled())
        self.assertFalse(button._spinner.isVisible())
        self.assertEqual(button.sizeHint(), original_size)
        button.deleteLater()

    def test_loading_restores_previously_disabled_state_and_is_independent(self):
        first = LoadingButton("One")
        second = LoadingButton("Two")
        first.setEnabled(False)
        first.set_loading(True)
        second.set_loading(True)
        self.assertFalse(first.isEnabled())
        self.assertFalse(second.isEnabled())
        first.set_loading(False)
        self.assertFalse(first.isEnabled())
        self.assertTrue(second.is_loading)
        second.set_loading(False)
        self.assertFalse(first.is_loading)
        self.assertFalse(second.is_loading)
        first.deleteLater()
        second.deleteLater()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

APP_DIR = Path.home() / ".video-downloader-facebook"
DB_PATH = APP_DIR / "projects.sqlite3"
_INVALID_WINDOWS_NAME_CHARS = '<>:"/\\|?*'
_RESERVED_WINDOWS_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def validate_windows_name(value: str, field: str) -> str:
    raw_value = value
    value = value.strip()
    if not value:
        raise ValueError(f"{field} không được để trống")
    if len(value) > 120:
        raise ValueError(f"{field} tối đa 120 ký tự")
    if raw_value != value or value[-1] in ". " or any(char in _INVALID_WINDOWS_NAME_CHARS or ord(char) < 32 for char in value):
        raise ValueError(f"{field} chứa ký tự không hợp lệ trên Windows")
    if value.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES:
        raise ValueError(f"{field} dùng tên dành riêng của Windows")
    return value


def validate_output_dir(value: str, *, may_exist: bool) -> Path:
    path = Path(value).expanduser()
    if not value.strip() or not path.is_absolute():
        raise ValueError("Thư mục xuất phải là đường dẫn tuyệt đối")
    if path.exists() and (not path.is_dir() or not may_exist):
        raise ValueError("Thư mục xuất đã tồn tại hoặc không phải thư mục")
    parent = path if path.exists() else path.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK):
        raise ValueError("Thư mục cha không tồn tại hoặc không có quyền ghi")
    return path.resolve()


class ProjectStore:
    def __init__(self, path: str | Path = DB_PATH) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("""CREATE TABLE IF NOT EXISTS projects (
            uuid TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            prefix TEXT NOT NULL,
            output_dir TEXT NOT NULL UNIQUE,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        self.connection.commit()

    def list_projects(self) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE"))

    def create(self, name: str, prefix: str, output_dir: str, notes: str = "") -> str:
        name = validate_windows_name(name, "Tên dự án")
        prefix = validate_windows_name(prefix, "Tiền tố")
        folder = validate_output_dir(output_dir, may_exist=False)
        project_uuid, now = str(uuid.uuid4()), _now()
        try:
            folder.mkdir()
            self.connection.execute(
                "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_uuid, name, prefix, str(folder), notes.strip(), now, now),
            )
            self.connection.commit()
        except sqlite3.IntegrityError as exc:
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
            raise ValueError("Tên dự án hoặc thư mục xuất đã tồn tại") from exc
        except OSError as exc:
            raise ValueError("Không thể tạo thư mục xuất mà không ghi đè") from exc
        return project_uuid

    def update(self, project_uuid: str, name: str, prefix: str, output_dir: str, notes: str = "") -> None:
        current = self.connection.execute("SELECT output_dir FROM projects WHERE uuid=?", (project_uuid,)).fetchone()
        if current is None:
            raise ValueError("Dự án không tồn tại")
        name = validate_windows_name(name, "Tên dự án")
        prefix = validate_windows_name(prefix, "Tiền tố")
        folder = validate_output_dir(output_dir, may_exist=True)
        old_folder = Path(current["output_dir"])
        made_folder = False
        if folder != old_folder:
            folder = validate_output_dir(output_dir, may_exist=False)
            try:
                folder.mkdir()
                made_folder = True
            except OSError as exc:
                raise ValueError("Không thể tạo thư mục xuất mà không ghi đè") from exc
        try:
            cursor = self.connection.execute(
                "UPDATE projects SET name=?, prefix=?, output_dir=?, notes=?, updated_at=? WHERE uuid=?",
                (name, prefix, str(folder), notes.strip(), _now(), project_uuid),
            )
            self.connection.commit()
            if cursor.rowcount != 1:
                raise ValueError("Dự án không tồn tại")
        except sqlite3.IntegrityError as exc:
            if made_folder and not any(folder.iterdir()):
                folder.rmdir()
            raise ValueError("Tên dự án hoặc thư mục xuất đã tồn tại") from exc

    def close(self) -> None:
        self.connection.close()


class ProjectDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, row: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Dự án")
        self.name = QLineEdit(row["name"] if row else "")
        self.prefix = QLineEdit(row["prefix"] if row else "")
        self.output_dir = QLineEdit(row["output_dir"] if row else "")
        self.notes = QTextEdit(row["notes"] if row else "")
        self.name.setPlaceholderText("Tên dự án")
        self.prefix.setPlaceholderText("Tiền tố tệp, ví dụ: fb")
        self.output_dir.setPlaceholderText(r"C:\Videos\Du-an-moi")
        self.notes.setPlaceholderText("Ghi chú (không bắt buộc)")
        form = QFormLayout()
        form.addRow("Tên *", self.name)
        form.addRow("Tiền tố *", self.prefix)
        form.addRow("Thư mục xuất mới *", self.output_dir)
        form.addRow("Ghi chú", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self); layout.addLayout(form); layout.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self, store: ProjectStore) -> None:
        super().__init__()
        self.store = store
        self.setWindowTitle("Trình tải video Facebook")
        self.resize(880, 540)
        self.projects = QListWidget()
        self.projects.currentItemChanged.connect(self._selection_changed)
        add = QPushButton("+ Dự án mới"); add.clicked.connect(self.add_project)
        edit = QPushButton("Sửa dự án"); edit.clicked.connect(self.edit_project)
        sidebar = QVBoxLayout(); sidebar.addWidget(QLabel("DỰ ÁN")); sidebar.addWidget(self.projects); sidebar.addWidget(add); sidebar.addWidget(edit)
        side = QWidget(); side.setObjectName("sidebar"); side.setLayout(sidebar)
        self.details = QLabel("Chọn dự án để xem chi tiết")
        self.details.setWordWrap(True)
        body = QHBoxLayout(); body.addWidget(side, 1); body.addWidget(self.details, 2)
        container = QWidget(); container.setLayout(body); self.setCentralWidget(container)
        self.refresh()

    def refresh(self) -> None:
        self.projects.clear()
        for row in self.store.list_projects():
            item = QListWidgetItem(row["name"])
            item.setData(Qt.ItemDataRole.UserRole, dict(row)); self.projects.addItem(item)

    def _selection_changed(self, current: QListWidgetItem | None, _: QListWidgetItem | None) -> None:
        if not current:
            self.details.setText("Chọn dự án để xem chi tiết"); return
        row = current.data(Qt.ItemDataRole.UserRole)
        self.details.setText(f"<h2>{row['name']}</h2><p><b>Tiền tố:</b> {row['prefix']}</p><p><b>Xuất:</b><br>{row['output_dir']}</p><p>{row['notes']}</p>")

    def _save_dialog(self, row: dict | None = None) -> None:
        dialog = ProjectDialog(self, row)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            fields = (dialog.name.text(), dialog.prefix.text(), dialog.output_dir.text(), dialog.notes.toPlainText())
            if row:
                self.store.update(row["uuid"], *fields)
            else:
                self.store.create(*fields)
        except ValueError as exc:
            QMessageBox.warning(self, "Dữ liệu không hợp lệ", str(exc)); return
        self.refresh()

    def add_project(self) -> None:
        self._save_dialog()

    def edit_project(self) -> None:
        item = self.projects.currentItem()
        if item:
            self._save_dialog(item.data(Qt.ItemDataRole.UserRole))


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet("QWidget { background: #17191f; color: #eef0f5; font-size: 14px; } #sidebar { background: #20242d; } QLineEdit, QTextEdit, QListWidget { background: #2a2f3a; border: 1px solid #505866; border-radius: 4px; padding: 7px; } QPushButton { background: #4d70d6; border: 0; border-radius: 4px; padding: 8px 12px; } QPushButton:hover { background: #6284e5; }")
    store = ProjectStore()
    window = MainWindow(store); window.show()
    result = app.exec(); store.close(); return result


if __name__ == "__main__":
    raise SystemExit(main())

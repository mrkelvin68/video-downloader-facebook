from __future__ import annotations

import os
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

APP_DIR = Path.home() / ".video-downloader-facebook"
DB_PATH = APP_DIR / "projects.sqlite3"
_INVALID_WINDOWS_NAME_CHARS = '<>:"/\\|?*'
_RESERVED_WINDOWS_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def validate_windows_name(value: str, field: str) -> str:
    raw_value = value; value = value.strip()
    if not value: raise ValueError(f"{field} không được để trống")
    if len(value) > 120: raise ValueError(f"{field} tối đa 120 ký tự")
    if raw_value != value or value[-1] in ". " or any(c in _INVALID_WINDOWS_NAME_CHARS or ord(c) < 32 for c in value):
        raise ValueError(f"{field} chứa ký tự không hợp lệ trên Windows")
    if value.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES: raise ValueError(f"{field} dùng tên dành riêng của Windows")
    return value


def validate_output_dir(value: str, *, may_exist: bool) -> Path:
    path = Path(value).expanduser()
    if not value.strip() or not path.is_absolute(): raise ValueError("Thư mục xuất phải là đường dẫn tuyệt đối")
    if path.exists() and (not path.is_dir() or not may_exist): raise ValueError("Thư mục xuất đã tồn tại hoặc không phải thư mục")
    parent = path if path.exists() else path.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK): raise ValueError("Thư mục cha không tồn tại hoặc không có quyền ghi")
    return path.resolve()


class ProjectStore:
    def __init__(self, path: str | Path = DB_PATH) -> None:
        self.path = str(path)
        if self.path != ":memory:": Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path); self.connection.row_factory = sqlite3.Row
        self.connection.execute("""CREATE TABLE IF NOT EXISTS projects (
            uuid TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, prefix TEXT NOT NULL,
            output_dir TEXT NOT NULL UNIQUE, notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""); self.connection.commit()

    def list_projects(self) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE"))

    def create(self, name: str, prefix: str, output_dir: str, notes: str = "") -> str:
        name = validate_windows_name(name, "Tên dự án"); prefix = validate_windows_name(prefix, "Tiền tố")
        folder = validate_output_dir(output_dir, may_exist=False); project_uuid, now = str(uuid.uuid4()), _now()
        try:
            folder.mkdir(); self.connection.execute("INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?)", (project_uuid, name, prefix, str(folder), notes.strip(), now, now)); self.connection.commit()
        except sqlite3.IntegrityError as exc:
            if folder.exists() and not any(folder.iterdir()): folder.rmdir()
            raise ValueError("Tên dự án hoặc thư mục xuất đã tồn tại") from exc
        except OSError as exc: raise ValueError("Không thể tạo thư mục xuất mà không ghi đè") from exc
        return project_uuid

    def update(self, project_uuid: str, name: str, prefix: str, output_dir: str, notes: str = "") -> None:
        current = self.connection.execute("SELECT output_dir FROM projects WHERE uuid=?", (project_uuid,)).fetchone()
        if current is None: raise ValueError("Dự án không tồn tại")
        name = validate_windows_name(name, "Tên dự án"); prefix = validate_windows_name(prefix, "Tiền tố"); folder = validate_output_dir(output_dir, may_exist=True); old_folder = Path(current["output_dir"]); made_folder = False
        if folder != old_folder:
            folder = validate_output_dir(output_dir, may_exist=False)
            try: folder.mkdir(); made_folder = True
            except OSError as exc: raise ValueError("Không thể tạo thư mục xuất mà không ghi đè") from exc
        try:
            cursor = self.connection.execute("UPDATE projects SET name=?, prefix=?, output_dir=?, notes=?, updated_at=? WHERE uuid=?", (name, prefix, str(folder), notes.strip(), _now(), project_uuid)); self.connection.commit()
            if cursor.rowcount != 1: raise ValueError("Dự án không tồn tại")
        except sqlite3.IntegrityError as exc:
            if made_folder and not any(folder.iterdir()): folder.rmdir()
            raise ValueError("Tên dự án hoặc thư mục xuất đã tồn tại") from exc

    def close(self) -> None: self.connection.close()


class ProjectDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, row: dict | None = None) -> None:
        super().__init__(parent); self.row = row; self.setWindowTitle("Chỉnh sửa dự án" if row else "Tạo dự án mới"); self.setModal(True); self.setMinimumWidth(520)
        self.name = QLineEdit(row["name"] if row else ""); self.prefix = QLineEdit(row["prefix"] if row else ""); self.output_dir = QLineEdit(row["output_dir"] if row else ""); self.notes = QTextEdit(row["notes"] if row else "")
        self.name.setPlaceholderText("Ví dụ: Series review phim"); self.prefix.setPlaceholderText("Ví dụ: ep1"); self.output_dir.setPlaceholderText(r"D:\Videos\Du-an-moi"); self.notes.setPlaceholderText("Ghi chú không bắt buộc")
        self.error = QLabel(); self.error.setObjectName("error"); self.error.setWordWrap(True)
        browse = QPushButton("Duyệt..."); browse.clicked.connect(self.pick_folder)
        folder = QHBoxLayout(); folder.addWidget(self.output_dir); folder.addWidget(browse)
        form = QFormLayout(); form.setVerticalSpacing(12); form.addRow("Tên dự án *", self.name); form.addRow("Prefix video *", self.prefix); form.addRow("Thư mục lưu video *", folder); form.addRow("Ghi chú", self.notes)
        cancel = QPushButton("Hủy bỏ"); save = QPushButton("Tạo dự án" if not row else "Lưu thay đổi"); save.setObjectName("primary"); cancel.clicked.connect(self.reject); save.clicked.connect(self.validate_and_accept); self.name.returnPressed.connect(self.validate_and_accept)
        buttons = QHBoxLayout(); buttons.addStretch(); buttons.addWidget(cancel); buttons.addWidget(save)
        layout = QVBoxLayout(self); layout.setContentsMargins(28, 24, 28, 24); layout.addWidget(QLabel("Thông tin dự án")); layout.addLayout(form); layout.addWidget(self.error); layout.addLayout(buttons); self.name.setFocus()

    def pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục cha")
        if folder: self.output_dir.setText(str(Path(folder) / self.prefix.text().strip()))

    def validate_and_accept(self) -> None:
        try:
            validate_windows_name(self.name.text(), "Tên dự án"); validate_windows_name(self.prefix.text(), "Tiền tố"); validate_output_dir(self.output_dir.text(), may_exist=bool(self.row and Path(self.output_dir.text()).resolve() == Path(self.row["output_dir"]).resolve()) if self.output_dir.text() else False)
        except ValueError as exc: self.error.setText(str(exc)); return
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, store: ProjectStore) -> None:
        super().__init__(); self.store = store; self.setWindowTitle("Video Downloader"); self.resize(1180, 720); self.setMinimumSize(900, 600)
        self.projects = QListWidget(); self.projects.currentItemChanged.connect(self._selection_changed)
        sidebar = QFrame(); sidebar.setObjectName("sidebar"); sl = QVBoxLayout(sidebar); sl.setContentsMargins(22, 24, 16, 20); brand = QLabel("Video Downloader"); brand.setObjectName("brand"); sl.addWidget(brand); sl.addSpacing(26); sl.addWidget(QLabel("ĐIỀU HƯỚNG CHÍNH"))
        for text, active in (("⌂  Dashboard", False), ("▣  Quản lý dự án", True), ("⇩  Hàng đợi tải video  · Sắp có", False), ("≡  Live Logs  · Sắp có", False), ("⚙  Cài đặt  · Sắp có", False)):
            button = QPushButton(text); button.setProperty("active", active); button.setEnabled(active); sl.addWidget(button)
        sl.addStretch(); sl.addWidget(QLabel("●  SQLite đang hoạt động")); sl.addWidget(QLabel("Phase 1 · Quản lý dự án"))
        self.content = QWidget(); self.content.setObjectName("content"); self.body = QVBoxLayout(self.content); self.body.setContentsMargins(34, 28, 34, 28); self.render_list()
        root = QWidget(); layout = QHBoxLayout(root); layout.setContentsMargins(0, 0, 0, 0); layout.addWidget(sidebar); layout.addWidget(self.content, 1); self.setCentralWidget(root); self.refresh()

    def render_list(self) -> None:
        self.clear_body(); head = QHBoxLayout(); title = QLabel("Quản lý dự án"); title.setObjectName("title"); head.addWidget(title); head.addStretch(); add = QPushButton("＋ Tạo dự án mới"); add.setObjectName("primary"); add.clicked.connect(self.add_project); head.addWidget(add); self.body.addLayout(head); self.body.addWidget(QLabel("Tạo và quản lý thư mục video theo từng dự án.")); self.body.addSpacing(18); self.body.addWidget(self.projects, 1)

    def clear_body(self) -> None:
        while self.body.count(): item = self.body.takeAt(0); widget = item.widget(); widget and widget.deleteLater()

    def refresh(self) -> None:
        self.projects.clear()
        for row in self.store.list_projects():
            item = QListWidgetItem(); item.setData(Qt.ItemDataRole.UserRole, dict(row)); self.projects.addItem(item); self.projects.setItemWidget(item, self.card(dict(row))); item.setSizeHint(self.item_size())

    def item_size(self): return self.projects.sizeHint().expandedTo(self.projects.sizeHint())

    def card(self, row: dict) -> QWidget:
        card = QFrame(); card.setObjectName("card"); grid = QGridLayout(card); grid.setContentsMargins(20, 16, 20, 16); name = QLabel(row["name"]); name.setObjectName("cardTitle"); prefix = QLabel(row["prefix"]); prefix.setObjectName("tag"); path = QLabel(f"📁  {row['output_dir']}"); path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); created = QLabel(f"Tạo lúc {row['created_at'][:10]}"); status = QLabel("●  Chờ tải"); status.setObjectName("success"); open_btn = QPushButton("Mở dự án  →"); open_btn.clicked.connect(lambda: self.show_detail(row)); edit = QPushButton("Sửa"); edit.clicked.connect(lambda: self._save_dialog(row)); grid.addWidget(name, 0, 0); grid.addWidget(prefix, 0, 1); grid.addWidget(status, 0, 2); grid.addWidget(path, 1, 0, 1, 3); grid.addWidget(created, 2, 0); grid.addWidget(edit, 2, 1); grid.addWidget(open_btn, 2, 2); return card

    def _selection_changed(self, current, _):
        if current: self.show_detail(current.data(Qt.ItemDataRole.UserRole))

    def show_detail(self, row: dict) -> None:
        self.clear_body(); back = QPushButton("← Quay lại danh sách"); back.clicked.connect(self.render_list); self.body.addWidget(back); title = QLabel(row["name"]); title.setObjectName("title"); self.body.addWidget(title); self.body.addWidget(QLabel(f"Prefix: {row['prefix']}   ·   {row['output_dir']}")); steps = QHBoxLayout()
        for i, text in enumerate(("Tạo dự án", "Import CSV", "Tải video", "Hoàn tất"), 1):
            label = QLabel(f"{i}\n{text}"); label.setAlignment(Qt.AlignmentFlag.AlignCenter); label.setObjectName("stepActive" if i == 1 else "step"); steps.addWidget(label)
        self.body.addLayout(steps); self.body.addStretch(); note = QLabel("Giai đoạn 1 hoàn tất. Các bước tiếp theo sẽ được triển khai ở phiên bản sau."); note.setObjectName("muted"); self.body.addWidget(note)

    def _save_dialog(self, row=None) -> None:
        dialog = ProjectDialog(self, row)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        try:
            fields = (dialog.name.text(), dialog.prefix.text(), dialog.output_dir.text(), dialog.notes.toPlainText()); self.store.update(row["uuid"], *fields) if row else self.store.create(*fields)
        except ValueError as exc: QMessageBox.warning(self, "Không thể lưu", str(exc)); return
        self.render_list(); self.refresh()

    def add_project(self): self._save_dialog()
    def edit_project(self):
        item = self.projects.currentItem()
        if item: self._save_dialog(item.data(Qt.ItemDataRole.UserRole))


def main() -> int:
    app = QApplication(sys.argv); app.setStyleSheet(STYLES); store = ProjectStore(); window = MainWindow(store); window.show(); result = app.exec(); store.close(); return result


STYLES = """
QWidget { background:#0d1526; color:#e5e7eb; font-size:14px; }
#sidebar { background:#09111f; min-width:240px; max-width:240px; border-right:1px solid #1f2d46; }
#content { background:#0d1526; } #brand { color:#60a5fa; font-size:20px; font-weight:700; }
QLabel { color:#aebbd0; } #title { color:#f8fafc; font-size:28px; font-weight:700; } #sidebar QLabel { color:#71829d; }
QPushButton { background:transparent; color:#aebbd0; border:1px solid transparent; border-radius:8px; padding:10px 12px; text-align:left; }
QPushButton:hover { background:#162238; color:#fff; } QPushButton[active="true"] { background:#1d4ed8; color:#fff; } QPushButton#primary { background:#2563eb; color:#fff; font-weight:600; text-align:center; padding:11px 16px; }
QListWidget { background:transparent; border:0; outline:0; } QListWidget::item { margin:5px 0; }
#card { background:#15233b; border:1px solid #243653; border-radius:11px; } #cardTitle { color:#f8fafc; font-size:17px; font-weight:600; } #tag { background:#1e3a5f; color:#60a5fa; border-radius:5px; padding:4px 8px; } #success { color:#34d399; } #muted { color:#71829d; }
QLineEdit, QTextEdit { background:#101c30; border:1px solid #30425f; border-radius:7px; padding:9px; color:#f8fafc; } QLineEdit:focus, QTextEdit:focus { border:1px solid #3b82f6; }
QDialog { background:#101b2e; } QDialog QLabel { color:#cbd5e1; } #error { color:#f87171; } #stepActive, #step { background:#162b4d; border-radius:8px; padding:15px; color:#60a5fa; font-weight:600; } #step { color:#71829d; }
"""

if __name__ == "__main__": raise SystemExit(main())

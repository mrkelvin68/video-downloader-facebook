from __future__ import annotations

import csv
import os
import sqlite3
import sys
import uuid
from io import StringIO
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QMimeData
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSpacerItem, QStackedWidget, QStyle,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
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
    if raw_value != value or value[-1] in ". " or any(c in _INVALID_WINDOWS_NAME_CHARS or ord(c) < 32 for c in value):
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


def normalize_video_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), host, path, parts.query, ""))


def validate_facebook_url(url: str) -> bool:
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower().removeprefix("www.")
        return parts.scheme in {"http", "https"} and host in {"facebook.com", "fb.watch", "m.facebook.com"} and bool(parts.path.strip("/"))
    except ValueError:
        return False


def parse_video_csv(text: str) -> tuple[list[dict[str, str]], list[str]]:
    payload = text.lstrip("\ufeff")
    try:
        dialect = csv.Sniffer().sniff(payload[:4096], delimiters=",;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(StringIO(payload, newline=""), dialect=dialect, skipinitialspace=True)
    if not reader.fieldnames:
        return [], ["CSV không có tiêu đề"]
    headers = {h.strip().lower(): h for h in reader.fieldnames if h}
    sequence_key = headers.get("stt") or headers.get("sequence")
    title_key = headers.get("ten_video") or headers.get("title")
    url_key = headers.get("url")
    missing = [label for key, label in ((sequence_key, "stt"), (title_key, "ten_video"), (url_key, "url")) if not key]
    if missing:
        return [], [f"CSV thiếu cột: {', '.join(missing)}"]
    rows, errors, sequences, urls = [], [], set(), set()
    for number, row in enumerate(reader, 2):
        raw_sequence = (row.get(sequence_key) or "").strip()
        title = (row.get(title_key) or "").strip()
        url = (row.get(url_key) or "").strip()
        if not raw_sequence.isdigit() or int(raw_sequence) < 1:
            errors.append(f"Dòng {number}: stt phải là số nguyên dương")
        elif int(raw_sequence) in sequences:
            errors.append(f"Dòng {number}: stt trùng")
        elif not title:
            errors.append(f"Dòng {number}: tên video không được để trống")
        elif not validate_facebook_url(url):
            errors.append(f"Dòng {number}: URL Facebook không hợp lệ")
        elif normalize_video_url(url) in urls:
            errors.append(f"Dòng {number}: URL trùng")
        else:
            sequence = int(raw_sequence); sequences.add(sequence); urls.add(normalize_video_url(url))
            rows.append({"sequence": sequence, "url": url, "title": title})
    return rows, errors


def sample_video_csv() -> str:
    return "stt,ten_video,url\\n1,Video mẫu,https://www.facebook.com/watch/?v=example\\n"


class ProjectStore:
    def __init__(self, path: str | Path = DB_PATH) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS projects (
            uuid TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, prefix TEXT NOT NULL,
            output_dir TEXT NOT NULL UNIQUE, notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(videos)")}
        if columns and "project_id" not in columns:
            with self.connection:
                self.connection.execute("ALTER TABLE videos RENAME TO videos_legacy")
                self._create_videos_table()
                self.connection.execute("""INSERT INTO videos
                    (id,project_id,sequence_number,video_code,title,source_url,normalized_url,created_at,updated_at)
                    SELECT lower(hex(randomblob(16))),project_uuid,sequence,code,title,url,normalized_url,created_at,created_at
                    FROM videos_legacy""")
                self.connection.execute("DROP TABLE videos_legacy")
        elif not columns:
            self._create_videos_table()
        self.connection.commit()

    def _create_videos_table(self) -> None:
        self.connection.execute("""CREATE TABLE videos (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, sequence_number INTEGER NOT NULL,
            video_code TEXT NOT NULL, title TEXT NOT NULL, source_url TEXT NOT NULL,
            normalized_url TEXT NOT NULL, download_status TEXT NOT NULL DEFAULT 'pending',
            local_video_path TEXT, thumbnail_path TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(uuid) ON DELETE CASCADE,
            UNIQUE(project_id, sequence_number), UNIQUE(project_id, normalized_url), UNIQUE(project_id, video_code))""")

    def list_projects(self) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE"))

    def import_csv(self, project_uuid: str, text: str) -> int:
        rows, errors = parse_video_csv(text)
        if errors:
            raise ValueError("; ".join(errors))
        project = self.connection.execute("SELECT prefix FROM projects WHERE uuid=?", (project_uuid,)).fetchone()
        if project is None:
            raise ValueError("Dự án không tồn tại")
        try:
            with self.connection:
                self.connection.execute("DELETE FROM videos WHERE project_id=?", (project_uuid,))
                for row in rows:
                    now = _now()
                    code = f"{project['prefix']}-{row['sequence']}"
                    self.connection.execute("""INSERT INTO videos
                        (id,project_id,sequence_number,video_code,title,source_url,normalized_url,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?)""", (str(uuid.uuid4()), project_uuid, row['sequence'], code, row['title'], row['url'], normalize_video_url(row['url']), now, now))
        except sqlite3.IntegrityError as exc:
            raise ValueError("Video trùng sequence, URL hoặc mã") from exc
        return len(rows)

    def list_videos(self, project_uuid: str) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM videos WHERE project_id=? ORDER BY sequence_number", (project_uuid,)))

    def export_csv(self, project_uuid: str) -> str:
        output = StringIO(newline="")
        output.write("\ufeff")
        writer = csv.writer(output)
        writer.writerow(("stt", "ten_video", "url"))
        writer.writerows(self.connection.execute("SELECT sequence_number,title,source_url FROM videos WHERE project_id=? ORDER BY sequence_number", (project_uuid,)))
        return output.getvalue()

    def create(self, name: str, prefix: str, output_dir: str, notes: str = "") -> str:
        name = validate_windows_name(name, "Tên dự án")
        prefix = validate_windows_name(prefix, "Tiền tố")
        folder = validate_output_dir(output_dir, may_exist=False)
        project_uuid, now = str(uuid.uuid4()), _now()
        try:
            folder.mkdir()
            self.connection.execute("INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?)", (project_uuid, name, prefix, str(folder), notes.strip(), now, now))
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
            cursor = self.connection.execute("UPDATE projects SET name=?, prefix=?, output_dir=?, notes=?, updated_at=? WHERE uuid=?", (name, prefix, str(folder), notes.strip(), _now(), project_uuid))
            self.connection.commit()
            if cursor.rowcount != 1:
                raise ValueError("Dự án không tồn tại")
        except sqlite3.IntegrityError as exc:
            if made_folder and not any(folder.iterdir()):
                folder.rmdir()
            raise ValueError("Tên dự án hoặc thư mục xuất đã tồn tại") from exc

    def close(self) -> None:
        self.connection.close()


class VideoStore(ProjectStore):
    pass


def button_icon(widget: QWidget, icon: QStyle.StandardPixmap):
    return widget.style().standardIcon(icon)


class ProjectDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, row: dict | None = None) -> None:
        super().__init__(parent)
        self.row = row
        self.setWindowTitle("Chỉnh sửa dự án" if row else "Tạo dự án mới")
        self.setModal(True)
        self.setMinimumWidth(610)
        self.setMaximumWidth(680)

        self.name = QLineEdit(row["name"] if row else "")
        self.prefix = QLineEdit(row["prefix"] if row else "")
        self.output_dir = QLineEdit(row["output_dir"] if row else "")
        self.notes = QTextEdit(row["notes"] if row else "")
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
        title = QLabel("Chỉnh sửa dự án" if row else "Tạo dự án mới")
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
        example = QLabel("Ví dụ tệp video: ep1-6-001.mp4")
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
        save = QPushButton("Tạo dự án" if not row else "Lưu thay đổi")
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
            same_folder = bool(self.row and self.output_dir.text() and Path(self.output_dir.text()).resolve() == Path(self.row["output_dir"]).resolve())
            validate_windows_name(self.name.text(), "Tên dự án")
            validate_windows_name(self.prefix.text(), "Tiền tố")
            validate_output_dir(self.output_dir.text(), may_exist=same_folder)
        except ValueError as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, store: ProjectStore) -> None:
        super().__init__()
        self.store = store
        self.setWindowTitle("Video Downloader")
        self.resize(1366, 768)
        self.setMinimumSize(960, 640)

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self.build_sidebar())

        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(self.build_header())
        self.pages = QStackedWidget()
        self.pages.addWidget(self.build_projects_page())
        self.pages.addWidget(self.build_detail_page())
        workspace_layout.addWidget(self.pages, 1)
        root_layout.addWidget(workspace, 1)
        self.setCentralWidget(root)
        self.refresh()

    def build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(240)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 22, 16, 18)
        layout.setSpacing(5)
        brand_row = QHBoxLayout()
        logo = QLabel()
        logo.setObjectName("brandMark")
        logo.setPixmap(button_icon(self, QStyle.StandardPixmap.SP_MediaPlay).pixmap(QSize(22, 22)))
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
        status_layout.addWidget(QLabel("Phase 1 · Quản lý dự án"))
        layout.addWidget(status)
        return sidebar

    def build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(58)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(32, 0, 32, 0)
        crumb = QLabel("Workspace  /  Quản lý dự án")
        crumb.setObjectName("breadcrumb")
        layout.addWidget(crumb)
        layout.addStretch()
        phase = QLabel("PHASE 1")
        phase.setObjectName("phaseTag")
        layout.addWidget(phase)
        return header

    def build_projects_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(0)
        heading = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        title = QLabel("Quản lý dự án")
        title.setObjectName("title")
        title_col.addWidget(title)
        title_col.addWidget(QLabel("Mỗi dự án dùng một thư mục video riêng, không ghi đè dữ liệu cũ."))
        heading.addLayout(title_col)
        heading.addStretch()
        add = QPushButton("Tạo dự án mới")
        add.setObjectName("primary")
        add.setIcon(button_icon(self, QStyle.StandardPixmap.SP_FileDialogNewFolder))
        add.clicked.connect(self.add_project)
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
        return page

    def build_detail_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        back = QPushButton("Quay lại danh sách dự án")
        back.setObjectName("back")
        back.setIcon(button_icon(self, QStyle.StandardPixmap.SP_ArrowBack))
        back.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        layout.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)
        self.detail_title = QLabel()
        self.detail_title.setObjectName("title")
        self.detail_meta = QLabel()
        self.detail_meta.setObjectName("detailMeta")
        self.current_project: dict | None = None
        self.csv_rows: list[dict[str, str]] = []
        self.csv_errors: list[str] = []
        self.csv_raw_text: str = ""
        layout.addSpacing(12)
        layout.addWidget(self.detail_title)
        layout.addWidget(self.detail_meta)
        layout.addSpacing(30)
        steps = QHBoxLayout()
        steps.setSpacing(12)
        for index, label in enumerate(("Tạo dự án", "Import CSV", "Tải video", "Hoàn tất"), 1):
            step = QFrame()
            step.setObjectName("stepActive" if index == 2 else "step")
            step_layout = QVBoxLayout(step)
            step_layout.setContentsMargins(16, 15, 16, 15)
            number = QLabel(str(index))
            number.setObjectName("stepNumberActive" if index == 2 else "stepNumber")
            number.setAlignment(Qt.AlignmentFlag.AlignCenter)
            text = QLabel(label)
            text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            text.setObjectName("stepText")
            step_layout.addWidget(number)
            step_layout.addWidget(text)
            steps.addWidget(step, 1)
        layout.addLayout(steps)
        layout.addSpacing(24)
        import_box = QFrame(); import_box.setObjectName("detailNotice")
        import_layout = QVBoxLayout(import_box)
        section_title = QLabel("Bước 2: Import danh sách video từ CSV")
        section_title.setObjectName("cardTitle")
        import_layout.addWidget(section_title)
        import_layout.addWidget(QLabel("Tệp CSV cần có cột sequence, url (Facebook hợp lệ); cột title không bắt buộc."))
        self.drop_zone = QLabel("Kéo thả CSV vào đây hoặc chọn tệp")
        self.drop_zone.setObjectName("dropZone"); self.drop_zone.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_zone.setAcceptDrops(True); self.drop_zone.setMinimumHeight(90)
        self.drop_zone.dragEnterEvent = lambda event: event.acceptProposedAction() if event.mimeData().hasUrls() else event.ignore()
        self.drop_zone.dropEvent = lambda event: self.load_csv(Path(event.mimeData().urls()[0].toLocalFile()))
        import_layout.addWidget(self.drop_zone)
        actions = QHBoxLayout()
        choose = QPushButton("Chọn CSV"); choose.setObjectName("primary"); choose.clicked.connect(self.choose_csv)
        sample = QPushButton("Tải CSV mẫu"); sample.setObjectName("secondary"); sample.clicked.connect(self.save_sample_csv)
        export = QPushButton("Xuất CSV"); export.setObjectName("secondary"); export.clicked.connect(self.export_csv)
        self.confirm = QPushButton("Xác nhận import"); self.confirm.setObjectName("primary"); self.confirm.setEnabled(False); self.confirm.clicked.connect(self.confirm_import)
        actions.addWidget(choose); actions.addWidget(sample); actions.addWidget(export); actions.addStretch(); actions.addWidget(self.confirm)
        import_layout.addLayout(actions)
        self.csv_summary = QLabel("Chưa chọn tệp CSV"); self.csv_summary.setObjectName("detailMeta"); import_layout.addWidget(self.csv_summary)
        self.video_list = QLabel(); self.video_list.setObjectName("path"); self.video_list.setWordWrap(True); import_layout.addWidget(self.video_list)
        layout.addWidget(import_box); layout.addStretch()
        return page

    def choose_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Chọn CSV", "", "CSV (*.csv)")
        if path: self.load_csv(Path(path))

    def load_csv(self, path: Path) -> None:
        if self.csv_rows and QMessageBox.question(self, "Import lại CSV?", "Danh sách hiện tại sẽ bị thay thế.") != QMessageBox.StandardButton.Yes:
            return
        try: text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            QMessageBox.warning(self, "Không thể đọc CSV", str(exc)); return
        self.csv_raw_text = text
        self.csv_rows, self.csv_errors = parse_video_csv(text)
        self.csv_summary.setText(f"{path.name}: {len(self.csv_rows)} dòng hợp lệ · {len(self.csv_errors)} lỗi")
        self.video_list.setText("\n".join(f"• #{row['sequence']}: {row['title'] or row['url']}" for row in self.csv_rows[:30]) or "Không có dòng hợp lệ")
        self.confirm.setEnabled(bool(self.csv_rows) and not self.csv_errors)
        self.show_csv_preview(path.name)

    def show_csv_preview(self, filename: str) -> None:
        dialog = QDialog(self); dialog.setWindowTitle("Xem trước CSV"); dialog.resize(760, 460)
        layout = QVBoxLayout(dialog)
        status_lbl = QLabel(f"{filename}: {len(self.csv_rows)} dòng hợp lệ · {len(self.csv_errors)} lỗi")
        status_lbl.setObjectName("cardTitle")
        layout.addWidget(status_lbl)
        table = QTableWidget(len(self.csv_rows), 3)
        table.setHorizontalHeaderLabels(["STT", "URL", "Tiêu đề"])
        for index, row in enumerate(self.csv_rows):
            table.setItem(index, 0, QTableWidgetItem(str(row.get("sequence", index + 1))))
            table.setItem(index, 1, QTableWidgetItem(row.get("url", "")))
            table.setItem(index, 2, QTableWidgetItem(row.get("title", "")))
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table)
        if self.csv_errors:
            errors = QLabel("\n".join(self.csv_errors[:10]))
            errors.setObjectName("error")
            errors.setWordWrap(True)
            layout.addWidget(errors)
        close = QPushButton("Đóng"); close.setObjectName("primary"); close.clicked.connect(dialog.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def save_sample_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Lưu CSV mẫu", "sample.csv", "CSV (*.csv)")
        if path:
            try: Path(path).write_text(sample_video_csv(), encoding="utf-8")
            except OSError as exc: QMessageBox.warning(self, "Không thể lưu", str(exc))

    def export_csv(self) -> None:
        if not self.csv_rows:
            QMessageBox.information(self, "Xuất CSV", "Không có dữ liệu để xuất")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Xuất CSV", "videos.csv", "CSV (*.csv)")
        if not path:
            return
        if self.current_project:
            try:
                content = self.store.export_csv(self.current_project["uuid"])
                Path(path).write_text(content, encoding="utf-8")
                return
            except Exception:
                pass
        out = StringIO(newline="")
        out.write("\ufeff")
        writer = csv.writer(out)
        writer.writerow(("sequence", "url", "title"))
        for index, row in enumerate(self.csv_rows, 1):
            writer.writerow((row.get("sequence", index), row.get("url", ""), row.get("title", "")))
        try:
            Path(path).write_text(out.getvalue(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Không thể xuất", str(exc))

    def confirm_import(self) -> None:
        if self.csv_errors or not self.csv_rows or not self.current_project: return
        try:
            imported = self.store.import_csv(self.current_project["uuid"], self.csv_raw_text)
            self.load_project_videos(self.current_project["uuid"])
            QMessageBox.information(self, "Thành công", f"Đã lưu thành công {imported} video vào dự án.")
        except ValueError as exc:
            QMessageBox.warning(self, "Lỗi import", str(exc))

    def clear_projects(self) -> None:
        while self.project_layout.count():
            item = self.project_layout.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()

    def refresh(self) -> None:
        self.clear_projects()
        rows = [dict(row) for row in self.store.list_projects()]
        if not rows:
            self.project_layout.addStretch()
            empty = QFrame()
            empty.setObjectName("emptyState")
            empty_layout = QVBoxLayout(empty)
            empty_layout.setContentsMargins(40, 44, 40, 44)
            empty_layout.setSpacing(10)
            icon = QLabel()
            icon.setPixmap(button_icon(self, QStyle.StandardPixmap.SP_FileDialogNewFolder).pixmap(QSize(34, 34)))
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            heading = QLabel("Chưa có dự án nào")
            heading.setObjectName("emptyTitle")
            heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
            description = QLabel("Tạo dự án đầu tiên để đăng ký thư mục lưu video.")
            description.setAlignment(Qt.AlignmentFlag.AlignCenter)
            create = QPushButton("Tạo dự án mới")
            create.setObjectName("primary")
            create.setIcon(button_icon(self, QStyle.StandardPixmap.SP_FileDialogNewFolder))
            create.clicked.connect(self.add_project)
            empty_layout.addWidget(icon)
            empty_layout.addWidget(heading)
            empty_layout.addWidget(description)
            empty_layout.addSpacing(8)
            empty_layout.addWidget(create, alignment=Qt.AlignmentFlag.AlignCenter)
            self.project_layout.addWidget(empty)
            self.project_layout.addStretch(2)
            return
        for row in rows:
            self.project_layout.addWidget(self.project_card(row))
        self.project_layout.addStretch()

    def project_card(self, row: dict) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 19, 22, 18)
        layout.setSpacing(13)
        top = QHBoxLayout()
        title = QLabel(row["name"])
        title.setObjectName("cardTitle")
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tag = QLabel(row["prefix"])
        tag.setObjectName("tag")
        status = QLabel("Chờ tải")
        status.setObjectName("status")
        top.addWidget(title)
        top.addWidget(tag)
        top.addWidget(status)
        layout.addLayout(top)
        path = QLabel(row["output_dir"])
        path.setObjectName("path")
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path.setToolTip(row["output_dir"])
        layout.addWidget(path)
        bottom = QHBoxLayout()
        created = QLabel(f"Tạo ngày {row['created_at'][:10]}")
        created.setObjectName("muted")
        bottom.addWidget(created)
        bottom.addStretch()
        edit = QPushButton("Chỉnh sửa")
        edit.setObjectName("secondary")
        edit.setIcon(button_icon(self, QStyle.StandardPixmap.SP_FileDialogContentsView))
        edit.clicked.connect(lambda: self._save_dialog(row))
        open_project = QPushButton("Mở dự án")
        open_project.setObjectName("primary")
        open_project.setIcon(button_icon(self, QStyle.StandardPixmap.SP_ArrowForward))
        open_project.clicked.connect(lambda: self.show_detail(row))
        bottom.addWidget(edit)
        bottom.addWidget(open_project)
        layout.addLayout(bottom)
        return card

    def show_detail(self, row: dict) -> None:
        self.current_project = row
        self.detail_title.setText(row["name"])
        self.detail_meta.setText(f"Prefix video: {row['prefix']}    |    Thư mục: {row['output_dir']}")
        self.load_project_videos(row["uuid"])
        self.pages.setCurrentIndex(1)

    def load_project_videos(self, project_uuid: str) -> None:
        videos = [dict(v) for v in self.store.list_videos(project_uuid)]
        self.csv_rows = videos
        self.csv_errors = []
        self.csv_raw_text = ""
        self.confirm.setEnabled(False)
        if videos:
            self.csv_summary.setText(f"Hiện có {len(videos)} video đã lưu trong dự án.")
            self.video_list.setText("\n".join(f"{v['video_code']}: {v['title'] or v['source_url']}" for v in videos[:30]))
        else:
            self.csv_summary.setText("Chưa có danh sách video trong dự án. Kéo thả hoặc chọn tệp CSV.")
            self.video_list.setText("Chưa có video")

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
            QMessageBox.warning(self, "Không thể lưu dự án", str(exc))
            return
        self.refresh()
        if row is None:
            created = next(item for item in self.store.list_projects() if item["name"] == dialog.name.text().strip())
            self.show_detail(dict(created))

    def add_project(self) -> None:
        self._save_dialog()


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLES)
    store = ProjectStore()
    window = MainWindow(store)
    window.show()
    result = app.exec()
    store.close()
    return result


STYLES = """
QWidget { background:#0d1526; color:#d7e0ef; font-family:"Segoe UI"; font-size:14px; }
#sidebar { background:#09111f; border-right:1px solid #1d2b43; }
#brand { color:#f8fafc; font-size:17px; font-weight:700; }
#brandMark { background:#2563eb; border-radius:8px; padding:7px; }
#navSection { color:#64748b; font-size:11px; font-weight:700; letter-spacing:0.8px; padding-left:10px; }
#sidebar QPushButton { color:#9badc4; border:1px solid transparent; border-radius:8px; min-height:26px; padding:9px 10px; text-align:left; }
#sidebar QPushButton:hover:enabled { background:#13223a; color:#f8fafc; }
#sidebar QPushButton[active="true"] { background:#1d4ed8; color:#fff; font-weight:600; }
#sidebar QPushButton:disabled { color:#63738c; }
#sidebar QPushButton[comingSoon="true"]:disabled { border:1px solid #17263d; }
#storageStatus { background:#101c30; border:1px solid #1d304b; border-radius:9px; color:#71829d; }
#storageTitle { color:#34d399; font-weight:600; }
#header { background:#101a2c; border-bottom:1px solid #1d2b43; }
#breadcrumb { color:#91a4bf; } #phaseTag { background:#1e3a5f; border-radius:5px; color:#7dd3fc; font-size:11px; font-weight:700; padding:5px 8px; }
#title { color:#f8fafc; font-size:27px; font-weight:700; } #detailMeta, #muted { color:#8ea1bb; }
QPushButton { color:#c5d2e5; background:transparent; border:1px solid transparent; border-radius:7px; padding:9px 13px; }
QPushButton:hover:enabled { background:#17263d; color:#fff; } QPushButton:focus { border:1px solid #60a5fa; }
QPushButton#primary { background:#2563eb; border:1px solid #3b82f6; color:#fff; font-weight:600; } QPushButton#primary:hover { background:#1d4ed8; }
QPushButton#secondary { background:#14223a; border:1px solid #2b4264; color:#c6d7ee; } QPushButton#secondary:hover { background:#1b3151; }
QPushButton#back { color:#93c5fd; padding-left:0; }
QScrollArea { background:transparent; } QScrollBar:vertical { background:#0d1526; width:9px; margin:4px; } QScrollBar::handle:vertical { background:#31445f; border-radius:4px; min-height:25px; }
#card { background:#14223a; border:1px solid #263a58; border-radius:11px; } #card:hover { border:1px solid #3b82f6; background:#162741; }
#cardTitle { color:#f8fafc; font-size:17px; font-weight:650; } #tag { background:#1d3c66; border-radius:5px; color:#93c5fd; font-weight:600; padding:4px 8px; }
#status { color:#34d399; font-weight:600; } #path { color:#aabbd2; background:#101c30; border-radius:6px; padding:8px 10px; }
#emptyState { background:#14223a; border:1px dashed #365170; border-radius:12px; } #emptyTitle { color:#f8fafc; font-size:18px; font-weight:650; }
QDialog { background:#101b2e; } #dialogHeader { background:#14233b; border-bottom:1px solid #293e5d; } #dialogIcon { background:#1d4ed8; border-radius:8px; padding:8px; }
#dialogTitle { color:#f8fafc; font-size:19px; font-weight:700; } #dialogSubtitle, #fieldHint { color:#8fa3bf; font-size:12px; } #dialogFooter { background:#14233b; border-top:1px solid #293e5d; }
QFormLayout > QLabel { color:#dbe7f7; font-weight:600; } QLineEdit, QTextEdit { background:#0c1729; border:1px solid #304766; border-radius:7px; color:#f8fafc; padding:9px 10px; } QLineEdit:focus, QTextEdit:focus { border:1px solid #60a5fa; }
#error { color:#fca5a5; background:#3a1f2a; border:1px solid #713342; border-radius:6px; padding:8px 10px; }
#stepActive, #step { background:#14223a; border:1px solid #283e5e; border-radius:10px; min-height:88px; } #stepActive { border-color:#3b82f6; background:#163158; }
#stepNumberActive, #stepNumber { background:#24354e; border-radius:14px; color:#9fb0c7; font-weight:700; min-width:28px; min-height:28px; max-width:28px; max-height:28px; } #stepNumberActive { background:#2563eb; color:#fff; } #stepText { color:#b8c8dd; font-weight:600; }
#detailNotice { background:#13233a; border:1px solid #284365; border-radius:9px; color:#b8cae2; }
#dropZone { border:2px dashed #304766; border-radius:8px; color:#8ea1bb; background:#0c1729; }
#dropZone:hover { border-color:#3b82f6; color:#d7e0ef; }
QTableWidget { background:#0c1729; border:1px solid #284365; color:#d7e0ef; gridline-color:#1c2d46; }
QHeaderView::section { background:#14223a; color:#8ea1bb; border:1px solid #284365; padding:4px; }
"""

if __name__ == "__main__":
    raise SystemExit(main())

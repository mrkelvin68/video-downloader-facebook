import sqlite3
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)

from app.core.exceptions import ImportBlockedError, ImportConfirmationRequired
from app.core.single_instance import acquire_single_instance
from app.database.connection import Database
from app.models.project import Project
from app.services.csv_service import parse_video_csv, sample_video_csv
from app.services.project_service import ProjectService
from app.services.video_service import VideoService
from app.ui.dialogs.csv_preview_dialog import CsvPreviewDialog
from app.ui.dialogs.project_dialog import ProjectDialog
from app.ui.pages.project_detail_page import ProjectDetailPage
from app.ui.pages.project_page import ProjectPage
from app.ui.widgets.sidebar import Sidebar

APP_DIR = Path.home() / ".video-downloader-facebook"
DB_PATH = APP_DIR / "projects.sqlite3"


class MainWindow(QMainWindow):
    def __init__(self, database: Database) -> None:
        super().__init__()
        self.database = database
        self.projects = ProjectService(database)
        self.videos = VideoService(database)
        self.current_project: Project | None = None
        self.pending_csv_text: str | None = None
        self.pending_csv_rows: list[dict] = []
        self.pending_csv_errors: list[str] = []
        self.setWindowTitle("Video Downloader")
        self.resize(1366, 768)
        self.setMinimumSize(960, 640)

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(Sidebar())
        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(self._build_header())
        self.pages = QStackedWidget()
        self.project_page = ProjectPage()
        self.detail_page = ProjectDetailPage()
        self.pages.addWidget(self.project_page)
        self.pages.addWidget(self.detail_page)
        workspace_layout.addWidget(self.pages, 1)
        root_layout.addWidget(workspace, 1)
        self.setCentralWidget(root)
        self.project_page.createRequested.connect(self.add_project)
        self.project_page.editRequested.connect(self.edit_project)
        self.project_page.openRequested.connect(self.show_detail)
        self.detail_page.backRequested.connect(self.show_projects)
        self.detail_page.csvChosen.connect(self.choose_csv)
        self.detail_page.csvDropped.connect(self.load_csv)
        self.detail_page.sampleRequested.connect(self.save_sample_csv)
        self.detail_page.exportRequested.connect(self.export_csv)
        self.detail_page.importRequested.connect(self.confirm_import)
        self.refresh()

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(58)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(32, 0, 32, 0)
        crumb = QLabel("Workspace  /  Quản lý dự án")
        crumb.setObjectName("breadcrumb")
        layout.addWidget(crumb)
        layout.addStretch()
        phase = QLabel("PHASE 1–2")
        phase.setObjectName("phaseTag")
        layout.addWidget(phase)
        return header

    def refresh(self) -> bool:
        try:
            self.project_page.set_projects(self.projects.list_projects())
            return True
        except sqlite3.Error as exc:
            QMessageBox.critical(self, "Lỗi cơ sở dữ liệu", str(exc))
            return False

    def show_projects(self) -> None:
        self.pending_csv_text = None
        self.pending_csv_rows = []
        self.pending_csv_errors = []
        self.pages.setCurrentIndex(0)

    def show_detail(self, project: Project) -> None:
        self.current_project = project
        self.detail_page.set_project(project)
        self._refresh_project_videos()
        self.pages.setCurrentIndex(1)

    def _refresh_project_videos(self) -> bool:
        if self.current_project is None:
            return False
        try:
            self.detail_page.set_saved_videos(
                self.videos.list_videos(self.current_project.uuid)
            )
            return True
        except sqlite3.Error as exc:
            QMessageBox.critical(self, "Lỗi cơ sở dữ liệu", str(exc))
            return False

    def add_project(self) -> None:
        self._save_project()

    def edit_project(self, project: Project) -> None:
        self._save_project(project)

    def _save_project(self, project: Project | None = None) -> None:
        dialog = ProjectDialog(self, project)
        if dialog.exec() != ProjectDialog.DialogCode.Accepted:
            return
        fields = (
            dialog.name.text(),
            dialog.prefix.text(),
            dialog.output_dir.text(),
            dialog.notes.toPlainText(),
        )
        try:
            if project:
                self.projects.update(project.uuid, *fields)
            else:
                self.projects.create(*fields)
        except (ValueError, sqlite3.Error, OSError) as exc:
            QMessageBox.warning(self, "Không thể lưu dự án", str(exc))
            return
        if not self.refresh():
            return
        if project is None:
            try:
                created = next(
                    (
                        item
                        for item in self.projects.list_projects()
                        if item.name == dialog.name.text().strip()
                    ),
                    None,
                )
            except sqlite3.Error as exc:
                QMessageBox.critical(self, "Lỗi cơ sở dữ liệu", str(exc))
                return
            if created:
                self.show_detail(created)

    def choose_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Chọn CSV", "", "CSV (*.csv)")
        if path:
            self.load_csv(Path(path))

    def load_csv(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            self.pending_csv_text = None
            self.pending_csv_rows = []
            self.pending_csv_errors = [f"Không thể đọc CSV: {exc}"]
            self.detail_page.show_preview(path.name, 0, self.pending_csv_errors)
            QMessageBox.warning(self, "Không thể đọc CSV", str(exc))
            return
        rows, errors = parse_video_csv(text)
        self.pending_csv_text = text
        self.pending_csv_rows = rows
        self.pending_csv_errors = errors
        self.detail_page.show_preview(path.name, len(rows), errors)
        CsvPreviewDialog(path.name, rows, errors, self).exec()

    def save_sample_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Lưu CSV mẫu", "videos.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            Path(path).write_text(sample_video_csv(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Không thể lưu", str(exc))

    def export_csv(self) -> None:
        if self.current_project is None:
            return
        try:
            content = self.videos.export_csv(self.current_project.uuid)
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.critical(self, "Không thể xuất CSV", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Xuất CSV", "videos.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            Path(path).write_text(content, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Không thể xuất", str(exc))

    def confirm_import(self) -> None:
        if (
            self.current_project is None
            or self.pending_csv_text is None
            or self.pending_csv_errors
            or not self.pending_csv_rows
        ):
            return
        project_id = self.current_project.uuid
        replace_confirmed = False
        try:
            has_videos = self.database.project_has_videos(project_id)
            if has_videos:
                if not self.database.project_videos_can_be_replaced(project_id):
                    raise ImportBlockedError(
                        "Không thể thay thế: dự án có video đang tải/đã tải hoặc có tệp liên kết."
                    )
                answer = QMessageBox.question(
                    self,
                    "Xác nhận thay thế danh sách",
                    "Danh sách video đã lưu sẽ được thay thế. Tiếp tục?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                replace_confirmed = True
            imported = self.videos.import_csv(
                project_id,
                self.pending_csv_text,
                replace_confirmed=replace_confirmed,
            )
        except ImportConfirmationRequired as exc:
            QMessageBox.warning(self, "Cần xác nhận", str(exc))
            return
        except (ImportBlockedError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Lỗi import", str(exc))
            return
        self.pending_csv_text = None
        self.pending_csv_rows = []
        self.pending_csv_errors = []
        if not self._refresh_project_videos():
            return
        QMessageBox.information(
            self, "Thành công", f"Đã lưu thành công {imported} video vào dự án."
        )


def main() -> int:
    if not acquire_single_instance():
        from ctypes import windll

        windll.user32.MessageBoxW(
            None,
            "Video Downloader đang chạy.",
            "Video Downloader",
            0x40,
        )
        return 0
    application = QApplication(sys.argv)
    style_path = Path(__file__).with_name("styles.qss")
    application.setStyleSheet(style_path.read_text(encoding="utf-8"))
    database = Database(DB_PATH)
    try:
        window = MainWindow(database)
        window.show()
        return application.exec()
    finally:
        database.close()

import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QTimer
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
from app.services.download_queue_service import DownloadQueueService
from app.ui.dialogs.csv_preview_dialog import CsvPreviewDialog
from app.ui.dialogs.project_dialog import ProjectDialog
from app.ui.pages.project_detail_page import ProjectDetailPage
from app.ui.pages.project_page import ProjectPage
from app.ui.pages.download_queue_page import DownloadQueuePage
from app.ui.widgets.sidebar import Sidebar
from app.ui.download_worker import DownloadWorker

APP_DIR = Path.home() / ".video-downloader-facebook"
DB_PATH = APP_DIR / "projects.sqlite3"


class MainWindow(QMainWindow):
    def __init__(self, database: Database) -> None:
        super().__init__()
        self.database = database
        self.projects = ProjectService(database)
        self.videos = VideoService(database)
        self.download_queue = DownloadQueueService(database)
        self.current_project: Project | None = None
        self.pending_csv_text: str | None = None
        self.pending_csv_rows: list[dict] = []
        self.pending_csv_errors: list[str] = []
        self.queue_worker: DownloadWorker | None = None
        self.queue_running = False
        self.pause_after_current = False
        self.queue_job_id: str | None = None
        self.queue_error: str | None = None
        self.download_queue.recover_interrupted()
        self.setWindowTitle("Video Downloader")
        self.resize(1366, 768)
        self.setMinimumSize(960, 640)

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.sidebar = Sidebar()
        root_layout.addWidget(self.sidebar)
        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(self._build_header())
        self.pages = QStackedWidget()
        self.project_page = ProjectPage()
        self.detail_page = ProjectDetailPage()
        self.queue_page = DownloadQueuePage()
        self.pages.addWidget(self.project_page)
        self.pages.addWidget(self.detail_page)
        self.pages.addWidget(self.queue_page)
        workspace_layout.addWidget(self.pages, 1)
        root_layout.addWidget(workspace, 1)
        self.setCentralWidget(root)
        self.project_page.createRequested.connect(self.add_project)
        self.project_page.editRequested.connect(self.edit_project)
        self.project_page.openRequested.connect(self.show_detail)
        self.detail_page.backRequested.connect(self.show_projects)
        self.sidebar.projectsRequested.connect(self.show_projects)
        self.sidebar.queueRequested.connect(self.show_queue)
        self.detail_page.csvChosen.connect(self.choose_csv)
        self.detail_page.csvDropped.connect(self.load_csv)
        self.detail_page.sampleRequested.connect(self.save_sample_csv)
        self.detail_page.exportRequested.connect(self.export_csv)
        self.detail_page.runNowRequested.connect(self.run_now)
        self.detail_page.enqueueRequested.connect(self.add_selected_to_queue)
        self.detail_page.queueRequested.connect(self.show_queue)
        self.detail_page.selectAllRequested.connect(
            self.detail_page.video_table.select_all
        )
        self.detail_page.clearSelectionRequested.connect(
            self.detail_page.video_table.clear_selection
        )
        self.queue_page.backRequested.connect(self.show_projects)
        self.queue_page.startRequested.connect(self.start_queue)
        self.queue_page.pauseRequested.connect(self.pause_queue)
        self.queue_page.resumeRequested.connect(self.start_queue)
        self.queue_page.retryRequested.connect(self.retry_queue_jobs)
        self.queue_page.cancelRequested.connect(self.cancel_queue_jobs)
        self.queue_page.prioritizeRequested.connect(self.prioritize_queue_jobs)
        self.refresh_queue()
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
        phase = QLabel("PHASE 1–3.2")
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
        self.sidebar.set_active("projects")

    def show_detail(self, project: Project) -> None:
        self.current_project = project
        self.detail_page.set_project(project)
        self._refresh_project_videos()
        self.pages.setCurrentIndex(1)
        self.sidebar.set_active("projects")

    def show_queue(self) -> None:
        self.refresh_queue()
        self.pages.setCurrentIndex(2)
        self.sidebar.set_active("queue")

    def _refresh_project_videos(self) -> bool:
        if self.current_project is None:
            return False
        try:
            videos = self.videos.list_videos(self.current_project.uuid)
            job_info = self.download_queue.video_job_info(self.current_project.uuid)
            decorated = []
            for video in videos:
                row = asdict(video)
                job = job_info.get(video.id)
                if job is not None:
                    row["queue_status"] = job["status"]
                    row["queue_progress"] = job["progress"]
                    row["queue_total_bytes"] = job["total_bytes"]
                    row["queue_downloaded_bytes"] = job["downloaded_bytes"]
                decorated.append(row)
            self.detail_page.set_saved_videos(decorated)
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
        if self.current_project is None:
            return
        dialog = CsvPreviewDialog(
            path.name,
            rows,
            errors,
            self.current_project.prefix,
            self,
        )
        if dialog.exec() == CsvPreviewDialog.DialogCode.Accepted:
            self.confirm_import()
        else:
            self.pending_csv_text = None
            self.pending_csv_rows = []
            self.pending_csv_errors = []
            self.detail_page.discard_preview()

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

    def _selected_project_video_ids(self) -> list[str]:
        if self.current_project is None:
            return []
        selected = self.detail_page.video_table.selected_video_ids()
        if not selected:
            QMessageBox.warning(self, "Chọn video", "Hãy chọn ít nhất một video.")
        return selected

    def add_selected_to_queue(self) -> None:
        if self.current_project is None:
            return
        selected = self._selected_project_video_ids()
        if not selected:
            return
        try:
            created = self.download_queue.enqueue(
                self.current_project.uuid, selected
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể thêm vào hàng đợi", str(exc))
            return
        self.refresh_queue()
        self._refresh_project_videos()
        message = (
            f"Đã thêm {len(created)} video vào hàng đợi."
            if created
            else "Các video đã chọn đã có trong hàng đợi."
        )
        self.detail_page.queue_status.setText(message)

    def run_now(self) -> None:
        if self.current_project is None:
            return
        selected = self._selected_project_video_ids()
        if not selected:
            return
        try:
            self.download_queue.enqueue(
                self.current_project.uuid, selected, run_now=True
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể chạy video", str(exc))
            return
        self.refresh_queue()
        self._refresh_project_videos()
        self.start_queue()

    def start_queue(self) -> None:
        self.queue_running = True
        self.pause_after_current = False
        self.queue_page.queue_status.setText("Đang chạy hàng đợi tuần tự.")
        self.detail_page.queue_status.setText("Đang chạy hàng đợi.")
        self._start_next_queue_job()

    def pause_queue(self) -> None:
        if self.queue_worker is None:
            self.queue_running = False
            self.queue_page.queue_status.setText("Hàng đợi đang tạm dừng.")
            self.detail_page.queue_status.setText("Hàng đợi đang tạm dừng.")
            return
        self.pause_after_current = True
        self.queue_page.queue_status.setText(
            "Sẽ tạm dừng sau khi video hiện tại hoàn tất."
        )
        self.detail_page.queue_status.setText("Tạm dừng sau video hiện tại.")

    def _start_next_queue_job(self) -> None:
        if self.queue_worker is not None or not self.queue_running:
            return
        if self.pause_after_current:
            self.queue_running = False
            return
        try:
            job = self.download_queue.next_queued_job()
        except sqlite3.Error as exc:
            self.queue_running = False
            QMessageBox.critical(self, "Lỗi hàng đợi", str(exc))
            return
        if job is None:
            self.queue_running = False
            self.queue_page.queue_status.setText("Không còn job đang chờ.")
            self.detail_page.queue_status.setText("Hàng đợi đã hoàn tất.")
            self.refresh_queue()
            return
        worker = DownloadWorker(self.database.path, job["job_id"], self)
        self.queue_worker = worker
        self.queue_job_id = job["job_id"]
        self.queue_error = None
        worker.progress.connect(self._queue_progress)
        worker.jobFinished.connect(self._queue_job_finished)
        worker.finished.connect(
            lambda active_worker=worker: self._queue_thread_finished(active_worker)
        )
        job_info = self.download_queue.get_job(job["job_id"])
        code = job["job_id"]
        if job_info is not None:
            video = self.database.connection.execute(
                "SELECT video_code FROM videos WHERE id=?", (job_info["video_id"],)
            ).fetchone()
            if video is not None:
                code = video["video_code"]
        self.queue_page.queue_status.setText(f"Đang tải {code}…")
        self.detail_page.queue_status.setText(f"Đang tải {code}…")
        worker.start()

    def _queue_progress(
        self,
        job_id: str,
        percent: float,
        downloaded: int,
        total: int | None,
        speed: float | None,
        eta: int | None,
    ) -> None:
        self.refresh_queue()
        self._refresh_project_videos()
        job = self.download_queue.get_job(job_id)
        code = job_id
        if job is not None:
            video = self.database.connection.execute(
                "SELECT video_code FROM videos WHERE id=?", (job["video_id"],)
            ).fetchone()
            if video is not None:
                code = video["video_code"]
        rate = f"{speed / 1024 / 1024:.1f} MB/s" if speed else "—"
        remaining = f" · còn {eta}s" if eta is not None else ""
        status = f"Đang tải {code}: {percent:.1f}% · {rate}{remaining}"
        self.queue_page.queue_status.setText(status)
        self.detail_page.queue_status.setText(status)

    def _queue_job_finished(self, job_id: str, succeeded: bool, message: str) -> None:
        worker = self.queue_worker
        if worker is None or self.queue_job_id != job_id:
            return
        worker.wait()
        self.queue_worker = None
        self.queue_job_id = None
        self.queue_error = message if not succeeded else None
        can_continue = True
        if not succeeded:
            try:
                self.download_queue.fail_unclaimed(job_id, message)
                job = self.download_queue.get_job(job_id)
                can_continue = job is None or job["status"] not in (
                    "queued",
                    "downloading",
                )
            except sqlite3.Error as exc:
                message += f"; không lưu được trạng thái lỗi: {exc}"
                can_continue = False
        self.refresh_queue()
        if self.current_project is not None:
            self._refresh_project_videos()
        if not succeeded:
            status = f"Job lỗi: {message}"
            self.queue_page.queue_status.setText(status)
            self.detail_page.queue_status.setText(status)
        else:
            self.queue_page.queue_status.setText("Video tải thành công.")
            self.detail_page.queue_status.setText("Video tải thành công.")
        if self.pause_after_current:
            self.pause_after_current = False
            self.queue_running = False
            self.queue_page.queue_status.setText(
                "Đã tạm dừng sau video hiện tại."
            )
            return
        if can_continue:
            QTimer.singleShot(0, self._start_next_queue_job)
        else:
            self.queue_running = False

    def _queue_thread_finished(self, worker: DownloadWorker) -> None:
        if self.queue_worker is not worker:
            return
        job_id = self.queue_job_id
        if job_id is None:
            self.queue_worker = None
            return
        try:
            if not self.download_queue.interrupt_job(
                job_id, "Worker dừng bất thường trước khi báo hoàn tất."
            ):
                self.download_queue.fail_unclaimed(
                    job_id, "Worker dừng bất thường trước khi nhận job."
                )
        except sqlite3.Error as exc:
            QMessageBox.critical(self, "Lỗi hàng đợi", str(exc))
        self.queue_worker = None
        self.queue_job_id = None
        self.refresh_queue()
        self._refresh_project_videos()
        self.queue_page.queue_status.setText(
            "Worker bị gián đoạn; các job còn lại vẫn được giữ."
        )
        QTimer.singleShot(0, self._start_next_queue_job)

    def refresh_queue(self) -> None:
        try:
            self.queue_page.set_jobs(self.download_queue.list_jobs())
        except sqlite3.Error as exc:
            QMessageBox.critical(self, "Lỗi hàng đợi", str(exc))

    def _selected_queue_job_ids(self) -> list[str]:
        selected = self.queue_page.table.selected_job_ids()
        if not selected:
            QMessageBox.warning(
                self, "Chọn job", "Hãy chọn ít nhất một job trong hàng đợi."
            )
        return selected

    def retry_queue_jobs(self) -> None:
        selected = self._selected_queue_job_ids()
        if not selected:
            return
        try:
            created = self.download_queue.retry(selected)
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể retry", str(exc))
            return
        self.refresh_queue()
        self.queue_page.queue_status.setText(
            f"Đã tạo {len(created)} job retry. Bấm Bắt đầu hàng đợi để chạy."
        )

    def cancel_queue_jobs(self) -> None:
        selected = self._selected_queue_job_ids()
        if not selected:
            return
        try:
            cancelled = self.download_queue.cancel(selected)
        except sqlite3.Error as exc:
            QMessageBox.warning(self, "Không thể xóa job", str(exc))
            return
        self.refresh_queue()
        self._refresh_project_videos()
        self.queue_page.queue_status.setText(
            f"Đã xóa {cancelled} job đang chờ; file video không bị xóa."
        )

    def prioritize_queue_jobs(self) -> None:
        selected = self._selected_queue_job_ids()
        if not selected:
            return
        try:
            count = self.download_queue.prioritize(selected)
        except sqlite3.Error as exc:
            QMessageBox.warning(self, "Không thể ưu tiên job", str(exc))
            return
        self.refresh_queue()
        self.queue_page.queue_status.setText(f"Đã ưu tiên {count} job đang chờ.")

    def closeEvent(self, event) -> None:
        if self.queue_worker is not None:
            QMessageBox.information(
                self,
                "Đang tải",
                "Đợi video hiện tại hoàn tất trước khi đóng ứng dụng.",
            )
            event.ignore()
            return
        super().closeEvent(event)


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

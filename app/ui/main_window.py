import csv
from urllib.parse import unquote, urlsplit
import sqlite3
import time
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtGui import QImageReader, QStandardItem
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QInputDialog,
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
from app.database.viral_reels_store import ViralReelsStore
from app.models.project import Project
from app.services.csv_service import parse_video_csv, sample_video_csv
from app.services.project_service import ProjectService
from app.services.video_service import VideoService
from app.services.download_queue_service import DownloadQueueService
from app.services.matrix_service import MatrixService
from app.services.page_export_service import export_pages_csv
from app.services.viral_reels_export_service import export_csv as export_reels_csv, export_xlsx as export_reels_xlsx
from app.services.viral_reels_scheduler import ViralReelsScheduler
from app.services.viral_reels_service import (
    DiscoveryRequest,
    PageCandidate,
    PageDiscoveryProvider,
    PageDiscoveryService,
    extract_page_identity,
    normalize_facebook_url,
    ReelFilters,
    ReelsCollectionService,
    reel_matches,
)
from app.services.apify_facebook_provider import ApifyFacebookPageSearchProvider, ApifyFacebookReelsProvider
from app.services.meta_graph_provider import MetaGraphReelsProvider
from app.services.credential_service import get_apify_token, get_meta_token, get_page_token
from app.services.facebook_connection_service import FacebookConnectionService, FacebookConnectionError
from app.services.facebook_publishing_service import (
    FacebookPublishingError,
    FacebookPublishingPending,
    FacebookPublishingService,
    MetaGraphPublishingClient,
)
from app.ui.dialogs.viral_provider_dialog import ViralProviderDialog, viral_settings
from app.ui.dialogs.csv_preview_dialog import CsvPreviewDialog
from app.ui.dialogs.google_sheets_import_dialog import GoogleSheetsImportDialog
from app.ui.dialogs.manual_video_import_dialog import ManualVideoImportDialog
from app.ui.dialogs.matrix_configuration_dialog import (
    MatrixConfigurationDialog,
    MatrixEntryDialog,
)
from app.ui.dialogs.project_dialog import ProjectDialog
from app.ui.dialogs.project_delete_dialog import ProjectDeleteDialog
from app.ui.pages.project_detail_page import ProjectDetailPage
from app.ui.pages.project_page import ProjectPage
from app.ui.pages.download_queue_page import DownloadQueuePage
from app.ui.pages.matrix_page import MatrixPage
from app.ui.pages.viral_reels_page import ViralReelsPage
from app.ui.widgets.sidebar import Sidebar
from app.ui.download_worker import DownloadWorker
from app.ui.facebook_worker import FacebookPublishWorker, FacebookRequestWorker

APP_DIR = Path.home() / ".video-downloader-facebook"
DB_PATH = APP_DIR / "projects.sqlite3"
APIFY_PROVIDER_NAME = "Apify Facebook Reels Scraper"


def active_viral_provider_name() -> str:
    settings = viral_settings()
    if settings.value("viral_reels/provider", APIFY_PROVIDER_NAME, type=str) == "Meta Graph API":
        if hasattr(settings, "setValue"):
            settings.setValue("viral_reels/provider", APIFY_PROVIDER_NAME)
            settings.sync()
    return APIFY_PROVIDER_NAME


class MainWindow(QMainWindow):
    DEFAULT_QUEUE_CONCURRENCY = 2
    QUEUE_PROGRESS_UI_INTERVAL = 0.2

    @property
    def queue_worker(self) -> DownloadWorker | None:
        """Compatibility view: first active worker (or None)."""
        return next(iter(self.queue_workers.values()), None)

    @queue_worker.setter
    def queue_worker(self, worker: DownloadWorker | None) -> None:
        if worker is None:
            self.queue_workers.clear()
        else:
            self.queue_workers[getattr(worker, "job_id", "")] = worker

    @property
    def queue_job_id(self) -> str | None:
        return next(iter(self.queue_workers), None)

    @queue_job_id.setter
    def queue_job_id(self, value: str | None) -> None:
        if value is None:
            self.queue_workers.clear()
        elif "" in self.queue_workers:
            self.queue_workers[value] = self.queue_workers.pop("")

    @staticmethod
    def _load_queue_concurrency() -> int:
        value = QSettings("VideoDownloader", "VideoDownloaderFacebook").value(
            "downloads/concurrency", MainWindow.DEFAULT_QUEUE_CONCURRENCY
        )
        try:
            return max(1, min(5, int(value)))
        except (TypeError, ValueError):
            return MainWindow.DEFAULT_QUEUE_CONCURRENCY

    def set_queue_concurrency(self, value: int) -> None:
        self.queue_concurrency = max(1, min(5, int(value)))
        QSettings("VideoDownloader", "VideoDownloaderFacebook").setValue(
            "downloads/concurrency", self.queue_concurrency
        )
        if self.queue_page.concurrency_combo.currentData() != self.queue_concurrency:
            self.queue_page.set_concurrency(self.queue_concurrency)
        if self.queue_running and not self.pause_after_current:
            self._start_next_queue_job()
        self._update_queue_stats()

    def _update_queue_stats(self) -> None:
        try:
            pending = self.download_queue.count_jobs(("queued",))
        except sqlite3.Error:
            pending = 0
        speed = sum(self._queue_speeds.get(job_id, 0.0) for job_id in self.queue_workers)
        self.queue_page.set_stats(len(self.queue_workers), pending, speed)

    def __init__(self, database: Database) -> None:
        super().__init__()
        self.database = database
        self.projects = ProjectService(database)
        self.videos = VideoService(database)
        self.download_queue = DownloadQueueService(database)
        self.facebook_connection = FacebookConnectionService(database)
        self.facebook_publishing = FacebookPublishingService(database)
        self._facebook_workers: dict[str, FacebookRequestWorker] = {}
        self._facebook_publish_worker: FacebookPublishWorker | None = None
        self.matrices = MatrixService(database)
        self.viral_reels = ViralReelsStore(database)
        self.viral_reels_scheduler = ViralReelsScheduler(self._run_scheduled_viral_scan)
        self.current_project: Project | None = None
        self.current_matrix_id: str | None = None
        self.pending_csv_text: str | None = None
        self.pending_csv_rows: list[dict] = []
        self.pending_csv_errors: list[str] = []
        self.queue_workers: dict[str, DownloadWorker] = {}
        self._queue_video_ids: dict[str, str] = {}
        self._queue_speeds: dict[str, float] = {}
        self._queue_ui_stamp: dict[str, float] = {}
        self._queue_codes: dict[str, str] = {}
        self._pending_wait_ids: set[str] = set()
        self.queue_concurrency = self._load_queue_concurrency()
        self.queue_running = False
        self.pause_after_current = False
        self.queue_error: str | None = None
        self.pending_project_delete: tuple[str, bool] | None = None
        self.pending_queue_delete: tuple[list[str], str] | None = None
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
        self.matrix_page = MatrixPage()
        self.viral_reels_page = ViralReelsPage()
        self.pages.addWidget(self.project_page)
        self.pages.addWidget(self.detail_page)
        self.pages.addWidget(self.queue_page)
        self.pages.addWidget(self.matrix_page)
        self.pages.addWidget(self.viral_reels_page)
        workspace_layout.addWidget(self.pages, 1)
        root_layout.addWidget(workspace, 1)
        self.setCentralWidget(root)
        self.project_page.createRequested.connect(self.add_project)
        self.project_page.editRequested.connect(self.edit_project)
        self.project_page.openRequested.connect(self.show_detail)
        self.project_page.matrixRequested.connect(self.show_matrices)
        self.project_page.deleteRequested.connect(self.delete_project)
        self.detail_page.backRequested.connect(self.show_projects)
        self.detail_page.projectEditRequested.connect(
            lambda: self.edit_project(self.current_project)
            if self.current_project is not None else None
        )
        self.detail_page.facebookConnectRequested.connect(self._connect_facebook)
        self.detail_page.facebookTestRequested.connect(self._test_facebook)
        self.detail_page.facebookRefreshRequested.connect(self._refresh_facebook_async)
        self.detail_page.facebookDisconnectRequested.connect(self._disconnect_facebook)
        self.detail_page.facebookDefaultPageRequested.connect(self._set_facebook_default)
        self.detail_page.facebookSaveDraftRequested.connect(self._save_facebook_draft)
        self.detail_page.facebookPrepareRequested.connect(self._prepare_facebook_video)
        self.detail_page.facebookPublishNowRequested.connect(self._publish_facebook_now)
        self.detail_page.facebookJobReconcileRequested.connect(self._reconcile_facebook_job)
        self.detail_page.facebookJobRetryRequested.connect(self._retry_facebook_job)
        self.detail_page.facebookJobErrorRequested.connect(self._show_facebook_job_error)
        self.detail_page.facebookJobCancelRequested.connect(self._cancel_facebook_job)
        self.detail_page.facebookJobOpenRequested.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        self.detail_page.facebookJobDeleteRequested.connect(self._delete_facebook_job_history)
        self.sidebar.projectsRequested.connect(self.show_projects)
        self.sidebar.queueRequested.connect(self.show_queue)
        self.sidebar.viralReelsRequested.connect(self.show_viral_reels)
        self.viral_reels_page.findPagesRequested.connect(self.find_viral_pages)
        self.viral_reels_page.providerRequested.connect(self.configure_viral_provider)
        self.viral_reels_page.scanReelsRequested.connect(self.scan_viral_reels)
        self.viral_reels_page.recoverRunRequested.connect(self.recover_apify_reels_run)
        self.viral_reels_page.stopRequested.connect(self.stop_viral_operation)
        self.viral_reels_page.pageAddRequested.connect(self.add_viral_page)
        self.viral_reels_page.pagesImportRequested.connect(self.import_viral_pages)
        self.viral_reels_page.pagesDeleteRequested.connect(self.delete_viral_pages)
        self.viral_reels_page.foundPagesDeleteRequested.connect(self.delete_found_viral_pages)
        self.viral_reels_page.filtersChanged.connect(self.apply_stored_viral_reels_filters)
        self.load_viral_pages()
        self.apply_stored_viral_reels_filters()
        self._refresh_viral_provider_status()
        self.viral_reels_page.exportCsvRequested.connect(self.export_viral_reels_csv)
        self.viral_reels_page.exportPagesCsvRequested.connect(self.export_viral_pages_csv)
        self.viral_reels_page.exportExcelRequested.connect(self.export_viral_reels_xlsx)
        self.detail_page.csvChosen.connect(self.choose_csv)
        self.detail_page.csvDropped.connect(self.load_csv)
        self.detail_page.googleSheetsRequested.connect(self.import_google_sheets)
        self.detail_page.manualImportRequested.connect(self.import_manual_videos)
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
        self.queue_page.set_concurrency(self.queue_concurrency)
        self.queue_page.concurrencyChanged.connect(self.set_queue_concurrency)
        self.queue_page.pauseRequested.connect(self.pause_queue)
        self.queue_page.resumeRequested.connect(self.start_queue)
        self.queue_page.retryRequested.connect(self.retry_queue_jobs)
        self.queue_page.cancelRequested.connect(self.cancel_queue_jobs)
        self.queue_page.prioritizeRequested.connect(self.prioritize_queue_jobs)
        self.queue_page.deleteSelectedRequested.connect(
            self.delete_selected_queue_jobs
        )
        self.queue_page.deleteFailedRequested.connect(
            self.delete_failed_queue_jobs
        )
        self.queue_page.deleteCompletedRequested.connect(
            self.delete_completed_queue_jobs
        )
        self.queue_page.deleteInactiveRequested.connect(
            self.delete_inactive_queue_jobs
        )
        self.matrix_page.backRequested.connect(self.show_projects)
        self.matrix_page.matrixChanged.connect(self._refresh_matrices)
        self.matrix_page.newMatrixRequested.connect(self._create_matrix)
        self.matrix_page.configureRequested.connect(self._configure_matrix)
        self.matrix_page.exportRequested.connect(self._export_matrix)
        self.matrix_page.regenerateRequested.connect(self._regenerate_matrix)
        self.matrix_page.undoRequested.connect(self._undo_matrix)
        self.matrix_page.renameRequested.connect(self._rename_matrix)
        self.matrix_page.deleteRequested.connect(self._delete_matrix)
        self.matrix_page.editEntryRequested.connect(self._edit_matrix_entry)
        self.matrix_page.publishStatusRequested.connect(self._set_matrix_status)
        self.matrix_page.facebookUploadStatusRequested.connect(
            self._set_video_facebook_uploaded
        )
        self.matrix_page.videoEditRequested.connect(self._update_project_video_field)
        self.matrix_page.videoImageRequested.connect(
            self._choose_project_video_thumbnail
        )
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
        phase = QLabel("PHASE 1–4.1")
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

    def delete_project(self, project: Project) -> None:
        try:
            details = self.projects.deletion_details(project.uuid)
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể xóa dự án", str(exc))
            return
        dialog = ProjectDeleteDialog(
            str(details["name"]),
            str(details["output_dir"]),
            int(details["video_count"]),
            int(details["job_count"]),
            self,
        )
        if dialog.exec() != ProjectDeleteDialog.DialogCode.Accepted:
            return
        delete_files = dialog.delete_files_radio.isChecked()
        if delete_files:
            answer = QMessageBox.warning(
                self,
                "Xác nhận xóa tệp",
                "Thao tác này sẽ xóa vĩnh viễn toàn bộ thư mục dự án. "
                "Bạn có chắc chắn muốn tiếp tục?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        try:
            downloading_ids = [
                row["job_id"]
                for row in self.database.connection.execute(
                    """SELECT job_id FROM download_jobs
                       WHERE project_id=? AND status='downloading'""",
                    (project.uuid,),
                ).fetchall()
            ]
        except sqlite3.Error as exc:
            QMessageBox.critical(self, "Lỗi hàng đợi", str(exc))
            return
        if downloading_ids:
            if any(job_id not in self.queue_workers for job_id in downloading_ids):
                QMessageBox.warning(
                    self,
                    "Không thể dừng tải",
                    "Không tìm thấy worker đang sở hữu job tải của dự án. "
                    "Dự án chưa bị xóa.",
                )
                return
            answer = QMessageBox.question(
                self,
                "Dừng video đang tải",
                "Dự án đang có video được tải. Hủy job hiện tại, chờ worker dừng "
                "hoàn toàn rồi mới xóa?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.pending_project_delete = (project.uuid, delete_files)
            self._pending_wait_ids = set(downloading_ids)
            self.queue_running = False
            self.pause_after_current = False
            self.setEnabled(False)
            self.queue_page.queue_status.setText(
                "Đang dừng worker trước khi xóa dự án…"
            )
            for job_id in downloading_ids:
                self.queue_workers[job_id].request_cancel()
            return
        self._perform_project_deletion(project.uuid, delete_files)

    def _perform_project_deletion(self, project_id: str, delete_files: bool) -> None:
        try:
            result = self.projects.delete(project_id, delete_files=delete_files)
        except (ValueError, OSError, sqlite3.Error, RuntimeError) as exc:
            QMessageBox.warning(self, "Không thể xóa dự án", str(exc))
            self.refresh()
            self.refresh_queue()
            return
        if self.current_project is not None and self.current_project.uuid == project_id:
            self.current_project = None
            self.current_matrix_id = None
            self.pending_csv_text = None
            self.pending_csv_rows = []
            self.pending_csv_errors = []
            self.show_projects()
        self.refresh()
        self.refresh_queue()
        if result.cleanup_error:
            QMessageBox.warning(
                self,
                "Đã xóa metadata, chưa xóa hết tệp",
                "Thông tin dự án đã được xóa khỏi SQLite nhưng không thể dọn "
                "thư mục video hoàn toàn.\n"
                f"Thư mục còn lại: {result.remaining_files_path}\n"
                f"Chi tiết: {result.cleanup_error}",
            )
        elif delete_files:
            QMessageBox.information(
                self, "Đã xóa dự án", "Metadata và thư mục video đã được xóa."
            )
        else:
            QMessageBox.information(
                self,
                "Đã xóa dự án",
                "Metadata và job đã được xóa khỏi ứng dụng. "
                "Các tệp video trên ổ đĩa được giữ nguyên.",
            )

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
        self._refresh_facebook()
        self.pages.setCurrentIndex(1)
        self.sidebar.set_active("projects")

    def _refresh_facebook(self) -> None:
        if self.current_project is None:
            return
        project_id = self.current_project.uuid
        pages = self.facebook_publishing.list_pages(project_id)
        default_page = next((page for page in pages if page.get("is_default")), None)
        drafts = {row["video_id"]: row for row in self.facebook_publishing.list_drafts(project_id)}
        jobs_by_video: dict[str, list[dict]] = {}
        for job in self.facebook_publishing.list_jobs(project_id):
            jobs_by_video.setdefault(str(job["video_id"]), []).insert(0, job)
        rows = []
        for video in self.videos.list_videos(project_id):
            row = asdict(video)
            row.update(drafts.get(video.id, {}))
            if not row.get("page_id") and default_page is not None:
                row["page_id"] = default_page["page_id"]
                row["page_name"] = default_page["page_name"]
            row.setdefault("video_id", video.id)
            row.setdefault("video_title", video.title)
            rows.append(row)
        self.detail_page.set_facebook_data(pages, rows, jobs_by_video)

    def _connect_facebook(self, token: str) -> None:
        if self.current_project is None:
            return
        project_id = self.current_project.uuid
        database_path = self.database.path
        self._run_facebook_request(
            "connect", self.detail_page.facebook_connect_button,
            lambda: self._facebook_connection_in_worker(database_path, project_id, token, "connect"),
            lambda _pages: self._refresh_facebook(),
        )

    def _test_facebook(self) -> None:
        if self.current_project is None:
            return
        project_id = self.current_project.uuid
        database_path = self.database.path
        self._run_facebook_request(
            "test", self.detail_page.facebook_test_button,
            lambda: self._facebook_connection_in_worker(database_path, project_id, None, "test"),
            lambda _pages: self.detail_page.facebook_status.setText("Đã kết nối Facebook"),
        )

    def _refresh_facebook_async(self) -> None:
        if self.current_project is None:
            return
        project_id = self.current_project.uuid
        database_path = self.database.path
        self._run_facebook_request(
            "refresh", self.detail_page.facebook_refresh_button,
            lambda: self._facebook_connection_in_worker(database_path, project_id, None, "test"),
            lambda _pages: self._refresh_facebook(),
        )

    @staticmethod
    def _facebook_connection_in_worker(database_path: str, project_id: str, token: str | None, action: str):
        worker_database = Database(database_path)
        try:
            service = FacebookConnectionService(worker_database)
            if action == "connect":
                return service.connect(project_id, token or "")
            return service.test_connection(project_id)
        finally:
            worker_database.close()

    def _run_facebook_request(self, kind: str, button, operation, on_success) -> None:
        if kind in self._facebook_workers or self.current_project is None:
            return
        project_id = self.current_project.uuid
        worker = FacebookRequestWorker(operation, self)
        self._facebook_workers[kind] = worker
        button.set_loading(True)

        def succeeded(value) -> None:
            if self.current_project is not None and self.current_project.uuid == project_id:
                on_success(value)

        def failed(message: str) -> None:
            if self.current_project is not None and self.current_project.uuid == project_id:
                QMessageBox.warning(self, "Facebook", message)

        def finished() -> None:
            self._facebook_workers.pop(kind, None)
            button.set_loading(False)
            worker.deleteLater()

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.finished.connect(finished)
        worker.start()

    def _disconnect_facebook(self) -> None:
        if self.current_project is None:
            return
        self.facebook_connection.disconnect(self.current_project.uuid)
        self._refresh_facebook()

    def _set_facebook_default(self, page_id: str) -> None:
        if self.current_project is None or not page_id:
            return
        try:
            self.facebook_connection.set_default_page(self.current_project.uuid, page_id)
        except FacebookConnectionError as exc:
            QMessageBox.warning(self, "Fanpage mặc định", str(exc))

    def _save_facebook_draft(self, values: dict) -> None:
        if self.current_project is None:
            return
        video_id = values.get("video_id")
        if not video_id:
            return
        self.facebook_publishing.save_draft(
            self.current_project.uuid, str(video_id),
            page_id=values.get("page_id"), facebook_title=values["title"],
            description=values["description"], hashtags=values["hashtags"],
        )
        self._refresh_facebook()

    def _prepare_facebook_video(self, values: dict) -> None:
        if self.current_project is None:
            return
        try:
            self.facebook_publishing.prepare_video(self.current_project.uuid, values["video_id"], values.get("file_path"))
            self._refresh_facebook()
        except Exception as exc:
            QMessageBox.warning(self, "Kiểm tra video", str(exc))

    def _publish_facebook_now(self, values: dict) -> None:
        if self.current_project is None or self._facebook_publish_worker is not None:
            return
        project_id = self.current_project.uuid
        video_id = str(values.get("video_id") or "")
        page_id = str(values.get("page_id") or "")
        if not video_id or not page_id:
            QMessageBox.warning(self, "Đăng Facebook", "Hãy chọn một video và một Fanpage.")
            return
        video = self.database.connection.execute(
            "SELECT title, local_video_path FROM videos WHERE id=? AND project_id=?", (video_id, project_id)
        ).fetchone()
        draft = self.database.connection.execute(
            "SELECT * FROM facebook_drafts WHERE project_id=? AND video_id=?", (project_id, video_id)
        ).fetchone()
        if video is None or draft is None:
            QMessageBox.warning(self, "Đăng Facebook", "Hãy lưu nội dung và chọn video hợp lệ.")
            return
        selected_path = Path(values.get("file_path") or video["local_video_path"] or "")
        if str(draft["preparation_status"] or "") != "READY" or not selected_path.is_file():
            QMessageBox.warning(self, "Đăng Facebook", "Video chưa sẵn sàng hoặc không còn file local.")
            return
        try:
            FacebookPublishingService._inspect_video(selected_path)
            confirmed_identity = FacebookPublishingService.file_identity(selected_path)
        except FacebookPublishingError as exc:
            QMessageBox.warning(self, "Đăng Facebook", str(exc))
            return
        active = self.database.connection.execute(
            """SELECT status FROM facebook_publishing_jobs
               WHERE project_id=? AND video_id=? AND page_id=?
                 AND status NOT IN ('FAILED','CANCELLED')""", (project_id, video_id, page_id)
        ).fetchone()
        if active is not None:
            QMessageBox.warning(self, "Đăng Facebook", "Video này đã có job đang hoạt động hoặc đang chờ xác nhận.")
            return
        page = self.database.connection.execute(
            "SELECT page_name FROM facebook_pages WHERE project_id=? AND page_id=?", (project_id, page_id)
        ).fetchone()
        if page is None:
            QMessageBox.warning(self, "Đăng Facebook", "Fanpage không thuộc kết nối của dự án.")
            return
        description = str(draft["description"] or "")
        hashtags = str(draft["hashtags"] or "").strip()
        if hashtags:
            description = f"{description}\n\n{hashtags}" if description else hashtags
        confirmation = QMessageBox.question(
            self, "Xác nhận đăng video",
            f"Video: {video['title']}\nFile: {selected_path}\nFanpage: {page['page_name']}\n"
            f"Page ID: {page_id}\nTiêu đề: {draft['facebook_title']}\nCaption: {description}\n"
            f"Loại: Page video thường\n\nĐăng ngay?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        try:
            token = get_page_token(project_id, page_id)
            if not token:
                raise FacebookPublishingError("Chưa có Page Access Token cho Fanpage này.")
            job_id = self.facebook_publishing.enqueue_from_draft(
                project_id, video_id, page_id, file_path=str(selected_path),
                confirmed_identity=confirmed_identity,
            )
        except (FacebookPublishingError, ValueError) as exc:
            QMessageBox.warning(self, "Đăng Facebook", str(exc))
            return
        self._start_facebook_job(job_id, token, project_id)

    def _start_facebook_job(self, job_id: str, token: str, project_id: str) -> None:
        database_path = self.database.path
        self.detail_page.facebook_publish_button.set_loading(True)
        self.detail_page.facebook_status.setText("Đang chuẩn bị đăng video…")

        def publish(progress_callback):
            worker_database = Database(database_path)
            try:
                client = MetaGraphPublishingClient(token)
                service = FacebookPublishingService(worker_database, client)
                return service.publish(job_id, progress=progress_callback)
            finally:
                worker_database.close()

        worker = FacebookPublishWorker(publish, self)
        self._facebook_publish_worker = worker
        worker.progressChanged.connect(lambda value: self.detail_page.facebook_status.setText(f"Đang upload: {value:.0f}%"))
        worker.succeeded.connect(lambda result: self._facebook_publish_succeeded(result, project_id))
        worker.failed.connect(lambda message: self._facebook_publish_failed(message, project_id, job_id))
        worker.finished.connect(self._facebook_publish_finished)
        worker.start()

    def _reconcile_facebook_job(self, job_id: str) -> None:
        if self.current_project is None:
            return
        project_id = self.current_project.uuid
        database_path = self.database.path

        def reconcile():
            worker_database = Database(database_path)
            try:
                job = FacebookPublishingService(worker_database).get_job(job_id)
                if job is None or job["project_id"] != project_id:
                    raise FacebookPublishingError("Không tìm thấy job Facebook trong dự án hiện tại.")
                token = get_page_token(project_id, job["page_id"])
                if not token:
                    raise FacebookPublishingError("Chưa có Page Access Token cho Fanpage này.")
                return FacebookPublishingService(worker_database, MetaGraphPublishingClient(token)).reconcile_job(job_id)
            finally:
                worker_database.close()

        self._run_facebook_request(
            "reconcile-" + job_id, self.detail_page.facebook_reconcile_button, reconcile,
            lambda _job: self._refresh_facebook(),
        )

    def _show_facebook_job_error(self, job_id: str) -> None:
        job = self.facebook_publishing.get_job(job_id)
        if job is not None:
            QMessageBox.information(self, "Lỗi publishing job", str(job.get("last_error") or "Job chưa có lỗi được lưu."))

    def _cancel_facebook_job(self, job_id: str) -> None:
        try:
            self.facebook_publishing.cancel(job_id)
            self._refresh_facebook()
        except FacebookPublishingError as exc:
            QMessageBox.warning(self, "Hủy publishing job", str(exc))

    def _delete_facebook_job_history(self, job_id: str) -> None:
        job = self.facebook_publishing.get_job(job_id)
        if job is None:
            return
        confirmation = QMessageBox.question(
            self, "Xóa lịch sử publishing",
            f"Xóa lịch sử job FAILED/CANCELLED này?\nJob: {job_id}\nFile: {job.get('local_file_path')}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        try:
            self.facebook_publishing.delete_job_history(job_id)
            self._refresh_facebook()
        except FacebookPublishingError as exc:
            QMessageBox.warning(self, "Xóa lịch sử publishing", str(exc))

    def _retry_facebook_job(self, job_id: str) -> None:
        if self.current_project is None or self._facebook_publish_worker is not None:
            return
        job = self.facebook_publishing.get_job(job_id)
        if job is None or job["project_id"] != self.current_project.uuid:
            return
        state = str(job.get("display_status") or job.get("status") or "")
        risk_confirmed = False
        if state == "UNRESOLVED":
            confirmation = QMessageBox.question(
                self, "Xác nhận đăng lại có rủi ro",
                f"Job cũ: {job_id}\nRemote Video ID: {job.get('remote_video_id') or '-'}\n"
                f"Fanpage: {job.get('page_id')}\nFile: {job.get('local_file_path')}\n\n"
                "Trạng thái chưa xác minh. Video có thể đã được đăng nhưng ứng dụng chưa đọc được.\n\n"
                "Tôi hiểu rằng việc đăng lại có thể tạo bài đăng trùng.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            risk_confirmed = confirmation == QMessageBox.StandardButton.Yes
            if not risk_confirmed:
                return
        elif state == "FAILED":
            confirmation = QMessageBox.question(
                self, "Xác nhận đăng lại", f"Đăng lại job FAILED này?\nFile: {job.get('local_file_path')}\nFanpage: {job.get('page_id')}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirmation != QMessageBox.StandardButton.Yes:
                return
        else:
            return
        try:
            new_job_id = self.facebook_publishing.retry_job(job_id, risk_confirmed=risk_confirmed)
            token = get_page_token(self.current_project.uuid, job["page_id"])
            if not token:
                raise FacebookPublishingError("Chưa có Page Access Token cho Fanpage này.")
        except FacebookPublishingError as exc:
            QMessageBox.warning(self, "Đăng lại Facebook", str(exc))
            return
        self._refresh_facebook()
        self._start_facebook_job(new_job_id, token, self.current_project.uuid)

    def _facebook_publish_succeeded(self, result: dict, project_id: str) -> None:
        if self.current_project is None or self.current_project.uuid != project_id:
            return
        self.detail_page.facebook_status.setText(f"Đã đăng thành công: {result.get('published_url') or result.get('published_post_id')}")
        self._refresh_facebook()

    def _facebook_publish_failed(self, message: str, project_id: str, job_id: str) -> None:
        if self.current_project is None or self.current_project.uuid != project_id:
            return
        state = self.database.connection.execute("SELECT status FROM facebook_publishing_jobs WHERE id=?", (job_id,)).fetchone()
        if state is not None and state["status"] in {"PROCESSING", "PUBLISHING"}:
            self.detail_page.facebook_status.setText("Meta đã nhận video nhưng chưa xác nhận hoàn tất; không tự động đăng lại.")
        else:
            self.detail_page.facebook_status.setText("Đăng thất bại")
        QMessageBox.warning(self, "Đăng Facebook", message)

    def _facebook_publish_finished(self) -> None:
        self.detail_page.facebook_publish_button.set_loading(False)
        worker = self._facebook_publish_worker
        self._facebook_publish_worker = None
        if worker is not None:
            worker.deleteLater()

    def show_queue(self) -> None:
        self.refresh_queue()
        self.pages.setCurrentIndex(2)
        self.sidebar.set_active("queue")

    def show_viral_reels(self) -> None:
        self.pages.setCurrentWidget(self.viral_reels_page)
        self.sidebar.set_active("viral-reels")

    @staticmethod
    def _viral_schedule(value: str) -> str:
        return {
            "Manual": "manual", "Every 6 hours": "6h",
            "Every 12 hours": "12h", "Daily": "daily",
        }.get(value, "manual")

    @staticmethod
    def _viral_filters(values: dict) -> ReelFilters:
        def boundary(value: str, end: bool = False) -> datetime | None:
            if not value:
                return None
            parsed = datetime.fromisoformat(value).replace(tzinfo=UTC)
            return parsed + timedelta(days=1) - timedelta(microseconds=1) if end else parsed

        adv = bool(values.get("advanced_enabled", True))
        return ReelFilters(
            minimum_views=(values["min_views"] or None) if adv else None,
            country=values["country"] if adv else "All",
            language=values["language"] if adv else "All",
            topic=values["topic"] if adv else "",
            published_from=boundary(values["date_from"]) if adv else None,
            published_to=boundary(values["date_to"], True) if adv else None,
            maximum_results=values["max_results"],
        )

    @staticmethod
    def _filter_rejection_reason(filters: ReelFilters) -> str:
        parts: list[str] = []
        if filters.minimum_views is not None:
            parts.append(f"Minimum views >= {filters.minimum_views:,}")
        if filters.country != "All":
            parts.append(f"Country = {filters.country}")
        if filters.language != "All":
            parts.append(f"Language = {filters.language}")
        if filters.topic:
            parts.append(f"Topic = {filters.topic}")
        if filters.published_from or filters.published_to:
            parts.append("Date range")
        return "0 Reels khớp bộ lọc: " + ", ".join(parts) if parts else ""

    def apply_stored_viral_reels_filters(self, values: dict | None = None, collected_override: int | None = None) -> list[dict]:
        page = self.viral_reels_page
        vals = values if isinstance(values, dict) else page.filters()
        filters = self._viral_filters(vals)
        rows = ViralReelsStore(self.database).list_reels(limit=100000)
        selected_urls = set(page.checked_page_urls())
        page_rows = [dict(r) for r in rows if (not selected_urls or str(r["page_url"]) in selected_urls)]
        deduped_valid = list({str(r.get("reel_id") or r.get("reel_url")): r for r in page_rows if r.get("reel_url")}.values())
        valid_count = len(deduped_valid)
        collected_count = collected_override if collected_override is not None else len(page_rows)
        matched = [r for r in deduped_valid if reel_matches(r, filters)]
        filtered_count = valid_count - len(matched)
        displayed = matched[: filters.maximum_results]
        reason = self._filter_rejection_reason(filters) if (valid_count > 0 and not displayed) else ""
        page.set_results(displayed)
        page.set_scan_stats(collected_count, valid_count, filtered_count, len(displayed), reason)
        return displayed

    def _configure_viral_schedule(self, value: str) -> None:
        schedule = self._viral_schedule(value)
        self.viral_reels.set_schedule(schedule)
        self.viral_reels_scheduler.start(schedule)

    def _run_scheduled_viral_scan(self) -> None:
        # No authorized provider is configured; never substitute mock data.
        QTimer.singleShot(0, lambda: QMessageBox.information(
            self, "Authorized provider required",
            "Scheduled Viral Reels scan blocked: configure an authorized Facebook API or licensed provider.",
        ))

    def load_viral_pages(self, select: tuple[str, ...] = ()) -> None:
        rows = ViralReelsStore(self.database).list_pages(limit=100000)
        self.viral_reels_page.set_found_pages([{"page_name": r["page_name"], "page_url": r["page_url"], "page_id": r["page_id"], "data_source": r["source"]} for r in rows])
        self.viral_reels_page.set_scan_pages([{"page_name": r["page_name"], "page_url": r["page_url"]} for r in rows], select)

    def add_viral_page(self, url: str) -> None:
        try:
            page_id, name, normalized = extract_page_identity(url)
            store = ViralReelsStore(self.database)
            pages = store.list_pages(limit=100000)
            if not any(str(row["page_url"]) == normalized or (page_id and str(row["page_id"]) == page_id) for row in pages):
                store.upsert_page(PageCandidate(page_id, name, normalized, "Unknown", "Unknown", ""))
            self.load_viral_pages((normalized,))
        except Exception as error:
            QMessageBox.warning(self, "Invalid Page", str(error))
    def import_viral_pages(self, path: str) -> None:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            for row in csv.reader(handle):
                if row and row[0].strip():
                    self.add_viral_page(row[0].strip())

    def delete_viral_pages(self, urls: list) -> None:
        store = ViralReelsStore(self.database)
        store.delete_pages(urls)
        self.load_viral_pages()

    def delete_found_viral_pages(self, rows: list) -> None:
        # Find Pages results are a view-level list; do not delete saved Pages.
        self.viral_reels_page.remove_found_pages(rows)

    def configure_viral_provider(self) -> None:
        if ViralProviderDialog(self).exec() == ViralProviderDialog.DialogCode.Accepted:
            self._refresh_viral_provider_status()

    def _refresh_viral_provider_status(self) -> None:
        provider_name = active_viral_provider_name()
        provider = self._viral_provider()
        if provider is None:
            self.viral_reels_page.set_provider_status(provider_name, "Not configured", "Unavailable")
            return
        capabilities = "Find Pages + Scan Page Reels"
        self.viral_reels_page.set_provider_status(provider.name, "Configured; connection unverified", capabilities)

    def _viral_provider(self):
        token = get_apify_token()
        if not token:
            return None
        return ApifyFacebookReelsProvider(token)

    def find_viral_pages(self, values: dict) -> None:
        token = get_apify_token()
        keywords = tuple(item.strip() for item in values.get("keywords", "").split(",") if item.strip())
        maximum = max(1, min(int(values.get("max_results", 50)), 100))
        if not token:
            self.viral_reels_page.set_busy(False)
            QMessageBox.information(self, "Find Pages", "Configure Apify token first.")
            return
        if not keywords:
            self.viral_reels_page.set_busy(False)
            QMessageBox.information(self, "Find Pages", "Enter at least one keyword.")
            return
        if QMessageBox.question(self, "Charge approval", "Facebook Page Search may incur Apify charges. Continue?") != QMessageBox.StandardButton.Yes:
            self.viral_reels_page.set_busy(False)
            return

        def discover(cancel, progress):
            if cancel.is_set():
                return []
            provider = ApifyFacebookPageSearchProvider(token)
            provider.test_connection()
            progress(0, 1, "Searching Facebook Pages")
            request = DiscoveryRequest(country=values.get("country", "All"), keywords=keywords, maximum_pages=maximum)
            store = ViralReelsStore.for_thread(self.database.path)
            try:
                return PageDiscoveryService(store, [provider]).discover(request)
            finally:
                store.close()

        def show_pages(pages):
            self.viral_reels_page.set_found_pages([
                {"page_name": page.page_name, "page_url": page.page_url,
                 "page_id": page.page_id, "data_source": page.source}
                for page in pages
            ])
            self.viral_reels_page.set_progress(0, 0, 0, 0)
            if not pages:
                QMessageBox.information(self, "Find Pages", "No Facebook Pages found.")

        self.viral_reels_page.run_operation(discover, show_pages)

    def scan_viral_reels(self, values: dict) -> None:
        self._configure_viral_schedule(values["schedule"])
        if values.get("mode") == "discover_trending":
            self._discover_cached_reels(values)
            return
        if values.get("data_source") != "Database":
            self._scan_selected_pages(values)
            return
        filters = self._viral_filters(values)

        def load(cancel, progress):
            worker_store = ViralReelsStore.for_thread(self.database.path)
            try:
                rows = worker_store.list_reels(values["max_results"] * 10)
                results = []
                for index, row in enumerate(rows, 1):
                    if cancel.is_set():
                        break
                    record = dict(row)
                    if reel_matches(record, filters):
                        results.append(record)
                    progress(index, len(rows), "Filtering stored Reels")
                return results[: filters.maximum_results]
            finally:
                worker_store.close()

        self.viral_reels_page.run_operation(load, self.viral_reels_page.set_results)

    def _discover_cached_reels(self, values: dict) -> None:
        """Discover from saved-page SQLite cache; the configured Actor has no keyword search."""
        filters = self._viral_filters(values)
        page = self.viral_reels_page

        def load(cancel, progress):
            store = ViralReelsStore.for_thread(self.database.path)
            try:
                saved_urls = {str(row["page_url"]) for row in store.list_pages(limit=100000)}
                progress(0, 1, "Loading cached Reels")
                if not saved_urls:
                    return [], 0, 0
                rows = store.list_reels(limit=100000)
                matched = []
                candidates = 0
                for index, row in enumerate(rows, 1):
                    if cancel.is_set():
                        break
                    record = dict(row)
                    if str(record.get("page_url") or "") not in saved_urls:
                        progress(index, len(rows), "Ranking cached Reels")
                        continue
                    candidates += 1
                    if reel_matches(record, filters):
                        matched.append(record)
                    progress(index, len(rows), "Ranking cached Reels")
                matched.sort(key=lambda item: float(item.get("viral_score") or 0), reverse=True)
                return matched[: filters.maximum_results], candidates, len(matched)
            finally:
                store.close()

        def show(payload):
            results, candidates, matched = payload
            page.set_results(results)
            page.set_scan_stats(candidates, matched, max(0, candidates - matched), len(results), "Saved Pages cache")
            if not results:
                QMessageBox.information(
                    self,
                    "Discover Trending Reels",
                    "No cached Reels found for Saved Pages. Use Scan by Page first.",
                )

        page.run_operation(load, show)

    MAX_SCAN_PAGES_PER_RUN = 10

    def _build_apify_reels_provider(self, token: str, **kwargs):
        try:
            return ApifyFacebookReelsProvider(token, **kwargs)
        except TypeError:
            return ApifyFacebookReelsProvider(token)

    def _scan_selected_pages(self, values: dict) -> None:
        """Scan the checked Pages with the ACTIVE provider (never depends on hidden data_source)."""
        page = self.viral_reels_page
        provider_name = active_viral_provider_name()
        token = get_apify_token()
        if not token:
            page.set_busy(False)
            QMessageBox.information(self, "Provider required", f"Configure the {provider_name} token first (Configure provider).")
            return
        selected = list(dict.fromkeys(values.get("selected_page_urls", [])))
        if len(selected) > self.MAX_SCAN_PAGES_PER_RUN:
            page.set_busy(False)
            QMessageBox.warning(
                self,
                "Scan Reels",
                f"To protect against excessive Apify costs, select at most {self.MAX_SCAN_PAGES_PER_RUN} Pages per scan (selected: {len(selected)}).",
            )
            return
        rows = {str(row["page_url"]): row for row in ViralReelsStore(self.database).list_pages(limit=100000)}
        pages = [PageCandidate(str(rows[u]["page_id"]), str(rows[u]["page_name"]), u, str(rows[u]["country"] or "Unknown"), str(rows[u]["language"] or "Unknown"), str(rows[u]["topic"] or "")) for u in selected if u in rows]
        if not pages:
            page.set_busy(False)
            QMessageBox.information(self, "Scan Reels", "Selected Pages were not found in the saved list.")
            return
        max_res = max(1, min(int(values.get("max_results", 30)), 100))
        actor_limit = int(getattr(ApifyFacebookReelsProvider, "MAX_RESULTS_LIMIT", 100))
        results_limit = min(max_res, actor_limit)
        filters = self._viral_filters(values)
        active_filters = ["Advanced ON" if values.get("advanced_enabled", True) else "Advanced OFF"]
        if filters.minimum_views is not None:
            active_filters.append(f"minimum views >= {filters.minimum_views:,}")
        if filters.country != "All":
            active_filters.append(f"country={filters.country}")
        if filters.language != "All":
            active_filters.append(f"language={filters.language}")
        if filters.topic:
            active_filters.append(f"topic={filters.topic}")
        if filters.published_from or filters.published_to:
            active_filters.append("date range")
        if provider_name == APIFY_PROVIDER_NAME:
            names = "\n".join(f"• {p.page_name}" for p in pages[:10]) + (f"\n… +{len(pages) - 10} more" if len(pages) > 10 else "")
            answer = QMessageBox.question(
                self,
                "Charge approval",
                f"This live Apify scan may incur charges.\n"
                f"Selected Pages: {len(pages)}\n"
                f"Collection limit: {results_limit} Reels/Page\n"
                f"Maximum collection: {results_limit * len(pages)} Reels\n"
                f"Display limit: {values.get('max_results', 30)}\n"
                f"Active filters: {', '.join(active_filters)}\n"
                f"Estimated cost: unavailable\n\n{names}\n\n"
                f"Actor resultsLimit: {results_limit} Reels per Page (max 100).\nContinue?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                page.set_busy(False)
                return
        def collect(cancel, progress):
            store = ViralReelsStore.for_thread(self.database.path)
            try:
                provider = self._build_apify_reels_provider(
                    token,
                    results_limit=results_limit,
                    store=store,
                    cancel_event=cancel,
                    progress_callback=progress,
                )
                results, state = ReelsCollectionService(store, provider).collect(pages, filters, cancel)
                total_collected = state.reels_collected + state.duplicates_skipped
                return results, list(state.messages), state.errors, total_collected
            finally:
                store.close()

        def show(payload):
            results, messages, errors, total_collected = payload
            deduped = list({str(r.get('reel_id') or r.get('reel_url')): r for r in results if reel_matches(r, filters)}.values())
            displayed = deduped[: filters.maximum_results]
            valid_count = total_collected or len(results)
            filtered_count = max(0, valid_count - len(displayed))
            reason = self._filter_rejection_reason(filters) if (valid_count > 0 and not displayed) else ""
            page.set_results(displayed)
            page.set_scan_stats(valid_count, valid_count, filtered_count, len(displayed), reason)
            if errors:
                QMessageBox.warning(self, "Scan Reels", f"{errors} error(s):\n" + "\n".join(messages[:5]))
            elif not displayed:
                reason = self._filter_rejection_reason(filters)
                msg = f"The provider returned no Reels matching the filters ({reason})." if reason else "The provider returned no Reels matching the filters."
                QMessageBox.information(self, "Scan Reels", msg)

        def on_cancelled():
            rows = ViralReelsStore(self.database).list_reels(filters.maximum_results * 10)
            matched = [dict(r) for r in rows if str(r["page_url"]) in selected and reel_matches(dict(r), filters)]
            if matched:
                page.set_results(list({str(r.get("reel_id") or r.get("reel_url")): r for r in matched[: filters.maximum_results]}.values()))

        if page.run_operation(collect, show) and page._worker is not None:
            page._worker.signals.cancelled.connect(on_cancelled)

    def stop_viral_operation(self) -> None:
        """Stop local worker tracking; only abort remote Apify Actor if user explicitly confirms."""
        self.viral_reels_page.cancel_operation()
        cfg = ViralReelsStore(self.database).provider_configuration("apify_reels_last_run")
        run_id = str(cfg.get("run_id") or "")
        status = str(cfg.get("status") or "")
        if run_id and status in {"READY", "RUNNING"}:
            answer = QMessageBox.question(
                self,
                "Stop Apify Run",
                f"Đã dừng theo dõi trên ứng dụng.\n\n"
                f"Apify Run {run_id} có thể vẫn đang chạy trên Apify Cloud.\n"
                f"Bạn có muốn gửi lệnh HỦY (Abort) Actor thật trên Apify luôn không?\n"
                f"(Chọn No để giữ Actor chạy tiếp trên Apify và khôi phục kết quả sau)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                token = get_apify_token()
                if token:
                    try:
                        self._build_apify_reels_provider(token, store=ViralReelsStore(self.database)).abort_run(run_id)
                    except Exception:
                        pass

    def recover_apify_reels_run(self, run_id_override: str | None = None) -> None:
        """Recover Reels from an existing Apify Run ID or Dataset without starting a new Actor run."""
        page = self.viral_reels_page
        token = get_apify_token()
        if not token:
            QMessageBox.information(self, "Provider required", "Configure the Apify token first (Configure provider).")
            return
        store_main = ViralReelsStore(self.database)
        saved_cfg = store_main.provider_configuration("apify_reels_last_run")
        default_run = str(saved_cfg.get("run_id") or "")
        if not isinstance(run_id_override, str):
            text, ok = QInputDialog.getText(
                self,
                "Khôi phục Apify Run",
                "Nhập Apify Run ID (hoặc để trống để lấy Run gần nhất từ Apify):",
                text=default_run,
            )
            if not ok:
                return
            target_run_id = text.strip()
        else:
            target_run_id = run_id_override.strip()

        values = page.filters()
        filters = self._viral_filters(values)

        def recover(cancel, progress):
            store = ViralReelsStore.for_thread(self.database.path)
            try:
                provider = self._build_apify_reels_provider(
                    token,
                    store=store,
                    cancel_event=cancel,
                    progress_callback=progress,
                )
                run_id = target_run_id
                if not run_id:
                    recent = provider.list_recent_runs(limit=1)
                    if not recent:
                        raise RuntimeError("Không tìm thấy Apify Run nào trước đó.")
                    run_id = str(recent[0].get("id") or "")
                progress(0, 1, f"Đang kiểm tra Apify Run {run_id}...")
                run_data = provider.poll_run_until_done(run_id)
                dataset_id = str(run_data.get("defaultDatasetId") or "")
                progress(0, 1, f"Đang tải Dataset {dataset_id} của Run {run_id}...")
                items = provider.fetch_dataset_items(dataset_id)
                pages_by_url = {
                    str(r["page_url"]): PageCandidate(
                        str(r["page_id"]),
                        str(r["page_name"]),
                        str(r["page_url"]),
                        str(r["country"] or "Unknown"),
                        str(r["language"] or "Unknown"),
                        str(r["topic"] or ""),
                    )
                    for r in store.list_pages(limit=100000)
                }
                fallback_page = next(iter(pages_by_url.values()), PageCandidate("recovered", "Recovered Page", "https://www.facebook.com/facebook", "Unknown", "Unknown", ""))
                raw_reels = provider.parse_reel_items(items, fallback_page, pages_by_url)
                results: list[dict[str, object]] = []
                from app.services.viral_reels_service import classify_language, viral_score
                for idx, raw in enumerate(raw_reels, 1):
                    if cancel.is_set():
                        break
                    if raw.page_url not in pages_by_url:
                        store.upsert_page(PageCandidate(raw.page_id, raw.page_name, raw.page_url, raw.country, raw.language, raw.topic))
                        pages_by_url[raw.page_url] = PageCandidate(raw.page_id, raw.page_name, raw.page_url, raw.country, raw.language, raw.topic)
                    language, confidence = classify_language(raw.caption)
                    record = {
                        **raw.__dict__,
                        "published_at": raw.published_at.isoformat() if raw.published_at else None,
                        "country": raw.country or "Unknown",
                        "language": raw.language or language,
                        "language_confidence": confidence,
                    }
                    store.upsert_reel(record)
                    store.record_metrics_snapshot(raw.reel_id, raw.views, raw.likes, raw.comments, raw.shares)
                    record["viral_score"] = viral_score(record, store.metrics_history(raw.reel_id))
                    store.update_viral_score(raw.reel_id, float(record["viral_score"]))
                    if reel_matches(record, filters):
                        results.append(record)
                    if idx % 25 == 0 or idx == len(raw_reels):
                        progress(idx, len(raw_reels), f"Đã lưu {idx}/{len(raw_reels)} Reels từ Run {run_id}")
                results.sort(key=lambda item: float(item["viral_score"]), reverse=True)
                return run_id, dataset_id, len(items), len(raw_reels), results[: filters.maximum_results]
            finally:
                store.close()

        def show_recovered(payload):
            run_id, dataset_id, raw_count, valid_count, results = payload
            self.load_viral_pages()
            deduped = list({str(r.get("reel_id") or r.get("reel_url")): r for r in results}.values())
            filtered_out = max(0, valid_count - len(deduped))
            reason = self._filter_rejection_reason(filters) if (valid_count > 0 and not deduped) else ""
            page.set_results(deduped)
            page.set_scan_stats(raw_count, valid_count, filtered_out, len(deduped), reason)
            QMessageBox.information(
                self,
                "Khôi phục Run thành công",
                f"Run ID: {run_id}\nDataset ID: {dataset_id}\nTổng item: {raw_count} · Reels hợp lệ (không trùng): {valid_count}\nHiển thị theo bộ lọc: {len(deduped)}",
            )

        page.run_operation(recover, show_recovered)

    def export_viral_reels_csv(self, records: list[dict]) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Viral Reels", "viral-reels.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            count = export_reels_csv(path, records)
        except OSError as error:
            QMessageBox.warning(self, "Export failed", f"Cannot write {path}: {error}")
            return
        QMessageBox.information(self, "Export CSV", f"Exported {count} Reels to {Path(path).name}")
    def export_viral_pages_csv(self, pages: list[dict]) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Facebook Pages", "facebook-pages.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            count = export_pages_csv(path, pages)
        except OSError as error:
            QMessageBox.warning(self, "Export failed", f"Cannot write {path}: {error}")
            return
        QMessageBox.information(self, "Export CSV", f"Exported {count} Pages to {Path(path).name}")
    def export_viral_reels_xlsx(self, records: list[dict]) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Viral Reels", "viral-reels.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            export_reels_xlsx(path, records)
        except RuntimeError as error:
            QMessageBox.warning(self, "Excel export unavailable", str(error))

    def show_matrices(self, project: Project) -> None:
        self.current_project = project
        self.current_matrix_id = None
        if self._refresh_matrices():
            self.pages.setCurrentIndex(3)
            self.sidebar.set_active("projects")

    def _refresh_matrices(self, matrix_id: str | None = None) -> bool:
        if self.current_project is None:
            return False
        if isinstance(matrix_id, bool):
            matrix_id = None
        if matrix_id:
            self.current_matrix_id = matrix_id
        try:
            project_id = self.current_project.uuid
            matrices = self.matrices.list_matrices(project_id)
            selected = next(
                (
                    item
                    for item in matrices
                    if item["matrix_id"] == self.current_matrix_id
                ),
                matrices[0] if matrices else None,
            )
            self.current_matrix_id = selected["matrix_id"] if selected else None
            if selected:
                matrix = self.matrices.get_matrix(
                    selected["matrix_id"], project_id
                )
                configuration = self.matrices.get_configuration(
                    selected["matrix_id"], project_id
                )
                pages = self.matrices.list_pages(selected["matrix_id"], project_id)
                entries = self.matrices.list_entries(
                    selected["matrix_id"], project_id
                )
                can_undo = self.matrices.can_undo(
                    selected["matrix_id"], project_id
                )
            else:
                matrix = configuration = None
                pages = entries = []
                can_undo = False
            videos = self.matrices.list_project_videos(project_id)
            self.matrix_page.set_data(
                self.current_project,
                matrices,
                matrix,
                configuration,
                pages,
                entries,
                videos,
                can_undo,
                self.current_matrix_id,
            )
            self.project_page.set_projects(self.projects.list_projects())
            return True
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.critical(self, "Lỗi ma trận", str(exc))
            return False

    def _create_matrix(self) -> None:
        if self.current_project is None:
            return
        next_name = f"Ma trận {len(self.matrix_page.matrices) + 1}"
        initial = {
            "name": next_name,
            "start_date": date.today().isoformat(),
        }
        dialog = MatrixConfigurationDialog(self, initial)
        if dialog.exec() != MatrixConfigurationDialog.DialogCode.Accepted:
            return
        configuration = dialog.configuration()
        try:
            matrix_id = self.matrices.create_matrix(
                self.current_project.uuid, **configuration
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể tạo ma trận", str(exc))
            return
        self._refresh_matrices(matrix_id)

    def _configure_matrix(self) -> None:
        if self.current_project is None or self.current_matrix_id is None:
            return
        try:
            configuration = self.matrices.get_configuration(
                self.current_matrix_id, self.current_project.uuid
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể đọc cấu hình", str(exc))
            return
        dialog = MatrixConfigurationDialog(self, configuration)
        if dialog.exec() != MatrixConfigurationDialog.DialogCode.Accepted:
            return
        answer = QMessageBox.question(
            self,
            "Sinh lại ma trận",
            "Lưu cấu hình sẽ sinh lại lịch. Các ô Đã đăng được giữ nguyên. Tiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.matrices.update_configuration(
                self.current_matrix_id,
                self.current_project.uuid,
                **dialog.configuration(),
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể cập nhật cấu hình", str(exc))
            return
        self._refresh_matrices(self.current_matrix_id)

    def _regenerate_matrix(self) -> None:
        if self.current_project is None or self.current_matrix_id is None:
            return
        answer = QMessageBox.question(
            self,
            "Sinh lại ma trận",
            "Sinh lại sẽ phân bổ video theo cấu hình. Các ô Đã đăng được giữ nguyên; "
            "bạn có thể Hoàn tác một lần. Tiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.matrices.regenerate(
                self.current_matrix_id, self.current_project.uuid
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể sinh lịch", str(exc))
            return
        self._refresh_matrices(self.current_matrix_id)

    def _undo_matrix(self) -> None:
        if self.current_project is None or self.current_matrix_id is None:
            return
        try:
            self.matrices.undo_regeneration(
                self.current_matrix_id, self.current_project.uuid
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể hoàn tác", str(exc))
            return
        self._refresh_matrices(self.current_matrix_id)

    def _rename_matrix(self) -> None:
        if self.current_project is None or self.current_matrix_id is None:
            return
        try:
            matrix = self.matrices.get_matrix(
                self.current_matrix_id, self.current_project.uuid
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể đọc ma trận", str(exc))
            return
        name, accepted = QInputDialog.getText(
            self, "Đổi tên ma trận", "Tên ma trận:", text=matrix["name"]
        )
        if not accepted:
            return
        try:
            self.matrices.rename_matrix(
                self.current_matrix_id, self.current_project.uuid, name
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể đổi tên", str(exc))
            return
        self._refresh_matrices(self.current_matrix_id)

    def _delete_matrix(self) -> None:
        if self.current_project is None or self.current_matrix_id is None:
            return
        answer = QMessageBox.question(
            self,
            "Xóa ma trận",
            "Xóa ma trận và lịch của nó? Video và file MP4 không bị xóa.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.matrices.delete_matrix(
                self.current_matrix_id, self.current_project.uuid
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể xóa ma trận", str(exc))
            return
        self.current_matrix_id = None
        self._refresh_matrices()

    def _edit_matrix_entry(self, entry: dict) -> None:
        if self.current_project is None or self.current_matrix_id is None:
            return
        dialog = MatrixEntryDialog(entry, self.matrix_page.videos, self)
        if dialog.exec() != MatrixEntryDialog.DialogCode.Accepted:
            return
        video_id, caption = dialog.values()
        try:
            self.matrices.update_entry(
                self.current_matrix_id,
                self.current_project.uuid,
                entry["entry_id"],
                video_id,
                caption,
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể cập nhật ô lịch", str(exc))
            return
        self._refresh_matrices(self.current_matrix_id)

    def _set_matrix_status(self, entry: dict, posted: bool) -> None:
        if self.current_project is None or self.current_matrix_id is None:
            return
        try:
            self.matrices.set_publish_status(
                self.current_matrix_id,
                self.current_project.uuid,
                entry["entry_id"],
                posted,
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể cập nhật trạng thái", str(exc))
            return
        self._refresh_matrices(self.current_matrix_id)

    def _set_video_facebook_uploaded(self, video_id: str, uploaded: bool) -> None:
        if self.current_project is None:
            return
        try:
            self.videos.set_facebook_uploaded(
                self.current_project.uuid, video_id, uploaded
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(
                self, "Không thể cập nhật trạng thái Facebook", str(exc)
            )
            return
        self._refresh_matrices(self.current_matrix_id)

    def _update_project_video_field(
        self, video_id: str, field: str, value: object
    ) -> None:
        if self.current_project is None:
            return
        try:
            self.videos.update_video_field(
                self.current_project.uuid, video_id, field, value
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể cập nhật video", str(exc))
        finally:
            self._refresh_matrices(self.current_matrix_id)

    def _choose_project_video_thumbnail(self, video_id: str) -> None:
        if self.current_project is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn ảnh thumbnail",
            "",
            "Tệp ảnh (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
        )
        if not path:
            return
        reader = QImageReader(path)
        if not reader.canRead():
            QMessageBox.warning(
                self, "Ảnh thumbnail không hợp lệ", reader.errorString()
            )
            return
        self._update_project_video_field(video_id, "thumbnail_path", path)

    def _export_matrix(self) -> None:
        if self.current_project is None or self.current_matrix_id is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Xuất lịch đăng CSV", "matrix-schedule.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            self.matrices.export_csv(
                self.current_matrix_id, self.current_project.uuid, path
            )
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể xuất CSV", str(exc))
            return
        QMessageBox.information(self, "Đã xuất CSV", f"Đã lưu lịch: {path}")

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
                    row["download_error"] = job["error_message"]
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
        if self.current_project is None:
            return
        start_sequence = self.database.next_video_sequence(self.current_project.uuid)
        self.detail_page.show_preview(path.name, len(rows), errors)
        dialog = CsvPreviewDialog(
            path.name,
            rows,
            errors,
            self.current_project.prefix,
            self,
            start_sequence=start_sequence,
        )
        if dialog.exec() == CsvPreviewDialog.DialogCode.Accepted:
            self.confirm_import()
        else:
            self.pending_csv_text = None
            self.pending_csv_rows = []
            self.pending_csv_errors = []
            self.detail_page.discard_preview()

    def import_google_sheets(self) -> None:
        if self.current_project is None:
            return
        dialog = GoogleSheetsImportDialog(self)
        if dialog.exec() != GoogleSheetsImportDialog.DialogCode.Accepted:
            return
        self.pending_csv_text = dialog.csv_text
        self.pending_csv_rows = list(dialog.rows)
        self.pending_csv_errors = []
        self.confirm_import()

    def import_manual_videos(self) -> None:
        if self.current_project is None:
            return
        previous_rows: list[dict[str, object]] | None = None
        while True:
            dialog = ManualVideoImportDialog(self, rows=previous_rows)
            if dialog.exec() != ManualVideoImportDialog.DialogCode.Accepted:
                return
            self.pending_csv_text = dialog.csv_text
            self.pending_csv_rows = list(dialog.rows)
            self.pending_csv_errors = []
            self.confirm_import()
            if self.pending_csv_text is None:
                return
            previous_rows = list(dialog.rows)

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
        try:
            imported = self.videos.import_csv(
                project_id,
                self.pending_csv_text,
                append=True,
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
            self, "Thành công", f"Đã thêm thành công {imported} video vào dự án."
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
        self.detail_page.run_now_button.set_loading(True)
        self.start_queue()

    def start_queue(self) -> None:
        self.queue_running = True
        self._set_queue_loading(True)
        self.pause_after_current = False
        self.queue_page.queue_status.setText(
            f"Đang chạy hàng đợi ({self.queue_concurrency} luồng song song)."
        )
        self.detail_page.queue_status.setText("Đang chạy hàng đợi.")
        self._start_next_queue_job()

    def pause_queue(self) -> None:
        if not self.queue_workers:
            self.queue_running = False
            self._set_queue_loading(False)
            self.queue_page.queue_status.setText("Hàng đợi đang tạm dừng.")
            self.detail_page.queue_status.setText("Hàng đợi đang tạm dừng.")
            return
        self.pause_after_current = True
        self.queue_page.queue_status.setText(
            "Sẽ tạm dừng sau khi các video đang tải hoàn tất."
        )
        self.detail_page.queue_status.setText("Tạm dừng sau video hiện tại.")

    def _start_next_queue_job(self) -> None:
        """Fill every free download slot with the highest-priority queued job."""
        if not self.queue_running:
            return
        if self.pause_after_current:
            if not self.queue_workers:
                self.queue_running = False
                self._set_queue_loading(False)
            return
        while len(self.queue_workers) < self.queue_concurrency:
            try:
                job = self.download_queue.next_queued_job(
                    exclude_job_ids=tuple(self.queue_workers),
                    exclude_video_ids=tuple(self._queue_video_ids.values()),
                )
            except sqlite3.Error as exc:
                self.queue_running = False
                self._set_queue_loading(False)
                QMessageBox.critical(self, "Lỗi hàng đợi", str(exc))
                return
            if job is None:
                if not self.queue_workers:
                    self.queue_running = False
                    self._set_queue_loading(False)
                    self.queue_page.queue_status.setText("Không còn job đang chờ.")
                    self.detail_page.queue_status.setText("Hàng đợi đã hoàn tất.")
                    self.refresh_queue()
                break
            self._launch_queue_worker(job)
        self._update_queue_stats()

    def _launch_queue_worker(self, job) -> None:
        job_id = job["job_id"]
        worker = DownloadWorker(self.database.path, job_id, self)
        self.queue_workers[job_id] = worker
        try:
            self._queue_video_ids[job_id] = job["video_id"]
        except (IndexError, KeyError):
            pass
        self.queue_error = None
        worker.progress.connect(self._queue_progress)
        worker.jobFinished.connect(self._queue_job_finished)
        worker.finished.connect(
            lambda active_worker=worker: self._queue_thread_finished(active_worker)
        )
        code = job_id
        job_info = self.download_queue.get_job(job_id)
        if job_info is not None:
            video = self.database.connection.execute(
                "SELECT video_code FROM videos WHERE id=?", (job_info["video_id"],)
            ).fetchone()
            if video is not None:
                code = video["video_code"]
        self._queue_codes[job_id] = code
        self.queue_page.queue_status.setText(f"Đang tải {code}…")
        self.detail_page.queue_status.setText(f"Đang tải {code}…")
        worker.start()

    def _release_queue_job(self, job_id: str) -> None:
        self.queue_workers.pop(job_id, None)
        self._queue_video_ids.pop(job_id, None)
        self._queue_speeds.pop(job_id, None)
        self._queue_ui_stamp.pop(job_id, None)
        self._queue_codes.pop(job_id, None)

    def _set_queue_loading(self, loading: bool) -> None:
        self.queue_page.set_queue_loading(loading)
        self.detail_page.set_queue_activity(loading, title="Đang tải video" if loading else "Hàng đợi sẵn sàng")
        if not loading:
            self.detail_page.run_now_button.set_loading(False)

    def _queue_progress(
        self,
        job_id: str,
        percent: float,
        downloaded: int,
        total: int | None,
        speed: float | None,
        eta: int | None,
    ) -> None:
        if job_id not in self.queue_workers:
            return
        self._queue_speeds[job_id] = float(speed or 0.0)
        now = time.monotonic()
        last = self._queue_ui_stamp.get(job_id, 0.0)
        if percent < 100 and now - last < self.QUEUE_PROGRESS_UI_INTERVAL:
            return
        self._queue_ui_stamp[job_id] = now
        self.queue_page.table.queue_model.update_progress(
            job_id, percent, downloaded, total, speed, eta
        )
        code = self._queue_codes.get(job_id, job_id)
        rate = f"{speed / 1024 / 1024:.1f} MB/s" if speed else "—"
        remaining = f" · còn {eta}s" if eta is not None else ""
        status = f"Đang tải {code}: {percent:.1f}% · {rate}{remaining}"
        self.queue_page.queue_status.setText(status)
        self.detail_page.queue_status.setText(status)
        self.detail_page.set_queue_progress(percent)
        self._update_queue_stats()

    def _run_pending_deletions(self) -> bool:
        """Run deferred deletions once the cancelled workers have fully stopped."""
        if self._pending_wait_ids & set(self.queue_workers):
            return False
        if self.pending_project_delete is not None:
            project_id, delete_files = self.pending_project_delete
            self.pending_project_delete = None
            self._pending_wait_ids.clear()
            self.queue_running = False
            self._set_queue_loading(False)
            self.setEnabled(True)
            self._perform_project_deletion(project_id, delete_files)
            return True
        if self.pending_queue_delete is not None:
            job_ids, description = self.pending_queue_delete
            self.pending_queue_delete = None
            self._pending_wait_ids.clear()
            self.queue_running = False
            self._set_queue_loading(False)
            self.setEnabled(True)
            self._perform_queue_job_deletion(job_ids, description)
            return True
        return False

    def _queue_job_finished(self, job_id: str, succeeded: bool, message: str) -> None:
        worker = self.queue_workers.get(job_id)
        if worker is None:
            return
        worker.wait()
        self._release_queue_job(job_id)
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
        self._update_queue_stats()
        if self._run_pending_deletions():
            return
        if self.pending_project_delete is not None or self.pending_queue_delete is not None:
            return
        if not succeeded:
            status = f"Job lỗi: {message}"
        else:
            status = "Video tải thành công."
        self.queue_page.queue_status.setText(status)
        self.detail_page.queue_status.setText(status)
        if self.pause_after_current:
            if not self.queue_workers:
                self.pause_after_current = False
                self.queue_running = False
                self._set_queue_loading(False)
                self.queue_page.queue_status.setText(
                    "Đã tạm dừng sau video hiện tại."
                )
            return
        if can_continue:
            QTimer.singleShot(0, self._start_next_queue_job)
        elif not self.queue_workers:
            self.queue_running = False
            self._set_queue_loading(False)

    def _queue_thread_finished(self, worker: DownloadWorker) -> None:
        job_id = worker.job_id
        if self.queue_workers.get(job_id) is not worker:
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
        self._release_queue_job(job_id)
        self.refresh_queue()
        self._refresh_project_videos()
        self._update_queue_stats()
        if self._run_pending_deletions():
            return
        if self.pending_project_delete is not None or self.pending_queue_delete is not None:
            return
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
            f"Đã đưa {len(created)} job vào hàng đợi. "
            "Bấm Bắt đầu hàng đợi để chạy."
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

    def delete_selected_queue_jobs(self) -> None:
        selected = self._selected_queue_job_ids()
        if selected:
            self._request_queue_job_deletion(selected, "job đã chọn")

    def delete_failed_queue_jobs(self) -> None:
        self._delete_queue_jobs_with_statuses(
            ("failed", "interrupted"), "job thất bại/gián đoạn"
        )

    def delete_completed_queue_jobs(self) -> None:
        self._delete_queue_jobs_with_statuses(
            ("completed",), "lịch sử hoàn tất"
        )

    def delete_inactive_queue_jobs(self) -> None:
        self._delete_queue_jobs_with_statuses(
            ("completed", "failed", "interrupted", "cancelled"),
            "job không hoạt động",
        )

    def _delete_queue_jobs_with_statuses(
        self, statuses: tuple[str, ...], description: str
    ) -> None:
        try:
            job_ids = self.download_queue.job_ids_with_statuses(statuses)
        except sqlite3.Error as exc:
            QMessageBox.warning(self, "Không thể đọc hàng đợi", str(exc))
            return
        self._request_queue_job_deletion(job_ids, description)

    def _request_queue_job_deletion(
        self, job_ids: list[str], description: str
    ) -> None:
        job_ids = list(dict.fromkeys(job_ids))
        if not job_ids:
            self.queue_page.queue_status.setText(
                f"Không có {description} cần xóa."
            )
            return
        try:
            jobs = [
                job
                for job_id in job_ids
                if (job := self.download_queue.get_job(job_id)) is not None
            ]
        except sqlite3.Error as exc:
            QMessageBox.warning(self, "Không thể đọc hàng đợi", str(exc))
            return
        if not jobs:
            self.refresh_queue()
            return
        counts = {}
        for job in jobs:
            counts[job["status"]] = counts.get(job["status"], 0) + 1
        details = ", ".join(
            f"{count} {status}" for status, count in sorted(counts.items())
        )
        message = f"Xóa {len(jobs)} {description} khỏi lịch sử?\n({details})"
        active = [job for job in jobs if job["status"] == "downloading"]
        if active:
            message += (
                "\nJob đang tải sẽ được hủy và chờ worker dừng trước khi xóa."
            )
        answer = QMessageBox.question(
            self,
            "Xác nhận xóa job",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if active:
            if any(row["job_id"] not in self.queue_workers for row in active):
                QMessageBox.warning(
                    self,
                    "Không thể dừng tải",
                    "Không tìm thấy worker tương ứng; không có job nào bị xóa.",
                )
                return
            self.pending_queue_delete = (job_ids, description)
            self._pending_wait_ids = {row["job_id"] for row in active}
            self.queue_running = False
            self.pause_after_current = False
            self.setEnabled(False)
            self.queue_page.queue_status.setText(
                "Đang dừng worker trước khi xóa job…"
            )
            for row in active:
                self.queue_workers[row["job_id"]].request_cancel()
            return
        self._perform_queue_job_deletion(job_ids, description)

    def _perform_queue_job_deletion(
        self, job_ids: list[str], description: str
    ) -> None:
        try:
            deleted = self.download_queue.delete_jobs(job_ids)
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Không thể xóa job", str(exc))
            return
        self.refresh_queue()
        if self.current_project is not None:
            self._refresh_project_videos()
        self.queue_page.queue_status.setText(
            f"Đã xóa {deleted} {description}. Tệp video không bị xóa."
        )

    def closeEvent(self, event) -> None:
        self.viral_reels_scheduler.stop()
        if self._facebook_publish_worker is not None and self._facebook_publish_worker.isRunning():
            QMessageBox.information(
                self,
                "Đang đăng Facebook",
                "Video đang được upload. Hãy chờ worker kết thúc để giữ trạng thái an toàn.",
            )
            event.ignore()
            return
        if self.queue_workers:
            QMessageBox.information(
                self,
                "Đang tải",
                "Đợi các video hiện tại hoàn tất trước khi đóng ứng dụng.",
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
    application.setOrganizationName("VideoDownloader")
    application.setApplicationName("VideoDownloaderFacebook")
    style_path = Path(__file__).with_name("styles.qss")
    application.setStyleSheet(style_path.read_text(encoding="utf-8"))
    database = Database(DB_PATH)
    try:
        window = MainWindow(database)
        window.show()
        return application.exec()
    finally:
        database.close()

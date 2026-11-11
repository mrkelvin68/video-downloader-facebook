from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QRadioButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from app.services.logging_service import (
    SETTINGS_APPLICATION,
    SETTINGS_ORGANIZATION,
    get_default_log_dir,
    logging_service,
    validate_log_dir_writable,
)

PLATFORMS = ("youtube", "facebook", "instagram")
PLATFORM_LABELS = {"youtube": "YouTube", "facebook": "Facebook", "instagram": "Instagram"}


def _cookie_file_is_valid(path: str) -> bool:
    candidate = Path(path)
    if not candidate.is_file() or candidate.stat().st_size <= 0:
        return False
    try:
        sample = candidate.read_text(encoding="utf-8", errors="ignore")[:4096]
    except OSError:
        return False
    return "netscape http cookie file" in sample.casefold() or any(
        len(line.split("\t")) >= 7 for line in sample.splitlines() if line and not line.startswith("#")
    )


def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


class SettingsDialog(QDialog):
    """Centralized Settings dialog with Authentication and Log Management tabs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cài đặt hệ thống")
        self.setMinimumWidth(680)
        self.settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)

        self.auth_fields: dict[str, tuple[QRadioButton, QRadioButton, QLineEdit, QLabel]] = {}

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(14)
        main_layout.setContentsMargins(18, 16, 18, 16)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_auth_tab(), "Xác thực tải video")
        self.tabs.addTab(self._build_log_management_tab(), "Quản lý Log")
        main_layout.addWidget(self.tabs)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        main_layout.addWidget(self.buttons)

        self._refresh_dir_size()

    # --- TAB 1: Authentication ---
    def _build_auth_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(10)

        header_label = QLabel(
            "<b>Xác thực phiên tải video (Cookies)</b><br>"
            "<span style='color: #94a3b8;'>Video public vẫn tải bình thường không cần đăng nhập. "
            "Chỉ chọn file cookies do bạn chủ động cung cấp; ứng dụng không tự ý truy cập trình duyệt.</span>"
        )
        header_label.setWordWrap(True)
        layout.addWidget(header_label)

        sub_tabs = QTabWidget()
        for platform in PLATFORMS:
            sub_tabs.addTab(self._build_platform_page(platform), PLATFORM_LABELS[platform])
        layout.addWidget(sub_tabs)
        layout.addStretch()
        return container

    def _build_platform_page(self, platform: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)

        configured = str(self.settings.value(f"{platform}/auth/cookies_file", "", type=str) or "")
        none_radio = QRadioButton("Không đăng nhập (mặc định)")
        file_radio = QRadioButton(f"Sử dụng file phiên {PLATFORM_LABELS[platform]} do người dùng chọn")

        path_edit = QLineEdit(configured)
        path_edit.setPlaceholderText("Đường dẫn file cookies Netscape (.txt)")
        browse_btn = QPushButton("Chọn file")
        clear_btn = QPushButton("Xóa cấu hình")

        row = QHBoxLayout()
        row.addWidget(path_edit, 1)
        row.addWidget(browse_btn)
        row.addWidget(clear_btn)

        status = QLabel()
        layout.addWidget(none_radio)
        layout.addWidget(file_radio)
        layout.addLayout(row)
        layout.addWidget(status)

        none_radio.setChecked(not configured)
        file_radio.setChecked(bool(configured))

        browse_btn.clicked.connect(lambda: self._browse_cookie(platform, path_edit, file_radio))
        clear_btn.clicked.connect(lambda: self._clear_cookie(path_edit, none_radio))

        self.auth_fields[platform] = (none_radio, file_radio, path_edit, status)
        path_edit.textChanged.connect(lambda: self._refresh_platform_status(platform))
        self._refresh_platform_status(platform)
        return page

    def _browse_cookie(self, platform: str, edit: QLineEdit, radio: QRadioButton) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Chọn file cookies {PLATFORM_LABELS[platform]}",
            "",
            "Cookies (*.txt);;All files (*)",
        )
        if path:
            radio.setChecked(True)
            edit.setText(path)

    def _clear_cookie(self, edit: QLineEdit, none_radio: QRadioButton) -> None:
        none_radio.setChecked(True)
        edit.clear()

    def _refresh_platform_status(self, platform: str) -> None:
        _, file_radio, edit, status = self.auth_fields[platform]
        path = edit.text().strip()
        if file_radio.isChecked() and path:
            if _cookie_file_is_valid(path):
                status.setText("<span style='color: #10b981;'>Trạng thái: Đã cấu hình phiên hợp lệ.</span>")
            else:
                status.setText("<span style='color: #ef4444;'>Trạng thái: File không tồn tại hoặc sai định dạng Netscape.</span>")
        else:
            status.setText("<span style='color: #94a3b8;'>Trạng thái: Không đăng nhập.</span>")

    # --- TAB 2: Log Management ---
    def _build_log_management_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(14)

        # 1. Config Group
        cfg_group = QGroupBox("Cấu hình Log")
        cfg_layout = QVBoxLayout(cfg_group)
        cfg_layout.setSpacing(8)

        self.log_enabled_cb = QCheckBox("Bật ghi log chẩn đoán (Diagnostic Logging)")
        self.log_enabled_cb.setChecked(logging_service.config.enabled)
        cfg_layout.addWidget(self.log_enabled_cb)

        note_label = QLabel(
            "<span style='color: #94a3b8; font-size: 11px;'>"
            "Ghi chú: Lỗi nghiêm trọng và cảnh báo hệ thống luôn được bảo toàn và ghi vào error.log. "
            "Thông tin nhạy cảm (token, mật khẩu, cookie) được tự động ẩn trước khi ghi.</span>"
        )
        note_label.setWordWrap(True)
        cfg_layout.addWidget(note_label)

        level_row = QHBoxLayout()
        level_label = QLabel("Cấp độ log (Log Level):")
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        current_level = logging_service.config.level.upper()
        idx = self.log_level_combo.findText(current_level)
        if idx >= 0:
            self.log_level_combo.setCurrentIndex(idx)
        level_row.addWidget(level_label)
        level_row.addWidget(self.log_level_combo)
        level_row.addStretch()
        cfg_layout.addLayout(level_row)

        layout.addWidget(cfg_group)

        # 2. Directory Group
        dir_group = QGroupBox("Thư mục lưu trữ Log")
        dir_layout = QVBoxLayout(dir_group)
        dir_layout.setSpacing(8)

        self.log_dir_edit = QLineEdit(str(logging_service.get_log_dir()))
        self.log_dir_edit.setReadOnly(True)
        dir_layout.addWidget(self.log_dir_edit)

        dir_btns = QHBoxLayout()
        open_dir_btn = QPushButton("Mở thư mục Log")
        open_dir_btn.clicked.connect(self._open_log_folder)
        dir_btns.addWidget(open_dir_btn)

        change_dir_btn = QPushButton("Đổi thư mục...")
        change_dir_btn.clicked.connect(self._change_log_folder)
        dir_btns.addWidget(change_dir_btn)

        reset_dir_btn = QPushButton("Đặt lại mặc định")
        reset_dir_btn.clicked.connect(self._reset_log_folder)
        dir_btns.addWidget(reset_dir_btn)

        dir_btns.addStretch()
        dir_layout.addLayout(dir_btns)
        layout.addWidget(dir_group)

        # 3. Policy & Maintenance Group
        maint_group = QGroupBox("Chính sách lưu trữ & Bảo trì")
        maint_layout = QVBoxLayout(maint_group)
        maint_layout.setSpacing(8)

        policy_row = QHBoxLayout()
        policy_row.addWidget(QLabel("Dung lượng tối đa mỗi file:"))
        self.max_size_spin = QSpinBox()
        self.max_size_spin.setRange(1, 100)
        self.max_size_spin.setSuffix(" MB")
        self.max_size_spin.setValue(max(1, logging_service.config.max_bytes // (1024 * 1024)))
        policy_row.addWidget(self.max_size_spin)

        policy_row.addSpacing(20)
        policy_row.addWidget(QLabel("Thời gian lưu giữ log:"))
        self.retention_spin = QSpinBox()
        self.retention_spin.setRange(1, 365)
        self.retention_spin.setSuffix(" ngày")
        self.retention_spin.setValue(logging_service.config.retention_days)
        policy_row.addWidget(self.retention_spin)
        policy_row.addStretch()
        maint_layout.addLayout(policy_row)

        self.dir_size_label = QLabel("Đang kiểm tra dung lượng log...")
        maint_layout.addWidget(self.dir_size_label)

        action_row = QHBoxLayout()
        export_zip_btn = QPushButton("Xuất Log ZIP...")
        export_zip_btn.clicked.connect(self._export_zip)
        action_row.addWidget(export_zip_btn)

        clean_btn = QPushButton("Xóa Log cũ")
        clean_btn.clicked.connect(self._cleanup_logs)
        action_row.addWidget(clean_btn)

        action_row.addStretch()
        maint_layout.addLayout(action_row)

        layout.addWidget(maint_group)
        layout.addStretch()
        return container

    def _open_log_folder(self) -> None:
        log_dir = logging_service.get_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir)))

    def _change_log_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Chọn thư mục lưu trữ Log mới", str(logging_service.get_log_dir())
        )
        if not selected:
            return
        new_path = Path(selected).resolve()
        is_writable, err = validate_log_dir_writable(new_path)
        if not is_writable:
            QMessageBox.warning(
                self,
                "Không có quyền ghi",
                f"Không thể ghi vào thư mục đã chọn: {err}\nHệ thống sẽ giữ nguyên thư mục hiện tại.",
            )
            return

        ok, err_set = logging_service.set_log_dir(new_path)
        if ok:
            self.log_dir_edit.setText(str(new_path))
            self._refresh_dir_size()
            QMessageBox.information(
                self, "Đổi thư mục", f"Đã chuyển thư mục lưu trữ log sang:\n{new_path}"
            )
        else:
            QMessageBox.warning(self, "Lỗi", f"Không thể đổi thư mục log: {err_set}")

    def _reset_log_folder(self) -> None:
        default_dir = get_default_log_dir()
        ok, _ = logging_service.set_log_dir(default_dir)
        if ok:
            self.log_dir_edit.setText(str(default_dir))
            self._refresh_dir_size()

    def _refresh_dir_size(self) -> None:
        total_bytes, count = logging_service.get_directory_size_bytes()
        formatted = format_bytes(total_bytes)
        self.dir_size_label.setText(
            f"<b>Dung lượng log hiện tại:</b> {formatted} ({count} tệp)"
        )

    def _export_zip(self) -> None:
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        suggested_name = f"vdf_logs_{now_str}.zip"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Xuất Log ZIP đã kiểm duyệt bảo mật",
            suggested_name,
            "ZIP Files (*.zip)",
        )
        if not file_path:
            return
        try:
            exported_count = logging_service.export_logs_zip(Path(file_path))
            self._refresh_dir_size()
            QMessageBox.information(
                self,
                "Xuất Log thành công",
                f"Đã đóng gói {exported_count} file log vào:\n{file_path}\n"
                "(Mọi thông tin nhạy cảm đã được loại bỏ an toàn.)",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Lỗi xuất file", f"Không thể xuất file ZIP: {exc}")

    def _cleanup_logs(self) -> None:
        reply = QMessageBox.question(
            self,
            "Xác nhận xóa Log cũ",
            "Bạn có chắc chắn muốn dọn dẹp các tệp log luân phiên cũ và file nén xuất trước đó không?\n"
            "(Các file log đang hoạt động sẽ được giữ nguyên.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            deleted = logging_service.clean_old_logs(max_days=0)
            self._refresh_dir_size()
            QMessageBox.information(
                self,
                "Dọn dẹp hoàn tất",
                f"Đã dọn dẹp {deleted} tệp log cũ.",
            )

    def _save(self) -> None:
        # Validate authentication cookies
        for platform, (_, file_radio, edit, _) in self.auth_fields.items():
            path = edit.text().strip() if file_radio.isChecked() else ""
            if path and not _cookie_file_is_valid(path):
                QMessageBox.warning(
                    self,
                    PLATFORM_LABELS[platform],
                    "File phiên không tồn tại hoặc không đúng định dạng Netscape.",
                )
                return
            key = f"{platform}/auth/cookies_file"
            if path:
                self.settings.setValue(key, path)
            else:
                self.settings.remove(key)

        # Apply Logging Settings
        logging_service.set_enabled(self.log_enabled_cb.isChecked())
        logging_service.set_level(self.log_level_combo.currentText())
        logging_service.set_max_size_mb(self.max_size_spin.value())
        logging_service.set_retention_days(self.retention_spin.value())

        self.settings.sync()
        self.accept()


# Alias for backwards compatibility
YouTubeAuthDialog = SettingsDialog

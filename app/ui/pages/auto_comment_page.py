from __future__ import annotations
from datetime import date
from pathlib import Path
from PySide6.QtCore import QEvent, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from app.services.auto_comment_service import AutoCommentService, AutoCommentValidationError, CommentItem, parse_comment_csv, select_comments
from app.database.connection import Database
from app.ui.facebook_worker import FacebookRequestWorker
from app.ui.widgets.loading_button import LoadingButton


class TableLoadingOverlay(QWidget):
    """Semi-transparent loading overlay with rotating cyan spinner and status text."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._angle = 0
        self._text = "Đang đồng bộ dữ liệu..."
        self._timer = QTimer(self)
        self._timer.setInterval(25)  # 40 fps
        self._timer.timeout.connect(self._rotate)
        self.hide()

    def _rotate(self) -> None:
        self._angle = (self._angle + 6) % 360
        self.update()

    def start(self, text: str = "Đang đồng bộ dữ liệu...") -> None:
        self._text = text
        if self.parentWidget():
            self.resize(self.parentWidget().size())
            self.raise_()
        if not self._timer.isActive():
            self._timer.start()
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dark semi-transparent backdrop
        painter.fillRect(self.rect(), QColor(11, 17, 32, 195))

        center_x = self.width() / 2.0
        center_y = self.height() / 2.0 - 15.0

        # Draw rotating spinner arc
        spinner_radius = 22.0
        painter.save()
        painter.translate(center_x, center_y)
        painter.rotate(self._angle)

        pen = QPen(QColor("#38bdf8"), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        arc_rect = QRectF(-spinner_radius, -spinner_radius, spinner_radius * 2.0, spinner_radius * 2.0)
        painter.drawArc(arc_rect, 0, 270 * 16)
        painter.restore()

        # Draw label text underneath
        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        painter.setPen(QColor("#e2e8f0"))
        text_rect = QRectF(0, center_y + spinner_radius + 12, self.width(), 30)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self._text)
        painter.end()


class AutoCommentPage(QWidget):
    pageManagementRequested = Signal()
    def __init__(self, database, parent=None):
        super().__init__(parent); self.database=database; self.service=AutoCommentService(database); self.library_id=None; self.campaign_id=None; self.posts=[]; self.selected_post_ids=set(); self.preview_comments={}; self._posts_worker=None; self._preview_busy=False; self.setAcceptDrops(True)
        root=QVBoxLayout(self); root.setContentsMargins(24,18,24,18); root.addWidget(QLabel("Auto Comment", objectName="title")); root.addWidget(QLabel("Lập kế hoạch comment cục bộ. Preview và lưu campaign không đăng comment lên Facebook."))
        self.workflow_progress=QLabel();self.workflow_progress.setObjectName("autoCommentProgress");root.addWidget(self.workflow_progress)
        self.workflow_help=QLabel();self.workflow_help.setWordWrap(True);root.addWidget(self.workflow_help)
        self.workflow_message=QLabel();self.workflow_message.setWordWrap(True);root.addWidget(self.workflow_message)
        self.tabs=QTabWidget(); self.tabs.addTab(self._posts(),"Bước 1"); self.tabs.addTab(self._library(),"Bước 2"); self.tabs.addTab(self._campaign(),"Bước 3");self.tabs.tabBar().hide();self.tabs.currentChanged.connect(self._workflow_step_changed); root.addWidget(self.tabs,1)
        navigation=QHBoxLayout();self.back_button=QPushButton("← Quay lại");self.back_button.clicked.connect(lambda:self._go_to_step(self.tabs.currentIndex()-1));self.next_button=QPushButton("Tiếp theo →");self.next_button.setObjectName("primary");self.next_button.clicked.connect(self._next_step);navigation.addWidget(self.back_button);navigation.addStretch();navigation.addWidget(self.next_button);root.addLayout(navigation); self.refresh_pages(); self.refresh_libraries();self._workflow_step_changed(0)
    def _posts(self):
        w=QWidget(); l=QVBoxLayout(w); box=QGroupBox("Fanpage và bộ lọc"); f=QFormLayout(box); self.page=QComboBox(); self.page.currentIndexChanged.connect(self._page_changed); manage=QPushButton("Quản lý Fanpage"); manage.clicked.connect(self.pageManagementRequested.emit); pr=QHBoxLayout(); pr.addWidget(self.page,1); pr.addWidget(manage)
        self.start_date,self.end_date=QDateEdit(),QDateEdit()
        for x in (self.start_date,self.end_date): x.setCalendarPopup(True); x.setDisplayFormat("yyyy-MM-dd"); x.dateChanged.connect(self._invalidate)
        self.start_date.setDate(date.today().replace(day=1)); self.end_date.setDate(date.today()); self.content_type=QComboBox(); self.content_type.addItems(["Tất cả","Reels","Bài viết thường"]); self.content_type.currentIndexChanged.connect(self._invalidate); self.load_posts_button=LoadingButton("Tải bài viết", loading_text="⌛ Đang tải bài viết..."); self.load_posts_button.setObjectName("primary"); self.load_posts_button.clicked.connect(self.load_posts)
        f.addRow("Fanpage",pr); f.addRow("Từ ngày",self.start_date); f.addRow("Đến ngày",self.end_date); f.addRow("Loại",self.content_type); f.addRow(self.load_posts_button); l.addWidget(box)

        self.loading_container = QWidget()
        loading_layout = QVBoxLayout(self.loading_container)
        loading_layout.setContentsMargins(0, 4, 0, 4)
        loading_layout.setSpacing(4)
        self.loading_notice = QLabel("Đang tải bài viết từ Facebook... (Vui lòng đợi giây lát) ◌")
        self.loading_notice.setStyleSheet("color: #38bdf8; font-size: 12px; font-weight: 500;")
        self.loading_bar = QProgressBar()
        self.loading_bar.setFixedHeight(3)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setStyleSheet(
            "QProgressBar { background: #16263d; border: 0; border-radius: 1.5px; }"
            "QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #06b6d4, stop:1 #3b82f6); border-radius: 1.5px; }"
        )
        loading_layout.addWidget(self.loading_notice)
        loading_layout.addWidget(self.loading_bar)
        self.loading_container.hide()
        l.addWidget(self.loading_container)

        r=QHBoxLayout(); a=QPushButton("Chọn tất cả"); a.clicked.connect(lambda:self._all_posts(True)); b=QPushButton("Bỏ chọn tất cả"); b.clicked.connect(lambda:self._all_posts(False)); self.post_status=QLabel("Chưa tải bài viết."); r.addWidget(a);r.addWidget(b);r.addWidget(self.post_status,1);l.addLayout(r)
        self.posts_table=QTableWidget(0,5);self.posts_table.setHorizontalHeaderLabels(["Chọn","Published At","Loại","ID nguồn","Trạng thái"]);self.posts_table.horizontalHeader().setStretchLastSection(True);self.posts_table.setColumnWidth(0,54);self.posts_table.itemChanged.connect(self._post_changed);l.addWidget(self.posts_table,1);l.addWidget(QLabel("Chỉ đọc dữ liệu Facebook; Phase 1 không gửi comment."))
        self.table_overlay = TableLoadingOverlay(self.posts_table)
        self.posts_table.installEventFilter(self)
        return w
    def _library(self):
        w=QWidget();l=QVBoxLayout(w);r=QHBoxLayout();self.library_picker=QComboBox();self.library_picker.currentIndexChanged.connect(self.open_library);self.library_name=QLineEdit("Thư viện comment");self.search=QLineEdit();self.search.setPlaceholderText("Tìm comment…");self.search.textChanged.connect(self._filter)
        for x in (self.library_picker,self.library_name,self.search):r.addWidget(x,1)
        l.addLayout(r);r=QHBoxLayout()
        for text,fn in (("Import CSV",self.import_csv),("Thêm dòng",self.add_row),("Xóa đã chọn",self.delete_rows),("Lưu thư viện",self.save_library),("Xóa import",self.clear_rows)):
            x=QPushButton(text);x.clicked.connect(fn);r.addWidget(x)
        self.csv_status=QLabel();r.addWidget(self.csv_status,1);l.addLayout(r);self.comments=QTableWidget(0,3);self.comments.setHorizontalHeaderLabels(["Bật","ID","Comment"]);self.comments.setWordWrap(True);self.comments.horizontalHeader().setStretchLastSection(True);self.comments.setColumnWidth(0,52);self.comments.setColumnWidth(1,92);self.comments.itemChanged.connect(self._changed);l.addWidget(self.comments,1);l.addWidget(QLabel("Kéo-thả CSV vào trang này. CSV gốc không bị thay đổi."));return w
    def _campaign(self):
        w=QWidget();l=QVBoxLayout(w);box=QGroupBox("Cấu hình chiến dịch");f=QFormLayout(box);self.campaign_name=QLineEdit("Auto Comment draft");self.summary=QLabel();self.one=QRadioButton("Random 1 comment mỗi bài");self.three=QRadioButton("Random 3 comment khác nhau mỗi bài");self.one.setChecked(True);self.random_mode=QRadioButton("Ngẫu nhiên");self.sequential_mode=QRadioButton("Tuần tự");self.random_mode.setChecked(True);self.prevent_duplicates=QCheckBox("Không lặp nội dung trong cùng bài");self.prevent_duplicates.setChecked(True);r=QHBoxLayout()
        for x in (self.one,self.three,self.random_mode,self.sequential_mode,self.prevent_duplicates):r.addWidget(x);x.toggled.connect(self._invalidate)
        self.campaign_picker=QComboBox();self.campaign_picker.currentIndexChanged.connect(self.open_campaign);f.addRow("Tên",self.campaign_name);f.addRow("Thông tin",self.summary);f.addRow("Chọn comment",r);f.addRow("Mở campaign",self.campaign_picker);l.addWidget(box)
        r=QHBoxLayout();self.preview_button=LoadingButton("Xem trước comment");self.preview_button.clicked.connect(self.generate_preview);r.addWidget(self.preview_button)
        for text,fn in (("Tạo lại preview",self.generate_preview),("Lưu campaign",self.save_campaign),("Chọn tất cả",lambda:self._all_preview(True)),("Bỏ chọn",lambda:self._all_preview(False))):x=QPushButton(text);x.clicked.connect(fn);r.addWidget(x)
        l.addLayout(r);self.preview=QTableWidget(0,7);self.preview.setHorizontalHeaderLabels(["Chọn","Post","Loại","Published At","Số comment","Comments","Trạng thái"]);self.preview.setWordWrap(True);self.preview.horizontalHeader().setStretchLastSection(True);self.preview.setColumnWidth(0,54);self.preview.itemChanged.connect(self._preview_changed);l.addWidget(self.preview,1);self.preview_status=QLabel("Chưa có preview.");l.addWidget(self.preview_status);x=QPushButton("Đăng comment — sẽ triển khai ở Phase 2B.");x.setEnabled(False);l.addWidget(x);return w
    def refresh_pages(self):
        old=self.page.currentData();self.page.blockSignals(True);self.page.clear();self.page.addItem("Chọn Fanpage",None)
        for p in self.service.list_pages():self.page.addItem(p["page_name"],(p["project_id"],p["page_id"]))
        self.page.setCurrentIndex(max(0,self.page.findData(old)));self.page.blockSignals(False);self._summary()
    def refresh_libraries(self):
        self.library_picker.blockSignals(True);self.library_picker.clear();self.library_picker.addItem("Mở thư viện đã lưu…",None)
        for x in self.service.list_libraries():self.library_picker.addItem(x["name"],x["id"])
        self.library_picker.blockSignals(False);self.campaign_picker.blockSignals(True);self.campaign_picker.clear();self.campaign_picker.addItem("Mở campaign đã lưu…",None)
        for x in self.service.list_campaigns():self.campaign_picker.addItem(x["name"],x["id"])
        self.campaign_picker.blockSignals(False);self._summary()
    def _workflow_step_changed(self,index):
        titles=("Chọn Fanpage & bài viết","Chuẩn bị thư viện comment","Cấu hình & xem trước")
        help_text=(
            "Chọn Fanpage, tải dữ liệu Facebook và chọn các bài viết cần đưa vào preview.",
            "Import CSV hoặc thêm comment cục bộ, sau đó bật các comment muốn sử dụng.",
            "Chọn Random 1/3, tạo preview và lưu campaign cục bộ. Không có comment nào được đăng Facebook.",
        )
        self._workflow_step=index;self._workflow_titles=titles
        self.workflow_help.setText(help_text[index]);self.workflow_message.setText("")
        self.back_button.setEnabled(index>0);self.next_button.setVisible(index<2)
        if index<2:self.next_button.setText("Tiếp theo →")
        self._workflow_counts()
    def _workflow_counts(self):
        selected=len(self.selected_post_ids);eligible=sum(bool(p["post_id"] in self.selected_post_ids and (p.get("comment_target_id") or p.get("post_type")!="Reel")) for p in self.posts)
        enabled=sum(bool(item.enabled and item.comment_text.strip()) for item in self.items()) if hasattr(self,"comments") else 0
        preview_count=sum(map(len,self.preview_comments.values()))
        counts=f"Đã chọn: {selected} bài ({eligible} target đã xác minh) · Comment bật: {enabled} · Preview: {preview_count}"
        self.workflow_progress.setText("  •  ".join(f"{'●' if i==self._workflow_step else '○'} Bước {i+1}: {title}" for i,title in enumerate(self._workflow_titles))+"\n"+counts)
        self.summary.setText(counts)
    def _go_to_step(self,index):
        if 0<=index<self.tabs.count():self.tabs.setCurrentIndex(index)
    def _next_step(self):
        step=self.tabs.currentIndex()
        if step==0:
            if not self.page.currentData():self.workflow_message.setText("Hãy chọn Fanpage, sau đó tải bài viết từ Facebook.");return
            if not self.selected_post_ids:self.workflow_message.setText("Hãy tải và chọn ít nhất một bài viết trước khi tiếp tục.");return
            unverified=sum(p["post_id"] in self.selected_post_ids and p.get("post_type")=="Reel" and not p.get("comment_target_id") for p in self.posts)
            if unverified:self.workflow_message.setText(f"Có {unverified} Reels chưa có Post ID mục tiêu; chúng chỉ được giữ để xem trước, không dùng để gửi comment.")
        if step==1 and not any(item.enabled and item.comment_text.strip() for item in self.items()):
            self.workflow_message.setText("Hãy import hoặc thêm ít nhất một comment đang bật trước khi tiếp tục.");return
        self._go_to_step(step+1)
    def _set_posts_loading(self, loading: bool) -> None:
        self.load_posts_button.set_loading(loading)
        if loading:
            self.load_posts_button.setStyleSheet(
                "QPushButton { background-color: #1e293b; color: #94a3b8; border: 1px solid #334155; border-radius: 6px; padding: 7px 16px; font-weight: 600; }"
            )
            self.loading_container.show()
            self.post_status.setText("Đang tải bài viết từ Facebook...")
            if hasattr(self, "table_overlay"):
                self.table_overlay.start("Đang đồng bộ dữ liệu...")
        else:
            self.load_posts_button.setStyleSheet("")
            self.loading_container.hide()
            if hasattr(self, "table_overlay"):
                self.table_overlay.stop()

    def eventFilter(self, watched, event):
        if hasattr(self, "posts_table") and watched is self.posts_table and event.type() == QEvent.Type.Resize:
            if hasattr(self, "table_overlay") and self.table_overlay.isVisible():
                self.table_overlay.resize(self.posts_table.size())
        return super().eventFilter(watched, event)

    def load_posts(self):
        selected=self.page.currentData()
        if not selected:self.post_status.setText("Hãy chọn Fanpage trước.");return
        if self._posts_worker is not None:return
        project_id,page_id=selected; kind={"Tất cả":"all","Reels":"reels","Bài viết thường":"posts"}[self.content_type.currentText()]
        self._set_posts_loading(True)
        path=self.database.path
        def operation():
            worker_database=Database(path)
            try:return AutoCommentService(worker_database).fetch_posts(project_id,page_id,self.start_date.text(),self.end_date.text(),kind)
            finally:worker_database.close()
        worker=FacebookRequestWorker(operation,self);self._posts_worker=worker
        def succeeded(posts):
            if self.page.currentData()!=selected:return
            had_posts=bool(self.posts);previous=set(self.selected_post_ids);self.posts=posts
            self.selected_post_ids={p["post_id"] for p in posts if p["post_id"] in previous} if had_posts else {p["post_id"] for p in posts}
            self._render_posts();self._invalidate()
            post_count=sum(p["post_type"] == "Post" for p in posts);reel_count=sum(p["post_type"] == "Reel" for p in posts)
            self.post_status.setText(f"Đã tải {post_count} bài viết và {reel_count} Reels · {len(self.selected_post_ids)} đã chọn.")
        def failed(message):
            self.post_status.setText(f"Không tải được bài viết: {message}")
        def finished():
            if self._posts_worker is worker:self._posts_worker=None;self._set_posts_loading(False)
            worker.deleteLater()
        worker.succeeded.connect(succeeded);worker.failed.connect(failed);worker.finished.connect(finished);worker.start()
    def _render_posts(self):
        self.posts_table.blockSignals(True);self.posts_table.setRowCount(0)
        for p in self.posts:
            row=self.posts_table.rowCount();self.posts_table.insertRow(row);q=QTableWidgetItem();q.setCheckState(Qt.CheckState.Checked if p["post_id"] in self.selected_post_ids else Qt.CheckState.Unchecked);self.posts_table.setItem(row,0,q)
            source_id=p.get("source_post_id") or p.get("reel_id") or p["post_id"];status="Sẵn sàng" if p.get("comment_target_id") else "Reel: chưa có Post ID"
            for c,v in enumerate((p["published_at"],p["post_type"],source_id,status),1):
                item=QTableWidgetItem(str(v));
                if c==3:item.setData(Qt.ItemDataRole.UserRole,p["post_id"])
                self.posts_table.setItem(row,c,item)
        self.posts_table.blockSignals(False);self.post_status.setText(f"{len(self.posts)} bài · {len(self.selected_post_ids)} đã chọn" if self.posts else "Tải thành công nhưng không có bài phù hợp trong khoảng ngày đã chọn.");self._summary()
    def _post_changed(self,i):
        if i.column()==0:
            pid=self.posts_table.item(i.row(),3).data(Qt.ItemDataRole.UserRole);(self.selected_post_ids.add if i.checkState()==Qt.CheckState.Checked else self.selected_post_ids.discard)(pid);self._render_posts();self._invalidate()
    def _all_posts(self,on):self.selected_post_ids={p["post_id"] for p in self.posts} if on else set();self._render_posts();self._invalidate()
    def set_items(self,items):
        self.comments.blockSignals(True);self.comments.setRowCount(0)
        for it in items:
            r=self.comments.rowCount();self.comments.insertRow(r);x=QTableWidgetItem();x.setCheckState(Qt.CheckState.Checked if it.enabled else Qt.CheckState.Unchecked);self.comments.setItem(r,0,x);self.comments.setItem(r,1,QTableWidgetItem(it.source_row_id or ""));self.comments.setItem(r,2,QTableWidgetItem(it.comment_text))
        self.comments.blockSignals(False);self._changed()
    def items(self):return [CommentItem(self.comments.item(r,2).text(),self.comments.item(r,0).checkState()==Qt.CheckState.Checked,self.comments.item(r,1).text() or None,r) for r in range(self.comments.rowCount())]
    def import_csv(self):
        p,_=QFileDialog.getOpenFileName(self,"Chọn CSV comment","","CSV (*.csv)");
        if p:self.load_csv(Path(p))
    def load_csv(self,p):
        try:self.set_items(parse_comment_csv(p.read_bytes()).items)
        except (OSError,AutoCommentValidationError) as e:QMessageBox.warning(self,"CSV comment",str(e))
    def add_row(self):self.set_items(self.items()+[CommentItem("",True,None,self.comments.rowCount())])
    def delete_rows(self):
        rows=sorted({x.row() for x in self.comments.selectedItems()},reverse=True)
        if rows and QMessageBox.question(self,"Xóa comment",f"Xóa {len(rows)} dòng?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes:
            for r in rows:self.comments.removeRow(r)
            self._changed()
    def clear_rows(self):self.comments.setRowCount(0);self.library_id=None;self._changed()
    def save_library(self):
        try:self.library_id=self.service.save_library(self.library_name.text(),self.items(),self.library_id);self.refresh_libraries();self.csv_status.setText("Đã lưu thư viện.")
        except AutoCommentValidationError as e:self.csv_status.setText(f"Không thể lưu thư viện: {e}");QMessageBox.warning(self,"Thư viện",str(e))
    def open_library(self):
        if i:=self.library_picker.currentData():self.library_id=i;self.library_name.setText(self.library_picker.currentText());self.set_items(self.service.load_library(i))
    def _filter(self,t):
        for r in range(self.comments.rowCount()):self.comments.setRowHidden(r,bool(t and t.casefold() not in self.comments.item(r,2).text().casefold()))
    def _changed(self,*_):
        a=self.items();self.csv_status.setText(f"{len(a)} tổng · {sum(x.enabled for x in a)} đang bật · {len(a)-len({x.comment_text for x in a})} trùng");self._invalidate();self._summary()
    def generate_preview(self):
        if self._preview_busy:return
        self._preview_busy=True;self.preview_button.set_loading(True);self.preview_status.setText("Đang tạo preview...")
        try:
            posts=[p for p in self.posts if p["post_id"] in self.selected_post_ids]
            if not posts:self.preview_status.setText("Hãy tải và chọn ít nhất một bài viết trước khi tạo preview.");return
            comments={p["post_id"]:select_comments(self.items(),3 if self.three.isChecked() else 1,"sequential" if self.sequential_mode.isChecked() else "random") for p in posts}
            self.preview_comments=comments;self._render_preview(posts);self._summary()
            self.preview_status.setText(f"Đã tạo {sum(map(len,comments.values()))} comment cho {len(posts)} bài viết.")
        except AutoCommentValidationError as e:self.preview_status.setText(str(e))
        finally:self._preview_busy=False;self.preview_button.set_loading(False)
    def _render_preview(self,posts):
        self.preview.blockSignals(True)
        self.preview.setRowCount(0)
        for p in posts:
            r=self.preview.rowCount();self.preview.insertRow(r);x=QTableWidgetItem();x.setCheckState(Qt.CheckState.Checked);self.preview.setItem(r,0,x)
            for c,v in enumerate((p["post_id"],p["post_type"],p["published_at"],str(len(self.preview_comments.get(p["post_id"],[]))),"\n".join(self.preview_comments.get(p["post_id"],[])),"PREVIEW"),1):self.preview.setItem(r,c,QTableWidgetItem(v))
        self.preview.blockSignals(False)
    def save_campaign(self):
        selected=self.page.currentData()
        if not selected:self.preview_status.setText("Hãy chọn Fanpage trước khi lưu campaign.");return
        posts=[p for p in self.posts if p["post_id"] in self.preview_comments and self._preview_post_selected(p["post_id"])]
        if not posts:self.preview_status.setText("Hãy tạo preview và chọn ít nhất một bài viết trước khi lưu.");return
        v={"name":self.campaign_name.text() or "Auto Comment draft","page_id":selected[1],"start_date":self.start_date.text(),"end_date":self.end_date.text(),"content_type":self.content_type.currentText(),"selection_mode":"sequential" if self.sequential_mode.isChecked() else "random","count":3 if self.three.isChecked() else 1,"prevent_duplicates":self.prevent_duplicates.isChecked()}
        self.campaign_id=self.service.save_preview(v,posts,{p["post_id"]:self.preview_comments[p["post_id"]] for p in posts},self.campaign_id)
        self.refresh_libraries();self.preview_status.setText("Đã lưu campaign.")
    def open_campaign(self):
        campaign_id=self.campaign_picker.currentData()
        if not campaign_id:return
        c,rows=self.service.load_campaign_preview(campaign_id);self.campaign_id=campaign_id;self.campaign_name.setText(c["name"])
        page_index=self.page.findData(next((data for data in (self.page.itemData(i) for i in range(self.page.count())) if data and data[1]==c["page_id"]),None))
        if page_index>=0:self.page.setCurrentIndex(page_index)
        self.start_date.setDate(date.fromisoformat(c["post_start_date"]));self.end_date.setDate(date.fromisoformat(c["post_end_date"]));self.content_type.setCurrentText(c["content_type"]);self.three.setChecked(c["comments_per_post"]==3);self.one.setChecked(c["comments_per_post"]!=3);self.sequential_mode.setChecked(c["selection_mode"]=="sequential");self.random_mode.setChecked(c["selection_mode"]!="sequential");self.prevent_duplicates.setChecked(bool(c["prevent_duplicates"]))
        grouped={}
        for row in rows:grouped.setdefault(row["post_id"],[]).append(row)
        self.posts=[{"post_id":post_id,"post_type":items[0]["post_type"],"published_at":items[0]["published_at"]} for post_id,items in grouped.items()];self.selected_post_ids=set(grouped);self.preview_comments={post_id:[item["comment_text"] for item in items] for post_id,items in grouped.items()};self._render_posts();self._render_preview(self.posts);self.preview_status.setText(f"Đã mở campaign {c['name']}.")
    def _all_preview(self,on):
        for r in range(self.preview.rowCount()):self.preview.item(r,0).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
    def _preview_post_selected(self,post_id):
        for r in range(self.preview.rowCount()):
            if self.preview.item(r,1).text()==post_id:return self.preview.item(r,0).checkState()==Qt.CheckState.Checked
        return False
    def _preview_changed(self,item):
        if item.column()==0:self.preview_status.setText(f"Preview: {sum(self._preview_post_selected(self.preview.item(r,1).text()) for r in range(self.preview.rowCount()))} bài viết được chọn.");self._summary()
    def _page_changed(self,*_):self.posts=[];self.selected_post_ids.clear();self._render_posts();self._invalidate()
    def _invalidate(self,*_):
        if getattr(self,"preview_comments",None):self.preview_status.setText("Cấu hình đã thay đổi; hãy tạo lại preview.")
    def _summary(self):
        if hasattr(self,"_workflow_step") and hasattr(self,"summary"):self._workflow_counts()
    def closeEvent(self,event):
        worker=self._posts_worker;self._posts_worker=None;self._set_posts_loading(False)
        if worker is not None:
            worker.requestInterruption()
            for signal in (worker.succeeded,worker.failed,worker.finished):
                try:signal.disconnect()
                except (RuntimeError,TypeError):pass
            worker.finished.connect(worker.deleteLater)
        super().closeEvent(event)
    def dragEnterEvent(self,e):
        if e.mimeData().hasUrls() and any(x.toLocalFile().lower().endswith('.csv') for x in e.mimeData().urls()):e.acceptProposedAction()
    def dropEvent(self,e):
        for u in e.mimeData().urls():
            if Path(u.toLocalFile()).suffix.lower()=='.csv':self.load_csv(Path(u.toLocalFile()));e.acceptProposedAction();return

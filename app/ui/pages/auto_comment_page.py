from __future__ import annotations
from datetime import date
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget
from app.database.connection import Database
from app.services.auto_comment_comment_service import AutoCommentCommentService
from app.services.auto_comment_service import AutoCommentService, AutoCommentValidationError, CommentItem, parse_comment_csv, select_comments
from app.ui.facebook_worker import FacebookRequestWorker
from app.ui.widgets.loading_button import LoadingButton

TARGET_LABELS={"READY":"Sẵn sàng","TARGET_UNVERIFIED":"Chưa xác minh","TARGET_UNSUPPORTED":"Không hỗ trợ","PERMISSION_DENIED":"Thiếu quyền","TOKEN_MISSING":"Thiếu token","TOKEN_EXPIRED":"Token hết hạn","WRONG_PAGE":"Sai Fanpage","ERROR":"Lỗi"}

class CheckboxTable(QTableWidget):
    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Space and self.currentColumn()==0:
            item=self.currentItem()
            if item is not None and item.flags()&Qt.ItemFlag.ItemIsUserCheckable and item.flags()&Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(Qt.CheckState.Unchecked if item.checkState()==Qt.CheckState.Checked else Qt.CheckState.Checked);event.accept();return
        super().keyPressEvent(event)

class AutoCommentPage(QWidget):
    pageManagementRequested=Signal()
    def __init__(self,database,parent=None):
        super().__init__(parent);self.database=database;self.service=AutoCommentService(database);self.comment_service=AutoCommentCommentService(database);self.library_id=self.campaign_id=None;self.posts=[];self.selected_post_ids=set();self.preview_comments={};self.target_states={};self._posts_worker=self._target_worker=self._publish_worker=None;self._preview_busy=False;self.setAcceptDrops(True)
        self.setStyleSheet("QTableWidget#autoCommentPostsTable,QTableWidget#autoCommentCommentsTable,QTableWidget#autoCommentPreviewTable{selection-background-color:#1d4ed8;selection-color:white} QLabel#autoCommentProgress{padding:8px 10px;border-radius:6px;background:#17243a;color:#dbeafe} QFrame#autoCommentLoading{background:rgba(10,18,32,210);border:1px solid #2d5f9e;border-radius:6px}")
        root=QVBoxLayout(self);root.setContentsMargins(18,14,18,14);root.setSpacing(7);title=QLabel("Auto Comment",objectName="title");root.addWidget(title);root.addWidget(QLabel("Chuẩn bị, xác minh và xem trước cục bộ. Preview và lưu campaign không đăng comment Facebook."));self.workflow_progress=QLabel(objectName="autoCommentProgress");self.workflow_help=QLabel(wordWrap=True);self.workflow_message=QLabel(wordWrap=True);root.addWidget(self.workflow_progress);root.addWidget(self.workflow_help);root.addWidget(self.workflow_message)
        self.tabs=QTabWidget();self.tabs.addTab(self._posts(),"Bước 1");self.tabs.addTab(self._library(),"Bước 2");self.tabs.addTab(self._campaign(),"Bước 3");self.tabs.addTab(self._testing(),"Bước 4");self.tabs.tabBar().hide();self.tabs.currentChanged.connect(self._workflow_step_changed);root.addWidget(self.tabs,1);nav=QHBoxLayout();self.back_button=QPushButton("← Quay lại");self.back_button.clicked.connect(lambda:self._go_to_step(self.tabs.currentIndex()-1));self.next_button=QPushButton("Tiếp theo →",objectName="primary");self.next_button.clicked.connect(self._next_step);nav.addWidget(self.back_button);nav.addStretch();nav.addWidget(self.next_button);root.addLayout(nav);self.refresh_pages();self.refresh_libraries();self._workflow_step_changed(0)
    @staticmethod
    def _table(columns,name,editable=False):
        table=CheckboxTable(0,len(columns));table.setObjectName(name);table.setHorizontalHeaderLabels(columns);table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked|QAbstractItemView.EditTrigger.EditKeyPressed if editable else QAbstractItemView.EditTrigger.NoEditTriggers);header=table.horizontalHeader();header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive);header.setStretchLastSection(True);return table
    @staticmethod
    def _check_item(checked=True,enabled=True):
        item=QTableWidgetItem();flags=Qt.ItemFlag.ItemIsSelectable|Qt.ItemFlag.ItemIsUserCheckable
        if enabled:flags|=Qt.ItemFlag.ItemIsEnabled
        item.setFlags(flags);item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked);return item
    def _posts(self):
        w=QWidget();l=QVBoxLayout(w);box=QGroupBox("Bước 1 · Chọn Fanpage & bài viết");f=QFormLayout(box);self.page=QComboBox();self.page.currentIndexChanged.connect(self._page_changed);manage=QPushButton("Quản lý Fanpage");manage.clicked.connect(self.pageManagementRequested.emit);pr=QHBoxLayout();pr.addWidget(self.page,1);pr.addWidget(manage);self.start_date,self.end_date=QDateEdit(),QDateEdit()
        for x in (self.start_date,self.end_date):x.setCalendarPopup(True);x.setDisplayFormat("yyyy-MM-dd");x.dateChanged.connect(self._invalidate)
        self.start_date.setDate(date.today().replace(day=1));self.end_date.setDate(date.today());self.content_type=QComboBox();self.content_type.addItems(["Tất cả","Reels","Bài viết thường"]);self.content_type.currentIndexChanged.connect(self._invalidate);self.load_posts_button=LoadingButton("Tải bài viết");self.load_posts_button.setObjectName("primary");self.load_posts_button.clicked.connect(self.load_posts);f.addRow("Fanpage",pr);f.addRow("Từ ngày",self.start_date);f.addRow("Đến ngày",self.end_date);f.addRow("Loại",self.content_type);f.addRow(self.load_posts_button);l.addWidget(box)
        r=QHBoxLayout();self.select_all_posts=QPushButton("Chọn tất cả");self.select_all_posts.clicked.connect(lambda:self._all_posts(True));self.clear_all_posts=QPushButton("Bỏ chọn tất cả");self.clear_all_posts.clicked.connect(lambda:self._all_posts(False));verify=QPushButton("Xác minh target đã chọn");verify.clicked.connect(self.verify_selected_targets);self.post_status=QLabel("Chưa tải bài viết.");r.addWidget(self.select_all_posts);r.addWidget(self.clear_all_posts);r.addWidget(verify);r.addWidget(self.post_status,1);l.addLayout(r);self.posts_table=self._table(["Chọn","Published At","Loại","ID nguồn","Target comment"],"autoCommentPostsTable");self.posts_table.setColumnWidth(0,58);self.posts_table.setColumnWidth(1,150);self.posts_table.setColumnWidth(2,75);self.posts_table.setColumnWidth(3,260);self.posts_table.itemChanged.connect(self._post_changed);l.addWidget(self.posts_table,1);self.loading_container=QFrame(self.posts_table.viewport(),objectName="autoCommentLoading");o=QVBoxLayout(self.loading_container);x=QLabel("Đang đồng bộ dữ liệu…");x.setAlignment(Qt.AlignmentFlag.AlignCenter);o.addWidget(x);self.table_overlay=self.loading_container;self.loading_container.hide();l.addWidget(QLabel("Reel chỉ trở thành target gửi được sau khi xác minh GET-only; không có ID nào được tự suy diễn."));return w
    def _library(self):
        w=QWidget();l=QVBoxLayout(w);l.addWidget(QLabel("Bước 2 · Import CSV hoặc chỉnh sửa thư viện cục bộ. Thay đổi chưa lưu vẫn được giữ khi chuyển bước."));r=QHBoxLayout();self.library_picker=QComboBox();self.library_picker.currentIndexChanged.connect(self.open_library);self.library_name=QLineEdit("Thư viện comment");self.search=QLineEdit(placeholderText="Tìm comment…");self.search.textChanged.connect(self._filter)
        for x in (self.library_picker,self.library_name,self.search):r.addWidget(x,1)
        l.addLayout(r);r=QHBoxLayout()
        for text,fn in (("Import CSV",self.import_csv),("Thêm dòng",self.add_row),("Xóa đã chọn",self.delete_rows),("Lưu thư viện",self.save_library),("Xóa import",self.clear_rows)):x=QPushButton(text);x.clicked.connect(fn);r.addWidget(x)
        self.csv_status=QLabel();r.addWidget(self.csv_status,1);l.addLayout(r);self.comments=self._table(["Bật","ID","Comment"],"autoCommentCommentsTable",editable=True);self.comments.setColumnWidth(0,58);self.comments.setColumnWidth(1,100);self.comments.itemChanged.connect(self._changed);l.addWidget(self.comments,1);l.addWidget(QLabel("CSV gốc không bị thay đổi. Chỉ comment bật và hợp lệ được đưa vào preview."));return w
    def _campaign(self):
        w=QWidget();l=QVBoxLayout(w);box=QGroupBox("Bước 3 · Cấu hình & xem trước");f=QFormLayout(box);self.campaign_name=QLineEdit("Auto Comment draft");self.summary=QLabel();self.one=QRadioButton("Random 1 comment mỗi bài");self.three=QRadioButton("Random 3 comment khác nhau mỗi bài");self.one.setChecked(True);self.random_mode=QRadioButton("Ngẫu nhiên");self.sequential_mode=QRadioButton("Tuần tự");self.random_mode.setChecked(True);self.prevent_duplicates=QCheckBox("Không lặp nội dung trong cùng bài");self.prevent_duplicates.setChecked(True);r=QHBoxLayout()
        for x in (self.one,self.three,self.random_mode,self.sequential_mode,self.prevent_duplicates):r.addWidget(x);x.toggled.connect(self._invalidate)
        self.campaign_picker=QComboBox();self.campaign_picker.currentIndexChanged.connect(self.open_campaign);f.addRow("Tên",self.campaign_name);f.addRow("Thông tin",self.summary);f.addRow("Chọn comment",r);f.addRow("Mở campaign",self.campaign_picker);l.addWidget(box);r=QHBoxLayout();self.preview_button=LoadingButton("Xem trước comment");self.preview_button.clicked.connect(self.generate_preview);r.addWidget(self.preview_button)
        for text,fn in (("Tạo lại preview",self.generate_preview),("Lưu campaign",self.save_campaign),("Chọn tất cả",lambda:self._all_preview(True)),("Bỏ chọn",lambda:self._all_preview(False))):x=QPushButton(text);x.clicked.connect(fn);r.addWidget(x)
        l.addLayout(r);self.preview=self._table(["Chọn","Post","Loại","Published At","Số comment","Comments","Target","Preview"],"autoCommentPreviewTable");self.preview.setColumnWidth(0,58);self.preview.setColumnWidth(1,200);self.preview.setColumnWidth(2,70);self.preview.setColumnWidth(3,150);self.preview.setColumnWidth(4,90);self.preview.setColumnWidth(5,300);self.preview.itemChanged.connect(self._preview_changed);l.addWidget(self.preview,1);self.preview_status=QLabel("Chưa có preview.");l.addWidget(self.preview_status);l.addWidget(QLabel("Preview chỉ là kế hoạch cục bộ; không có comment nào được đăng từ bước này."));return w
    def _testing(self):
        w=QWidget();l=QVBoxLayout(w);box=QGroupBox("Gửi thử 1 comment");f=QFormLayout(box);self.test_target_info=QLabel("Chọn đúng một Post hoặc Reel ở Bước 1.",wordWrap=True);self.manual_comment=QLineEdit(placeholderText="Nhập một comment thử nghiệm");self.manual_comment.textChanged.connect(self._refresh_test_target);self.publish_one_button=LoadingButton("Gửi thử 1 comment");self.publish_one_button.clicked.connect(self.confirm_publish_one);self.test_status=QLabel();f.addRow("Target đã chọn",self.test_target_info);f.addRow("Nội dung xem trước",self.manual_comment);f.addRow(self.publish_one_button);l.addWidget(box)
        history=QGroupBox("Lịch sử comment");h=QVBoxLayout(history);filters=QHBoxLayout();self.history_page=QComboBox();self.history_type=QComboBox();self.history_type.addItem("Tất cả loại","");self.history_type.addItems(["POST","REEL"]);self.history_status=QComboBox();self.history_status.addItem("Tất cả trạng thái","");self.history_status.addItems(["SUCCESS","FAILED","UNCERTAIN"]);self.history_from=QLineEdit(placeholderText="Từ ngày YYYY-MM-DD");self.history_to=QLineEdit(placeholderText="Đến ngày YYYY-MM-DD");refresh=QPushButton("Làm mới lịch sử");refresh.clicked.connect(self.refresh_history)
        for x in (self.history_page,self.history_type,self.history_status,self.history_from,self.history_to,refresh):filters.addWidget(x)
        h.addLayout(filters);self.history_table=self._table(["Thời gian","Fanpage","Loại","Nguồn","Target","Comment","Comment ID","Xuất bản","Lỗi","Link"],"autoCommentHistoryTable");self.history_table.setColumnWidth(0,145);self.history_table.setColumnWidth(1,150);self.history_table.setColumnWidth(3,145);self.history_table.setColumnWidth(4,145);self.history_table.setColumnWidth(5,220);self.history_table.setColumnWidth(6,210);h.addWidget(self.history_table,1);actions=QHBoxLayout();copy=QPushButton("Sao chép Comment ID");copy.clicked.connect(self.copy_history_comment_id);self.open_history_link=QPushButton("Không có liên kết trực tiếp");self.open_history_link.setEnabled(False);actions.addWidget(copy);actions.addWidget(self.open_history_link);actions.addStretch();h.addLayout(actions);l.addWidget(history,1);l.addWidget(self.test_status);return w
    def refresh_pages(self):
        old=self.page.currentData();self.page.blockSignals(True);self.page.clear();self.page.addItem("Chọn Fanpage",None)
        for p in self.service.list_pages():self.page.addItem(p["page_name"],(p["project_id"],p["page_id"]))
        self.page.setCurrentIndex(max(0,self.page.findData(old)));self.page.blockSignals(False)
        if hasattr(self,"history_page"):
            current=self.history_page.currentData();self.history_page.blockSignals(True);self.history_page.clear();self.history_page.addItem("Tất cả Fanpage",None)
            for p in self.service.list_pages():self.history_page.addItem(f"{p['page_name']} · {p['page_id']}",(p["project_id"],p["page_id"]))
            self.history_page.setCurrentIndex(max(0,self.history_page.findData(current)));self.history_page.blockSignals(False);self.refresh_history()
        self._summary()
    def refresh_libraries(self):
        self.library_picker.blockSignals(True);self.library_picker.clear();self.library_picker.addItem("Mở thư viện đã lưu…",None)
        for x in self.service.list_libraries():self.library_picker.addItem(x["name"],x["id"])
        self.library_picker.blockSignals(False);self.campaign_picker.blockSignals(True);self.campaign_picker.clear();self.campaign_picker.addItem("Mở campaign đã lưu…",None)
        for x in self.service.list_campaigns():self.campaign_picker.addItem(x["name"],x["id"])
        self.campaign_picker.blockSignals(False);self._summary()
    def _workflow_step_changed(self,index):
        titles=("Chọn Fanpage & bài viết","Chuẩn bị thư viện comment","Chiến dịch & Preview","Gửi thử & Lịch sử");help_text=("Chọn Fanpage, tải dữ liệu Facebook, sau đó chọn bài viết. Xác minh target trước khi muốn gửi một comment.","Import CSV hoặc thêm comment cục bộ; bật các dòng hợp lệ cần dùng.","Chọn Random 1/3, tạo preview và lưu campaign. Các thao tác này không đăng Facebook.","Gửi đúng một comment sau khi target đã READY và xác nhận rõ ràng; xem lịch sử đã lưu.");self._workflow_step=index;self._workflow_titles=titles;self.workflow_help.setText(help_text[index]);self.workflow_message.setText("");self.back_button.setEnabled(index>0);self.next_button.setVisible(index<3);self._workflow_counts()
    def _workflow_counts(self):
        selected=len(self.selected_post_ids);eligible=sum(p["post_id"] in self.selected_post_ids and self.target_states.get(p["post_id"],{}).get("status")=="READY" for p in self.posts);enabled=sum(x.enabled and bool(x.comment_text.strip()) for x in self.items()) if hasattr(self,"comments") else 0;preview_count=sum(map(len,self.preview_comments.values()));counts=f"Đã chọn: {selected} bài · Target sẵn sàng: {eligible} · Comment bật: {enabled} · Preview: {preview_count}";self.workflow_progress.setText("  •  ".join(f"{'●' if i==self._workflow_step else '○'} Bước {i+1}: {title}" for i,title in enumerate(self._workflow_titles))+"\n"+counts);self.summary.setText(counts)
    def _go_to_step(self,index):
        if 0<=index<self.tabs.count():self.tabs.setCurrentIndex(index)
    def _next_step(self):
        step=self.tabs.currentIndex()
        if step==0 and (not self.page.currentData() or not self.selected_post_ids):self.workflow_message.setText("Hãy chọn Fanpage, tải dữ liệu và chọn ít nhất một bài viết trước khi tiếp tục.");return
        if step==1 and not any(x.enabled and x.comment_text.strip() for x in self.items()):self.workflow_message.setText("Hãy import hoặc thêm ít nhất một comment đang bật trước khi tiếp tục.");return
        self._go_to_step(step+1)
    def _set_posts_loading(self,loading):
        self.load_posts_button.set_loading(loading);self.load_posts_button.setText("⌛ Đang tải bài viết..." if loading else "Tải bài viết");self.loading_container.setGeometry(self.posts_table.viewport().rect());self.loading_container.setVisible(loading)
    def load_posts(self):
        selected=self.page.currentData()
        if not selected:self.post_status.setText("Hãy chọn Fanpage trước.");return
        if self._posts_worker is not None:return
        project_id,page_id=selected;kind={"Tất cả":"all","Reels":"reels","Bài viết thường":"posts"}[self.content_type.currentText()];self._set_posts_loading(True);self.post_status.setText("Đang tải bài viết từ Facebook...");path=self.database.path
        def operation():
            db=Database(path)
            try:return AutoCommentService(db).fetch_posts(project_id,page_id,self.start_date.text(),self.end_date.text(),kind)
            finally:db.close()
        worker=FacebookRequestWorker(operation,self);self._posts_worker=worker
        def succeeded(posts):
            if self.page.currentData()!=selected:return
            old=set(self.selected_post_ids);had=bool(self.posts);self.posts=posts;self.selected_post_ids={p["post_id"] for p in posts if p["post_id"] in old} if had else {p["post_id"] for p in posts};self._restore_target_states();self._render_posts();a=sum(p["post_type"]=="Post" for p in posts);b=sum(p["post_type"]=="Reel" for p in posts);self.post_status.setText(f"Đã tải {a} bài viết và {b} Reels · {len(self.selected_post_ids)} đã chọn.")
        def failed(message):self.post_status.setText(f"Không tải được bài viết: {message}")
        def finished():
            if self._posts_worker is worker:self._posts_worker=None;self._set_posts_loading(False)
            worker.deleteLater()
        worker.succeeded.connect(succeeded);worker.failed.connect(failed);worker.finished.connect(finished);worker.start()
    def _target_status(self,p):
        state=self.target_states.get(p["post_id"])
        if state:return TARGET_LABELS.get(state["status"],state["status"])
        return "Chờ xác minh" if p.get("comment_target_id") or p.get("video_id") else "Reel chưa có target"
    def _restore_target_states(self):
        selected=self.page.currentData();self.target_states=self.comment_service.load_targets(*selected,[p["post_id"] for p in self.posts]) if selected else {}
    def _render_posts(self):
        self.posts_table.blockSignals(True);self.posts_table.setRowCount(0)
        for p in self.posts:
            r=self.posts_table.rowCount();self.posts_table.insertRow(r);self.posts_table.setItem(r,0,self._check_item(p["post_id"] in self.selected_post_ids));source=p.get("source_post_id") or p.get("reel_id") or p["post_id"]
            for c,v in enumerate((p.get("published_at",""),p.get("post_type",""),source,self._target_status(p)),1):item=QTableWidgetItem(str(v));item.setData(Qt.ItemDataRole.UserRole,p["post_id"]);self.posts_table.setItem(r,c,item)
        self.posts_table.blockSignals(False);self._update_header_selection();self._refresh_test_target();self._summary()
    def _update_header_selection(self):self.select_all_posts.setEnabled(bool(self.posts));self.clear_all_posts.setEnabled(bool(self.selected_post_ids))
    def _post_changed(self,item):
        if item.column()==0:
            pid=self.posts_table.item(item.row(),3).data(Qt.ItemDataRole.UserRole);(self.selected_post_ids.add if item.checkState()==Qt.CheckState.Checked else self.selected_post_ids.discard)(pid);self._update_header_selection();self._refresh_test_target();self._invalidate();self._summary()
    def _all_posts(self,on):self.selected_post_ids={p["post_id"] for p in self.posts} if on else set();self._render_posts();self._invalidate()
    def verify_selected_targets(self):
        selected=self.page.currentData();posts=[p for p in self.posts if p["post_id"] in self.selected_post_ids]
        if not selected or not posts:self.post_status.setText("Hãy chọn Fanpage và ít nhất một bài viết để xác minh target.");return
        if self._target_worker is not None:return
        project_id,page_id=selected;path=self.database.path
        def operation():
            db=Database(path)
            try:return [AutoCommentCommentService(db).resolve_target(project_id,page_id,p) for p in posts]
            finally:db.close()
        worker=FacebookRequestWorker(operation,self);self._target_worker=worker;self.post_status.setText("Đang xác minh target comment bằng GET-only…")
        def succeeded(results):self.target_states.update({x["source_id"]:x for x in results});self._render_posts();self.post_status.setText(f"Đã xác minh {sum(x['status']=='READY' for x in results)}/{len(results)} target có thể comment.")
        def failed(message):self.post_status.setText(f"Không xác minh được target: {message}")
        def finished():
            if self._target_worker is worker:self._target_worker=None
            worker.deleteLater()
        worker.succeeded.connect(succeeded);worker.failed.connect(failed);worker.finished.connect(finished);worker.start()
    def set_items(self,items):
        self.comments.blockSignals(True);self.comments.setRowCount(0)
        for it in items:r=self.comments.rowCount();self.comments.insertRow(r);self.comments.setItem(r,0,self._check_item(it.enabled));self.comments.setItem(r,1,QTableWidgetItem(it.source_row_id or ""));self.comments.setItem(r,2,QTableWidgetItem(it.comment_text))
        self.comments.blockSignals(False);self._changed()
    def items(self):return [CommentItem(self.comments.item(r,2).text(),self.comments.item(r,0).checkState()==Qt.CheckState.Checked,self.comments.item(r,1).text() or None,r) for r in range(self.comments.rowCount())]
    def import_csv(self):
        p,_=QFileDialog.getOpenFileName(self,"Chọn CSV comment","","CSV (*.csv)")
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
    def _changed(self,*_):a=self.items();self.csv_status.setText(f"{len(a)} tổng · {sum(x.enabled for x in a)} đang bật · {len(a)-len({x.comment_text for x in a})} trùng");self._invalidate();self._summary()
    def generate_preview(self):
        if self._preview_busy:return
        self._preview_busy=True;self.preview_button.set_loading(True);self.preview_status.setText("Đang tạo preview...")
        try:
            posts=[p for p in self.posts if p["post_id"] in self.selected_post_ids]
            if not posts:self.preview_status.setText("Hãy tải và chọn ít nhất một bài viết trước khi tạo preview.");return
            comments={p["post_id"]:select_comments(self.items(),3 if self.three.isChecked() else 1,"sequential" if self.sequential_mode.isChecked() else "random") for p in posts};self.preview_comments=comments;self._render_preview(posts);self.preview_status.setText(f"Đã tạo {sum(map(len,comments.values()))} comment cho {len(posts)} bài viết.");self._summary()
        except AutoCommentValidationError as e:self.preview_status.setText(str(e))
        finally:self._preview_busy=False;self.preview_button.set_loading(False)
    def _render_preview(self,posts):
        self.preview.blockSignals(True);self.preview.setRowCount(0)
        for p in posts:
            r=self.preview.rowCount();self.preview.insertRow(r);self.preview.setItem(r,0,self._check_item());target=TARGET_LABELS.get(self.target_states.get(p["post_id"],{}).get("status",""),"Chưa xác minh")
            for c,v in enumerate((p["post_id"],p.get("post_type",""),p.get("published_at",""),str(len(self.preview_comments.get(p["post_id"],[]))),"\n".join(self.preview_comments.get(p["post_id"],[])),target,"PREVIEW"),1):self.preview.setItem(r,c,QTableWidgetItem(v))
        self.preview.blockSignals(False)
    def save_campaign(self):
        selected=self.page.currentData();posts=[p for p in self.posts if p["post_id"] in self.preview_comments and self._preview_post_selected(p["post_id"])]
        if not selected:self.preview_status.setText("Hãy chọn Fanpage trước khi lưu campaign.");return
        if not posts:self.preview_status.setText("Hãy tạo preview và chọn ít nhất một bài viết trước khi lưu.");return
        v={"name":self.campaign_name.text() or "Auto Comment draft","page_id":selected[1],"start_date":self.start_date.text(),"end_date":self.end_date.text(),"content_type":self.content_type.currentText(),"selection_mode":"sequential" if self.sequential_mode.isChecked() else "random","count":3 if self.three.isChecked() else 1,"prevent_duplicates":self.prevent_duplicates.isChecked()};self.campaign_id=self.service.save_preview(v,posts,{p["post_id"]:self.preview_comments[p["post_id"]] for p in posts},self.campaign_id);self.refresh_libraries();self.preview_status.setText("Đã lưu campaign. Chưa có comment nào được đăng Facebook.")
    def open_campaign(self):
        i=self.campaign_picker.currentData()
        if not i:return
        c,rows=self.service.load_campaign_preview(i);self.campaign_id=i;self.campaign_name.setText(c["name"]);idx=self.page.findData(next((d for d in (self.page.itemData(j) for j in range(self.page.count())) if d and d[1]==c["page_id"]),None))
        if idx>=0:self.page.setCurrentIndex(idx)
        self.start_date.setDate(date.fromisoformat(c["post_start_date"]));self.end_date.setDate(date.fromisoformat(c["post_end_date"]));self.content_type.setCurrentText(c["content_type"]);self.three.setChecked(c["comments_per_post"]==3);self.one.setChecked(c["comments_per_post"]!=3);self.sequential_mode.setChecked(c["selection_mode"]=="sequential");self.random_mode.setChecked(c["selection_mode"]!="sequential");self.prevent_duplicates.setChecked(bool(c["prevent_duplicates"]));grouped={}
        for row in rows:grouped.setdefault(row["post_id"],[]).append(row)
        self.posts=[{"post_id":pid,"post_type":items[0]["post_type"],"published_at":items[0]["published_at"]} for pid,items in grouped.items()];self.selected_post_ids=set(grouped);self.preview_comments={pid:[x["comment_text"] for x in items] for pid,items in grouped.items()};self._restore_target_states();self._render_posts();self._render_preview(self.posts);self.preview_status.setText(f"Đã mở campaign {c['name']}.")
    def _all_preview(self,on):
        for r in range(self.preview.rowCount()):self.preview.item(r,0).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
    def _preview_post_selected(self,pid):return any(self.preview.item(r,1).text()==pid and self.preview.item(r,0).checkState()==Qt.CheckState.Checked for r in range(self.preview.rowCount()))
    def _preview_changed(self,item):
        if item.column()==0:self.preview_status.setText(f"Preview: {sum(self._preview_post_selected(self.preview.item(r,1).text()) for r in range(self.preview.rowCount()))} bài viết được chọn.");self._summary()
    def _page_changed(self,*_):self.posts=[];self.selected_post_ids.clear();self.target_states={};self._render_posts();self._invalidate()
    def _invalidate(self,*_):
        if self.preview_comments:self.preview_status.setText("Cấu hình đã thay đổi; hãy tạo lại preview.")
    def _summary(self):
        if hasattr(self,"_workflow_step") and hasattr(self,"summary"):self._workflow_counts()
    def _send_status(self,text):
        self.preview_status.setText(text);self.test_status.setText(text)
    def _refresh_test_target(self,*_):
        if not hasattr(self,"test_target_info"):return
        selected=self.page.currentData();posts=[p for p in self.posts if p["post_id"] in self.selected_post_ids]
        if len(posts)!=1 or not selected:
            self.test_target_info.setText("Chọn đúng một Post hoặc Reel ở Bước 1 để gửi thử.");self.publish_one_button.setEnabled(False);return
        p=posts[0];state=self.target_states.get(p["post_id"],{});status=state.get("status","TARGET_UNVERIFIED");target=state.get("target_id") or "Chưa có"
        self.test_target_info.setText(f"Fanpage: {self.page.currentText()}\nPage ID: {selected[1]}\nLoại: {p.get('post_type','')}\nNguồn: {p['post_id']}\nTarget comment: {target}\nXác minh: {TARGET_LABELS.get(status,status)}\nĐăng lúc: {p.get('published_at','')}")
        self.publish_one_button.setEnabled(status=="READY" and bool(self.manual_comment.text().strip()))
    def refresh_history(self):
        if not hasattr(self,"history_table"):return
        page=self.history_page.currentData();type_filter=self.history_type.currentText() if self.history_type.currentIndex() else None;status=self.history_status.currentText() if self.history_status.currentIndex() else None
        rows=self.comment_service.submission_history(project_id=page[0] if page else None,page_id=page[1] if page else None,target_type=type_filter,status=status,since=self.history_from.text().strip() or None,until=self.history_to.text().strip() or None)
        self.history_table.setRowCount(0)
        for entry in rows:
            row=self.history_table.rowCount();self.history_table.insertRow(row);publication="PUBLISHED" if entry["status"]=="SUCCESS" else entry["status"]
            values=(entry["created_at"],f"{entry.get('page_name') or 'Không rõ'} · {entry['page_id']}",entry.get("target_type") or "Không rõ",entry["source_id"],entry["target_id"],entry["comment_text"],entry.get("remote_comment_id") or "",f"{publication} · {entry['verification_status']}",entry.get("error_category") or "", "Không có liên kết trực tiếp")
            for column,value in enumerate(values):self.history_table.setItem(row,column,QTableWidgetItem(str(value)))
    def copy_history_comment_id(self):
        row=self.history_table.currentRow();item=self.history_table.item(row,6) if row>=0 else None
        if item and item.text():QApplication.clipboard().setText(item.text());self.test_status.setText("Đã sao chép Comment ID.")
        else:self.test_status.setText("Hãy chọn một dòng có Comment ID.")
    def confirm_publish_one(self):
        selected=self.page.currentData();posts=[p for p in self.posts if p["post_id"] in self.selected_post_ids];text=self.manual_comment.text().strip()
        if len(posts)!=1 or not selected or not text or self.target_states.get(posts[0]["post_id"],{}).get("status")!="READY":self._send_status("Chọn đúng một Reel hoặc bài viết có target đã xác minh và nhập một comment trước khi gửi.");return
        p=posts[0];target=self.target_states[p["post_id"]]["target_id"];reel_id=p.get("reel_id") or p["post_id"]
        if QMessageBox.question(self,"Xác nhận gửi 1 comment",f"Fanpage: {self.page.currentText()}\nReel/bài viết: {reel_id}\nTarget comment: {target}\nNội dung:\n{text}\n\nGửi đúng một comment này?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:self._send_status("Đã hủy gửi comment.");return
        self._publish_confirmed(selected,p,target,text)
    def _publish_confirmed(self,selected,p,target,text):
        if self._publish_worker is not None:return
        project_id,page_id=selected;path=self.database.path;self.publish_one_button.set_loading(True);self._send_status("Đang gửi 1 comment đã xác nhận…")
        def operation():
            db=Database(path)
            try:return AutoCommentCommentService(db).publish_one(project_id,page_id,p["post_id"],target,text,confirmed=True)
            finally:db.close()
        worker=FacebookRequestWorker(operation,self);self._publish_worker=worker
        def succeeded(result):self._send_status(f"Đã gửi comment {result['comment_id']} ({result['verification_status']})." if result["status"]=="SUCCESS" else f"Không gửi lại tự động: {result['message']}");self.refresh_history()
        def failed(message):self._send_status(f"Không thể gửi comment: {message}");self.refresh_history()
        def finished():
            if self._publish_worker is worker:self._publish_worker=None;self.publish_one_button.set_loading(False)
            worker.deleteLater()
        worker.succeeded.connect(succeeded);worker.failed.connect(failed);worker.finished.connect(finished);worker.start()
    def closeEvent(self,event):
        self._set_posts_loading(False)
        for name in ("_posts_worker","_target_worker","_publish_worker"):
            worker=getattr(self,name);setattr(self,name,None)
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

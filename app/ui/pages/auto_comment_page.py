from __future__ import annotations
from datetime import date
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget
from app.services.auto_comment_service import AutoCommentService, AutoCommentValidationError, CommentItem, parse_comment_csv, select_comments


class AutoCommentPage(QWidget):
    pageManagementRequested = Signal()
    def __init__(self, database, parent=None):
        super().__init__(parent); self.service=AutoCommentService(database); self.library_id=None; self.posts=[]; self.selected_post_ids=set(); self.preview_comments={}; self.setAcceptDrops(True)
        root=QVBoxLayout(self); root.setContentsMargins(24,18,24,18); root.addWidget(QLabel("Auto Comment", objectName="title")); root.addWidget(QLabel("Lập kế hoạch comment cục bộ. Phase UI-2 không gửi comment lên Facebook."))
        self.tabs=QTabWidget(); self.tabs.addTab(self._posts(),"Bài viết"); self.tabs.addTab(self._library(),"Thư viện CSV"); self.tabs.addTab(self._campaign(),"Chiến dịch & Preview"); root.addWidget(self.tabs,1); self.refresh_pages(); self.refresh_libraries()
    def _posts(self):
        w=QWidget(); l=QVBoxLayout(w); box=QGroupBox("Fanpage và bộ lọc"); f=QFormLayout(box); self.page=QComboBox(); self.page.currentIndexChanged.connect(self._page_changed); manage=QPushButton("Quản lý Fanpage"); manage.clicked.connect(self.pageManagementRequested.emit); pr=QHBoxLayout(); pr.addWidget(self.page,1); pr.addWidget(manage)
        self.start_date,self.end_date=QDateEdit(),QDateEdit()
        for x in (self.start_date,self.end_date): x.setCalendarPopup(True); x.setDisplayFormat("yyyy-MM-dd"); x.dateChanged.connect(self._invalidate)
        self.start_date.setDate(date.today().replace(day=1)); self.end_date.setDate(date.today()); self.content_type=QComboBox(); self.content_type.addItems(["Tất cả","Reels","Bài viết thường"]); self.content_type.currentIndexChanged.connect(self._invalidate); self.load_posts_button=QPushButton("Tải bài viết"); self.load_posts_button.setObjectName("primary"); self.load_posts_button.clicked.connect(self.load_posts)
        f.addRow("Fanpage",pr); f.addRow("Từ ngày",self.start_date); f.addRow("Đến ngày",self.end_date); f.addRow("Loại",self.content_type); f.addRow(self.load_posts_button); l.addWidget(box)
        r=QHBoxLayout(); a=QPushButton("Chọn tất cả"); a.clicked.connect(lambda:self._all_posts(True)); b=QPushButton("Bỏ chọn tất cả"); b.clicked.connect(lambda:self._all_posts(False)); self.post_status=QLabel("Chưa tải bài viết."); r.addWidget(a);r.addWidget(b);r.addWidget(self.post_status,1);l.addLayout(r)
        self.posts_table=QTableWidget(0,5);self.posts_table.setHorizontalHeaderLabels(["Chọn","Published At","Loại","Post ID","Trạng thái"]);self.posts_table.horizontalHeader().setStretchLastSection(True);self.posts_table.setColumnWidth(0,54);self.posts_table.itemChanged.connect(self._post_changed);l.addWidget(self.posts_table,1);l.addWidget(QLabel("Phase 2A sẽ bổ sung đồng bộ Facebook. Chỉ bài publish cục bộ có Post ID/timestamp tin cậy được hiển thị."));return w
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
        r=QHBoxLayout();
        for text,fn in (("Tạo preview",self.generate_preview),("Tạo lại preview",self.generate_preview),("Lưu campaign",self.save_campaign),("Chọn tất cả",lambda:self._all_preview(True)),("Bỏ chọn",lambda:self._all_preview(False))):x=QPushButton(text);x.clicked.connect(fn);r.addWidget(x)
        l.addLayout(r);self.preview=QTableWidget(0,7);self.preview.setHorizontalHeaderLabels(["Chọn","Post","Loại","Published At","Số comment","Comments","Trạng thái"]);self.preview.setWordWrap(True);self.preview.horizontalHeader().setStretchLastSection(True);self.preview.setColumnWidth(0,54);l.addWidget(self.preview,1);self.preview_status=QLabel("Chưa có preview.");l.addWidget(self.preview_status);x=QPushButton("Đăng comment — sẽ triển khai ở Phase 2B.");x.setEnabled(False);l.addWidget(x);return w
    def refresh_pages(self):
        old=self.page.currentData();self.page.blockSignals(True);self.page.clear();self.page.addItem("Chọn Fanpage",None)
        for p in self.service.list_pages():self.page.addItem(p["page_name"],p["page_id"])
        self.page.setCurrentIndex(max(0,self.page.findData(old)));self.page.blockSignals(False);self._summary()
    def refresh_libraries(self):
        self.library_picker.blockSignals(True);self.library_picker.clear();self.library_picker.addItem("Mở thư viện đã lưu…",None)
        for x in self.service.list_libraries():self.library_picker.addItem(x["name"],x["id"])
        self.library_picker.blockSignals(False);self.campaign_picker.blockSignals(True);self.campaign_picker.clear();self.campaign_picker.addItem("Mở campaign đã lưu…",None)
        for x in self.service.list_campaigns():self.campaign_picker.addItem(x["name"],x["id"])
        self.campaign_picker.blockSignals(False);self._summary()
    def load_posts(self):
        if not self.page.currentData():self.post_status.setText("Hãy chọn Fanpage trước.");return
        kind={"Tất cả":"all","Reels":"reels","Bài viết thường":"posts"}[self.content_type.currentText()];self.posts=self.service.local_posts(self.page.currentData(),self.start_date.text(),self.end_date.text(),kind);self.selected_post_ids={p["post_id"] for p in self.posts};self._render_posts();self._invalidate()
    def _render_posts(self):
        self.posts_table.blockSignals(True);self.posts_table.setRowCount(0)
        for p in self.posts:
            row=self.posts_table.rowCount();self.posts_table.insertRow(row);q=QTableWidgetItem();q.setCheckState(Qt.CheckState.Checked if p["post_id"] in self.selected_post_ids else Qt.CheckState.Unchecked);self.posts_table.setItem(row,0,q)
            for c,v in enumerate((p["published_at"],p["post_type"],p["post_id"],"Sẵn sàng"),1):self.posts_table.setItem(row,c,QTableWidgetItem(str(v)))
        self.posts_table.blockSignals(False);self.post_status.setText(f"{len(self.posts)} bài · {len(self.selected_post_ids)} đã chọn" if self.posts else "Không có bài phù hợp trong dữ liệu cục bộ.");self._summary()
    def _post_changed(self,i):
        if i.column()==0:
            pid=self.posts_table.item(i.row(),3).text();(self.selected_post_ids.add if i.checkState()==Qt.CheckState.Checked else self.selected_post_ids.discard)(pid);self._render_posts();self._invalidate()
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
        except AutoCommentValidationError as e:QMessageBox.warning(self,"Thư viện",str(e))
    def open_library(self):
        if i:=self.library_picker.currentData():self.library_id=i;self.library_name.setText(self.library_picker.currentText());self.set_items(self.service.load_library(i))
    def _filter(self,t):
        for r in range(self.comments.rowCount()):self.comments.setRowHidden(r,bool(t and t.casefold() not in self.comments.item(r,2).text().casefold()))
    def _changed(self,*_):
        a=self.items();self.csv_status.setText(f"{len(a)} tổng · {sum(x.enabled for x in a)} đang bật · {len(a)-len({x.comment_text for x in a})} trùng");self._invalidate()
    def generate_preview(self):
        posts=[p for p in self.posts if p["post_id"] in self.selected_post_ids]
        try:self.preview_comments={p["post_id"]:select_comments(self.items(),3 if self.three.isChecked() else 1,"sequential" if self.sequential_mode.isChecked() else "random") for p in posts}
        except AutoCommentValidationError as e:self.preview_status.setText(str(e));return
        self._render_preview(posts);self.preview_status.setText(f"Preview chưa lưu: {len(posts)} bài, {sum(map(len,self.preview_comments.values()))} comment.")
    def _render_preview(self,posts):
        self.preview.setRowCount(0)
        for p in posts:
            r=self.preview.rowCount();self.preview.insertRow(r);x=QTableWidgetItem();x.setCheckState(Qt.CheckState.Checked);self.preview.setItem(r,0,x)
            for c,v in enumerate((p["post_id"],p["post_type"],p["published_at"],str(len(self.preview_comments.get(p["post_id"],[]))),"\n".join(self.preview_comments.get(p["post_id"],[])),"PREVIEW"),1):self.preview.setItem(r,c,QTableWidgetItem(v))
    def save_campaign(self):
        posts=[p for p in self.posts if p["post_id"] in self.preview_comments]
        if not posts:self.preview_status.setText("Hãy tạo preview trước khi lưu.");return
        v={"name":self.campaign_name.text() or "Auto Comment draft","page_id":self.page.currentData(),"start_date":self.start_date.text(),"end_date":self.end_date.text(),"content_type":self.content_type.currentText(),"selection_mode":"sequential" if self.sequential_mode.isChecked() else "random","count":3 if self.three.isChecked() else 1,"prevent_duplicates":self.prevent_duplicates.isChecked()};self.service.save_preview(v,posts,self.preview_comments);self.refresh_libraries();self.preview_status.setText("Đã lưu campaign.")
    def open_campaign(self):
        if not (i:=self.campaign_picker.currentData()):return
        c,rows=self.service.load_campaign_preview(i);self.campaign_name.setText(c["name"]);g={}
        for x in rows:g.setdefault(x["post_id"],[]).append(x)
        self.preview_comments={k:[x["comment_text"] for x in v] for k,v in g.items()};self._render_preview([{"post_id":k,"post_type":v[0]["post_type"],"published_at":v[0]["published_at"]} for k,v in g.items()]);self.preview_status.setText(f"Đã mở campaign {c['name']}.")
    def _all_preview(self,on):
        for r in range(self.preview.rowCount()):self.preview.item(r,0).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
    def _page_changed(self,*_):self.posts=[];self.selected_post_ids.clear();self._render_posts();self._invalidate()
    def _invalidate(self,*_):
        if getattr(self,"preview_comments",None):self.preview_status.setText("Cấu hình đã thay đổi; hãy tạo lại preview.")
    def _summary(self):
        if hasattr(self,"summary"):self.summary.setText(f"Fanpage: {self.page.currentText()} · Bài viết: {len(self.selected_post_ids)} · Thư viện: {self.library_name.text()}")
    def dragEnterEvent(self,e):
        if e.mimeData().hasUrls() and any(x.toLocalFile().lower().endswith('.csv') for x in e.mimeData().urls()):e.acceptProposedAction()
    def dropEvent(self,e):
        for u in e.mimeData().urls():
            if Path(u.toLocalFile()).suffix.lower()=='.csv':self.load_csv(Path(u.toLocalFile()));e.acceptProposedAction();return

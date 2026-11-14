from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLineEdit,QTableWidget,QTableWidgetItem,QDialog,QFormLayout,QPlainTextEdit,QCheckBox,QDialogButtonBox,QMessageBox,QComboBox,QLabel
from app.services.auto_comment_template_service import AutoCommentTemplateService,TemplateError
from app.services.ai_comment_service import generate_text_templates
from app.ui.facebook_worker import FacebookRequestWorker
from app.ui.widgets.loading_button import LoadingButton

class CommentTemplateLibraryWidget(QWidget):
 def __init__(self,database,project_id=None,page_id=None,parent=None):
  super().__init__(parent);self.service=AutoCommentTemplateService(database);self.project_id=project_id;self.page_id=page_id;layout=QVBoxLayout(self);bar=QHBoxLayout();self.search=QLineEdit(placeholderText="Tìm mẫu…");self.search.textChanged.connect(self.reload);ai=QPushButton("✨ AI tạo mẫu text");ai.clicked.connect(self.open_ai_generator);add=QPushButton("Thêm mẫu");add.clicked.connect(self.create_template_dialog);edit=QPushButton("Sửa mẫu");edit.clicked.connect(self.edit_selected_template);delete=QPushButton("Xóa mẫu");delete.clicked.connect(self.delete);refresh=QPushButton("Làm mới");refresh.clicked.connect(self.reload)
  for x in (ai,add,edit,delete,refresh,self.search):bar.addWidget(x)
  layout.addLayout(bar);self.table=QTableWidget(0,6);self.table.setHorizontalHeaderLabels(["Bật","Tên mẫu","Phân loại","Nội dung","Placeholder","Cập nhật"]);self.table.horizontalHeader().setStretchLastSection(True);self.table.itemChanged.connect(self.toggle);layout.addWidget(self.table);self.reload()
 def set_context(self,project_id,page_id): self.project_id=project_id;self.page_id=page_id;self.reload()
 def reload(self,*_):
  self.table.blockSignals(True);self.table.setRowCount(0)
  if self.project_id and self.page_id:
   for item in self.service.list_templates(self.project_id,self.page_id):
    if self.search.text().casefold() not in (item['name']+item['body_text']+(item['category'] or '')).casefold():continue
    r=self.table.rowCount();self.table.insertRow(r);check=QTableWidgetItem();check.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable);check.setCheckState(Qt.CheckState.Checked if item['enabled'] else Qt.CheckState.Unchecked);check.setData(Qt.ItemDataRole.UserRole,item['id']);self.table.setItem(r,0,check)
    for c,key in enumerate(('name','category','body_text'),1):self.table.setItem(r,c,QTableWidgetItem(item[key] or ''))
    self.table.setItem(r,4,QTableWidgetItem("{affiliate_url}" if '{affiliate_url}' in item['body_text'] else '—'));self.table.setItem(r,5,QTableWidgetItem(item['updated_at']))
  self.table.blockSignals(False)
 def selected(self):
  row=self.table.currentRow();return self.table.item(row,0).data(Qt.ItemDataRole.UserRole) if row>=0 else None
 def toggle(self,item):
  if item.column()==0:
   try:self.service.set_template_enabled(self.project_id,self.page_id,item.data(Qt.ItemDataRole.UserRole),item.checkState()==Qt.CheckState.Checked)
   except TemplateError:self.reload()
 def create_template_dialog(self): self._editor(None)
 def edit_selected_template(self):
  tid=self.selected()
  if tid:self._editor(tid)
 def _editor(self,tid):
  old=self.service.get_template(self.project_id,self.page_id,tid) if tid else {};d=QDialog(self);d.setWindowTitle("Mẫu comment");f=QFormLayout(d);name=QLineEdit(old.get('name',''));cat=QLineEdit(old.get('category','') or '');body=QPlainTextEdit(old.get('body_text',''));enabled=QCheckBox("Bật mẫu");enabled.setChecked(old.get('enabled',True));f.addRow("Tên mẫu",name);f.addRow("Phân loại",cat);f.addRow("Nội dung",body);f.addRow(enabled);f.addRow("Có thể dùng {affiliate_url} để đánh dấu vị trí chèn link ở Phase 3.");buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);f.addRow(buttons);buttons.rejected.connect(d.reject)
  def save():
   try:
    if tid:self.service.update_template(self.project_id,self.page_id,tid,name.text(),body.toPlainText(),cat.text(),enabled.isChecked())
    else:self.service.create_template(self.project_id,self.page_id,name.text(),body.toPlainText(),cat.text(),enabled.isChecked())
    d.accept();self.reload()
   except TemplateError as exc:QMessageBox.warning(d,"Mẫu comment",str(exc))
  buttons.accepted.connect(save);d.exec()
 def delete(self):
  tid=self.selected()
  if tid and QMessageBox.question(self,"Xóa mẫu","Xóa mẫu đã chọn?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes:
   try:self.service.delete_template(self.project_id,self.page_id,tid);self.reload()
   except TemplateError:pass
 def open_ai_generator(self):
  if not self.project_id or not self.page_id:return
  d=QDialog(self);d.setWindowTitle("✨ AI tạo mẫu text");d.setMinimumSize(900,650);layout=QVBoxLayout(d);form=QFormLayout();context=QPlainTextEdit();category=QLineEdit();tone=QComboBox();tone.addItems(["Tự nhiên","Thân thiện","Ngắn gọn"]);count=QComboBox();count.addItems(["3","5","10"]);placeholder=QCheckBox("Có placeholder {affiliate_url}")
  for label,field in (("Chủ đề / ngữ cảnh",context),("Phân loại",category),("Phong cách",tone),("Số mẫu",count)):form.addRow(label,field)
  layout.addLayout(form);layout.addWidget(placeholder);results=QTableWidget(0,6);results.setHorizontalHeaderLabels(["Chọn","Tên mẫu","Phân loại","Nội dung mẫu","Placeholder","Cảnh báo"]);results.horizontalHeader().setStretchLastSection(True);status=QLabel();go=LoadingButton("✨ Xử lý AI")
  def run():
   if go.is_loading:return
   go.set_loading(True);status.setText("AI đang tạo mẫu text…");worker=FacebookRequestWorker(lambda:generate_text_templates(context.toPlainText(),category.text(),int(count.currentText()),tone.currentText(),placeholder.isChecked()),d)
   def ok(items):
    results.setRowCount(0)
    for item in items:
     r=results.rowCount();results.insertRow(r);check=QTableWidgetItem();check.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable);check.setCheckState(Qt.CheckState.Unchecked);check.setData(Qt.ItemDataRole.UserRole,item['id']);results.setItem(r,0,check);results.setItem(r,1,QTableWidgetItem(item['name']));results.setItem(r,2,QTableWidgetItem(item['category']));results.setItem(r,3,QTableWidgetItem(item['body_text']));results.setItem(r,4,QTableWidgetItem("Có" if item['has_affiliate_placeholder'] else "—"));results.setItem(r,5,QTableWidgetItem(""))
    status.setText(f"Đã tạo {len(items)} mẫu; hãy chọn để duyệt.")
   def fail(msg):status.setText(msg)
   def done():go.set_loading(False);worker.deleteLater()
   worker.succeeded.connect(ok);worker.failed.connect(fail);worker.finished.connect(done);worker.start()
  go.clicked.connect(run);close=QPushButton("Đóng");close.clicked.connect(d.reject);layout.addWidget(go);layout.addWidget(results,1);layout.addWidget(status);layout.addWidget(close);d.exec()

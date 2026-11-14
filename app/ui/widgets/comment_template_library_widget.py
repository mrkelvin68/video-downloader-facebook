from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLineEdit,QTableWidget,QTableWidgetItem,QDialog,QFormLayout,QPlainTextEdit,QCheckBox,QDialogButtonBox,QMessageBox
from app.services.auto_comment_template_service import AutoCommentTemplateService,TemplateError

class CommentTemplateLibraryWidget(QWidget):
 def __init__(self,database,project_id=None,page_id=None,parent=None):
  super().__init__(parent);self.service=AutoCommentTemplateService(database);self.project_id=project_id;self.page_id=page_id;layout=QVBoxLayout(self);bar=QHBoxLayout();self.search=QLineEdit(placeholderText="Tìm mẫu…");self.search.textChanged.connect(self.reload);add=QPushButton("Thêm mẫu");add.clicked.connect(self.edit);edit=QPushButton("Sửa mẫu");edit.clicked.connect(self.edit);delete=QPushButton("Xóa mẫu");delete.clicked.connect(self.delete);refresh=QPushButton("Làm mới");refresh.clicked.connect(self.reload)
  for x in (add,edit,delete,refresh,self.search):bar.addWidget(x)
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
 def edit(self):
  tid=self.selected();old=self.service.get_template(self.project_id,self.page_id,tid) if tid else {};d=QDialog(self);d.setWindowTitle("Mẫu comment");f=QFormLayout(d);name=QLineEdit(old.get('name',''));cat=QLineEdit(old.get('category','') or '');body=QPlainTextEdit(old.get('body_text',''));enabled=QCheckBox("Bật mẫu");enabled.setChecked(old.get('enabled',True));f.addRow("Tên mẫu",name);f.addRow("Phân loại",cat);f.addRow("Nội dung",body);f.addRow(enabled);f.addRow("Có thể dùng {affiliate_url} để đánh dấu vị trí chèn link ở Phase 3.");buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);f.addRow(buttons);buttons.rejected.connect(d.reject)
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

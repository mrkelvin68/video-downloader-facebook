from __future__ import annotations
import re, uuid
from datetime import UTC, datetime

class TemplateError(ValueError): pass

def _now(): return datetime.now(UTC).isoformat(timespec="seconds")
def _validate(name, body):
    if not str(name).strip(): raise TemplateError("Tên mẫu không được để trống.")
    if not str(body).strip(): raise TemplateError("Nội dung mẫu không được để trống.")
    text = str(body)
    placeholders = re.findall(r"{([^{}]*)}", text)
    if any(item != "affiliate_url" for item in placeholders) or text.count("{") != len(placeholders) or text.count("}") != len(placeholders):
        raise TemplateError("Chỉ hỗ trợ placeholder {affiliate_url}.")

class AutoCommentTemplateService:
    def __init__(self, database): self.database=database
    def create_template(self, project_id, page_id, name, body_text, category=None, enabled=True):
        _validate(name,body_text); now=_now(); template_id=str(uuid.uuid4())
        try:
            with self.database.connection:self.database.connection.execute("INSERT INTO auto_comment_templates VALUES (?,?,?,?,?,?,?,?,?)",(template_id,project_id,page_id,name.strip(),body_text,category or None,int(enabled),now,now))
        except Exception as exc: raise TemplateError("Tên mẫu đã tồn tại trong Fanpage này.") from exc
        return template_id
    def list_templates(self, project_id,page_id): return [dict(x) for x in self.database.connection.execute("SELECT * FROM auto_comment_templates WHERE project_id=? AND page_id=? ORDER BY updated_at DESC,name",(project_id,page_id))]
    def get_template(self, project_id,page_id,template_id):
        row=self.database.connection.execute("SELECT * FROM auto_comment_templates WHERE id=? AND project_id=? AND page_id=?",(template_id,project_id,page_id)).fetchone()
        if row is None: raise TemplateError("Không tìm thấy mẫu trong Fanpage đang chọn.")
        return dict(row)
    def update_template(self, project_id,page_id,template_id,name,body_text,category=None,enabled=True):
        _validate(name,body_text)
        try:
            with self.database.connection:
                cur=self.database.connection.execute("UPDATE auto_comment_templates SET name=?,body_text=?,category=?,enabled=?,updated_at=? WHERE id=? AND project_id=? AND page_id=?",(name.strip(),body_text,category or None,int(enabled),_now(),template_id,project_id,page_id))
        except Exception as exc: raise TemplateError("Tên mẫu đã tồn tại trong Fanpage này.") from exc
        if cur.rowcount!=1: raise TemplateError("Không tìm thấy mẫu trong Fanpage đang chọn.")
    def delete_template(self,project_id,page_id,template_id):
        with self.database.connection: cur=self.database.connection.execute("DELETE FROM auto_comment_templates WHERE id=? AND project_id=? AND page_id=?",(template_id,project_id,page_id))
        if cur.rowcount!=1: raise TemplateError("Không tìm thấy mẫu trong Fanpage đang chọn.")
    def set_template_enabled(self,project_id,page_id,template_id,enabled):
        row=self.get_template(project_id,page_id,template_id);self.update_template(project_id,page_id,template_id,row["name"],row["body_text"],row["category"],enabled)
    def create_templates(self, project_id, page_id, templates):
        """Atomic batch create; validation/conflicts roll back every selected draft."""
        if not str(project_id or "").strip() or not str(page_id or "").strip():
            raise TemplateError("Hãy chọn Fanpage trước khi lưu mẫu.")
        if not templates:
            raise TemplateError("Hãy chọn ít nhất một mẫu để lưu.")
        ids = []
        now = _now()
        seen_names: set[str] = set()
        try:
            with self.database.connection:
                for item in templates:
                    name = str(item.get("name", ""))
                    body_text = str(item.get("body_text", ""))
                    _validate(name, body_text)
                    clean_name = name.strip()
                    norm_name = clean_name.casefold()
                    if norm_name in seen_names:
                        raise TemplateError(f"Tên mẫu '{clean_name}' bị trùng trong danh sách chọn.")
                    seen_names.add(norm_name)
                    existing = self.database.connection.execute(
                        "SELECT 1 FROM auto_comment_templates WHERE project_id=? AND page_id=? AND LOWER(name)=LOWER(?)",
                        (project_id, page_id, clean_name),
                    ).fetchone()
                    if existing is not None:
                        raise TemplateError(f"Tên mẫu '{clean_name}' đã tồn tại trong Fanpage này.")
                    tid = str(uuid.uuid4())
                    category = str(item.get("category") or "").strip() or None
                    enabled = int(bool(item.get("enabled", True)))
                    self.database.connection.execute(
                        "INSERT INTO auto_comment_templates VALUES (?,?,?,?,?,?,?,?,?)",
                        (tid, project_id, page_id, clean_name, body_text, category, enabled, now, now),
                    )
                    ids.append(tid)
        except TemplateError:
            raise
        except Exception as exc:
            raise TemplateError("Không thể lưu mẫu: tên mẫu trùng hoặc dữ liệu không hợp lệ.") from exc
        return ids

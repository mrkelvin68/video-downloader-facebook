import tempfile, unittest
from pathlib import Path
from app.database.connection import Database
from app.services.auto_comment_template_service import AutoCommentTemplateService, TemplateError

class TemplateTests(unittest.TestCase):
 def setUp(self): self.tmp=tempfile.TemporaryDirectory();self.db=Database(Path(self.tmp.name)/"x.db");self.s=AutoCommentTemplateService(self.db)
 def tearDown(self): self.db.close();self.tmp.cleanup()
 def test_crud_unicode_and_isolation(self):
  tid=self.s.create_template("p","a","Mẫu Việt","Xem {affiliate_url}","sale")
  self.assertEqual(self.s.get_template("p","a",tid)["body_text"],"Xem {affiliate_url}")
  self.s.update_template("p","a",tid,"Mẫu mới","Xin chào",enabled=False);self.assertFalse(self.s.get_template("p","a",tid)["enabled"])
  self.assertEqual(self.s.list_templates("p","b"),[])
  with self.assertRaises(TemplateError): self.s.get_template("p","b",tid)
  self.s.delete_template("p","a",tid);self.assertEqual(self.s.list_templates("p","a"),[])
 def test_validation_and_duplicate(self):
  with self.assertRaises(TemplateError): self.s.create_template("p","a","","x")
  with self.assertRaises(TemplateError): self.s.create_template("p","a","x","{bad}")
  self.s.create_template("p","a","x","nội dung")
  with self.assertRaises(TemplateError): self.s.create_template("p","a","x","khác")
 def test_atomic_batch_rolls_back_duplicate(self):
  with self.assertRaises(TemplateError): self.s.create_templates("p","a",[{"name":"a","body_text":"x"},{"name":"a","body_text":"y"}])
  self.assertEqual(self.s.list_templates("p","a"),[])

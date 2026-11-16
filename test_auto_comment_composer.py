from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.database.connection import Database
from app.services.auto_comment_affiliate_link_service import AutoCommentAffiliateLinkService
from app.services.auto_comment_composer_service import AutoCommentComposerService, CommentComposerError
from app.services.auto_comment_service import AutoCommentService, CommentItem
from app.services.auto_comment_template_service import AutoCommentTemplateService
from app.services.facebook_publishing_service import FacebookPublishingService
from app.services.project_service import ProjectService
from app.ui.widgets.comment_composer_widget import CommentComposerWidget


class CommentMixerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.db = Database(Path(self.tmp.name) / "mixer.sqlite3")
        project = ProjectService(self.db).create("Project", "p", str(Path(self.tmp.name) / "v"))
        with self.db.connection: self.db.connection.execute("UPDATE projects SET uuid='proj-1' WHERE uuid=?", (project,))
        FacebookPublishingService(self.db).save_pages("proj-1", [{"id":"page-a","name":"A"},{"id":"page-b","name":"B"}])
        self.templates = AutoCommentTemplateService(self.db); self.links = AutoCommentAffiliateLinkService(self.db); self.service = AutoCommentComposerService(self.db); self.libraries = AutoCommentService(self.db)
    def tearDown(self): self.db.close(); self.tmp.cleanup()
    def _seed(self, count=3):
        templates = [self.templates.create_template("proj-1", "page-a", f"Mẫu {i}", "Xem {affiliate_url}" if i == 0 else f"Nội dung {i}") for i in range(count)]
        links = [self.links.create_link("proj-1", "page-a", f"Link {i}", f"https://s.shopee.vn/item{i}?utm_source=fb") for i in range(3)]
        return templates, links
    def test_round_robin_and_combinations_preserve_links(self):
        templates, links = self._seed(10)
        drafts = self.service.mix_drafts("proj-1", "page-a", templates, links, limit=10)
        self.assertEqual(len(drafts), 10); self.assertEqual([d.affiliate_link_id for d in drafts[:4]], [links[0], links[1], links[2], links[0]])
        self.assertEqual(drafts[0].edited_text, "Xem https://s.shopee.vn/item0?utm_source=fb")
        self.assertEqual(drafts[1].edited_text, "Nội dung 1\nhttps://s.shopee.vn/item1?utm_source=fb")
        all_drafts = self.service.mix_drafts("proj-1", "page-a", templates, links, mode="all", limit=30)
        self.assertEqual(len(all_drafts), 30)
    def test_widget_select_save_once_and_page_isolation(self):
        templates, links = self._seed(); library = self.libraries.save_library("Đích", [CommentItem("Giữ nguyên")], project_id="proj-1", page_id="page-a")
        scope = [("proj-1", "page-a")]; widget = CommentComposerWidget(self.db, "proj-1", "page-a", "A", active_scope_provider=lambda:scope[0])
        self.assertEqual(widget.destination.currentData(), library)
        self.assertEqual(widget.results.columnWidth(0), 55)
        widget.template_toggle.click(); widget.link_toggle.click(); widget.mix(); self.assertEqual(widget.results.rowCount(), 3)
        widget.results.item(1, 1).setText("Đã sửa cuối cùng"); widget.results.item(2, 0).setCheckState(Qt.CheckState.Unchecked)
        widget.destination.setCurrentIndex(widget.destination.findData(library))
        with patch("app.services.auto_comment_comment_service.AutoCommentCommentService.publish_one") as publish:
            saved = widget.save_selected(); publish.assert_not_called()
        self.assertEqual(len(saved), 2); self.assertIsNone(widget.save_selected())
        rows = self.db.connection.execute("SELECT comment_text FROM auto_comment_library_items WHERE library_id=? ORDER BY sort_order", (library,)).fetchall()
        self.assertEqual([row[0] for row in rows], ["Giữ nguyên", rows[1][0], "Đã sửa cuối cùng"])
        scope[0] = ("proj-1", "page-b"); self.assertFalse(widget.save_button.isEnabled())
    def test_creates_library_when_page_has_none_and_preserves_full_text(self):
        templates, links = self._seed(1); seen=[]
        widget = CommentComposerWidget(self.db, "proj-1", "page-a", "A", saved_callback=seen.append)
        self.assertEqual(widget.destination.currentData(), "__new__")
        widget.template_toggle.click(); widget.link_toggle.click(); widget.mix(); widget.destination.setCurrentIndex(0)
        final = "Comment đầy đủ\nhttps://s.shopee.vn/item0?utm_source=fb"
        widget.results.item(0, 1).setText(final)
        ids = widget.save_selected()
        self.assertEqual(len(ids), 1); self.assertEqual(seen, [widget._saved_library_id])
        row = self.db.connection.execute("SELECT comment_text FROM auto_comment_library_items WHERE id=?", (ids[0],)).fetchone()
        self.assertEqual(row[0], final); self.assertTrue(widget.view_saved_button.isEnabled())
    def test_atomic_rollback_stale_sources_and_legacy_blocked(self):
        templates, links = self._seed(1); library = self.libraries.save_library("Đích", [CommentItem("old")], project_id="proj-1", page_id="page-a")
        drafts = self.service.mix_drafts("proj-1", "page-a", templates, links[:2], mode="all")
        self.db.connection.execute("CREATE TRIGGER fail_mixer BEFORE INSERT ON auto_comment_library_items WHEN NEW.library_id='{}' BEGIN SELECT RAISE(ABORT,'fail'); END".format(library))
        with self.assertRaises(CommentComposerError): self.service.save_selected_drafts(drafts, library, "proj-1", "page-a")
        self.assertEqual(self.db.connection.execute("SELECT COUNT(*) FROM auto_comment_library_items WHERE library_id=?", (library,)).fetchone()[0], 1)
        self.db.connection.execute("DROP TRIGGER fail_mixer")
        self.links.delete_link("proj-1", "page-a", links[0])
        with self.assertRaises(CommentComposerError): self.service.save_selected_drafts(drafts, library, "proj-1", "page-a")
        legacy = self.libraries.save_library("Cũ", [CommentItem("legacy")])
        with self.assertRaises(Exception): self.service.save_selected_drafts([drafts[1]], legacy, "proj-1", "page-a")

if __name__ == "__main__": unittest.main()

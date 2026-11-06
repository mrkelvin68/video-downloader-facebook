import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database.connection import Database
from app.ui.facebook_worker import FacebookRequestWorker
from app.ui.main_window import MainWindow


class _ThreadOwnedConnectionService:
    worker_thread_id = None

    def __init__(self, database):
        self.database = database

    def connect(self, project_id, token):
        type(self).worker_thread_id = threading.get_ident()
        with self.database.connection:
            self.database.connection.execute(
                "INSERT INTO facebook_pages(project_id,page_id,page_name,created_at,updated_at) VALUES (?,?,?,?,?)",
                (project_id, "page-1", "Page 1", "now", "now"),
            )
        return [{"id": "page-1", "name": "Page 1"}]


class SqliteThreadHotfixTests(unittest.TestCase):
    def test_connection_worker_creates_and_uses_thread_owned_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "projects.sqlite3")
            database = Database(path)
            try:
                with database.connection:
                    database.connection.execute(
                        "INSERT INTO projects(uuid,name,prefix,output_dir,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                        ("project-1", "Project", "p", str(Path(directory) / "videos"), "", "now", "now"),
                    )
                result = []
                errors = []
                worker = FacebookRequestWorker(
                    lambda: MainWindow._facebook_connection_in_worker(path, "project-1", "fresh-token", "connect")
                )
                worker.succeeded.connect(result.append)
                worker.failed.connect(errors.append)
                with patch("app.ui.main_window.FacebookConnectionService", _ThreadOwnedConnectionService):
                    worker.start()
                    self.assertTrue(worker.wait(5000))
                self.assertFalse(errors)
                self.assertNotEqual(_ThreadOwnedConnectionService.worker_thread_id, threading.get_ident())
                self.assertEqual(database.list_projects()[0]["uuid"], "project-1")
                self.assertEqual(database.connection.execute("SELECT page_id FROM facebook_pages").fetchone()[0], "page-1")
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()

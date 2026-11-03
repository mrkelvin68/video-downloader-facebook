import base64
import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import venv
import zipfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent


def create_minimal_pyside6_wheel(path: Path) -> None:
    files = {
        "PySide6/__init__.py": "__version__ = '6.5.0'\n",
        "PySide6/QtCore.py": "__version__ = '6.5.0'\n",
        "PySide6/QtWidgets.py": "",
        "PySide6-6.5.0.dist-info/METADATA": (
            "Metadata-Version: 2.1\nName: PySide6\nVersion: 6.5.0\n"
        ),
        "PySide6-6.5.0.dist-info/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: launcher-test\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for filename, contents in files.items():
        digest = base64.urlsafe_b64encode(
            hashlib.sha256(contents.encode("utf-8")).digest()
        ).rstrip(b"=").decode("ascii")
        writer.writerow((filename, f"sha256={digest}", len(contents.encode("utf-8"))))
    writer.writerow(("PySide6-6.5.0.dist-info/RECORD", "", ""))
    files["PySide6-6.5.0.dist-info/RECORD"] = record.getvalue()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename, contents in files.items():
            archive.writestr(filename, contents)


def copy_launchers(root: Path) -> None:
    for filename in ("install.py", "run.py", "start.bat"):
        shutil.copy2(REPOSITORY / filename, root / filename)


class LauncherIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        copy_launchers(self.root)
        self.wheel = self.root / "PySide6-6.5.0-py3-none-any.whl"
        create_minimal_pyside6_wheel(self.wheel)
        self.requirements = self.root / "requirements.txt"
        self.requirements.write_text(
            f"PySide6 @ {self.wheel.as_uri()}\n", encoding="utf-8"
        )
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.marker = self.root / "launch.json"
        self.database = self.root / "projects.sqlite3"
        connection = sqlite3.connect(self.database)
        connection.execute("CREATE TABLE user_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO user_data VALUES ('preserve me')")
        connection.commit()
        connection.close()
        self.database_snapshot = self.database.read_bytes()
        (self.root / "main.py").write_text(
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "Path(os.environ['LAUNCH_MARKER']).write_text(json.dumps({"
            "'executable': sys.executable, 'cwd': os.getcwd()}), "
            "encoding='utf-8')\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def run_install(self):
        return subprocess.run(
            [sys.executable, str(self.root / "install.py")],
            cwd=self.outside,
            env={**os.environ, "PYTHONUTF8": "1"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )

    def test_install_fresh_environment_and_idempotent_second_run(self):
        self.assertFalse((self.root / ".venv").exists())
        first = self.run_install()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("Cài đặt thành công. Chạy: py run.py", first.stdout)
        venv_python = self.root / ".venv" / "Scripts" / "python.exe"
        self.assertTrue(venv_python.is_file())
        verification = subprocess.run(
            [
                str(venv_python),
                "-c",
                "import sys, PySide6; print(sys.prefix); print(PySide6.__version__)",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn(str(self.root / ".venv"), verification.stdout)
        preserved = self.root / ".venv" / "preserve-marker"
        preserved.write_text("keep", encoding="utf-8")
        second = self.run_install()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertTrue(preserved.is_file())
        self.assertIn("Cài đặt thành công. Chạy: py run.py", second.stdout)
        self.assertEqual(self.database.read_bytes(), self.database_snapshot)

    def test_run_uses_venv_from_an_unrelated_working_directory(self):
        installed = self.run_install()
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        environment = os.environ.copy()
        environment["LAUNCH_MARKER"] = str(self.marker)
        result = subprocess.run(
            [sys.executable, str(self.root / "run.py")],
            cwd=self.outside,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = json.loads(self.marker.read_text(encoding="utf-8"))
        self.assertEqual(
            Path(record["executable"]).resolve(),
            (self.root / ".venv" / "Scripts" / "python.exe").resolve(),
        )
        self.assertEqual(Path(record["cwd"]).resolve(), self.root.resolve())
        self.assertEqual(self.database.read_bytes(), self.database_snapshot)

    def test_application_startup_failure_is_reported_by_run_py(self):
        installed = self.run_install()
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        (self.root / "main.py").write_text(
            "raise RuntimeError('startup failure')\n", encoding="utf-8"
        )
        result = subprocess.run(
            [sys.executable, str(self.root / "run.py")],
            cwd=self.outside,
            env={**os.environ, "PYTHONUTF8": "1"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Ứng dụng kết thúc với mã lỗi", result.stderr)

    def test_missing_venv_and_dependency_have_actionable_messages(self):
        missing_environment = subprocess.run(
            [sys.executable, str(self.root / "run.py")],
            cwd=self.outside,
            env={**os.environ, "PYTHONUTF8": "1"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertNotEqual(missing_environment.returncode, 0)
        self.assertIn("py install.py", missing_environment.stderr)

        venv.EnvBuilder(with_pip=True).create(self.root / ".venv")
        missing_dependency = subprocess.run(
            [sys.executable, str(self.root / "run.py")],
            cwd=self.outside,
            env={**os.environ, "PYTHONUTF8": "1"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertNotEqual(missing_dependency.returncode, 0)
        self.assertIn("Thiếu PySide6", missing_dependency.stderr)

    def test_dependency_install_error_preserves_existing_environment(self):
        venv.EnvBuilder(with_pip=True).create(self.root / ".venv")
        marker = self.root / ".venv" / "preserve-marker"
        marker.write_text("keep", encoding="utf-8")
        self.requirements.write_text("not a valid requirement !!!\n", encoding="utf-8")
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cài dependencies", result.stderr)
        self.assertTrue(marker.is_file())

    @unittest.skipUnless(sys.platform == "win32", "Windows batch launcher test")
    def test_start_bat_opens_using_the_venv_pythonw(self):
        installed = self.run_install()
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        environment = os.environ.copy()
        environment["LAUNCH_MARKER"] = str(self.marker)
        result = subprocess.run(
            ["cmd.exe", "/c", str(self.root / "start.bat")],
            cwd=self.outside,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        deadline = time.monotonic() + 10
        while not self.marker.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(self.marker.is_file(), "start.bat did not launch main.py")
        record = json.loads(self.marker.read_text(encoding="utf-8"))
        self.assertEqual(
            Path(record["executable"]).resolve(),
            (self.root / ".venv" / "Scripts" / "pythonw.exe").resolve(),
        )
        self.assertEqual(self.database.read_bytes(), self.database_snapshot)

    @unittest.skipUnless(sys.platform == "win32", "Windows batch launcher test")
    def test_start_bat_without_environment_keeps_actionable_console_message(self):
        result = subprocess.run(
            ["cmd.exe", "/c", str(self.root / "start.bat")],
            cwd=self.outside,
            input="\r\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("py install.py", result.stdout)

    @unittest.skipUnless(sys.platform == "win32", "Windows mutex test")
    def test_windows_single_instance_mutex_rejects_second_launch(self):
        script = (
            "from app.core.single_instance import acquire_single_instance; "
            "first = acquire_single_instance(); "
            "second = acquire_single_instance(); "
            "raise SystemExit(0 if first and not second else 1)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPOSITORY,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

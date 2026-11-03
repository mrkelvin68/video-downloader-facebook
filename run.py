from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENVIRONMENT = ROOT / ".venv"
VENV_PYTHON = (
    ENVIRONMENT / "Scripts" / "python.exe"
    if sys.platform == "win32"
    else ENVIRONMENT / "bin" / "python"
)


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    _configure_output()
    if not VENV_PYTHON.is_file():
        print(
            "Chưa tìm thấy Python trong .venv. Cài đặt môi trường bằng: py install.py",
            file=sys.stderr,
        )
        return 1

    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    try:
        dependency_check = subprocess.run(
            [
                str(VENV_PYTHON),
                "-c",
                "from PySide6 import QtCore, QtWidgets",
            ],
            cwd=ROOT,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
    except OSError as exc:
        print(f"Không thể chạy Python trong .venv: {exc}", file=sys.stderr)
        return 1
    if dependency_check.returncode:
        print(
            "Thiếu PySide6 hoặc môi trường .venv bị lỗi. Chạy `py install.py` "
            "để cài/kiểm tra lại dependencies.",
            file=sys.stderr,
        )
        return dependency_check.returncode

    try:
        result = subprocess.run(
            [str(VENV_PYTHON), str(ROOT / "main.py")],
            cwd=ROOT,
            check=False,
            env=environment,
        )
    except OSError as exc:
        print(f"Không thể khởi chạy ứng dụng: {exc}", file=sys.stderr)
        return 1

    if result.returncode:
        print(
            f"Ứng dụng kết thúc với mã lỗi {result.returncode}. "
            "Kiểm tra database/quyền truy cập và thử lại.",
            file=sys.stderr,
        )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

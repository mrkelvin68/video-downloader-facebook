from __future__ import annotations

import platform
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIREMENTS = ROOT / "requirements.txt"
MINIMUM_PYTHON = (3, 11)


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _python_in_venv(environment: Path) -> Path:
    if sys.platform == "win32":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _run(command: list[str], description: str) -> None:
    try:
        environment = os.environ.copy()
        environment.setdefault("PYTHONUTF8", "1")
        subprocess.run(command, cwd=ROOT, check=True, env=environment)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Không tìm thấy chương trình cần để {description}: {command[0]}"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"{description} thất bại (mã thoát {exc.returncode}). "
            "Kiểm tra kết nối mạng, quyền ghi và thông báo lỗi phía trên."
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"Không thể {description}: {exc}") from exc


def install(root: Path = ROOT) -> None:
    if sys.version_info[:2] < MINIMUM_PYTHON:
        raise RuntimeError(
            f"Cần Python 3.11 trở lên; đang chạy Python {platform.python_version()}. "
            "Cài Python 3.11+ rồi chạy lại `py install.py`."
        )

    requirements = root / "requirements.txt"
    if not requirements.is_file():
        raise RuntimeError(f"Không tìm thấy file dependencies: {requirements}")

    environment = root / ".venv"
    if not environment.exists():
        print("Đang tạo môi trường Python riêng trong .venv...", flush=True)
        try:
            venv.EnvBuilder(with_pip=True).create(environment)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(
                f"Không tạo được virtual environment tại {environment}: {exc}. "
                "Kiểm tra quyền ghi và cài đặt Python đầy đủ, sau đó chạy lại."
            ) from exc

    python = _python_in_venv(environment)
    if not python.is_file():
        raise RuntimeError(
            f"Đã có .venv nhưng không tìm thấy Python tại {python}. "
            "Không xóa môi trường cũ; kiểm tra/cài lại Python rồi phục hồi .venv."
        )
    _run(
        [str(python), "--version"],
        "kiểm tra Python trong .venv",
    )
    _run(
        [
            str(python),
            "-c",
            (
                "import sys; "
                "raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)"
            ),
        ],
        "kiểm tra phiên bản Python trong .venv",
    )
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(requirements),
        ],
        "cài dependencies vào .venv",
    )
    _run([str(python), "-m", "pip", "check"], "kiểm tra dependencies")
    _run(
        [
            str(python),
            "-c",
            (
                "import PySide6; from PySide6 import QtCore, QtWidgets; "
                "print('PySide6', PySide6.__version__, "
                "QtCore.__version__ if hasattr(QtCore, '__version__') else '')"
            ),
        ],
        "kiểm tra PySide6",
    )


def main() -> int:
    _configure_output()
    try:
        install()
    except RuntimeError as exc:
        print(f"\nCài đặt thất bại: {exc}", file=sys.stderr)
        return 1
    print("\nCài đặt thành công. Chạy: py run.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

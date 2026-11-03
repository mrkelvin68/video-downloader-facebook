from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Windows release")
    parser.add_argument("--onefile", action="store_true", help="Build one executable")
    args = parser.parse_args()
    if not PYTHON.is_file():
        print("Thiếu .venv. Chạy: py install.py", file=sys.stderr)
        return 1
    check = subprocess.run([str(PYTHON), "-c", "import PyInstaller"], cwd=ROOT)
    if check.returncode:
        print("Thiếu PyInstaller. Chạy: .venv\\Scripts\\python.exe -m pip install -r build_requirements.txt", file=sys.stderr)
        return check.returncode

    build_dir = ROOT / "build"
    dist_dir = ROOT / "dist"
    shutil.rmtree(build_dir, ignore_errors=True)
    shutil.rmtree(dist_dir, ignore_errors=True)
    env = os.environ.copy()
    if args.onefile:
        env["VIDEO_DOWNLOADER_ONEFILE"] = "1"
    command = [str(PYTHON), "-m", "PyInstaller", "--noconfirm", "--clean", "VideoDownloader.spec"]
    result = subprocess.run(command, cwd=ROOT, env=env)
    if result.returncode:
        print("Build thất bại. Kiểm tra log PyInstaller.", file=sys.stderr)
        return result.returncode
    output = dist_dir / "VideoDownloader"
    if args.onefile:
        output = dist_dir / "VideoDownloader.exe"
    elif (dist_dir / "VideoDownloader.exe").is_file():
        (dist_dir / "VideoDownloader.exe").unlink()
    if not output.exists():
        print(f"Không tìm thấy output: {output}", file=sys.stderr)
        return 1
    if not args.onefile:
        archive = dist_dir / "VideoDownloader-Windows-x64.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in output.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(dist_dir))
        print(f"Đã tạo: {archive}")
    print(f"Build thành công: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

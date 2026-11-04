from pathlib import Path
from PyInstaller.utils.hooks import collect_all
from static_ffmpeg import run as static_ffmpeg_run

ROOT = Path(SPECPATH)
ffmpeg_path, ffprobe_path = (
    static_ffmpeg_run.get_or_fetch_platform_executables_else_raise()
)
if not Path(ffmpeg_path).is_file() or not Path(ffprobe_path).is_file():
    raise FileNotFoundError("static_ffmpeg did not provide FFmpeg and FFprobe")
app_datas, app_binaries, app_hidden = collect_all("app")
ytdlp_datas, ytdlp_binaries, ytdlp_hidden = collect_all("yt_dlp")
static_datas, static_binaries, static_hidden = collect_all("static_ffmpeg")

ffmpeg_dir = Path(ffmpeg_path).parent
for binary in ffmpeg_dir.glob("*"):
    if binary.is_file() and binary.suffix.lower() in {".exe", ".dll"}:
        app_binaries.append((str(binary), "static_ffmpeg/bin/win32"))

app_datas.append((str(ROOT / "app" / "ui" / "styles.qss"), "app/ui"))

onefile = __import__("os").environ.get("VIDEO_DOWNLOADER_ONEFILE") == "1"

analysis = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=app_binaries + ytdlp_binaries + static_binaries,
    datas=app_datas + ytdlp_datas + static_datas,
    hiddenimports=app_hidden + ytdlp_hidden + static_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries if onefile else [],
    analysis.datas if onefile else [],
    [],
    name="VideoDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    exclude_binaries=not onefile,
)
if not onefile:
    coll = COLLECT(exe, analysis.binaries, analysis.datas, name="VideoDownloader")

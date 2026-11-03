@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
set "PYTHONW=%~dp0.venv\Scripts\pythonw.exe"

if not exist "%PYTHON%" (
    echo Chua tim thay moi truong Python .venv.
    echo Hay cai dat truoc bang lenh: py install.py
    pause
    exit /b 1
)

if not exist "%PYTHONW%" (
    echo Khong tim thay pythonw.exe trong .venv.
    echo Hay chay lai: py install.py
    pause
    exit /b 1
)

"%PYTHON%" -c "from PySide6 import QtCore, QtWidgets" >nul 2>&1
if errorlevel 1 (
    echo Thieu PySide6 hoac moi truong .venv bi loi.
    echo Hay chay lai: py install.py
    pause
    exit /b 1
)

start "" "%PYTHONW%" "%~dp0main.py"
if errorlevel 1 (
    echo Khong the khoi chay ung dung.
    echo Thu chay py run.py trong cua so PowerShell de xem loi chi tiet.
    pause
    exit /b 1
)

exit /b 0

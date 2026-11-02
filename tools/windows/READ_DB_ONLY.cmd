@echo off
setlocal
cd /d "%~dp0"
set "HISTORY_EXPORT=%~dp0export_history_readonly.py"
if not exist "%HISTORY_EXPORT%" set "HISTORY_EXPORT=%~dp0..\..\scripts\export_history_readonly.py"
if not exist "%HISTORY_EXPORT%" (
  echo export_history_readonly.py was not found. Extract the whole tool ZIP first.
  pause
  exit /b 2
)
set "HISTORY_PY=py -3"
py -3 -c "import sys" >nul 2>&1
if errorlevel 1 (
  set "HISTORY_PY=python"
  python -c "import sys" >nul 2>&1
  if errorlevel 1 (
    echo Python 3 is required. Install Python 3 and run this file again.
    pause
    exit /b 2
  )
)
%HISTORY_PY% -c "import psycopg2" >nul 2>&1
if errorlevel 1 (
  %HISTORY_PY% -m pip install "psycopg2-binary>=2.9.9,<3"
  if errorlevel 1 (
    pause
    exit /b 2
  )
)
%HISTORY_PY% "%HISTORY_EXPORT%"
pause

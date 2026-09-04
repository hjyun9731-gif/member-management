@echo off
chcp 65001 >nul
setlocal
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
set "DEST=%LOCALAPPDATA%\MemberManagement\UPlusBridge"
if not exist "%DEST%" mkdir "%DEST%"
copy /Y "%~dp0CRMProBridge.ps1" "%DEST%\CRMProBridge.ps1" >nul
netsh http delete urlacl url=http://127.0.0.1:18765/ >nul 2>&1
netsh http add urlacl url=http://127.0.0.1:18765/ user="%USERDOMAIN%\%USERNAME%" >nul
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "MemberManagement_UPlus_Bridge" /t REG_SZ /d "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ^\"%DEST%\CRMProBridge.ps1^\"" /f >nul
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%DEST%\CRMProBridge.ps1"
timeout /t 2 >nul
start "" "http://127.0.0.1:18765/health"
echo.
echo U+ CRM Pro 연동 설치가 끝났습니다.
echo 회원관리 수납/미수금 화면에서 'U+ 문자' - '연결확인'을 눌러 확인하세요.
pause

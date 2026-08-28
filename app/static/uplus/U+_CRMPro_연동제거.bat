@echo off
chcp 65001 >nul
setlocal
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "MemberManagement_UPlus_Bridge" /f >nul 2>&1
netsh http delete urlacl url=http://127.0.0.1:18765/ >nul 2>&1
rmdir /S /Q "%LOCALAPPDATA%\MemberManagement\UPlusBridge" >nul 2>&1
echo U+ CRM Pro Bridge 자동실행/설정 제거가 끝났습니다.
echo 현재 실행 중인 Bridge는 Windows 재로그인 후 완전히 종료됩니다.
pause

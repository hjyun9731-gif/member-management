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
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*CRMProBridge.ps1*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
rmdir /S /Q "%LOCALAPPDATA%\MemberManagement\UPlusBridge" >nul 2>&1
echo U+ CRM Pro Bridge 트레이 프로그램과 자동실행 설정을 모두 제거했습니다.
pause

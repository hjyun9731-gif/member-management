@echo off
chcp 65001 > nul
setlocal
set "REPO=C:\Users\PC\Documents\GitHub\member-management"
set "PATCH=%~dp0member-management-폐업현황-실제수정.patch"

echo.
echo ============================================
echo  폐업현황 UI 실제수정 + GitHub main 배포
echo ============================================
echo 저장소: %REPO%
echo.

if not exist "%REPO%\.git" (
  echo [실패] Git 저장소가 없습니다.
  pause
  exit /b 1
)

cd /d "%REPO%"

for /f "delims=" %%B in ('git branch --show-current') do set "BRANCH=%%B"
if /I not "%BRANCH%"=="main" (
  echo [실패] 현재 브랜치가 main이 아닙니다: %BRANCH%
  pause
  exit /b 1
)

git diff --quiet -- app/static/app.js app/static/index.html
if errorlevel 1 (
  echo [중단] app/static/app.js 또는 index.html에 미커밋 변경이 있습니다.
  echo 기존 작업 보호를 위해 아무것도 수정하지 않았습니다.
  git status --short -- app/static/app.js app/static/index.html
  pause
  exit /b 2
)

echo [1/7] 최신 main 받는 중...
git pull --ff-only origin main
if errorlevel 1 goto :fail

echo [2/7] 패치 적용 가능 여부 검사...
git apply --check "%PATCH%"
if errorlevel 1 (
  echo.
  echo [중단] 현재 GitHub 코드가 기준본과 달라 자동 적용하지 않았습니다.
  echo 아무 파일도 수정하지 않았습니다.
  pause
  exit /b 3
)

echo [3/7] 실제 코드 수정...
git apply "%PATCH%"
if errorlevel 1 goto :restorefail

echo [4/7] JavaScript 문법 검사...
where node >nul 2>nul
if not errorlevel 1 (
  node --check app\static\app.js
  if errorlevel 1 goto :restorefail
) else (
  echo Node.js가 없어 node --check는 건너뜁니다.
)

git diff --check
if errorlevel 1 goto :restorefail

echo.
echo ===== 실제 변경 내용 =====
git diff -- app/static/app.js app/static/index.html
echo ===========================
echo.

echo [5/7] 커밋...
git add app/static/app.js app/static/index.html
git commit -m "폐업현황: 양수인/이관지역 토글 및 폐업사유 표시"
if errorlevel 1 goto :restorefail

echo [6/7] GitHub main push...
git push origin main
if errorlevel 1 goto :fail

echo [7/7] 완료
echo.
git log -1 --oneline
echo.
echo 운영 주소:
echo https://member-management-production.up.railway.app
echo.
echo Railway가 main 자동배포 연결이면 지금 새 배포가 시작됩니다.
echo 배포 후 브라우저에서 Ctrl+F5 하세요.
echo.
pause
exit /b 0

:restorefail
echo.
echo [실패] 검사 중 문제가 생겨 수정 파일을 원상복구합니다.
git restore --staged app/static/app.js app/static/index.html >nul 2>nul
git restore app/static/app.js app/static/index.html
pause
exit /b 10

:fail
echo.
echo [실패] 명령 실행이 중단되었습니다.
echo 현재 상태:
git status --short
pause
exit /b 11

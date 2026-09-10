param(
  [string]$Repo = "C:\Users\PC\Documents\GitHub\member-management"
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Patcher = Join-Path $Here "apply_closure_ui_patch.py"

Write-Host ""
Write-Host "=== member-management 폐업현황 UI 패치 ===" -ForegroundColor Cyan
Write-Host "Repo: $Repo"

if (!(Test-Path $Repo)) { throw "저장소 폴더가 없습니다: $Repo" }
Set-Location $Repo

if (!(Test-Path ".git")) { throw "Git 저장소가 아닙니다: $Repo" }
if (!(Test-Path "app\static\app.js")) { throw "app\static\app.js가 없습니다." }

$branch = (git branch --show-current).Trim()
if ($branch -ne "main") { throw "현재 브랜치가 main이 아닙니다: $branch" }

$dirtyApp = git status --porcelain -- "app/static/app.js"
if ($dirtyApp) {
  Write-Host ""
  Write-Host "[중단] app/static/app.js에 커밋되지 않은 수정이 있습니다." -ForegroundColor Red
  Write-Host "기존 작업을 섞지 않기 위해 자동 패치를 중단했습니다."
  Write-Host $dirtyApp
  exit 2
}

Write-Host ""
Write-Host "[1/6] main 최신화" -ForegroundColor Yellow
git pull --ff-only origin main
if ($LASTEXITCODE -ne 0) { throw "git pull 실패" }

Write-Host ""
Write-Host "[2/6] 폐업현황 UI 수정" -ForegroundColor Yellow
python $Patcher $Repo
if ($LASTEXITCODE -ne 0) { throw "패치 적용 실패" }

Write-Host ""
Write-Host "[3/6] JavaScript 문법 검사" -ForegroundColor Yellow
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
  node --check "app/static/app.js"
  if ($LASTEXITCODE -ne 0) {
    Write-Host "[실패] JS 문법 오류. 커밋/푸시하지 않습니다." -ForegroundColor Red
    exit 3
  }
} else {
  Write-Host "Node.js가 없어 node --check는 건너뜁니다." -ForegroundColor DarkYellow
}

Write-Host ""
Write-Host "[4/6] Git diff 검사" -ForegroundColor Yellow
git diff --check
if ($LASTEXITCODE -ne 0) {
  Write-Host "[실패] git diff --check 오류. 커밋/푸시하지 않습니다." -ForegroundColor Red
  exit 4
}
git diff -- "app/static/app.js"

Write-Host ""
Write-Host "[5/6] commit" -ForegroundColor Yellow
git add -- "app/static/app.js"
git commit -m "폐업현황: 양수인/이관지역 토글 및 폐업사유 표시"
if ($LASTEXITCODE -ne 0) {
  $remaining = git diff --cached --name-only
  if (!$remaining) {
    Write-Host "커밋할 변경사항이 없습니다. 이미 반영된 상태일 수 있습니다." -ForegroundColor DarkYellow
  } else {
    throw "git commit 실패"
  }
}

Write-Host ""
Write-Host "[6/6] main push" -ForegroundColor Yellow
git push origin main
if ($LASTEXITCODE -ne 0) { throw "git push 실패" }

Write-Host ""
Write-Host "=== 완료 ===" -ForegroundColor Green
git log -1 --oneline
Write-Host ""
Write-Host "GitHub main push가 완료되었습니다."
Write-Host "Railway production은 main 자동배포 연결이므로 새 배포가 시작됩니다."
Write-Host "운영 URL: https://member-management-production.up.railway.app"

$ErrorActionPreference = "Stop"

$Repo = "C:\Users\PC\Documents\GitHub\member-management"
$Here = $PSScriptRoot

if (-not (Test-Path "$Repo\app\main.py")) {
    throw "저장소를 찾을 수 없습니다: $Repo"
}

Write-Host "[1/6] 최신화 코드 복사"
Copy-Item "$Here\app\receivables_hotfix_20260916.py" "$Repo\app\receivables_hotfix_20260916.py" -Force
New-Item -ItemType Directory -Path "$Repo\app\data" -Force | Out-Null
Copy-Item "$Here\app\data\receivables_hotfix_20260916.json" "$Repo\app\data\receivables_hotfix_20260916.json" -Force

Write-Host "[2/6] app/main.py 최소 패치"
python "$Here\patch_main.py" "$Repo\app\main.py"

Set-Location $Repo

Write-Host "[3/6] Python 문법검사"
python -m py_compile app\main.py app\receivables_hotfix_20260916.py

Write-Host "[4/6] 변경파일 확인"
git status --short
git diff -- app/main.py app/receivables_hotfix_20260916.py app/data/receivables_hotfix_20260916.json

Write-Host "[5/6] GitHub main 커밋"
git add app/main.py app/receivables_hotfix_20260916.py app/data/receivables_hotfix_20260916.json
$changes = git diff --cached --name-only
if (-not $changes) {
    Write-Host "커밋할 변경사항이 없습니다. 이미 적용된 파일일 수 있습니다."
} else {
    git commit -m "fix: apply 2026-09-16 receivables ledger corrections"
}

Write-Host "[6/6] GitHub main push -> Railway 자동배포"
git push origin main

Write-Host ""
Write-Host "완료: GitHub main push 했습니다. Railway 자동배포가 시작됩니다."
Write-Host "배포 후 /receivables 에서 수납처리 엑셀을 다시 내려받아 확인하면 됩니다."

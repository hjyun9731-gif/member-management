$ErrorActionPreference = "Stop"
$repo = "C:\Users\PC\Documents\GitHub\member-management"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "[1/8] 저장소 확인"
if (!(Test-Path (Join-Path $repo ".git"))) { throw "Git 저장소를 찾을 수 없습니다: $repo" }
Set-Location $repo

$dirty = git status --porcelain
if ($dirty) { throw "저장소에 미커밋 변경사항이 있습니다. 기존 작업 보호를 위해 중단합니다.`n$dirty" }

Write-Host "[2/8] GitHub main 최신화"
git checkout main
git pull --ff-only origin main

Write-Host "[3/8] v3 파일 복사"
Copy-Item (Join-Path $here "app\receivables_reconcile_20260916_v3.py") (Join-Path $repo "app\receivables_reconcile_20260916_v3.py") -Force
Copy-Item (Join-Path $here "app\data\receivables_reconcile_20260916_v3.json") (Join-Path $repo "app\data\receivables_reconcile_20260916_v3.json") -Force

Write-Host "[4/8] main.py에 v3 작업 연결"
python (Join-Path $here "patch_main_v3.py") (Join-Path $repo "app\main.py")

Write-Host "[5/8] 검증"
python (Join-Path $here "verify_package.py")
python -m py_compile (Join-Path $repo "app\receivables_reconcile_20260916_v3.py") (Join-Path $repo "app\main.py")
git diff --check

Write-Host "[6/8] 커밋"
git add app/main.py app/receivables_reconcile_20260916_v3.py app/data/receivables_reconcile_20260916_v3.json
if (-not (git diff --cached --quiet)) {
    git commit -m "fix: reconcile receivables with Sep 2026 ledger"
} else {
    Write-Host "이미 동일 내용이 적용되어 커밋할 변경이 없습니다."
}

Write-Host "[7/8] GitHub main push"
git push origin main

Write-Host "[8/8] Railway 자동배포 확인"
$url = "https://member-management-production.up.railway.app/health/receivables-reconcile-20260916-v3"
$ok = $false
for ($i=0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 5
    try {
        $r = Invoke-RestMethod -Uri $url -Method Get -TimeoutSec 10
        Write-Host ("Railway: " + ($r | ConvertTo-Json -Compress))
        if ($r.status -eq "ok" -and [int]$r.applied_count -eq 269) { $ok=$true; break }
    } catch { Write-Host "배포 대기 중..." }
}
if (-not $ok) { throw "GitHub push는 완료됐지만 Railway v3 확인이 아직 안 됐습니다." }
Write-Host "완료: 269건 원본 재대조 보정 / Railway 확인 성공"

$ErrorActionPreference = "Stop"

$Repo = "C:\Users\PC\Documents\GitHub\member-management"
$Here = $PSScriptRoot
$HealthUrl = "https://member-management-production.up.railway.app/health/receivables-hotfix-20260916"

if (-not (Test-Path "$Repo\app\main.py")) {
    throw "저장소를 찾을 수 없습니다: $Repo"
}

Set-Location $Repo
Write-Host "[1/9] main 최신화"
git switch main
git pull --ff-only origin main

Write-Host "[2/9] 패키지 자체 검증"
python "$Here\verify_package.py"

Write-Host "[3/9] hotfix 파일 복사"
Copy-Item "$Here\app\receivables_hotfix_20260916.py" "$Repo\app\receivables_hotfix_20260916.py" -Force
New-Item -ItemType Directory -Path "$Repo\app\data" -Force | Out-Null
Copy-Item "$Here\app\data\receivables_hotfix_20260916.json" "$Repo\app\data\receivables_hotfix_20260916.json" -Force

Write-Host "[4/9] app/main.py 최소 패치"
python "$Here\patch_main.py" "$Repo\app\main.py"

Write-Host "[5/9] Python 문법검사"
python -m py_compile app\main.py app\receivables_hotfix_20260916.py

Write-Host "[6/9] diff 오류검사"
git diff --check

git status --short
Write-Host "--- 변경사항 ---"
git diff -- app/main.py app/receivables_hotfix_20260916.py app/data/receivables_hotfix_20260916.json

Write-Host "[7/9] commit"
git add app/main.py app/receivables_hotfix_20260916.py app/data/receivables_hotfix_20260916.json
$changes = git diff --cached --name-only
if ($changes) {
    git commit -m "fix: reconcile 2026-09-16 receivables ledger"
} else {
    Write-Host "새 커밋할 변경사항이 없습니다. 이미 같은 패치가 적용되어 있을 수 있습니다."
}

Write-Host "[8/9] GitHub main push -> Railway 자동배포"
git push origin main

Write-Host "[9/9] Railway 운영 반영 확인"
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $r = Invoke-RestMethod -Uri $HealthUrl -Method Get -TimeoutSec 10
        Write-Host ("상태: " + ($r | ConvertTo-Json -Compress))
        if ($r.status -eq "ok" -and $r.balance_patch_applied -eq $true -and $r.guard_ready -eq $true -and $r.confirmed_exclusion_count -eq 28 -and $r.first_charge_date_leak_count -eq 0) {
            $ok = $true
            break
        }
    } catch {
        Write-Host "새 배포 확인 중..."
    }
    Start-Sleep -Seconds 10
}

if (-not $ok) {
    throw "GitHub push는 완료됐지만 운영 DB hotfix 확인이 아직 OK가 아닙니다. Railway 로그에서 '수납미수금 2026-09-16 최종원장 v2 보정 완료'를 확인하세요."
}

Write-Host ""
Write-Host "완료"
Write-Host "- GitHub main push 완료"
Write-Host "- 271건 잔액 보정 상태 확인"
Write-Host "- 자격증명 미발급 확정 28명 부과제외 보호장치 확인"
Write-Host "- first_charge_date 누출 0건 확인"
Write-Host "- 자격증명 발급 시 다음 달 1일부터 자동 정상부과 전환"

param(
  [string]$Repo = "C:\Users\PC\Documents\GitHub\member-management"
)
$ErrorActionPreference="Stop"
Set-Location $Repo
Write-Host "최근 커밋:" -ForegroundColor Yellow
git log -1 --oneline
Write-Host ""
Write-Host "마지막 폐업현황 UI 커밋만 되돌리려면:" -ForegroundColor Cyan
Write-Host "git revert HEAD"
Write-Host "git push origin main"
Write-Host ""
Write-Host "※ reset --hard는 사용하지 않습니다."

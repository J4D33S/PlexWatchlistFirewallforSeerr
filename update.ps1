# Media Request Firewall - One-click updater
# Run from inside the media-firewall folder

$projectDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$parentDir   = Split-Path -Parent $projectDir
$parentEnv   = Join-Path $parentDir ".env"
$projectEnv  = Join-Path $projectDir ".env"
$composeFile = Join-Path $projectDir "docker-compose.yml"

Write-Host ""
Write-Host "  Media Request Firewall - Updater" -ForegroundColor Cyan
Write-Host ""

if (Test-Path $parentEnv) {
    Write-Host "  Copying keys from parent .env..." -ForegroundColor Yellow
    Copy-Item $parentEnv $projectEnv -Force
    Write-Host "  Keys copied OK" -ForegroundColor Green
} else {
    Write-Host "  WARNING: No .env found in parent folder" -ForegroundColor Yellow
}

Write-Host "  Stopping container..." -ForegroundColor Yellow
docker compose -f $composeFile down

Write-Host "  Rebuilding image..." -ForegroundColor Yellow
docker compose -f $composeFile build --no-cache

Write-Host "  Starting container..." -ForegroundColor Yellow
docker compose -f $composeFile up -d --force-recreate

Write-Host ""
Write-Host "  Done! Firewall running at http://localhost:7878" -ForegroundColor Green
Write-Host ""
pause

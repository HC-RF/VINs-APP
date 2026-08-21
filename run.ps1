<#
    Start the VIN Decoder locally on Windows.

    First run creates a virtual environment and installs dependencies; later
    runs reuse them.

        .\run.ps1              # start the server
        .\run.ps1 -Test        # run the test suite instead
        .\run.ps1 -Port 8080   # start on a different port
#>
[CmdletBinding()]
param(
    [switch]$Test,
    [switch]$Reinstall,
    [int]$Port = 8000,
    [string]$AppHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if ($Reinstall -and (Test-Path ".venv")) {
    Write-Host "Removing existing virtual environment..." -ForegroundColor Yellow
    Remove-Item ".venv" -Recurse -Force
}

if (-not (Test-Path $python)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) { & py -3 -m venv .venv } else { & python -m venv .venv }
    if (-not (Test-Path $python)) { throw "Could not create the virtual environment. Is Python 3.11+ installed?" }

    Write-Host "Installing dependencies..." -ForegroundColor Cyan
    & $python -m pip install --quiet --upgrade pip
    & $python -m pip install --quiet -r requirements-dev.txt
}

if (-not (Test-Path ".env")) {
    Write-Host "No .env found - copying .env.example (free providers only, no API cost)." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
}

if ($Test) {
    & $python -m pytest
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "  VIN Decoder -> http://$AppHost`:$Port" -ForegroundColor Green
Write-Host "  API docs    -> http://$AppHost`:$Port/api/docs" -ForegroundColor DarkGray
Write-Host ""
& $python -m uvicorn app.main:app --host $AppHost --port $Port --reload

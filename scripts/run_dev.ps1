# Run RetirementPet from source with debug logging and a console.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\run_dev.ps1

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Error "venv python not found at $VenvPython"
}

$env:PYTHONPATH = "$Root\src"
& $VenvPython -m retirement_pet --debug

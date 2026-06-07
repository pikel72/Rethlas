param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8091
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvScripts = Join-Path $RootDir ".venv\Scripts"
$Python = Join-Path $VenvScripts "python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python virtual environment not found at $Python. Create it and install requirements.txt first."
}

$env:PATH = "$VenvScripts;$env:PATH"

Write-Host "Starting Rethlas verification service"
Write-Host "  Directory: $RootDir"
Write-Host "  Python:    $Python"
Write-Host "  URL:       http://${HostAddress}:$Port"
Write-Host ""

Push-Location $RootDir
try {
    & $Python -m uvicorn api.server:app --host $HostAddress --port $Port
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

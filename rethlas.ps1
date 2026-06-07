param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArgsFromUser
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $Python) {
        throw "Python was not found on PATH. Install Python 3.11+ or activate an environment first."
    }
    & $Python.Source -m rethlas.cli @ArgsFromUser
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

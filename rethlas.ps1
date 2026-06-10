param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArgsFromUser
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    $RepoPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $RepoPython) {
        $PythonExe = $RepoPython
    }
    else {
        $Python = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $Python) {
            throw "Python was not found on PATH. Install Python 3.11+ or run python -m rethlas.cli setup."
        }
        $PythonExe = $Python.Source
    }
    if (-not (Test-Path -LiteralPath $PythonExe)) {
        throw "Python was not found on PATH. Install Python 3.11+ or activate an environment first."
    }
    & $PythonExe -m rethlas.cli @ArgsFromUser
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

param(
    [string]$ProblemFile = $env:PROBLEM_FILE,
    [string]$Model = $env:MODEL,
    [string]$ReasoningEffort = $env:REASONING_EFFORT,
    [string]$LogDir = $env:LOG_DIR,
    [string]$VerifyUrl = $env:VERIFY_URL,
    [switch]$DryRun,
    [switch]$LiveLog,
    [switch]$NoLiveLog
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..\..")
$ArgsForCli = @("run")

if ([string]::IsNullOrWhiteSpace($ProblemFile)) {
    $ProblemFile = "example"
}
$ArgsForCli += $ProblemFile

if (-not [string]::IsNullOrWhiteSpace($Model)) {
    $ArgsForCli += @("--model", $Model)
}
if ($DryRun) {
    $ArgsForCli += "--dry-run"
}
if ($NoLiveLog) {
    $ArgsForCli += "--no-live-log"
}

if (-not [string]::IsNullOrWhiteSpace($ReasoningEffort)) {
    Write-Warning "ReasoningEffort is now read from rethlas.toml model profiles; this wrapper ignores -ReasoningEffort."
}
if (-not [string]::IsNullOrWhiteSpace($LogDir)) {
    Write-Warning "LogDir is now calculated by the root Rethlas CLI; this wrapper ignores -LogDir."
}
if (-not [string]::IsNullOrWhiteSpace($VerifyUrl)) {
    Write-Warning "VerifyUrl is now read from rethlas.toml; this wrapper ignores -VerifyUrl."
}
if ($LiveLog) {
    Write-Warning "LiveLog is the default for the root Rethlas CLI; this wrapper ignores -LiveLog."
}

Push-Location $RepoRoot
try {
    & python -m rethlas.cli @ArgsForCli
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

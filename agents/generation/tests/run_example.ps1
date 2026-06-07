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
$RootDir = Resolve-Path (Join-Path $ScriptDir "..")
$RootDir = $RootDir.Path
$VenvScripts = Join-Path $RootDir ".venv\Scripts"

if ([string]::IsNullOrWhiteSpace($ProblemFile)) {
    $ProblemFile = "data/example.md"
}
if ([string]::IsNullOrWhiteSpace($Model)) {
    $Model = "gpt-5.5"
}
if ([string]::IsNullOrWhiteSpace($ReasoningEffort)) {
    $ReasoningEffort = "xhigh"
}
if ([string]::IsNullOrWhiteSpace($VerifyUrl)) {
    $VerifyUrl = "http://127.0.0.1:8091/health"
}

$ProblemFile = $ProblemFile.Replace("\", "/")

if ([System.IO.Path]::IsPathRooted($ProblemFile)) {
    throw "PROBLEM_FILE must be relative to agents/generation: $ProblemFile"
}
if ($ProblemFile -eq ".." -or $ProblemFile.StartsWith("../") -or $ProblemFile.Contains("/../") -or $ProblemFile.EndsWith("/..")) {
    throw "PROBLEM_FILE must not contain '..': $ProblemFile"
}
if (-not $ProblemFile.StartsWith("data/")) {
    $ProblemFile = "data/$ProblemFile"
}
if (-not $ProblemFile.EndsWith(".md")) {
    $Extension = [System.IO.Path]::GetExtension($ProblemFile)
    if (-not [string]::IsNullOrWhiteSpace($Extension)) {
        throw "PROBLEM_FILE must point to a markdown file under data/: $ProblemFile"
    }
    $ProblemFile = "$ProblemFile.md"
}
if (-not ($ProblemFile -like "data/*.md")) {
    throw "PROBLEM_FILE must point to a markdown file under data/: $ProblemFile"
}

$AbsProblemFile = Join-Path $RootDir $ProblemFile
if (-not (Test-Path -LiteralPath $AbsProblemFile -PathType Leaf)) {
    throw "Problem file not found: $AbsProblemFile"
}

$ProblemRel = $ProblemFile.Substring("data/".Length)
$ProblemRel = $ProblemRel.Substring(0, $ProblemRel.Length - ".md".Length)
$ProblemId = [System.IO.Path]::GetFileNameWithoutExtension($ProblemFile)
$RefDir = "data/$ProblemRel.refs"
$RefPrompt = "Use reference_dir=$RefDir if it exists."

$AbsRefDir = Join-Path $RootDir $RefDir
if (Test-Path -LiteralPath $AbsRefDir -PathType Container) {
    $Pdfs = Get-ChildItem -LiteralPath $AbsRefDir -Recurse -File -Filter *.pdf |
        Where-Object { $_.FullName -notlike (Join-Path $AbsRefDir ".extracted\*") }

    if ($Pdfs.Count -gt 0) {
        $PdfToText = Get-Command pdftotext -ErrorAction SilentlyContinue
        if ($null -eq $PdfToText) {
            Write-Warning "Found PDF references, but pdftotext is not installed; PDFs will be ignored."
        }
        else {
            foreach ($Pdf in $Pdfs) {
                $RelPdf = [System.IO.Path]::GetRelativePath($AbsRefDir, $Pdf.FullName)
                $TxtRel = [System.IO.Path]::ChangeExtension($RelPdf, ".txt")
                $Txt = Join-Path (Join-Path $AbsRefDir ".extracted") $TxtRel
                $TxtDir = Split-Path -Parent $Txt
                New-Item -ItemType Directory -Force -Path $TxtDir | Out-Null
                if ((-not (Test-Path -LiteralPath $Txt)) -or ($Pdf.LastWriteTime -gt (Get-Item -LiteralPath $Txt).LastWriteTime)) {
                    & $PdfToText.Source -layout $Pdf.FullName $Txt
                }
            }
            $RefPrompt = "Use reference_dir=$RefDir if it exists. PDF references have been extracted to $RefDir/.extracted; read those extracted .txt files instead of the PDFs."
        }
    }
}

if ([string]::IsNullOrWhiteSpace($LogDir)) {
    $LogDir = Join-Path (Join-Path $RootDir "logs") $ProblemRel
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$LogFile = Join-Path $LogDir "$ProblemId.md"
$Prompt = "Use AGENTS.md exactly to solve the math problem in $ProblemFile. Use problem_id=$ProblemRel. $RefPrompt"

if (Test-Path -LiteralPath $VenvScripts -PathType Container) {
    $env:PATH = "$VenvScripts;$env:PATH"
}

$CodexVersion = "unknown"
try {
    $CodexVersion = (& codex --version 2>$null)
}
catch {
    $CodexVersion = "unknown"
}

Write-Host "========================================"
Write-Host " Codex:     $CodexVersion"
Write-Host " Model:     $Model"
Write-Host " Effort:    $ReasoningEffort"
Write-Host " Problem:   $ProblemFile"
Write-Host " Problem ID: $ProblemRel"
Write-Host " References: $RefDir"
Write-Host " Log:       $LogFile"
Write-Host "========================================"
Write-Host ""

try {
    Invoke-WebRequest -Uri $VerifyUrl -UseBasicParsing -TimeoutSec 5 | Out-Null
}
catch {
    $BaseVerifyUrl = $VerifyUrl -replace "/health.*$", ""
    Write-Warning "Verification service not reachable at $BaseVerifyUrl"
    Write-Warning "The agent will skip proof verification. Start it first if you need verified proofs."
    Write-Host ""
}

$Started = Get-Date
Write-Host "Running $ProblemFile -> $LogFile"
if ($NoLiveLog) {
    Write-Host "Output is being written to the log file."
    Write-Host "In another PowerShell terminal, you can also watch it with:"
    Write-Host "  Get-Content -Wait -Tail 80 `"$LogFile`""
}
else {
    Write-Host "Streaming Codex output below and saving a copy to the log file."
}
Write-Host ""

$CodexArgs = @(
    "exec",
    "-C", $RootDir,
    "-m", $Model,
    "--config", "model_reasoning_effort=`"$ReasoningEffort`"",
    "--dangerously-bypass-approvals-and-sandbox",
    $Prompt
)

if ($DryRun) {
    Write-Host "Dry run only. Codex command:"
    Write-Host ("  codex " + ($CodexArgs -join " "))
    exit 0
}

$CodexExitCode = 1
Push-Location $RootDir
try {
    $OldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($NoLiveLog) {
            & codex @CodexArgs > $LogFile 2>&1
        }
        else {
            & codex @CodexArgs 2>&1 | Tee-Object -FilePath $LogFile
        }
        $CodexExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $OldErrorActionPreference
    }
}
finally {
    Pop-Location
}

$Elapsed = New-TimeSpan -Start $Started -End (Get-Date)

if ($CodexExitCode -ne 0) {
    Write-Warning "codex exited with code $CodexExitCode (see $LogFile for details)"
}

Write-Host "Finished $ProblemFile -> $LogFile"
Write-Host ("Total time: {0:00}:{1:00}:{2:00}" -f [int]$Elapsed.TotalHours, $Elapsed.Minutes, $Elapsed.Seconds)
Write-Host ""
Write-Host "Result files will be under:"
Write-Host "  $RootDir\results\$ProblemRel"

exit $CodexExitCode

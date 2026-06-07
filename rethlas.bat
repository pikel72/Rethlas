@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
cd /d "%ROOT%"

:menu
cls
echo Rethlas launcher
echo =================
echo.
echo  1. Doctor
echo  2. Start verification service in a new window
echo  3. Run included example
echo  4. Run a problem
echo  5. Dry-run a problem
echo  0. Exit
echo.
set /p "CHOICE=Choose an option: "

if "%CHOICE%"=="1" goto doctor
if "%CHOICE%"=="2" goto verifier
if "%CHOICE%"=="3" goto run_example
if "%CHOICE%"=="4" goto run_problem
if "%CHOICE%"=="5" goto dry_run_problem
if "%CHOICE%"=="0" goto end

echo.
echo Unknown option: %CHOICE%
pause
goto menu

:doctor
call :print_header "Doctor"
call :check_command python
call :check_command powershell
call :check_command codex
call :check_path "agents\generation\.venv\Scripts\python.exe" "generation venv"
call :check_path "agents\verification\.venv\Scripts\python.exe" "verification venv"
call :check_path "agents\generation\tests\run_example.ps1" "generation runner"
call :check_path "agents\verification\start_server.ps1" "verification runner"
echo.
python -m rethlas.cli doctor
echo.
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8091/health | Out-Null; 'verifier: reachable at http://127.0.0.1:8091' } catch { 'verifier: not reachable at http://127.0.0.1:8091' }"
echo.
pause
goto menu

:verifier
call :print_header "Starting verification service"
echo A new PowerShell window will stay open while the service is running.
echo Close that window or press Ctrl+C there to stop the service.
echo.
start "Rethlas verification service" powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "%ROOT%rethlas.ps1" verify-server
pause
goto menu

:run_example
set "PROBLEM=example"
goto run_selected

:run_problem
call :ask_problem
goto run_selected

:dry_run_problem
call :ask_problem
goto dry_run_selected

:run_selected
call :print_header "Running %PROBLEM%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%rethlas.ps1" run "%PROBLEM%"
echo.
pause
goto menu

:dry_run_selected
call :print_header "Dry run %PROBLEM%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%rethlas.ps1" run "%PROBLEM%" --dry-run
echo.
pause
goto menu

:ask_problem
echo.
echo Enter a problem id or path.
echo Examples:
echo   example
echo   ns/ns
echo   data/modrep/modrep.md
echo.
set /p "PROBLEM=Problem: "
if "%PROBLEM%"=="" set "PROBLEM=example"
exit /b 0

:print_header
echo.
echo %~1
echo ------------------------------------------------------------
exit /b 0

:check_command
where "%~1" >nul 2>nul
if errorlevel 1 (
  echo %~1: missing
) else (
  for /f "delims=" %%P in ('where "%~1" 2^>nul') do (
    echo %~1: %%P
    goto :eof
  )
)
exit /b 0

:check_path
if exist "%ROOT%%~1" (
  echo %~2: found
) else (
  echo %~2: missing ^(%~1^)
)
exit /b 0

:end
endlocal

# CryptoQuant local development startup script (PowerShell / Windows).
# Run from the project root: .\start-dev.ps1

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$runDir = Join-Path $root ".run"

if (-not (Test-Path $venvPython)) {
    throw "Backend virtual environment not found: $venvPython. Run .\scripts\start.ps1 -SkipMigrate first, or create .venv manually."
}

if (-not (Test-Path (Join-Path -Path $frontendDir -ChildPath "node_modules"))) {
    throw ("Frontend dependencies not found: {0}. Run npm install in frontend." -f (Join-Path $frontendDir "node_modules"))
}

$npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not $npm) {
    $npm = (Get-Command npm -ErrorAction SilentlyContinue).Source
}
if (-not $npm) {
    throw "npm not found. Install Node.js first."
}

New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$processes = @()
$windowsTerminal = (Get-Command wt.exe -ErrorAction SilentlyContinue).Source

try {
    $backendLog = Join-Path $runDir "dev-backend.log"
    $workerLog = Join-Path $runDir "dev-worker.log"
    $frontendLog = Join-Path $runDir "dev-frontend.log"

    $backendErrorLog = Join-Path $runDir "dev-backend.log.err"
    $workerErrorLog = Join-Path $runDir "dev-worker.log.err"
    $frontendErrorLog = Join-Path $runDir "dev-frontend.log.err"

    if ($windowsTerminal) {
        $backendCommand = "Set-Location -LiteralPath '$backendDir'; & '$venvPython' -m uvicorn app.main:app --host 0.0.0.0 --port 8007 --reload"
        $workerCommand = "Set-Location -LiteralPath '$backendDir'; & '$venvPython' -m arq app.job.arq_app.WorkerSettings"
        $frontendCommand = "Set-Location -LiteralPath '$frontendDir'; & '$npm' run dev"
        $backendEncodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($backendCommand))
        $workerEncodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($workerCommand))
        $frontendEncodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($frontendCommand))

        $terminalArguments = @(
            "-w", "0", "new-tab", "--title", "CryptoQuant Backend", "powershell.exe", "-NoLogo", "-NoExit", "-EncodedCommand", $backendEncodedCommand,
            ";",
            "new-tab", "--title", "CryptoQuant Worker", "powershell.exe", "-NoLogo", "-NoExit", "-EncodedCommand", $workerEncodedCommand,
            ";",
            "new-tab", "--title", "CryptoQuant Frontend", "powershell.exe", "-NoLogo", "-NoExit", "-EncodedCommand", $frontendEncodedCommand
        )
        & $windowsTerminal @terminalArguments | Out-Null

        Write-Host "Backend started:  http://localhost:8007 (logs in CryptoQuant Backend tab)" -ForegroundColor Green
        Write-Host "Worker started:   ARQ (logs in CryptoQuant Worker tab)" -ForegroundColor Green
        Write-Host "Frontend started: http://localhost:5177 (logs in CryptoQuant Frontend tab)" -ForegroundColor Green
        Write-Host "Each service is running in its own Windows Terminal tab. Close the tabs or press Ctrl+C there to stop." -ForegroundColor Yellow
        return
    }

    Write-Host "Windows Terminal (wt.exe) not found; starting services with log files in $runDir." -ForegroundColor Yellow
    $backend = Start-Process -FilePath $venvPython `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8007", "--reload") `
        -WorkingDirectory $backendDir `
        -RedirectStandardOutput $backendLog `
        -RedirectStandardError $backendErrorLog `
        -NoNewWindow -PassThru
    $processes += $backend

    $worker = Start-Process -FilePath $venvPython `
        -ArgumentList @("-m", "arq", "app.job.arq_app.WorkerSettings") `
        -WorkingDirectory $backendDir `
        -RedirectStandardOutput $workerLog `
        -RedirectStandardError $workerErrorLog `
        -NoNewWindow -PassThru
    $processes += $worker

    $frontend = Start-Process -FilePath $npm `
        -ArgumentList @("run", "dev") `
        -WorkingDirectory $frontendDir `
        -RedirectStandardOutput $frontendLog `
        -RedirectStandardError $frontendErrorLog `
        -NoNewWindow -PassThru
    $processes += $frontend

    Write-Host "Backend started:  http://localhost:8007 (uvicorn --reload, PID $($backend.Id))" -ForegroundColor Green
    Write-Host "Worker started:   ARQ (PID $($worker.Id))" -ForegroundColor Green
    Write-Host "Frontend started: http://localhost:5177 (npm run dev, PID $($frontend.Id))" -ForegroundColor Green
    Write-Host "Logs:             $runDir" -ForegroundColor Cyan
    Write-Host "Press Ctrl+C to stop the development services." -ForegroundColor Yellow

    while ($processes | Where-Object { -not $_.HasExited }) {
        Start-Sleep -Seconds 1
    }
}
finally {
    foreach ($process in $processes) {
        if (-not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

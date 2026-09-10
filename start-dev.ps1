# CryptoQuant local development startup script (PowerShell / Windows).
# Run from the project root: .\start-dev.ps1
# Options:
#   -SkipMigration  不执行 alembic（DB 不可达时用）
#   -SkipServices   只做 DB 迁移/检查就退出，不启动服务

[CmdletBinding()]
param(
    [switch]$SkipMigration,
    [switch]$SkipServices
)

$ErrorActionPreference = "Stop"

# 任何终止性错误：显示原因并停留窗口，避免“闪退”无从排查
trap {
    Write-Host ""
    Write-Host "start-dev.ps1 stopped with an error: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Boot log: $runDir\dev-boot.log  (if created)" -ForegroundColor Yellow
    try { Stop-Transcript | Out-Null } catch { }
    Read-Host "Press Enter to close this window"
    exit 1
}

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

# 启动全程留痕，窗口关闭也能看 .run/dev-boot.log 排查
try { Start-Transcript -Path (Join-Path $runDir "dev-boot.log") -Force | Out-Null } catch {  }
$processes = @()
$windowsTerminal = (Get-Command wt.exe -ErrorAction SilentlyContinue).Source

# ---- DB 迁移：启动前自动 upgrade head（失败只警告不阻断，方便先跑 Docker 再启） ----
if (-not $SkipMigration) {
    $migrationLog = Join-Path $runDir "dev-migration.log"
    Write-Host "Checking DB migrations (alembic upgrade head)..." -ForegroundColor Cyan
    $oldCwd = Get-Location
    try {
        Set-Location -LiteralPath $backendDir
        & $venvPython -m alembic upgrade head *> $migrationLog
        if ($LASTEXITCODE -ne 0) {
            Write-Host ("DB migration failed (exit {0}); see {1}. Continuing anyway." -f $LASTEXITCODE, $migrationLog) -ForegroundColor Yellow
            Get-Content -LiteralPath $migrationLog -Tail 12 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
        }
        else {
            Write-Host "DB migrations up to date." -ForegroundColor Green
        }
    }
    catch {
        Write-Host "DB migration skipped: $($_.Exception.Message). See $migrationLog." -ForegroundColor Yellow
    }
    finally {
        Set-Location -LiteralPath $oldCwd
    }
}

if ($SkipServices) {
    Write-Host "SkipServices: DB checked, services NOT started. Exiting." -ForegroundColor Green
    try { Stop-Transcript | Out-Null } catch {  }
    return
}

try {
    $backendLog = Join-Path $runDir "dev-backend.log"
    $workerLog = Join-Path $runDir "dev-worker.log"
    $schedulerLog = Join-Path $runDir "dev-scheduler.log"
    $frontendLog = Join-Path $runDir "dev-frontend.log"

    $backendErrorLog = Join-Path $runDir "dev-backend.log.err"
    $workerErrorLog = Join-Path $runDir "dev-worker.log.err"
    $schedulerErrorLog = Join-Path $runDir "dev-scheduler.log.err"
    $frontendErrorLog = Join-Path $runDir "dev-frontend.log.err"

    if ($windowsTerminal) {
        $backendCommand = "Set-Location -LiteralPath '$backendDir'; & '$venvPython' -m uvicorn app.main:app --host 0.0.0.0 --port 8007 --reload"
        $workerCommand = "Set-Location -LiteralPath '$backendDir'; & '$venvPython' -m arq app.job.arq_app.WorkerSettings"
        $schedulerCommand = "Set-Location -LiteralPath '$backendDir'; & '$venvPython' -m app.job.scheduler"
        $frontendCommand = "Set-Location -LiteralPath '$frontendDir'; & '$npm' run dev"
        $backendEncodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($backendCommand))
        $workerEncodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($workerCommand))
        $schedulerEncodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($schedulerCommand))
        $frontendEncodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($frontendCommand))

        $terminalArguments = @(
            "-w", "0", "new-tab", "--title", "CryptoQuant Backend", "powershell.exe", "-NoLogo", "-NoExit", "-EncodedCommand", $backendEncodedCommand,
            ";",
            "new-tab", "--title", "CryptoQuant Worker", "powershell.exe", "-NoLogo", "-NoExit", "-EncodedCommand", $workerEncodedCommand,
            ";",
            "new-tab", "--title", "CryptoQuant Scheduler", "powershell.exe", "-NoLogo", "-NoExit", "-EncodedCommand", $schedulerEncodedCommand,
            ";",
            "new-tab", "--title", "CryptoQuant Frontend", "powershell.exe", "-NoLogo", "-NoExit", "-EncodedCommand", $frontendEncodedCommand
        )
        & $windowsTerminal @terminalArguments | Out-Null

        Write-Host "Backend started:  http://localhost:8007 (logs in CryptoQuant Backend tab)" -ForegroundColor Green
        Write-Host "Worker started:   ARQ (logs in CryptoQuant Worker tab)" -ForegroundColor Green
        Write-Host "Scheduler started: beat - cron 定时下载/自动账户 (logs in CryptoQuant Scheduler tab)" -ForegroundColor Green
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

    $scheduler = Start-Process -FilePath $venvPython `
        -ArgumentList @("-m", "app.job.scheduler") `
        -WorkingDirectory $backendDir `
        -RedirectStandardOutput $schedulerLog `
        -RedirectStandardError $schedulerErrorLog `
        -NoNewWindow -PassThru
    $processes += $scheduler

    $frontend = Start-Process -FilePath $npm `
        -ArgumentList @("run", "dev") `
        -WorkingDirectory $frontendDir `
        -RedirectStandardOutput $frontendLog `
        -RedirectStandardError $frontendErrorLog `
        -NoNewWindow -PassThru
    $processes += $frontend

    Write-Host "Backend started:  http://localhost:8007 (uvicorn --reload, PID $($backend.Id))" -ForegroundColor Green
    Write-Host "Worker started:   ARQ (PID $($worker.Id))" -ForegroundColor Green
    Write-Host "Scheduler started: beat (PID $($scheduler.Id)) - cron 定时下载/自动账户" -ForegroundColor Green
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
    try { Stop-Transcript | Out-Null } catch { }
}

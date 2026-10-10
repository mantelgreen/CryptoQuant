# ============================================================
# CryptoQuant 一键启动脚本（PowerShell / Windows）
#
# 用法（在仓库根目录 d:\Quant\CryptoQuant 下执行）：
#   .\scripts\start.ps1              # 创建 venv、装依赖、跑迁移、起 gateway + worker
#   .\scripts\start.ps1 -SkipInstall # 跳过 pip install（已装过时）
#   .\scripts\start.ps1 -SkipMigrate # 跳过 alembic upgrade
#   .\scripts\start.ps1 -OnlyBackend # 只起后端
#   .\scripts\start.ps1 -OnlyFrontend # 只起前端
# ============================================================

param(
    [switch]$SkipInstall,
    [switch]$SkipMigrate,
    [switch]$OnlyBackend,
    [switch]$OnlyFrontend,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

if ($Help) {
    Write-Host @"
CryptoQuant 一键启动脚本

用法:
    .\scripts\start.ps1                  完整流程（venv + 依赖 + 迁移 + 后端 + worker + 前端）
    .\scripts\start.ps1 -SkipInstall     跳过 pip install
    .\scripts\start.ps1 -SkipMigrate     跳过 alembic upgrade
    .\scripts\start.ps1 -OnlyBackend     只起后端（gateway + worker）
    .\scripts\start.ps1 -OnlyFrontend    只起前端
"@
    exit 0
}

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "[OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 1 }

# ===== 1. 后端：venv + 依赖 =====
if (-not $OnlyFrontend) {
    Step "后端：创建虚拟环境 (.venv) 与安装依赖"

    if (-not (Test-Path ".venv")) {
        python -m venv .venv
        Ok "已创建 .venv"
    } else {
        Ok ".venv 已存在"
    }

    $venvPy = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) { Fail "虚拟环境创建失败：$venvPy 不存在" }

    if (-not $SkipInstall) {
        & $venvPy -m pip install --upgrade pip | Out-Null
        # pyproject.toml 在 backend/ 下，必须切到该目录再安装
        Push-Location (Join-Path $root "backend")
        try {
            & (Join-Path $root ".venv\Scripts\python.exe") -m pip install -e ".[dev]"
            if ($LASTEXITCODE -ne 0) { Fail "pip install 失败" }
        } finally {
            Pop-Location
        }
        Ok "依赖安装完成"
    } else {
        Ok "已跳过 pip install"
    }

    # ===== 2. 加载 .env =====
    Step "加载 .env 配置"
    $envFile = Join-Path $root "backend\.env"
    if (-not (Test-Path $envFile)) {
        Copy-Item (Join-Path $root "backend\.env.example") $envFile
        Warn "已从 .env.example 复制 backend\.env，请按需修改"
    }
    Get-Content $envFile | ForEach-Object {
        if ($_ -match "^\s*#") { return }
        if ($_ -match "^\s*$") { return }
        $line = $_ -replace '\r?\n',''
        if ($line -match "^\s*([A-Z0-9_]+)\s*=\s*(.*)$") {
            $k = $Matches[1]
            $v = $Matches[2].Trim('"').Trim("'")
            Set-Item -Path "Env:$k" -Value $v
        }
    }
    Ok "环境变量已注入（DATABASE_HOST=$env:DATABASE_HOST / REDIS_HOST=$env:REDIS_HOST）"

    # ===== 3. alembic 迁移 =====
    if (-not $SkipMigrate) {
        Step "alembic upgrade head"
        Push-Location (Join-Path $root "backend")
        try {
            & $venvPy -m alembic upgrade head
            if ($LASTEXITCODE -ne 0) { Fail "alembic 失败" }
            Ok "迁移完成"
        } finally {
            Pop-Location
        }
    } else {
        Ok "已跳过 alembic"
    }

    # ===== 4. smoke test =====
    Step "smoke test"
    Push-Location (Join-Path $root "backend")
    try {
        $env:PYTHONPATH = (Join-Path $root "backend\src")
        & $venvPy -m pytest tests/ -q
        if ($LASTEXITCODE -ne 0) { Warn "smoke test 有失败，请检查上方输出" } else { Ok "smoke test 全部通过" }
    } finally {
        Pop-Location
    }
}

# ===== 5. 启动进程 =====
$jobs = @()

if (-not $OnlyFrontend) {
    Step "启动 gateway (uvicorn)"

    $venvPy = Join-Path $root ".venv\Scripts\python.exe"
    $env:DATABASE_HOST = if ($env:DATABASE_HOST) { $env:DATABASE_HOST } else { "127.0.0.1" }
    $env:REDIS_HOST    = if ($env:REDIS_HOST)    { $env:REDIS_HOST }    else { "127.0.0.1" }
    $env:REDIS_DB      = if ($env:REDIS_DB)      { $env:REDIS_DB }      else { "8" }
    $env:APP_ENV       = if ($env:APP_ENV)       { $env:APP_ENV }       else { "dev" }
    $env:APP_LOG_LEVEL = if ($env:APP_LOG_LEVEL) { $env:APP_LOG_LEVEL } else { "INFO" }

    $gwLog = Join-Path $root ".run\gateway.log"
    $wkLog = Join-Path $root ".run\worker.log"
    New-Item -ItemType Directory -Force -Path (Split-Path $gwLog) | Out-Null

    $gw = Start-Process -FilePath $venvPy `
        -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8000" `
        -WorkingDirectory (Join-Path $root "backend") `
        -RedirectStandardOutput $gwLog `
        -RedirectStandardError  "$gwLog.err" `
        -NoNewWindow -PassThru
    $jobs += @{ Name="gateway"; Proc=$gw; Log=$gwLog }
    Ok "gateway 已后台启动 (pid=$($gw.Id), log=$gwLog)"

    Step "启动 ARQ worker"
    $wk = Start-Process -FilePath $venvPy `
        -ArgumentList "-m","arq","app.job.arq_app.WorkerSettings" `
        -WorkingDirectory (Join-Path $root "backend") `
        -RedirectStandardOutput $wkLog `
        -RedirectStandardError  "$wkLog.err" `
        -NoNewWindow -PassThru
    $jobs += @{ Name="worker"; Proc=$wk; Log=$wkLog }
    Ok "ARQ worker 已后台启动 (pid=$($wk.Id), log=$wkLog)"

    Step "启动 beat 调度器"
    $beatLog = Join-Path $root ".run\beat.log"
    $beat = Start-Process -FilePath $venvPy `
        -ArgumentList "-m","app.job.scheduler" `
        -WorkingDirectory (Join-Path $root "backend") `
        -RedirectStandardOutput $beatLog `
        -RedirectStandardError  "$beatLog.err" `
        -NoNewWindow -PassThru
    $jobs += @{ Name="beat"; Proc=$beat; Log=$beatLog }
    Ok "beat 已后台启动 (pid=$($beat.Id), log=$beatLog)"
}

if (-not $OnlyBackend) {
    Step "前端：安装依赖 (npm install)"
    $feDir = Join-Path $root "frontend"
    $npmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue) ? "npm.cmd" : "npm"
    Push-Location $feDir
    try {
        if (-not (Test-Path "node_modules")) {
            & $npmCmd install --legacy-peer-deps 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Warn "npm install 失败，尝试 --force"
                & $npmCmd install --force 2>&1 | Out-Null
            }
            if (-not (Test-Path "node_modules")) {
                Warn "前端依赖安装失败，请手动在 frontend/ 执行 npm install --legacy-peer-deps"
            } else {
                Ok "前端依赖安装完成"
            }
        } else {
            Ok "node_modules 已存在"
        }

        if (Test-Path "node_modules") {
            Step "前端：启动 vite dev server"
            $feLog = Join-Path $root ".run\frontend.log"
            $fe = Start-Process -FilePath $npmCmd `
                -ArgumentList "run","dev" `
                -WorkingDirectory $feDir `
                -RedirectStandardOutput $feLog `
                -RedirectStandardError  "$feLog.err" `
                -NoNewWindow -PassThru
            $jobs += @{ Name="frontend"; Proc=$fe; Log=$feLog }
            Ok "vite 已后台启动 (pid=$($fe.Id), log=$feLog) — http://localhost:5173"
        }
    } finally {
        Pop-Location
    }
}

# ===== 6. 健康检查（不阻塞脚本） =====
Step "健康检查"
Start-Sleep -Seconds 5
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 5
    Ok "gateway health: db=$($h.data.db) redis=$($h.data.redis)"
} catch {
    Warn "gateway 健康检查未通过，请查看 .run\gateway.log.err"
}
try {
    $code = (curl.exe -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:5173/ 2>$null)
    if ($code -eq "200") { Ok "frontend: http://localhost:5173" }
    else { Warn "frontend 未就绪（$code），请查看 .run\frontend.log.err" }
} catch {
    Warn "frontend 未就绪，请查看 .run\frontend.log.err"
}

Step "启动完成（进程已转入后台，终端可继续使用）"
Write-Host "  - API     : http://localhost:8000"   -ForegroundColor Green
Write-Host "  - Health  : http://localhost:8000/health" -ForegroundColor Green
Write-Host "  - Docs    : http://localhost:8000/docs" -ForegroundColor Green
Write-Host "  - Frontend: http://localhost:5173"   -ForegroundColor Green
Write-Host "  - 日志    : .run\*.log"              -ForegroundColor Green
Write-Host ""
Write-Host "停止全部服务请运行: .\scripts\stop.ps1" -ForegroundColor Yellow
Write-Host "查看运行日志请运行: Get-Content .run\gateway.log -Wait" -ForegroundColor Yellow

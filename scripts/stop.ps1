# ============================================================
# CryptoQuant 停止脚本（PowerShell）
# 停止 gateway / ARQ worker / vite 等由 start.ps1 启动的子进程。
# ============================================================
$ErrorActionPreference = "Continue"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")

function Stop-ByName($pattern) {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$pattern*" }
    foreach ($p in $procs) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "[OK] stopped pid=$($p.ProcessId) ($pattern)" -ForegroundColor Green
        } catch {
            Write-Host "[WARN] cannot stop pid=$($p.ProcessId): $_" -ForegroundColor Yellow
        }
    }
}

function Stop-ByPort($port) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        try {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
            Write-Host "[OK] stopped pid=$($c.OwningProcess) (port=$port)" -ForegroundColor Green
        } catch {
            Write-Host "[WARN] cannot stop pid=$($c.OwningProcess): $_" -ForegroundColor Yellow
        }
    }
}

Write-Host "=== 停止 gateway ==="
Stop-ByName "uvicorn app.main:app"

Write-Host "=== 停止 ARQ worker ==="
Stop-ByName "arq app.job.arq_app"

Write-Host "=== 停止 beat 调度器 ==="
Stop-ByName "app.job.scheduler"

Write-Host "=== 停止 vite / node ==="
$nodeProcs = Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*vite*" }
foreach ($p in $nodeProcs) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "[OK] stopped pid=$($p.ProcessId) (vite)" -ForegroundColor Green
    } catch {
        Write-Host "[WARN] cannot stop pid=$($p.ProcessId): $_" -ForegroundColor Yellow
    }
}

Write-Host "=== 兜底：按端口 8000/5173 ==="
Stop-ByPort 8000
Stop-ByPort 5173

Write-Host "done." -ForegroundColor Cyan

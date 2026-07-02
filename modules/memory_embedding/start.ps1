# modules/memory_embedding/start.ps1
#
# nomic-embed 启动 — bin/memory-embedding-serve.bat 的 PowerShell 替代
# 直接调 llama-server.exe (supervisor-friendly: 用 powershell -File 即可执行)
#
# 哥哥 2026-07-02 拍板: :8587 必须跑, 模型缺失就 exit 1 让 supervisor 处理
#
# Args layout: see modules/memory_embedding/module.json (single source of truth)

[CmdletBinding()]
param()

. $PSScriptRoot\..\..\deps\hermes-env.ps1

$Model  = Join-Path $HERMES_ROOT 'data\models\nomic-embed-text-v1.5-q4\nomic-embed-text-v1.5.Q4_K_M.gguf'
$Llama  = Join-Path $HERMES_ROOT 'runtime\llama-server.exe'
$LogDir = Join-Path $HERMES_ROOT 'data\logs'
$Log    = Join-Path $LogDir 'memory_embedding.log'

# 模型文件缺失 → FATAL, 不 fallback (哥哥 axiom: 本地优先 + 显式下载提示)
if (-not (Test-Path $Model)) {
    Write-Host '[FATAL] model missing:' -ForegroundColor Red
    Write-Host "         $Model"
    Write-Host '         下载地址: https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.Q4_K_M.gguf'
    Write-Host '         放到 data/models/nomic-embed-text-v1.5-q4/ 后重启 supervisor'
    exit 1
}
if (-not (Test-Path $Llama)) {
    Write-Host '[FATAL] llama-server.exe missing:' -ForegroundColor Red
    Write-Host "         $Llama"
    exit 1
}

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# Kill zombie on :8587
$Port = 8587
$netstat = & netstat -aon -p tcp 2>$null
foreach ($line in $netstat) {
    if ($line -match "TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)$") {
        $zpid = [int]$matches[1]
        if ($zpid -gt 0) {
            Write-Host "  killing zombie PID $zpid on :$Port..."
            try { & taskkill /F /PID $zpid /T 2>$null | Out-Null } catch {}
        }
    }
}
Start-Sleep -Seconds 1

Write-Host '============================================================'
Write-Host '  Hermes - memory_embedding (nomic-embed-text-v1.5 Q4)'
Write-Host "  Port:    $Port"
Write-Host '  Dims:    768  (pooling=mean)'
Write-Host '============================================================'
Write-Host ''

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $Llama
$psi.WorkingDirectory = $HERMES_ROOT
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.WindowStyle = 'Hidden'
$psi.Arguments = @(
    '-m', $Model,
    '--host', '127.0.0.1',
    '--port', "$Port",
    '-c', '2048',
    '-ngl', '0',  # CPU only — GPU offload (-ngl 99) lazy-load 卡死, 2026-07-02 Ikaros 验证
    '--embeddings',
    '--pooling', 'mean',
    '--alias', 'nomic-embed-text-v1.5-q4',
    '--cont-batching',
    '--flash-attn'
) -join ' '

$proc = [System.Diagnostics.Process]::Start($psi)
Write-Host "  [pid] $($proc.Id)"
Write-Host "  log:  $Log"
$proc.Dispose()
exit 0

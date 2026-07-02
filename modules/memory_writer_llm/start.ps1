# modules/memory_writer_llm/start.ps1
#
# DeepSeek-R1-Distill-Qwen-1.5B 推理服务 — memory_writer reduce 的核心 LLM
# 哥哥 2026-07-02 拍板: :8589 必须跑, 模型缺失就 exit 1 让 supervisor 处理

[CmdletBinding()]
param()

. $PSScriptRoot\..\..\deps\hermes-env.ps1

$Model  = Join-Path $HERMES_ROOT 'data\models\deepseek-r1-distill-qwen-1.5b-q4\DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf'
$Llama  = Join-Path $HERMES_ROOT 'runtime\llama-server.exe'
$LogDir = Join-Path $HERMES_ROOT 'data\logs'
$Log    = Join-Path $LogDir 'memory_writer_llm.log'

if (-not (Test-Path $Model)) {
    Write-Host '[FATAL] model missing:' -ForegroundColor Red
    Write-Host "         $Model"
    Write-Host '         下载地址: https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B-GGUF/resolve/main/deepseek-r1-distill-qwen-1.5b.q4_k_m.gguf'
    Write-Host '         放到 data/models/deepseek-r1-distill-qwen-1.5b-q4/ 后重启 supervisor'
    exit 1
}
if (-not (Test-Path $Llama)) {
    Write-Host '[FATAL] llama-server.exe missing:' -ForegroundColor Red
    Write-Host "         $Llama"
    exit 1
}

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# Kill zombie on :8589
$Port = 8589
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
Write-Host '  Hermes - memory_writer_llm (DeepSeek-R1-Distill-Qwen-1.5B)'
Write-Host "  Port:    $Port"
Write-Host '  Ctx:     8192  (jinja chat template, R1 reasoning)'
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
    '-c', '8192',
    '-ngl', '0',  # CPU only — GPU offload (-ngl 99) lazy-load 卡死, 2026-07-02 Ikaros 验证
    '--jinja',
    '--alias', 'DeepSeek-R1-Distill-Qwen-1.5B-q4',
    '--cont-batching',
    '--flash-attn', 'auto'
) -join ' '

$proc = [System.Diagnostics.Process]::Start($psi)
Write-Host "  [pid] $($proc.Id)"
Write-Host "  log:  $Log"
$proc.Dispose()
exit 0

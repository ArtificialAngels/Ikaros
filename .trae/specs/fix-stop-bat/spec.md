# Fix hermes-stop.bat Process Termination Spec

## Why
`hermes-stop.bat` 未能可靠终止 llama-server CUDA 进程和 Hermes 相关的 PowerShell 窗口（Console、Trace），导致 VRAM 未释放、残留进程堆积。

## What Changes
- **Step 1**: 改用 `Get-Process | Stop-Process -Force` 杀 llama-server（与 `model_manager.py` 一致，可穿透 CUDA 进程保护）
- **Step 2**: 确保 python hermes 进程被终止
- **Step 3**: 确保 node WebUI 进程被终止
- **Step 4**: 确保 Hermes-Console 和 Hermes-Trace PowerShell 窗口被关闭
- **Step 5**: 确保 gopeed-web 被终止

## Impact
- Affected specs: none
- Affected code: `bin/hermes-stop.bat`

## MODIFIED Requirements

### Requirement: Kill llama-server processes
系统 SHALL 使用 `Get-Process -Name 'llama-server*' | Stop-Process -Force` 终止所有 llama-server 变体（llama-server-cuda-12.4.exe 等），而不是 `taskkill /F /IM`。

#### Scenario: CUDA process termination
- **WHEN** llama-server-cuda-12.4.exe 正在运行
- **THEN** `hermes-stop.bat` 执行后该进程不再存在

### Requirement: Close PowerShell windows
系统 SHALL 通过 `Get-CimInstance Win32_Process` 匹配命令行中含 `hermes-console` 或 `hermes-trace` 的 powershell.exe 进程并终止。

#### Scenario: Console window close
- **WHEN** Hermes Console PowerShell 窗口正在运行
- **THEN** `hermes-stop.bat` 执行后该窗口被关闭

#### Scenario: Trace window close
- **WHEN** Hermes Trace PowerShell 窗口正在运行
- **THEN** `hermes-stop.bat` 执行后该窗口被关闭

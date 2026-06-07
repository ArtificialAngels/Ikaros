# Tasks

- [ ] Task 1: Rewrite `hermes-stop.bat` Step 1 to use `Get-Process | Stop-Process -Force`
  - Replace `taskkill /F /IM "llama-server-cuda-12.4.exe"` with PowerShell `Get-Process -Name 'llama-server*' | Stop-Process -Force`
  - Keep `-ErrorAction SilentlyContinue` to suppress "not found" noise
  - Add `Write-Host` to show which PIDs were killed

- [ ] Task 2: Verify Step 2-5 already use correct methods
  - Step 2 (python): uses Get-CimInstance + Stop-Process — correct
  - Step 3 (node): uses Get-CimInstance + Stop-Process — correct
  - Step 4 (console/trace): uses Get-CimInstance + Stop-Process — correct
  - Step 5 (gopeed): uses taskkill — correct (gopeed is not CUDA-protected)

# Task Dependencies
- Task 2 depends on Task 1 (read final file to verify all steps)

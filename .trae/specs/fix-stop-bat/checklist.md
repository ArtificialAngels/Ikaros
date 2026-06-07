# Checklist

- [x] Step 1 uses `Get-Process -Name 'llama-server*' | Stop-Process -Force` instead of `taskkill /F /IM`
- [x] Step 1 shows PID and process name for each killed process
- [x] Step 2 kills python processes with `hermes` in command line
- [x] Step 3 kills node processes with `hermes-web-ui` in command line
- [x] Step 4 kills powershell processes with `hermes-console` or `hermes-trace` in command line
- [x] Step 5 kills gopeed-web.exe
- [x] All steps report `[N/5]` progress labels
- [x] Script exits with code 0

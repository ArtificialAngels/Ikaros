@"
# Local development .env

# Use local data dir (not /data which is for Docker)
HERMES_DATA_DIR=E:/Hermes Agent/hermes/data

# Logging
LOG_LEVEL=INFO

# === LLM Keys (optional - leave empty for local-only mode) ===
# OPENAI_API_KEY=
# ANTHROPIC_API_KEY=
# OPENROUTER_API_KEY=
# GOOGLE_API_KEY=

# MiniMax Cloud (added by user)
MINIMAX_API_KEY=sk-cp-4uMQwR5EMEfPBPT2gmSUt8T6KUk713GtiVMXpuLe3JwJrXRcNDtIcSp5ayqKmrwNh7jdKuTD0RgIVWRnSRyOncPQlqIcFks8bsKrS6H27vUnvLUceKiMEXQ
MINIMAX_BASE_URL=https://api.minimaxi.chat/v1
MINIMAX_MODEL=MiniMax-Text-01

# === Local LLM (only used after llama-server is started) ===
# HERMES_LOCAL_LLM_URL=http://127.0.0.1:8080/v1
# HERMES_LOCAL_LLM_PORT=8080

# === Forced mode (uncomment to override auto-detection) ===
# HERMES_LLM_MODE=hybrid
# HERMES_LLM_MODE=local-only
# HERMES_LLM_MODE=cloud-only
"@ | Set-Content -Path "E:\Hermes Agent\.env" -Encoding UTF8
Write-Host ".env updated with MiniMax" -ForegroundColor Green
Get-Content "E:\Hermes Agent\.env" | Select-String "MINIMAX" | ForEach-Object { Write-Host "  $_" }
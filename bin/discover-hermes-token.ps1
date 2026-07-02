# discover-hermes-token.ps1
# Helper: extract the __HERMES_SESSION_TOKEN__ from a Hermes dashboard's index page.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File discover-hermes-token.ps1 -Port 34979
# Output: the token string (single line), or empty if not found.

param([int]$Port = 0)

if ($Port -le 0) { exit 1 }

try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    if ($r.Content -match '__HERMES_SESSION_TOKEN__="([^"]+)"') {
        Write-Output $matches[1]
        exit 0
    }
} catch {
    # Swallow connection errors — empty output means "no token here"
}

exit 1

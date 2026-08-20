# ikaros launcher (PowerShell) - UTF-8
$ErrorActionPreference = "Stop"

# IKAROS_ROOT self-anchored
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Env:IKAROS_ROOT = (Resolve-Path "$ScriptDir\..").Path

# source environment authority
& "$Env:IKAROS_ROOT\bin\ikaros-env.ps1"

# call Python core
$Python = if ($Env:IKAROS_PYTHON -and (Test-Path $Env:IKAROS_PYTHON)) {
    $Env:IKAROS_PYTHON
} else {
    "python"
}
& $Python "$Env:IKAROS_ROOT\core\ikarosctl.py" @args
exit $LASTEXITCODE

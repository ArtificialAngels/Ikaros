@echo off
setlocal
set "GN=%USERPROFILE%\.local\share\gitnexus\gitnexus\dist\cli\index.js"
set "NODE=E:\Hermes Agent\runtime\node23\node.exe"
if not exist "%GN%" (
  echo [gitnexus] ERROR: not installed at %GN%
  exit /b 1
)
"%NODE%" "%GN%" %*

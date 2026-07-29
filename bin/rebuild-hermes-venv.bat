@echo off
rem rebuild-hermes-venv.bat
rem Recreates the hermes venv from scratch. Drive-letter independent (uses %~dp0).
rem When to use: after the project is moved, the drive letter changes, or
rem hermes.exe / "import hermes_cli" breaks (canonicalize / ModuleNotFound).
rem IMPORTANT: close the Hermes dashboard and cd out of the venv dir first,
rem or the old venv cannot be removed (its files are locked by a running process).

set "ROOT=%~dp0.."
set "HERMES=%ROOT%\core\hermes"
set "VENV=%HERMES%\venv"
set "UV=%ROOT%\runtime\portable-python\Scripts\uv.exe"
set "PY=%ROOT%\runtime\portable-python\python.exe"

if exist "%VENV%" (
  echo Removing old venv at %VENV%
  rmdir /s /q "%VENV%"
)

echo Creating fresh venv...
"%UV%" venv --clear --python "%PY%" --system-site-packages "%VENV%"

echo Installing hermes-agent (editable)...
"%UV%" pip install --python "%VENV%\Scripts\python.exe" -e "%HERMES%"

echo.
echo Done. Verify with: %VENV%\Scripts\hermes.exe --help

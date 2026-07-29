@echo off
rem pin-ikaros-path.bat  -- RUN THIS AS ADMINISTRATOR
rem Makes the Ikaros volume reachable independent of the drive letter.
rem (1) Pins the current E: letter to this volume (persists per-device).
rem (2) Creates C:\Ikaros as a volume-GUID mount point (never changes).
rem After running: E:\Ikaros stays primary; C:\Ikaros is a letter-independent
rem alias to the SAME volume. If the letter ever changes, use C:\Ikaros and
rem re-run rebuild-hermes-venv.bat from there.
rem To undo the mount later:  mountvol C:\Ikaros /D

set "GUID=\\?\Volume{3f18f903-0000-0000-0000-100000000000}\"

echo Step 1: pin drive letter E: to this volume (no-op if already E:)
echo select volume E > %TEMP%\pin_disk.txt
echo assign letter=E >> %TEMP%\pin_disk.txt
diskpart /s %TEMP%\pin_disk.txt

echo Step 2: create C:\Ikaros volume-GUID mount point
if not exist C:\Ikaros mkdir C:\Ikaros
mountvol C:\Ikaros %GUID%

echo.
echo Done. Verify both paths point to the same volume:
echo   E:\Ikaros   (primary)
echo   C:\Ikaros   (letter-independent alias)
echo If the drive letter ever changes, open C:\Ikaros and run rebuild-hermes-venv.bat.

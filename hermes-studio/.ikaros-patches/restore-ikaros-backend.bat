@echo off
REM ========================================
REM 恢复 Ikaros 后端设置 key 继承补丁 (Windows)
REM ========================================
REM 用途: 把 .ikaros-patches/ikaros-backend.ts 恢复到
REM       packages/server/src/controllers/ikaros-backend.ts
REM 调用时机: Hermes Studio 更新 (git pull / npm install) 后重建本地定制
setlocal
set SCRIPT_DIR=%~dp0
set STUDIO_ROOT=%SCRIPT_DIR%..\..\hermes-studio
set SRC=%SCRIPT_DIR%ikaros-backend.ts
set DEST=%STUDIO_ROOT%\packages\server\src\controllers\ikaros-backend.ts
if not exist "%SRC%" (
  echo [ERROR] 源文件缺失: %SRC%
  exit /b 1
)
if not exist "%STUDIO_ROOT%\packages\server\src\controllers" mkdir "%STUDIO_ROOT%\packages\server\src\controllers"
copy /Y "%SRC%" "%DEST%" >nul
echo [SUCCESS] ikaros-backend.ts 已恢复到 %DEST%
echo 下一步: cd %STUDIO_ROOT% && npm run dev
endlocal

@echo off
REM ============================================================
REM data\models\初始模型下载.bat
REM
REM Hermes 初始模型下载器
REM   目标文件: Qwen3.6-35B-A3B-UD-Q3_K_M.gguf (~16.6 GB)
REM   来源    : ModelScope unsloth/Qwen3.6-35B-A3B-GGUF
REM
REM 设计要点:
REM   * 路径全部走 deps\hermes-env.bat 解析,绝无硬编码 E:\
REM   * 优先用系统 PATH 里的 modelscope,其次 portable-python 自带
REM   * 都没有则 pip install modelscope 到 portable-python (不污染系统)
REM   * 已存在则跳过 (节省带宽,支持重跑)
REM   * WITH_MMPROJ=1 时同步下载 mmproj-F16.gguf (多模态投影)
REM
REM 用法:
REM   双击 data\models\初始模型下载.bat
REM
REM 改源:修改下方 MS_REPO / MS_FILE 常量
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

REM ---- 切换常量(改这里即可) ----
set "MS_REPO=unsloth/Qwen3.6-35B-A3B-GGUF"
set "MS_FILE=Qwen3.6-35B-A3B-UD-Q3_K_M.gguf"
set "MS_MMPROJ=mmproj-F16.gguf"
set "WITH_MMPROJ=0"

REM ---- 解析 HERMES_ROOT / HERMES_MODELS / HERMES_PYTHON ----
call "%~dp0..\..\deps\hermes-env.bat"
if errorlevel 1 (
    echo [FATAL] deps\hermes-env.bat 解析失败
    pause
    exit /b 2
)
if not defined HERMES_MODELS set "HERMES_MODELS=%HERMES_ROOT%\data\models"

echo ============================================================
echo   Hermes 初始模型下载器
echo.
echo   源   : ModelScope %MS_REPO%
echo   文件 : %MS_FILE%  (~16.6 GB)
echo   目标 : %HERMES_MODELS%
if "%WITH_MMPROJ%"=="1" echo   附加 : %MS_MMPROJ%  (多模态)
echo ============================================================
echo.

REM ---- 1. 跳过已存在 ----
if exist "%HERMES_MODELS%\%MS_FILE%" (
    for %%A in ("%HERMES_MODELS%\%MS_FILE%") do set "EXIST_SZ=%%~zA"
    echo [SKIP] %MS_FILE% 已存在 ^(大小 !EXIST_SZ! 字节^),无需重新下载
    if "%WITH_MMPROJ%"=="1" (
        if exist "%HERMES_MODELS%\%MS_MMPROJ%" echo [SKIP] %MS_MMPROJ% 已存在
    )
    echo.
    pause
    exit /b 0
)

REM ---- 2. 定位 modelscope (PATH 优先,然后 portable-python,最后 pip install) ----
set "MS_CMD="
where modelscope >nul 2>&1
if not errorlevel 1 (
    set "MS_CMD=modelscope"
    echo [1/3] 找到系统 PATH 里的 modelscope
    goto :download
)

"%HERMES_PYTHON%" -c "import modelscope" >nul 2>&1
if not errorlevel 1 (
    set "MS_CMD=%HERMES_PYTHON% -m modelscope"
    echo [1/3] 找到 portable-python 里的 modelscope
    goto :download
)

echo [1/3] 未找到 modelscope,正在装到 portable-python ...
"%HERMES_PYTHON%" -m pip install --quiet modelscope
if errorlevel 1 (
    echo [FATAL] pip install modelscope 失败,请检查网络或手工执行:
    echo         "%HERMES_PYTHON%" -m pip install modelscope
    pause
    exit /b 1
)
"%HERMES_PYTHON%" -c "import modelscope" >nul 2>&1
if errorlevel 1 (
    echo [FATAL] modelscope 安装后仍不可用
    pause
    exit /b 1
)
set "MS_CMD=%HERMES_PYTHON% -m modelscope"

:download
echo.
echo [2/3] 下载主模型 %MS_FILE% ...
%MS_CMD% download --model %MS_REPO% %MS_FILE% --local_dir "%HERMES_MODELS%"
if errorlevel 1 (
    echo [FAIL] 主模型下载失败,见上方 ModelScope 错误
    pause
    exit /b 1
)

if "%WITH_MMPROJ%"=="1" (
    if not exist "%HERMES_MODELS%\%MS_MMPROJ%" (
        echo [2/3] 下载 %MS_MMPROJ% ...
        %MS_CMD% download --model %MS_REPO% %MS_MMPROJ% --local_dir "%HERMES_MODELS%"
        if errorlevel 1 echo [WARN] %MS_MMPROJ% 下载失败 ^(主模型已 OK^)
    )
)

REM ---- 3. 校验大小 ----
echo.
echo [3/3] 校验文件大小 ...
if not exist "%HERMES_MODELS%\%MS_FILE%" (
    echo [FAIL] 下载后文件不存在
    pause
    exit /b 1
)
for %%I in ("%HERMES_MODELS%\%MS_FILE%") do set "SZ=%%~zI"
echo       大小 : !SZ! 字节
if !SZ! LSS 1000000000 (
    echo [WARN] 体积过小 ^(小于 1 GB^),可能下载不完整
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   完成。
echo   启动 : bin\hermes-all.bat
echo   浏览器: http://localhost:8648/
echo ============================================================
echo.
pause
endlocal

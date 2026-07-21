@echo off
REM ========================================
REM Ikaros V5 Global Agent 自动恢复脚本 (Windows)
REM ========================================

setlocal enabledelayedexpansion

REM ========================================
REM 配置
REM ========================================
set "SCRIPT_DIR=%~dp0"
set "STUDIO_ROOT=%SCRIPT_DIR%..\..\hermes-studio"
set "PATCHES_DIR=%SCRIPT_DIR%"

REM ========================================
REM 检查环境
REM ========================================
if not exist "%STUDIO_ROOT%" (
    echo [ERROR] Hermes Studio 源码不存在: %STUDIO_ROOT%
    echo [INFO] 请确保 Studio 源码路径正确
    exit /b 1
)

echo [INFO] Studio 源码路径: %STUDIO_ROOT%
echo [INFO] 补丁目录: %PATCHES_DIR%

REM ========================================
REM 1. 复制 V5 Agent Manager
REM ========================================
echo [INFO] 复制 V5 Agent Manager...

set "V5_MANAGER_SRC=%PATCHES_DIR%\v5-agent-manager.ts"
set "V5_MANAGER_DST=%STUDIO_ROOT%\packages\server\src\services\v5-agent\manager.ts"

if exist "%V5_MANAGER_SRC%" (
    if not exist "%STUDIO_ROOT%\packages\server\src\services\v5-agent" (
        mkdir "%STUDIO_ROOT%\packages\server\src\services\v5-agent"
    )
    copy /Y "%V5_MANAGER_SRC%" "%V5_MANAGER_DST%" >nul
    echo [SUCCESS] V5 Agent Manager 已复制
) else (
    echo [ERROR] V5 Agent Manager 源码不存在: %V5_MANAGER_SRC%
    exit /b 1
)

REM ========================================
REM 2. 复制 V5 Agent Run Handler
REM ========================================
echo [INFO] 复制 V5 Agent Run Handler...

set "V5_HANDLER_SRC=%PATCHES_DIR%\handle-v5-agent-run.ts"
set "V5_HANDLER_DST=%STUDIO_ROOT%\packages\server\src\services\hermes\run-chat\handle-v5-agent-run.ts"

if exist "%V5_HANDLER_SRC%" (
    if not exist "%STUDIO_ROOT%\packages\server\src\services\hermes\run-chat" (
        mkdir "%STUDIO_ROOT%\packages\server\src\services\hermes\run-chat"
    )
    copy /Y "%V5_HANDLER_SRC%" "%V5_HANDLER_DST%" >nul
    echo [SUCCESS] V5 Agent Run Handler 已复制
) else (
    echo [ERROR] V5 Agent Run Handler 源码不存在: %V5_HANDLER_SRC%
    exit /b 1
)

REM ========================================
REM 3. 创建 V5 类型定义
REM ========================================
echo [INFO] 创建 V5 类型定义...

set "V5_TYPES_DST=%STUDIO_ROOT%\packages\server\src\services\hermes\run-chat\types-v5.ts"

(
echo // V5 Agent 类型定义（与 Ekko Agent 保持一致）
echo export interface V5AgentRunSocketData {
echo   input: string ^| any^[
echo   display_input?: string ^| any^[] ^| null
echo   display_role?: 'user' ^| 'command'
echo   storage_message?: string
echo   session_id?: string
echo   profile?: string
echo   provider?: string
echo   model?: string
echo   workspace?: string ^| null
echo   baseUrl?: string
echo   base_url?: string
echo   apiKey?: string
echo   api_key?: string
echo   mode?: 'scoped' ^| 'global'
echo   source?: string
echo   peerExcludeSocketId?: string
echo   queue_id?: string
echo   onEvent?: ^(event: string, payload: any^) =^> void
echo   coding_agent_id?: string
echo   agent_id?: string
echo }
) > "%V5_TYPES_DST%"

echo [SUCCESS] V5 类型定义已创建

REM ========================================
REM 4. 检查路由文件
REM ========================================
echo [INFO] 检查路由文件...

set "CHAT_RUN_ROUTE=%STUDIO_ROOT%\packages\server\src\routes\hermes\chat-run.ts"

if exist "%CHAT_RUN_ROUTE%" (
    findstr /C:"handle-v5-agent-run" "%CHAT_RUN_ROUTE%" >nul
    if %errorlevel% equ 0 (
        echo [SUCCESS] V5 导入已存在
    ) else (
        echo [WARN] V5 导入不存在，需要手动添加
        echo [INFO] 请在 %CHAT_RUN_ROUTE% 添加导入：
        echo   import { handleV5AgentRun } from '../../services/hermes/run-chat/handle-v5-agent-run'
    )

    findstr /C:"ikaros-v5" "%CHAT_RUN_ROUTE%" >nul
    if %errorlevel% equ 0 (
        echo [SUCCESS] V5 分支已存在
    ) else (
        echo [WARN] V5 分支不存在，需要手动添加
        echo [INFO] 请参考 %PATCHES_DIR%\ROUTE_PATCH_INSTRUCTIONS.md
    )
) else (
    echo [WARN] 路由文件不存在: %CHAT_RUN_ROUTE%
)

REM ========================================
REM 5. 检查 index.ts
REM ========================================
echo [INFO] 检查主入口文件...

set "INDEX_FILE=%STUDIO_ROOT%\packages\server\src\index.ts"

if exist "%INDEX_FILE%" (
    findstr /C:"shutdownV5AgentManager" "%INDEX_FILE%" >nul
    if %errorlevel% equ 0 (
        echo [SUCCESS] V5 关闭处理已存在
    ) else (
        echo [WARN] V5 关闭处理不存在，需要手动添加
        echo [INFO] 请在 %INDEX_FILE% 添加导入和关闭钩子
        echo   import { shutdownV5AgentManager } from './services/v5-agent/manager'
    )
) else (
    echo [WARN] 主入口文件不存在: %INDEX_FILE%
)

REM ========================================
REM 完成
REM ========================================
echo.
echo [SUCCESS] =========================================
echo [SUCCESS] Ikaros V5 Global Agent 恢复完成
echo [SUCCESS] =========================================
echo.
echo [INFO] 下一步：
echo 1. 检查并手动添加 V5 导入和分支（如需要）
echo 2. 运行 pnpm build 重新构建
echo 3. 重启 Hermes Studio
echo.
echo [INFO] 详细说明见: %PATCHES_DIR%\ROUTE_PATCH_INSTRUCTIONS.md

endlocal
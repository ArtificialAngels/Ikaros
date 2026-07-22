@echo off
rem ============================================================
rem Studio local update (fork-safe)
rem Pulls the latest hermes-studio from its official upstream
rem (EKKOLearnAI/hermes-studio), then reapplies Ikaros patches.
rem Does NOT touch the Ikaros repo's other directories.
rem Pure ASCII. No setlocal. No timeout.
rem ============================================================
set "HERE=%~dp0"
for %%I in ("%HERE%..") do set "ROOT=%%~fI"
call "%ROOT%\Ikaros-environment\init.bat"
set "PY=%ROOT%\runtime\portable-python\python.exe"
set "STUDIO=%ROOT%\hermes-studio"
set "PATCHES=%STUDIO%\.ikaros-patches"
set "LOGDIR=%ROOT%\data\logs"
set "TMPDIR=%ROOT%\tmp"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
if not exist "%TMPDIR%" mkdir "%TMPDIR%"
set "LOG=%LOGDIR%\studio-update.log"
set "_TMP=%TMPDIR%\studio-update-tmp.py"

cd /d "%ROOT%"

call :log "=== Studio local update started ==="
call :log "ROOT=%ROOT%"
call :log "STUDIO=%STUDIO%"
call :log "PY=%PY%"

rem ───────────────────────────────
rem 1. Check git remote
rem ───────────────────────────────
call :log "[1/7] Check git remote: studio-upstream"
"%PY%" -c "import subprocess,sys; r=subprocess.run(['git','remote','get-url','studio-upstream'],capture_output=True,text=True); sys.stdout.write('  remote-url: '+(r.stdout.strip() or '(not found)')+'\n'); sys.stdout.write('  exit: '+str(r.returncode)+'\n')" >> "%LOG%" 2>&1
if errorlevel 1 call :log "[1/7] WARN git remote check failed (continuing anyway)"

rem ───────────────────────────────
rem 2. Fetch upstream
rem ───────────────────────────────
call :log "[2/7] Fetch studio-upstream/main"
git fetch studio-upstream main >> "%LOG%" 2>&1
if errorlevel 1 (
  call :log "[2/7] FETCH FAILED -- cannot continue"
  call :log "DONE (aborted)"
  exit /b 1
)
call :log "[2/7] Fetch OK"

rem ───────────────────────────────
rem 3. Fetch stats + stash dirty count (single-line Python OK)
rem ───────────────────────────────
"%PY%" -c "import subprocess,sys; r=subprocess.run(['git','rev-list','--count','HEAD..studio-upstream/main'],capture_output=True,text=True); sys.stdout.write('  unpicked-commits: '+(r.stdout.strip() or '0')+'\n')" >> "%LOG%" 2>&1
"%PY%" -c "import subprocess,sys; r=subprocess.run(['git','rev-parse','--short','studio-upstream/main'],capture_output=True,text=True); sys.stdout.write('  upstream-head: '+(r.stdout.strip() or '(unknown)')+'\n')" >> "%LOG%" 2>&1

call :log "[3/7] Stash local hermes-studio changes"
"%PY%" -c "import subprocess,sys; r=subprocess.run(['git','status','--porcelain','--','hermes-studio/'],capture_output=True,text=True); dirty=[l for l in r.stdout.splitlines() if l.strip()]; sys.stdout.write('  dirty-files: '+str(len(dirty))+'\n')" >> "%LOG%" 2>&1
git stash push -m "studio-local-update-autostash" -- hermes-studio/ >> "%LOG%" 2>&1
set _STASH_EC=%errorlevel%
if %_STASH_EC% neq 0 (
  call :log "[3/7] Stash returned %_STASH_EC% -- stale stash may still exist"
) else (
  call :log "[3/7] Stash OK"
)

rem ───────────────────────────────
rem 4. Extract upstream (temp Python file to avoid batch multi-line quoting bugs)
rem ───────────────────────────────
call :log "[4/7] Extract studio-upstream into hermes-studio/"
> "%_TMP%" echo import subprocess,tarfile,io,sys
>> "%_TMP%" echo data=subprocess.check_output(['git','archive','studio-upstream/main'])
>> "%_TMP%" echo sys.stdout.write('  archive-size: '+str(len(data))+' bytes\n')
>> "%_TMP%" echo tf=tarfile.open(fileobj=io.BytesIO(data))
>> "%_TMP%" echo members=tf.getmembers()
>> "%_TMP%" echo sys.stdout.write('  archive-members: '+str(len(members))+'\n')
>> "%_TMP%" echo tf.extractall(r'%STUDIO%', filter='data')
>> "%_TMP%" echo sys.stdout.write('  extract OK\n')
"%PY%" "%_TMP%" >> "%LOG%" 2>&1
set _EXTRACT_EC=%errorlevel%
del "%_TMP%" >nul 2>&1
if %_EXTRACT_EC% neq 0 (
  call :log "[4/7] ARCHIVE EXTRACT FAILED (exit %_EXTRACT_EC%) -- rolling back"
  git stash pop >> "%LOG%" 2>&1
  call :log "DONE (aborted)"
  exit /b 1
)
call :log "[4/7] Extract OK"

rem ───────────────────────────────
rem 5. Restore local changes (stash pop)
rem ───────────────────────────────
call :log "[5/7] Restore local changes"
git stash pop >> "%LOG%" 2>&1
set _POP_EC=%errorlevel%
if %_POP_EC% equ 0 (
  call :log "[5/7] Stash pop OK (local changes restored)"
) else if %_POP_EC% equ 1 (
  call :log "[5/7] Stash pop CONFLICTS -- manual resolution may be needed"
) else (
  call :log "[5/7] Stash pop FAILED code=%_POP_EC%"
)

rem ───────────────────────────────
rem 6. Reapply Ikaros patches (temp Python for route patch)
rem ───────────────────────────────
call :log "[6/7] Reapply Ikaros patches"

rem 6a. v5-agent-manager.ts
if exist "%PATCHES%\v5-agent-manager.ts" (
  copy /Y "%PATCHES%\v5-agent-manager.ts" "%STUDIO%\packages\server\src\services\v5-agent\manager.ts" >> "%LOG%" 2>&1
  if errorlevel 1 ( call :log "[6a] FAILED v5-agent-manager.ts"
  ) else ( call :log "[6a] OK v5-agent-manager.ts" )
) else ( call :log "[6a] SKIP v5-agent-manager.ts (patch file missing)" )

rem 6b. handle-v5-agent-run.ts
if exist "%PATCHES%\handle-v5-agent-run.ts" (
  copy /Y "%PATCHES%\handle-v5-agent-run.ts" "%STUDIO%\packages\server\src\services\hermes\run-chat\handle-v5-agent-run.ts" >> "%LOG%" 2>&1
  if errorlevel 1 ( call :log "[6b] FAILED handle-v5-agent-run.ts"
  ) else ( call :log "[6b] OK handle-v5-agent-run.ts" )
) else ( call :log "[6b] SKIP handle-v5-agent-run.ts (patch file missing)" )

rem 6c. v5-system-prompt.ts
if exist "%PATCHES%\v5-system-prompt.ts" (
  copy /Y "%PATCHES%\v5-system-prompt.ts" "%STUDIO%\packages\server\src\services\hermes\run-chat\v5-system-prompt.ts" >> "%LOG%" 2>&1
  if errorlevel 1 ( call :log "[6c] FAILED v5-system-prompt.ts"
  ) else ( call :log "[6c] OK v5-system-prompt.ts" )
) else ( call :log "[6c] SKIP v5-system-prompt.ts (patch file missing)" )

rem 6d. types-v5.ts (single-line Python OK)
"%PY%" -c "import os; target=r'%STUDIO%\packages\server\src\services\hermes\run-chat\types-v5.ts'; open(target,'w').write('// V5 Agent types (generated by studio-local-update)\nexport interface V5AgentRunSocketData {\n  input: string | any[];\n  display_input?: string | any[] | null;\n  session_id?: string;\n  profile?: string;\n  provider?: string;\n  model?: string;\n  baseUrl?: string;\n  base_url?: string;\n  apiKey?: string;\n  api_key?: string;\n  coding_agent_id?: string;\n  agent_id?: string;\n}\n'); import sys; sys.stdout.write('  regenerated: '+str(os.path.getsize(target))+' bytes\n')" >> "%LOG%" 2>&1

rem 6e. Route patches (temp Python file)
> "%_TMP%" echo import sys, os
>> "%_TMP%" echo P = r'%STUDIO%\packages\server\src\services\hermes\run-chat\index.ts'
>> "%_TMP%" echo if not os.path.exists(P): sys.stdout.write('  ROUTE-PATCH SKIP: run-chat/index.ts not found\n'); sys.exit(0)
>> "%_TMP%" echo c = open(P, encoding='utf-8').read()
>> "%_TMP%" echo steps = []
>> "%_TMP%" echo if 'handle-v5-agent-run' not in c:
>> "%_TMP%" echo   c = c.replace(\"import { handleEkkoAgentRun } from './handle-ekko-agent-run'\", \"import { handleEkkoAgentRun } from './handle-ekko-agent-run'\\nimport { handleV5AgentRun } from './handle-v5-agent-run'\")
>> "%_TMP%" echo   steps.append('import')
>> "%_TMP%" echo if 'isV5AgentExecution' not in c:
>> "%_TMP%" echo   v5fn = '\\nfunction isV5AgentExecution(data?: { coding_agent_id?: string; agent_id?: string }): boolean {\\n  return data?.coding_agent_id === \\'ikaros-v5\\' || data?.agent_id === \\'ikaros-v5\\'\\n}\\n'
>> "%_TMP%" echo   c = c.replace('function isEkkoAgentExecution(data?: { coding_agent_id?: string; agent_id?: string }): boolean {', v5fn + 'function isEkkoAgentExecution(data?: { coding_agent_id?: string; agent_id?: string }): boolean {')
>> "%_TMP%" echo   steps.append('function')
>> "%_TMP%" echo if 'handleV5AgentRun' not in c:
>> "%_TMP%" echo   v5br = '    if (isV5AgentExecution(data)) {\\n      await handleV5AgentRun(\\n        this.nsp,\\n        socket,\\n        data,\\n        profile,\\n        this.sessionMap,\\n        this.dequeueNextQueuedRun.bind(this),\\n      )\\n      return\\n    }\\n\\n'
>> "%_TMP%" echo   c = c.replace('    if (isEkkoAgentExecution(data)) {', v5br + '    if (isEkkoAgentExecution(data)) {')
>> "%_TMP%" echo   steps.append('dispatch')
>> "%_TMP%" echo if steps:
>> "%_TMP%" echo   open(P, 'w', encoding='utf-8').write(c)
>> "%_TMP%" echo   sys.stdout.write('  ROUTE-PATCH applied: '+','.join(steps)+'\n')
>> "%_TMP%" echo else:
>> "%_TMP%" echo   sys.stdout.write('  ROUTE-PATCH no changes (all patches already applied)\n')
"%PY%" "%_TMP%" >> "%LOG%" 2>&1
del "%_TMP%" >nul 2>&1

call :log "[6/7] Reapply patches done"

rem ───────────────────────────────
rem 7. npm install
rem ───────────────────────────────
call :log "[7/7] npm install (start)"
cd /d "%STUDIO%"
if not exist "%STUDIO%\node_modules" (
  call :log "[7/7] node_modules missing -- running full install"
  call npm install --no-audit --no-fund >> "%LOG%" 2>&1
) else (
  call :log "[7/7] node_modules exists -- prefer-offline"
  call npm install --no-audit --no-fund --prefer-offline >> "%LOG%" 2>&1
)
if errorlevel 1 (
  call :log "[7/7] npm install FAILED"
) else (
  call :log "[7/7] npm install OK"
)

call :log "=== Studio local update finished ==="
echo DONE >> "%LOG%"
goto :eof

rem ── log subroutine ──
rem Usage: call :log "message"
rem Appends a timestamped line to %LOG%.
:log
set _TS=%DATE% %TIME%
echo [%_TS%] %* >> "%LOG%"
goto :eof

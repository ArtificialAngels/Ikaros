@echo off
rem ============================================================
rem Restore V5 Agent route patches (Windows)
rem Applies V5 agent dispatch to hermes-studio source files.
rem ============================================================
set "HERE=%~dp0"
for %%I in ("%HERE%..") do set "ROOT=%%~fI"
set "STUDIO=%ROOT%\hermes-studio"
set "SRC=%STUDIO%\packages\server\src"
set "PY=%ROOT%\runtime\portable-python\python.exe"

echo [restore-v5-route] Starting...
"%PY%" -c "
import os, re

src = r'%SRC%'
runchat = os.path.join(src, 'services', 'hermes', 'run-chat', 'index.ts')

with open(runchat, 'r', encoding='utf-8') as f:
    content = f.read()

changed = False

# 1. Add import if missing
if 'handle-v5-agent-run' not in content:
    content = content.replace(
        \"import { handleEkkoAgentRun } from './handle-ekko-agent-run'\",
        \"import { handleEkkoAgentRun } from './handle-ekko-agent-run'\\nimport { handleV5AgentRun } from './handle-v5-agent-run'\"
    )
    changed = True
    print('[1/3] V5 agent import added')

# 2. Add isV5AgentExecution function if missing
if 'isV5AgentExecution' not in content:
    v5_fn = '''
function isV5AgentExecution(data?: { coding_agent_id?: string; agent_id?: string }): boolean {
  return data?.coding_agent_id === 'ikaros-v5' || data?.agent_id === 'ikaros-v5'
}
'''
    content = content.replace(
        'function isEkkoAgentExecution(data?: { coding_agent_id?: string; agent_id?: string }): boolean {',
        v5_fn + 'function isEkkoAgentExecution(data?: { coding_agent_id?: string; agent_id?: string }): boolean {'
    )
    changed = True
    print('[2/3] V5 dispatch function added')

# 3. Add V5 dispatch branch if missing
if 'handleV5AgentRun' not in content:
    v5_branch = '''    if (isV5AgentExecution(data)) {
      await handleV5AgentRun(
        this.nsp,
        socket,
        data,
        profile,
        this.sessionMap,
        this.dequeueNextQueuedRun.bind(this),
      )
      return
    }

'''
    content = content.replace(
        '    if (isEkkoAgentExecution(data)) {',
        v5_branch + '    if (isEkkoAgentExecution(data)) {'
    )
    changed = True
    print('[3/3] V5 run branch added')

if changed:
    with open(runchat, 'w', encoding='utf-8') as f:
        f.write(content)
    print('[restore-v5-route] Done - runchat patched.')
else:
    print('[restore-v5-route] No changes needed - route patches already applied.')
"

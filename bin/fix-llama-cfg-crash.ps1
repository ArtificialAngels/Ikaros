@'
llama-server.exe CFG/Exploit Protection 崩溃修复工具

问题: llama-server.exe 被 Windows Defender Exploit Protection (CFG) 杀掉
现象: watchdog 反复重启 :8080/:8587 失败, data/.llama_cfg_crashed 文件生成

修复方法 (管理员权限):
  以管理员身份运行此 PowerShell 脚本，或在 PowerShell (管理员) 中执行:
    Set-ProcessMitigation -Name llama-server.exe -Disable CFG

验证:
  重新启动控制面板 -> Memory 组件 -> 查看 logs/memory-watchdog.log 确认 :8080/:8587 正常
'@

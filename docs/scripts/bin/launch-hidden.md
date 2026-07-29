# bin/launch-hidden.vbs — 完全隐藏启动

## 用途
用 `WScript.Shell.Run cmd, 0, False` 以完全隐藏（无窗口）方式启动命令，立即返回。

## 用法
```
wscript.exe launch-hidden.vbs "command to run"
```

## 说明
- 命令以 detached 方式运行，本脚本不等待其结束。
- 被各启动脚本广泛用于拉起后台服务（watchdog / voice-ws / studio / desktop）。

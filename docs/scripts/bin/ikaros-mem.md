# bin/ikaros-mem.bat — Memory V5.1 CLI 封装

## 用途
把 python 调用路由到 `portable-python`（含 chromadb + aiosqlite + sqlite-vec），而非默认的 `hermes-agent` venv（**不含**这些包）。

## 用法
```
ikaros-mem stats
ikaros-mem search "query"
ikaros-mem store "content" --type fact --weight 0.7
ikaros-mem decay
```

## 实现
- 默认 `MEM_SCRIPT=%~dp0..\Ikaros-memory\v5\store.py`，转发全部参数。
- 先 `call init.bat`（静默），确保 `IKAROS_PYTHON` 已设置。

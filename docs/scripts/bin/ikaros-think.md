# bin/ikaros-think.bat — V5.1 自思考循环

## 用途
运行 V5.1 自我思考（metacog）循环；本质是 `Ikaros-memory/v5/think.py` 的薄封装。

## 用法
```
ikaros-think             -- 跑一轮思考（适合 cron）
ikaros-think --watch     -- 每 5 分钟循环（daemon）
ikaros-think --interval=N -- 自定义间隔（分钟）
```

## 退出码
- `0` = 产生思考
- `1` = 无思考

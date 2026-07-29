# bin/ikaros-verify.bat — V5.1 测试入口

## 用途
Ikaros V5.1 测试套件 GREEN 入口；被 `ikaros-start.bat` 作为 `[Step 0]`（带 `--quick`）接入。

## 用法
```
ikaros-verify              -- 运行全部 V5.1 测试（全套）
ikaros-verify --quick      -- 只跑 goal_contract 测试
set IKAROS_SKIP_VERIFY=1   -- 静默（立即 exit 0）
```

## 退出码
- `0` = 全部通过
- `1` = 任一失败

## 说明
- 独立调用时若未初始化会先 `call init.bat`。
- 不用 `setlocal`：变量须透传给调用方（start 链需要）。

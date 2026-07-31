# bin/ikaros-soul-sync.py — V5 → Hermes SOUL.md 同步

## 用途（原模块 docstring）
替换 2026-07-05 损坏的 V3 Memory Plugin AUTO-SYNCED 机制（外部同步，不修改 core/hermes）。读取 V5 `self_model.json` + `axiom.md` + `v4.db` lessons，生成 `SOUL.md`，写入 `data/hermes-agent/SOUL.md`。

## 用法
```
python ikaros-soul-sync.py [--once | --watch N]
  --once     Sync once and exit (default)
  --watch N  Sync every N seconds (daemon mode)
```
调用方：`ikaros-start.bat`（Step 2c，memory watchdog 启动后）；V5 metacog cycle（可选，经 reflect scheduler）。

## 生成内容（_build_soul_md）
1. 核心身份（来自 self_model.identity：name/nature/creator/vibe + self_narrative）
2. 存在公理（axiom.md）
3. 信念与价值观（self_model.beliefs，前 8）
4. 当前情感状态（affect.json 的 PAD + mood_label）
5. 经验教训（v4.db 高权重 rule/lesson/config/identity/axiom，前 5）
6. 能力清单（ikaros-identity/capabilities.md）

文件头标注 `<!-- AUTO-SYNCED ... DO NOT manually edit -->`。

## 路径自举
`IKAROS_ROOT` = env 或 `Path(__file__).resolve().parent.parent`；`IKAROS_MEMORY = IKAROS_ROOT/core/memory_v5`；`HERMES_HOME` = env 或 `IKAROS_ROOT/data/hermes-agent`。`sys.path.insert(0, IKAROS_MEMORY)`。

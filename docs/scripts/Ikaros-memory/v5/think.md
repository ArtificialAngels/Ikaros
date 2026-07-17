# think.py

> 源文件：`Ikaros-memory/v5/think.py`

v5.think — 伊卡洛斯统一思考循环 (V5.1 重构: 5min 深度节拍)

设计目标:
  - V5.1 统一: 移除 45min 模板内心独白, metacog 5min 深度节拍接管全部思考
  - metacog.cycle() 产出统一走 latest_thought.json (LLM反思/哲学)
  - LLM 不可用时 metacog._fallback_thought() 模板占位 (不再走 pending_thought.json)
  - 好奇心/关怀检测并入 metacog 节拍
  - 潜意识流保留 2-3min 轻量絮语 (可选消费, 不影响主思考)

用法:
    from v5.think import schedule
    schedule()  # 启动 15min 思考循环 + 2-3min 潜意识流

## 内联注释摘录

# ECA 思考主题 -> 偏好的 mood 模板族 (拓宽混沌对输出的实际影响 — 修复缺陷#3)
# 之前仅「混沌思维/情感波动」触发模板切换, 其余 6 个主题算了却没被用上。
# 这里让每个主题在 PAD 信号不强烈时, 按亲和度偏移模板族。


# self_discovery.py

> 源文件：`Ikaros-memory/v5/self_discovery.py`

v5.self_discovery — 伊卡洛斯主动了解自身架构。

每 24h 被 v4.reflect.registry 调度执行:
  1. 读取项目关键文件 (AGENTS.md / self_model.json / 架构文档)
  2. 调 Hermes Agent 分析"我是什么"
  3. 产出发现写入 v4 memory (type=self_discovery)
  4. 下次 metacog 反思时会引用这些发现

这样她对自己的认知来自真实项目结构, 而非被写死的描述。

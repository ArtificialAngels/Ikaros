# router.py

> 源文件：`Ikaros-memory/v5/router.py`

v5.router — 对话/任务分类 + 任务指令优化

目标:
  把哥哥的输入分为"对话"和"任务"两类。
  - 对话 → 走现有 V5 情感链, 直达 cloud LLM
  - 任务 → 本地 LLM 预处理: 检索 V4 记忆 + 结构化为
    有上下文/目标/约束的 prompt, 再送 cloud LLM
    
  省 token: 模糊的任务描述 → 精炼的任务 prompt

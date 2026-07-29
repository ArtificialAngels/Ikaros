# llm_client.py

> 源文件：`Ikaros-memory/v5/reflect/llm_client.py`

v5.reflect.llm_client — V5.1 LLM client (DeepSeek + 本地 Qwen2.5-7B)

设计目标:
  - 双轨: 本地小模型 (Qwen2.5-7B :8080) + cloud 大模型 (DeepSeek)
  - 统一接口: 一处定义, 两处实现, 调用方不感知
  - 密钥零接触: API key 只从 os.environ / .env 读, 不写进代码, 不进 git
  - 显式错误: 失败时抛, 不静默

V3 vs V4:
  - V3 memory_reflect.py 风格的本地小模型调用 (现本地模型为 Qwen2.5-7B)
  - V4 新增大模型反思 (哥哥 id 158 长线目标), 用 DeepSeek
  - 小模型仍然在 (consolidate 提取用, 因为便宜/快)

## 内联注释摘录

# ─── 启动时自动从 .env 读 DEEPSEEK_API_KEY ─────────────────────
# 哥哥 (2026-07-05) K1b 决策: 用 python-dotenv 自动加载
# Hermes Agent .env 在 <IKAROS_ROOT>/data/hermes-agent/.env (HERMES_HOME env)
# 优先 HERMES_HOME 路径, 然后 Ikaros 默认, 最后 V4 自己的 .env (允许覆盖)

        # 思考模型 (deepseek-v4-flash / reasoner) 可能把答案放在 reasoning_content,
        # content 为空 → 兜底取 reasoning_content, 与 _call_local 行为保持一致,
        # 否则本地小模型挂掉时云端兜底会误报 "empty content" 而整体失败


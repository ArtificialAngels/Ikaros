# task_runner.py

> 源文件：`Ikaros-memory/v5/task_runner.py`

v5.task_runner — 后台任务执行 + 结果持久化 + 主动提醒

流程:
  1. task_runner.call_async(optimized_text, original_text)
     → spawn 后台线程调 cloud LLM
     → 立即返 {"status": "running", "task_id": "..."}
  2. LLM 完成 → 写结果到 data/v5/task_result.json
  3. cloud_chat.build_system_prompt() 每次检查
     → 有结果则注入 "哥哥，任务完成了，有空听吗？"
  4. 用户反应:
     - "有空" → deliver result, 清文件
     - "没空" → 写 pending 标记, cron 后重提

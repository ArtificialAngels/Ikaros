# scheduler.py

> 源文件：`Ikaros-memory/v5/reflect/scheduler.py`

v5.reflect.scheduler — V5.1 反思周期调度器

V3 痛点 (已查, 757 行 memory_reflect.py):
  - trigger 与 logic 混在 reflect_cycle() (line 594-678)
  - 主控 try/except 全包 + 返 -1 = 沉默失败 6 处
  - _DEFAULT_DEDUP_INTERVAL 被复用到 cleanup (line 649, V3 小 bug)
  - 反思后 import chromadb 死 (line 669, V3 沉默失败)

V4 修复:
  - trigger 与 logic 分离: 调度器只回答 "该跑吗", 不回答 "怎么跑"
  - 错误显式: 让异常上抛, 不吞
  - 每个操作独立 interval 常量, 不复用
  - 不在调度器里 import 重依赖 (chromadb 留给 vector 模块)

## 内联注释摘录

# V4 反思状态持久化到 Ikaros-memory/data/v4/ (与 V3 data/ 并列, 不污染)
# 注意: Path 是 v4/reflect/scheduler.py, parent=v4/reflect, parent.parent=v4/,
# parent.parent.parent=Ikaros-memory/, + "data/v4" → Ikaros-memory/data/v4/


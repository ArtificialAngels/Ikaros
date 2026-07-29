# migrate_from_v3.py

> 源文件：`Ikaros-memory/v5/migrate_from_v3.py`

v4.migrate_from_v3 — V3 → V4 数据迁移脚本

哥哥 (2026-07-05) 拍 C: 跳过低 weight (< 0.5), 只迁移 0.5+ 的记忆
(weight 是 V3 自评质量分, 低分记忆不值得带进 V4)

用法:
  python v4/migrate_from_v3.py            # 跑迁移 (默认路径)
  python v4/migrate_from_v3.py --dry-run  # 只统计, 不写入
  python v4/migrate_from_v3.py --src PATH --dst PATH  # 自定义路径

V3 db 默认: E:\Ikaros\core\v5\data\v3.db
V4 db 默认: E:\Ikaros\core\v5\data\v4\v4.db

设计原则:
  - 1:1 迁移 (id, content, type, tags, weight, access_count, last_accessed, created 全保)
  - 跳过低 weight (< 0.5)
  - 跳过已存在 (按 content dedup)
  - 不动 V3 db (只读)
  - 显式错误 (失败时抛, 不静默)
  - dry-run 模式 (统计 + 不写入, 验证迁移范围)

## 内联注释摘录

# V3 db 与 V4 db 同在 Ikaros-memory/data/ 下
# V4_ROOT = Ikaros-memory/  (parent of v4/ dir)
# V3 db: V4_ROOT / "data" / "v3.db"  (不是 .parent! 那是上层)
# V4 db: V4_ROOT / "data" / "v4" / "v4.db"


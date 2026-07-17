# search.py

> 源文件：`Ikaros-memory/v5/search.py`

v5.search — V5 语义搜索层 (ChromaDB) (代码于 2026-07-12 由 v4/ 迁入 v5/)

设计目标:
  - API 与 V3 vector_search.py 兼容
  - 显式依赖: chromadb 1.5+ (portable-python 已装, 见 ikaros-paths.json)
  - 不在 import 时崩溃 (V3 沉默失败根因)

V3 沉默失败根因 (2026-07-05 复盘):
  V3 走默认 `python` (hermes-agent venv), 该 venv 没装 chromadb
  → import 死, search 返空
  V4 修: 走 portable-python (有 chromadb 1.5.2) + 文档明示

## 内联注释摘录

# ── 运行时内存缓存 (性能优化, 哥哥优化项) ──
# 1) Embedding LRU: (task+text) -> vector, 进程级, 削 :8587 忙时尖峰 + 冷启动
# 2) VectorIndex 单例: 复用 chroma 客户端, 不再每轮重开 (冷启动 850ms); 周期刷新拾取外部新增记忆


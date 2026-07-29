# memory_tool.py

> 源文件：`Ikaros-memory/v5/tools/memory_tool.py`

v5.tools.memory_tool — 5 memory tools.

  v5_memory_store(content, ...)     -> store a memory, return {id, ok}
  v5_memory_search(query, ...)      -> fused / FTS5 / emotion-tag search
  v5_memory_get(memory_id)          -> fetch one memory
  v5_memory_delete(memory_id)       -> delete one memory
  v5_memory_stats()                 -> storage statistics

Extended from the legacy mcp_server v5_store/v5_search with Ekko-style
structured fields (domain / category_path / key) and time / emotion filters.

## 内联注释摘录

    # 2. delegate retrieval to the unified memory API.  It runs the same 3-way
    #    fuse (memory_retrieval) with a FTS5 fallback and honors time_range, so
    #    behavior is identical to the old inline paths.  We re-apply the
    #    min_weight / exclude filters the previous tool applied so callers get
    #    the same shaped results.


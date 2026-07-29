# memory_api.py

> 源文件：`Ikaros-memory/v5/memory_api.py`

v5.memory_api — unified memory interface for V5.

Wraps store.py + search.py + data/v5/*.json into one interface that supports
TWO addressing modes:

  1. V5-native free-form memory
        api.store("哥哥喜欢简洁", memory_type="preference")
        api.search("哥哥 喜欢")                 # semantic fuse

  2. Ekko-style structured (exact) addressing
        api.store("...", domain="project_x", key="deadline")
        api.search(domain="project_x")         # exact tag match
        api.search(key="deadline")             # exact tag match

Structured fields are encoded into tags:
    v5_domain:<domain>   v5_cat:<category_path>   v5_key:<key>

So a single store() call serves both a semantic vector index AND an
exact-match knowledge base — no separate tables required.

## 内联注释摘录

                # `retrieve` swallows embedding / ChromaDB errors and may return an
                # EMPTY list even when FTS5 has matches (e.g. :8080 is down).  Only
                # take the fused result when it's non-empty; otherwise fall through
                # to the FTS5-only fallback below so offline search still works.


# bin/ikaros-embed-server.py — 临时 embedding 服务

## 用途（原模块 docstring）
临时替代 llama-server :8587 的 embedding 服务。用 chromadb `DefaultEmbeddingFunction`（ONNX MiniLM L6 v2，384 维），提供与 llama-server 相同的 `/embedding` API，供 Ikaros 记忆搜索使用。

## 重要注意
- 384 维 vs nomic-embed-text 768 维 —— 已有 chroma 向量不兼容。
- FTS5 搜索仍可用；切回 llama-server 后向量搜索需重建。

## 接口
- `POST /embedding` 与 `POST /v1/embeddings`
- 输入：`{"content": "text"}`（Ikaros）或 `{"input": "text"}`（OpenAI 格式）
- 文本截断到 500 字符（与 Ikaros 对齐）
- 输出 llama-server 格式：`[{"index":0,"embedding":[[...]]}]`
- float32 → float 转换后 JSON 序列化

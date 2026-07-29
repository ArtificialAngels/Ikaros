# 上游升级候选报告

Generated: 2026-06-16T16:55:12.446832+00:00

This report lists what has changed in our upstream sources since we
pinned them. Each candidate needs manual review before picking.

## core/hermes

- URL: https://github.com/NousResearch/core/hermes.git
- Pinned: v2026.6.5
- Our integration point: `core/hermes/`
- Where our dev lives: `modules/,bridge/,hermes/`

### Commits since v2026.6.5

```
c6e99ab3 Merge pull request #46959 from NousResearch/bb/composer-model-selector
```

## hermes-web-ui

- URL: https://github.com/EKKOLearnAI/hermes-web-ui.git
- Pinned: 0.6.14
- Our integration point: `runtime/node23/node_modules/hermes-web-ui/`
- Where our dev lives: `modules/webui_proxy/,modules/webui/`

> Not cloned. Run `python bin/hermes-upstream-sync.py pull hermes-web-ui`.

## llama.cpp

- URL: https://github.com/ggml-org/llama.cpp.git
- Pinned: b9503
- Our integration point: `runtime/`
- Where our dev lives: `modules/llm_engine/`

> Not cloned. Run `python bin/hermes-upstream-sync.py pull llama.cpp`.

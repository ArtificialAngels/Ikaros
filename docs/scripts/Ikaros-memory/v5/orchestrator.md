# orchestrator.py

> 源文件：`Ikaros-memory/v5/orchestrator.py`

v5.orchestrator — V5 Agent runtime (companion delegation + agent loop).

This module is the *only* new entry point that sits "above" the existing
``bin/cloud_chat.py`` pipeline.  It implements the V5 agent-ization plan
(Step 3) without ever modifying ``cloud_chat.py``.

Two runtime modes, selected by the ``V5_AGENT_MODE`` environment variable:

  * ``companion`` (default) — delegate straight to ``cloud_chat.cloud_chat``.
    The full emotion / task / 4-injection pipeline stays 100% unchanged.
  * ``agent``  (new)          — run a think -> tool_call -> observe loop.
    The local LLM decides which ``v5_*`` tool to call; the tool result is
    fed back to the LLM to synthesize a natural reply.

Hard guarantees (from the plan):
  * cloud_chat.py is NEVER modified.
  * Every failure degrades gracefully:
      - agent loop thinks with no LLM        -> fall back to companion
      - agent loop picks an unknown tool      -> fall back to companion
      - agent loop can't synthesize a reply   -> fall back to companion
      - companion pipeline errors             -> return a safe string

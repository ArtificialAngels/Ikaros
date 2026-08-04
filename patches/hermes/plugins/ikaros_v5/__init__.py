"""Ikaros V5 external plugin — context engine + memory provider.

Lives OUTSIDE the hermes repo at ``$HERMES_HOME/plugins/ikaros_v5/``
(``data/hermes-agent/plugins/ikaros_v5/`` in Ikaros), so hermes updates
never touch it. It is discovered by TWO native hermes systems, no source
patch required:

  1. General plugin system (plugin.yaml + ``register(ctx)``) — registers
     the context engine via ``ctx.register_context_engine()``. Selection:
     ``context.engine: ikaros_v5`` in config.yaml.
  2. Memory provider system (source scan for ``MemoryProvider``) —
     instantiates :class:`IkarosV5MemoryProvider`. Selection:
     ``memory.provider: ikaros_v5`` in config.yaml.

The general plugin system is opt-in, so the plugin must be listed in
``plugins.enabled`` (see data/hermes-agent/config.yaml).

Activation (config.yaml):
    context:
      engine: ikaros_v5
    memory:
      provider: ikaros_v5
    plugins:
      enabled:
        - ikaros_v5
"""

from __future__ import annotations

from .context_engine import IkarosV5ContextEngine
from .memory_provider import IkarosV5MemoryProvider

__all__ = ["IkarosV5ContextEngine", "IkarosV5MemoryProvider"]


def register(ctx) -> None:
    """Dual registration, guarded for both loader contexts.

    - General plugin system calls register() with a ``PluginContext``
      that has ``register_context_engine`` but NOT ``register_memory_provider``.
    - The memory-provider scan calls register() with a ``_ProviderCollector``
      that has ``register_memory_provider`` but NOT ``register_context_engine``.
    """
    if hasattr(ctx, "register_context_engine"):
        ctx.register_context_engine(IkarosV5ContextEngine())
    if hasattr(ctx, "register_memory_provider"):
        ctx.register_memory_provider(IkarosV5MemoryProvider())

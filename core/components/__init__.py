"""Ikaros components registry.

The components subsystem provides a single source of truth (SSOT) for the
metadata of every runtime component that makes up the Ikaros stack: ports,
process markers, lifecycle scripts, dependencies, healthchecks, and
DeepSeek Harness integration overlays.

The data model is intentionally minimal:

  * ``ComponentSpec`` -- a typed dataclass mirroring the YAML schema.
  * ``load_components`` -- load + validate the YAML into a list of specs.
  * ``get_component`` -- lookup by ``id``.
  * ``list_components`` -- cheap accessor for the (cached) global list.

All paths in the YAML are RELATIVE to ``$IKAROS_ROOT``. ``IKAROS_ROOT``
itself is the authoritative root discoverer
(``bin/ikaros-env.bat|sh``); callers should never hardcode drive letters.

See Also
--------
``docs/ARCHITECTURE.md`` §1.2 -- canonical port assignments.
``docs/ikaros-dsh-plugin-architecture.md`` -- overlay / MCP wiring.
"""

from .registry import (
    ComponentSpec,
    get_component,
    list_components,
    load_components,
)

__all__ = [
    "ComponentSpec",
    "load_components",
    "get_component",
    "list_components",
]
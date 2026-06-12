"""Hermes modules package.

Each subdirectory under ``modules/`` is a self-describing service module
with its own ``module.json``, ``start.ps1`` / ``stop.ps1`` and
(optionally) ``health.ps1`` for services with network endpoints.

The ``supervisor`` module reads all ``modules/*/module.json`` files,
topologically sorts them by their ``depends_on`` field, and starts /
stops / reports status of every service in dependency order.
"""

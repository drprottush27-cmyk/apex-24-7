"""APEX 24/7 — Read-Only Local API Package.

Provides a minimal, secure, read-only HTTP API surface bound strictly to localhost.
Allows consumers (such as the Telegram Mini App) to monitor system health,
risk state, positions, and tactical signals with full provenance metadata.
"""
from apex.api.server import ApexApiServer

__all__ = ["ApexApiServer"]

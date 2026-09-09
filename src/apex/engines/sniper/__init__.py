"""APEX 24/7 — MTF Sniper Signal Engine.

Recovered from Tree A and adapted to canonical Apex domain models (CandleSeries -> Signal).
Advisory signal generation only — zero execution authority.
"""

from apex.engines.sniper.detector import MTFSniperEngine, SniperConfig, SniperSignalEngine

__all__ = ["MTFSniperEngine", "SniperConfig", "SniperSignalEngine"]

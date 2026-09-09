"""APEX 24/7 — Trade Journal (Phase 9).

Append-only journal of completed paper/shadow trades with full decision
context, plus the read-only analysis layer that turns historical performance
into human-gated learning proposals.

ADVISORY ONLY: Nothing in this package can modify trading behavior,
configuration, risk parameters, or code. It only records and analyzes.
"""

from apex.journal.models import (
    ClosedTradeRecord,
    TradeContext,
    build_closed_trade_record,
    from_closed_position,
)
from apex.journal.repository import TradeJournal, TradeJournalError

__all__ = [
    "ClosedTradeRecord",
    "TradeContext",
    "build_closed_trade_record",
    "from_closed_position",
    "TradeJournal",
    "TradeJournalError",
]

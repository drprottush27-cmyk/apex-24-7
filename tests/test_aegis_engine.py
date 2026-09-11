import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.aegis_engine import AegisLiveScanner
from src.scanner.models import MarketDataSummary, MarketRegime
from src.strategy.models import Signal, SignalType
from src.executor import HardenedRiskEngine

class MockScanner:
    def __init__(self, snapshots=None):
        self.snapshots = snapshots or {}

    def fetch_market_data(self, symbol: str) -> MarketDataSummary:
        if symbol in self.snapshots:
            return self.snapshots[symbol]
        return MarketDataSummary(
            symbol=symbol,
            current_price=Decimal('50000.0'),
            liquidity_usd=Decimal('20000000.0'),
            volume_24h=Decimal('50000000.0'),
            regime=MarketRegime.TREND_BULL,
            timestamp=datetime.now(timezone.utc),
            is_data_fresh=True,
            is_data_intact=True,
            indicators={"EMA_20": 51000.0, "EMA_50": 49000.0, "RSI_14": 55.0}
        )

def test_aegis_live_scanner_deterministic_execution():
    now = datetime.now(timezone.utc)
    mock_scanner = MockScanner({
        "BTC-USDT": MarketDataSummary(
            symbol="BTC-USDT",
            current_price=Decimal('70000.0'),
            liquidity_usd=Decimal('25000000.0'),
            volume_24h=Decimal('80000000.0'),
            regime=MarketRegime.TREND_BULL,
            timestamp=now,
            is_data_fresh=True,
            is_data_intact=True,
            indicators={"EMA_20": 71000.0, "EMA_50": 69000.0, "RSI_14": 55.0}
        )
    })
    
    mock_notion = MagicMock()
    risk_engine = HardenedRiskEngine(simulated_balance=10000.0)
    
    scanner = AegisLiveScanner(
        scanner_adapter=mock_scanner,
        risk_engine=risk_engine,
        notion_publisher=mock_notion
    )
    scanner.active_pairs = ["BTC-USDT"]
    
    # 1st cycle: Deterministic entry execution occurs (creates OPEN paper trade)
    trades = scanner.scan_cycle()
    assert len(trades) == 1
    t = trades[0]
    assert t.symbol == "BTC-USDT"
    assert t.position == "Long"
    assert t.is_open is True
    assert t.net_pnl == 0.0
    assert t.is_win is False
    assert t.is_loss is False
    assert t.is_breakeven is False
    assert mock_notion.publish.called

    # 2nd cycle immediately at same price: position already open, no new entries or exits
    trades_2 = scanner.scan_cycle()
    assert len(trades_2) == 0

    # 3rd cycle: Price reaches TP -> position exits
    open_pos = risk_engine.open_positions["BTC-USDT"]
    tp_price = open_pos["tp"]
    mock_scanner.snapshots["BTC-USDT"] = MarketDataSummary(
        symbol="BTC-USDT",
        current_price=Decimal(str(tp_price + 100)),
        liquidity_usd=Decimal('25000000.0'),
        volume_24h=Decimal('80000000.0'),
        regime=MarketRegime.TREND_BULL,
        timestamp=datetime.now(timezone.utc),
        is_data_fresh=True,
        is_data_intact=True,
        indicators={"EMA_20": 75000.0, "EMA_50": 69000.0, "RSI_14": 65.0}
    )
    exit_trades = scanner.scan_cycle()
    assert len(exit_trades) == 1
    closed_t = exit_trades[0]
    assert closed_t.symbol == "BTC-USDT"
    assert closed_t.is_open is False
    assert closed_t.net_pnl > 0.0
    assert closed_t.is_win is True
    assert "BTC-USDT" not in risk_engine.open_positions

def test_aegis_live_scanner_stale_corrupt_data_filtered():
    now = datetime.now(timezone.utc)
    mock_scanner = MockScanner({
        "BTC-USDT": MarketDataSummary(
            symbol="BTC-USDT",
            current_price=Decimal('70000.0'),
            liquidity_usd=Decimal('25000000.0'),
            volume_24h=Decimal('80000000.0'),
            regime=MarketRegime.TREND_BULL,
            timestamp=now,
            is_data_fresh=False,  # Stale data
            is_data_intact=True
        )
    })
    scanner = AegisLiveScanner(scanner_adapter=mock_scanner)
    scanner.active_pairs = ["BTC-USDT"]
    trades = scanner.scan_cycle()
    assert len(trades) == 0

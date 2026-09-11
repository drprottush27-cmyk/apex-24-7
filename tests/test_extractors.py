import pytest
from unittest.mock import patch, MagicMock
from src.extractors import CCXTExtractor, DefiLlamaDEXExtractor, DEXPluginExtractor
from src.engine import SyncEngine

def test_dex_extractor_data_honesty_no_fabricated_trades():
    extractor = DEXPluginExtractor(wallet_address="0x12345", chain="arbitrum")
    
    # 1. Closed trades must NEVER fabricate synthetic PnL/trades from macro volume
    closed_trades = extractor.fetch_closed_trades()
    assert isinstance(closed_trades, list)
    assert len(closed_trades) == 0  # No fake trades fabricated

    # 2. Positions must return empty when no on-chain positions exist
    positions = extractor.fetch_futures_positions()
    assert isinstance(positions, list)
    assert len(positions) == 0

def test_sync_engine_with_extractors():
    dex_ext = DEXPluginExtractor(wallet_address="0xabc", chain="arbitrum")
    mock_pub = MagicMock()
    
    engine = SyncEngine(extractors=[dex_ext], publishers=[mock_pub])
    engine.run_sync()
    
    # Verify publisher was called with list (empty since no fake trades fabricated)
    assert mock_pub.publish.called
    call_args = mock_pub.publish.call_args[0][0]
    assert isinstance(call_args, list)
    assert len(call_args) == 0

def test_ccxt_extractor_sandbox_mode():
    with patch("src.extractors.ccxt") as mock_ccxt:
        mock_client_instance = MagicMock()
        mock_ccxt.binance = MagicMock(return_value=mock_client_instance)
        
        extractor = CCXTExtractor(
            exchange_id="binance",
            api_key="test_key",
            secret="test_secret",
            paper_trade=True
        )
        
        # Verify sandbox mode was engaged
        assert mock_client_instance.set_sandbox_mode.called
        assert mock_client_instance.set_sandbox_mode.call_args[0][0] is True

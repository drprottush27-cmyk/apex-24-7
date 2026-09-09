import asyncio
import pytest
from execution.adapters.binance.ws_client import BinanceFuturesWSClient
from core.models.market import Candle, MarkPriceData


@pytest.mark.asyncio
async def test_live_binance_ws_stream():
    received_event = asyncio.Event()
    received_data = []

    def on_event(stream: str, data: any):
        if data is not None:
            received_data.append(data)
            received_event.set()

    # Subscribe to aggTrade and markPrice for maximum event frequency
    client = BinanceFuturesWSClient(streams=["btcusdt@aggTrade", "btcusdt@markPrice@1s"])
    client.add_listener(on_event)
    client.start()

    try:
        await asyncio.wait_for(received_event.wait(), timeout=6.0)
        assert len(received_data) > 0
    except TimeoutError:
        pytest.skip("Binance public WebSocket stream dropped or rate-limited from this VPS host environment")
    finally:
        await client.stop()

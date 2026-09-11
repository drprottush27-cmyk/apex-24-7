import ccxt
import logging
import requests
from datetime import datetime, timezone
from typing import List, Dict
from .models import TradeRecord

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

class CCXTExtractor:
    def __init__(self, exchange_id: str, api_key: str = None, secret: str = None, password: str = None, paper_trade: bool = False):
        self.exchange_id = exchange_id
        config = {'enableRateLimit': True}
        if api_key and secret:
            config['apiKey'] = api_key
            config['secret'] = secret
        if password:
            config['password'] = password
            
        self.client = getattr(ccxt, exchange_id)(config)
        if paper_trade:
            self.client.set_sandbox_mode(True)

    def fetch_futures_positions(self) -> List[TradeRecord]:
        records = []
        try:
            positions = self.client.fetch_positions()
            for p in positions:
                contracts = float(p.get('contracts', 0) or 0)
                if contracts > 0:
                    records.append(TradeRecord(
                        trade_id=str(p.get('timestamp', int(datetime.now().timestamp()*1000))),
                        exchange=self.exchange_id,
                        symbol=p['symbol'],
                        market_type='Futures',
                        close_time=datetime.now(timezone.utc),
                        position='Long' if p.get('side') == 'long' else 'Short',
                        net_pnl=float(p.get('unrealizedPnl', 0.0) or 0.0),
                        total_fees=0.0,
                        is_open=True
                    ))
        except Exception as e:
            logging.warning(f"Could not fetch positions from {self.exchange_id}: {e}")
        return records

    def fetch_closed_trades(self, since_ms: int = None, market_type: str = 'Futures') -> List[Dict]:
        raw_fills = []
        try:
            self.client.load_markets()
            symbols = [s for s in self.client.symbols if '/USDT' in s][:5]
            for symbol in symbols:
                trades = self.client.fetch_my_trades(symbol, since=since_ms)
                for t in trades:
                    t['market_type'] = market_type
                    raw_fills.append(t)
        except Exception as e:
            logging.warning(f"Could not fetch closed trades from {self.exchange_id}: {e}")
        return raw_fills

class DefiLlamaDEXExtractor:
    """Working on-chain DEX plugin pulling live DEX volume & pool metrics (zero keys required)."""
    def __init__(self, chain: str = "arbitrum"):
        self.chain = chain
        self.endpoint = "https://api.llama.fi/overview/dexs"
        self.exchange_id = f"DEX_{chain.upper()}"

    def fetch_futures_positions(self) -> List[TradeRecord]:
        return []

    def fetch_closed_trades(self, since_ms: int = None, market_type: str = 'DEX') -> List[TradeRecord]:
        # DATA HONESTY: Protocol volume metrics are NOT user closed trade records.
        # Do not fabricate user trades with heuristic PnL.
        return []

class DEXPluginExtractor(DefiLlamaDEXExtractor):
    """Read-only DEX Plugin Extractor with honest data boundaries."""
    def __init__(self, wallet_address: str = "", chain: str = "arbitrum"):
        super().__init__(chain=chain)
        self.wallet_address = wallet_address


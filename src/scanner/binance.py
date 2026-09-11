import json
import urllib.request
import pandas as pd
import pandas_ta as ta
from decimal import Decimal
from datetime import datetime, timezone
from src.scanner.models import MarketDataSummary, MarketRegime

class BinanceTestnetScanner:
    BASE_URL = "https://testnet.binancefuture.com"

    def fetch_market_data(self, symbol: str) -> MarketDataSummary:
        binance_symbol = symbol.replace("-", "")
        ticker_url = f"{self.BASE_URL}/fapi/v1/ticker/24hr?symbol={binance_symbol}"
        klines_url = f"{self.BASE_URL}/fapi/v1/klines?symbol={binance_symbol}&interval=1h&limit=100"
        
        try:
            # Fetch 24h Ticker for volume and macro regime
            req_ticker = urllib.request.Request(ticker_url, headers={'User-Agent': 'AegisAlpha/1.0'})
            with urllib.request.urlopen(req_ticker, timeout=5) as response:
                ticker_data = json.loads(response.read().decode())
                
            current_price = Decimal(ticker_data['lastPrice'])
            volume_24h = Decimal(ticker_data['volume'])
            quote_volume = Decimal(ticker_data['quoteVolume'])
            price_change_pct = Decimal(ticker_data['priceChangePercent'])
            
            if price_change_pct > Decimal('2.0'):
                regime = MarketRegime.TREND_BULL
            elif price_change_pct < Decimal('-2.0'):
                regime = MarketRegime.TREND_BEAR
            else:
                regime = MarketRegime.RANGING

            # Fetch Klines for Pandas-TA
            req_klines = urllib.request.Request(klines_url, headers={'User-Agent': 'AegisAlpha/1.0'})
            with urllib.request.urlopen(req_klines, timeout=5) as response:
                klines_data = json.loads(response.read().decode())
                
            df = pd.DataFrame(klines_data, columns=[
                "open_time", "open", "high", "low", "close", "volume", 
                "close_time", "qav", "num_trades", "taker_base_vol", "taker_quote_vol", "ignore"
            ])
            df['close'] = df['close'].astype(float)
            
            # Run Pandas-TA technical indicators
            df.ta.ema(length=20, append=True)
            df.ta.ema(length=50, append=True)
            df.ta.rsi(length=14, append=True)
            
            latest = df.iloc[-1]
            indicators = {
                "EMA_20": float(latest["EMA_20"]) if not pd.isna(latest["EMA_20"]) else 0.0,
                "EMA_50": float(latest["EMA_50"]) if not pd.isna(latest["EMA_50"]) else 0.0,
                "RSI_14": float(latest["RSI_14"]) if not pd.isna(latest["RSI_14"]) else 50.0
            }

            return MarketDataSummary(
                symbol, current_price, quote_volume, volume_24h, regime,
                datetime.now(timezone.utc), True, True, indicators
            )
        except Exception as e:
            print(f"[!] SCANNER ERROR: {str(e)}")
            return MarketDataSummary(
                symbol, Decimal('0'), Decimal('0'), Decimal('0'),
                MarketRegime.RANGING, datetime.now(timezone.utc), False, False
            )

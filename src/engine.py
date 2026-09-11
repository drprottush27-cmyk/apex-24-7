from datetime import datetime, timezone
from typing import List, Dict
from .models import TradeRecord

class SyncEngine:
    def __init__(self, extractors: list, publishers: list):
        self.extractors = extractors
        self.publishers = publishers

    def _aggregate_spot_fills(self, raw_fills: List[Dict], exchange: str) -> List[TradeRecord]:
        aggregated = []
        grouped = {}
        for fill in raw_fills:
            sym = fill['symbol']
            if sym not in grouped:
                grouped[sym] = {'fills': [], 'total_cost': 0, 'total_amount': 0, 'fees': 0}
            grouped[sym]['fills'].append(fill)
            grouped[sym]['total_cost'] += fill.get('cost', 0)
            grouped[sym]['total_amount'] += fill.get('amount', 0)
            grouped[sym]['fees'] += fill.get('fee', {}).get('cost', 0)

        for sym, data in grouped.items():
            if not data['fills']: continue
            last_fill = data['fills'][-1]
            dt = datetime.fromtimestamp(last_fill['timestamp']/1000, tz=timezone.utc)
            aggregated.append(TradeRecord(
                trade_id=last_fill['id'],
                exchange=exchange,
                symbol=sym,
                market_type=last_fill.get('market_type', 'Spot'),
                close_time=dt,
                position='Long' if last_fill['side'] == 'buy' else 'Short',
                net_pnl=last_fill['info'].get('realizedPnl', 0.0),
                total_fees=data['fees'],
                is_open=False
            ))
        return aggregated

    def run_sync(self, since_ms: int = None, force_update: bool = False):
        all_records = []
        for ext in self.extractors:
            all_records.extend(ext.fetch_futures_positions())
            raw_spot = ext.fetch_closed_trades(since_ms, 'Spot')
            raw_futures = ext.fetch_closed_trades(since_ms, 'Futures')
            
            if hasattr(ext, 'exchange_id'):
                all_records.extend(self._aggregate_spot_fills(raw_spot, ext.exchange_id))
                all_records.extend(self._aggregate_spot_fills(raw_futures, ext.exchange_id))

        for pub in self.publishers:
            pub.publish(all_records, force=force_update)

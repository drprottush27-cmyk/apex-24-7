from decimal import Decimal
from typing import Optional
from datetime import datetime, timezone
from .models import RiskLimits, RiskEvaluation

class RiskEngine:
    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def evaluate_order(
        self,
        symbol: str,
        order_size: Decimal,
        order_price: Decimal,
        stop_loss_price: Optional[Decimal],
        current_liquidity_usd: Decimal,
        data_timestamp_utc: datetime,
        current_equity: Decimal,
        peak_equity: Decimal,
        current_exposure_usd: Decimal,
        current_open_positions: int
    ) -> RiskEvaluation:
        # 1. Stale Data Rejection (Fail-Closed)
        now = datetime.now(timezone.utc)
        if (now - data_timestamp_utc).total_seconds() > 60:
            return RiskEvaluation.reject("STALE_DATA: Price data is older than 60 seconds")

        # 2. Invalid Price / Size Rejection
        if order_size <= Decimal('0') or order_price <= Decimal('0'):
            return RiskEvaluation.reject("INVALID_DATA: Order size and price must be strictly positive")

        # 3. Drawdown Breaker
        if peak_equity > Decimal('0'):
            drawdown = (peak_equity - current_equity) / peak_equity
            if drawdown > self.limits.max_drawdown_pct:
                return RiskEvaluation.reject(f"DRAWDOWN_BREAKER: Current drawdown {drawdown*100:.2f}% exceeds limit")

        # 4. Mandatory Stop Loss
        if self.limits.mandatory_stop_loss_enabled and stop_loss_price is None:
            return RiskEvaluation.reject("MANDATORY_STOP_LOSS: Order lacks a defined stop loss")
        
        if stop_loss_price is not None and stop_loss_price <= Decimal('0'):
            return RiskEvaluation.reject("INVALID_DATA: Stop loss price must be strictly positive")

        # 5. Position Size and Exposure Limits
        notional_value = order_size * order_price
        if notional_value > self.limits.max_position_size_usd:
            return RiskEvaluation.reject("MAX_POSITION_SIZE: Order exceeds maximum allowed position size")
            
        if current_exposure_usd + notional_value > self.limits.max_total_exposure_usd:
            return RiskEvaluation.reject("MAX_TOTAL_EXPOSURE: Order would exceed maximum total exposure")

        # 6. Concurrent Positions Limit
        if current_open_positions >= self.limits.max_concurrent_positions:
            return RiskEvaluation.reject("MAX_CONCURRENT_POSITIONS: Order would exceed position count limit")

        # 7. Liquidity Threshold
        if current_liquidity_usd < self.limits.min_liquidity_usd:
            return RiskEvaluation.reject("INSUFFICIENT_LIQUIDITY: Market liquidity below minimum safety threshold")

        # 8. Risk Per Trade (Distance to SL)
        if stop_loss_price is not None and current_equity > Decimal('0'):
            price_risk = abs(order_price - stop_loss_price)
            total_trade_risk_usd = order_size * price_risk
            risk_pct = total_trade_risk_usd / current_equity
            if risk_pct > self.limits.max_risk_per_trade_pct:
                return RiskEvaluation.reject(f"MAX_RISK_PER_TRADE: Trade risks {risk_pct*100:.2f}% of equity, exceeds limit")

        # All checks passed
        return RiskEvaluation.approve()

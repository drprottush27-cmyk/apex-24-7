import os
import logging
import secrets
from typing import Optional, Set
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv

from src.executor import ExecutionModule

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
load_dotenv('/srv/apex/.env')

app = FastAPI(title="Aegis Alpha Paper Webhook Server", version="1.0.0")

# PAPER EXECUTION BOUNDARY: Exchange order placement remains strictly disabled
executor = ExecutionModule(exchange_id='mock', paper_trade=True)

# Replay / Duplicate Signal Tracker
_seen_signal_ids: Set[str] = set()

def get_secret_passphrase() -> str:
    return os.getenv("WEBHOOK_PASSPHRASE", "")

class SignalPayload(BaseModel):
    passphrase: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=2)
    action: str = Field(...)
    signal_type: str = Field(default="ALERT")
    price: float = Field(..., gt=0.0)
    defensive_sl: Optional[float] = Field(default=None)
    signal_id: Optional[str] = Field(default=None)

    @field_validator('symbol')
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        s = v.strip().upper()
        if not s:
            raise ValueError("Symbol must not be empty")
        return s

    @field_validator('action')
    @classmethod
    def validate_action(cls, v: str) -> str:
        a = v.strip().upper()
        if a not in ('BUY', 'SELL', 'CLOSE'):
            raise ValueError(f"Action '{v}' is invalid; must be 'BUY', 'SELL', or 'CLOSE'")
        return a

    @field_validator('price')
    @classmethod
    def validate_price(cls, v: float) -> float:
        import math
        if math.isnan(v) or math.isinf(v) or v <= 0:
            raise ValueError("Price must be a finite positive number")
        return v

    @field_validator('defensive_sl')
    @classmethod
    def validate_sl(cls, v: Optional[float]) -> Optional[float]:
        if v is not None:
            import math
            if math.isnan(v) or math.isinf(v) or v <= 0:
                raise ValueError("Defensive SL must be a finite positive number")
        return v

@app.post("/webhook")
async def receive_tradingview_alert(payload: SignalPayload):
    configured_passphrase = get_secret_passphrase()
    
    # 1. Authentication (Fail-closed, constant-time compare)
    if not configured_passphrase or not secrets.compare_digest(payload.passphrase, configured_passphrase):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: invalid or unconfigured webhook passphrase"
        )

    # 2. Replay / Duplicate Protection
    if payload.signal_id:
        if payload.signal_id in _seen_signal_ids or payload.signal_id in executor.risk.processed_signal_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Duplicate signal rejected: signal_id '{payload.signal_id}' already processed"
            )
        _seen_signal_ids.add(payload.signal_id)

    # 3. Defensive SL requirement for entry actions
    if payload.action in ('BUY', 'SELL') and payload.defensive_sl is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Defensive stop loss is mandatory for entry action '{payload.action}'"
        )

    # 4. Handle Position Close
    if payload.action == 'CLOSE':
        success, pnl, msg = executor.close_position(payload.symbol, payload.price)
        if not success:
            return {
                "status": "rejected",
                "action": "CLOSE",
                "symbol": payload.symbol,
                "reason": msg,
                "realized_pnl": 0.0
            }
        return {
            "status": "executed",
            "action": "CLOSE",
            "symbol": payload.symbol,
            "realized_pnl": pnl,
            "detail": msg
        }

    # 5. Handle Paper Entry Execution strictly via Risk Guardian
    exec_result = executor.process_signal(
        symbol=payload.symbol,
        action=payload.action,
        entry_price=payload.price,
        defensive_sl=payload.defensive_sl,
        signal_id=payload.signal_id
    )

    if not exec_result["approved"]:
        return {
            "status": "rejected",
            "action": payload.action,
            "symbol": payload.symbol,
            "reason": exec_result["reason"],
            "signal_id": payload.signal_id
        }

    return {
        "status": "executed",
        "action": payload.action,
        "symbol": payload.symbol,
        "authorized_qty": exec_result["qty"],
        "entry_price": exec_result["entry_price"],
        "stop_loss": exec_result["stop_loss"],
        "take_profit": exec_result["take_profit"],
        "signal_id": payload.signal_id,
        "message": "Paper order approved by Risk Guardian and registered in virtual ledger"
    }

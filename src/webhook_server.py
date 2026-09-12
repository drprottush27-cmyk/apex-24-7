from contextlib import asynccontextmanager
import asyncio
import os
import logging
import secrets
from typing import Optional, Set
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv

from src.executor import ExecutionModule
from src.api_v1 import register_v1_observability
from src.apex.advisory.ollama import OllamaAdvisor
from src.apex.agents.binance import BinanceMarketAgent
from src.apex.agents.bybit import BybitMarketAgent
from src.apex.agents.okx import OKXMarketAgent
from src.apex.integration.pipeline import ApexIntelligencePipeline
from src.apex.intelligence.engine import CrossExchangeIntelligence
from src.apex.orchestration.engine import PaperAccountOrchestrator

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
load_dotenv('/srv/apex/.env')


_WATCHLIST = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']

async def _pipeline_background_scanner(interval_seconds: int = 15):
    logging.info('Starting continuous background market scanner for %s', _WATCHLIST)
    while True:
        for symbol in _WATCHLIST:
            try:
                await _pipeline.run_cycle(symbol)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logging.error('Error scanning %s: %s', symbol, e)
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_pipeline_background_scanner())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="Aegis Alpha Paper Webhook Server", version="1.0.0", lifespan=lifespan)

# PAPER EXECUTION BOUNDARY: Exchange order placement remains strictly disabled
executor = ExecutionModule(exchange_id='mock', paper_trade=True)

# PAPER-ONLY advisory integration: read-only market agents, cross-exchange
# confirmation, a local advisory model and paper multi-account orchestration.
# None of these components can place, cancel or execute orders.
_pipeline = ApexIntelligencePipeline(
    agents={
        "binance": BinanceMarketAgent(),
        "okx": OKXMarketAgent(),
        "bybit": BybitMarketAgent(),
    },
    intelligence=CrossExchangeIntelligence(),
    advisor=OllamaAdvisor(),
    orchestrator=PaperAccountOrchestrator(guardian=executor.risk),
)

# Phase 1 read-only observability API (GET only, mounted without duplicating health)
register_v1_observability(app, executor, pipeline=_pipeline)

# Replay / Duplicate Signal Tracker
_seen_signal_ids: Set[str] = set()

def get_secret_passphrase() -> str:
    return os.getenv("WEBHOOK_PASSPHRASE", "")

@app.get("/api/v1/health")
@app.get("/health")
async def health_check():
    """Canonical minimal read-only health and readiness check."""
    return {
        "status": "healthy",
        "service": "aegis-webhook",
        "trading_mode": "PAPER",
        "live_trading_enabled": False,
        "circuit_breaker_tripped": executor.risk.circuit_breaker_tripped,
        "open_positions_count": len(executor.risk.open_positions)
    }

class SignalPayload(BaseModel):
    passphrase: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=2)
    action: str = Field(...)
    signal_type: str = Field(default="ALERT")
    price: float = Field(..., gt=0.0)
    defensive_sl: Optional[float] = Field(default=None)
    take_profit: Optional[float] = Field(default=None)
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

    @field_validator('take_profit')
    @classmethod
    def validate_tp(cls, v: Optional[float]) -> Optional[float]:
        if v is not None:
            import math
            if math.isnan(v) or math.isinf(v) or v <= 0:
                raise ValueError("Take profit must be a finite positive number")
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
        signal_id=payload.signal_id,
        tp_price=payload.take_profit
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


from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

DIST_DIR = '/srv/apex/frontend/dist'
if os.path.isdir(DIST_DIR):
    app.mount('/assets', StaticFiles(directory=os.path.join(DIST_DIR, 'assets')), name='assets')

    @app.api_route('/', methods=['GET', 'HEAD'])
    @app.api_route('/{full_path:path}', methods=['GET', 'HEAD'])
    async def serve_spa(full_path: str = ''):
        if full_path and any(full_path.startswith(p) for p in ('api/', 'webhook', 'health', 'docs', 'openapi.json')):
            raise HTTPException(status_code=404, detail='Endpoint not found')
        index_file = os.path.join(DIST_DIR, 'index.html')
        if os.path.exists(index_file):
            return FileResponse(index_file)
        raise HTTPException(status_code=404, detail='Frontend dist not built')

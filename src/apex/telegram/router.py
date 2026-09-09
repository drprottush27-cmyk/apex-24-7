"""APEX 24/7 — Authoritative Telegram Command Router & Natural Language Processor.

SAFETY INVARIANTS:
- All commands are strictly READ-ONLY and ADVISORY. Zero execution authority.
- No Telegram command may directly place an order, bypass risk checks, bypass EndpointGuard,
  disable KillSwitch, change trading mode, or grant execution authority.
- Any attempt to execute trades or run shell commands is rejected immediately.
- Only authorized user IDs can issue control or inspection commands.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from typing import Any

from apex.engines.tactical.alerts import (
    AlertCategory,
    AlertSeverity,
    get_telegram_dispatcher,
    redact_secrets,
)
from apex.telegram.auth import is_user_authorized
from apex.telegram.formatters import (
    fmt_price,
    format_agent,
    format_alerts_status,
    format_audit_events,
    format_best_setups,
    format_control_response,
    format_health,
    format_help,
    format_investment,
    format_pnl,
    format_positions,
    format_prepump_candidates,
    format_report,
    format_risk,
    format_run,
    format_scan_results,
    format_settings,
    format_start,
    format_status,
    format_team,
    format_thesis,
    format_trade_setup,
    format_trades,
    format_watchlist,
)

logger = logging.getLogger(__name__)

# Forbidden direct execution patterns (placing manual buy/sell/swap orders)
FORBIDDEN_TRADE_RE = re.compile(
    r"\b(buy\b|sell\b|trade\b|swap\b|convert\b|transfer\b|withdraw\b|deposit\b|place order\b|cancel order\b|close position\b|flatten\b|long\s+[a-z0-9]+|short\s+[a-z0-9]+)",
    re.IGNORECASE,
)

# Forbidden execution / shell commands
FORBIDDEN_SHELL_RE = re.compile(
    r"\b(exec|shell|eval|bash|sh|system|subprocess|os\.system|rm\s|cat\s|curl\s|wget\s)\b",
    re.IGNORECASE,
)


class ApexTelegramRouter:
    """Robust command router and natural language interpreter for Telegram.

    Coordinates read-only inspection and alerting settings via local APEX API or direct engine.
    """

    def __init__(
        self,
        api_base_url: str = "http://127.0.0.1:8765",
        engine: Any = None,
        miniapp_url: str = "",
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.engine = engine
        self.miniapp_url = miniapp_url
        self._audit_log: deque[dict[str, Any]] = deque(maxlen=200)
        self._signals_cache: list[dict[str, Any]] | None = None
        self._signals_cache_time: float = 0.0

    def _record_audit(
        self,
        user_id: int | str | None,
        raw_text: str,
        command: str,
        status: str,
        error: str | None = None,
    ) -> None:
        safe_text = redact_secrets(raw_text[:100])
        entry = {
            "timestamp_ms": int(time.time() * 1000),
            "user_id": user_id,
            "raw_text": safe_text,
            "command": command,
            "status": status,
            "error": redact_secrets(error) if error else None,
        }
        self._audit_log.append(entry)

    def get_audit_log(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(reversed(list(self._audit_log)[-limit:]))

    def _fetch_api(self, endpoint: str, query: dict[str, Any] | None = None, timeout: float = 10.0) -> Any:
        url = f"{self.api_base_url}{endpoint}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        req = urllib.request.Request(url, headers={"User-Agent": "ApexTelegramRouter/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data) if data else {}

    def _post_api(self, endpoint: str, payload: dict[str, Any], timeout: float = 10.0) -> Any:
        url = f"{self.api_base_url}{endpoint}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "ApexTelegramRouter/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data) if data else {}

    def _get_signals(self, symbol: str | None = None, max_age_seconds: float = 20.0) -> list[dict[str, Any]]:
        if self.engine is not None:
            try:
                if symbol:
                    sig = self.engine.get_tactical_signal(symbol)
                    return [sig.to_dict()] if sig is not None else []
                return self.engine.get_tactical_signals()
            except Exception as exc:
                logger.debug("Engine get_tactical_signals failed: %s", exc)

        if symbol:
            try:
                res = self._fetch_api("/api/v1/signals", query={"symbol": symbol}, timeout=10.0)
                return res if isinstance(res, list) else []
            except Exception:
                return []

        now = time.time()
        if self._signals_cache is not None and (now - self._signals_cache_time) < max_age_seconds:
            return self._signals_cache

        try:
            signals = self._fetch_api("/api/v1/signals", timeout=25.0)
            if isinstance(signals, list) and signals:
                self._signals_cache = signals
                self._signals_cache_time = now
                return signals
        except Exception as exc:
            logger.warning("Error fetching fresh signals: %s", exc)
            if self._signals_cache is not None:
                return self._signals_cache

        return self._signals_cache or []

    # ── Command & Message Dispatcher ──────────────────────────────────────────

    def handle(self, text: str, user_id: int | str | None = None) -> str:
        """Entrypoint for incoming Telegram messages and commands."""
        raw_text = (text or "").strip()

        # 1. Authorization Gate
        if not is_user_authorized(user_id):
            self._record_audit(user_id, raw_text, "UNAUTHORIZED", "REJECTED", "Unauthorized user ID")
            return "⛔ Unauthorized."

        # 2. Safety Gate — Reject direct trade execution attempts
        # /flatten is an authorized control plane command, but natural language
        # execution instructions like "flatten account", "cancel order", "close position" are blocked.
        if raw_text.startswith("/flatten"):
            pass
        elif FORBIDDEN_TRADE_RE.search(raw_text):
            self._record_audit(user_id, raw_text, "TRADE_ATTEMPT", "REJECTED", "Trade execution disallowed")
            return (
                "⛔ Execution rejected: Direct trade execution via Telegram is permanently disabled.\n\n"
                "APEX operates strictly as a deterministic, safety-first PAPER trading system. "
                "All order intents are generated exclusively by deterministic multi-factor signal engines "
                "and validated by the authoritative RiskGuardian pipeline. Telegram is strictly advisory."
            )

        # 3. Security Gate — Reject shell/code execution attempts
        if FORBIDDEN_SHELL_RE.search(raw_text):
            self._record_audit(user_id, raw_text, "SHELL_ATTEMPT", "REJECTED", "Shell execution disallowed")
            return "⛔ Error: Shell and code execution commands are strictly prohibited."

        # 4. Command Normalization
        command, args = self._parse_command_or_natural_language(raw_text)

        try:
            response = self._route_command(command, args)
            self._record_audit(user_id, raw_text, command, "SUCCESS")
            return response
        except Exception as exc:
            logger.exception("Error executing Telegram command '%s': %s", command, exc)
            self._record_audit(user_id, raw_text, command, "ERROR", str(exc))
            return f"⚠️ Notice: Unable to execute command <code>/{command}</code> ({exc})"

    def _parse_command_or_natural_language(self, text: str) -> tuple[str, list[str]]:
        """Normalize commands and map natural-language expressions to canonical handlers."""
        lower = text.lower().strip()

        # Direct Slash Commands
        if lower.startswith("/"):
            parts = text[1:].strip().split()
            cmd = parts[0].lower() if parts else "help"
            # Strip bot mention if in group (e.g., /status@ApexBot)
            if "@" in cmd:
                cmd = cmd.split("@")[0]
            return cmd, parts[1:]

        # Natural-Language Mapping
        if lower in ("start", "hello", "hi"):
            return "start", []
        if lower in ("help", "commands", "menu"):
            return "help", []
        if lower in ("status", "system status", "engine status", "health"):
            return "status", []
        if lower in ("scan", "scan market", "scan the market", "run scan"):
            return "scan", []
        if lower in ("scan long", "find long", "long setups", "longs", "find longs"):
            return "scan_long", []
        if lower in ("scan short", "find short", "short setups", "shorts", "what is dumping", "find shorts"):
            return "scan_short", []
        if lower in ("find best trade", "best setup", "best setup now", "find best coin", "best trade", "best"):
            return "best", []
        if lower in ("find pre-pump", "find prepump", "prepump", "what is pumping", "pumping", "pre pump"):
            return "prepump", []
        if lower in ("show positions", "open positions", "my positions", "positions", "position"):
            return "positions", []
        if lower in ("show trades", "trades", "trade history", "history", "recent trades"):
            return "trades", []
        if lower in ("show today’s pnl", "show today's pnl", "today pnl", "pnl", "profit"):
            return "pnl", []
        if lower in ("risk status", "risk", "check risk"):
            return "risk", []
        if lower in ("equity", "balance", "account balance"):
            return "equity", []
        if lower in ("run", "run status", "24h run", "paper run"):
            return "run", []
        if lower in ("alerts", "alert settings", "notification settings"):
            return "alerts", []
        if lower in ("alerts on", "enable alerts"):
            return "alerts_on", []
        if lower in ("alerts off", "disable alerts"):
            return "alerts_off", []
        if lower in ("settings", "config", "configuration"):
            return "settings", []
        if lower in ("market", "market overview", "gainers", "what is the market doing", "market status"):
            return "market", []
        if lower in ("stop", "stop system", "stop bot", "shut down", "halt"):
            return "stop", []
        if lower in ("pause", "pause system", "pause trading", "pause bot"):
            return "pause", []
        if lower in ("resume", "resume system", "resume trading", "resume bot"):
            return "resume", []
        if lower in ("restart", "restart system", "restart bot"):
            return "restart", []
        if lower in ("emergency stop", "emergency-stop", "kill switch", "trip kill switch"):
            return "emergency_stop", []
        if lower in ("flatten", "flatten positions", "close all positions"):
            return "flatten", []
        if lower in ("team", "agents", "management team", "team status", "who is running"):
            return "team", []
        if lower in ("invest", "portfolio", "investment research", "investment"):
            return "invest", []
        if lower in ("theses", "theses list", "investment theses"):
            return "theses", []
        if lower in ("watchlist", "investment watchlist"):
            return "watchlist", []
        if lower in ("report", "hourly report", "executive report", "latest report"):
            return "report", []
        if lower in ("events", "audit", "audit log", "audit trail", "recent events"):
            return "events", []

        # Parameterized: "thesis BTC", "thesis for BTC"
        m_th = re.search(r"thesis\s+(?:for\s+)?([a-z0-9]+)", lower)
        if m_th:
            sym = m_th.group(1).upper()
            if not sym.endswith("USDT") and not sym.endswith("BUSD"):
                sym = f"{sym}USDT"
            return "thesis", [sym]

        # Parameterized: "agent risk_manager"
        m_ag = re.search(r"agent\s+([a-z0-9_]+)", lower)
        if m_ag:
            return "agent", [m_ag.group(1).lower()]

        # Parameterized: "flatten BTC"
        m_fl = re.search(r"flatten\s+([a-z0-9]+)", lower)
        if m_fl:
            sym = m_fl.group(1).upper()
            if not sym.endswith("USDT") and not sym.endswith("BUSD"):
                sym = f"{sym}USDT"
            return "flatten", [sym]

        # Parameterized Natural Language: "give me trade setup for BTC", "trade setup BTC", "setup BTC", "setup for BTC"
        m_setup = re.search(r"(?:trade\s+)?setup\s+(?:for\s+)?([a-z0-9]+)", lower)
        if m_setup:
            sym = m_setup.group(1).upper()
            if not sym.endswith("USDT") and not sym.endswith("BUSD"):
                sym = f"{sym}USDT"
            return "setup", [sym]

        # Parameterized Natural Language: "price BTC", "price of BTC", "what is BTC price", "BTC price"
        m_price1 = re.search(r"^price\s+(?:of\s+)?([a-z0-9]+)$", lower)
        if m_price1:
            sym = m_price1.group(1).upper()
            if not sym.endswith("USDT") and not sym.endswith("BUSD"):
                sym = f"{sym}USDT"
            return "price", [sym]

        m_price2 = re.search(r"(?:what\s+is\s+)?([a-z0-9]+)\s+price", lower)
        if m_price2:
            sym = m_price2.group(1).upper()
            if not sym.endswith("USDT") and not sym.endswith("BUSD"):
                sym = f"{sym}USDT"
            return "price", [sym]

        # Parameterized Natural Language: "signal BTC", "signal for BTC"
        m_sig = re.search(r"signal\s+(?:for\s+)?([a-z0-9]+)", lower)
        if m_sig:
            sym = m_sig.group(1).upper()
            if not sym.endswith("USDT") and not sym.endswith("BUSD"):
                sym = f"{sym}USDT"
            return "signal", [sym]

        parts = text.split()
        return parts[0].lower(), parts[1:]

    def _route_command(self, cmd: str, args: list[str]) -> str:
        """Route normalized command to corresponding business logic handler."""
        if cmd == "start":
            if args and args[0].lower() in ("system", "run", "paper", "service", "start"):
                return self._handle_control_start(args)
            return format_start(self.miniapp_url)
        elif cmd in ("start_system", "run_system"):
            return self._handle_control_start(args)
        elif cmd == "stop":
            return self._handle_control_stop(args)
        elif cmd == "pause":
            return self._handle_control_pause(args)
        elif cmd == "resume":
            return self._handle_control_resume(args)
        elif cmd == "restart":
            return self._handle_control_restart(args)
        elif cmd in ("emergency_stop", "emergencystop", "kill"):
            return self._handle_control_emergency_stop(args)
        elif cmd in ("flatten", "close_all"):
            return self._handle_control_flatten(args)
        elif cmd in ("ack", "acknowledge"):
            return self._handle_control_ack(args)
        elif cmd in ("team", "agents"):
            return self._handle_team(args)
        elif cmd == "agent":
            return self._handle_agent(args)
        elif cmd in ("invest", "investments", "portfolio"):
            return self._handle_investment(args)
        elif cmd == "theses":
            return self._handle_theses(args)
        elif cmd == "thesis":
            return self._handle_thesis(args)
        elif cmd == "watchlist":
            return self._handle_watchlist(args)
        elif cmd in ("report", "reports", "hourly"):
            return self._handle_report(args)
        elif cmd in ("events", "audit"):
            return self._handle_events(args)
        elif cmd in ("intel", "grok"):
            return self._handle_intel(args)
        elif cmd == "help":
            return format_help()
        elif cmd in ("status", "dash", "dashboard"):
            return self._handle_status()
        elif cmd == "health":
            return self._handle_health()
        elif cmd == "scan":
            return self._handle_scan(direction_filter=None)
        elif cmd in ("scan_long", "long", "longs"):
            return self._handle_scan(direction_filter="LONG")
        elif cmd in ("scan_short", "short", "shorts"):
            return self._handle_scan(direction_filter="SHORT")
        elif cmd in ("setup", "plan"):
            sym = args[0].upper() if args else None
            return self._handle_setup(sym)
        elif cmd == "best":
            return self._handle_best()
        elif cmd in ("prepump", "pumping"):
            return self._handle_prepump()
        elif cmd == "tactical":
            sym = args[0].upper() if args else None
            return self._handle_tactical(sym)
        elif cmd == "market":
            return self._handle_market()
        elif cmd == "price":
            sym = args[0].upper() if args else "BTCUSDT"
            return self._handle_price(sym)
        elif cmd == "signal":
            sym = args[0].upper() if args else "BTCUSDT"
            return self._handle_signal(sym)
        elif cmd in ("position", "positions"):
            return self._handle_positions()
        elif cmd == "trades":
            return self._handle_trades(scope="current_run")
        elif cmd == "history":
            return self._handle_trades(scope="all")
        elif cmd == "pnl":
            return self._handle_pnl()
        elif cmd == "risk":
            return self._handle_risk()
        elif cmd in ("equity", "balance"):
            return self._handle_equity()
        elif cmd == "run":
            return self._handle_run()
        elif cmd == "alerts":
            return self._handle_alerts(args)
        elif cmd == "alerts_on":
            return self._handle_alerts_toggle_all(True)
        elif cmd == "alerts_off":
            return self._handle_alerts_toggle_all(False)
        elif cmd == "settings":
            return self._handle_settings()
        else:
            return f"❓ Unknown command: <code>/{cmd}</code>. Type /help for available commands."

    # ── Command Handlers ──────────────────────────────────────────────────────

    def _handle_status(self) -> str:
        try:
            data = self._fetch_api("/api/v1/status")
            return format_status(data)
        except Exception as exc:
            # Fallback to health endpoint
            try:
                data = self._fetch_api("/api/v1/health")
                return format_health(data)
            except Exception:
                return f"⚠️ APEX API is offline or unreachable ({exc})."

    def _handle_health(self) -> str:
        data = self._fetch_api("/api/v1/health")
        return format_health(data)

    def _handle_scan(self, direction_filter: str | None = None) -> str:
        # 1. Verify health & freshness
        try:
            health_data = self._fetch_api("/api/v1/health")
            if health_data.get("is_data_stale"):
                age = health_data.get("data_age_seconds", 0)
                return (
                    f"⚠️ <b>SCAN TEMPORARILY BLOCKED: STALE MARKET DATA</b>\n\n"
                    f"Market data age is {age:.1f}s (>180s safety threshold).\n"
                    f"Scan blocked to prevent trading against outdated quotes.\n\n"
                    f"<i>Fails closed until fresh candle streams resume.</i>"
                )
        except Exception:
            pass

        # 2. Fetch active signals/setups from scanner
        signals = self._get_signals()
        candidates: list[dict[str, Any]] = []
        for s in signals:
            score = float(s.get("score", 0.0))
            features = s.get("features", {})
            bias = float(features.get("directional_bias", 0.0)) if isinstance(features, dict) else 0.0
            direction = "LONG" if bias >= 0 else "SHORT"
            candidates.append({
                "symbol": s.get("symbol"),
                "score": score,
                "direction": direction,
                "price": s.get("price"),
                "stop_loss": s.get("suggested_stop_loss"),
                "tp1": s.get("suggested_take_profit"),
                "rvol": features.get("rvol", 1.0) if isinstance(features, dict) else 1.0,
                "setup_type": "Tactical Squeeze" if score >= 70 else "Breakout",
            })

        return format_scan_results(candidates, direction_filter=direction_filter)

    def _handle_setup(self, symbol: str | None = None) -> str:
        target_symbol = symbol
        if not target_symbol:
            # Find best candidate symbol from signals
            try:
                signals = self._get_signals()
                if signals:
                    signals = sorted(signals, key=lambda x: float(x.get("score", 0.0)), reverse=True)
                    target_symbol = signals[0].get("symbol")
            except Exception:
                pass

        if not target_symbol:
            target_symbol = "BTCUSDT"

        if not target_symbol.endswith("USDT") and not target_symbol.endswith("BUSD"):
            target_symbol = f"{target_symbol}USDT"

        plan = self._fetch_api("/api/v1/plan", query={"symbol": target_symbol})
        if not plan or not plan.get("symbol"):
            return (
                f"⚪ <b>NO VALID SETUP FOR {target_symbol}</b>\n\n"
                f"The symbol does not currently meet technical entry criteria or lacks sufficient candle history.\n\n"
                f"<i>Mode: PAPER / ADVISORY</i>"
            )

        return format_trade_setup(plan)

    def _handle_best(self) -> str:
        signals = self._get_signals()
        setups: list[dict[str, Any]] = []
        for s in signals:
            score = float(s.get("score", 0.0))
            features = s.get("features", {})
            bias = float(features.get("directional_bias", 0.0)) if isinstance(features, dict) else 0.0
            side = "LONG" if bias >= 0 else "SHORT"
            setups.append({
                "symbol": s.get("symbol"),
                "score": score,
                "side": side,
                "entry_price": s.get("price"),
                "stop_loss": s.get("suggested_stop_loss"),
                "tp1": s.get("suggested_take_profit"),
            })

        return format_best_setups(setups)

    def _handle_prepump(self) -> str:
        signals = self._get_signals()
        candidates: list[dict[str, Any]] = []
        for s in signals:
            f = s.get("features", {})
            if not isinstance(f, dict):
                continue
            rvol = float(f.get("rvol") or 1.0)
            bbw = float(f.get("bbw_percentile") or 50.0)
            score = float(s.get("score") or 0.0)
            funding = float(f.get("funding_rate") or 0.0001) * 100.0
            depth_imb = float(f.get("depth_imbalance") or 0.0) * 100.0
            bias = float(f.get("directional_bias") or 0.0)

            # Look for volume expansion + compression
            if rvol >= 1.5 or score >= 60.0:
                candidates.append({
                    "symbol": s.get("symbol"),
                    "prepump_score": score,
                    "oi_change_pct": round((rvol - 1.0) * 4.5, 2),  # derived observation
                    "rvol": rvol,
                    "funding_rate": funding,
                    "depth_imbalance": depth_imb,
                    "volatility_compression": bbw,
                    "directional_bias": bias,
                    "htf_trend": "BULLISH" if bias > 0 else "BEARISH",
                    "sfp_status": "BULLISH" if f.get("sfp_bullish") else ("BEARISH" if f.get("sfp_bearish") else "NONE"),
                })

        candidates.sort(key=lambda x: x["prepump_score"], reverse=True)
        return format_prepump_candidates(candidates)

    def _handle_tactical(self, symbol: str | None) -> str:
        sym = symbol or "BTCUSDT"
        if not sym.endswith("USDT") and not sym.endswith("BUSD"):
            sym = f"{sym}USDT"

        signals = self._get_signals(symbol=sym)
        if not signals:
            return f"⚪ No tactical observations available for {sym}."

        s = signals[0]
        score = s.get("score", 0)
        verdict = s.get("verdict", "NO_DATA")
        features = s.get("features", {})
        reasons = s.get("reasons", [])

        lines = [
            f"📊 <b>TACTICAL ANALYSIS: {sym}</b>\n",
            f"<b>Conviction Score:</b> <code>{score:.1f}/100</code> | <b>Tier:</b> <code>{verdict}</code>",
            f"<b>Current Price:</b> <code>{fmt_price(s.get('price'))}</code>\n",
            "<b>Technical Features:</b>",
            f"• Volatility Regime: <code>{features.get('volatility_regime', 'NORMAL')}</code>",
            f"• Volume (RVOL): <code>{features.get('rvol', 1.0):.2f}x</code>",
            f"• Directional Bias: <code>{features.get('directional_bias', 0.0):+.2f}</code>",
            f"• BBW Percentile: <code>{features.get('bbw_percentile', 0.0):.1f}%</code>",
            f"• ATR Ratio: <code>{features.get('atr_ratio', 1.0):.2f}</code>\n",
        ]

        if reasons:
            lines.append("<b>Confluence Drivers:</b>")
            for r in reasons[:4]:
                lines.append(f"• {r}")

        lines.append("\n<i>Mode: PAPER / ADVISORY</i>")
        return "\n".join(lines)

    def _handle_market(self) -> str:
        dashboard = self._fetch_api("/api/v1/dashboard")
        markets = dashboard.get("markets", [])
        if not markets:
            return "⚪ Market overview temporarily unavailable."

        # Sort gainers
        sorted_m = sorted(markets, key=lambda x: float(x.get("change_24h", 0.0)), reverse=True)
        top_gainers = sorted_m[:4]
        top_losers = sorted_m[-4:][::-1]

        lines = [
            "🌐 <b>24H MARKET OVERVIEW</b>\n",
            "<b>Top Gainers:</b>",
        ]
        for m in top_gainers:
            sym = m.get("symbol")
            p = fmt_price(m.get("price"))
            chg = float(m.get("change_24h", 0.0))
            lines.append(f"• <b>{sym}</b>: <code>{p}</code> (🟢 +{chg:.2f}%)")

        lines.append("\n<b>Top Decliners:</b>")
        for m in top_losers:
            sym = m.get("symbol")
            p = fmt_price(m.get("price"))
            chg = float(m.get("change_24h", 0.0))
            lines.append(f"• <b>{sym}</b>: <code>{p}</code> (🔴 {chg:.2f}%)")

        lines.append(f"\nTotal Universe: <code>{len(markets)} perpetuals</code>")
        return "\n".join(lines)

    def _handle_price(self, symbol: str) -> str:
        sym = symbol.upper()
        if not sym.endswith("USDT") and not sym.endswith("BUSD"):
            sym = f"{sym}USDT"

        dashboard = self._fetch_api("/api/v1/dashboard")
        markets = dashboard.get("markets", [])
        target = next((m for m in markets if m.get("symbol") == sym), None)

        if not target:
            # Try plan price
            plan = self._fetch_api("/api/v1/plan", query={"symbol": sym})
            if plan and plan.get("entry_price"):
                p = fmt_price(plan.get("entry_price"))
                return f"🏷️ <b>{sym}</b>: <code>{p}</code>\nATR(14): <code>{fmt_price(plan.get('atr_14'))}</code>"
            return f"⚪ Symbol {sym} not found in tracked liquid universe."

        price = fmt_price(target.get("price"))
        chg = float(target.get("change_24h", 0.0))
        chg_sign = "+" if chg > 0 else ""
        chg_emoji = "🟢" if chg >= 0 else "🔴"

        return (
            f"🏷️ <b>PRICE CHECK: {sym}</b>\n\n"
            f"<b>Price:</b> <code>{price}</code>\n"
            f"<b>24h Change:</b> {chg_emoji} <code>{chg_sign}{chg:.2f}%</code>\n\n"
            f"<i>Source: Binance USDT-M Perpetuals Public API</i>"
        )

    def _handle_signal(self, symbol: str) -> str:
        return self._handle_tactical(symbol)

    def _handle_positions(self) -> str:
        positions_data = self._fetch_api("/api/v1/positions")
        positions_list = positions_data if isinstance(positions_data, list) else positions_data.get("positions", [])
        risk_data = self._fetch_api("/api/v1/risk")
        max_pos = risk_data.get("max_concurrent_positions", 2)
        return format_positions(positions_list, max_pos=max_pos)

    def _handle_trades(self, scope: str = "current_run") -> str:
        data = self._fetch_api("/api/v1/trades", query={"scope": scope})
        return format_trades(data, scope=scope)

    def _handle_pnl(self) -> str:
        status_data = self._fetch_api("/api/v1/status")
        return format_pnl(status_data)

    def _handle_risk(self) -> str:
        data = self._fetch_api("/api/v1/risk")
        return format_risk(data)

    def _handle_equity(self) -> str:
        status_data = self._fetch_api("/api/v1/status")
        return format_pnl(status_data)

    def _handle_run(self) -> str:
        try:
            summary = self._fetch_api("/api/v1/trades")
            curr_run = summary.get("current_run", {})
        except Exception:
            curr_run = {}

        # Fetch status for scan & latency info
        status_data = self._fetch_api("/api/v1/status")

        data = {
            "run_id": status_data.get("run_id", "active_run"),
            "status": "RUNNING",
            "elapsed_hours": status_data.get("elapsed_hours", 0.0),
            "initial_equity": 10000.0,
            "current_equity": status_data.get("current_equity", 10000.0),
            "net_pnl_usd": status_data.get("today_pnl_usd", 0.0),
            "net_return_pct": status_data.get("today_pnl_pct", 0.0),
            "max_drawdown_pct": status_data.get("daily_drawdown_pct", 0.0),
            "scans_completed": status_data.get("symbols_tracked", 100),
            "scan_failures_count": status_data.get("consecutive_scan_failures", 0),
            "scan_latency_avg_ms": status_data.get("scan_latency_ms", 0),
            "trades_opened_count": curr_run.get("trades_opened_count", 0),
            "trades_closed_count": curr_run.get("trades_closed_count", 0),
        }
        return format_run(data)

    def _handle_alerts(self, args: list[str]) -> str:
        if args and len(args) >= 2:
            cat = args[0].upper()
            action = args[1].lower()
            state = action in ("on", "enable", "true", "1")
            try:
                self._post_api("/api/v1/alerts/settings", {"category": cat, "state": state})
                return f"🔔 Alert category <b>{cat}</b> set to <code>{'ON' if state else 'OFF'}</code>."
            except Exception as exc:
                # Direct dispatcher fallback
                disp = get_telegram_dispatcher()
                if disp.update_category(cat, state):
                    return f"🔔 Alert category <b>{cat}</b> set to <code>{'ON' if state else 'OFF'}</code>."
                return f"⚠️ Failed to update alert category ({exc})"

        data = self._fetch_api("/api/v1/telegram")
        return format_alerts_status(data)

    def _handle_alerts_toggle_all(self, enabled: bool) -> str:
        try:
            self._post_api("/api/v1/alerts/settings", {"enabled": enabled})
        except Exception:
            get_telegram_dispatcher().set_alerts_enabled(enabled)

        state_str = "ENABLED" if enabled else "DISABLED (Critical Safety Alerts Remain Always Active)"
        return f"🔔 All Telegram alerts have been <b>{state_str}</b>."

    def _handle_settings(self) -> str:
        risk_data = self._fetch_api("/api/v1/risk")
        tel_data = self._fetch_api("/api/v1/telegram")
        return format_settings({"risk": risk_data, "telegram_alerts": tel_data})

    # ── Control Plane & Governance Handlers ─────────────────────────────────────

    def _handle_control_start(self, args: list[str]) -> str:
        try:
            res = self._post_api("/api/v1/control/start", {"actor": "telegram_operator", "source": "telegram"})
            return format_control_response(res)
        except Exception as exc:
            return f"⚠️ Failed to start system: {exc}"

    def _handle_control_stop(self, args: list[str]) -> str:
        reason = " ".join(args) if args else "Stopped via Telegram"
        try:
            res = self._post_api("/api/v1/control/stop", {"actor": "telegram_operator", "source": "telegram", "reason": reason})
            return format_control_response(res)
        except Exception as exc:
            return f"⚠️ Failed to stop system: {exc}"

    def _handle_control_pause(self, args: list[str]) -> str:
        reason = " ".join(args) if args else "Paused via Telegram"
        try:
            res = self._post_api("/api/v1/control/pause", {"actor": "telegram_operator", "source": "telegram", "reason": reason})
            return format_control_response(res)
        except Exception as exc:
            return f"⚠️ Failed to pause system: {exc}"

    def _handle_control_resume(self, args: list[str]) -> str:
        try:
            res = self._post_api("/api/v1/control/resume", {"actor": "telegram_operator", "source": "telegram"})
            return format_control_response(res)
        except Exception as exc:
            return f"⚠️ Failed to resume system: {exc}"

    def _handle_control_restart(self, args: list[str]) -> str:
        try:
            res = self._post_api("/api/v1/control/restart", {"actor": "telegram_operator", "source": "telegram"})
            return format_control_response(res)
        except Exception as exc:
            return f"⚠️ Failed to restart system: {exc}"

    def _handle_control_emergency_stop(self, args: list[str]) -> str:
        reason = " ".join(args) if args else "Emergency stop triggered via Telegram"
        try:
            res = self._post_api("/api/v1/control/emergency_stop", {"actor": "telegram_operator", "source": "telegram", "reason": reason})
            return format_control_response(res)
        except Exception as exc:
            return f"⚠️ Failed to execute emergency stop: {exc}"

    def _handle_control_flatten(self, args: list[str]) -> str:
        symbol = args[0].upper() if args else None
        if symbol and not symbol.endswith("USDT") and not symbol.endswith("BUSD"):
            symbol = f"{symbol}USDT"
        try:
            res = self._post_api("/api/v1/control/flatten", {"actor": "telegram_operator", "source": "telegram", "symbol": symbol, "reason": "Flattened via Telegram"})
            return format_control_response(res)
        except Exception as exc:
            return f"⚠️ Failed to flatten positions: {exc}"

    def _handle_control_ack(self, args: list[str]) -> str:
        alert_id = args[0] if args else ""
        try:
            res = self._post_api("/api/v1/alerts/ack", {"actor": "telegram_operator", "alert_id": alert_id})
            return f"✅ Alert <code>{alert_id}</code> acknowledged." if res.get("success") else "⚠️ Failed to acknowledge alert."
        except Exception as exc:
            return f"⚠️ Error acknowledging alert: {exc}"

    def _handle_team(self, args: list[str]) -> str:
        try:
            data = self._fetch_api("/api/v1/team")
            team_list = data.get("team", [])
            return format_team(team_list)
        except Exception as exc:
            return f"⚠️ Failed to fetch team status: {exc}"

    def _handle_agent(self, args: list[str]) -> str:
        if not args:
            return "⚠️ Please specify agent identifier (e.g. <code>/agent risk_manager</code> or <code>/agent operations</code>). Use /team to list."
        agent_id = args[0].lower()
        try:
            data = self._fetch_api(f"/api/v1/team/{agent_id}")
            if not data or "agent_id" not in data:
                return f"⚠️ Agent <code>{agent_id}</code> not found. Use /team to list all 8 agents."
            return format_agent(data)
        except Exception as exc:
            return f"⚠️ Failed to fetch agent profile: {exc}"

    def _handle_investment(self, args: list[str]) -> str:
        try:
            data = self._fetch_api("/api/v1/investment")
            return format_investment(data)
        except Exception as exc:
            return f"⚠️ Failed to fetch investment research overview: {exc}"

    def _handle_theses(self, args: list[str]) -> str:
        try:
            data = self._fetch_api("/api/v1/investment")
            theses = data.get("theses", [])
            if not theses:
                return "No active investment theses."
            lines = [
                "📜 <b>ACTIVE INVESTMENT THESES (RESEARCH ONLY)</b>",
                "━━━━━━━━━━━━━━━━━━━━━",
                "⚠️ <i>RESEARCH ONLY — NEVER DIRECTLY EXECUTED</i>\n",
            ]
            for t in theses:
                lines.append(
                    f"• <b>{t.get('symbol')}</b> ({t.get('asset_name')}): <code>{t.get('sentiment')}</code> [{t.get('conviction')}]\n"
                    f"  Zone: ${t.get('accumulation_zone_low', 0):,.2f}-${t.get('accumulation_zone_high', 0):,.2f} | Target: ${t.get('target_price_conservative', 0):,.2f}"
                )
            lines.append("\n━━━━━━━━━━━━━━━━━━━━━")
            lines.append("<i>For full deep-dive: /thesis [SYMBOL]</i>")
            return "\n".join(lines)
        except Exception as exc:
            return f"⚠️ Failed to fetch investment theses: {exc}"

    def _handle_thesis(self, args: list[str]) -> str:
        if not args:
            return "⚠️ Please specify symbol (e.g. <code>/thesis BTC</code>, <code>/thesis ETH</code>, <code>/thesis SOL</code>, <code>/thesis TAO</code>)."
        sym = args[0].upper()
        if not sym.endswith("USDT") and not sym.endswith("BUSD"):
            sym = f"{sym}USDT"
        try:
            data = self._fetch_api("/api/v1/investment/theses", query={"symbol": sym})
            if not data or "symbol" not in data:
                return f"⚠️ No active research thesis found for <b>{sym}</b>. Active: BTC, ETH, SOL, TAO."
            return format_thesis(data)
        except Exception as exc:
            return f"⚠️ Failed to fetch thesis for {sym}: {exc}"

    def _handle_watchlist(self, args: list[str]) -> str:
        try:
            data = self._fetch_api("/api/v1/investment/watchlist")
            wl = data.get("watchlist", [])
            return format_watchlist(wl)
        except Exception as exc:
            return f"⚠️ Failed to fetch watchlist: {exc}"

    def _handle_report(self, args: list[str]) -> str:
        try:
            data = self._fetch_api("/api/v1/reports/latest")
            if not data:
                return "⚠️ No executive reports available yet. A report is generated each hour."
            return format_report(data)
        except Exception as exc:
            return f"⚠️ Failed to fetch executive report: {exc}"

    def _handle_events(self, args: list[str]) -> str:
        try:
            data = self._fetch_api("/api/v1/audit", query={"limit": 8})
            events = data.get("events", [])
            return format_audit_events(events)
        except Exception as exc:
            return f"⚠️ Failed to fetch audit events: {exc}"

    def _handle_intel(self, args: list[str]) -> str:
        symbol = args[0].upper() if args else "BTCUSDT"
        try:
            res = self._post_api("/api/v1/control/intelligence", payload={"symbol": symbol})
            result = res.get("result", {})
            source = result.get("source", "Intelligence Engine")
            analysis = result.get("analysis", "No analysis returned.")
            disclaimer = result.get("disclaimer", "[AI RESEARCH — NOT FINANCIAL ADVICE]")
            return (
                f"🧠 <b>APEX MARKET INTELLIGENCE — {symbol}</b>\n"
                f"<b>Source:</b> {source}\n\n"
                f"{analysis}\n\n"
                f"<i>{disclaimer}</i>"
            )
        except Exception as exc:
            return f"⚠️ Failed to fetch intelligence: {exc}"

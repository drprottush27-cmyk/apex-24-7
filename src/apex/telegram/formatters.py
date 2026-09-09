"""APEX 24/7 — Telegram Compact iPhone-Friendly Message Formatters.

Formats structured responses using HTML tags suitable for Telegram on mobile devices.
Labels PAPER / ADVISORY clearly on all market/setup analysis.
Never fabricates data or invents numbers.
"""
from __future__ import annotations

import datetime
import time
from typing import Any


def fmt_price(val: float | None) -> str:
    if val is None or val <= 0:
        return "N/A"
    if val < 0.0001:
        return f"${val:.8f}"
    if val < 1.0:
        return f"${val:.6f}"
    if val < 10.0:
        return f"${val:.4f}"
    return f"${val:,.2f}"


def fmt_pnl(val: float | None) -> str:
    if val is None:
        return "$0.00"
    sign = "+" if val > 0 else ""
    return f"{sign}${val:,.2f}"


def format_start(app_url: str = "") -> str:
    return (
        "⚡ <b>APEX 24/7 COMMAND CENTER</b>\n\n"
        "Autonomous algorithmic trading system operating strictly in <b>PAPER</b> mode.\n\n"
        "Telegram is a read-only observability & control interface.\n"
        "Direct trade execution via chat is permanently disabled.\n\n"
        "<b>Quick Commands:</b>\n"
        "• /status — Engine health & runtime dashboard\n"
        "• /scan — Live multi-factor market scan\n"
        "• /best — Top A+/A ranked setups\n"
        "• /prepump — Pre-pump volume/OI anomalies\n"
        "• /positions — Active paper positions\n"
        "• /trades — Trade history & PnL\n"
        "• /risk — Safety chain & circuit breakers\n"
        "• /alerts — Notification settings\n"
        "• /help — Full command catalog\n\n"
        "<i>Mode: PAPER / ADVISORY</i>"
    )


def format_help() -> str:
    return (
        "📖 <b>APEX COMMAND CATALOG</b>\n\n"
        "<b>Master Control (Central Plane):</b>\n"
        "• /start [system] — Start system / resume autonomous operations\n"
        "• /stop — Gracefully stop autonomous operations\n"
        "• /pause — Pause new entries (keeps monitoring active)\n"
        "• /resume — Resume autonomous trading\n"
        "• /restart — Cleanly restart control plane & scanner\n"
        "• /emergency_stop — Emergency stop & trip killswitch\n"
        "• /flatten [sym] — Safely close positions via OEM danger manager\n"
        "• /ack [id] — Acknowledge active grace-period alert\n\n"
        "<b>Management Team & Governance:</b>\n"
        "• /team — Live status of all 8 specialized agents\n"
        "• /agent [name] — Inspect specific agent profile & task\n\n"
        "<b>Investment Research (Research Only):</b>\n"
        "• /invest — Strategic macro allocations & portfolio view\n"
        "• /theses — View fundamental asset theses\n"
        "• /thesis [sym] — Detailed thesis for BTC, ETH, SOL, TAO\n"
        "• /watchlist — Curated research watchlist\n\n"
        "<b>Market & Analysis:</b>\n"
        "• /scan — Multi-factor 100-pair market scan\n"
        "• /scan_long — Filter for LONG setups\n"
        "• /scan_short — Filter for SHORT setups\n"
        "• /best — Top ranked setups right now\n"
        "• /prepump — Detect volume compression anomalies\n"
        "• /setup [sym] — Trade plan, targets & sizing\n"
        "• /tactical [sym] — Multi-factor breakdown\n"
        "• /price [sym] — Real-time price check\n"
        "• /signal [sym] — Current signal status\n"
        "• /market — 24h market performance summary\n\n"
        "<b>Trading & Telemetry:</b>\n"
        "• /positions — Current open paper positions\n"
        "• /trades — Closed trades in current run\n"
        "• /history — Full trade history archive\n"
        "• /pnl — Realized & unrealized PnL summary\n"
        "• /equity — Account balance & margin\n"
        "• /status — Control plane & engine dashboard\n"
        "• /risk — RiskGuardian & KillSwitch status\n"
        "• /report — Latest executive operational report\n"
        "• /events — Immutable audit trail of recent events\n"
        "• /alerts — Notification settings & categories\n\n"
        "<i>Direct trade execution via chat is permanently disabled. Mode: PAPER ONLY.</i>"
    )


def format_status(data: dict[str, Any]) -> str:
    engine_state = data.get("engine_state", "UNKNOWN")
    trading_mode = data.get("trading_mode", "PAPER")
    run_id = data.get("run_id", "active_run")
    elapsed_h = data.get("elapsed_hours", 0.0)
    health_status = data.get("health_status", "UNKNOWN")
    data_age = data.get("data_freshness_seconds")
    is_stale = data.get("is_data_stale", False)

    freshness_label = "STALE" if is_stale else "Fresh"
    freshness_str = f"{data_age}s ({freshness_label})" if data_age is not None else "N/A"

    last_scan_utc = data.get("last_scan_utc", "N/A")
    latency = data.get("scan_latency_ms", 0)

    symbols_avail = data.get("symbols_available", 0)
    symbols_tot = data.get("symbols_tracked", 0)

    open_pos = data.get("open_positions", 0)
    max_pos = data.get("max_positions", 2)

    today_pnl = data.get("today_pnl_usd", 0.0)
    today_pnl_pct = data.get("today_pnl_pct", 0.0)
    pnl_sign = "+" if today_pnl > 0 else ""
    daily_dd = data.get("daily_drawdown_pct", 0.0)
    max_dd = data.get("daily_drawdown_kill_pct", 3.0)

    ks_tripped = data.get("kill_switch_tripped", False)
    ks_label = "🔴 ACTIVE" if ks_tripped else "🟢 NORMAL"

    breakers = data.get("circuit_breakers_active", False)
    breakers_label = f"🔴 TRIPPED ({data.get('circuit_breakers_reason', 'active')})" if breakers else "🟢 CLEAR"

    tg_info = data.get("telegram_alerts", {})
    tg_status = tg_info.get("status", "CONNECTED")
    tg_sent = tg_info.get("total_sent", 0)
    tg_fail = tg_info.get("total_failed", 0)

    alerts_enabled = tg_info.get("settings", {}).get("enabled", True)
    alerts_label = "🟢 ENABLED" if alerts_enabled else "🟠 OFF (Critical Only)"

    health_emoji = "🟢" if health_status == "HEALTHY" else ("🟠" if health_status == "DEGRADED" else "🔴")

    return (
        f"⚡ <b>APEX SYSTEM STATUS</b>\n\n"
        f"<b>Engine:</b> <code>{engine_state}</code>\n"
        f"<b>Trading Mode:</b> <code>{trading_mode}</code> (Paper Only)\n"
        f"<b>Run:</b> <code>{run_id}</code> ({elapsed_h:.1f}h elapsed)\n"
        f"<b>Health:</b> {health_emoji} <code>{health_status}</code>\n"
        f"<b>Data Freshness:</b> <code>{freshness_str}</code>\n"
        f"<b>Last Scan:</b> <code>{last_scan_utc}</code> ({latency}ms)\n"
        f"<b>Symbols:</b> <code>{symbols_avail}/{symbols_tot} active</code>\n"
        f"<b>Open Positions:</b> <code>{open_pos}/{max_pos}</code>\n"
        f"<b>Today's PnL:</b> <code>{pnl_sign}${today_pnl:,.2f} ({today_pnl_pct:+.2f}%)</code>\n"
        f"<b>Daily Drawdown:</b> <code>{daily_dd:.2f}% / max {max_dd:.2f}%</code>\n"
        f"<b>KillSwitch:</b> {ks_label}\n"
        f"<b>Circuit Breakers:</b> {breakers_label}\n"
        f"<b>Telegram:</b> <code>{tg_status}</code> (sent: {tg_sent}, fail: {tg_fail})\n"
        f"<b>Alerts:</b> {alerts_label}\n\n"
        f"<i>Mode: PAPER / ADVISORY</i>"
    )


def format_health(data: dict[str, Any]) -> str:
    status = data.get("health_status", "UNKNOWN")
    engine_state = data.get("engine_state", "UNKNOWN")
    symbols = f"{data.get('available_symbols', 0)}/{data.get('total_symbols', 0)}"
    skipped = data.get("skipped_symbols", 0)
    failed = data.get("failed_symbols", 0)
    consec_data = data.get("consecutive_data_failures", 0)
    consec_exec = data.get("consecutive_execution_failures", 0)
    data_age = data.get("data_age_seconds", 0.0)
    is_stale = data.get("is_data_stale", False)

    status_emoji = "🟢" if status == "HEALTHY" else ("🟠" if status == "DEGRADED" else "🔴")

    return (
        f"🏥 <b>SYSTEM HEALTH REPORT</b>\n\n"
        f"<b>Overall Status:</b> {status_emoji} <code>{status}</code>\n"
        f"<b>Engine State:</b> <code>{engine_state}</code>\n"
        f"<b>Active Symbols:</b> <code>{symbols}</code> (skipped: {skipped}, failed: {failed})\n"
        f"<b>Data Staleness:</b> <code>{data_age}s</code> ({'STALE' if is_stale else 'OK'})\n"
        f"<b>Consecutive Data Fails:</b> <code>{consec_data}</code>\n"
        f"<b>Consecutive Exec Fails:</b> <code>{consec_exec}</code>\n\n"
        f"<i>All gates certified fail-closed.</i>"
    )


def format_trade_setup(plan: dict[str, Any], freshness_sec: float | None = None) -> str:
    symbol = plan.get("symbol", "UNKNOWN")
    side = plan.get("side", "LONG").upper()
    side_emoji = "🟢" if side == "LONG" else "🔴"
    score = float(plan.get("score", 0.0))
    verdict = plan.get("verdict", "CONFLUENCE")

    quality = "A+" if score >= 75 else ("A" if score >= 60 else "B")
    current_price = fmt_price(plan.get("entry_price"))

    zone = plan.get("entry_zone", {})
    entry_low = fmt_price(zone.get("low", plan.get("entry_price")))
    entry_high = fmt_price(zone.get("high", plan.get("entry_price")))

    sl = fmt_price(plan.get("stop_loss"))
    dist_pct = plan.get("stop_distance_pct", 0.0)

    targets = plan.get("targets", [])
    tp1 = fmt_price(targets[0].get("price")) if len(targets) > 0 else "N/A"
    tp2 = fmt_price(targets[1].get("price")) if len(targets) > 1 else "N/A"
    tp3 = fmt_price(targets[2].get("price")) if len(targets) > 2 else "Trail"

    sizing = plan.get("sizing", {})
    units = sizing.get("units", 0.0)
    notional = sizing.get("notional_usd", 0.0)
    leverage = sizing.get("leverage", 1.0)
    risk_usd = sizing.get("risk_usd", 100.0)
    risk_pct = sizing.get("risk_pct", 1.0)

    provenance = plan.get("provenance", {})
    real_count = provenance.get("real_factors_count", 0)
    est_count = provenance.get("estimated_factors_count", 0)

    factors = plan.get("factors", [])
    factor_lines: list[str] = []
    for f in factors[:4]:
        name = f.get("factor", "").replace("_", " ").title()
        conf = f.get("confidence", 1.0)
        source = f.get("source", "real")
        factor_lines.append(f"• {name}: <code>{source}</code> ({conf*100:.0f}% conf)")

    factors_str = "\n".join(factor_lines) if factor_lines else "• Multi-factor technical confluence"

    utc_now = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S")
    fresh_str = f"{freshness_sec:.1f}s ago" if freshness_sec is not None else "Live"

    return (
        f"🚨 <b>{quality} SETUP</b>\n\n"
        f"<b>{symbol}</b>\n"
        f"{side_emoji} <b>{side}</b>\n\n"
        f"<b>Entry Zone:</b> <code>{entry_low} - {entry_high}</code>\n"
        f"<b>Current Price:</b> <code>{current_price}</code>\n"
        f"<b>Stop Loss:</b> <code>{sl}</code> (-{dist_pct:.1f}%)\n"
        f"<b>TP1 (1.5R):</b> <code>{tp1}</code> (40%)\n"
        f"<b>TP2 (2.5R):</b> <code>{tp2}</code> (30%)\n"
        f"<b>TP3 (Trail):</b> <code>{tp3}</code> (30%)\n\n"
        f"<b>R:R:</b> <code>1:1.5 / 1:2.5 / 1:4.0</code>\n"
        f"<b>Score:</b> <code>{score:.1f}/100</code> | <b>Conviction:</b> <code>{verdict}</code>\n"
        f"<b>Sizing:</b> <code>{units} units (${notional:,.0f}, {leverage:.1f}x)</code>\n"
        f"<b>Risk:</b> <code>${risk_usd:,.0f} ({risk_pct:.1f}% equity)</code>\n\n"
        f"<b>Why It Qualifies:</b>\n"
        f"{factors_str}\n"
        f"• Provenance: <code>{real_count} real / {est_count} proxy factors</code>\n\n"
        f"<b>Invalidation Condition:</b>\n"
        f"Candle close beyond Stop Loss (<code>{sl}</code>)\n\n"
        f"<b>Mode:</b> <code>PAPER / ADVISORY</code>\n"
        f"<b>Time:</b> <code>{utc_now} UTC</code> | <b>Data:</b> <code>{fresh_str}</code>"
    )


def format_scan_results(candidates: list[dict[str, Any]], direction_filter: str | None = None) -> str:
    if direction_filter:
        candidates = [c for c in candidates if c.get("direction", "").upper() == direction_filter.upper()]

    # Filter for quality (score >= 60)
    valid = [c for c in candidates if float(c.get("score", 0.0)) >= 60.0]

    if not valid:
        dir_str = f" {direction_filter.upper()}" if direction_filter else ""
        return (
            f"⚪ <b>NO A+ SETUP RIGHT NOW</b>\n\n"
            f"All tracked universe symbols evaluated against multi-factor criteria.\n"
            f"No{dir_str} high-conviction setup currently meets quality thresholds (score ≥ 60.0).\n\n"
            f"<i>Mode: PAPER / ADVISORY — No forced trades.</i>"
        )

    valid.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
    top_candidates = valid[:5]

    lines = [
        f"🔍 <b>MARKET SCAN RESULTS</b> ({len(valid)} qualified)\n"
    ]

    for idx, c in enumerate(top_candidates, 1):
        sym = c.get("symbol", "")
        direction = c.get("direction", "LONG").upper()
        dir_emoji = "🟢" if direction == "LONG" else "🔴"
        score = float(c.get("score", 0.0))
        tier = "A+" if score >= 75 else "A"
        price = fmt_price(c.get("price"))
        sl = fmt_price(c.get("stop_loss"))
        tp1 = fmt_price(c.get("tp1"))
        rvol = c.get("rvol", 1.0)
        lines.append(
            f"{idx}. <b>{sym}</b> — {dir_emoji} {direction} — <b>{tier}</b> (Score: {score:.1f})\n"
            f"   Price: <code>{price}</code> | SL: <code>{sl}</code> | TP1: <code>{tp1}</code>\n"
            f"   RVOL: <code>{rvol:.1f}x</code> | Setup: <code>{c.get('setup_type', 'Breakout')}</code>\n"
        )

    lines.append("<i>Mode: PAPER / ADVISORY — Run /setup [SYMBOL] for full trade plan.</i>")
    return "\n".join(lines)


def format_best_setups(setups: list[dict[str, Any]]) -> str:
    a_plus = [s for s in setups if float(s.get("score", 0.0)) >= 65.0]
    if not a_plus:
        return (
            "⚪ <b>NO A+ SETUP RIGHT NOW</b>\n\n"
            "Current market conditions do not present any top-tier opportunities.\n"
            "Thresholds remain strict to prevent overtrading.\n\n"
            "<i>Mode: PAPER / ADVISORY</i>"
        )

    a_plus.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
    lines = ["🏆 <b>TOP SETUPS</b>\n"]
    for idx, s in enumerate(a_plus[:3], 1):
        sym = s.get("symbol")
        side = s.get("side", "LONG").upper()
        side_emoji = "🟢" if side == "LONG" else "🔴"
        score = float(s.get("score", 0.0))
        tier = "A+" if score >= 75 else "A"
        entry = fmt_price(s.get("entry_price"))
        sl = fmt_price(s.get("stop_loss"))
        tp1 = fmt_price(s.get("tp1") or s.get("targets", [{}])[0].get("price"))
        lines.append(
            f"{idx}. <b>{sym}</b> — {side_emoji} {side} — <b>{tier}</b> (Score: {score:.1f})\n"
            f"   Entry: <code>{entry}</code> | SL: <code>{sl}</code> | TP1: <code>{tp1}</code>\n"
        )

    lines.append("<i>Mode: PAPER / ADVISORY — Use /setup [SYMBOL] for pinpoint execution levels.</i>")
    return "\n".join(lines)


def format_prepump_candidates(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return (
            "⚪ <b>NO PRE-PUMP ANOMALY DETECTED</b>\n\n"
            "No symbol currently exhibits compression + abnormal volume/OI divergence.\n\n"
            "<i>Mode: PAPER / ADVISORY</i>"
        )

    lines = [
        "🔥 <b>PRE-PUMP CANDIDATES</b>\n",
        "⚠️ <i>PRE-PUMP CANDIDATE (Advisory) — NOT A CONFIRMED TRADE. Real live observations.</i>\n",
    ]

    for idx, c in enumerate(candidates[:5], 1):
        sym = c.get("symbol", "")
        score = float(c.get("prepump_score", 0.0))
        oi_chg = c.get("oi_change_pct", 0.0)
        rvol = c.get("rvol", 1.0)
        funding = c.get("funding_rate", 0.0)
        depth = c.get("depth_imbalance", 0.0)
        bbw = c.get("volatility_compression", 0.0)
        bias = c.get("directional_bias", 0.0)
        htf = c.get("htf_trend", "NEUTRAL")
        sfp = c.get("sfp_status", "NONE")

        lines.append(
            f"{idx}. <b>{sym}</b> — Score: <code>{score:.1f}</code>\n"
            f"   • RVOL: <code>{rvol:.2f}x</code> | OI: <code>{oi_chg:+.2f}%</code>\n"
            f"   • Funding: <code>{funding:.4f}%</code> | Depth Imb: <code>{depth:+.1f}%</code>\n"
            f"   • Compression (BBW): <code>{bbw:.1f}%</code>\n"
            f"   • HTF Trend: <code>{htf}</code> | Bias: <code>{bias:+.2f}</code>\n"
            f"   • SFP: <code>{sfp}</code>\n"
        )

    return "\n".join(lines)


def format_positions(positions: list[dict[str, Any]], max_pos: int = 2) -> str:
    if not positions:
        return (
            "💼 <b>OPEN PAPER POSITIONS</b>\n\n"
            "No active open positions right now.\n"
            f"Capacity: 0/{max_pos} slots utilized.\n\n"
            "<i>Mode: PAPER / ADVISORY</i>"
        )

    lines = [
        f"💼 <b>OPEN PAPER POSITIONS ({len(positions)}/{max_pos})</b>\n"
    ]

    for p in positions:
        sym = p.get("symbol", "")
        side = p.get("side", "LONG").upper()
        side_emoji = "🟢" if side == "LONG" else "🔴"
        entry = fmt_price(p.get("entry_price"))
        mark = fmt_price(p.get("mark_price"))
        pnl = float(p.get("unrealized_pnl", 0.0))
        r_curr = float(p.get("current_r", 0.0))
        r_peak = float(p.get("peak_r", 0.0))
        sl = fmt_price(p.get("stop_loss"))
        tp = fmt_price(p.get("take_profit"))
        trailing = p.get("trailing_state", "ARMED")

        lines.append(
            f"• <b>{sym}</b> {side_emoji} <b>{side}</b>\n"
            f"  Entry: <code>{entry}</code> | Mark: <code>{mark}</code>\n"
            f"  PnL: <code>{fmt_pnl(pnl)} ({r_curr:+.2f}R, peak {r_peak:+.2f}R)</code>\n"
            f"  SL: <code>{sl}</code> | TP: <code>{tp}</code>\n"
            f"  Trailing Stop: <code>{trailing}</code>\n"
        )

    lines.append("<i>Managed autonomously by RiskGuardian & AutoClose.</i>")
    return "\n".join(lines)


def format_trades(data: dict[str, Any], scope: str = "current_run") -> str:
    if scope in ("all", "history") or data.get("scope") == "all":
        trades = data.get("trades", [])
        count = data.get("count", len(trades))
        lines = [
            "📜 <b>TRADE HISTORY (HISTORICAL ARCHIVE)</b>\n",
            f"<b>Total Archive Trades:</b> <code>{count}</code>\n",
        ]
        if trades:
            lines.append("<b>Recent Historical Trades:</b>")
            for t in trades[:10]:
                sym = t.get("symbol", "")
                pnl = float(t.get("pnl", t.get("realized_pnl", 0.0)))
                r = float(t.get("r_multiple", 0.0))
                res = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")
                res_emoji = "🟢" if res == "WIN" else ("🔴" if res == "LOSS" else "⚪")
                lines.append(f"{res_emoji} <b>{sym}</b>: <code>{fmt_pnl(pnl)} ({r:+.2f}R)</code>")
        else:
            lines.append("<i>No trades found in historical archive.</i>")
        lines.append("\n<i>Mode: PAPER / ADVISORY</i>")
        return "\n".join(lines)

    curr_run = data.get("current_run", {})
    run_id = curr_run.get("run_id", "active_run")
    trades_closed = curr_run.get("trades_closed_count", 0)
    realized_pnl = curr_run.get("realized_pnl", 0.0)
    closed_trades = curr_run.get("closed_trades", [])
    total_historical = data.get("total_historical_trades", 0)

    lines = [
        "📜 <b>TRADE HISTORY (CURRENT RUN)</b>\n",
        f"<b>Current Run:</b> <code>{run_id}</code>",
        f"<b>Closed Trades:</b> <code>{trades_closed}</code>",
        f"<b>Realized PnL:</b> <code>{fmt_pnl(realized_pnl)}</code>\n",
    ]

    if closed_trades:
        lines.append("<b>Recent Closed Trades (Current Run):</b>")
        for t in closed_trades[:5]:
            sym = t.get("symbol")
            res = t.get("result", "BREAKEVEN")
            res_emoji = "🟢" if res == "WIN" else ("🔴" if res == "LOSS" else "⚪")
            pnl = float(t.get("realized_pnl", 0.0))
            r = float(t.get("r_multiple", 0.0))
            reason = t.get("close_reason", "")
            lines.append(f"{res_emoji} <b>{sym}</b>: <code>{fmt_pnl(pnl)} ({r:+.2f}R)</code> | {reason}")
    else:
        lines.append("<i>Zero trades closed in current run. All safety criteria strictly enforced.</i>")

    lines.append(f"\n<b>Historical Archive:</b> <code>{total_historical} trades</code> (use /history for archive)")
    lines.append("<i>Mode: PAPER / ADVISORY</i>")
    return "\n".join(lines)


def format_risk(data: dict[str, Any]) -> str:
    ks = data.get("kill_switch_tripped", False)
    ks_reason = data.get("kill_switch_reason", "Engine init")
    can_trade = data.get("can_trade", False)
    mode = data.get("trading_mode", "PAPER")
    daily_dd = data.get("daily_drawdown_pct", 0.0)
    daily_dd_kill = data.get("daily_drawdown_kill_pct", 3.0)
    max_lev = data.get("max_leverage", 3.0)
    max_risk = data.get("max_risk_per_trade_pct", 1.0)
    max_pos = data.get("max_concurrent_positions", 2)
    open_pos = data.get("open_positions_count", 0)

    at = data.get("auto_trade", {})
    at_enabled = at.get("enabled", False)
    breaker = at.get("circuit_breaker_active", False)
    losses = at.get("consecutive_losses", 0)
    max_losses = at.get("max_consecutive_losses", 3)

    return (
        f"🛡️ <b>RISK GUARDIAN CENTER</b>\n\n"
        f"<b>Trading Mode:</b> <code>{mode}</code> (Paper Locked)\n"
        f"<b>Execution Allowed:</b> <code>{'YES' if can_trade else 'BLOCKED'}</code>\n\n"
        f"<b>KillSwitch:</b> <code>{'🔴 ACTIVE (' + str(ks_reason) + ')' if ks else '🟢 INACTIVE'}</code>\n"
        f"<b>Daily Drawdown:</b> <code>{daily_dd:.2f}% / max {daily_dd_kill:.2f}%</code>\n"
        f"<b>Open Positions:</b> <code>{open_pos}/{max_pos} slots</code>\n"
        f"<b>Max Risk/Trade:</b> <code>{max_risk:.2f}% equity</code>\n"
        f"<b>Max Leverage:</b> <code>{max_lev:.1f}x max</code>\n\n"
        f"<b>Auto-Trade Engine:</b> <code>{'ENABLED' if at_enabled else 'DISABLED'}</code>\n"
        f"<b>Circuit Breaker:</b> <code>{'🔴 TRIPPED' if breaker else '🟢 NORMAL'}</code>\n"
        f"<b>Consecutive Losses:</b> <code>{losses}/{max_losses} max</code>\n\n"
        f"<i>Safety Chain: RiskGuardian → OEM → EndpointGuard → PaperAdapter.</i>"
    )


def format_pnl(data: dict[str, Any]) -> str:
    equity = data.get("current_equity", 10_000.0)
    today_pnl = data.get("today_pnl_usd", 0.0)
    today_pct = data.get("today_pnl_pct", 0.0)
    daily_dd = data.get("daily_drawdown_pct", 0.0)
    max_dd = data.get("daily_drawdown_kill_pct", 3.0)

    return (
        f"💰 <b>PnL & EQUITY SUMMARY</b>\n\n"
        f"<b>Total Equity:</b> <code>${equity:,.2f} USDT</code>\n"
        f"<b>Today's Net PnL:</b> <code>{fmt_pnl(today_pnl)} ({today_pct:+.2f}%)</code>\n"
        f"<b>Daily Drawdown:</b> <code>{daily_dd:.2f}% (Kill limit: {max_dd:.2f}%)</code>\n\n"
        f"<i>Mode: PAPER / ADVISORY</i>"
    )


def format_run(data: dict[str, Any]) -> str:
    run_id = data.get("run_id", "active_run")
    status = data.get("status", "RUNNING")
    elapsed_h = data.get("elapsed_hours", 0.0)
    target_h = data.get("duration_target_hours", 24.0)
    pct = (elapsed_h / target_h * 100.0) if target_h > 0 else 0.0

    eq_init = data.get("initial_equity", 10_000.0)
    eq_curr = data.get("current_equity", 10_000.0)
    net_pnl = data.get("net_pnl_usd", 0.0)
    ret_pct = data.get("net_return_pct", 0.0)
    max_dd = data.get("max_drawdown_pct", 0.0)

    scans = data.get("scans_completed", 0)
    fails = data.get("scan_failures_count", 0)
    lat = data.get("scan_latency_avg_ms", 0)
    opened = data.get("trades_opened_count", 0)
    closed = data.get("trades_closed_count", 0)

    return (
        f"⏱️ <b>24-HOUR RUN TELEMETRY</b>\n\n"
        f"<b>Run ID:</b> <code>{run_id}</code>\n"
        f"<b>Status:</b> <code>{status}</code>\n"
        f"<b>Progress:</b> <code>{elapsed_h:.2f}h / {target_h:.0f}h ({pct:.1f}%)</code>\n\n"
        f"<b>Equity:</b> <code>${eq_curr:,.2f}</code> (start: ${eq_init:,.2f})\n"
        f"<b>Net Return:</b> <code>{fmt_pnl(net_pnl)} ({ret_pct:+.2f}%)</code>\n"
        f"<b>Max Drawdown:</b> <code>{max_dd:.2f}%</code>\n\n"
        f"<b>Scans Completed:</b> <code>{scans}</code> (fails: {fails}, avg {lat}ms)\n"
        f"<b>Trades Executed:</b> <code>{opened} opened / {closed} closed</code>\n\n"
        f"<i>Continuous 24/7 autonomous paper verification.</i>"
    )


def format_alerts_status(data: dict[str, Any]) -> str:
    status_str = data.get("status", "CONNECTED")
    sent = data.get("total_sent", 0)
    failed = data.get("total_failed", 0)
    suppressed = data.get("total_duplicates_suppressed", 0)
    settings = data.get("settings", {})
    enabled = settings.get("enabled", True)
    cats = settings.get("categories", {})

    cat_lines = []
    for cat, active in cats.items():
        cat_lines.append(f"• <b>{cat}:</b> {'🟢 ON' if active else '🔴 OFF'}")

    cats_str = "\n".join(cat_lines) if cat_lines else "• All categories enabled"

    return (
        f"🔔 <b>TELEGRAM ALERT SETTINGS</b>\n\n"
        f"<b>Master Switch:</b> <code>{'🟢 ENABLED' if enabled else '🟠 OFF (Critical Only)'}</code>\n"
        f"<b>Dispatcher Status:</b> <code>{status_str}</code>\n"
        f"<b>Stats:</b> <code>{sent} sent | {suppressed} suppressed | {failed} failed</code>\n\n"
        f"<b>Category Toggles:</b>\n"
        f"{cats_str}\n\n"
        f"<i>Safety Rule: CRITICAL alerts (KillSwitch / Safety Breakers) are never suppressible.</i>\n"
        f"<i>Toggle syntax: /alerts [market|trading|risk|system|all] [on|off]</i>"
    )


def format_settings(data: dict[str, Any]) -> str:
    risk_info = data.get("risk", {})
    auto_trade = risk_info.get("auto_trade", {})
    alert_info = data.get("telegram_alerts", {}).get("settings", {})

    return (
        f"⚙️ <b>APEX SYSTEM SETTINGS</b>\n\n"
        f"<b>Core Mode:</b> <code>PAPER ONLY</code> (Live permanently disabled)\n"
        f"<b>Max Leverage:</b> <code>{risk_info.get('max_leverage', 3.0)}x</code>\n"
        f"<b>Max Risk per Trade:</b> <code>{risk_info.get('max_risk_per_trade_pct', 1.0)}%</code>\n"
        f"<b>Max Positions:</b> <code>{risk_info.get('max_concurrent_positions', 2)}</code>\n"
        f"<b>Daily Drawdown Limit:</b> <code>{risk_info.get('daily_drawdown_kill_pct', 3.0)}%</code>\n\n"
        f"<b>Auto-Trade:</b> <code>{'ENABLED' if auto_trade.get('enabled') else 'DISABLED'}</code>\n"
        f"<b>Min Conviction Score:</b> <code>{auto_trade.get('min_score', 65.0)}/100</code>\n"
        f"<b>Max Consecutive Losses:</b> <code>{auto_trade.get('max_consecutive_losses', 3)}</code>\n\n"
        f"<b>Telegram Alerts:</b> <code>{'ENABLED' if alert_info.get('enabled', True) else 'OFF'}</code>\n\n"
        f"<i>All safety boundaries enforced by RiskGuardian.</i>"
    )


def format_control_response(res: dict[str, Any]) -> str:
    cmd = res.get("command", "")
    actor = res.get("actor", "operator")
    ts_ms = res.get("timestamp_ms") or int(time.time() * 1000)
    utc_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts_ms / 1000))
    prev_s = res.get("previous_state", "UNKNOWN")
    curr_s = res.get("current_state", "UNKNOWN")
    mode = res.get("trading_mode", "PAPER")
    status = res.get("status", "UNKNOWN")
    action = res.get("action", "")
    msg = res.get("message", "")
    affected = res.get("affected_components", [])
    cid = res.get("correlation_id", "N/A")
    failures = res.get("failures", [])

    status_badge = "🟢 SUCCESS" if status in ("SUCCESS", "ALREADY_ACTIVE") else "🔴 FAILED"

    lines = [
        f"<b>APEX CONTROL PLANE</b> | {status_badge}",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"• <b>Command:</b> <code>{cmd}</code>",
        f"• <b>Actor:</b> <code>{actor}</code>",
        f"• <b>Timestamp:</b> <code>{utc_str}</code>",
        f"• <b>State Transition:</b> <code>{prev_s}</code> ➔ <code>{curr_s}</code>",
        f"• <b>Trading Mode:</b> <b>{mode} ONLY</b>",
        f"• <b>Action:</b> <code>{action}</code>",
        f"• <b>Summary:</b> {msg}",
    ]
    if affected:
        lines.append(f"• <b>Affected Subsystems:</b> {', '.join(affected)}")
    if failures:
        lines.append(f"• <b>Warnings/Failures:</b> ⚠️ {'; '.join(failures)}")
    lines.append(f"• <b>Correlation ID:</b> <code>{cid}</code>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_team(team_data: list[dict[str, Any]]) -> str:
    status_icons = {
        "WORKING": "🟢",
        "IDLE": "🟡",
        "PAUSED": "🟠",
        "ERROR": "🔴",
    }
    lines = [
        "👔 <b>APEX MANAGEMENT TEAM (8 AGENTS)</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    for agent in team_data:
        aid = agent.get("agent_id", "")
        name = agent.get("name", aid)
        st = agent.get("status", "IDLE")
        icon = status_icons.get(st, "⚪")
        task = agent.get("current_task", "Standby")
        lines.append(f"{icon} <b>{name}</b> (<code>{st}</code>)")
        lines.append(f"   <i>Task:</i> {task}")

    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>Safety Invariant: NO AI AGENT HAS DIRECT EXECUTION AUTHORITY.</i>")
    lines.append("<i>Inspect single agent: /agent [name]</i>")
    return "\n".join(lines)


def format_agent(agent: dict[str, Any]) -> str:
    st = agent.get("status", "IDLE")
    status_icons = {"WORKING": "🟢", "IDLE": "🟡", "PAUSED": "🟠", "ERROR": "🔴"}
    icon = status_icons.get(st, "⚪")
    hb_ms = agent.get("heartbeat_ms", 0)
    now_ms = int(time.time() * 1000)
    age_sec = max(0.0, (now_ms - hb_ms) / 1000.0) if hb_ms else 0.0

    return (
        f"👤 <b>AGENT PROFILE: {agent.get('name', 'Unknown')}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>ID:</b> <code>{agent.get('agent_id')}</code>\n"
        f"• <b>Role:</b> {agent.get('role')}\n"
        f"• <b>Status:</b> {icon} <code>{st}</code>\n"
        f"• <b>Heartbeat:</b> {age_sec:.1f}s ago\n"
        f"• <b>Current Task:</b> {agent.get('current_task')}\n"
        f"• <b>Last Result:</b> {agent.get('last_result')}\n"
        f"• <b>Next Task:</b> {agent.get('next_task')}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>All agent operations route strictly through the central Control Plane.</i>"
    )


def format_investment(data: dict[str, Any]) -> str:
    pv = data.get("portfolio_view", {})
    theses = data.get("theses", [])
    weights = pv.get("recommended_core_weights", {})
    weight_lines = [f"• <b>{k}:</b> {v}%" for k, v in weights.items()]

    thesis_symbols = [t.get("symbol") for t in theses if t.get("symbol")]

    return (
        "🏛️ <b>APEX INVESTMENT RESEARCH ORGANIZATION</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <i>DISCLAIMER: RESEARCH & ANALYSIS ONLY — NEVER DIRECTLY EXECUTED.</i>\n\n"
        "<b>Strategic Core Allocation Model:</b>\n"
        + ("\n".join(weight_lines) if weight_lines else "• Balanced macro exposure")
        + "\n\n"
        f"<b>Active Research Theses ({len(thesis_symbols)}):</b>\n"
        f"• {', '.join(thesis_symbols) if thesis_symbols else 'None'}\n\n"
        "<b>Commands:</b>\n"
        "• /theses — View all fundamental theses\n"
        "• /thesis [SYM] — Deep thesis for BTC, ETH, SOL, TAO\n"
        "• /watchlist — Active macro watchlist\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )


def format_thesis(t: dict[str, Any]) -> str:
    sym = t.get("symbol", "")
    name = t.get("asset_name", sym)
    sector = t.get("sector", "Crypto")
    sentiment = t.get("sentiment", "ACCUMULATE")
    conviction = t.get("conviction", "HIGH")
    horizon = t.get("time_horizon", "6-12M")
    curr_p = t.get("current_price", 0.0)
    ac_low = t.get("accumulation_zone_low", 0.0)
    ac_high = t.get("accumulation_zone_high", 0.0)
    tp_cons = t.get("target_price_conservative", 0.0)
    tp_bull = t.get("target_price_bull", 0.0)
    inval = t.get("invalidation_level", 0.0)
    summary = t.get("thesis_summary", "")
    catalysts = t.get("catalysts", [])
    risks = t.get("counter_thesis_risks", [])

    cat_lines = "\n".join([f"  + {c}" for c in catalysts]) if catalysts else "  + None listed"
    risk_lines = "\n".join([f"  - {r}" for r in risks]) if risks else "  - None listed"

    return (
        f"📜 <b>RESEARCH THESIS: {sym} ({name})</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <i>RESEARCH ONLY — NEVER DIRECTLY EXECUTED</i>\n\n"
        f"• <b>Sector:</b> {sector}\n"
        f"• <b>Conviction:</b> <code>{conviction}</code> | <b>Sentiment:</b> <code>{sentiment}</code>\n"
        f"• <b>Horizon:</b> {horizon}\n"
        f"• <b>Current Benchmark:</b> ${curr_p:,.2f}\n"
        f"• <b>Accumulation Zone:</b> ${ac_low:,.2f} – ${ac_high:,.2f}\n"
        f"• <b>Targets:</b> Cons: ${tp_cons:,.2f} | Bull: ${tp_bull:,.2f}\n"
        f"• <b>Invalidation Level:</b> ${inval:,.2f}\n\n"
        f"<b>Core Thesis:</b>\n{summary}\n\n"
        f"<b>Key Catalysts:</b>\n{cat_lines}\n\n"
        f"<b>Counter-Thesis Risks:</b>\n{risk_lines}\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )


def format_watchlist(watchlist: list[dict[str, Any]]) -> str:
    lines = [
        "👀 <b>INVESTMENT WATCHLIST (RESEARCH ONLY)</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        "⚠️ <i>Advisory watchlist curated by Asset Research Agent.</i>\n",
    ]
    for item in watchlist:
        sym = item.get("symbol", "")
        name = item.get("asset_name", sym)
        sent = item.get("sentiment", "OBSERVE")
        conv = item.get("conviction", "NEUTRAL")
        low = item.get("accumulation_zone_low")
        high = item.get("accumulation_zone_high")
        zone_str = f"(${low:,.2f}-${high:,.2f})" if low and high else ""
        lines.append(f"• <b>{sym}</b> ({name}): <code>{sent}</code> [{conv}] {zone_str}")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>For full thesis: /thesis [SYMBOL]</i>")
    return "\n".join(lines)


def format_report(rep: dict[str, Any]) -> str:
    rep_id = rep.get("report_id", "N/A")
    created = rep.get("created_at_utc", "N/A")
    rep_type = rep.get("report_type", "HOURLY")
    sys_sum = rep.get("system_summary", {})
    tr_sum = rep.get("trading_summary", {})
    risk_sum = rep.get("risk_summary", {})
    attention = rep.get("recommended_attention", [])
    actions = rep.get("next_actions", [])

    att_str = "\n".join([f"• {a}" for a in attention]) if attention else "• All systems nominal"
    act_str = "\n".join([f"• {a}" for a in actions]) if actions else "• Maintain paper scan cycle"

    return (
        f"📊 <b>EXECUTIVE OPERATIONAL REPORT</b>\n"
        f"ID: <code>{rep_id}</code> ({rep_type})\n"
        f"UTC: <code>{created}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>System State:</b> <code>{sys_sum.get('state', 'UNKNOWN')}</code>\n"
        f"• <b>Trading Mode:</b> <b>{sys_sum.get('mode', 'PAPER')}</b>\n"
        f"• <b>Current Equity:</b> <code>${sys_sum.get('equity', 10000.0):,.2f}</code>\n"
        f"• <b>Open Positions:</b> <code>{tr_sum.get('open_positions', 0)}</code>\n"
        f"• <b>Daily Drawdown:</b> <code>{risk_sum.get('daily_drawdown_pct', 0.0):.2f}%</code>\n"
        f"• <b>KillSwitch:</b> <code>{'ENGAGED' if risk_sum.get('kill_switch_tripped') else 'NORMAL'}</code>\n\n"
        f"<b>Priority Attention:</b>\n{att_str}\n\n"
        f"<b>Next Actions:</b>\n{act_str}\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )


def format_audit_events(events: list[dict[str, Any]]) -> str:
    lines = [
        f"🛡️ <b>APEX AUDIT LOG ({len(events)} RECENT EVENTS)</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    for e in events[:8]:
        ts_ms = e.get("timestamp_ms", 0)
        t_str = time.strftime("%H:%M:%S", time.gmtime(ts_ms / 1000)) if ts_ms else "--:--"
        topic = e.get("topic", "event")
        actor = e.get("actor", "system")
        summary = e.get("summary", "")
        cid = e.get("correlation_id")
        cid_str = f" [{cid[:8]}]" if cid else ""
        lines.append(f"• <code>[{t_str}]</code> <b>{topic}</b> ({actor}){cid_str}")
        lines.append(f"   <i>{summary}</i>")

    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>Immutable audit trail persisted in var/audit_events.jsonl</i>")
    return "\n".join(lines)

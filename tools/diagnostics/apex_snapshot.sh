#!/usr/bin/env bash
set +e

ROOT="/home/apex/apex"
cd "$ROOT" || exit 1

TS="$(date '+%Y%m%d_%H%M%S')"
OUT="$ROOT/data/diagnostics"
REPORT="$OUT/apex_snapshot_${TS}.txt"

mkdir -p "$OUT"

exec > >(tee "$REPORT") 2>&1

echo "======================================================================"
echo " APEX 24/7 — COMPLETE DIAGNOSTIC SNAPSHOT"
echo "======================================================================"
echo "Timestamp : $(date -Is)"
echo "Host      : $(hostname)"
echo "Root      : $ROOT"
echo "Report    : $REPORT"
echo "======================================================================"

section() {
    echo
    echo "======================================================================"
    echo " $1"
    echo "======================================================================"
}

run_cmd() {
    echo
    echo "+ $*"
    "$@" 2>&1
    echo "[exit=$?]"
}

# ----------------------------------------------------------------------
section "01 — SYSTEM / ENVIRONMENT"
# ----------------------------------------------------------------------

run_cmd uname -a
run_cmd whoami
run_cmd pwd
run_cmd date -Is
run_cmd python3 --version
run_cmd "$ROOT/.venv/bin/python" --version

echo
echo "Environment variables relevant to APEX:"
env | grep -E '^APEX_|^PYTHON' | sort || true

# ----------------------------------------------------------------------
section "02 — GIT / SOURCE STATE"
# ----------------------------------------------------------------------

run_cmd git branch --show-current
run_cmd git rev-parse HEAD
run_cmd git rev-parse --short HEAD
run_cmd git status --short --branch
run_cmd git log -1 --oneline --decorate

echo
echo "Recent commits:"
git log -8 --oneline --decorate 2>&1 || true

# ----------------------------------------------------------------------
section "03 — SOURCE / RUNNING-PROCESS MISMATCH"
# ----------------------------------------------------------------------

echo "Source run_api.py:"
grep -nE 'MarketObservationClient|ResilientHTTPTransport|ApexEngine|observation_client=' \
    scripts/run_api.py 2>/dev/null || true

echo
echo "Running APEX processes:"
ps -ef | grep -E 'run_api|run_service|run_paper|monitor_24h_run|apex' \
    | grep -v grep || true

echo
echo "APEX process details:"
for PID in $(pgrep -f 'scripts/run_api.py|scripts/run_service.py|scripts/run_paper.py|monitor_24h_run.py' 2>/dev/null); do
    echo "--- PID $PID ---"
    ps -o pid,ppid,user,lstart,etime,cmd -p "$PID" 2>/dev/null || true
    readlink -f "/proc/$PID/cwd" 2>/dev/null || true
    readlink -f "/proc/$PID/exe" 2>/dev/null || true
done

# ----------------------------------------------------------------------
section "04 — SYSTEMD SERVICES"
# ----------------------------------------------------------------------

for SVC in \
    apex-api.service \
    apex-24h-monitor.service \
    binance-miniapp-server.service \
    cloudflared-miniapp.service \
    apex-telegram-bot.service
do
    echo
    echo "===== $SVC ====="
    systemctl is-enabled "$SVC" 2>&1 || true
    systemctl is-active "$SVC" 2>&1 || true
    systemctl show "$SVC" \
        -p ActiveState \
        -p SubState \
        -p MainPID \
        -p ExecMainStartTimestamp \
        -p FragmentPath \
        -p Environment \
        2>&1 || true
done

# ----------------------------------------------------------------------
section "05 — API HEALTH"
# ----------------------------------------------------------------------

echo "GET /api/v1/health"
curl -sS --max-time 10 http://127.0.0.1:8765/api/v1/health 2>&1 || true
echo

# ----------------------------------------------------------------------
section "06 — API SIGNALS"
# ----------------------------------------------------------------------

echo "GET /api/v1/signals"
curl -sS --max-time 20 http://127.0.0.1:8765/api/v1/signals \
    | python3 -m json.tool 2>&1 | head -500 || true

# ----------------------------------------------------------------------
section "07 — SIGNAL FEATURE AVAILABILITY SUMMARY"
# ----------------------------------------------------------------------

"$ROOT/.venv/bin/python" - <<'PY'
import json
import urllib.request

url = "http://127.0.0.1:8765/api/v1/signals"

try:
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.loads(r.read())

    print("Signals:", len(data))

    fields = [
        "oi_expansion_pct",
        "funding_rate",
        "funding_velocity",
        "depth_imbalance",
        "directional_bias",
        "liquidation_imbalance_pct",
        "rs_percentile",
    ]

    for field in fields:
        vals = []
        available = 0
        for s in data:
            v = s.get("features", {}).get(field)
            vals.append(v)
            if v is not None:
                available += 1

        print(f"{field:30} available={available:3}/{len(data):3}")

        nonnull = [v for v in vals if v is not None]
        if nonnull:
            print("   sample:", nonnull[:5])

    print()
    print("Verdicts:")
    counts = {}
    for s in data:
        v = s.get("verdict", "UNKNOWN")
        counts[v] = counts.get(v, 0) + 1
    print(counts)

except Exception as e:
    print("SIGNAL SUMMARY ERROR:", type(e).__name__, str(e))
PY

# ----------------------------------------------------------------------
section "08 — LIVE OBSERVATION CLIENT"
# ----------------------------------------------------------------------

"$ROOT/.venv/bin/python" - <<'PY'
from apex.market.observations import MarketObservationClient
from apex.market.transport import ResilientHTTPTransport

symbols = ["BTCUSDT", "1000PEPEUSDT"]

try:
    transport = ResilientHTTPTransport(
        timeout_s=10.0,
        max_retries=1,
    )

    client = MarketObservationClient(http_transport=transport)

    print("CLIENT: OK")
    print("TRANSPORT:", type(transport).__name__)
    print("BASE URL:", getattr(client, "_base_url", "UNKNOWN"))

    for symbol in symbols:
        print()
        print("=" * 60)
        print(symbol)
        print("=" * 60)

        try:
            oi = client.fetch_open_interest_history(
                symbol,
                period="5m",
                limit=5,
            )
            print("OI:", "AVAILABLE" if oi else "UNAVAILABLE")
            if oi:
                print("  points:", len(oi))
                print("  latest:", oi[-1])
        except Exception as e:
            print("OI ERROR:", type(e).__name__, str(e))

        try:
            funding = client.fetch_funding_history(
                symbol,
                limit=5,
            )
            print("FUNDING:", "AVAILABLE" if funding else "UNAVAILABLE")
            if funding:
                print("  points:", len(funding))
                print("  latest:", funding[-1])
        except Exception as e:
            print("FUNDING ERROR:", type(e).__name__, str(e))

        try:
            depth = client.fetch_depth(
                symbol,
                limit=20,
            )
            print("DEPTH:", "AVAILABLE" if depth else "UNAVAILABLE")
            if depth:
                print("  bids:", len(depth.bids))
                print("  asks:", len(depth.asks))
                print("  mid:", depth.mid_price)
        except Exception as e:
            print("DEPTH ERROR:", type(e).__name__, str(e))

except Exception as e:
    print("CLIENT CONSTRUCTION FAILED:", type(e).__name__, str(e))
PY

# ----------------------------------------------------------------------
section "09 — OBSERVATION SOURCE IMPLEMENTATION"
# ----------------------------------------------------------------------

echo "--- observations.py: class / fetch_all ---"
grep -n -A110 -B15 \
    'class MarketObservationClient' \
    src/apex/market/observations.py 2>/dev/null | head -180 || true

echo
echo "--- fetch_all ---"
grep -n -A110 -B15 \
    'def fetch_all' \
    src/apex/market/observations.py 2>/dev/null | head -160 || true

# ----------------------------------------------------------------------
section "10 — ENGINE OBSERVATION WIRING"
# ----------------------------------------------------------------------

echo "--- constructor ---"
grep -n -A100 -B15 \
    'def __init__' \
    src/apex/runtime/engine.py 2>/dev/null | head -160 || true

echo
echo "--- advisory context ---"
grep -n -A120 -B20 \
    'def _build_advisory_context' \
    src/apex/runtime/engine.py 2>/dev/null | head -180 || true

echo
echo "--- observation references ---"
grep -nE \
    'observation_client|fetch_all|_observation_cache|TacticalObservations|TacticalContext' \
    src/apex/runtime/engine.py 2>/dev/null || true

# ----------------------------------------------------------------------
section "11 — ALL OBSERVATION CONSTRUCTION / INJECTION SITES"
# ----------------------------------------------------------------------

grep -RInE \
    'MarketObservationClient\(|observation_client=|build_observation_client' \
    scripts src \
    --exclude-dir=__pycache__ \
    --exclude='*.pyc' 2>/dev/null | head -300 || true

# ----------------------------------------------------------------------
section "12 — TRANSPORT SAFETY"
# ----------------------------------------------------------------------

grep -n -A180 -B15 \
    'class ResilientHTTPTransport' \
    src/apex/market/transport.py 2>/dev/null | head -230 || true

echo
echo "HTTP methods used by market-data code:"
grep -RInE \
    'method=.*(POST|PUT|DELETE|PATCH)|["'\''](POST|PUT|DELETE|PATCH)["'\'']' \
    src/apex/market src/apex/runtime \
    --exclude-dir=__pycache__ \
    --exclude='*.pyc' 2>/dev/null | head -100 || true

# ----------------------------------------------------------------------
section "13 — TACTICAL CONFIGURATION"
# ----------------------------------------------------------------------

grep -RInE \
    'oi_expansion_min_pct|funding_lookback|funding_max_rate|depth_band_pct|depth_imbalance_min|liquidation_window_ms|liquidation_notional_min|high_threshold|medium_threshold' \
    src/apex \
    --exclude-dir=__pycache__ \
    --exclude='*.pyc' 2>/dev/null | head -150 || true

# ----------------------------------------------------------------------
section "14 — SAFETY CHAIN"
# ----------------------------------------------------------------------

echo "Safety-chain references:"
grep -RInE \
    'RiskGuardian|OrderExecutionManager|EndpointGuard|KillSwitch|PaperExecutionAdapter' \
    src/apex/runtime src/apex/engines \
    --exclude-dir=__pycache__ \
    --exclude='*.pyc' 2>/dev/null | head -250 || true

echo
echo "Execution call references:"
grep -RInE \
    '\.execute\(|execute_order|place_order|create_order|new_order|futures_create_order' \
    src/apex scripts \
    --exclude-dir=__pycache__ \
    --exclude='*.pyc' 2>/dev/null | head -250 || true

# ----------------------------------------------------------------------
section "15 — TRADING MODE / PAPER SAFETY"
# ----------------------------------------------------------------------

echo "Systemd APEX trading mode:"
systemctl show apex-api.service -p Environment 2>/dev/null || true

echo
echo "Source trading-mode references:"
grep -RInE \
    'APEX_TRADING_MODE|trading_mode|PAPER|LIVE' \
    scripts src/apex \
    --exclude-dir=__pycache__ \
    --exclude='*.pyc' 2>/dev/null | head -200 || true

# ----------------------------------------------------------------------
section "16 — 24H RUN"
# ----------------------------------------------------------------------

echo "--- RUN_REPORT.md ---"
if [ -f "$ROOT/data/run_24h/RUN_REPORT.md" ]; then
    tail -n 120 "$ROOT/data/run_24h/RUN_REPORT.md"
else
    echo "RUN_REPORT.md NOT FOUND"
fi

echo
echo "--- latest timeseries ---"
if [ -f "$ROOT/data/run_24h/run_timeseries.jsonl" ]; then
    tail -n 10 "$ROOT/data/run_24h/run_timeseries.jsonl"
else
    echo "run_timeseries.jsonl NOT FOUND"
fi

# ----------------------------------------------------------------------
section "17 — JOURNAL / OBSERVATION TELEMETRY"
# ----------------------------------------------------------------------

grep -RniE \
    'tactical|open_interest|funding_velocity|depth_imbalance|liquidation_imbalance|relative_strength|binance_funding|binance_depth|binance_open_interest|observation' \
    data/run_24h logs data \
    --exclude='*.pyc' \
    --exclude='*.db' \
    --exclude='*.sqlite*' \
    2>/dev/null | tail -200 || true

# ----------------------------------------------------------------------
section "18 — RECENT ERRORS"
# ----------------------------------------------------------------------

echo "--- APEX API journal ---"
journalctl -u apex-api.service \
    --since "2 hours ago" \
    --no-pager 2>/dev/null \
    | grep -iE \
      'error|exception|traceback|failed|observation|tactical|timeout|429|418' \
    | tail -200 || true

echo
echo "--- 24h monitor journal ---"
journalctl -u apex-24h-monitor.service \
    --since "2 hours ago" \
    --no-pager 2>/dev/null \
    | grep -iE \
      'error|exception|traceback|failed|observation|tactical|timeout|429|418' \
    | tail -200 || true

# ----------------------------------------------------------------------
section "19 — TELEGRAM"
# ----------------------------------------------------------------------

curl -sS --max-time 10 \
    http://127.0.0.1:8765/api/v1/health \
    | python3 - <<'PY'
import sys, json
try:
    d=json.load(sys.stdin)
    print(json.dumps(d.get("telegram_alerts", {}), indent=2))
except Exception as e:
    print("Telegram health parse error:", e)
PY

# ----------------------------------------------------------------------
section "20 — PRODUCTION MINIAPP SYMLINK"
# ----------------------------------------------------------------------

echo "Mini App symlink:"
ls -la /root/binance-agent/miniapp/webapp 2>&1 || true

echo
echo "Resolved:"
readlink -f /root/binance-agent/miniapp/webapp 2>/dev/null || true

echo
echo "Expected:"
echo "/home/apex/apex/webapp"

# ----------------------------------------------------------------------
section "21 — OPEN TERMINAL UI / SEPARATE WORKTREE"
# ----------------------------------------------------------------------

if [ -d "/home/apex/apex-open-terminal-ui" ]; then
    cd /home/apex/apex-open-terminal-ui

    echo "UI branch:"
    git branch --show-current 2>&1 || true

    echo
    echo "UI commit:"
    git rev-parse --short HEAD 2>&1 || true

    echo
    echo "UI status:"
    git status --short --branch 2>&1 || true

    cd "$ROOT"
else
    echo "OpenTerminalUI worktree not found."
fi

# ----------------------------------------------------------------------
section "22 — HARDCODED / STALE VALUE CHECK"
# ----------------------------------------------------------------------

echo "Potential hardcoded runtime values in UI:"
grep -RInE \
    '10,000\.00|1,327|11\.2s|14\.4s|9\.7|paper_run_20260907_110800|1\.5R - 4\.0R' \
    webapp /home/apex/apex-open-terminal-ui 2>/dev/null \
    | head -150 || true

# ----------------------------------------------------------------------
section "23 — TEST STATUS"
# ----------------------------------------------------------------------

if [ -d tests ]; then
    echo "Pytest collection:"
    "$ROOT/.venv/bin/python" -m pytest --collect-only -q \
        tests/unit/test_phase14_observations.py 2>&1 | tail -60 || true
fi

# ----------------------------------------------------------------------
section "24 — FILE / SERVICE TIMESTAMPS"
# ----------------------------------------------------------------------

echo "Important source mtimes:"
for F in \
    src/apex/runtime/engine.py \
    src/apex/market/observations.py \
    src/apex/market/transport.py \
    scripts/run_api.py \
    scripts/run_paper.py \
    scripts/run_service.py
do
    if [ -f "$F" ]; then
        stat -c '%y  %n' "$F" 2>/dev/null || true
    fi
done

echo
echo "API service start:"
systemctl show apex-api.service \
    -p ExecMainStartTimestamp \
    -p MainPID 2>/dev/null || true

# ----------------------------------------------------------------------
section "25 — FINAL QUICK VERDICT"
# ----------------------------------------------------------------------

echo
echo "The snapshot is READ-ONLY."
echo "No services were restarted."
echo "No source files were modified."
echo "No exchange orders were created."
echo "No trading configuration was changed."

echo
echo "REPORT FILE:"
echo "$REPORT"

echo
echo "======================================================================"
echo " END APEX DIAGNOSTIC SNAPSHOT"
echo "======================================================================"

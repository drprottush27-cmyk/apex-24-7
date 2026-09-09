#!/usr/bin/env bash
set -u

cd /home/apex/apex

echo "============================================================"
echo "APEX OBSERVATION WIRING DEBUG"
echo "============================================================"
date -Is

echo
echo "=== 1. RUNNING API PROCESS ==="
pgrep -af 'scripts/run_api.py' || true

echo
echo "=== 2. API HEALTH ==="
curl -sS --max-time 10 http://127.0.0.1:8765/api/v1/health || true
echo

echo
echo "=== 3. API SIGNALS ==="
curl -sS --max-time 15 http://127.0.0.1:8765/api/v1/signals || true
echo

echo
echo "=== 4. OBSERVATION CLIENT CONSTRUCTION ==="
grep -RIn \
  --exclude-dir=.git \
  --exclude-dir=.venv \
  -E 'MarketObservationClient|observation_client|allow_live_observations' \
  src scripts/run_api.py scripts/run_service.py \
  2>/dev/null | head -120

echo
echo "=== 5. ENGINE ADVISORY CONTEXT ==="
grep -n -A100 -B15 \
  'def _build_advisory_context' \
  src/apex/runtime/engine.py \
  2>/dev/null | head -140

echo
echo "=== 6. ENGINE OBSERVATION FETCH ==="
grep -n -A45 -B15 \
  'fetch_all(' \
  src/apex/runtime/engine.py \
  2>/dev/null | head -100

echo
echo "=== 7. OBSERVATION RESULT ==="
grep -n -A80 -B15 \
  'class ObservationResult' \
  src/apex/market/observations.py \
  2>/dev/null | head -110

echo
echo "=== 8. FETCH_ALL IMPLEMENTATION ==="
grep -n -A100 -B10 \
  'def fetch_all' \
  src/apex/market/observations.py \
  2>/dev/null | head -130

echo
echo "=== 9. TACTICAL CONTEXT CREATION ==="
grep -RIn \
  --exclude-dir=.git \
  --exclude-dir=.venv \
  -E 'TacticalContext|TacticalObservations|oi_history|funding_history|depth=' \
  src/apex/runtime src/apex/engines \
  2>/dev/null | head -160

echo
echo "=== 10. SIGNAL SERIALIZATION ==="
grep -RIn \
  --exclude-dir=.git \
  --exclude-dir=.venv \
  -E 'oi_expansion_pct|funding_rate|funding_velocity|depth_imbalance|liquidation_imbalance_pct' \
  src \
  2>/dev/null | head -160

echo
echo "=== 11. LIVE OBSERVATION PROOF ==="
./.venv/bin/python - <<'PY'
from apex.market.observations import MarketObservationClient
from apex.market.transport import ResilientHTTPTransport

transport = ResilientHTTPTransport(timeout_s=10.0, max_retries=1)
client = MarketObservationClient(http_transport=transport)

for symbol in ("BTCUSDT", "1000PEPEUSDT"):
    print(f"\n--- {symbol} ---")

    result = client.fetch_all(
        symbol,
        oi_period="5m",
        oi_limit=5,
        funding_limit=5,
        depth_limit=20,
    )

    print("OI:", "AVAILABLE" if result.oi_history else "NONE")
    if result.oi_history:
        print("OI_POINTS:", len(result.oi_history))
        print("OI_LATEST:", result.oi_history[-1])

    print("FUNDING:", "AVAILABLE" if result.funding_history else "NONE")
    if result.funding_history:
        print("FUNDING_POINTS:", len(result.funding_history))
        print("FUNDING_LATEST:", result.funding_history[-1])

    print("DEPTH:", "AVAILABLE" if result.depth else "NONE")
    if result.depth:
        print("DEPTH_BIDS:", len(result.depth.bids))
        print("DEPTH_ASKS:", len(result.depth.asks))
        print("DEPTH_MID:", result.depth.mid_price)

    print("LIQUIDATIONS:",
          "AVAILABLE" if result.liquidations else "NONE")
PY

echo
echo "=== 12. GIT STATE ==="
git branch --show-current
git rev-parse --short HEAD
git status --short

echo
echo "============================================================"
echo "END OBSERVATION DEBUG"
echo "============================================================"

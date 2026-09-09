#!/usr/bin/env bash
# APEX Recovery Script — Read-Only Session Recovery
# This script does NOT modify source code, commit changes, push, enable
# trading, start live execution, approve AI changes, or start an AI agent.
# It is purely informational and recovery-oriented.

set -euo pipefail

PROJECT_DIR="/home/apex/apex"
cd "$PROJECT_DIR"

echo "============================================"
echo "  APEX 24/7 — Recovery Session Status"
echo "============================================"
echo ""

echo "--- Git Branch ---"
git branch --show-current
echo ""

echo "--- Git HEAD ---"
git log --oneline -1
echo ""

echo "--- Working Tree Status ---"
git status --short --branch
echo ""

echo "--- Uncommitted Changes ---"
UNCOMMITTED=$(git status --porcelain)
if [ -z "$UNCOMMITTED" ]; then
    echo "None (working tree clean)"
else
    echo "$UNCOMMITTED"
fi
echo ""

echo "--- .ai/ Project State ---"
if [ -f .ai/PROJECT_STATE.md ]; then
    grep -E "^## Current task|^## Current Git|^## Test status|^Current task:" .ai/PROJECT_STATE.md || true
else
    echo "NOT FOUND"
fi
echo ""

echo "--- .ai/ Session State ---"
if [ -f .ai/SESSION_STATE.md ]; then
    grep -E "^Project:|^Current task:|^Phase:|^Status:|^Next action:|^Last verified commit:|^Last test result:|^Interruption status:|^Safety status:" .ai/SESSION_STATE.md || true
else
    echo "NOT FOUND"
fi
echo ""

echo "--- Safety Defaults ---"
grep -nE "TRADING_MODE|LIVE_TRADING_ENABLED|DRY_RUN" core/config/settings.py || true
echo ""

echo "--- Risk Guardian ---"
if grep -q "def evaluate_order" engines/risk/guardian.py 2>/dev/null; then
    echo "Risk Guardian: PRESENT (evaluate_order found)"
else
    echo "Risk Guardian: NOT FOUND"
fi
echo ""

echo "--- Git Remote ---"
REMOTE=$(git remote -v)
if [ -z "$REMOTE" ]; then
    echo "None (correct — no remote configured)"
else
    echo "$REMOTE"
fi
echo ""

echo "--- tmux Sessions ---"
tmux ls 2>/dev/null || echo "No tmux sessions running"
echo ""

echo "--- OpenCode Data ---"
if [ -d ~/.local/share/opencode ]; then
    echo "Location: ~/.local/share/opencode/"
    ls -lh ~/.local/share/opencode/ 2>/dev/null || true
else
    echo "NOT FOUND"
fi
echo ""

echo "--- To Resume Work ---"
echo ""
echo "  After SSH disconnect:"
echo "    tmux attach -t apex-opencode"
echo ""
echo "  After VPS reboot:"
echo "    cd $PROJECT_DIR"
echo "    bash scripts/recover_ai_session.sh"
echo ""
echo "  To start AI coding session:"
echo "    cd $PROJECT_DIR"
echo "    tmux new -s apex-opencode"
echo "    # then launch opencode inside tmux"
echo ""
echo "============================================"
echo "  Recovery check complete — no changes made"
echo "============================================"

#!/usr/bin/env bash
set -euo pipefail

echo "=========================================================="
echo " APEX DESK: SYSTEM-WIDE ORCHESTRATED RESTART"
echo "=========================================================="

sudo systemctl restart docker
sleep 2

echo "• Restarting Core Trading Engine..."
sudo systemctl restart apex.service

echo "• Restarting Telegram Control Plane..."
sudo systemctl restart apex-bot.service

echo "• Restarting Cloudflare Public Tunnel..."
sudo systemctl restart apex-tunnel.service

echo "• Checking Daily Report Timer..."
sudo systemctl restart apex-report.timer

sleep 4

echo "=========================================================="
echo " ACTIVE DAEMON STATES"
echo "=========================================================="
sudo systemctl status apex.service apex-bot.service apex-tunnel.service --no-pager

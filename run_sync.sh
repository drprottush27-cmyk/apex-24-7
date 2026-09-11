#!/bin/bash
cd /srv/apex
source .venv/bin/activate
# Added --paper to force testnet extraction
python main.py --since "2026-09-01" --paper >> /var/log/aegis_sync.log 2>&1

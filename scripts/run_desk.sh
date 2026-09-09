#!/usr/bin/env bash
set -e
cd /home/apex/apex
source .venv/bin/activate
export PYTHONPATH=.
exec python3 main.py

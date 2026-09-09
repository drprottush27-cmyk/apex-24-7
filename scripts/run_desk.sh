#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
fi

export PYTHONPATH=src:${PYTHONPATH}
exec python3 scripts/run_service.py "$@"

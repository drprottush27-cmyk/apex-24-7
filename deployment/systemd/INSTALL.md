# APEX 24/7 — systemd installation

This directory ships a production-oriented systemd unit for the APEX control
plane: `apex.service`.

## Architecture: one unit, not many

APEX runs as a **single long-running process**:

- `scripts/run_service.py --api-port 8000` launches the continuous autonomous
  PAPER trading engine and localhost inspection API server.
- The runtime lifecycle coordinates the `ScanScheduler`, `FullMarketScanner`,
  `OrderExecutionManager`, and emergency kill switch in a deterministic loop.
- Persistence is maintained via SQLite WAL journal (`src/apex/persistence/journal.py`).
- PostgreSQL and Redis are available for optional enterprise analytics storage.

There are **no** separate worker/dashboard/scanner daemons to run (`apps/` is
stubbed). A single `.service` unit is therefore the correct deployment; no
socket, target, or timer is needed.

## Files

| Path | Purpose |
|------|---------|
| `apex.service` | Hardened systemd unit for the APEX control plane |
| `INSTALL.md`   | This document |

## Prerequisites

- A non-root user `apex` that owns `/home/apex/apex`.
- A Python 3.11+ virtualenv at `/home/apex/apex/.venv` with the project
  dependencies installed (`requirements.txt`).
- PostgreSQL and Redis reachable on `127.0.0.1` (e.g. the Docker services in
  `docker-compose.yml`).
- Application configuration at `/home/apex/apex/.env` (gitignored). The app
  reads this file itself at startup; **systemd never holds the secrets**.

## Installing

```bash
sudo cp /home/apex/apex/deployment/systemd/apex.service /etc/systemd/system/apex.service
sudo systemctl daemon-reload
sudo systemctl enable apex.service
sudo systemctl start apex.service
```

To follow shutdown of the old, unhardened root-level `apex.service`, stop and
disable it first:

```bash
sudo systemctl stop apex.service
sudo systemctl disable apex.service
```

## Managing

```bash
sudo systemctl status apex.service
sudo systemctl restart apex.service
sudo systemctl stop apex.service
journalctl -u apex.service -f
```

## Configuration & secrets

The unit contains **no** `Environment=` directives and **no** `EnvironmentFile=`.
Secrets and runtime configuration live in `/home/apex/apex/.env`, which the
application loads itself from its `WorkingDirectory`. Keep `.env` mode-restricted
(e.g. `chmod 600`, owned by `apex`).

Safe defaults are enforced by the application and are **not** duplicated in the
unit:

- `DRY_RUN` trading mode by default
- `LIVE_TRADING_ENABLED=false`
- `AUTO_EXECUTE=false`
- Deterministic Risk Guardian retains execution veto
- Emergency kill switch remains authoritative

## Graceful shutdown & SIGTERM

`Type=simple` sends `SIGTERM` on `systemctl stop`. uvicorn handles the signal by
running the FastAPI lifespan teardown, which invokes `GracefulShutdown`:

1. Engages the emergency kill switch (immediate, fail-closed execution halt).
2. Stops the data feed.
3. Stops the trading orchestrator.
4. Writes a CRITICAL audit event.

`TimeoutStopSec=30` bounds this sequence; if it exceeds 30s systemd escalates to
`SIGKILL`. Because the kill switch engages first, any forced termination is safe
(fail closed).

## Hardening

The unit applies least-privilege hardening compatible with the application's
networking and CPython runtime:

- Runs as non-root `apex`, `CapabilityBoundingSet=` (empty)
- `ProtectSystem=strict`, `RestrictSUIDSGID`, `NoNewPrivileges`
- `PrivateTmp`, `ProtectKernel*`, `ProtectControlGroups`, `ProtectClock`
- `RestrictAddressFamilies`, `RestrictRealtime`, `LockPersonality`
- `SystemCallArchitectures=native`, `UMask=0077`

Options that would break CPython/network (e.g. `MemoryDenyWriteExecute`,
`PrivateNetwork`, `ProtectHome`) are deliberately omitted.

## Safety notes

- This unit does **not** enable live trading, create credentials, or run any
  destructive/migration database operation at startup.
- If configuration is absent or invalid the application itself fails closed;
  the unit's `ExecStartPre` guards also refuse to start the process when the
  venv binary or `.env` is missing.

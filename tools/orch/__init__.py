"""APEX Autonomous Engineering Orchestrator.

Deterministic, persistent orchestration layer that coordinates the APEX
engineering agents (PLANNER, BUILDER, TESTER, SECURITY_REVIEWER, FINAL_REVIEWER)
as a sequential pipeline with bounded retries, explicit state transitions, a
single-builder lock, provider abstraction, and durable recovery after any
process/prov`/session interruption.

This module never:
  - enables live trading
  - creates exchange credentials
  - stores API keys
  - pushes to a remote
  - connects to an unknown remote
  - bypasses Risk Guardian or any safety control
"""
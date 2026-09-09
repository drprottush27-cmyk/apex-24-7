#!/usr/bin/env python3
"""APEX Autonomous Engineering Orchestrator CLI.

Usage:
  python -m orch.cli status
  python -m orch.cli run
  python -m orch.cli advance
  python -m orch.cli recover
  python -m orch.cli pause
  python -m orch.cli resume
  python -m orch.cli provider [name]    (show/set preferred provider, optional)

Safe-by-construction: this CLI never enables trading, never creates
credentials, and never pushes/connects to a remote.
"""

from __future__ import annotations

import json
import sys

from .orchestrator import Orchestrator


def _print(obj) -> None:
    if hasattr(obj, "as_dict"):
        obj = obj.as_dict()
    print(json.dumps(obj, indent=2, default=str))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    orch = Orchestrator()

    if not argv:
        print(__doc__)
        return 0

    cmd = argv[0].lower()

    if cmd == "status":
        _print(orch.status())
    elif cmd == "run":
        _print(orch.run())
    elif cmd == "advance":
        _print(orch.advance_to_next_queue_task())
    elif cmd == "recover":
        _print(orch.recover())
    elif cmd == "pause":
        _print(orch.pause())
    elif cmd == "resume":
        _print(orch.resume())
    elif cmd == "provider":
        if len(argv) > 1:
            orch.set_provider(argv[1])
            _print(orch.status())
        else:
            _print(orch.status())
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
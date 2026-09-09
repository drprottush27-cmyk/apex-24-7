"""Git operations for the APEX engineering orchestrator.

These helpers wrap the local git binary for READ and COMMIT operations only.
The orchestrator may create local commits ONLY after every gate passes. It
NEVER pushes, NEVER adds/connects a remote, and NEVER syncs remotely.

Unknown-remote and dirty-tree detection fail closed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent

_ALLOWED_REMOTE_HOSTS = {"github.com", "gitlab.com", "bitbucket.org"}


class GitOpsError(Exception):
    """Raised when git state is unsafe for the current gate."""


class GitOps:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root

    def _run(self, args: List[str], check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            check=check,
            timeout=60,
        )

    # -- read-only ------------------------------------------------------
    def status_short(self) -> str:
        proc = self._run(["status", "--short"])
        return proc.stdout.strip()

    def is_clean(self) -> bool:
        return self.status_short() == ""

    def head(self) -> str:
        proc = self._run(["rev-parse", "--short", "HEAD"])
        return proc.stdout.strip()

    def branch(self) -> str:
        proc = self._run(["rev-parse", "--abbrev-ref", "HEAD"])
        return proc.stdout.strip()

    def diff(self) -> str:
        proc = self._run(["diff"], check=False)
        return proc.stdout

    def cached_diff(self) -> str:
        proc = self._run(["diff", "--cached"], check=False)
        return proc.stdout

    def diff_check(self) -> str:
        """Return output of ``git diff --check`` (non-zero is a whitespace gate fail)."""
        proc = self._run(["diff", "--check"], check=False)
        return proc.stdout

    def untracked(self) -> List[str]:
        proc = self._run(["ls-files", "--others", "--exclude-standard"])
        return [l for l in proc.stdout.splitlines() if l.strip()]

    def remotes(self) -> List[str]:
        proc = self._run(["remote"], check=False)
        return [l for l in proc.stdout.splitlines() if l.strip()]

    def remote_url(self, name: str) -> str:
        proc = self._run(["remote", "get-url", name], check=False)
        return proc.stdout.strip()

    def diff_names(self) -> List[str]:
        proc = self._run(["diff", "--name-only"], check=False)
        return [l for l in proc.stdout.splitlines() if l.strip()]

    def changed_files(self) -> List[str]:
        """All files changed in the working tree (tracked modifications +
        untracked, non-ignored files). Empty list == clean tree."""
        names = set(self.diff_names())
        names.update(self.untracked())
        # Also include staged changes (git diff --cached --name-only).
        proc = self._run(["diff", "--cached", "--name-only"], check=False)
        names.update(l for l in proc.stdout.splitlines() if l.strip())
        return sorted(names)

    # -- gates ----------------------------------------------------------
    def assert_clean_required(self) -> None:
        """The working tree must be clean unless the current stage is a build stage."""
        if not self.is_clean():
            raise GitOpsError("dirty_working_tree_rejected")

    def assert_no_unknown_remote(self) -> None:
        """Fail closed if any remote is unknown or cannot be verified.

        Only verified https remotes against known hosts are tolerated. Any
        unverifiable remote (SSH, unknown host, no URL) is treated as unsafe.
        The APEX repo is expected to have NO remote by default.
        """
        remotes = self.remotes()
        for name in remotes:
            url = self.remote_url(name)
            if not url:
                raise GitOpsError(f"remote {name} has no resolvable URL")
            if not url.startswith("https://"):
                raise GitOpsError(f"unknown_remote_protocol {name} -> {url}")
            host = _host_of(url)
            if host not in _ALLOWED_REMOTE_HOSTS:
                raise GitOpsError(f"unknown_remote_detected {name} -> {url}")

    def assert_no_push(self) -> None:
        """Structurally never push: this method only verifies policy, doesn't push."""

    # -- mutation (commit only) ------------------------------------------
    def stage_and_commit(self, message: str, *, author_name: str = "Apex Operator",
                         author_email: str = "apex@localhost") -> str:
        """Stage all changes and create a clean, single-task local commit."""
        proc = self._run(["add", "-A"])
        if proc.returncode != 0:
            raise GitOpsError(f"git_add_failed {proc.stderr[:200]}")
        env_commit = _commit_env(author_name, author_email)
        proc = self._run(
            ["commit", "-m", message, "--no-allow-empty"],
            check=False,
        )
        if proc.returncode != 0:
            if "nothing to commit" in proc.stdout + proc.stderr:
                raise GitOpsError("nothing_to_commit")
            raise GitOpsError(f"git_commit_failed {proc.stderr[:200]}")
        return self.head()
        # NOTE: no push, no remote add, no remote sync — by construction.


def _host_of(url: str) -> str:
    m = re.search(r"@([^:/]+)[:/]", url)
    if m:
        return m.group(1)
    m = re.search(r"://([^/:]+)", url)
    if m:
        return m.group(1)
    return ""


def _commit_env(author_name: str, author_email: str):
    return {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": author_name,
        "GIT_COMMITTER_EMAIL": author_email,
    }
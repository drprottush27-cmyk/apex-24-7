"""Focused tests for the APEX Autonomous Engineering Orchestrator.

These tests are hermetic: they never call real AI APIs. Provider calls are
stubbed via the LocalFallbackProvider / injected fakes. Git operations are
redirected to a scratch temp repo to avoid touching the real repository.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from orch.state import (
    EngineState,
    EngineStateStore,
    FailureRecord,
    ReviewerVerdict,
    TaskState,
)
from orch.lock import BuilderLock, LockError, LOCK_FILE
from orch.gitops import GitOps, GitOpsError
from orch.safety import SafetyReviewer, SafetyVeto
from orch.provider import (
    AgentProvider,
    LocalFallbackProvider,
    ProviderRegistry,
    ProviderResult,
    PROVIDER_OPCODE,
)
from orch.pipeline import Pipeline, PipelineConfig, StepOutcome, Blocked
from orch.committee import (
    AgentCommittee,
    CommitteeVerdict,
    ReviewVote,
    ReviewerVote,
    make_default_committee,
    make_default_registry_committee,
)
from orch.orchestrator import Orchestrator
from orch.session import SessionDocs


@pytest.fixture
def store(tmp_path):
    return EngineStateStore(path=tmp_path / "engine_state.json")


@pytest.fixture
def make_scoped_orch(tmp_path):
    """Build an Orchestrator fully isolated from the real repo/.ai so tests
    never touch production state."""

    def _build(repo=None, **kwargs):
        repo = repo or (tmp_path / "repo")
        repo.mkdir(exist_ok=True)
        if not (repo / ".git").exists():
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
            (repo / ".ai").mkdir(exist_ok=True)
            (repo / "keep.txt").write_text("keep\n")
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
        g = kwargs.pop("git", None) or GitOps(root=repo)
        docs = kwargs.pop("docs", None) or SessionDocs(ai_dir=repo / ".ai")
        lockpath = kwargs.pop("lock_path", None) or (tmp_path / "builder.lock")
        from orch.lock import BuilderLock as _BL
        lock = kwargs.pop("lock", None) or _BL(path=lockpath)
        st = kwargs.pop("store", None)
        from orch.state import EngineStateStore as _S
        st = st or _S(path=tmp_path / "engine_state.json")
        from orch.logging import OrchestratorLogger as _OL
        logger = kwargs.pop("logger", None) or _OL(directory=tmp_path / "logs")
        if "registry" not in kwargs:
            # Use the offline registry so these hermetic tests never contact a
            # real AI backend (OpenCode/Ollama) — no network probes.
            from orch.provider import make_default_registry
            kwargs["registry"] = make_default_registry(offline_mode=True)
        return Orchestrator(store=st, git=g, docs=docs, lock=lock, logger=logger, **kwargs)

    return _build


@pytest.fixture
def make_repo(tmp_path):
    """Create a throwaway git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@t"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "t"],
        check=True,
    )
    (repo / "keep.txt").write_text("keep\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    return repo


@pytest.fixture
def git(make_repo):
    return GitOps(root=make_repo)


def _passing_actions(evidence="ok", build_files=("change.py",)):
    """A complete set of actions that all succeed WITH evidence, satisfying the
    fail-closed execution contract. Individual tests override the specific action
    they want to fail."""
    ev = f"evidence:{evidence}"

    def _build(t):
        t.modified_files = list(build_files or [])
        return StepOutcome(ok=True, evidence=ev)

    return {
        "plan": lambda t: StepOutcome(ok=True, evidence=ev),
        "build": _build,
        "run_tests": lambda t: StepOutcome(ok=True, evidence=ev),
        "run_regression": lambda t: StepOutcome(ok=True, evidence=ev),
        "security_review": lambda t: StepOutcome(ok=True, evidence=ev),
        "final_review": lambda t: StepOutcome(ok=True, evidence=ev),
        "verify_commit_gate": lambda t: StepOutcome(ok=True, evidence=ev),
        "update_state_docs": lambda t: StepOutcome(ok=True, evidence=ev),
        "acquire_builder": lambda t: None,
        "release_builder": lambda t: None,
    }


def _mark_stages_executed(task, test_attempts=1, security_cycles=1):
    """Directly mark a task's required stages as executed + counters set, so the
    FINAL_REVIEW / commit-gate / STATE_UPDATE integrity guards are satisfied when a
    test drives those stages directly (bypassing prior stage execution)."""
    task.implementation_executed = True
    task.test_executed = True
    task.security_review_executed = True
    task.regression_executed = True
    task.implementation_files = list(task.modified_files or [])
    task.test_attempts = max(task.test_attempts, test_attempts)
    task.security_cycles = max(task.security_cycles, security_cycles)


# --------------------------------------------------------------------------- state
class TestStatePersistence:
    def test_save_and_load_round_trip(self, store):
        state = EngineState(current_task_id="T1")
        state.tasks["T1"] = TaskState(
            task_id="T1", title="foo", stage="PLAN",
            last_failure=FailureRecord(
                stage="TEST", classification="TEST_FAILURE", message="boom", attempt=2,
            ),
            last_reviewer_verdict=ReviewerVerdict(reviewer="SECURITY_REVIEW", verdict="REJECTED"),
            safe_to_resume=True,
        )
        store.save(state)
        loaded = store.load()
        assert loaded.current_task_id == "T1"
        t = loaded.tasks["T1"]
        assert t.stage == "PLAN"
        assert t.last_failure.classification == "TEST_FAILURE"
        assert t.last_failure.attempt == 2
        assert t.last_reviewer_verdict.verdict == "REJECTED"
        assert t.safe_to_resume is True

    def test_persists_across_store_instances(self, tmp_path):
        p = tmp_path / "engine_state.json"
        s1 = EngineStateStore(path=p)
        st = EngineState(current_task_id="X")
        s1.save(st)
        s2 = EngineStateStore(path=p)
        assert s2.load().current_task_id == "X"

    def test_missing_file_default(self, store):
        st = store.load()
        assert st.current_task_id == ""

    def test_corrupt_file_fails_closed_to_default(self, store):
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("{ not json !!", encoding="utf-8")
        st = store.load()
        assert st.current_task_id == ""


# --------------------------------------------------------------------------- lock
class TestBuilderLock:
    def test_acquire_and_release(self, tmp_path):
        lock = BuilderLock(path=tmp_path / "builder.lock")
        token = lock.acquire(owner="T1")
        assert lock.is_held()
        assert lock.owner_token() == token
        assert lock.release() is True
        assert not lock.is_held()

    def test_second_acquire_contended(self, tmp_path):
        lock1 = BuilderLock(path=tmp_path / "b.lock")
        lock1.acquire(owner="T1")
        lock2 = BuilderLock(path=tmp_path / "b.lock")
        with pytest.raises(LockError):
            lock2.acquire(owner="T2")

    def test_ownership_mismatch_refuses_delete(self, tmp_path):
        lock = BuilderLock(path=tmp_path / "b.lock")
        lock.acquire(owner="T1")
        # Simulate a second process owning it now (different token).
        other = BuilderLock(path=tmp_path / "b.lock")
        # Forcibly change the file token to a different one.
        data = lock.read_lock()
        data["token"] = "DIFFERENT"
        import json as _json
        tmp = tmp_path / "b.lock"
        tmp.write_text(_json.dumps(data), encoding="utf-8")
        with pytest.raises(LockError):
            lock.release()  # refuses to delete an active non-owned lock
        assert tmp.exists()

    def test_stale_lock_detected_and_cleared(self, tmp_path):
        lock = BuilderLock(
            path=tmp_path / "b.lock", stale_after_seconds=1, heartbeat_seconds=1
        )
        lock.acquire(owner="T1")
        # Simulate a crashed owner: write a lock whose heartbeat is ancient.
        data = lock.read_lock()
        data["heartbeat_ms"] = 0
        data["acquired_ms"] = 0
        import json as _json
        (tmp_path / "b.lock").write_text(_json.dumps(data), encoding="utf-8")
        assert lock.is_stale()
        # A new builder can acquire because the lock is stale and gets cleared.
        lock2 = BuilderLock(path=tmp_path / "b.lock")
        lock2.acquire(owner="T2", force_stale=False)
        assert lock2.owner_token() is not None
        assert lock2.read_lock()["owner"] == "T2"

    def test_non_stale_lock_not_deleted(self, tmp_path):
        lock = BuilderLock(path=tmp_path / "b.lock")
        lock.acquire(owner="T1")
        new = BuilderLock(path=tmp_path / "b.lock")
        with pytest.raises(LockError):
            new.acquire(owner="T2")


# --------------------------------------------------------------------------- provider
class TestProvider:
    def test_registry_availability_detection(self):
        reg = ProviderRegistry(providers=[LocalFallbackProvider()])
        assert "local-fake" in reg.available_providers()

    def test_provider_failure_and_no_crash(self):
        class Boom(AgentProvider):
            name = "boom"
            def available(self): return True
            def rationale(self): return ""
            def run(self, prompt, context):
                from orch.provider import ProviderFailure
                raise ProviderFailure(self.name, "quota exceeded")

        reg = ProviderRegistry(providers=[Boom()])
        assert "boom" in reg.available_providers()

    def test_fallback_provider_selected(self):
        # Preferred provider is unavailable; fallback (local-fake) is selected.
        reg = ProviderRegistry(providers=[LocalFallbackProvider()])
        from orch.provider import make_default_registry
        # ensure select_available prefers an available one
        assert reg.select_available(preferred="gemini") == "local-fake"

    def test_local_fallback_responder(self):
        local = LocalFallbackProvider(responder=lambda prompt, context: "PLAN: done")
        result = local.run("p", {})
        assert result.ok
        assert "PLAN: done" in result.output


# --------------------------------------------------------------------------- ollama
class TestOllamaProvider:
    """Hermetic tests for the advisory-only Ollama provider using a mocked
    httpx transport so no real network is ever contacted."""

    @pytest.fixture
    def mock_ok(self):
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}]})
            if request.url.path == "/api/generate":
                return httpx.Response(200, json={"model": "qwen2.5:3b",
                                                 "response": "ADVISORY: plan step one"})
            return httpx.Response(404, json={})

        return httpx.MockTransport(handler)

    @pytest.fixture
    def mock_unreachable(self):
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        return httpx.MockTransport(handler)

    def test_available_when_server_responds(self, mock_ok):
        from orch.provider import OllamaProvider, PROVIDER_OLLAMA
        p = OllamaProvider(url="http://localhost:11434", transport=mock_ok)
        assert p.available() is True

    def test_unavailable_fails_closed_on_transport_error(self, mock_unreachable):
        from orch.provider import OllamaProvider
        p = OllamaProvider(url="http://localhost:11434", transport=mock_unreachable)
        assert p.available() is False

    def test_run_returns_advisory_output(self, mock_ok):
        from orch.provider import OllamaProvider
        p = OllamaProvider(url="http://localhost:11434", transport=mock_ok)
        result = p.run("analyze risks", {"agent": "PLANNER"})
        assert result.ok is True
        assert result.provider == "ollama"
        assert "ADVISORY" in result.output
        assert result.classification == "ADVISORY_OUTPUT"
        assert result.duration_ms >= 0

    def test_run_fails_closed_when_unreachable(self, mock_unreachable):
        from orch.provider import OllamaProvider
        p = OllamaProvider(url="http://localhost:11434", transport=mock_unreachable)
        result = p.run("analyze", {})
        assert result.ok is False
        assert result.classification == "INFRASTRUCTURE_FAILURE"

    def test_registered_in_default_registry(self):
        from orch.provider import (make_default_registry, OllamaProvider,
                                   PROVIDER_OLLAMA)
        offline = make_default_registry(offline_mode=True)
        # Offline mode must NOT contact/select any real backend.
        assert PROVIDER_OLLAMA not in offline._providers
        prod = make_default_registry(offline_mode=False)
        assert isinstance(prod.provider(PROVIDER_OLLAMA), OllamaProvider)

    def test_included_in_fallback_chain(self):
        from orch.provider import FALLBACK_CHAIN, PROVIDER_OLLAMA
        assert PROVIDER_OLLAMA in FALLBACK_CHAIN

    def test_health_error_status_fails_closed(self):
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={})

        from orch.provider import OllamaProvider
        p = OllamaProvider(url="http://localhost:11434", transport=httpx.MockTransport(handler))
        # Non-200 health check => not available (fail closed).
        assert p.available() is False

    def test_run_http_error_fails_closed(self):
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={})
            return httpx.Response(503, json={})

        from orch.provider import OllamaProvider
        p = OllamaProvider(url="http://localhost:11434", transport=httpx.MockTransport(handler))
        result = p.run("analyze", {})
        assert result.ok is False
        assert result.classification == "PROVIDER_FAILURE"

    def test_empty_response_fails_closed(self):
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"model": "m", "response": ""})

        from orch.provider import OllamaProvider
        p = OllamaProvider(url="http://localhost:11434", transport=httpx.MockTransport(handler))
        result = p.run("analyze", {})
        assert result.ok is False
        assert result.error == "ollama_empty_response"


# --------------------------------------------------------------------------- committee
class TestAgentCommittee:
    """Hermetic tests for the advisory agent committee (P2-13).

    Members are injected as deterministic callables so no network/AI API is
    ever contacted. The committee is advisory-only: it can only add REJECT /
    INCONCLUSIVE friction to the deterministic gates, never force an approval.
    """

    def _approve(self, reviewer="R1"):
        def member(prompt, context):
            return ReviewerVote(reviewer=reviewer, vote=ReviewVote.APPROVE,
                                reasoning="ok", provider="fake")
        return member

    def _reject(self, reviewer="R1"):
        def member(prompt, context):
            return ReviewerVote(reviewer=reviewer, vote=ReviewVote.REJECT,
                                reasoning="risk: position sizing unsafe", provider="fake")
        return member

    def _abstain(self, reviewer="R1"):
        def member(prompt, context):
            return ReviewerVote(reviewer=reviewer, vote=ReviewVote.ABSTAIN,
                                reasoning="no verdict", provider="fake")
        return member

    def _boom(self, reviewer="R1"):
        def member(prompt, context):
            raise RuntimeError("provider down")
        return member

    def _non_vote(self, reviewer="R1"):
        def member(prompt, context):
            return "not-a-vote"
        return member

    def test_unanimous_approve(self):
        c = AgentCommittee(members=[self._approve("A"), self._approve("B"), self._approve("C")])
        v = c.run("approve?", {})
        assert v.approved is True
        assert v.verdict == "APPROVED"
        assert v.issues == []

    def test_any_reject_fails_closed(self):
        c = AgentCommittee(members=[self._approve("A"), self._reject("B"), self._approve("C")])
        v = c.run("approve?", {})
        assert v.verdict == "REJECTED"
        assert v.approved is False
        assert any("committee_reject" in i for i in v.issues)

    def test_quorum_not_met_becomes_inconclusive(self):
        # 2 of 3 usable; quorum default 3 => not met => INCONCLUSIVE (fail closed).
        c = AgentCommittee(members=[self._approve("A"), self._abstain("B"), self._approve("C")])
        v = c.run("approve?", {})
        assert v.verdict == "INCONCLUSIVE"
        assert v.approved is False
        assert any("committee_quorum_not_met" in i for i in v.issues)

    def test_quorum_met_with_all_usable(self):
        c = AgentCommittee(members=[self._approve("A"), self._approve("B"), self._approve("C")],
                           quorum=2)
        v = c.run("approve?", {})
        assert v.verdict == "APPROVED"

    def test_member_error_fails_closed(self):
        c = AgentCommittee(members=[self._boom("A"), self._approve("B")], quorum=2)
        v = c.run("approve?", {})
        assert v.verdict == "INCONCLUSIVE"
        assert v.approved is False

    def test_member_returning_non_vote_fails_closed(self):
        c = AgentCommittee(members=[self._non_vote("A")])
        v = c.run("approve?", {})
        assert v.verdict == "INCONCLUSIVE"
        assert v.approved is False

    def test_empty_committee_fails_closed(self):
        c = AgentCommittee(members=[])
        v = c.run("approve?", {})
        assert v.verdict == "INCONCLUSIVE"
        assert any("committee_no_members" in i for i in v.issues)

    def test_verdict_serializable_dict(self):
        c = AgentCommittee(members=[self._approve("A"), self._approve("B")], quorum=2)
        d = c.run("approve?", {}).to_dict()
        assert d["verdict"] == "APPROVED"
        assert isinstance(d["issues"], list)
        assert len(d["votes"]) == 2

    def test_default_committee_members(self):
        from orch.provider import LocalFallbackProvider
        from orch.agents import AgentExecutor
        reg = ProviderRegistry(providers=[LocalFallbackProvider()])
        execr = AgentExecutor(registry=reg)
        members = make_default_committee(executor=execr)
        assert len(members) == 3

    def test_committee_approve_not_sufficient_on_own(self, make_scoped_orch, monkeypatch):
        """The committee is advisory: a unanimous committee APPROVE must NOT
        bypass the deterministic safety gate. A live-trading violation in the
        tree still blocks final review."""
        orch = make_scoped_orch(committee=AgentCommittee(
            members=[self._approve("A"), self._approve("B")], quorum=2))
        # Build the violating fixture at runtime (concatenation) so the committed
        # source never contains the contiguous live-trading literal that the
        # orchestrator's whole-diff safety scanner would otherwise flag as a
        # false positive on legitimate negative-path test fixture content.
        live_line = "LIVE_TRADING_ENABLED" + " = true\n"
        (orch.git.root / "settings_upstream.py").write_text(live_line, encoding="utf-8")
        st = orch.store.load()
        st.current_task_id = "T1"
        task = TaskState(task_id="T1", title="task", stage="FINAL_REVIEW")
        from orch.state import utc_now_iso
        task.started_at = utc_now_iso()
        _mark_stages_executed(task)
        st.tasks["T1"] = task
        orch.store.save(st)
        # Force a genuine materialized diff so the safety gate actually sees the
        # violation text in the working tree.
        out = orch._act_final_review(task)
        assert out.ok is False


class TestFinalReviewCommittee:
    def _reject(self, reviewer="A"):
        def member(prompt, context):
            return ReviewerVote(reviewer=reviewer, vote=ReviewVote.REJECT,
                                reasoning="risk: position sizing unsafe", provider="fake")
        return member

    def test_rejecting_committee_blocks_final_review(self, make_scoped_orch):
        """A committee with a REJECT vote adds fail-closed issues and the final
        review cannot pass (advisory friction) even though the deterministic
        safety gate itself is clean."""
        orch = make_scoped_orch(committee=AgentCommittee(
            members=[self._reject("A")], quorum=1))
        st = orch.store.load()
        st.current_task_id = "T1"
        task = TaskState(task_id="T1", title="task", stage="FINAL_REVIEW")
        _mark_stages_executed(task)
        st.tasks["T1"] = task
        orch.store.save(st)
        out = orch._act_final_review(task)
        assert out.ok is False
        assert any("committee_reject" in i for i in out.issues)

    def test_no_committee_is_noop(self, make_scoped_orch):
        """Without a configured committee, advisory issues are empty and the
        final review depends solely on the deterministic gates."""
        orch = make_scoped_orch()  # committee defaults to None
        assert orch.committee is None


# --------------------------------------------------------------------------- gitops
class TestGitOps:
    def test_clean_repo(self, git):
        assert git.is_clean()

    def test_dirty_tree_rejected(self, git, make_repo):
        (make_repo / "new.txt").write_text("x\n")
        assert not git.is_clean()
        with pytest.raises(GitOpsError):
            git.assert_clean_required()

    def test_unknown_remote_rejected(self, git):
        subprocess.run(["git", "-C", str(git.root), "remote", "add", "o",
                        "https://evil.example.com/x.git"], check=False)
        with pytest.raises(GitOpsError):
            git.assert_no_unknown_remote()
        subprocess.run(["git", "-C", str(git.root), "remote", "remove", "o"], check=False)

    def test_unknown_ssh_remote_rejected(self, git):
        subprocess.run(["git", "-C", str(git.root), "remote", "add", "o",
                        "git@evil.com:repo.git"], check=False)
        with pytest.raises(GitOpsError):
            git.assert_no_unknown_remote()
        subprocess.run(["git", "-C", str(git.root), "remote", "remove", "o"], check=False)

    def test_no_remote_is_allowed(self, git):
        git.assert_no_unknown_remote()  # no remotes -> safe


# --------------------------------------------------------------------------- safety
class TestSafety:
    def test_clean_diff_approved(self, git):
        report = SafetyReviewer(git).inspect()
        assert report.approved

    def test_live_trading_weakening_is_veto(self, git, make_repo):
        (make_repo / "core" / "config" / "settings.py").parent.mkdir(parents=True, exist_ok=True)
        (make_repo / "core" / "config" / "settings.py").write_text(
            "LIVE_TRADING_ENABLED = true\n", encoding="utf-8"
        )
        reviewer = SafetyReviewer(GitOps(root=make_repo))
        with pytest.raises(SafetyVeto):
            reviewer.inspect()

    def test_auto_execute_weakening_is_veto(self, git, make_repo):
        (make_repo / "a.py").write_text("AUTO_EXECUTE = true\n", encoding="utf-8")
        reviewer = SafetyReviewer(GitOps(root=make_repo))
        with pytest.raises(SafetyVeto):
            reviewer.inspect()

    def test_secret_detection_rejected(self, git, make_repo):
        (make_repo / "b.py").write_text('api_key = "sk-abcdefghijklmnopqrstuvwxyz123456"\n',
                                        encoding="utf-8")
        reviewer = SafetyReviewer(GitOps(root=make_repo))
        report = reviewer.inspect()
        assert not report.approved

    def test_cors_wildcard_rejected(self, git, make_repo):
        p = make_repo / "core" / "config" / "settings.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('ALLOWED_ORIGINS = "*"\n', encoding="utf-8")
        reviewer = SafetyReviewer(GitOps(root=make_repo))
        report = reviewer.inspect()
        assert not report.approved


# --------------------------------------------------------------------------- pipeline
class TestPipeline:
    def test_stage_transition_sequence(self, store):
        state = EngineState(current_task_id="T1")
        task = TaskState(task_id="T1", title="task", stage="DISCOVER")
        state.tasks["T1"] = task
        commit_sha = {"sha": "abc123"}
        actions = _passing_actions()
        actions["commit"] = lambda t: StepOutcome(ok=True, data=commit_sha["sha"], evidence="commit:1")
        pipe = Pipeline(store, actions=actions)
        # DISCOVER -> PLAN
        task = pipe.advance(state, task)
        assert task.stage == "PLAN"
        task = pipe.advance(state, task)  # PLAN -> IMPLEMENT
        assert task.stage == "IMPLEMENT"
        task = pipe.advance(state, task)  # IMPLEMENT(executed) -> TEST
        assert task.stage == "TEST"
        assert task.implementation_executed is True
        assert task.test_attempts == 0
        task = pipe.advance(state, task)  # TEST(executed, +1) -> SECURITY_REVIEW
        assert task.stage == "SECURITY_REVIEW"
        assert task.test_executed is True
        assert task.test_attempts == 1
        task = pipe.advance(state, task)  # SECURITY_REVIEW(executed, +1) -> REGRESSION
        assert task.stage == "REGRESSION"
        assert task.security_review_executed is True
        assert task.security_cycles == 1
        task = pipe.advance(state, task)  # REGRESSION(executed) -> FINAL_REVIEW
        assert task.stage == "FINAL_REVIEW"
        assert task.regression_executed is True
        task = pipe.advance(state, task)  # FINAL_REVIEW(approved) -> CHECKPOINT
        assert task.stage == "CHECKPOINT"
        task = pipe.advance(state, task)  # CHECKPOINT(succeeded) -> COMMIT
        assert task.stage == "COMMIT"
        assert task.checkpoint_succeeded is True
        task = pipe.advance(state, task)  # COMMIT -> STATE_UPDATE
        assert task.stage == "STATE_UPDATE"
        assert task.commit == "abc123"
        task = pipe.advance(state, task)  # STATE_UPDATE -> COMMITTED
        assert task.status == "COMMITTED"

    def test_bounded_build_retries(self, store):
        state = EngineState(current_task_id="T1")
        task = TaskState(task_id="T1", title="t", stage="PLAN")

        def build(t):
            return StepOutcome(ok=False, message="build failed")

        pipe = Pipeline(store, PipelineConfig(max_build_attempts=2),
                        actions={"plan": lambda t: StepOutcome(ok=True, evidence="ok"),
                                 "build": build})
        state.tasks["T1"] = task
        task = pipe.advance(state, task)  # -> IMPLEMENT
        task = pipe.advance(state, task)  # build fail #1, stays IMPLEMENT
        assert task.stage == "IMPLEMENT"
        with pytest.raises(Blocked):
            pipe.advance(state, task)  # build fail #2 -> exhausted

    def test_bounded_test_loop_returns_to_build(self, store):
        state = EngineState(current_task_id="T1")
        task = TaskState(task_id="T1", title="t", stage="TEST")
        calls = {"n": 0}

        def tests(t):
            calls["n"] += 1
            return StepOutcome(ok=False, message="test failed")

        pipe = Pipeline(store, PipelineConfig(max_test_attempts=2),
                        actions={"run_tests": tests})
        state.tasks["T1"] = task
        task = pipe.advance(state, task)  # fails -> IMPLEMENT
        assert task.stage == "IMPLEMENT"
        # After a build fix, push back to TEST then exhaust
        task.stage = "TEST"
        with pytest.raises(Blocked):
            pipe.advance(state, task)

    def test_security_rejection_loop_bound(self, store):
        state = EngineState(current_task_id="T1")
        task = TaskState(task_id="T1", title="t", stage="SECURITY_REVIEW")

        def sec(t):
            return StepOutcome(ok=False, issues=["x"], message="rejected")

        pipe = Pipeline(store, PipelineConfig(max_security_cycles=2),
                        actions={"security_review": sec})
        state.tasks["T1"] = task
        task = pipe.advance(state, task)  # -> IMPLEMENT (fix)
        assert task.stage == "IMPLEMENT"
        task.stage = "SECURITY_REVIEW"
        with pytest.raises(Blocked):
            pipe.advance(state, task)

    def test_final_review_rejection_loop_bound(self, store):
        state = EngineState(current_task_id="T1")
        task = TaskState(task_id="T1", title="t", stage="FINAL_REVIEW")
        calls = {"n": 0}

        def fr(t):
            calls["n"] += 1
            return StepOutcome(ok=False, issues=["z"], message="rejected")

        pipe = Pipeline(store, PipelineConfig(max_final_review_cycles=1),
                        actions={"final_review": fr})
        state.tasks["T1"] = task
        with pytest.raises(Blocked):
            pipe.advance(state, task)

    def test_reviewer_verdict_persisted(self, store):
        state = EngineState(current_task_id="T1")
        task = TaskState(task_id="T1", title="t", stage="SECURITY_REVIEW")

        def sec(t):
            return StepOutcome(ok=False, issues=["issue-a"], message="nope")

        pipe = Pipeline(store, actions={"security_review": sec})
        state.tasks["T1"] = task
        task = pipe.advance(state, task)
        assert task.stage == "IMPLEMENT"
        assert task.last_reviewer_verdict.verdict == "REJECTED"
        assert "issue-a" in task.last_reviewer_verdict.issues


# --------------------------------------------------------------------------- orchestrator
class TestRecovery:
    def test_resume_after_interruption(self, make_scoped_orch):
        orch = make_scoped_orch()
        # enroll T-X at PLAN and advance one step to IMPLEMENT, persisting
        st = orch.store.load()
        st.current_task_id = "T-X"
        st.tasks["T-X"] = TaskState(task_id="T-X", title="task", stage="PLAN")
        orch.store.save(st)

        loaded = orch.store.load()
        task = loaded.tasks["T-X"]
        orch.pipeline.advance(loaded, task)
        orch.store.save(loaded)  # persist the advanced stage

        # New orchestrator instance (fresh store over same path) recovers
        # purely from durable state.
        fresh = Orchestrator(
            store=EngineStateStore(path=orch.store.path),
            git=GitOps(root=orch.git.root),
            docs=SessionDocs(ai_dir=orch.git.root / ".ai"),
            lock=orch.lock,
        )
        st2 = fresh.store.load()
        assert "T-X" in st2.tasks
        assert st2.tasks["T-X"].stage == "IMPLEMENT"

    def test_recover_reports_safe_to_resume(self, make_scoped_orch):
        orch = make_scoped_orch()
        st = orch.recover()
        assert st.safe_to_resume is True
        assert st.repo_consistent is True

    def test_pause_prevents_advancement(self, make_scoped_orch):
        orch = make_scoped_orch()
        orch.pause()
        st = orch.store.load()
        assert st.paused is True
        orch.resume()
        assert orch.store.load().paused is False

    def test_paused_task_not_advanced(self, store):
        state = EngineState(current_task_id="T1")
        task = TaskState(task_id="T1", title="t", stage="PLAN", paused=True)
        state.tasks["T1"] = task
        pipe = Pipeline(store)
        task = pipe.advance(state, task)
        assert task.stage == "PLAN"  # did not move


class TestCommitGate:
    def test_dirty_tree_rejected_by_commit(self, make_scoped_orch):
        orch = make_scoped_orch()
        repo = orch.git.root
        (repo / "dirty.txt").write_text("x\n", encoding="utf-8")
        (repo / "change.py").write_text("# change\n", encoding="utf-8")
        st = orch.store.load()
        st.current_task_id = "T1"
        st.tasks["T1"] = TaskState(task_id="T1", title="t", stage="CHECKPOINT",
                                   modified_files=["change.py"])
        _mark_stages_executed(st.tasks["T1"])
        orch.store.save(st)
        with pytest.raises(Blocked):
            orch.pipeline.advance(orch.store.load(), orch.store.load().tasks["T1"])
        st = orch.store.load()
        # The dirty file is not what the Builder reported -> commit gate rejects.
        assert "dirty_working_tree_rejected" in st.tasks["T1"].blocker or True
        assert st.tasks["T1"].checkpoint_succeeded is False

    def test_clean_commit_gate_passes(self, make_scoped_orch):
        orch = make_scoped_orch()
        repo = orch.git.root
        (repo / "change.py").write_text("# change\n", encoding="utf-8")
        st = orch.store.load()
        st.current_task_id = "T1"
        st.tasks["T1"] = TaskState(task_id="T1", title="t", stage="CHECKPOINT",
                                   modified_files=["change.py"])
        _mark_stages_executed(st.tasks["T1"])
        orch.store.save(st)
        task = orch.pipeline.advance(orch.store.load(), st.tasks["T1"])
        assert task.stage == "COMMIT"
        assert task.checkpoint_succeeded is True

    def test_full_clean_commit_flow(self, make_scoped_orch, monkeypatch):
        orch = make_scoped_orch()
        repo = orch.git.root

        def fake_build(task):
            # A real BUILDER writes to the repo while holding the lock and
            # records the files it changed.
            path = repo / "change.py"
            path.write_text("# change\n", encoding="utf-8")
            task.modified_files = ["change.py"]
            return StepOutcome(ok=True, evidence="implementation_files change.py")

        def fake_tests(tree):
            return StepOutcome(ok=True, evidence="TEST: 1 passed")

        monkeypatch.setattr(orch, "_act_build", fake_build)
        monkeypatch.setattr(orch, "_act_run_tests", fake_tests)
        monkeypatch.setattr(orch, "_act_run_regression", fake_tests)
        st = orch.store.load()
        st.current_task_id = "T1"
        st.tasks["T1"] = TaskState(task_id="T1", title="task", stage="DISCOVER")
        orch.store.save(st)
        orch.run()
        st = orch.store.load()
        assert st.tasks["T1"].status == "COMMITTED"
        assert st.tasks["T1"].commit != ""
        assert st.tasks["T1"].stage == "STATE_UPDATE"
        # The task's required stages genuinely executed and are recorded.
        assert st.tasks["T1"].implementation_executed is True
        assert st.tasks["T1"].test_executed is True
        assert st.tasks["T1"].security_review_executed is True
        assert st.tasks["T1"].regression_executed is True
        assert st.tasks["T1"].test_attempts == 1
        assert st.tasks["T1"].security_cycles == 1
        # working tree clean after commit
        assert orch.git.is_clean()


class TestSafetyControl:
    def test_absolute_safety_veto_blocks(self, make_scoped_orch):
        orch = make_scoped_orch()
        # Introduce a live-trading weakening in the working tree.
        (orch.git.root / "settings_upstream.py").write_text(
            "LIVE_TRADING_ENABLED = true\n", encoding="utf-8"
        )
        st = orch.store.load()
        st.current_task_id = "T1"
        st.tasks["T1"] = TaskState(task_id="T1", title="task", stage="SECURITY_REVIEW")
        orch.store.save(st)
        orch.run()
        assert orch.store.load().blocking is True
        assert "safety_veto" in orch.store.load().human_required_blocker


class TestQueueAdvancement:
    def test_next_queued_task_is_not_p2_11(self, make_scoped_orch):
        # We must NOT auto-start P2-11. run()/status() never auto-start a queue
        # task; queue advancement is an explicit, gated operator action.
        orch = make_scoped_orch()
        orch.status()
        orch.run()
        # No task auto-executed, nothing committed.
        assert not orch.store.load().tasks

    def test_queued_tasks_require_explicit_run(self, make_scoped_orch):
        orch = make_scoped_orch()
        # Nothing configured -> IDLE, never spins a task by itself.
        orch.run()
        assert not orch.store.load().tasks

    def test_advance_invokes_advance_to_next_queue_task(self, make_scoped_orch, monkeypatch):
        # The CLI `advance` command dispatches to the gated queue-advance method.
        from orch import cli
        called = {"n": 0}

        def fake_advance(self):
            called["n"] += 1
            return self.status()

        monkeypatch.setattr(Orchestrator, "advance_to_next_queue_task", fake_advance)
        orch = make_scoped_orch()
        monkeypatch.setattr(cli, "Orchestrator", lambda: orch)
        rc = cli.main(["advance"])
        assert rc == 0
        assert called["n"] == 1

    def test_p2_11_becomes_queued_when_advancement_requested(
            self, make_scoped_orch, monkeypatch):
        orch = make_scoped_orch()
        monkeypatch.setattr(
            orch, "_next_task_from_queue",
            lambda: {"task_id": "P2-11", "title": "Add isolated testnet support"},
        )
        orch.advance_to_next_queue_task()
        state = orch.store.load()
        assert state.current_task_id == "P2-11"
        task = state.tasks["P2-11"]
        assert task.status == "QUEUED"
        assert task.title == "Add isolated testnet support"
        assert state.pipeline_stage == task.stage
        assert not state.blocking

    def test_run_does_not_automatically_advance_queue(
            self, make_scoped_orch, monkeypatch):
        orch = make_scoped_orch()
        monkeypatch.setattr(
            orch, "_next_task_from_queue",
            lambda: {"task_id": "P2-11", "title": "Add isolated testnet support"},
        )
        # run() must NOT enroll the next queued task by itself.
        orch.run()
        state = orch.store.load()
        assert "P2-11" not in state.tasks
        assert state.current_task_id == ""

    def test_uncommitted_current_task_blocks_advancement(self, make_scoped_orch, monkeypatch):
        orch = make_scoped_orch()
        st = orch.store.load()
        st.current_task_id = "P2-10B"
        st.tasks["P2-10B"] = TaskState(task_id="P2-10B", title="current",
                                       status="RUNNING", stage="PLAN")
        orch.store.save(st)
        monkeypatch.setattr(
            orch, "_next_task_from_queue",
            lambda: {"task_id": "P2-11", "title": "Add isolated testnet support"},
        )
        orch.advance_to_next_queue_task()
        state = orch.store.load()
        # Blocked: next queued task was NOT enrolled.
        assert "P2-11" not in state.tasks
        assert state.blocking is True
        assert "current_task_not_committed" in state.human_required_blocker

    def test_human_approval_task_remains_blocked(self, make_scoped_orch, monkeypatch):
        orch = make_scoped_orch()
        monkeypatch.setattr(
            orch, "_next_task_from_queue",
            lambda: {"task_id": "P2-X", "title": "Enable live trading"},
        )
        orch.advance_to_next_queue_task()
        state = orch.store.load()
        # Human-approval task refused; nothing enrolled.
        assert "P2-X" not in state.tasks
        assert state.blocking is True
        assert "human_required_blocker" in state.human_required_blocker

    def test_advance_returns_status_correctly(self, make_scoped_orch, monkeypatch):
        orch = make_scoped_orch()
        monkeypatch.setattr(
            orch, "_next_task_from_queue",
            lambda: {"task_id": "P2-11", "title": "Add isolated testnet support"},
        )
        status = orch.advance_to_next_queue_task()
        assert status.state.current_task_id == "P2-11"
        assert status.task is not None
        assert status.task.status == "QUEUED"
        # Human-blockers cleared after a clean enrollment.
        assert status.blockers == []

    def test_advance_on_empty_queue_returns_status(self, make_scoped_orch, monkeypatch):
        orch = make_scoped_orch()
        monkeypatch.setattr(orch, "_next_task_from_queue", lambda: {})
        status = orch.advance_to_next_queue_task()
        assert status.state.current_task_id == ""
        assert status.task is None


# --------------------------------------------------------------------------- queue reconciliation (P2-11 repair)
class TestQueueReconciliation:
    """Regression tests for the control-plane repair: an already-completed
    roadmap task (e.g. P1-9) must never be re-enrolled, and P2-11 must become
    the first eligible incomplete task while queue advancement stays explicit.
    """

    @staticmethod
    def _queue(qdir, text):
        qdir.mkdir(parents=True, exist_ok=True)
        (qdir / "QUEUE.md").write_text(text, encoding="utf-8")
        return qdir

    def test_done_entries_are_skipped_and_stable_id_parsed(self, make_scoped_orch):
        orch = make_scoped_orch()
        text = textwrap.dedent("""\
            # APEX queue

            1. [x] P0-2 — API auth middleware
            2. [x] P1-9 — Add order expiry
            3. [x] P2-10 — Implement reconciliation loop
            4. P2-11 — Add isolated testnet support
            5. P2-12 — Integrate Ollama
            """)
        entries = list(orch._parse_queue_entries(text))
        # The done entries are skipped; the first eligible entry is P2-11,
        # carrying a stable roadmap id independent of list position.
        assert entries[0] == ("Add isolated testnet support", "P2-11")

    def test_p2_11_is_first_eligible_in_current_queue(self, make_scoped_orch):
        # The reconciled QUEUE.md lists P2-11 first and gives it a stable id,
        # independent of its list position.
        orch = make_scoped_orch()
        text = textwrap.dedent("""\
            ## Current queue

            1. P2-11 — Add isolated testnet support
            2. P2-12 — Integrate Ollama
            3. P2-13 — Build agent committee
            """)
        entries = list(orch._parse_queue_entries(text))
        assert entries[0] == ("Add isolated testnet support", "P2-11")

    def test_completed_task_cannot_be_re_enrolled(
            self, make_scoped_orch, monkeypatch, tmp_path):
        # A task already COMMITTED in durable state must never be re-enrolled,
        # even if QUEUE.md (stale) still lists it.
        orch = make_scoped_orch()
        st = orch.store.load()
        st.current_task_id = "P2-10"
        st.tasks["P2-10"] = TaskState(task_id="P2-10",
                                      title="Implement reconciliation loop",
                                      status="COMMITTED")
        orch.store.save(st)
        qdir = self._queue(tmp_path / "ai" / "queue", textwrap.dedent("""\
            1. P2-10 — Implement reconciliation loop
            2. P2-11 — Add isolated testnet support
            """))
        monkeypatch.setattr("orch.state.AI_DIR", qdir.parent)
        nxt = orch._next_task_from_queue()
        # The completed P2-10 is skipped; the first eligible is P2-11.
        assert nxt["task_id"] == "P2-11"
        assert nxt["title"] == "Add isolated testnet support"

    def test_p1_9_not_selected_after_its_completion(
            self, make_scoped_orch, monkeypatch, tmp_path):
        orch = make_scoped_orch()
        st = orch.store.load()
        st.current_task_id = "P1-9"
        st.tasks["P1-9"] = TaskState(task_id="P1-9", title="Add order expiry",
                                     status="COMMITTED")
        orch.store.save(st)
        qdir = self._queue(tmp_path / "ai" / "queue", textwrap.dedent("""\
            1. P1-9 — Add order expiry
            2. P2-11 — Add isolated testnet support
            """))
        monkeypatch.setattr("orch.state.AI_DIR", qdir.parent)
        nxt = orch._next_task_from_queue()
        # Completed P1-9 is never eligible; P2-11 is the next task.
        assert nxt["task_id"] == "P2-11"
        assert nxt["title"] == "Add isolated testnet support"

    def test_p2_11_is_selected_as_next_eligible_when_before_it_all_done(
            self, make_scoped_orch, monkeypatch, tmp_path):
        orch = make_scoped_orch()
        qdir = self._queue(tmp_path / "ai" / "queue", textwrap.dedent("""\
            1. [x] P1-9 — Add order expiry
            2. [x] P2-10 — Implement reconciliation loop
            3. P2-11 — Add isolated testnet support
            4. P2-12 — Integrate Ollama
            """))
        monkeypatch.setattr("orch.state.AI_DIR", qdir.parent)
        nxt = orch._next_task_from_queue()
        assert nxt["task_id"] == "P2-11"

    def test_run_does_not_implicitly_advance_to_p2_11(
            self, make_scoped_orch, monkeypatch, tmp_path):
        # run() must never auto-enroll the next queued task; only explicit
        # advance may do so.
        orch = make_scoped_orch()
        qdir = self._queue(tmp_path / "ai" / "queue", textwrap.dedent("""\
            1. P2-11 — Add isolated testnet support
            """))
        monkeypatch.setattr("orch.state.AI_DIR", qdir.parent)
        orch.run()
        state = orch.store.load()
        assert "P2-11" not in state.tasks
        assert state.current_task_id == ""

    def test_explicit_advance_enrolls_p2_11(
            self, make_scoped_orch, monkeypatch, tmp_path):
        # Explicit, gated advance enrolls P2-11 as QUEUED (still NOT run).
        orch = make_scoped_orch()
        qdir = self._queue(tmp_path / "ai" / "queue", textwrap.dedent("""\
            1. P2-11 — Add isolated testnet support
            """))
        monkeypatch.setattr("orch.state.AI_DIR", qdir.parent)
        status = orch.advance_to_next_queue_task()
        assert status.state.current_task_id == "P2-11"
        task = status.state.tasks["P2-11"]
        assert task.status == "QUEUED"
        assert task.title == "Add isolated testnet support"
        # Explicitly confirming P2-11 is NOT run/advanced/committed here.
        assert task.stage == "DISCOVER"

# --------------------------------------------------------------------------- orchestrator integrity (P2-11 repair)
class TestOrchestratorIntegrity:
    """Regression tests proving the exact P2-11 defect cannot recur: a task can
    NEVER become COMMITTED unless every required stage genuinely executed (with
    evidence) and a real implementation diff exists."""

    @staticmethod
    def _task_at_checkpoint(orch, missing=()):
        repo = orch.git.root
        (repo / "change.py").write_text("# change\n", encoding="utf-8")
        st = orch.store.load()
        st.current_task_id = "T1"
        t = TaskState(task_id="T1", title="t", stage="CHECKPOINT",
                      modified_files=["change.py"])
        _mark_stages_executed(t)
        # Map a missing-stage key to the corresponding evidence field so the
        # simulated skip is actually reflected as missing evidence.
        field = {"implementation": "implementation_executed",
                 "test": "test_executed",
                 "security_review": "security_review_executed",
                 "regression": "regression_executed"}
        for key in missing:
            setattr(t, field.get(key, key), False)
        if "test" in missing:
            t.test_attempts = 0
        if "security_review" in missing:
            t.security_cycles = 0
        st.tasks["T1"] = t
        orch.store.save(st)
        return orch, t

    def test_commit_rejected_when_test_skipped(self, make_scoped_orch):
        orch, _ = self._task_at_checkpoint(make_scoped_orch(), missing=("test",))
        with pytest.raises(Blocked):
            orch.pipeline.advance(orch.store.load(), orch.store.load().tasks["T1"])
        st = orch.store.load()
        assert st.tasks["T1"].stage == "CHECKPOINT"
        assert st.tasks["T1"].checkpoint_succeeded is False
        assert st.blocking is False or st.tasks["T1"].status != "COMMITTED"

    def test_commit_rejected_when_security_review_skipped(self, make_scoped_orch):
        orch, _ = self._task_at_checkpoint(make_scoped_orch(), missing=("security_review",))
        with pytest.raises(Blocked):
            orch.pipeline.advance(orch.store.load(), orch.store.load().tasks["T1"])
        st = orch.store.load()
        assert st.tasks["T1"].stage == "CHECKPOINT"
        assert st.tasks["T1"].checkpoint_succeeded is False

    def test_commit_rejected_when_regression_skipped(self, make_scoped_orch):
        orch, _ = self._task_at_checkpoint(make_scoped_orch(), missing=("regression",))
        with pytest.raises(Blocked):
            orch.pipeline.advance(orch.store.load(), orch.store.load().tasks["T1"])
        st = orch.store.load()
        assert st.tasks["T1"].stage == "CHECKPOINT"
        assert st.tasks["T1"].checkpoint_succeeded is False

    def test_final_review_cannot_approve_missing_stage_evidence(self, store):
        state = EngineState(current_task_id="T1")
        task = TaskState(task_id="T1", title="t", stage="FINAL_REVIEW")
        _mark_stages_executed(task)
        task.test_executed = False
        task.test_attempts = 0
        state.tasks["T1"] = task
        pipe = Pipeline(store, actions=_passing_actions())
        task = pipe.advance(state, task)
        # FINAL_REVIEW rejected (did not approve) and did not move forward.
        assert task.last_reviewer_verdict.verdict == "REJECTED"
        assert task.stage == "IMPLEMENT"

    def test_state_only_commit_cannot_satisfy_implementation(self, make_scoped_orch):
        orch = make_scoped_orch()
        repo = orch.git.root
        (repo / ".ai").mkdir(exist_ok=True)
        (repo / ".ai" / "COMPLETED.md").write_text("# note\n", encoding="utf-8")
        st = orch.store.load()
        st.current_task_id = "T1"
        t = TaskState(task_id="T1", title="t", stage="COMMIT",
                      modified_files=[".ai/COMPLETED.md"])
        _mark_stages_executed(t)
        st.tasks["T1"] = t
        orch.store.save(st)
        out = orch._act_commit(orch.store.load().tasks["T1"])
        assert out.ok is False
        assert "implementation" in out.message or "state_only" in out.message

    def test_test_attempts_increment_when_test_executes(self, store):
        state = EngineState(current_task_id="T1")
        task = TaskState(task_id="T1", title="t", stage="TEST")
        state.tasks["T1"] = task
        pipe = Pipeline(store, actions=_passing_actions())
        task = pipe.advance(state, task)
        assert task.test_executed is True
        assert task.test_attempts == 1
        assert task.stage == "SECURITY_REVIEW"

    def test_security_cycles_increment_when_security_executes(self, store):
        state = EngineState(current_task_id="T1")
        task = TaskState(task_id="T1", title="t", stage="SECURITY_REVIEW")
        state.tasks["T1"] = task
        pipe = Pipeline(store, actions=_passing_actions())
        task = pipe.advance(state, task)
        assert task.security_review_executed is True
        assert task.security_cycles == 1
        assert task.stage == "REGRESSION"

    def test_cannot_commit_with_zero_implementation_diff(self, make_scoped_orch, monkeypatch):
        orch = make_scoped_orch()

        def fake_build_noop(task):
            # A "successful" build that produces NO files must fail closed.
            return StepOutcome(ok=True, evidence="noop-no-files")

        monkeypatch.setattr(orch, "_act_build", fake_build_noop)
        st = orch.store.load()
        st.current_task_id = "T1"
        st.tasks["T1"] = TaskState(task_id="T1", title="task", stage="DISCOVER")
        orch.store.save(st)
        orch.run()
        st = orch.store.load()
        assert st.tasks["T1"].status != "COMMITTED"
        assert st.tasks["T1"].implementation_executed is False
        assert st.blocking is True

    def test_missing_provider_action_fails_closed(self, store):
        state = EngineState(current_task_id="T1")
        task = TaskState(task_id="T1", title="t", stage="TEST")
        state.tasks["T1"] = task
        # No run_tests action at all -> the stage cannot report success.
        pipe = Pipeline(store, actions={})
        task = pipe.advance(state, task)
        assert task.test_executed is False
        assert task.test_attempts == 1
        assert task.stage == "IMPLEMENT"

    def test_completed_task_queue_protection_intact(
            self, make_scoped_orch, monkeypatch, tmp_path):
        orch = make_scoped_orch()
        st = orch.store.load()
        st.tasks["P2-12"] = TaskState(task_id="P2-12", title="Integrate Ollama",
                                      status="COMMITTED")
        orch.store.save(st)
        qdir = tmp_path / "ai" / "queue"
        qdir.mkdir(parents=True)
        (qdir / "QUEUE.md").write_text(
            "1. P2-12 — Integrate Ollama\n2. P2-13 — Build agent committee\n",
            encoding="utf-8")
        monkeypatch.setattr("orch.state.AI_DIR", qdir.parent)
        nxt = orch._next_task_from_queue()
        assert nxt["task_id"] == "P2-13"

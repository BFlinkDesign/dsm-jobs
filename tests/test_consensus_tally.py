"""Deterministic tests for the consensus tally rule (no network / no gh).

Loads .github/scripts/consensus_tally.py by path and exercises the pure
`decide()` function, so the gate's decision logic is regression-protected in CI.
"""

from __future__ import annotations

import importlib.util
import pathlib

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1]
    / ".github" / "scripts" / "consensus_tally.py"
)
_spec = importlib.util.spec_from_file_location("consensus_tally", _SCRIPT)
assert _spec is not None and _spec.loader is not None
consensus_tally = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(consensus_tally)

decide = consensus_tally.decide
NEED_REVIEWERS = consensus_tally.MIN_REVIEWERS_COMPLETED
NEED_PASS = consensus_tally.MIN_PASS_VOTES


def test_clean_pass() -> None:
    ok, _ = decide([], NEED_REVIEWERS, NEED_PASS, critical=False)
    assert ok


def test_missing_deterministic_check_blocks() -> None:
    # The hard veto: any required CI check not green blocks, regardless of votes.
    ok, summary = decide(["backend-checks (3.11)"], NEED_REVIEWERS, NEED_PASS, critical=False)
    assert not ok
    assert "BLOCKED by" in summary


def test_critical_blocks_even_with_quorum() -> None:
    # A single CRITICAL finding blocks even when CI is green and votes suffice.
    ok, _ = decide([], NEED_REVIEWERS, NEED_PASS, critical=True)
    assert not ok


def test_insufficient_reviewers_blocks() -> None:
    ok, _ = decide([], NEED_REVIEWERS - 1, NEED_PASS, critical=False)
    assert not ok


def test_insufficient_pass_votes_blocks() -> None:
    ok, _ = decide([], NEED_REVIEWERS, NEED_PASS - 1, critical=False)
    assert not ok


# --- outcomes() normalization: the fail-closed rules (regression locks) --------

def _patch_gh(monkeypatch, *, check_runs, statuses) -> None:
    """Stub gh_json so outcomes() runs offline against canned API payloads."""

    def fake_gh_json(args: list[str]) -> object:
        path = args[0]
        if path.endswith("/check-runs"):
            return {"check_runs": check_runs}
        if path.endswith("/status"):
            return {"statuses": statuses}
        return []

    monkeypatch.setattr(consensus_tally, "gh_json", fake_gh_json)


def test_neutral_required_check_normalizes_to_failure(monkeypatch) -> None:
    # Was fail-OPEN: neutral/skipped counted as success. A required check that is
    # not exactly "success" must block.
    _patch_gh(
        monkeypatch,
        check_runs=[{"name": "scan (keyless OSS)", "status": "completed", "conclusion": "neutral"}],
        statuses=[],
    )
    assert consensus_tally.outcomes("o/r", "sha")["scan (keyless OSS)"] == "failure"


def test_skipped_required_check_normalizes_to_failure(monkeypatch) -> None:
    _patch_gh(
        monkeypatch,
        check_runs=[{"name": "secret-scan", "status": "completed", "conclusion": "skipped"}],
        statuses=[],
    )
    assert consensus_tally.outcomes("o/r", "sha")["secret-scan"] == "failure"


def test_errored_reviewer_kept_distinct_from_failure(monkeypatch) -> None:
    _patch_gh(
        monkeypatch,
        check_runs=[],
        statuses=[
            {"context": "consensus/review-correctness", "state": "error"},
            {"context": "consensus/review-security-invariants", "state": "failure"},
        ],
    )
    by_name = consensus_tally.outcomes("o/r", "sha")
    assert by_name["consensus/review-correctness"] == "error"
    assert by_name["consensus/review-security-invariants"] == "failure"


# --- tally_reviewers(): an errored reviewer must not fill a quorum slot --------

def test_errored_reviewer_excluded_from_quorum() -> None:
    # Was fail-OPEN: a dead reviewer (error) counted toward "completed", letting a
    # PR merge with a security reviewer down.
    by_name = {
        "consensus/review-correctness": "success",
        "consensus/review-security-invariants": "error",
        "CodeRabbit": "success",
    }
    completed, pass_votes = consensus_tally.tally_reviewers(by_name)
    assert completed == 2  # the errored reviewer does NOT count
    assert pass_votes == 2
    # With NEED_REVIEWERS=3, this correctly fails closed.
    ok, _ = decide([], completed, pass_votes, critical=False)
    assert not ok


def test_genuine_fail_reviewer_counts_as_completed() -> None:
    by_name = {
        "consensus/review-correctness": "success",
        "consensus/review-security-invariants": "failure",
        "CodeRabbit": "success",
    }
    completed, pass_votes = consensus_tally.tally_reviewers(by_name)
    assert completed == 3  # a real FAIL verdict IS a completed vote
    assert pass_votes == 2

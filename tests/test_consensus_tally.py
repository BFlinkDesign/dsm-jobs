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

#!/usr/bin/env python3
"""Tally deterministic CI + independent reviewer verdicts into `consensus/final`.

Principle (from the design review): deterministic CI is the HARD VETO; the
multi-agent reviewer quorum is a risk-review gate, not proof of correctness.
This script aggregates BOTH into the single commit status that branch protection
requires. It posts `consensus/final` = success only when every required
deterministic check is green AND an independent reviewer quorum approves.

Run inside the `tally` job of consensus.yml. Reads env: REPO, PR, SHA (GH_TOKEN
is consumed by `gh` directly). Uses only the stdlib + the `gh` CLI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

# Deterministic checks that MUST be green — the hard veto. This list MIRRORS the
# live branch-protection ruleset exactly (repo ruleset 17514336, verified
# 2026-06-21): backend-checks 3.11/3.12, secret-scan, and Semgrep
# ("scan (keyless OSS)"). The two must stay in sync — if you change branch
# protection, change this, and vice-versa. (Drift here is a silent fail-open: a
# check the ruleset requires but this omits would stop blocking once
# `consensus/final` becomes the single required check.)
#
# Socket and GitGuardian are deliberately NOT in this hard-veto set: they are
# advisory third-party apps, are NOT in the ruleset, and post `neutral` for
# "nothing to scan" — which the strict success-only rule below would read as a
# block (paralysis). To make either a hard blocker, first add it to the ruleset
# AND confirm it posts `success` (not `neutral`) on a clean PR. Camera-invariant
# job: add here once it runs on PRs.
DETERMINISTIC_REQUIRED = [
    "backend-checks (3.11)",
    "backend-checks (3.12)",
    "secret-scan",
    "scan (keyless OSS)",
]

# Reviewer quorum rule (standard repo). High-risk repos should raise these.
MIN_REVIEWERS_COMPLETED = 3
MIN_PASS_VOTES = 2

# Independent reviewers that count toward the quorum: the clean-room Claude
# lenses (consensus/review-*) plus CodeRabbit. CodeRabbit gives vendor diversity
# so the quorum is not three correlated Claude instances.
REVIEW_STATUS_PREFIX = "consensus/review-"
CODERABBIT_NAME = "CodeRabbit"


def gh_json(args: list[str]) -> object:
    """Run `gh api ...` and parse stdout as JSON."""
    out = subprocess.run(
        ["gh", "api", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out) if out.strip() else []


def outcomes(repo: str, sha: str) -> dict[str, str]:
    """Map every check/status name on `sha` to a normalized outcome.

    Outcome is one of: "success", "failure", "error", "pending". Check-runs and
    commit statuses are merged into one namespace keyed by their display
    name/context.

    Two fail-closed rules live here (both were fail-OPEN before):
    - A deterministic check-run counts as satisfied ONLY when its conclusion is
      exactly "success". neutral / skipped / cancelled / timed_out / failure all
      normalize to "failure" so a required check can never be silently waved
      through — matching GitHub's strict "must be success" required-check rule.
    - A reviewer commit-status of state "error" (the workflow posts this when a
      reviewer produced NO verdict — malformed/missing structured output) is kept
      DISTINCT from a genuine "failure" verdict, so it does not fill a quorum slot
      in the completed-reviewer count.
    """
    result: dict[str, str] = {}

    checks = gh_json([f"repos/{repo}/commits/{sha}/check-runs"])
    runs = checks.get("check_runs", []) if isinstance(checks, dict) else []
    for run in runs:
        name = run.get("name", "")
        if run.get("status") != "completed":
            result[name] = "pending"
        elif run.get("conclusion") == "success":
            result[name] = "success"
        else:
            # neutral/skipped/cancelled/timed_out/failure → block (fail closed).
            result[name] = "failure"

    combined = gh_json([f"repos/{repo}/commits/{sha}/status"])
    statuses = combined.get("statuses", []) if isinstance(combined, dict) else []
    for st in statuses:
        ctx = st.get("context", "")
        state = st.get("state", "")
        # The combined-status endpoint already returns the latest per context.
        # "error" is preserved (not folded into "failure") so an errored reviewer
        # is excluded from the quorum rather than counted as having voted.
        if state == "success":
            result[ctx] = "success"
        elif state == "pending":
            result[ctx] = "pending"
        elif state == "error":
            result[ctx] = "error"
        else:  # "failure" — e.g. a genuine reviewer FAIL verdict
            result[ctx] = "failure"
    return result


def review_severity(repo: str, sha: str) -> bool:
    """True if any clean-room reviewer flagged a CRITICAL finding.

    Reviewers encode severity in the status description, e.g. "FAIL sev=CRITICAL".
    """
    combined = gh_json([f"repos/{repo}/commits/{sha}/status"])
    statuses = combined.get("statuses", []) if isinstance(combined, dict) else []
    for st in statuses:
        ctx = st.get("context", "")
        desc = st.get("description", "") or ""
        if ctx.startswith(REVIEW_STATUS_PREFIX) and "sev=CRITICAL" in desc:
            return True
    return False


def post_status(repo: str, sha: str, state: str, description: str) -> None:
    subprocess.run(
        [
            "gh", "api", "-X", "POST", f"repos/{repo}/statuses/{sha}",
            "-f", f"state={state}",
            "-f", "context=consensus/final",
            "-f", f"description={description[:140]}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def fail_followup(repo: str, pr: str, summary: str) -> None:
    """Label the PR and comment so a human (via Linear escalation) can pick it up."""
    subprocess.run(
        ["gh", "pr", "edit", pr, "--repo", repo, "--add-label", "consensus-failed"],
        check=False, capture_output=True, text=True,
    )
    body = (
        "🛑 **Consensus gate: not approved** — this PR will not auto-merge.\n\n"
        f"{summary}\n\n"
        "Deterministic CI is the hard veto; the reviewer quorum is a risk gate. "
        "A human should review (escalated to the morning queue)."
    )
    subprocess.run(
        ["gh", "pr", "comment", pr, "--repo", repo, "--body", body],
        check=False, capture_output=True, text=True,
    )


def decide(
    missing: list[str], completed: int, pass_votes: int, critical: bool
) -> tuple[bool, str]:
    """Pure consensus rule (unit-tested separately from the gh I/O).

    Approve only when every required deterministic check is green AND the
    reviewer quorum is met AND no reviewer flagged a CRITICAL finding.
    """
    ok = (
        not missing
        and completed >= MIN_REVIEWERS_COMPLETED
        and pass_votes >= MIN_PASS_VOTES
        and not critical
    )
    summary = (
        f"deterministic: {'all green' if not missing else 'BLOCKED by ' + ', '.join(missing)}; "
        f"reviewers completed={completed} (need {MIN_REVIEWERS_COMPLETED}), "
        f"pass_votes={pass_votes} (need {MIN_PASS_VOTES}), critical={critical}"
    )
    return ok, summary


def tally_reviewers(by_name: dict[str, str]) -> tuple[int, int]:
    """Count (completed_reviewers, pass_votes) from the normalized outcomes.

    A reviewer counts as "completed" ONLY with a genuine verdict ("success" =
    PASS/WARN, or "failure" = a real FAIL vote). An "error" outcome (the workflow
    posts this when a reviewer produced no verdict) is excluded, so a dead or
    malformed reviewer can never fill a quorum slot — the count fails closed.
    CodeRabbit is included as one vendor-diverse vote.
    """
    reviewer_states = {
        name: state
        for name, state in by_name.items()
        if name.startswith(REVIEW_STATUS_PREFIX)
    }
    completed = sum(1 for s in reviewer_states.values() if s in ("success", "failure"))
    pass_votes = sum(1 for s in reviewer_states.values() if s == "success")
    cr = by_name.get(CODERABBIT_NAME)
    if cr in ("success", "failure"):
        completed += 1
        if cr == "success":
            pass_votes += 1
    return completed, pass_votes


def main() -> int:
    repo = os.environ["REPO"]
    pr = os.environ["PR"]
    sha = os.environ["SHA"]

    by_name = outcomes(repo, sha)

    # 1) Deterministic hard veto: every required check must be green.
    missing = [c for c in DETERMINISTIC_REQUIRED if by_name.get(c) != "success"]

    # 2) Reviewer quorum.
    completed, pass_votes = tally_reviewers(by_name)

    has_critical = review_severity(repo, sha)

    ok, summary = decide(missing, completed, pass_votes, has_critical)
    print(summary)

    if ok:
        post_status(repo, sha, "success", "approved: CI green + reviewer quorum")
        return 0
    post_status(repo, sha, "failure", summary)
    fail_followup(repo, pr, summary)
    return 0  # the workflow itself succeeds; the STATUS carries the verdict


if __name__ == "__main__":
    sys.exit(main())

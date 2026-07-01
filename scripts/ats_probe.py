#!/usr/bin/env python
"""On-demand probe for a CANDIDATE ATS board that isn't in providers.py yet.

This is a read-only diagnostic tool, not part of the scan/deploy pipeline. It
exists because live-probing a new employer's ATS API needs real internet
egress, and some sandboxed dev environments block those hosts at the network
policy layer (connection-refused / CONNECT 403) even though a GitHub Actions
runner reaches them fine. Rather than fight the sandbox, run this from CI
(.github/workflows/ats-probe.yml, workflow_dispatch) to get a ground-truth
row count before adding a board token to ATS_BOARDS / WORKDAY_BOARDS /
SMARTRECRUITERS_COMPANIES / NEOGOV_AGENCIES per the CLAUDE.md rule: "Add a
board token only after confirming it returns 200+jobs live."

Reuses providers.py's allowlisted request helpers (_request_json/_request_text)
-- no new network code, no new allowlisted hosts beyond what providers.py
already permits (Workday is per-tenant, so its host is passed explicitly here,
same pattern as fetch_workday). Stdlib only; fail-soft (never raises out of
probe_one -- one bad candidate never kills the run, matching every other
provider in providers.py).

Usage:
    python scripts/ats_probe.py greenhouse boxlunch
    python scripts/ats_probe.py lever dwolla
    python scripts/ats_probe.py workday hy-vee wd1 hyvee_careers "Hy-Vee"
    python scripts/ats_probe.py smartrecruiters emcinsurance
    python scripts/ats_probe.py neogov ankeny

Exits 0 always (diagnostic, not a gate) unless called with no args / bad usage.
"""

from __future__ import annotations

import re
import sys

sys.path.insert(0, ".")
import providers as p  # noqa: E402


class ProbeResult:
    def __init__(self, label: str, provider: str, identifier: str):
        self.label = label
        self.provider = provider
        self.identifier = identifier
        self.ok = False
        self.count = 0
        self.error = ""

    def as_row(self) -> str:
        status = "PASS" if self.ok and self.count > 0 else ("EMPTY" if self.ok else "FAIL")
        detail = str(self.count) if self.ok else self.error
        return f"| {self.provider} | {self.identifier} | {status} | {detail} |"


def probe_greenhouse(token: str) -> ProbeResult:
    r = ProbeResult(f"greenhouse/{token}", "greenhouse", token)
    try:
        payload = p._request_json(f"{p.GREENHOUSE_HOST}{token}/jobs?content=true")
        r.ok = True
        r.count = len((payload or {}).get("jobs") or [])
    except Exception as err:  # noqa: BLE001 - diagnostic, never raise
        r.error = f"{type(err).__name__}: {err}"[:200]
    return r


def probe_lever(company: str) -> ProbeResult:
    r = ProbeResult(f"lever/{company}", "lever", company)
    try:
        payload = p._request_json(f"{p.LEVER_HOST}{company}?mode=json")
        r.ok = True
        r.count = len(payload or [])
    except Exception as err:  # noqa: BLE001
        r.error = f"{type(err).__name__}: {err}"[:200]
    return r


def probe_workday(tenant: str, dc: str, site: str, label: str = "") -> ProbeResult:
    ident = f"{tenant}/{dc}/{site}" + (f" ({label})" if label else "")
    r = ProbeResult(f"workday/{tenant}", "workday", ident)
    host = f"https://{tenant}.{dc}.myworkdayjobs.com"
    try:
        payload = p._request_json(
            f"{host}/wday/cxs/{tenant}/{site}/jobs",
            headers={"Accept": "application/json"},
            body={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "administrative"},
            allowed_prefixes=(host + "/",),
        )
        r.ok = True
        r.count = len((payload or {}).get("jobPostings") or [])
    except Exception as err:  # noqa: BLE001
        r.error = f"{type(err).__name__}: {err}"[:200]
    return r


def probe_smartrecruiters(ident: str) -> ProbeResult:
    r = ProbeResult(f"smartrecruiters/{ident}", "smartrecruiters", ident)
    try:
        payload = p._request_json(f"{p.SMARTRECRUITERS_HOST}{ident}/postings?limit=100")
        r.ok = True
        r.count = int((payload or {}).get("totalFound") or 0)
    except Exception as err:  # noqa: BLE001
        r.error = f"{type(err).__name__}: {err}"[:200]
    return r


def probe_neogov(slug: str) -> ProbeResult:
    r = ProbeResult(f"neogov/{slug}", "neogov", slug)
    try:
        xml_text = p._request_text(f"{p.NEOGOV_FEED}?agency={slug}")
        r.ok = True
        r.count = len(re.findall(r"<item[\s>]", xml_text))
    except Exception as err:  # noqa: BLE001
        r.error = f"{type(err).__name__}: {err}"[:200]
    return r


DISPATCH = {
    "greenhouse": (probe_greenhouse, 1),
    "lever": (probe_lever, 1),
    "workday": (probe_workday, (3, 4)),  # (tenant, dc, site[, label]) -> 3 required, 4th optional
    "smartrecruiters": (probe_smartrecruiters, 1),
    "neogov": (probe_neogov, 1),
}


def probe_one(provider: str, args: "list[str]") -> ProbeResult:
    if provider not in DISPATCH:
        r = ProbeResult(provider, provider, ",".join(args))
        r.error = f"unknown provider type {provider!r} (expected one of {sorted(DISPATCH)})"
        return r
    fn, arity = DISPATCH[provider]
    lo, hi = arity if isinstance(arity, tuple) else (arity, arity)
    if not (lo <= len(args) <= hi):
        r = ProbeResult(provider, provider, ",".join(args))
        r.error = f"{provider} expects {lo}-{hi} args, got {len(args)}: {args!r}"
        return r
    return fn(*args)


def render_summary(results: "list[ProbeResult]") -> str:
    lines = [
        "## ATS candidate probe results",
        "",
        "| provider | identifier | status | count / error |",
        "|---|---|---|---|",
    ]
    lines.extend(r.as_row() for r in results)
    lines.append("")
    passed = [r for r in results if r.ok and r.count > 0]
    lines.append(
        f"**{len(passed)}/{len(results)} candidate(s) returned 200 + rows.** "
        "Per CLAUDE.md: only add a board token to providers.py after confirming "
        "200+jobs live AND the metro+admin filters keep some rows."
    )
    return "\n".join(lines)


def main(argv: "list[str]") -> int:
    """argv is sys.argv[1:]: `<provider> <identifier...>`."""
    if len(argv) < 2:
        print(__doc__)
        return 1
    provider, *args = argv
    result = probe_one(provider, args)
    print(render_summary([result]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

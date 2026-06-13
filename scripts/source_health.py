#!/usr/bin/env python
"""Probe every keyless job source the scanner relies on and report health.

Self-evolving signal: run weekly in CI. Classification is deliberately
conservative so it never cries wolf:
  - GONE  (HTTP 404/410): the board was removed/renamed -> exit non-zero so the
          workflow opens an issue to fix or drop it. This is the real signal.
  - FLAKY (empty body / JSONDecode / network / 429 / 5xx, twice): big ATSs
          (Workday/Akamai, governmentjobs.com) bot-throttle rapid requests and
          return empty bodies — that is NOT "dead", so it is reported but does
          NOT fail the run. The spaced-out daily scan still gets these sources.
  - OK / EMPTY: reachable; empty just means no current openings.
Reuses providers' allowlisted request helpers — no new network code. Stdlib only.
"""

import sys
import time

sys.path.insert(0, ".")
import providers as p  # noqa: E402

ok, gone, flaky = [], [], []


def probe(label, fn):
    last = ""
    for attempt in (1, 2):
        try:
            n = fn()
            ok.append((label, n))
            print(f"  {'OK   ' if n else 'EMPTY'} {label:42s} {n} postings")
            return
        except Exception as e:  # noqa: BLE001 - classify and continue
            last = type(e).__name__ + ": " + str(e)[:90]
            msg = str(e)
            if "HTTP 404" in msg or "HTTP 410" in msg:
                gone.append((label, last))
                print(f"  GONE  {label:42s} {last}")
                return
            if attempt == 1:
                time.sleep(6)  # back off once; throttling clears
    flaky.append((label, last))
    print(f"  FLAKY {label:42s} {last}")


def main():
    print("== Greenhouse ==")
    for t in p.ATS_BOARDS.get("greenhouse", []):
        probe(
            "greenhouse/" + t,
            lambda t=t: len((p._request_json(f"{p.GREENHOUSE_HOST}{t}/jobs") or {}).get("jobs") or []),
        )
        time.sleep(1.5)
    print("== Lever ==")
    for t in p.ATS_BOARDS.get("lever", []):
        probe("lever/" + t, lambda t=t: len(p._request_json(f"{p.LEVER_HOST}{t}?mode=json") or []))
        time.sleep(1.5)
    print("== NEOGOV (government) ==")
    for slug, _ in p.NEOGOV_AGENCIES:
        probe(
            "neogov/" + slug,
            lambda slug=slug: p._request_text(f"{p.NEOGOV_FEED}?agency={slug}").count("<item>"),
        )
        time.sleep(2)
    print("== Workday ==")
    for tenant, dc, site, _ in p.WORKDAY_BOARDS:
        host = f"https://{tenant}.{dc}.myworkdayjobs.com"
        probe(
            "workday/" + tenant,
            lambda tenant=tenant, dc=dc, site=site, host=host: len(
                (
                    p._request_json(
                        f"{host}/wday/cxs/{tenant}/{site}/jobs",
                        headers={"Accept": "application/json"},
                        body={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": "administrative"},
                        allowed_prefixes=(host + "/",),
                    )
                    or {}
                ).get("jobPostings")
                or []
            ),
        )
        time.sleep(2)
    print("== SmartRecruiters ==")
    for ident, _ in p.SMARTRECRUITERS_COMPANIES:
        probe(
            "smartrecruiters/" + ident,
            lambda ident=ident: (
                p._request_json(f"{p.SMARTRECRUITERS_HOST}{ident}/postings?limit=1") or {}
            ).get("totalFound", 0),
        )
        time.sleep(1.5)

    print(f"\n{len(ok)} reachable, {len(flaky)} flaky/throttled, {len(gone)} GONE")
    if flaky:
        print("Flaky (bot-throttled, NOT failing the run): " + ", ".join(lbl for lbl, _ in flaky))
    empty = [lbl for lbl, n in ok if not n]
    if empty:
        print("Reachable but empty (no current openings): " + ", ".join(empty))
    if gone:
        print("\nGONE — fix or remove from providers.py:")
        for label, why in gone:
            print(f"  - {label}: {why}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

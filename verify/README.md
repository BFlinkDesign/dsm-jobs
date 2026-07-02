# verify/ — the camera (self-verifier)

`camera.py` builds the PWA, renders it in Playwright's bundled Chromium when
available and falls back to installed Chrome, photographs each view, and
inspects the live DOM against the load-bearing invariants. It's the loop's eyes:
run it after any change; exit 0 means every check passed.

```bash
pip install -r verify/requirements.txt   # playwright (verify-only; NOT a runtime dep)
python verify/camera.py                   # --mock scan + `cd app && npm run build` + shoot + inspect at /dsm-jobs/
python verify/camera.py --no-build        # inspect the existing web/ build
```

If Playwright's bundled Chromium is unavailable on this box, the camera launches
installed Chrome via `channel="chrome"`. (`pip install playwright` is enough for
that fallback; install the bundled browser when you need fully pinned pixels.)

## What it checks (8)

| Check | What it proves |
|---|---|
| `header_scam_checked` | header shows "N safe jobs · scam-checked" |
| `filter_rows_labeled` | the 3 pill rows are labeled (Filter / How far…) |
| `commute_chips` | radius chooser renders Any/20/30/45 with exactly one selected |
| `job_cards_render` | ≥1 card with a pay badge, "What you'd do", an Apply affordance |
| `no_render_garbage` | no `undefined`/`NaN`/`[object Object]`/`$None` in **visible** text; no unfilled `##TOKEN##` in the HTML |
| `invariant1_no_predicted_dollar` | **#1** — embedded data is clean *and* no card shows a `$` pay next to "Pay not listed" |
| `nav_switches_views` | bottom nav actually switches Jobs/Today/My corner/Help |
| `auth_dom_provider_aware` | auth modal + email/password present; Google button hidden (only enabled providers show) |

Outputs: `verify/shots/*.png` (the photos) + `verify/report.json` (machine
result). Both are generated and gitignored.

## Why visible-text vs raw-HTML matters

`undefined`/`NaN` are legitimate JS tokens in the inline `<script>`, so the
garbage scan reads **`innerText`** (what the user sees) for value-leaks and
reserves the raw-HTML scan for the distinctive `##TOKEN##` placeholders. A naive
`innerHTML` scan false-positives on the app's own code.

## Optional: run it in CI

GitHub `ubuntu-latest` ships Chrome; a job can `pip install -r
verify/requirements.txt && python verify/camera.py`. Left out of the required
gate for now (browser jobs are slower / occasionally flaky) — wire it as a
non-blocking check first, then promote once it's proven stable.

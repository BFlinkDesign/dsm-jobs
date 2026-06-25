#!/usr/bin/env python
"""Camera — a self-verifier for the dsm-jobs PWA.

Builds the Astro app (--mock data + npm build), renders it in REAL Chrome,
photographs each view, and inspects the live DOM against the load-bearing
invariants. Exit 0 iff EVERY check passes; otherwise prints the failures
and exits 1. Re-run it after any change — it is the loop's eyes.

Verify-only tool (NOT a runtime dependency). DETERMINISTIC by design: it drives
Playwright's BUNDLED Chromium, pinned to the playwright version in
verify/requirements.txt, against the canned --mock build — so the rendered
pixels are reproducible across runs and machines (CI or local). Setup:
    pip install -r verify/requirements.txt
    python -m playwright install --with-deps chromium    # the pinned revision

    python verify/camera.py            # build + shoot + inspect
    python verify/camera.py --no-build # inspect the existing web/ build
"""

import http.server
import json
import shutil
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
APP = ROOT / "app"
SHOTS = ROOT / "verify" / "shots"
BASE_PATH = "/dsm-jobs/"

# Empty-value / render-bug leakage that must never appear in VISIBLE text
# (innerText). Scanned against innerText, NOT innerHTML — the inline <script>
# legitimately contains the JS tokens "undefined"/"NaN" (typeof, isNaN, ...).
VALUE_LEAK = ["undefined", "NaN", "[object Object]", "$None", "~None min", "None min drive"]
# Unfilled template tokens — distinctive enough to scan the raw HTML safely.
TEMPLATE_TOKENS = ["##JOBS##", "##META##", "##PORTAL##", "##PORTAL_SCRIPT##", "##SENTRY##"]


def build():
    r = subprocess.run(
        [sys.executable, str(ROOT / "find_admin_jobs.py"), "--mock"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        raise SystemExit(f"mock scan failed: {r.stderr[-400:]}")

    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise SystemExit("npm not found — install Node.js to build the Astro app")
    r2 = subprocess.run(
        [npm, "run", "build"],
        cwd=str(APP),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if r2.returncode != 0:
        raise SystemExit(f"astro build failed: {r2.stderr[-400:] or r2.stdout[-400:]}")


def serve():
    base = BASE_PATH.rstrip("/")

    class BasePathHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(WEB), **kwargs)

        def translate_path(self, path):
            path = path.split("?", 1)[0]
            path = path.split("#", 1)[0]
            if path == base or path.startswith(base + "/"):
                path = path[len(base):] or "/"
            return super().translate_path(path)

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", 0), BasePathHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


# ── checks: each returns (name, ok, detail) ─────────────────────────────────


def check(name, ok, detail=""):
    return {"name": name, "ok": bool(ok), "detail": str(detail)[:300]}


def inspect(page):
    out = []

    page.wait_for_selector("#view-host", timeout=15000)
    page.wait_for_selector("#jobs-list .job-card", timeout=15000)

    meta_txt = page.inner_text("#meta-generated")
    out.append(
        check(
            "header_scam_checked",
            "safe job" in meta_txt.lower() and "scam-checked" in meta_txt.lower(),
            f"meta={meta_txt[:80]}",
        )
    )

    labels = page.eval_on_selector_all(".filter-label", "els => els.map(e => e.textContent.trim())")
    out.append(
        check(
            "filter_rows_labeled",
            any("Filter" in x for x in labels)
            and any("Job type" in x for x in labels)
            and any("How far" in x for x in labels),
            labels,
        )
    )

    bands = page.eval_on_selector_all(
        "#filter-commute .chip",
        "els => els.map(e => ({t:e.textContent.trim(), on:e.classList.contains('on')}))",
    )
    band_txt = [b["t"] for b in bands]
    pressed = [b for b in bands if b["on"]]
    out.append(
        check(
            "commute_chips",
            all(w in band_txt for w in ["Any distance", "Within 20 min", "Within 30 min", "Within 45 min"])
            and len(pressed) == 1,
            band_txt,
        )
    )

    n_cards = page.eval_on_selector_all("#jobs-list .job-card", "els => els.length")
    has_about = page.eval_on_selector_all("#jobs-list .job-card .job-meta", "els => els.length") > 0
    has_apply = page.locator("#jobs-list").inner_text().lower().count("sign in") >= 1
    out.append(
        check(
            "job_cards_render",
            n_cards >= 1 and has_about and has_apply,
            f"cards={n_cards} about={has_about} sign-in-cta={has_apply}",
        )
    )

    vis = page.eval_on_selector("body", "el => el.innerText")
    html = page.eval_on_selector("body", "el => el.innerHTML")
    hits = [g for g in VALUE_LEAK if g in vis] + [t for t in TEMPLATE_TOKENS if t in html]
    out.append(check("no_render_garbage", not hits, f"visible-leak/token: {hits}"))

    # Invariant #1: jobs.json must not pair "Pay not listed" with good/payNum,
    # and no card shows $ digits next to "Pay not listed".
    inv = page.evaluate(f"""async () => {{
        const base = {json.dumps(BASE_PATH)};
        const jobs = await fetch(base + 'jobs.json').then(r => r.json());
        const bad = [];
        for (const j of jobs) {{
          if (j.pay === 'Pay not listed' && (j.good === true || j.payNum > 0)) bad.push('listed-as-unlisted:' + j.id);
          if (j.good === true && !/\\$\\d/.test(j.pay || '')) bad.push('good-without-number:' + j.id);
        }}
        let conflict = false;
        document.querySelectorAll('#jobs-list .job-card').forEach(c => {{
          const t = c.innerText || '';
          if (/\\$\\d/.test(t) && /Pay not listed/.test(t)) conflict = true;
        }});
        return {{ jobs: jobs.length, bad, conflict }};
    }}""")
    out.append(check("invariant1_no_predicted_dollar", not inv["bad"] and not inv["conflict"], inv))

    # Bottom-nav view switching. Signed-out: Today/Apps/Corner show lock screen.
    nav = {}
    for view, key in [("today", "today_locked"), ("apps", "apps_locked"), ("corner", "corner_locked")]:
        page.click(f'.nav-bottom .tab[data-view="{view}"]')
        page.wait_for_timeout(150)
        nav[key] = page.query_selector("#view-host .lock-screen") is not None
    page.click('.nav-bottom .tab[data-view="help"]')
    page.wait_for_timeout(150)
    nav["help"] = page.inner_text("#view-host").lower().count("stays safe") >= 1
    page.click('.nav-bottom .tab[data-view="jobs"]')
    page.wait_for_timeout(150)
    nav["jobs"] = page.query_selector("#jobs-list") is not None
    out.append(check("nav_switches_views", all(nav.values()), nav))

    auth = page.evaluate("""() => ({
        modal: !!document.getElementById('auth-modal'),
        email: !!document.getElementById('auth-email'),
        pass: !!document.getElementById('auth-pass'),
        googleHidden: (document.getElementById('auth-google') || {}).hidden === true,
    })""")
    out.append(
        check(
            "auth_dom_provider_aware",
            auth["modal"] and auth["email"] and auth["pass"] and auth["googleHidden"],
            auth,
        )
    )
    return out


def shoot(page):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.click('.nav-bottom .tab[data-view="jobs"]')
    page.wait_for_timeout(150)
    page.screenshot(path=str(SHOTS / "01-jobs.png"), full_page=False)
    page.screenshot(path=str(SHOTS / "01-jobs-full.png"), full_page=True)
    for view, fn in [("today", "02-today"), ("corner", "03-corner"), ("help", "04-help")]:
        page.click(f'.nav-bottom .tab[data-view="{view}"]')
        page.wait_for_timeout(200)
        page.screenshot(path=str(SHOTS / f"{fn}.png"), full_page=True)
    page.click('.nav-bottom .tab[data-view="jobs"]')
    page.wait_for_timeout(150)


def extra_shots(page):
    """Signed-in surfaces. Force the `authed` class to reveal premium UI."""
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.evaluate("document.body.classList.add('authed')")
    page.click('.nav-bottom .tab[data-view="jobs"]')
    page.wait_for_timeout(150)
    page.screenshot(path=str(SHOTS / "05-jobs-authed.png"), full_page=False)
    page.click('.nav-bottom .tab[data-view="corner"]')
    page.wait_for_timeout(200)
    page.screenshot(path=str(SHOTS / "06-corner-authed.png"), full_page=True)
    opened = page.evaluate(
        """() => {
            const ov = document.getElementById('rudy-overlay');
            if (!ov) return '';
            ov.hidden = false;
            document.body.style.overflow = 'hidden';
            return 'forced'; }"""
    )
    if opened:
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "07-rudy.png"), full_page=False)
    return opened


def main():
    from playwright.sync_api import sync_playwright

    if "--no-build" not in sys.argv:
        build()
    httpd, port = serve()
    app_url = f"http://127.0.0.1:{port}{BASE_PATH}"
    try:
        with sync_playwright() as p:
            launch_args = ["--force-device-scale-factor=1", "--hide-scrollbars"]
            try:
                b = p.chromium.launch(headless=True, args=launch_args)
            except Exception:
                b = p.chromium.launch(channel="chrome", headless=True, args=launch_args)
            page = b.new_page(
                viewport={"width": 430, "height": 932},
                device_scale_factor=1,
                reduced_motion="reduce",
            )
            page.add_init_script(
                "(function(){var s=0x2545F491;Math.random=function(){"
                "s|=0;s=s+0x6D2B79F5|0;var t=Math.imul(s^s>>>15,1|s);"
                "t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};"
                "var FIXED=1781913600000,R=Date;function F(a,b,c,d,e,f,g){"
                "switch(arguments.length){case 0:return new R(FIXED);"
                "case 1:return new R(a);default:return new R(a,b,c,d,e,f,g);}}"
                "F.now=function(){return FIXED;};F.parse=R.parse;F.UTC=R.UTC;"
                "F.prototype=R.prototype;Date=F;window.Date=F;})();"
            )
            page.goto(app_url, wait_until="networkidle")
            page.add_style_tag(content=(
                "*,*::before,*::after{animation:none!important;transition:none!important;"
                "animation-duration:0s!important;caret-color:transparent!important;"
                "scroll-behavior:auto!important}"
            ))
            try:
                page.evaluate("document.fonts && document.fonts.ready")
            except Exception:  # noqa: BLE001 — fonts API is best-effort
                pass
            page.wait_for_timeout(120)
            results = inspect(page)
            shoot(page)
            try:
                extra_shots(page)
            except Exception as e:  # noqa: BLE001 — extra design shots are best-effort
                print(f"  (extra_shots skipped: {e})")
            b.close()
    finally:
        httpd.shutdown()

    SHOTS.mkdir(parents=True, exist_ok=True)
    (ROOT / "verify" / "report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    passed = sum(1 for r in results if r["ok"])
    print(f"\nCAMERA: {passed}/{len(results)} checks passed  (shots -> verify/shots/)")
    for r in results:
        print(f"  [{'PASS' if r['ok'] else 'FAIL'}] {r['name']}" + ("" if r["ok"] else f"  -> {r['detail']}"))
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()

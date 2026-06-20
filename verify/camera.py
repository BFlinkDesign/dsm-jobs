#!/usr/bin/env python
"""Camera — a self-verifier for the dsm-jobs PWA.

Builds the page (--mock), renders it in REAL Chrome, photographs each view, and
inspects the live DOM against the load-bearing invariants. Exit 0 iff EVERY check
passes; otherwise prints the failures and exits 1. Re-run it after any change —
it is the loop's eyes.

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
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SHOTS = ROOT / "verify" / "shots"

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
        raise SystemExit(f"build failed: {r.stderr[-400:]}")


def serve():
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(WEB), **k)  # noqa: E731
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)  # 0 = OS picks a free port
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


# ── checks: each returns (name, ok, detail) ─────────────────────────────────


def check(name, ok, detail=""):
    return {"name": name, "ok": bool(ok), "detail": str(detail)[:300]}


def inspect(page):
    out = []
    summ = page.inner_text("#summary")
    out.append(
        check(
            "header_scam_checked", "safe jobs" in summ.lower() and "scam-checked" in summ.lower(), summ[:80]
        )
    )

    labels = page.eval_on_selector_all(".chiplabel", "els => els.map(e => e.textContent.trim())")
    out.append(
        check(
            "filter_rows_labeled",
            any("Filter" == x for x in labels) and any("How far" in x for x in labels),
            labels,
        )
    )

    bands = page.eval_on_selector_all(
        "#commutechips button",
        "els => els.map(e => ({t:e.textContent.trim(), p:e.getAttribute('aria-pressed')}))",
    )
    band_txt = [b["t"] for b in bands]
    pressed = [b for b in bands if b["p"] == "true"]
    out.append(
        check(
            "commute_chips",
            all(
                w in " | ".join(band_txt)
                for w in ["Any distance", "Within 20 min", "Within 30 min", "Within 45 min"]
            )
            and len(pressed) == 1,
            band_txt,
        )
    )

    page.wait_for_selector("#list .pill", timeout=10000)
    n_cards = page.eval_on_selector_all("#list .pill", "els => els.length")
    has_about = page.eval_on_selector_all("#list .about", "els => els.length") > 0
    has_apply = page.locator("#list").inner_text().lower().count("apply") >= 1
    out.append(
        check(
            "job_cards_render",
            n_cards >= 1 and has_about and has_apply,
            f"pills={n_cards} about={has_about} apply={has_apply}",
        )
    )

    vis = page.eval_on_selector("body", "el => el.innerText")
    html = page.eval_on_selector("body", "el => el.innerHTML")
    hits = [g for g in VALUE_LEAK if g in vis] + [t for t in TEMPLATE_TOKENS if t in html]
    out.append(check("no_render_garbage", not hits, f"visible-leak/token: {hits}"))

    # Invariant #1 (camera): the embedded data the page renders from is clean,
    # AND no rendered card shows a $ pay next to "Pay not listed".
    inv = page.evaluate("""() => {
        const jobs = window.JOBS || [];
        const bad = [];
        for (const j of jobs) {
          if (j.pay === 'Pay not listed' && (j.good === true || j.payNum > 0)) bad.push('listed-as-unlisted:' + j.id);
          if (j.good === true && !/\\$\\d/.test(j.pay || '')) bad.push('good-without-number:' + j.id);
        }
        let conflict = false;
        document.querySelectorAll('#list .card, #list > *').forEach(c => {
          const t = c.innerText || '';
          if (/\\$\\d/.test(t) && /Pay not listed/.test(t)) conflict = true;
        });
        return { jobs: jobs.length, bad, conflict };
    }""")
    out.append(check("invariant1_no_predicted_dollar", not inv["bad"] and not inv["conflict"], inv))

    # Bottom-nav view switching. Signed-out (the mock build has no session),
    # Today + My corner are gated and show the benefits screen (#lockwrap), not
    # their own content; Jobs and Help stay open.
    nav = {}
    page.click("#nav-today")
    page.wait_for_timeout(150)
    nav["today_locked"] = not page.eval_on_selector("#lockwrap", "el => el.hidden")
    page.click("#nav-corner")
    page.wait_for_timeout(150)
    nav["corner_locked"] = not page.eval_on_selector("#lockwrap", "el => el.hidden")
    page.click("#nav-help")
    page.wait_for_timeout(150)
    nav["help"] = not page.eval_on_selector("#faqwrap", "el => el.hidden")
    page.click("#nav-jobs")
    page.wait_for_timeout(150)
    nav["jobs"] = page.eval_on_selector("#list", "el => !el.hidden")
    out.append(check("nav_switches_views", all(nav.values()), nav))

    # Auth DOM present + provider-aware: Google hidden, email/pass present.
    auth = page.evaluate("""() => ({
        modal: !!document.getElementById('authmodal'),
        email: !!document.getElementById('authemail'),
        pass: !!document.getElementById('authpass'),
        googleHidden: (document.getElementById('authgoogle') || {}).hidden === true,
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
    page.click("#nav-jobs")
    page.wait_for_timeout(150)
    page.screenshot(path=str(SHOTS / "01-jobs.png"), full_page=False)
    page.screenshot(path=str(SHOTS / "01-jobs-full.png"), full_page=True)
    for tab, fn in [("nav-today", "02-today"), ("nav-corner", "03-corner"), ("nav-help", "04-help")]:
        page.click(f"#{tab}")
        page.wait_for_timeout(200)
        page.screenshot(path=str(SHOTS / f"{fn}.png"), full_page=True)
    page.click("#nav-jobs")
    page.wait_for_timeout(150)


def extra_shots(page):
    """Signed-in surfaces. The mock build has no portal, so force the `authed`
    class to reveal the premium UI (résumé card, Ruby companion) and force-open
    the Ruby overlay — for a visual design pass, not a functional one."""
    SHOTS.mkdir(parents=True, exist_ok=True)
    # A job card while signed in (real Apply/actions instead of the lock CTA).
    page.evaluate("document.querySelector('.app').classList.add('authed')")
    page.click("#nav-jobs")
    page.wait_for_timeout(150)
    page.screenshot(path=str(SHOTS / "05-jobs-authed.png"), full_page=False)
    # My corner, signed in: Ruby intro card + résumé card + companion.
    page.click("#nav-corner")
    page.wait_for_timeout(200)
    page.screenshot(path=str(SHOTS / "06-corner-authed.png"), full_page=True)
    # Open the Ruby full-screen companion. Its launcher handler only exists in
    # the signed-in portal script (absent in the mock build), so force the
    # overlay visible directly to photograph its styling.
    opened = page.evaluate(
        """() => {
            const ov = document.getElementById('rubyov');
            if (!ov) return '';
            ov.hidden = false;
            document.body.style.overflow = 'hidden';
            return 'forced'; }"""
    )
    if opened:
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "07-ruby.png"), full_page=False)
    return opened


def main():
    from playwright.sync_api import sync_playwright

    if "--no-build" not in sys.argv:
        build()
    httpd, port = serve()
    try:
        with sync_playwright() as p:
            # DETERMINISTIC by construction: Playwright's BUNDLED Chromium is
            # pinned to the playwright version in verify/requirements.txt
            # (==1.58.0 -> one fixed Chromium revision), NOT system Chrome (which
            # drifts). Fixed viewport + device scale + reduced-motion + frozen
            # animations + canned --mock data => the same pixels every run, in CI
            # or locally. No browser-autodetect, no fallback cascade: if the
            # pinned Chromium isn't installed, this raises (a clear, repeatable
            # failure) rather than silently using a different browser.
            b = p.chromium.launch(headless=True, args=["--force-device-scale-factor=1", "--hide-scrollbars"])
            page = b.new_page(
                viewport={"width": 430, "height": 932},
                device_scale_factor=1,
                reduced_motion="reduce",
            )
            # Seed Math.random to a FIXED value before any page script runs, so
            # the rotating "— Daddy" affirmations (pickEnc) and any other RNG are
            # reproducible — otherwise full-page shots that capture the footer
            # phrase differ every render. This is camera-only (a seeded mulberry32);
            # production randomness is untouched.
            page.add_init_script(
                "(function(){var s=0x2545F491;Math.random=function(){"
                "s|=0;s=s+0x6D2B79F5|0;var t=Math.imul(s^s>>>15,1|s);"
                "t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};})();"
            )
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            # Freeze every animation/transition + the text caret, and wait for
            # web fonts, so a screenshot can never catch a mid-animation frame.
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

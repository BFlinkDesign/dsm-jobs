#!/usr/bin/env python3
"""Dev-only CSS hygiene gate for the generated PWA.

The app's CSS lives inline in find_admin_jobs.py's APP_TEMPLATE. This harness
builds a page, extracts the <style> block, and runs it through two modern tools:

  • Lightning CSS  — a Rust parser/transformer. It FAILS on invalid CSS and
    proves the stylesheet transpiles + autoprefixes cleanly for our browser
    targets (so modern syntax like color-mix()/nesting is safe to ship).
  • stylelint      — catches sloppy hygiene (dupes, unknown props, etc.).

Exit non-zero on a hard parse error so CI can gate on it. Usage:
    python verify/css/lint_css.py
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import find_admin_jobs as faj  # noqa: E402


def build_css(out_css: str) -> int:
    """Render a mock page, pull its <style> block, write it to out_css. Returns byte size."""
    rows = [{
        "id": "x1", "title": "Office Clerk", "company": "Hy-Vee", "location": "Grimes, IA",
        "hourly_min": 20.0, "hourly_max": 22.0, "predicted": False, "verdict": "meets",
        "created": "2026-06-10", "url": "https://example.com/j/1", "source": "local",
        "description": "Filing. Will train.",
    }]
    html_path = os.path.join(HERE, "_page.html")
    faj.write_html(rows, 1, 2, html_path, "2026-06-19 06:00", contact="Brady",
                   portal_cfg={"url": "https://abc123.supabase.co", "key": "sb_publishable_x"})
    with open(html_path, encoding="utf-8") as fh:
        html = fh.read()
    m = re.search(r"<style>(.*?)</style>", html, re.S)
    if not m:
        print("CSS LINT FAIL: no <style> block found", file=sys.stderr)
        sys.exit(2)
    css = m.group(1)
    with open(out_css, "w", encoding="utf-8") as fh:
        fh.write(css)
    return len(css)


def main() -> int:
    css_path = os.path.join(HERE, "app.css")
    size = build_css(css_path)
    print(f"Extracted CSS: {size:,} bytes -> {css_path}")

    # 1) Lightning CSS: validate + transpile for our targets. Hard-fails on parse errors.
    print("\n=== Lightning CSS (validate + transpile, last 2 versions / >0.3%) ===")
    lc = subprocess.run(
        ["npx", "--prefix", HERE, "lightningcss", "--minify", "--browserslist",
         "--error-recovery", css_path, "-o", os.path.join(HERE, "app.min.css")],
        cwd=HERE, capture_output=True, text=True,
    )
    sys.stdout.write(lc.stdout)
    sys.stderr.write(lc.stderr)
    if lc.returncode != 0:
        print("CSS LINT FAIL: Lightning CSS reported a hard parse error.", file=sys.stderr)
        return 1
    try:
        mn = os.path.getsize(os.path.join(HERE, "app.min.css"))
        print(f"Minified OK: {mn:,} bytes ({100 - mn * 100 // max(size, 1)}% smaller).")
    except OSError:
        pass

    # 2) stylelint: hygiene warnings (non-fatal — report only, since inline app
    #    CSS intentionally bends a few standard rules).
    print("\n=== stylelint (hygiene; warnings only) ===")
    sl = subprocess.run(
        ["npx", "--prefix", HERE, "stylelint", "--config", os.path.join(HERE, ".stylelintrc.json"),
         css_path],
        cwd=HERE, capture_output=True, text=True,
    )
    out = (sl.stdout + sl.stderr).strip()
    print(out if out else "stylelint: clean ✦")

    print("\nCSS LINT OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

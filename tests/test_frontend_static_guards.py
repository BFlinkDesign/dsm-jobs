"""Static guards for frontend regressions not covered by Python business tests."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_user_facing_copy_is_professional_safe():
    text = "\n".join(
        _read(path)
        for path in [
            "app/src/pages/index.astro",
            "app/src/scripts/app.ts",
            "app/src/scripts/rudy-sayings.ts",
            "find_admin_jobs.py",
            "scripts/generate_rudy_sayings.py",
        ]
    )
    banned = [
        "Daddy",
        "hot stuff",
        "on my knees",
        "flustered",
        "dirty",
        "undressing",
        "undress",
        "stripping",
        "irresistible",
        "gorgeous",
        "sweetheart",
        "my girl",
        "💋",
        "😏",
        "😉",
    ]
    hits = [term for term in banned if term.lower() in text.lower()]
    assert not hits


def test_rudy_thinking_bubbles_are_bound_by_element_reference():
    text = _read("app/src/scripts/app.ts")
    assert 'id="rudy-think"' not in text
    assert '$("#rudy-think")' not in text
    assert "thinkingBubble" in text


def test_rudy_resume_tailor_uses_mischief_voice_and_sparkler_meter():
    app = _read("app/src/scripts/app.ts")
    css = _read("app/src/styles/app.css")
    sayings = _read("app/src/scripts/rudy-sayings.ts")
    generator = _read("scripts/generate_rudy_sayings.py")

    assert "Rudy tailor résumé" in app
    assert "Rudy tailored this" in app
    assert "Rudy is side-eyeing the buzzwords" in app
    assert "Making the boring form behave" in sayings
    assert "Scammy listing energy detected" in generator
    assert "role=\"progressbar\"" in app
    assert "id=\"tailor-meter\"" in app
    assert "role=\"status\"" in app
    assert "spark-shower" in css
    assert "spark-fall" in css
    assert "route-sweep" in css
    assert "resume-readout" in css


def test_resume_tailor_is_trust_first_with_recovery_paths():
    app = _read("app/src/scripts/app.ts")
    css = _read("app/src/styles/app.css")

    assert "Usually takes 15-30 seconds" in app
    assert "Uses only her saved resume text and this job posting" in app
    assert "Checks the draft for made-up details" in app
    assert "You choose what to copy" in app
    assert "Tailor with pasted posting" in app
    assert "Use title only" in app
    assert "What Rudy changed" in app
    assert "tailor-result-actions" in app
    assert "data-download" in app
    assert "data-tailor-retry" in app
    assert "data-tailor-edit" in app
    assert "friendlyTailorError" in app
    assert ".tailor-trust" in css
    assert ".tailor-changes" in css
    assert ".tailor-result-actions" in css
    assert ".tailor-error" in css


def test_resume_upload_parser_has_explicit_formats_and_clear_fallbacks():
    resume = _read("app/src/scripts/resume.ts")
    app = _read("app/src/scripts/app.ts")

    assert "PDFJS_SRI" in resume
    assert "PDFJS_WORKER_SRI" in resume
    assert "word/document.xml" in resume
    assert "Old .doc files aren't supported" in resume
    assert "Use a .docx, .pdf, .md, or .txt file" in resume
    assert "a scanned PDF, maybe?" in app


def test_resume_tailor_function_requires_jwt_in_supabase_config():
    cfg = _read("supabase/config.toml")
    assert "[functions.resume-tailor]" in cfg
    section = cfg.split("[functions.resume-tailor]", 1)[1].split("[", 1)[0]
    assert "verify_jwt = true" in section


def test_chat_local_storage_is_user_scoped_when_signed_in():
    autosave = _read("app/src/scripts/autosave.ts")
    app = _read("app/src/scripts/app.ts")
    assert "function chatLocalKey()" in autosave
    assert "`${CHAT_LS_KEY}:${userId}`" in autosave
    assert "appendChatToLocal" in app
    assert 'localStorage.getItem("dsm-jobs-chat"' not in app


def test_filter_panel_is_collapsible_and_persisted():
    app = _read("app/src/scripts/app.ts")
    css = _read("app/src/styles/app.css")
    assert 'id="filter-toggle"' in app
    assert 'aria-controls="filter-panel"' in app
    assert "dsm-jobs-filters-expanded" in app
    assert ".filter-panel.is-collapsed" in css


def test_frontend_ci_uses_exact_node_engine_floor_and_generator():
    ci = _read(".github/workflows/ci.yml")
    scan = _read(".github/workflows/scan.yml")
    assert 'node-version: "22.12"' in ci
    assert 'node-version: "22.12"' in scan
    assert "python scripts/generate_rudy_sayings.py" in ci
    assert "python scripts/generate_rudy_sayings.py" in scan


def test_pre_publish_gate_checks_meta_json_before_reading():
    scan = _read(".github/workflows/scan.yml")
    assert 'mp = web / "meta.json"' in scan
    assert "if not mp.exists()" in scan
    assert 'mp.read_text(encoding="utf-8")' in scan


def test_health_monitor_reads_published_meta_json_for_freshness():
    health = _read(".github/workflows/health.yml")
    assert 'META="published/meta.json"' in health
    assert 'grep -oE \'"generated": *"[0-9]{4}-[0-9]{2}-[0-9]{2}\' "$META"' in health
    assert 'grep -oE \'"generated": *"[0-9]{4}-[0-9]{2}-[0-9]{2}\' "$PAGE"' not in health


def test_mobile_bottom_nav_does_not_show_content_underneath():
    css = _read("app/src/styles/app.css")
    assert "calc(var(--nav-h) + var(--safe-bottom) + var(--space-8))" in css
    assert ".nav-bottom" in css
    assert "background: var(--paper);" in css
    assert "box-shadow: 0 -12px 28px" in css


def test_service_worker_only_caches_same_origin_gets():
    sw = _read("app/public/sw.js")
    assert "const url = new URL(req.url);" in sw
    assert "if (url.origin !== self.location.origin) return;" in sw


def test_github_pages_serves_astro_underscore_assets():
    assert (ROOT / "app/public/.nojekyll").is_file()

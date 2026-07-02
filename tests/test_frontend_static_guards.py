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
        "hot stuff",
        "on my knees",
        "flustered",
        "horny",
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


def test_rudy_voice_and_spicy_modes_are_explicit_opt_in():
    app = _read("app/src/scripts/app.ts")
    page = _read("app/src/pages/index.astro")
    fn = _read("supabase/functions/companion/index.ts")

    # Rudy's read-aloud voice is opt-in: the toggle defaults to off
    # (aria-pressed="false") and its visible state label reads "Off".
    assert 'id="rudy-spk" aria-pressed="false"' in page
    assert 'id="rudy-spk-state">Off' in page
    assert 'localStorage.getItem("rudySpeak") === "1"' in app
    assert 'localStorage.getItem("rudySpicy") === "1"' in app
    assert 'const body: Record<string, unknown> = { message: msg, spicy: spicyOn }' in app
    assert "Spicy mode is off" in page
    assert "body?.spicy === true" in fn
    assert "Spicy never means sexual" in fn
    assert "HARD RULES, crisis routing, anti-confabulation" in fn
    assert "private life-and-job app that Daddy built" in fn
    assert '/^me$/i.test(rawWho) ? "Brady" : rawWho' in app


def test_rudy_chat_is_document_and_job_aware():
    """Grounded chat context (résumé doc / active job) rides along ONLY when
    active, and the companion function's anti-confabulation instructions still
    hold — see docs/plans/2026-06-27-fable5-task-queue.md section 3."""
    app = _read("app/src/scripts/app.ts")
    astro = _read("app/src/pages/index.astro")
    fn = _read("supabase/functions/companion/index.ts")

    # Frontend: activeResumeDocument() only returns a doc with real text, and
    # sendRudy only adds activeDocument/activeJob to the body when present —
    # a plain chat turn (no doc, no job) sends nothing extra.
    assert "function activeResumeDocument(): ResumeDocument | null" in app
    assert "if (doc) body.activeDocument = { name: doc.name, text: doc.text }" in app
    assert "if (rudyJobContext) {" in app
    assert 'data-ask-rudy="${esc(j.id)}">Ask Rudy about this job</button>' in app
    assert "let rudyJobContext: Job | null = null;" in app
    assert 'id="rudy-job-chip"' in astro

    # Backend: the optional context is parsed and passed through
    # documentContextBlocks — never unconditionally on every turn.
    assert "documentContextBlocks } from \"./doc_context.ts\"" in fn
    assert "documentContextBlocks(activeDoc, activeJob)" in fn
    assert "body?.activeDocument" in fn
    assert "body?.activeJob" in fn

    # Anti-confabulation: the system prompt explicitly says to answer only
    # from the provided document/job text and admit not knowing otherwise —
    # this is the load-bearing rule from CLAUDE.md invariant #8.
    assert "the ONLY source of truth for HER" in fn
    assert "say plainly you don't see that rather than guessing or" in fn
    assert "Never invent a wage: if pay isn't written in the posting" in fn


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
    assert "Filtering out risky listings before they waste your time" in generator
    assert "role=\"progressbar\"" in app
    assert "id=\"tailor-meter\"" in app
    assert "role=\"status\"" in app
    assert "spark-shower" in css
    assert "spark-fall" in css
    assert "route-sweep" in css
    assert "resume-readout" in css
    assert "Scammy listing energy detected" not in app
    assert "Scammy listing energy detected" not in generator


def test_resume_tailor_is_trust_first_with_recovery_paths():
    app = _read("app/src/scripts/app.ts")
    css = _read("app/src/styles/app.css")

    assert "Usually takes 15-30 seconds" in app
    assert "Uses only her saved résumé text and this job posting" in app
    assert "Checks the draft for made-up details" in app
    assert "You choose what to copy" in app
    assert "Tailor with pasted posting" in app
    assert "Use title only" in app
    assert "What Rudy changed" in app
    assert "tailor-result-actions" in app
    assert "data-download" in app
    assert "Download text file</button>" not in app
    assert "data-tailor-retry" in app
    assert "data-tailor-edit" in app
    assert "friendlyTailorError" in app
    assert "extractTailorErrorMessage" in app
    assert "normalizeTailorError" in app
    assert "navigator.clipboard?.writeText(text).then" not in app
    assert ".tailor-trust" in css
    assert ".tailor-changes" in css
    assert ".tailor-result-actions" in css
    assert ".tailor-error" in css


def test_application_pack_is_saved_and_reopenable():
    app = _read("app/src/scripts/app.ts")
    types = _read("app/src/scripts/types.ts")
    store = _read("app/src/scripts/store.ts")
    autosave = _read("app/src/scripts/autosave.ts")

    assert "export type ApplicationPack" in types
    assert "applicationPacks: Record<string, ApplicationPack>" in types
    assert "applicationPacks: {}" in store
    assert "applicationPacks: s.applicationPacks" in autosave
    assert "Save application pack" in app
    assert "data-save-pack" in app
    assert "data-pack" in app
    assert "openApplicationPack" in app
    assert "Follow-up message" in app
    assert "ATS alignment" in app


def test_application_status_has_persistent_undo_and_custom_followup_date():
    app = _read("app/src/scripts/app.ts")
    css = _read("app/src/styles/app.css")

    assert "Undo applied" in app
    assert "data-unapply" in app
    assert "Applied status removed" in app
    assert "data-follow-date" in app
    assert "Follow up on" in app
    assert "fu.done = false" in app
    assert ".follow-date-label" in css


def test_resume_document_manager_preserves_multiple_documents():
    app = _read("app/src/scripts/app.ts")
    types = _read("app/src/scripts/types.ts")
    store = _read("app/src/scripts/store.ts")
    css = _read("app/src/styles/app.css")

    assert "export type ResumeDocument" in types
    assert "documents: ResumeDocument[]" in types
    assert "activeDocumentId: string" in types
    assert "legacyResumeDocument" in store
    assert "Saved résumé" in store
    assert "normalizeProfile" in store
    assert "addResumeDocument" in app
    assert "selectResumeDocument" in app
    assert "removeResumeDocument" in app
    assert "data-doc-active" in app
    assert "data-doc-delete" in app
    assert "Rudy tailors from the selected résumé" in app
    assert "saved resume text" not in app
    assert ".doc-list" in css
    assert ".doc-item.is-active" in css


def test_resume_tailor_edge_function_returns_application_pack_fields():
    fn = _read("supabase/functions/resume-tailor/index.ts")

    assert "follow_up" in fn
    assert "ats_alignment" in fn
    assert "strong_matches" in fn
    assert "suggested_keywords" in fn
    assert "without keyword stuffing or inventing facts" in fn
    assert 'required: ["resume", "changes", "cover_note", "follow_up", "ats_alignment"]' in fn


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
    assert 'PYTHONUNBUFFERED: "1"' in scan
    assert 'timeout 540s python find_admin_jobs.py --contact "Brady" --push-supabase' in scan
    assert "Build mobile job app completed in" in scan


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
    assert "calc(var(--nav-h) + var(--safe-bottom) + var(--space-6))" in css
    assert ".call-btn" in css
    assert "bottom: calc(var(--nav-h) + var(--safe-bottom) + 12px)" in css
    assert ".app-foot .field-hint" in css
    assert ".nav-bottom" in css
    assert "background: var(--paper);" in css
    assert "box-shadow: 0 -12px 28px" in css


def test_ios_pull_to_refresh_has_release_hint_and_duplicate_guard():
    app = _read("app/src/scripts/app.ts")
    assert "function wirePullToRefresh()" in app
    assert "touchmove" in app
    assert "Release to refresh jobs" in app
    assert "Jobs refreshed" in app
    assert "pullRefreshing" in app
    assert "navigator.vibrate?.(10)" in app


def test_service_worker_only_caches_same_origin_gets():
    sw = _read("app/public/sw.js")
    assert "const url = new URL(req.url);" in sw
    assert "if (url.origin !== self.location.origin) return;" in sw


def test_github_pages_serves_astro_underscore_assets():
    assert (ROOT / "app/public/.nojekyll").is_file()

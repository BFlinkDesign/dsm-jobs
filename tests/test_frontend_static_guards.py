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


def test_service_worker_only_caches_same_origin_gets():
    sw = _read("app/public/sw.js")
    assert "const url = new URL(req.url);" in sw
    assert "if (url.origin !== self.location.origin) return;" in sw

"""source_health + auto_prune — offline tests."""

import json
import os
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(root, "scripts"))
import auto_prune_feeds as ap  # noqa: E402
import source_health as sh  # noqa: E402


def test_track_empty_increments_weeks():
    sh.ok.clear()
    sh.ok.append(("neogov/testslug", 0))
    state = {}
    actionable = sh._track_empty_feeds(state)
    assert actionable == []
    assert state["neogov/testslug"]["empty_weeks"] == 1


def test_track_empty_resets_when_postings_return():
    sh.ok.clear()
    sh.ok.append(("neogov/x", 2))
    state = {"neogov/x": {"empty_weeks": 3, "last_checked": "2026-01-01"}}
    assert sh._track_empty_feeds(state) == []
    assert "neogov/x" not in state


def test_actionable_after_four_empty_weeks():
    sh.ok.clear()
    sh.ok.append(("neogov/ankeny", 0))
    state = {"neogov/ankeny": {"empty_weeks": 3, "last_checked": "old"}}
    actionable = sh._track_empty_feeds(state)
    assert "neogov/ankeny" in actionable


def test_auto_prune_neogov_slug(tmp_path, monkeypatch):
    providers = tmp_path / "providers.py"
    providers.write_text(
        'NEOGOV_AGENCIES = [\n'
        '    ("iowa", "State of Iowa"),\n'
        '    ("ankeny", "City of Ankeny"),\n'
        ']\n',
        encoding="utf-8",
    )
    state = tmp_path / "source_health_state.json"
    state.write_text(json.dumps({"neogov/ankeny": {"empty_weeks": 4, "last_checked": "x"}}))
    monkeypatch.setattr(ap, "PROVIDERS_PATH", str(providers))
    monkeypatch.setattr(ap, "STATE_PATH", str(state))
    removed = ap.prune_providers(["ankeny"])
    assert removed == ["neogov/ankeny"]
    text = providers.read_text(encoding="utf-8")
    assert '("ankeny"' not in text
    assert '("iowa"' in text


def test_auto_prune_protects_grimes():
    state = {"neogov/grimes": {"empty_weeks": 10, "last_checked": "x"}}
    assert "grimes" not in ap._prune_candidates(state)


def test_auto_prune_aborts_on_syntax_break(tmp_path, monkeypatch):
    # If a regex prune would leave providers.py unparseable, it must NOT write
    # (the file is committed straight to main — a SyntaxError breaks every scan).
    providers = tmp_path / "providers.py"
    original = (
        'NEOGOV_AGENCIES = [\n'
        '    ("iowa", "State of Iowa"),\n'
        '    ("ankeny", "City of Ankeny"),\n'
        ']\n'
    )
    providers.write_text(original, encoding="utf-8")
    monkeypatch.setattr(ap, "PROVIDERS_PATH", str(providers))
    # Force the post-edit text to be invalid Python, simulating an over-broad match.
    monkeypatch.setattr(ap, "_remove_neogov_slugs",
                        lambda text, slugs: ("def broken(:\n", ["neogov/ankeny"]))
    removed = ap.prune_providers(["ankeny"])
    assert removed == []                                   # aborted
    assert providers.read_text(encoding="utf-8") == original  # file untouched

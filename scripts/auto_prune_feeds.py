#!/usr/bin/env python
"""Remove long-empty job feeds from providers.py — no human in the loop.

Reads scripts/source_health_state.json (written by source_health.py). When a
feed has been EMPTY for EMPTY_PRUNE_WEEKS consecutive weekly probes, drop it from
the matching providers.py list. Core metro feeds are never auto-pruned.
"""

from __future__ import annotations

import json
import os
import re
import sys

STATE_PATH = os.path.join(os.path.dirname(__file__), "source_health_state.json")
PROVIDERS_PATH = os.path.join(os.path.dirname(__file__), "..", "providers.py")
EMPTY_PRUNE_WEEKS = 4

# Never auto-drop: statewide + her city + proven high-trust metro feeds.
_PROTECTED = frozenset({
    "iowa", "desmoines", "grimes", "cityofjohnston", "dallascountyia",
    "urbandale", "waukee", "bondurant", "norwalkiowa",
    "businessolver", "olsson", "aloyoga", "telligen", "momsmeals",
    "athene", "corteva", "nationwide", "godirect", "wellmarkinc",
})


def _load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    with open(STATE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _prune_candidates(state: dict) -> list[str]:
    out = []
    for label, rec in state.items():
        if int(rec.get("empty_weeks", 0)) < EMPTY_PRUNE_WEEKS:
            continue
        slug = label.split("/", 1)[-1].strip().lower()
        if slug in _PROTECTED:
            continue
        out.append(slug)
    return sorted(set(out))


def _remove_neogov_slugs(text: str, slugs: set[str]) -> tuple[str, list[str]]:
    removed = []
    for slug in slugs:
        pat = re.compile(
            r"^\s*\(\"" + re.escape(slug) + r"\",\s*\"[^\"]*\"\),?\s*(#.*)?$",
            re.MULTILINE,
        )
        if pat.search(text):
            text = pat.sub(
                f"    # auto-pruned {slug} ({EMPTY_PRUNE_WEEKS}+ empty weekly checks)\n",
                text,
                count=1,
            )
            removed.append(f"neogov/{slug}")
    return text, removed


def _remove_list_tokens(text: str, list_name: str, prefix: str, slugs: set[str]) -> tuple[str, list[str]]:
    """Drop quoted tokens from ATS_BOARDS sub-lists, greenhouse/lever/workday boards."""
    removed = []
    for slug in slugs:
        label = f"{prefix}/{slug}" if prefix else slug
        if label in _PROTECTED or slug in _PROTECTED:
            continue
        # ("slug", ...) tuple lines inside WORKDAY_BOARDS / SMARTRECRUITERS_COMPANIES
        pat = re.compile(
            r"^\s*\(\"" + re.escape(slug) + r"\"[^\n]*\),?\s*(#.*)?$",
            re.MULTILINE,
        )
        if pat.search(text):
            text = pat.sub(f"    # auto-pruned {slug}\n", text, count=1)
            removed.append(f"{prefix}/{slug}" if prefix else slug)
        # simple string list: "slug",
        pat2 = re.compile(r'^\s*"' + re.escape(slug) + r'",\s*(#.*)?$', re.MULTILINE)
        if pat2.search(text):
            text = pat2.sub(f'    # auto-pruned "{slug}"\n', text, count=1)
            removed.append(f"{prefix}/{slug}" if prefix else slug)
    return text, removed


def prune_providers(slugs: list[str]) -> list[str]:
    if not slugs:
        return []
    with open(PROVIDERS_PATH, encoding="utf-8") as fh:
        text = fh.read()
    slug_set = set(slugs)
    removed: list[str] = []
    text, r = _remove_neogov_slugs(text, slug_set)
    removed.extend(r)
    text, r = _remove_list_tokens(text, "greenhouse", "greenhouse", slug_set)
    removed.extend(r)
    text, r = _remove_list_tokens(text, "lever", "lever", slug_set)
    removed.extend(r)
    if removed:
        with open(PROVIDERS_PATH, "w", encoding="utf-8") as fh:
            fh.write(text)
    return removed


def main() -> int:
    slugs = _prune_candidates(_load_state())
    if not slugs:
        print("auto-prune: nothing to remove")
        return 0
    removed = prune_providers(slugs)
    if removed:
        print("auto-prune: removed", ", ".join(removed))
        return 0
    print("auto-prune: candidates found but no matching lines:", ", ".join(slugs))
    return 0


if __name__ == "__main__":
    sys.exit(main())

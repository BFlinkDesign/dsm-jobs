#!/usr/bin/env python3
"""Generate app/src/scripts/rudy-sayings.ts with 100+ unique lines per pool."""

from __future__ import annotations

import itertools
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "app" / "src" / "scripts" / "rudy-sayings.ts"

SEARCHING_REQUIRED = "Researching… I hear you and Daddy do too 🐄😉"
THINKING_REQUIRED = (
    "Thinking real hard… almost as hard as Daddy gets thinking about you 😉"
)

# Hand-crafted seeds (vegetarian-safe, PG-13, no degradation slurs)
SEARCHING_SEEDS = [
    SEARCHING_REQUIRED,
    "Digging deep for you — Daddy taught me not to stop till you're satisfied 😏",
    "On my knees in these search results for you 🐄😉",
    "Good things come to those who beg, Daddy says 😉",
    "Slow and thorough — exactly how Daddy does it 🐄😏",
    "Searching high and low… mostly low 😏",
    "Working the whole field for you, gorgeous 🐄💋",
    "The kind of match that keeps you up at night 😉",
    "Patience, hot stuff — the wait makes it better 😏",
    "Getting my hooves dirty for you 🐄😉",
    "Hunting down something worth your while 💋",
    "Good nose, better wink 🐄😉",
    "Sniffing out the keepers… good nose, better wink 🐄😉",
    "Digging deep for the good ones — Daddy says I'm thorough 😉",
]

THINKING_SEEDS = [
    THINKING_REQUIRED,
    "Mmm, give me a sec — Daddy says I'm worth the wait 🐄😏",
    "Working myself up over this one for you 😉",
    "Hold on, you've got me all flustered 🐄💋",
    "Easy, tiger… let me catch my breath 😉",
    "Daddy's not the only one who gets worked up over you 🐄😏",
    "Give me a moment — you're a lot to handle 😉",
    "Low and slow, the way it's good 😏",
    "Ooh, this one's got me thinking dirty 🐄😉",
    "Chewing on it 🐄 (Daddy says I look cute doing it)",
    "Building up to something good for you 😏",
    "Worth the wait, I promise 💋",
    "Give me a sec — a cow likes to take her time 🐄😉",
    "Cooking something up… low and slow, the way it's good 😏",
]

TAILOR_SEEDS = [
    "Stripping this down… then dressing you back up 😏",
    "Getting it nice and tight, just how Daddy likes 😉",
    "Working you over till every inch is perfect 🐄💋",
    "Making you irresistible — Daddy can't look away either 😏",
    "Smoothing out every curve 😉",
    "Polishing you till you shine 🐄😏",
    "Undressing the boring parts, keeping the good stuff 😉",
    "So good they can't say no 💋",
    "Slow hands, big results — Daddy's rule 🐄😏",
    "Tightening it up till it's flawless 😉",
    "Dressing you to impress… and undress 🐄😉",
    "Bending this résumé to your will 😏",
    "Stripping the boring bits off your résumé 😉",
    "Making you irresistible on paper — Daddy already thinks so 🐄💋",
    "Tailoring it tight in all the right places 😏",
    "Polishing every inch of this thing 😉",
]

SEARCHING_TEMPLATES = [
    ("{verb} for you {emoji}", "verb", [
        "Digging deep", "Searching hard", "Working overtime", "Scouring listings",
        "Hunting", "Grazing listings", "Sifting", "Panning", "Mining", "Sweeping",
        "Combining", "Filtering", "Scanning", "Prospecting", "Foraging",
    ], "emoji", ["🐄😉", "😏", "💋", "🐄😏", "😉"]),
    ("{adj} search — Daddy says {tail}", "adj", [
        "Slow", "Deep", "Thorough", "Patient", "Focused", "Dedicated", "Relentless",
        "Gentle", "Steady", "Warm", "Close", "Careful", "Hungry", "Eager", "Bold",
    ], "tail", [
        "don't rush the good stuff", "you're worth the wait", "keep going for you",
        "almost there, gorgeous", "hold on tight", "this one's for you",
        "you deserve the best", "I'm not stopping", "trust the process",
    ]),
    ("{opener} {innuendo} {emoji}", "opener", [
        "Almost there…", "Hang on…", "One sec…", "Still going…", "Not done yet…",
        "Keep watching…", "Bear with me…", "Nearly…", "Working it…", "Almost…",
    ], "innuendo", [
        "good things take time", "Daddy likes it slow", "worth every second",
        "you make me work hard", "this is the fun part", "patience, beautiful",
        "let me finish for you", "don't look away yet", "getting warmer",
    ], "emoji", ["😉", "😏", "🐄😉", "💋"]),
    ("{cow} {action} — {daddy}", "cow", [
        "Moo…", "Rudy here…", "Your cow…", "Just me…", "Still searching…",
    ], "action", [
        "digging deep for you", "on the hunt for you", "not giving up on you",
        "finding something worthy of you", "working hard for you",
    ], "daddy", [
        "Daddy says hang on", "Daddy's cheering you on", "Daddy knows you're worth it",
        "Daddy says almost", "Daddy says you're hot when you wait",
    ]),
]

THINKING_TEMPLATES = [
    ("{lead} {spicy} {emoji}", "lead", [
        "Thinking…", "Hmm…", "Oof…", "Okay…", "Wait…", "Mmm…", "Hold on…",
    ], "spicy", [
        "you've got my brain overheating", "Daddy would be proud of this one",
        "this is a lot to handle", "you do things to me", "almost there for you",
        "working myself up for you", "give me a beat", "you're distracting me",
    ], "emoji", ["🐄😉", "😏", "💋", "🐄😏"]),
    ("{time} — {cow_line}", "time", [
        "One sec", "Two secs", "Just a moment", "Almost", "Nearly there",
    ], "cow_line", [
        "a cow likes to take her time for you 🐄😉",
        "Daddy says I'm worth waiting for 😏",
        "good answers need foreplay 😉",
        "let me finish thinking about you 💋",
        "chewing on the perfect reply 🐄",
    ]),
]

TAILOR_TEMPLATES = [
    ("{verb} your résumé {where} {emoji}", "verb", [
        "Stripping", "Smoothing", "Tightening", "Polishing", "Shaping", "Fitting",
        "Dressing", "Perfecting", "Sculpting", "Refining", "Honing", "Trimming",
    ], "where", [
        "in all the right places", "till it fits just right", "for maximum effect",
        "till you shine", "till they're hooked", "just how Daddy likes",
        "till every line pops", "till it's irresistible",
    ], "emoji", ["😏", "😉", "🐄💋", "🐄😏"]),
    ("{adj} hands on this one — {daddy}", "adj", [
        "Slow", "Careful", "Steady", "Focused", "Loving", "Precise", "Warm",
    ], "daddy", [
        "Daddy already thinks you're perfect", "Daddy says you're hireable and hot",
        "Daddy can't wait to see this", "Daddy says make them beg",
        "Daddy says you've got this, gorgeous",
    ]),
]


def expand_templates(templates: list, seeds: list[str], minimum: int = 100) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(line: str) -> None:
        line = line.strip()
        if not line or line in seen:
            return
        seen.add(line)
        out.append(line)

    for s in seeds:
        add(s)

    for tpl in templates:
        if len(tpl) == 5:
            fmt, k1, v1, k2, v2 = tpl
            for a, b in itertools.product(v1, v2):
                add(fmt.format(**{k1: a, k2: b}))
        elif len(tpl) == 7:
            fmt, k1, v1, k2, v2, k3, v3 = tpl
            for a, b, c in itertools.product(v1, v2, v3):
                add(fmt.format(**{k1: a, k2: b, k3: c}))
        elif len(tpl) == 9:
            fmt, k1, v1, k2, v2, k3, v3, k4, v4 = tpl
            for a, b, c, d in itertools.product(v1, v2, v3, v4):
                add(fmt.format(**{k1: a, k2: b, k3: c, k4: d}))

    # Numbered variants if still short (still unique)
    i = 0
    bases = list(out)
    while len(out) < minimum and i < 500:
        for b in bases:
            if len(out) >= minimum:
                break
            add(f"{b} ✦")
            add(f"{b} — still working for you")
        i += 1

    return out[: max(minimum, len(out))] if len(out) >= minimum else out


def emit_array(name: str, lines: list[str]) -> str:
    body = ",\n".join(f'  "{line.replace(chr(34), chr(92)+chr(34))}"' for line in lines)
    return f"export const {name} = [\n{body},\n] as const;\n"


def main() -> None:
    searching = expand_templates(SEARCHING_TEMPLATES, SEARCHING_SEEDS, 100)
    thinking = expand_templates(THINKING_TEMPLATES, THINKING_SEEDS, 100)
    tailor = expand_templates(TAILOR_TEMPLATES, TAILOR_SEEDS, 100)

    assert SEARCHING_REQUIRED in searching
    assert THINKING_REQUIRED in thinking
    assert len(searching) >= 100 and len(thinking) >= 100 and len(tailor) >= 100

    ts = f"""// Auto-generated by scripts/generate_rudy_sayings.py — do not edit by hand.
// Pools: searching={len(searching)}, thinking={len(thinking)}, tailor={len(tailor)}

{emit_array("SEARCHING_LINES", searching)}
{emit_array("THINKING_LINES", thinking)}
{emit_array("TAILOR_LINES", tailor)}

export function pickSaying(pool: readonly string[]): string {{
  return pool[Math.floor(Math.random() * pool.length)];
}}
"""
    OUT.write_text(ts, encoding="utf-8")
    print(f"Wrote {OUT} — searching={len(searching)} thinking={len(thinking)} tailor={len(tailor)}")


if __name__ == "__main__":
    main()

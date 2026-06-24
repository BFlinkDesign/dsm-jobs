#!/usr/bin/env python3
"""Generate app/src/scripts/rudy-sayings.ts with job-search-safe lines."""

from __future__ import annotations

import itertools
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "app" / "src" / "scripts" / "rudy-sayings.ts"

SEARCHING_REQUIRED = "Searching the safe job list for the best matches."
THINKING_REQUIRED = "Thinking through the best next step."

SEARCHING_SEEDS = [
    SEARCHING_REQUIRED,
    "Checking fresh listings and keeping the safe ones up front.",
    "Sorting the job list into a calmer, shorter set.",
    "Looking for roles that fit daytime hours and real employers.",
    "Scanning for training-friendly postings.",
    "Filtering for jobs that look practical today.",
    "Keeping scammy listings out of sight.",
    "Looking for work that respects your time.",
    "Finding options that are worth a closer look.",
    "Checking the commute, pay wording, and employer signals.",
    "Looking for a few strong starts instead of a wall of noise.",
    "Matching the list to what you said matters.",
    "Pay not listed? Side-eye logged.",
    "Filtering out risky listings before they waste your time.",
    "If the commute is nonsense, Rudy is judging it.",
    "Making the job pile behave.",
    "Sorting the chaos before it gets ideas.",
]

THINKING_SEEDS = [
    THINKING_REQUIRED,
    "Reading that carefully before I answer.",
    "Taking a moment to keep this grounded.",
    "Checking what we know and what we should not assume.",
    "Turning that into one useful next step.",
    "Looking for the safest, simplest answer.",
    "Keeping the advice practical and calm.",
    "Checking the details before I respond.",
    "Thinking about what helps today, not someday.",
    "Keeping this focused on your real options.",
    "Making sure I do not overstate anything.",
    "Finding the clearest way to say it.",
    "Rudy is side-eyeing the vague part.",
    "Checking the facts before the confidence gets spicy.",
    "Making the messy bit sit down and explain itself.",
    "Tiny chaos audit in progress.",
]

TAILOR_SEEDS = [
    "Reading the resume and the job posting side by side.",
    "Matching real experience to the job requirements.",
    "Pulling the strongest relevant details forward.",
    "Keeping the wording honest and specific.",
    "Trimming clutter so the useful parts stand out.",
    "Shaping the resume for this role without inventing anything.",
    "Checking that every claim stays grounded in the resume.",
    "Making the application easier to read.",
    "Highlighting transferable skills.",
    "Tightening the language for a hiring manager.",
    "Preparing a clean version to review.",
    "Rudy is side-eyeing the buzzwords.",
    "Making the boring form behave.",
    "Polishing this without lying. Rudy has standards.",
    "Putting the fluff on a short leash.",
    "Turning job-posting nonsense into usable wording.",
]

SEARCHING_TEMPLATES = [
    ("{verb} {target}.", "verb", [
        "Checking", "Reviewing", "Sorting", "Filtering", "Scanning", "Comparing",
        "Prioritizing", "Narrowing", "Reading", "Screening",
    ], "target", [
        "safe local matches", "training-friendly roles", "daytime options",
        "trusted employers", "jobs with practical commute times",
        "new postings", "office and customer support roles", "roles worth saving",
        "listings with clear apply links", "jobs that match your filters",
    ]),
    ("{lead} so the list feels {result}.", "lead", [
        "Removing noise", "Checking employer signals", "Grouping similar jobs",
        "Reading the details", "Sorting by fit", "Reviewing fresh roles",
        "Scanning the commute notes", "Checking pay wording",
    ], "result", [
        "smaller", "safer", "clearer", "less overwhelming", "more useful",
        "easier to compare", "ready to review", "focused",
    ]),
    ("{opener}: {detail}.", "opener", [
        "Quick pass", "Careful pass", "Fresh pass", "Safety pass",
        "Fit check", "Commute check", "Training check", "Employer check",
    ], "detail", [
        "keeping the strongest matches", "hiding anything suspicious",
        "looking for realistic next steps", "checking what changed",
        "finding roles to review first", "making the list easier to act on",
        "looking for good starting points", "staying grounded in the posting",
    ]),
]

THINKING_TEMPLATES = [
    ("{lead} {goal}.", "lead", [
        "Checking", "Reading", "Thinking through", "Grounding", "Sorting out",
        "Reviewing", "Comparing", "Clarifying", "Double-checking", "Framing",
    ], "goal", [
        "the safest answer", "what the posting actually says", "what we know",
        "the next practical step", "the important details", "the best wording",
        "the simplest option", "what not to assume", "the clearest path",
        "the useful part first",
    ]),
    ("{time} - {line}.", "time", [
        "One moment", "Almost there", "Quick check", "Taking a second",
        "Careful read", "Small pause", "Good question", "Working through it",
    ], "line", [
        "I want this to be accurate", "I am keeping it practical",
        "the details matter here", "I am checking the safest next step",
        "I will keep this simple", "I am staying with what we know",
        "I am looking for the useful answer", "I am avoiding guesswork",
    ]),
]

TAILOR_TEMPLATES = [
    ("{verb} the resume {target}.", "verb", [
        "Aligning", "Tightening", "Polishing", "Trimming", "Reframing",
        "Organizing", "Sharpening", "Matching", "Clarifying", "Preparing",
    ], "target", [
        "for this role", "around real experience", "without adding fiction",
        "for easier scanning", "to match the posting", "with clearer bullets",
        "around transferable skills", "for a hiring manager", "with honest wording",
        "so the strongest parts show first",
    ]),
    ("{lead}: {detail}.", "lead", [
        "Resume pass", "Job match", "Cover note pass", "Experience check",
        "Bullet cleanup", "Hiring-manager pass", "Grounding check", "Final polish",
    ], "detail", [
        "real details only", "clearer wording", "no invented claims",
        "stronger first impression", "less clutter", "better fit",
        "specific and honest", "ready to review",
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

    for seed in seeds:
        add(seed)

    for tpl in templates:
        fmt, k1, v1, k2, v2 = tpl
        for a, b in itertools.product(v1, v2):
            add(fmt.format(**{k1: a, k2: b}))

    bases = list(out)
    variants = [
        " Still working.",
        " Almost ready.",
        " Keeping it simple.",
        " Staying careful.",
        " One useful step at a time.",
    ]
    for base, suffix in itertools.product(bases, variants):
        if len(out) >= minimum:
            break
        add(f"{base}{suffix}")

    return out


def emit_array(name: str, lines: list[str]) -> str:
    body = ",\n".join(f'  "{line.replace(chr(34), chr(92) + chr(34))}"' for line in lines)
    return f"export const {name} = [\n{body},\n] as const;\n"


def main() -> None:
    searching = expand_templates(SEARCHING_TEMPLATES, SEARCHING_SEEDS, 100)
    thinking = expand_templates(THINKING_TEMPLATES, THINKING_SEEDS, 100)
    tailor = expand_templates(TAILOR_TEMPLATES, TAILOR_SEEDS, 100)

    assert SEARCHING_REQUIRED in searching
    assert THINKING_REQUIRED in thinking
    assert len(searching) >= 100 and len(thinking) >= 100 and len(tailor) >= 100

    ts = f"""// Auto-generated by scripts/generate_rudy_sayings.py - do not edit by hand.
// Pools: searching={len(searching)}, thinking={len(thinking)}, tailor={len(tailor)}

{emit_array("SEARCHING_LINES", searching)}
{emit_array("THINKING_LINES", thinking)}
{emit_array("TAILOR_LINES", tailor)}

export function pickSaying(pool: readonly string[]): string {{
  return pool[Math.floor(Math.random() * pool.length)];
}}
"""
    OUT.write_text(ts, encoding="utf-8")
    print(f"Wrote {OUT} - searching={len(searching)} thinking={len(thinking)} tailor={len(tailor)}")


if __name__ == "__main__":
    main()

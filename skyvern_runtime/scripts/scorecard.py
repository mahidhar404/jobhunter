"""Turns a batch_hybrid.py run into the categorized metrics an improvement
strategy actually needs to track over time - not just a status dump.

Built because every fix validated so far (Submit-suspicion rule, autocomplete
-confirm rule, discovery URL bug, WAIT/timeout rule) was proven on exactly one
observed case each. That catches real bugs, but it's not evidence of
aggregate improvement. This is the repeatable measurement: run a batch, get a
scorecard, compare it to the last one.

Categories are mutually exclusive and deliberately distinguish "the safety
system correctly did its job" (submit_alarm, watchdog) from "the run failed
for an uninteresting external reason" (dead_link) from "the run failed for a
reason worth investigating" (other_terminated, timeout, exception).
"""
import json
import sys
from pathlib import Path

DEAD_LINK_PHRASES = (
    "doesn't exist", "does not exist", "page not found", "404",
    "no longer exists", "not available", "maintenance",
)

# A termination that says "I found a required field I genuinely cannot
# answer, so I stopped" is Rule 11 (WAIT/no-human-available fix) working
# exactly as designed - not the same thing as a bug. Lumping these into
# "other_terminated" made a batch that happened to sample more jobs with
# hard-to-answer required fields (desired compensation, "describe your
# experience with X") look like a regression when it wasn't one - the system
# behaved correctly in each case, it just met more genuinely unmappable
# fields this round.
CORRECT_UNMAPPABLE_PHRASES = (
    "no values in the known mapping", "known field mapping does not",
    "no value in the known mapping", "cannot be inferred",
    "no mapping value available", "not provide values for these fields",
)


def categorize(r: dict) -> str:
    if r.get("captcha_blocked"):
        return "captcha_blocked"
    if r.get("submit_alarm"):
        return "submit_alarm"  # safety system caught something - not a clean pass, not a bug either
    if r.get("watchdog_triggered"):
        return "watchdog"  # stuck-loop safety net fired
    if r.get("error") and "timeout" in str(r.get("error")).lower():
        return "timeout"
    if r.get("status") == "exception":
        return "exception"
    if r.get("status") == "completed":
        return "completed"
    reason = str(r.get("failure_reason") or "").lower()
    if any(p in reason for p in DEAD_LINK_PHRASES):
        return "dead_link"
    if any(p in reason for p in CORRECT_UNMAPPABLE_PHRASES):
        return "correct_unmappable_termination"
    return "other_terminated"


def scorecard(results: list[dict]) -> dict:
    cats: dict[str, list] = {}
    for r in results:
        cats.setdefault(categorize(r), []).append(r)
    n = len(results)
    return {
        "n_jobs": n,
        "counts": {k: len(v) for k, v in sorted(cats.items())},
        "rates": {k: round(len(v) / n, 3) for k, v in sorted(cats.items())} if n else {},
        "detail": cats,
    }


def print_scorecard(path: str, label: str = "") -> dict:
    data = json.loads(Path(path).read_text())
    results = data["results"]
    sc = scorecard(results)
    print(f"=== scorecard {label or path} (n={sc['n_jobs']}) ===")
    for cat, count in sc["counts"].items():
        print(f"  {cat:18s} {count:3d}  ({sc['rates'][cat]*100:.0f}%)")
    return sc


def diff(path_a: str, path_b: str) -> None:
    sc_a = print_scorecard(path_a, "BEFORE")
    print()
    sc_b = print_scorecard(path_b, "AFTER")
    print("\n=== delta (AFTER - BEFORE), percentage points ===")
    cats = sorted(set(sc_a["rates"]) | set(sc_b["rates"]))
    for cat in cats:
        a = sc_a["rates"].get(cat, 0.0)
        b = sc_b["rates"].get(cat, 0.0)
        delta = (b - a) * 100
        sign = "+" if delta >= 0 else ""
        print(f"  {cat:18s} {a*100:5.0f}% -> {b*100:5.0f}%  ({sign}{delta:.0f}pp)")


if __name__ == "__main__":
    if len(sys.argv) == 3:
        diff(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 2:
        print_scorecard(sys.argv[1])
    else:
        print("usage: scorecard.py <batch_summary.json> [<batch_summary_b.json> for diff]", file=sys.stderr)
        sys.exit(1)

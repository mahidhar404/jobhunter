"""Evaluate the deterministic classifier against the label-resolved corpus.

Separate from evaluate.py (which graded the label-less action data) because this
one measures what the real Playwright filler will actually see: name, id,
placeholder, aria-label AND the resolved <label> text.

Prints per-platform accuracy plus, importantly, the raw text of everything it
FAILED to resolve - that list is the input to the next tuning round, so the loop
is driven by evidence rather than guesswork about what forms probably contain.

Usage:
    eval_corpus.py            summary + miss analysis
    eval_corpus.py -v         also dump every mis-classification with its text
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from field_map import (  # noqa: E402
    classify_field, NAME_FIRST, NAME_LAST, NAME_FULL,
    GITHUB, LINKEDIN, PORTFOLIO, ADDRESS_LINE1, ADDRESS_CITY,
)

CORPUS = Path(__file__).resolve().parent / "corpus.json"

# Groups whose members are interchangeable for scoring purposes.
#
# NAMES: a single "Full name" box and a split first/last pair both receive a
# correctly-typed name, so distinguishing them is not a real capability
# difference.
#
# URLS: a generic "Website" / "Other website" field is genuinely ambiguous - the
# LLM happened to put a GitHub URL there, but classifying it as PORTFOLIO is at
# least as defensible, and under the profile's own policy (never volunteer
# social links unless the field is required) leaving it blank is the CORRECT
# outcome. Grading PORTFOLIO as an error here would penalise the safer answer.
#
# ADDRESS: "Current location" vs "Home address" vs "City" all take address text
# on different platforms; which sub-part a given site wants is a formatting
# concern for the filler, not a classification failure.
NAMES = {NAME_FIRST, NAME_LAST, NAME_FULL}
URLS = {GITHUB, LINKEDIN, PORTFOLIO}
ADDRS = {ADDRESS_LINE1, ADDRESS_CITY}
EQUIV = (NAMES, URLS, ADDRS)


def equivalent(pred: str, truth: str) -> bool:
    return pred == truth or any(pred in g and truth in g for g in EQUIV)


def platform_of(url: str) -> str:
    u = (url or "").lower()
    for key, name in [
        ("ashbyhq", "ashby"), ("lever.co", "lever"), ("greenhouse", "greenhouse"),
        ("myworkdayjobs", "workday"), ("myworkdaysite", "workday"), ("icims", "icims"),
        ("smartrecruiters", "smartrecruiters"), ("workable", "workable"),
        ("taleo", "taleo"), ("jobvite", "jobvite"), ("avature", "avature"),
        ("oraclecloud", "oracle-hcm"), ("rippling", "rippling"), ("paylocity", "paylocity"),
        ("adp.com", "adp"), ("gem.com", "gem"), ("teamtailor", "teamtailor"),
        ("breezy.hr", "breezy"), ("clearcompany", "clearcompany"), ("bamboohr", "bamboohr"),
        ("zohorecruit", "zoho"), ("comeet", "comeet"), ("dayforce", "dayforce"),
        ("recruitee", "recruitee"), ("applytojob", "applytojob"), ("linkedin", "linkedin"),
        ("careerpuck", "careerpuck"), ("jobscore", "jobscore"), ("paycom", "paycom"),
        ("ultipro", "ultipro"), ("workwolf", "workwolf"), ("hirebridge", "hirebridge"),
        ("applicantpro", "applicantpro"), ("hrmdirect", "hrmdirect"), ("dover", "dover"),
        ("personio", "personio"),
    ]:
        if key in u:
            return name
    return "other"


def field_of(rec: dict) -> dict:
    return {
        "name": rec.get("name"),
        "id": rec.get("id"),
        "label": rec.get("label"),
        "aria_label": rec.get("aria_label"),
        "placeholder": rec.get("placeholder"),
        "autocomplete": rec.get("autocomplete"),
        "input_type": rec.get("input_type"),
    }


def describe(rec: dict) -> str:
    """Compact human-readable view of what signal a field actually carried."""
    bits = []
    for k in ("label", "name", "id", "placeholder", "aria_label"):
        v = (rec.get(k) or "").strip()
        if v:
            bits.append(f"{k}={v[:58]!r}")
    return "  ".join(bits) or "<no signal at all>"


def main():
    verbose = "-v" in sys.argv
    records = json.loads(CORPUS.read_text())

    total = resolved = correct = 0
    by_platform = defaultdict(lambda: {"n": 0, "ok": 0})
    by_layer = Counter()
    miss_texts = defaultdict(list)
    wrong_pairs = Counter()
    wrong_examples = defaultdict(list)
    no_signal = 0

    for rec in records:
        truth = rec["truth"]
        total += 1
        plat = platform_of(rec.get("page_url", ""))
        by_platform[plat]["n"] += 1

        pred, how = classify_field(field_of(rec))
        if pred:
            resolved += 1
            by_layer[how] += 1
            if equivalent(pred, truth):
                correct += 1
                by_platform[plat]["ok"] += 1
            else:
                wrong_pairs[f"{truth} -> {pred}"] += 1
                if len(wrong_examples[f"{truth} -> {pred}"]) < 3:
                    wrong_examples[f"{truth} -> {pred}"].append(describe(rec))
        else:
            d = describe(rec)
            if d == "<no signal at all>":
                no_signal += 1
            miss_texts[truth].append(d)

    print("=" * 72)
    print(f"DETERMINISTIC CLASSIFIER - {len({r['task_id'] for r in records})} real forms, "
          f"{total} fields")
    print("=" * 72)
    print(f"resolved (zero LLM):   {resolved:4d}  ({resolved/max(total,1)*100:.1f}%)")
    print(f"resolved AND correct:  {correct:4d}  ({correct/max(total,1)*100:.1f}%)")
    if resolved:
        print(f"precision:             {correct/resolved*100:.1f}%")
    print(f"fields with NO signal:  {no_signal:4d}  (unfixable by any regex)")
    if total - no_signal:
        print(f"correct, excluding those: {correct/(total-no_signal)*100:.1f}%")
    print()
    for how, n in by_layer.most_common():
        print(f"   {how:26s} {n:5d}")

    print(f"\n{'platform':16s} {'n':>5s} {'ok':>5s} {'rate':>7s}")
    for plat, s in sorted(by_platform.items(), key=lambda x: -x[1]["n"]):
        if s["n"] < 3:
            continue
        print(f"{plat:16s} {s['n']:5d} {s['ok']:5d} {s['ok']/s['n']*100:6.1f}%")

    if miss_texts:
        print("\n" + "-" * 72)
        print("UNRESOLVED - the tuning worklist (text the regexes never matched)")
        print("-" * 72)
        for truth, texts in sorted(miss_texts.items(), key=lambda x: -len(x[1])):
            print(f"\n{truth}  ({len(texts)} misses)")
            for t, n in Counter(texts).most_common(4):
                print(f"   {n:3d}x  {t[:118]}")

    if wrong_pairs:
        print("\n" + "-" * 72)
        print("MIS-CLASSIFIED (wrong answer given - higher priority than a miss)")
        print("-" * 72)
        for pair, n in wrong_pairs.most_common(12):
            print(f"\n{pair}  ({n})")
            if verbose:
                for ex in wrong_examples[pair]:
                    print(f"      {ex[:118]}")


if __name__ == "__main__":
    main()

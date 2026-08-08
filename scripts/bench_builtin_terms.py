#!/usr/bin/env python3
"""Benchmark Built In search-term yield (URL collection only, no ingest).

Collects search-result URLs per term against live Built In and reports
per-term unique/new/duplicate contribution plus title relevance. Never writes
jobs.json and never fetches job detail pages, so it is safe to run alongside
normal discovery.

Usage:
  python3 bench_builtin_terms.py --terms-file terms.json --pages 3 \
      --days 3 --out logs/builtin_term_bench.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scrape_builtin as sb  # noqa: E402
from known_job_urls import load_known_url_keys, url_is_known  # noqa: E402
from relevance import is_relevant  # noqa: E402

# Search cards carry the title in the job link's slug; good enough to judge
# relevance without fetching detail pages (which is the expensive part).
_SLUG_RE = re.compile(r"/job/([^/]+)/\d+")


def title_from_url(url: str) -> str:
    m = _SLUG_RE.search(url)
    return m.group(1).replace("-", " ") if m else ""


def bench_term(term: str, *, pages: int, days: int) -> dict:
    urls: list[str] = []
    failures = 0
    start = time.monotonic()
    consecutive_empty = 0
    for page in range(1, pages + 1):
        html = sb.fetch_html(sb.build_search_url(term, page, days_since_updated=days))
        if not html:
            failures += 1
            break
        found = sb.extract_job_urls(html)
        before = len(set(urls))
        urls.extend(sb.BASE + p for p in found)
        if len(set(urls)) == before:
            consecutive_empty += 1
            if consecutive_empty >= sb.CONSECUTIVE_EMPTY_PAGES_STOP:
                break
        else:
            consecutive_empty = 0
        if page < pages:
            sb.adaptive_sleep(search=True)
    unique = sorted(set(urls))
    return {
        "term": term,
        "fetched": len(urls),
        "unique": len(unique),
        "relevant": sum(1 for u in unique if is_relevant(title_from_url(u))),
        "failures": failures,
        "elapsed_s": round(time.monotonic() - start, 1),
        "urls": unique,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--terms-file", required=True)
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    terms = json.loads(Path(args.terms_file).read_text())
    known = load_known_url_keys()
    days = sb.normalize_days_since_updated(args.days)

    run_start = time.monotonic()
    per_term = [bench_term(t, pages=args.pages, days=days) for t in terms]

    # Marginal yield: URLs a term adds that no earlier term already found.
    seen: set[str] = set()
    all_relevant: set[str] = set()
    for row in per_term:
        urls = set(row["urls"])
        marginal = urls - seen
        seen |= urls
        rel_marginal = {u for u in marginal if is_relevant(title_from_url(u))}
        all_relevant |= {u for u in urls if is_relevant(title_from_url(u))}
        row["marginal_unique"] = len(marginal)
        row["marginal_relevant"] = len(rel_marginal)
        row["duplicate_of_earlier_terms"] = len(urls) - len(marginal)
        row["marginal_relevant_new_to_jobs"] = sum(
            1 for u in rel_marginal if not url_is_known(u, known)
        )
        row.pop("urls")

    summary = {
        "days_since_updated": days,
        "pages_per_term": args.pages,
        "country": sb.SEARCH_COUNTRY,
        "terms": len(terms),
        "total_unique_urls": len(seen),
        "total_relevant_urls": len(all_relevant),
        "total_relevant_new_to_jobs": sum(
            r["marginal_relevant_new_to_jobs"] for r in per_term
        ),
        "elapsed_s": round(time.monotonic() - run_start, 1),
        "per_term": per_term,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "per_term"}, indent=2))
    for r in per_term:
        print(
            f"{r['term']:<38} unique={r['unique']:>3} "
            f"marginal={r['marginal_unique']:>3} "
            f"marg_relevant={r['marginal_relevant']:>3} "
            f"marg_rel_new={r['marginal_relevant_new_to_jobs']:>3} "
            f"dupes={r['duplicate_of_earlier_terms']:>3} "
            f"fail={r['failures']} {r['elapsed_s']}s"
        )


if __name__ == "__main__":
    main()

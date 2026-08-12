#!/usr/bin/env python3
"""Pick N never-seen jobs for a headed improvement cycle.

Priority (per Yogesh's test request):
  1. Workday first  -- maximize never-seen Workday tenants (distinct hosts,
     never picked/attempted before).
  2. Latest postings -- prefer newest discovered jobs.
  3. Fill remaining slots with newest other never-seen ATS.

"Never seen" bar (same as prior never-seen runs): the job's normalized apply
URL keys must be absent from EVERY seen tracker:
  - SEEN_EXCLUDE.json
  - blocked_urls.json (durable user-deleted / blocked)
  - eval_urls.json
  - real_job_results/**/report.json (+ *_report.json)  live/eval/cycle runs
  - learning_store/experience.jsonl (host-level -> Workday host freshness)
  - prior NEVER_SEEN_PICK_*.json (picked urls + banned Workday hosts)
  - never_seen_live_* run dirs
  - TEN_UNSEEN_CANDIDATES.json

Also: any Workday host that appears in a prior pick (picked or banned) is
treated as NOT fresh, so each cycle exercises brand-new tenants.

Writes NEVER_SEEN_PICK_<stamp>.json (+ updates NEVER_SEEN_PICK_LATEST.json)
in the cycle-queue shape that cycle_orchestrate.load_urls_json accepts.

Dummy-only, never submit -- this only *selects* URLs; the cycle runner does
the fill under TEST_MODE=1.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = ROOT / "skyvern_runtime" / "real_job_results"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(HERE))

from apply_urls import normalize_url  # noqa: E402
from blocked_urls import block_keys_for_url, load_blocked_url_set  # noqa: E402
from fast_fill import detect_platform  # noqa: E402

AGGREGATOR_HOSTS = (
    "indeed.com", "linkedin.com", "builtin.com", "glassdoor.",
    "ziprecruiter.", "google.com", "internshala.", "naukri.",
    "hirist.", "cutshort.", "adzuna.",
)


def _host(url: str) -> str:
    from urllib.parse import urlparse

    try:
        h = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def _url_keys(url: str) -> list[str]:
    keys = []
    n = normalize_url(url)
    if n:
        keys.append(n)
    for k in block_keys_for_url(url):
        if k not in keys:
            keys.append(k)
    return keys


def _add_url(seen: set[str], url: str) -> None:
    for k in _url_keys(url):
        seen.add(k)


def _walk_json_urls(obj, out: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and (
                k.lower() in ("url", "apply_url", "job_url", "source_url", "norm_url")
            ) and v.startswith("http"):
                out.append(v)
            else:
                _walk_json_urls(v, out)
    elif isinstance(obj, list):
        for it in obj:
            _walk_json_urls(it, out)


def build_seen() -> tuple[set[str], set[str], set[str]]:
    """Return (seen_url_keys, seen_hosts, banned_workday_hosts)."""
    seen: set[str] = set()
    hosts: set[str] = set()
    banned_wd: set[str] = set()

    # blocked_urls.json (already normalized keys)
    seen |= load_blocked_url_set()

    def ingest_urls(urls):
        for u in urls:
            if not isinstance(u, str) or not u.startswith("http"):
                continue
            _add_url(seen, u)
            h = _host(u)
            if h:
                hosts.add(h)
                if "myworkdayjobs.com" in h or "myworkdaysite.com" in h:
                    banned_wd.add(h)

    # SEEN_EXCLUDE.json
    p = RESULTS / "SEEN_EXCLUDE.json"
    if p.exists():
        d = json.loads(p.read_text())
        ingest_urls(d.get("urls") or [])

    # eval_urls.json
    p = ROOT / "scripts" / "fastfill" / "eval_urls.json"
    if p.exists():
        d = json.loads(p.read_text())
        ingest_urls([u.get("url") for u in (d.get("urls") or []) if isinstance(u, dict)])

    # TEN_UNSEEN_CANDIDATES.json
    p = RESULTS / "TEN_UNSEEN_CANDIDATES.json"
    if p.exists():
        d = json.loads(p.read_text())
        ingest_urls([r.get("url") for r in d if isinstance(r, dict)])

    # experience.jsonl -> host-level seen (Workday host freshness)
    p = ROOT / "scripts" / "fastfill" / "learning_store" / "experience.jsonl"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            h = str(row.get("host") or "").lower()
            if h:
                hosts.add(h)
                if "myworkdayjobs.com" in h or "myworkdaysite.com" in h:
                    banned_wd.add(h)

    # prior NEVER_SEEN_PICK_*.json (picked urls + banned wd host lists)
    for pk in RESULTS.glob("NEVER_SEEN_PICK_*.json"):
        try:
            d = json.loads(pk.read_text())
        except json.JSONDecodeError:
            continue
        for host in (
            (d.get("banned_workday_hosts_from_prev") or [])
            + (d.get("banned_hosts_from_prev_run") or [])
        ):
            if isinstance(host, str):
                banned_wd.add(host.lower())
        for row in d.get("picked") or []:
            if isinstance(row, dict) and row.get("url"):
                _add_url(seen, row["url"])
                h = _host(row["url"])
                if h and ("myworkdayjobs.com" in h or "myworkdaysite.com" in h):
                    banned_wd.add(h)

    # real_job_results/**/report.json + *_report.json + live dirs
    for rep in list(RESULTS.rglob("report.json")) + list(RESULTS.rglob("*_report.json")):
        try:
            d = json.loads(rep.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        urls: list[str] = []
        _walk_json_urls(d, urls)
        ingest_urls(urls)

    return seen, hosts, banned_wd


def _posted_key(job: dict) -> str:
    """Best available recency key (newest first when sorted desc)."""
    for f in ("date_posted", "date_posted_fallback", "created_at", "updated_at"):
        v = job.get(f)
        if v and str(v) not in ("nan", "None"):
            return str(v)
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--min-workday", type=int, default=3)
    ap.add_argument("--jobs", type=Path, default=ROOT / "jobs.json")
    args = ap.parse_args(argv)

    seen, seen_hosts, banned_wd = build_seen()

    data = json.loads(Path(args.jobs).read_text())
    jobs = data.get("jobs") or []

    # Build never-seen candidate rows.
    wd_cands: list[dict] = []
    other_cands: list[dict] = []
    used_wd_hosts: set[str] = set()

    # Newest first.
    for job in sorted(jobs, key=_posted_key, reverse=True):
        if job.get("status") not in ("discovered", None):
            continue
        url = job.get("apply_url") or job.get("job_url") or ""
        if not url or not url.startswith("http"):
            continue
        host = _host(url)
        if not host or any(a in host for a in AGGREGATOR_HOSTS):
            continue
        keys = _url_keys(url)
        if any(k in seen for k in keys):
            continue
        platform = detect_platform(url)
        row = {
            "url": url,
            "company": job.get("company"),
            "title": job.get("title"),
            "status": job.get("status"),
            "job_id": job.get("id"),
            "platform": platform,
            "posted": _posted_key(job),
            "date_posted": job.get("date_posted"),
            "why_new": "exact URL absent from all seen trackers; status=discovered",
            "evidence": {
                "norm_url": normalize_url(url),
                "host": host,
                "in_seen_exact": False,
                "host_seen_before": host in seen_hosts,
            },
        }
        is_wd = "myworkdayjobs.com" in host or "myworkdaysite.com" in host
        if is_wd:
            if host in banned_wd or host in used_wd_hosts:
                continue  # need a brand-new tenant
            used_wd_hosts.add(host)
            row["why_new"] += "; brand-new Workday tenant host"
            row["evidence"]["banned_host"] = False
            wd_cands.append(row)
        else:
            other_cands.append(row)

    n = args.n
    picked: list[dict] = []
    # 1) Workday first (already newest-first, distinct tenants)
    picked.extend(wd_cands[: max(args.min_workday, 0)])
    # top up with more workday if available and still under n
    for row in wd_cands[len(picked):]:
        if len(picked) >= n:
            break
        picked.append(row)
    # 2/3) fill remaining with newest other ATS
    for row in other_cands:
        if len(picked) >= n:
            break
        picked.append(row)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    by_platform: dict[str, int] = {}
    for r in picked:
        by_platform[r["platform"]] = by_platform.get(r["platform"], 0) + 1

    out = {
        "picked_at": datetime.now(timezone.utc).isoformat(),
        "seen_key_count": len(seen),
        "banned_workday_hosts_from_prev": sorted(banned_wd),
        "counts": {
            "picked": len(picked),
            "by_platform": by_platform,
            "candidates": {"workday": len(wd_cands), "other": len(other_cands)},
        },
        "tracking_sources": [
            "SEEN_EXCLUDE.json", "blocked_urls.json", "eval_urls.json",
            "real_job_results/**/report.json", "experience.jsonl",
            "NEVER_SEEN_PICK_*.json", "never_seen_live_*",
            "TEN_UNSEEN_CANDIDATES.json",
            "prior never-seen Workday tenant hosts banned",
        ],
        "picked": picked,
    }
    out_path = RESULTS / f"NEVER_SEEN_PICK_{stamp}.json"
    out_path.write_text(json.dumps(out, indent=2) + "\n")
    (RESULTS / "NEVER_SEEN_PICK_LATEST.json").write_text(
        json.dumps(out, indent=2) + "\n"
    )
    print(json.dumps(out, indent=2))
    print(f"\n[pick] wrote {out_path}", file=sys.stderr)
    print(
        f"[pick] picked={len(picked)} workday={by_platform.get('workday', 0)} "
        f"wd_candidates={len(wd_cands)} other_candidates={len(other_cands)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

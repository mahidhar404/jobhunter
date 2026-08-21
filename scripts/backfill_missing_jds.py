#!/usr/bin/env python3
"""One-shot (re-runnable) backfill for jobs missing or truncated JDs.

Walks jobs.json for entries with empty or intro-only descriptions
(short text, no requirements/responsibilities headings), fetches JD
text via source-specific public APIs (SmartRecruiters / Rippling /
Lever / Breezy) or ``extract_job_posting.extract`` as HTML fallback,
then writes:

  * resumes/<id>/jd_full.txt
  * jobs.json job_description preview (≤500 chars, same trim as
    write_discovered_jobs.py)
  * recomputed min_yoe / min_yoe_fallback / work_mode / work_mode_fallback
  * clearance / us_person / salary stamps; prune+tombstone if newly
    disqualifying (discovered only)

Safety: HTTP/API first, then ``extract_job_posting`` (which may use
headless Chromium for JS-thin pages). Never submits, never CAPTCHA,
never uses applicant PII. Throttles between requests.

Usage:
  .venv/bin/python scripts/backfill_missing_jds.py [--dry-run] [--limit N]
  .venv/bin/python scripts/backfill_missing_jds.py --stamp-marker

Marker: logs/missing_jd_backfill_v1.done (skip auto-run once stamped).
"""
from __future__ import annotations

import argparse
import codecs
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS_FILE = ROOT / "jobs.json"
RESUMES_DIR = ROOT / "resumes"
LOGS_DIR = ROOT / "logs"
MARKER = LOGS_DIR / "missing_jd_backfill_v1.done"
DELAY_S = 0.35
DELAY_AFTER_EXTRACT_S = 1.5  # extract may launch headless Chromium
MIN_USEFUL_CHARS = 80

sys.path.insert(0, str(ROOT / "scripts"))
from jobs_lock import locked_jobs_for_write  # noqa: E402
from write_discovered_jobs import (  # noqa: E402
    looks_truncated_jd,
    trim_description,
    write_full_description,
)
from discovery_filters import (  # noqa: E402
    auto_delete_reason,
    detect_work_mode,
    detect_work_mode_fallback,
    extract_min_required_yoe,
    extract_min_required_yoe_fallback,
    extract_salary,
    extract_salary_fallback,
    stamp_clearance_us_person_tags,
)
from blocked_urls import block_deleted_job  # noqa: E402
from scrape_ats import (  # noqa: E402
    TransientFetchError,
    fetch_json,
    fetch_html,
    lever_compose_description,
    smartrecruiters_description_from_detail,
    rippling_description_from_detail,
    description_from_jobposting_ldjson,
    description_from_job_html,
    workable_compose_description,
    pinpoint_compose_description,
    clean_html_content,
)
from extract_job_posting import (  # noqa: E402
    extract as extract_posting,
    posting_url,
    jd_fetch_urls,
    try_greenhouse,
    try_ashby,
)
from pw_fetch_html import looks_like_challenge_page  # noqa: E402

SMARTRECRUITERS_RE = re.compile(r"jobs\.smartrecruiters\.com/([^/?#]+)/([^/?#]+)")
RIPPLING_RE = re.compile(r"ats\.rippling\.com/([^/?#]+)/jobs/([^/?#]+)")
LEVER_RE = re.compile(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]+)")
BREEZY_RE = re.compile(r"([a-z0-9-]+)\.breezy\.hr/p/([^/?#]+)")
WORKABLE_SHORT_RE = re.compile(r"apply\.workable\.com/j/([A-Za-z0-9]+)", re.I)
WORKABLE_HOST_RE = re.compile(
    r"https?://(?!apply\.|jobs\.|www\.)([a-z0-9-]+)\.workable\.com/",
    re.I,
)
PINPOINT_RE = re.compile(
    r"([a-z0-9-]+)\.pinpointhq\.com/(?:en/)?postings/([0-9a-f-]+)", re.I
)
REMOTEOK_ID_RE = re.compile(r"remoteok\.com/remote-jobs/[^/]*?(\d{5,})", re.I)
ZOHO_RE = re.compile(r"zohorecruit\.com", re.I)


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def stored_jd_text(job: dict) -> str:
    """Canonical on-disk JD, then jobs.json preview (suffix stripped by heuristic)."""
    job_id = job.get("id") or ""
    if job_id:
        for name in ("jd_full.txt", "jd_full.md"):
            path = RESUMES_DIR / job_id / name
            if path.is_file() and path.stat().st_size > 0:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace").strip()
                    if text:
                        return text
                except OSError:
                    pass
    return (job.get("job_description") or "").strip()


def has_jd_full(job_id: str) -> bool:
    return bool(stored_jd_text({"id": job_id, "job_description": ""}))


def needs_jd(job: dict) -> bool:
    """True when preview/jd_full is empty or looks like a truncated intro."""
    return looks_truncated_jd(stored_jd_text(job))


def candidate_urls(job: dict) -> list[str]:
    urls: list[str] = []
    for key in ("apply_url", "job_url", "source_url"):
        u = job.get(key)
        if u and u not in urls:
            urls.append(u)
    for u in job.get("alternate_urls") or []:
        if u and u not in urls:
            urls.append(u)
    return urls


def fetch_via_source_api(url: str) -> str:
    """Prefer source-specific APIs; return empty string on miss."""
    gh = try_greenhouse(url)
    if gh and (gh.get("description") or "").strip():
        return (gh.get("description") or "").strip()
    ashby = try_ashby(url)
    if ashby and (ashby.get("description") or "").strip():
        return (ashby.get("description") or "").strip()

    m = SMARTRECRUITERS_RE.search(url or "")
    if m:
        slug, job_id = m.groups()
        try:
            detail = fetch_json(
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{job_id}"
            )
        except TransientFetchError:
            return ""
        if isinstance(detail, dict):
            return smartrecruiters_description_from_detail(detail)
        return ""

    m = RIPPLING_RE.search(url or "")
    if m:
        slug, uuid = m.groups()
        try:
            detail = fetch_json(
                f"https://ats.rippling.com/api/v1/board/{slug}/jobs/{uuid}"
            )
        except TransientFetchError:
            return ""
        if isinstance(detail, dict):
            return rippling_description_from_detail(detail)
        return ""

    m = LEVER_RE.search(url or "")
    if m:
        slug, posting_id = m.groups()
        try:
            detail = fetch_json(
                f"https://api.lever.co/v0/postings/{slug}/{posting_id}?mode=json"
            )
        except TransientFetchError:
            return ""
        if isinstance(detail, dict):
            return lever_compose_description(detail)
        return ""

    m = BREEZY_RE.search(url or "")
    if m:
        html = fetch_html(url)
        if html:
            return description_from_job_html(html) or description_from_jobposting_ldjson(html)
        return ""

    if "applytojob.com" in (url or "").lower():
        html = fetch_html(url)
        if html:
            return description_from_job_html(html)
        return ""

    workable = _fetch_workable(url)
    if workable:
        return workable

    pinpoint = _fetch_pinpoint(url)
    if pinpoint:
        return pinpoint

    remoteok = _fetch_remoteok(url)
    if remoteok:
        return remoteok

    if ZOHO_RE.search(url or ""):
        html = fetch_html(url)
        if html and looks_like_challenge_page(html):
            return ""
        if html:
            return _zohorecruit_description_from_html(html)

    return ""


def http_status(url: str) -> int | None:
    """Return HTTP status for a GET, or None on network failure."""
    if not url:
        return None
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError

    req = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; job-hunter-agent/1.0)"},
        method="GET",
    )
    try:
        with urlopen(req, timeout=15) as resp:
            return int(getattr(resp, "status", None) or resp.getcode() or 200)
    except HTTPError as exc:
        return int(exc.code)
    except (URLError, TimeoutError, OSError):
        return None


def jd_fetch_urls_for_job(job: dict) -> list[str]:
    return jd_fetch_urls(*candidate_urls(job))


def _fetch_workable(url: str) -> str:
    m = WORKABLE_SHORT_RE.search(url or "")
    shortcode = m.group(1).upper() if m else None
    host = WORKABLE_HOST_RE.search(url or "")
    slug = host.group(1) if host else None
    if not shortcode and not slug:
        return ""
    if shortcode and not slug:
        try:
            listing = fetch_json(f"https://www.workable.com/api/jobs/{shortcode}")
        except TransientFetchError:
            listing = None
        if isinstance(listing, dict):
            host = WORKABLE_HOST_RE.search(listing.get("url") or "")
            slug = host.group(1) if host else None
    if not (slug and shortcode):
        return ""
    try:
        detail = fetch_json(
            f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{shortcode}"
        )
    except TransientFetchError:
        return ""
    if isinstance(detail, dict):
        return workable_compose_description(detail)
    return ""


def _fetch_pinpoint(url: str) -> str:
    m = PINPOINT_RE.search(url or "")
    if not m:
        return ""
    slug, uuid = m.groups()
    try:
        board = fetch_json(
            f"https://{slug}.pinpointhq.com/postings.json",
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
    except TransientFetchError:
        return ""
    if not isinstance(board, dict):
        return ""
    for item in board.get("data") or []:
        if not isinstance(item, dict):
            continue
        blob = f"{item.get('url') or ''} {item.get('id') or ''} {item.get('path') or ''}"
        if uuid in blob:
            return pinpoint_compose_description(item)
    return ""


def _fetch_remoteok(url: str) -> str:
    m = REMOTEOK_ID_RE.search(url or "")
    if not m:
        return ""
    job_id = m.group(1)
    from scrape_remoteok import query_urls  # local import: scraper side-effects

    for api_url in query_urls():
        try:
            rows = fetch_json(api_url)
        except TransientFetchError:
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("id") or "")
            rurl = str(row.get("url") or "")
            if job_id in rid or job_id in rurl:
                return clean_html_content(row.get("description") or "")
    return ""


def _zohorecruit_description_from_html(html: str) -> str:
    marker = "var jobs = JSON.parse('"
    start = (html or "").find(marker)
    if start < 0:
        return description_from_jobposting_ldjson(html) or description_from_job_html(
            html or ""
        )
    rest = html[start + len(marker) :]
    end = rest.find("');")
    if end < 0:
        return ""
    raw = rest[:end].replace("\\-", "-").replace("\\/", "/")
    try:
        decoded = codecs.decode(raw, "unicode_escape")
        jobs = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(jobs, list):
        return ""
    for item in jobs:
        if not isinstance(item, dict):
            continue
        text = clean_html_content(item.get("Job_Description") or "")
        if text:
            return text
    return ""


def skip_reason(job: dict) -> str | None:
    """Hard-skip hosts we must not fetch (Akamai/CAPTCHA) or URL-less recovered stubs."""
    urls = candidate_urls(job)
    if (job.get("source") or "").lower() == "recovered" and not urls:
        return "recovered-no-url"
    if not urls:
        return "no-url"
    blob = " ".join(urls)
    if re.search(r"myworkdayjobs\.com|myworkdaysite\.com", blob, re.I):
        return "workday"
    if re.search(r"\.icims\.com|icims\.com/", blob, re.I):
        return "icims"
    if re.search(r"linkedin\.com", blob, re.I):
        return "linkedin"
    return None


def fetch_description_for_job(job: dict) -> tuple[str, str]:
    """Return (description, method) where method is api|extract|none|skip:<reason>."""
    reason = skip_reason(job)
    if reason:
        return "", f"skip:{reason}"
    old = stored_jd_text(job)
    urls = jd_fetch_urls_for_job(job)
    for url in urls:
        text = fetch_via_source_api(url)
        if text and is_upgrade(old, text.strip()):
            return text.strip(), "api"
    if any(PINPOINT_RE.search(u or "") for u in urls):
        return "", "skip:closed"
    if any(LEVER_RE.search(u or "") for u in urls):
        posting_seen: list[str] = []
        statuses: list[int | None] = []
        for url in urls:
            post = posting_url(url)
            if post in posting_seen:
                continue
            posting_seen.append(post)
            statuses.append(http_status(post))
        if statuses and all(s in (404, 410) for s in statuses):
            return "", "skip:closed"

    def _try_extract(url: str, *, allow_playwright: bool) -> tuple[str, str] | None:
        if skip_reason({"apply_url": url, "id": job.get("id"), "source": job.get("source")}):
            return None
        if WORKABLE_SHORT_RE.search(url or ""):
            return None
        try:
            result = extract_posting(url, allow_playwright=allow_playwright)
        except Exception as exc:  # noqa: BLE001 — backfill must keep going
            log(f"  extract error for {url}: {exc}")
            return None
        if result and (result.get("description") or "").strip():
            text = result["description"].strip()
            if len(text) >= MIN_USEFUL_CHARS:
                return text, "extract"
        return None

    for url in urls:
        got = _try_extract(url, allow_playwright=False)
        if got:
            return got
        html = fetch_html(url)
        if html and looks_like_challenge_page(html):
            return "", "skip:captcha"

    for url in urls:
        if LEVER_RE.search(url or "") and posting_url(url) != url:
            continue
        got = _try_extract(url, allow_playwright=True)
        if got:
            return got
        html = fetch_html(url)
        if html and looks_like_challenge_page(html):
            return "", "skip:captcha"
    return "", "none"


def preview_needs_disk_sync(preview: str, disk: str) -> bool:
    """jobs.json preview is *supposed* to be a 500-char clip of jd_full.

    Only rewrite when the preview looks like the entire stored posting
    (intro-only, no 'full text in resumes' suffix) and disk has a real JD.
    """
    disk = (disk or "").strip()
    preview = (preview or "").strip()
    if not disk or looks_truncated_jd(disk):
        return False
    if not preview:
        return True
    if "full text in resumes" in preview:
        return False
    return looks_truncated_jd(preview) and len(disk) > len(preview) + 80


def is_upgrade(old: str, new: str) -> bool:
    """Keep a fetch only when it is useful and longer than a truncated original."""
    new = (new or "").strip()
    old = (old or "").strip()
    if not new or len(new) < MIN_USEFUL_CHARS:
        return False
    if not old:
        return True
    if looks_truncated_jd(new) and len(new) <= len(old) + 80:
        return False
    return len(new) > len(old) + 80 or (
        looks_truncated_jd(old) and not looks_truncated_jd(new)
    )


def maybe_prune_discovered_job(job: dict, full_text: str) -> str | None:
    """If a discovered job is newly disqualifying, soft-delete it in place.

    Returns the prune reason (or None). Caller must tombstone URLs after
    releasing the jobs write lock — same pattern as write_discovered_jobs.
    Only touches ``status == discovered``; never resurrects or re-prunes
    jobs already in progress / deleted.
    """
    status = str(job.get("status") or "").strip().lower()
    if status != "discovered":
        return None
    reason = auto_delete_reason(
        title=job.get("title"),
        location=job.get("location"),
        company=job.get("company"),
        description=full_text,
        url=job.get("apply_url") or job.get("job_url"),
    )
    if not reason:
        return None
    now = datetime.now(timezone.utc).isoformat()
    job["status"] = "deleted"
    job["deleted_reason"] = reason
    job["deleted_at"] = now
    job["updated_at"] = now
    job["status_detail"] = f"Pruned after JD backfill ({reason})."
    return reason


def apply_jd_fields(job: dict, full_text: str) -> str | None:
    """Stamp list fields from full JD text; prune discovered if disqualifying.

    Returns prune reason when the job was tombstoned in-place, else None.
    """
    title = job.get("title") or ""
    location = job.get("location") or ""
    job["job_description"] = trim_description(full_text)
    work_mode = detect_work_mode(
        title=title, location=location, description=full_text
    )
    wm_fb = detect_work_mode_fallback(
        title=title, location=location, description=full_text
    )
    job["min_yoe"] = extract_min_required_yoe(title=title, description=full_text)
    job["min_yoe_fallback"] = extract_min_required_yoe_fallback(
        title=title, description=full_text
    )
    job["work_mode"] = work_mode
    job["work_mode_fallback"] = (
        wm_fb if work_mode == "unknown" and wm_fb != "unknown" else None
    )
    sal = extract_salary(title=title, description=full_text)
    sal_fb = extract_salary_fallback(title=title, description=full_text)
    job["salary_min"] = (sal or {}).get("min")
    job["salary_max"] = (sal or {}).get("max")
    job["salary_min_fallback"] = (sal_fb or {}).get("min")
    job["salary_max_fallback"] = (sal_fb or {}).get("max")
    tags = stamp_clearance_us_person_tags(
        title=title,
        company=job.get("company"),
        location=location,
        description=full_text,
        url=job.get("apply_url") or job.get("job_url"),
    )
    job["clearance"] = tags["clearance"]
    job["us_person"] = tags["us_person"]
    job["updated_at"] = datetime.now(timezone.utc).isoformat()
    return maybe_prune_discovered_job(job, full_text)


def stamp_marker() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(
        json.dumps({
            "done_at": datetime.now(timezone.utc).isoformat(),
            "note": "missing JD backfill completed or skipped",
        }, indent=2)
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max jobs to attempt (0=all)")
    parser.add_argument(
        "--stamp-marker",
        action="store_true",
        help="Only write the done marker (used after a successful full run)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if marker exists",
    )
    args = parser.parse_args()

    if args.stamp_marker:
        stamp_marker()
        log(f"wrote {MARKER}")
        return 0

    if MARKER.exists() and not args.force and not args.dry_run:
        log(f"marker exists ({MARKER.name}); pass --force to re-run")
        return 0

    synced = 0
    to_block: list[dict] = []
    if not args.dry_run:
        with locked_jobs_for_write() as locked:
            for job in locked.get("jobs") or []:
                disk = stored_jd_text({"id": job.get("id"), "job_description": ""})
                preview = job.get("job_description") or ""
                if preview_needs_disk_sync(preview, disk):
                    reason = apply_jd_fields(job, disk)
                    synced += 1
                    if reason:
                        to_block.append({
                            "id": job.get("id"),
                            "company": job.get("company"),
                            "title": job.get("title"),
                            "apply_url": job.get("apply_url"),
                            "job_url": job.get("job_url"),
                            "alternate_urls": list(job.get("alternate_urls") or []),
                        })
        for snap in to_block:
            try:
                block_deleted_job(snap, keep_tombstone=True)
            except TypeError:
                try:
                    block_deleted_job(snap)
                except Exception as e:
                    log(f"warn: block on JD-sync prune {snap.get('id')}: {e}")
            except Exception as e:
                log(f"warn: block on JD-sync prune {snap.get('id')}: {e}")
        if synced:
            log(f"synced jobs.json preview from jd_full.txt: {synced}")
            if to_block:
                log(f"pruned after JD sync: {len(to_block)}")
        to_block = []

    data = json.loads(JOBS_FILE.read_text()) if JOBS_FILE.exists() else {"jobs": []}
    before_by_source: Counter[str] = Counter()
    skipped_by_reason: Counter[str] = Counter()
    targets: list[dict] = []
    for job in data.get("jobs") or []:
        if not needs_jd(job):
            continue
        src = job.get("source") or "unknown"
        before_by_source[src] += 1
        snap = {
            "id": job["id"],
            "source": src,
            "title": job.get("title") or "",
            "location": job.get("location") or "",
            "apply_url": job.get("apply_url"),
            "job_url": job.get("job_url"),
            "source_url": job.get("source_url"),
            "alternate_urls": list(job.get("alternate_urls") or []),
        }
        reason = skip_reason(snap)
        if reason:
            skipped_by_reason[reason] += 1
            continue
        targets.append(snap)
    targets.sort(
        key=lambda j: (0 if "nextgenfed" in str(j.get("id") or "") else 1, j["id"])
    )
    if args.limit and args.limit > 0:
        targets = targets[: args.limit]

    log(
        f"missing JD: {sum(before_by_source.values())} total "
        f"(fetch {len(targets)}, skip {dict(skipped_by_reason)}); "
        f"by source={dict(before_by_source)}"
    )

    filled = 0
    failed = 0
    skipped = sum(skipped_by_reason.values())
    filled_by_source: Counter[str] = Counter()
    failed_by_source: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()
    fetched: dict[str, str] = {}

    for i, job in enumerate(targets, 1):
        job_id = job["id"]
        src = job["source"]
        log(f"[{i}/{len(targets)}] {src} {job_id}")
        if args.dry_run:
            continue
        text, method = fetch_description_for_job(job)
        if method.startswith("skip:"):
            skipped += 1
            skipped_by_reason[method.split(":", 1)[1]] += 1
            method_counts[method] += 1
            log(f"  SKIP {method}")
            continue
        time.sleep(DELAY_AFTER_EXTRACT_S if method == "extract" else DELAY_S)
        old = stored_jd_text(job)
        if not text or not is_upgrade(old, text):
            failed += 1
            failed_by_source[src] += 1
            method_counts["none" if not text else "no-upgrade"] += 1
            log("  FAIL (no description)" if not text else "  FAIL (fetch not longer than stored)")
            continue
        write_full_description(job_id, text)
        fetched[job_id] = text
        filled += 1
        filled_by_source[src] += 1
        method_counts[method] += 1
        log(f"  OK via {method} ({len(text)} chars)")

    if fetched and not args.dry_run:
        to_block = []
        with locked_jobs_for_write() as locked:
            by_id = {j["id"]: j for j in locked.get("jobs") or []}
            for job_id, text in fetched.items():
                job = by_id.get(job_id)
                if not job:
                    continue
                reason = apply_jd_fields(job, text)
                if reason:
                    to_block.append({
                        "id": job.get("id"),
                        "company": job.get("company"),
                        "title": job.get("title"),
                        "apply_url": job.get("apply_url"),
                        "job_url": job.get("job_url"),
                        "alternate_urls": list(job.get("alternate_urls") or []),
                    })
        for snap in to_block:
            try:
                block_deleted_job(snap, keep_tombstone=True)
            except TypeError:
                try:
                    block_deleted_job(snap)
                except Exception as e:
                    log(f"warn: block on JD-backfill prune {snap.get('id')}: {e}")
            except Exception as e:
                log(f"warn: block on JD-backfill prune {snap.get('id')}: {e}")
        if to_block:
            log(f"pruned after JD backfill: {len(to_block)}")

    remaining_by_source: Counter[str] = Counter()
    data = json.loads(JOBS_FILE.read_text()) if JOBS_FILE.exists() else {"jobs": []}
    for job in data.get("jobs") or []:
        if needs_jd(job):
            remaining_by_source[job.get("source") or "unknown"] += 1

    summary = {
        "before_total": sum(before_by_source.values()),
        "before_by_source": dict(before_by_source),
        "attempted": len(targets),
        "filled": filled if not args.dry_run else 0,
        "failed": failed if not args.dry_run else 0,
        "skipped": skipped,
        "skipped_by_reason": dict(skipped_by_reason),
        "filled_by_source": dict(filled_by_source),
        "failed_by_source": dict(failed_by_source),
        "methods": dict(method_counts),
        "remaining_total": sum(remaining_by_source.values()),
        "remaining_by_source": dict(remaining_by_source),
        "dry_run": args.dry_run,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = LOGS_DIR / "missing_jd_backfill_report.json"
    report_path.write_text(json.dumps(summary, indent=2) + "\n")
    log(f"report: {report_path}")
    log(
        f"filled={summary['filled']} failed={summary['failed']} "
        f"skipped={summary['skipped']} remaining={summary['remaining_total']} "
        f"skips={summary['skipped_by_reason']} "
        f"by_source_remaining={summary['remaining_by_source']}"
    )

    if not args.dry_run and args.limit == 0:
        stamp_marker()
        log(f"marker: {MARKER}")

    return 0 if summary["failed"] == 0 or summary["filled"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

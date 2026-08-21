#!/usr/bin/env python3
"""HTTP link-liveness checks for Open jobs with known ATS / company apply URLs.

Prunes only on clear dead/closed signals (HTTP 404/410 or known ATS closed
page HTML). Soft failures (timeout, 429, 403/login wall, network errors) never
prune.

Covers Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, BambooHR,
JazzHR/applytojob, Pinpoint, Rippling, Gem, Teamtailor, and other
``is_known_ats_url`` hosts, plus company careers URLs that look like job posts
(``looks_like_job_apply_url``). Pure LinkedIn / aggregator URLs are skipped
(resolve agent owns those; never prune on LinkedIn authwall).

Distinct from ``unresolved_apply_url`` (LinkedIn/aggregator never resolved to
ATS). This path only inspects jobs that already have a resolveable ATS/company
apply URL.

Deleted reason / OmniDex chip use concrete labels, e.g. ``dead/404``,
``closed/lever``, ``closed/greenhouse``, ``closed/ashby`` — not a vague
generic alone.

Usage:
  python3 scripts/link_liveness.py --dry-run --limit 40
  python3 scripts/link_liveness.py --write --limit 40
  python3 scripts/link_liveness.py --write --limit 40 --concurrency 6
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from apply_urls import (  # noqa: E402
    is_aggregator_url,
    is_ats_or_company_apply,
    is_known_ats_url,
)
from jobs_lock import locked_jobs_for_read, locked_jobs_for_write  # noqa: E402

# Prune-settings / scheduled-sweep category (checkbox).
CLOSED_POSTING_REASON = "closed_posting"
# Alias accepted in prune settings / docs.
DEAD_APPLY_URL_REASON = "dead_apply_url"

DEFAULT_TIMEOUT_S = 12.0
DEFAULT_CONCURRENCY = 6
DEFAULT_LIMIT = 40
MAX_BODY_BYTES = 64_000
USER_AGENT = "Mozilla/5.0 (compatible; job-hunter-link-liveness/1.0)"

# Soft / inconclusive HTTP statuses — never prune.
SOFT_HTTP_STATUSES = frozenset({401, 403, 407, 408, 425, 429, 500, 502, 503, 504})

_APPLY_PATH_SUFFIXES = ("/apply", "/application", "/applications")

# Host substring → concrete closed/* reason (longest / most specific first).
_HOST_CLOSED_REASONS: tuple[tuple[str, str], ...] = (
    ("jobs.lever.co", "closed/lever"),
    ("lever.co", "closed/lever"),
    ("job-boards.greenhouse.io", "closed/greenhouse"),
    ("boards.greenhouse.io", "closed/greenhouse"),
    ("greenhouse.io", "closed/greenhouse"),
    ("greenhouse.com", "closed/greenhouse"),
    ("ashbyhq.com", "closed/ashby"),
    ("myworkdayjobs.com", "closed/workday"),
    ("myworkdaysite.com", "closed/workday"),
    ("smartrecruiters.com", "closed/smartrecruiters"),
    ("workable.com", "closed/workable"),
    ("bamboohr.com", "closed/bamboo"),
    ("applytojob.com", "closed/jazzhr"),
    ("pinpointhq.com", "closed/pinpoint"),
    ("icims.com", "closed/icims"),
    ("rippling.com", "closed/rippling"),
    ("jobvite.com", "closed/jobvite"),
    ("gem.com", "closed/gem"),
    ("teamtailor.com", "closed/teamtailor"),
    ("recruitee.com", "closed/recruitee"),
    ("breezy.hr", "closed/breezy"),
    ("jobscore.com", "closed/jobscore"),
    ("dover.io", "closed/dover"),
    ("phenom.com", "closed/phenom"),
    ("ultipro.com", "closed/ultipro"),
    ("oraclecloud.com", "closed/oracle"),
    ("successfactors.com", "closed/successfactors"),
    ("taleo.net", "closed/taleo"),
    ("personio.com", "closed/personio"),
    ("personio.de", "closed/personio"),
    ("dayforcehcm.com", "closed/dayforce"),
    ("ukg.net", "closed/ukg"),
)


def posting_url(url: str) -> str:
    """Strip trailing /apply so Lever/GH listing pages are checked.

    JazzHR ``*.applytojob.com/apply/...`` keeps ``/apply`` (that is the posting).
    Kept local so this module does not import extract_job_posting (bs4).
    """
    raw = (url or "").strip()
    if not raw:
        return raw
    parts = urlsplit(raw)
    if "applytojob.com" in (parts.netloc or "").lower():
        return raw
    path = parts.path.rstrip("/")
    lowered = path.lower()
    for suffix in _APPLY_PATH_SUFFIXES:
        if lowered.endswith(suffix):
            path = path[: -len(suffix)]
            return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return raw


# (compiled regex, deleted_reason code, short chip label)
_CLOSED_HTML_SIGNALS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(
            r"couldn't find anything here|posting.*(closed|removed)|"
            r"this job posting.*(closed|no longer available)|"
            r"sorry,\s*we couldn.?t find|"
            r"<title>\s*not found\s*[–—-]?\s*404|"
            r"\(404 error\)",
            re.I,
        ),
        "closed/lever",
        "closed/lever",
    ),
    (
        re.compile(
            r"this job posting is no longer available|"
            r"job not found|the job you are looking for.*(closed|removed|no longer)|"
            r"this position has been filled|"
            r"no longer accepting applications|"
            r"sorry,\s*this job is no longer available",
            re.I,
        ),
        "closed/greenhouse",
        "closed/greenhouse",
    ),
    (
        re.compile(
            r"this job is no longer available|"
            r"job you are trying to view.*(not found|no longer)|"
            r"the job is closed|requisition.*(not found|closed)|"
            r"wd-popup-title[^>]*>\s*error\b|"
            r"this job is closed|job posting has expired",
            re.I,
        ),
        "closed/workday",
        "closed/workday",
    ),
    (
        re.compile(
            r"this job is no longer available|"
            r"job posting not found|"
            r"ashby.*not found|"
            r"the job you(?:'|’)re looking for.*(closed|removed|no longer)",
            re.I,
        ),
        "closed/ashby",
        "closed/ashby",
    ),
    (
        re.compile(
            r"this job is no longer available|"
            r"job.*(has been|was) (closed|removed|filled)|"
            r"position is no longer (open|available)|"
            r"vacancy.*(closed|not found)|"
            r"this opening is closed|"
            r"sorry[,!]?\s*this (job|position) (is|has been) (closed|filled|removed)",
            re.I,
        ),
        "closed/ats",
        "closed/ats",
    ),
)


@dataclass(frozen=True)
class LivenessResult:
    url: str
    verdict: str  # alive | dead | soft_fail | skip
    http_status: int | None = None
    signal: str | None = None
    # Concrete OmniDex deleted_reason / chip, e.g. dead/404, closed/lever
    deleted_reason: str | None = None
    label: str | None = None
    detail: str | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _url_host(url: str) -> str:
    try:
        host = (urlparse(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def closed_reason_for_host(host: str) -> str | None:
    """Map known ATS host → closed/{platform}; None for company careers."""
    h = (host or "").lower()
    if not h:
        return None
    for needle, reason in _HOST_CLOSED_REASONS:
        if needle in h:
            return reason
    return None


def is_checkable_apply_url(url: str) -> bool:
    """True for known ATS or company job-post URLs; never aggregators/LinkedIn."""
    s = str(url or "").strip()
    if not s or is_aggregator_url(s):
        return False
    if is_known_ats_url(s):
        return True
    # Company careers that look like a single job post (not a bare /careers hub).
    try:
        from resolve_apply_urls import looks_like_job_apply_url

        return bool(looks_like_job_apply_url(s))
    except Exception:
        return bool(is_ats_or_company_apply(s))


def primary_listing_url(job: dict) -> str:
    """Prefer checkable ATS/company apply_url, else job_url; strip /apply.

    Skips LinkedIn/aggregator URLs even when they sit in apply_url so liveness
    checks the resolved ATS/company target (resolve agent owns aggregators).
    """
    if not isinstance(job, dict):
        return ""
    apply = str(job.get("apply_url") or "").strip()
    job_url = str(job.get("job_url") or "").strip()
    for raw in (apply, job_url):
        if raw and is_checkable_apply_url(raw):
            return posting_url(raw) or raw
    return ""


def job_has_checkable_ats_url(job: dict) -> bool:
    """True when the job has a known ATS or company job URL (not aggregator)."""
    if not isinstance(job, dict):
        return False
    apply = str(job.get("apply_url") or "").strip()
    job_url = str(job.get("job_url") or "").strip()
    for u in (apply, job_url):
        if u and is_checkable_apply_url(u):
            return True
    return False


def _job_age_sort_key(job: dict) -> tuple:
    """Oldest-first: prefer exact date_posted, then created_at, then updated_at."""
    for field in ("date_posted", "created_at", "updated_at", "discovered_at"):
        raw = str(job.get(field) or "").strip()
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (0, dt.timestamp())
        except (TypeError, ValueError):
            continue
    return (1, 0.0)


def select_open_ats_jobs(
    jobs: list,
    *,
    limit: int | None = None,
    skip_recently_checked_s: float | None = None,
    host_substr: str | None = None,
) -> list[dict]:
    """Open (discovered) jobs with ATS/company URLs, oldest first, bounded."""
    now = time.time()
    needle = (host_substr or "").strip().lower() or None
    out: list[dict] = []
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        if str(job.get("status") or "").strip().lower() != "discovered":
            continue
        if not job_has_checkable_ats_url(job):
            continue
        if job.get("closed_posting"):
            # Already stamped (shouldn't be Open); skip.
            continue
        if needle:
            blob = f"{job.get('apply_url') or ''} {job.get('job_url') or ''}".lower()
            if needle not in blob:
                continue
        if skip_recently_checked_s is not None:
            checked = str(job.get("link_liveness_at") or "").strip()
            if checked:
                try:
                    dt = datetime.fromisoformat(checked.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if (now - dt.timestamp()) < skip_recently_checked_s:
                        continue
                except (TypeError, ValueError):
                    pass
        url = primary_listing_url(job)
        if not url:
            continue
        out.append(job)
    out.sort(key=_job_age_sort_key)
    if limit is not None and limit >= 0:
        out = out[:limit]
    return out


def match_closed_html(html: str, *, host_hint: str = "") -> tuple[str, str] | None:
    """Return (deleted_reason, label) when body matches a known closed page."""
    if not html:
        return None
    host = (host_hint or "").lower()
    host_reason = closed_reason_for_host(host)
    # Prefer ATS-specific patterns when host is known.
    ordered = list(_CLOSED_HTML_SIGNALS)
    if "lever.co" in host:
        ordered = [ordered[0]] + [x for x in ordered[1:]]
    elif "greenhouse" in host:
        ordered = [ordered[1]] + [x for x in ordered if x is not ordered[1]]
    elif "workday" in host:
        ordered = [ordered[2]] + [x for x in ordered if x is not ordered[2]]
    elif "ashby" in host:
        ordered = [ordered[3]] + [x for x in ordered if x is not ordered[3]]
    for pat, reason, label in ordered:
        if pat.search(html):
            # Prefer host-mapped closed/* when available.
            if host_reason:
                return (host_reason, host_reason)
            if "lever.co" in host and reason != "closed/lever":
                if _CLOSED_HTML_SIGNALS[0][0].search(html):
                    return ("closed/lever", "closed/lever")
            if "greenhouse" in host:
                return ("closed/greenhouse", "closed/greenhouse")
            if "workday" in host and "lever" not in reason:
                return ("closed/workday", "closed/workday")
            if "ashby" in host:
                return ("closed/ashby", "closed/ashby")
            return (reason, label)
    return None


def classify_http_response(
    *,
    url: str,
    status: int | None,
    body: str = "",
    error: str | None = None,
) -> LivenessResult:
    """Map status/body/error → liveness verdict with concrete deleted_reason."""
    url = str(url or "").strip()
    if error:
        err = error.lower()
        if "timed out" in err or "timeout" in err:
            return LivenessResult(
                url=url,
                verdict="soft_fail",
                signal="timeout",
                detail="timeout",
            )
        return LivenessResult(
            url=url,
            verdict="soft_fail",
            signal="network_error",
            detail=error[:200],
        )
    if status is None:
        return LivenessResult(
            url=url,
            verdict="soft_fail",
            signal="no_status",
            detail="no HTTP status",
        )
    host = _url_host(url)
    host_closed = closed_reason_for_host(host)

    if status in (404, 410):
        code = f"dead/{status}"
        # Known ATS hosts: 404/410 alone is enough for closed/{platform}
        # (CSS-first 404 pages often put the h2 past our body cap).
        if host_closed:
            return LivenessResult(
                url=url,
                verdict="dead",
                http_status=status,
                signal=f"http_{status}_{host_closed}",
                deleted_reason=host_closed,
                label=host_closed,
                detail=f"HTTP {status} {host_closed}",
            )
        # Company careers / unknown host: always dead/404 or dead/410 — never
        # map generic "job not found" HTML onto closed/greenhouse etc.
        return LivenessResult(
            url=url,
            verdict="dead",
            http_status=status,
            signal=f"http_{status}",
            deleted_reason=code,
            label=code,
            detail=f"HTTP {status} not found",
        )

    if status in SOFT_HTTP_STATUSES:
        return LivenessResult(
            url=url,
            verdict="soft_fail",
            http_status=status,
            signal=f"http_{status}",
            detail=f"soft HTTP {status}",
        )

    if 200 <= status < 300:
        html_hit = match_closed_html(body, host_hint=host)
        if html_hit:
            reason, label = html_hit
            # Company careers: coerce ATS-specific pattern hits → closed/ats.
            if not host_closed and reason.startswith("closed/") and reason != "closed/ats":
                reason = label = "closed/ats"
            return LivenessResult(
                url=url,
                verdict="dead",
                http_status=status,
                signal=f"html_{reason}",
                deleted_reason=reason,
                label=label,
                detail=f"HTTP {status} closed-page HTML ({label})",
            )
        return LivenessResult(
            url=url,
            verdict="alive",
            http_status=status,
            signal="ok",
        )

    # Other 3xx should have been followed; treat leftover as soft.
    return LivenessResult(
        url=url,
        verdict="soft_fail",
        http_status=status,
        signal=f"http_{status}",
        detail=f"unhandled HTTP {status}",
    )


def fetch_url_liveness(
    url: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> LivenessResult:
    """GET url (follow redirects), classify dead vs soft vs alive. No browser."""
    url = str(url or "").strip()
    if not url:
        return LivenessResult(url="", verdict="skip", signal="empty_url")
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            status = int(getattr(resp, "status", None) or resp.getcode() or 200)
            raw = resp.read(MAX_BODY_BYTES)
            charset = "utf-8"
            ctype = ""
            try:
                ctype = str(resp.headers.get("Content-Type") or "")
                if "charset=" in ctype.lower():
                    charset = ctype.lower().split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
            except Exception:
                pass
            try:
                body = raw.decode(charset, errors="replace")
            except Exception:
                body = raw.decode("utf-8", errors="replace")
            return classify_http_response(url=url, status=status, body=body)
    except HTTPError as exc:
        status = int(exc.code)
        body = ""
        try:
            body = (exc.read(MAX_BODY_BYTES) or b"").decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return classify_http_response(url=url, status=status, body=body)
    except TimeoutError:
        return classify_http_response(url=url, status=None, error="timeout")
    except URLError as exc:
        reason = str(getattr(exc, "reason", None) or exc)
        if "timed out" in reason.lower():
            return classify_http_response(url=url, status=None, error="timeout")
        return classify_http_response(url=url, status=None, error=reason)
    except OSError as exc:
        msg = str(exc)
        if "timed out" in msg.lower():
            return classify_http_response(url=url, status=None, error="timeout")
        return classify_http_response(url=url, status=None, error=msg)


def check_job_liveness(
    job: dict,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    fetch: Callable[..., LivenessResult] | None = None,
) -> LivenessResult:
    url = primary_listing_url(job)
    if not url:
        return LivenessResult(url="", verdict="skip", signal="empty_url")
    if not job_has_checkable_ats_url(job):
        return LivenessResult(url=url, verdict="skip", signal="not_ats")
    fetcher = fetch or fetch_url_liveness
    return fetcher(url, timeout_s=timeout_s)


def stamp_closed_posting_tag(job: dict, *, on: bool = True, label: str | None = None) -> None:
    if on:
        job["closed_posting"] = True
        if label:
            job["closed_posting_label"] = label
    else:
        job.pop("closed_posting", None)
        job.pop("closed_posting_label", None)


def should_prune_closed_posting(job: dict, result: LivenessResult | None = None) -> bool:
    if not isinstance(job, dict):
        return False
    if str(job.get("status") or "").strip().lower() != "discovered":
        return False
    if not job_has_checkable_ats_url(job):
        return False
    if result is None:
        return False
    return result.verdict == "dead" and bool(result.deleted_reason)


def tombstone_closed_posting(job: dict, result: LivenessResult) -> bool:
    """Soft-delete with concrete deleted_reason (dead/404, closed/lever, …)."""
    if not should_prune_closed_posting(job, result):
        return False
    reason = str(result.deleted_reason or "").strip() or "dead/404"
    label = str(result.label or reason).strip() or reason
    detail = f"Pruned: {reason}."
    if result.detail and result.detail.lower() not in reason.lower():
        detail = f"Pruned: {reason} — {result.detail}."
    if result.url:
        detail = f"{detail} url={result.url}"
    if len(detail) > 500:
        detail = detail[:499] + "…"
    now = now_iso()
    job["status"] = "deleted"
    job["deleted_reason"] = reason
    job["deleted_at"] = now
    job["updated_at"] = now
    job["status_detail"] = detail
    job["link_liveness_at"] = now
    job["link_liveness_signal"] = result.signal
    stamp_closed_posting_tag(job, on=True, label=label)
    return True


def _block_snap_for_job(job: dict) -> dict:
    return {
        "id": job.get("id"),
        "company": job.get("company"),
        "title": job.get("title"),
        "apply_url": job.get("apply_url"),
        "job_url": job.get("job_url"),
        "alternate_urls": list(job.get("alternate_urls") or []),
    }


def _tombstone_url_block(snap: dict | None) -> None:
    if not snap:
        return
    try:
        from blocked_urls import block_deleted_job

        block_deleted_job(snap, keep_tombstone=True)
    except TypeError:
        try:
            from blocked_urls import block_deleted_job

            block_deleted_job(snap)
        except Exception:
            pass
    except Exception:
        pass


def _stamp_liveness_checked(job: dict, result: LivenessResult) -> None:
    job["link_liveness_at"] = now_iso()
    if result.signal:
        job["link_liveness_signal"] = result.signal
    if result.http_status is not None:
        job["link_liveness_status"] = result.http_status


def sweep_closed_postings(
    *,
    write: bool = False,
    limit: int = DEFAULT_LIMIT,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    skip_recently_checked_s: float | None = 6 * 3600,
    host_substr: str | None = None,
    fetch: Callable[..., LivenessResult] | None = None,
) -> dict:
    """Check oldest Open ATS/company jobs; prune clear dead/closed only.

    HTTP runs outside the jobs write lock. Soft failures are stamped with
    ``link_liveness_at`` when write=True so we do not hammer the same URL.
    """
    concurrency = max(1, min(int(concurrency or 1), 12))
    limit = max(0, int(limit or 0))
    with locked_jobs_for_read() as data:
        candidates = select_open_ats_jobs(
            list(data.get("jobs") or []),
            limit=limit,
            skip_recently_checked_s=skip_recently_checked_s,
            host_substr=host_substr,
        )
        snaps = [
            {
                "id": j.get("id"),
                "status": j.get("status"),
                "apply_url": j.get("apply_url"),
                "job_url": j.get("job_url"),
            }
            for j in candidates
        ]

    checked = 0
    dead = 0
    soft = 0
    alive = 0
    pruned = 0
    results_by_id: dict[str, LivenessResult] = {}
    pruned_by_reason: Counter[str] = Counter()
    pruned_by_host: Counter[str] = Counter()
    checked_by_host: Counter[str] = Counter()

    def _one(snap: dict) -> tuple[str, LivenessResult]:
        jid = str(snap.get("id") or "")
        return jid, check_job_liveness(snap, timeout_s=timeout_s, fetch=fetch)

    if snaps:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futs = [pool.submit(_one, s) for s in snaps]
            for fut in as_completed(futs):
                jid, result = fut.result()
                checked += 1
                results_by_id[jid] = result
                host = _url_host(result.url) or "?"
                checked_by_host[host] += 1
                if result.verdict == "dead":
                    dead += 1
                elif result.verdict == "alive":
                    alive += 1
                elif result.verdict == "soft_fail":
                    soft += 1

    to_block: list[dict] = []
    if write and results_by_id:
        with locked_jobs_for_write() as data:
            for job in data.get("jobs") or []:
                if not isinstance(job, dict):
                    continue
                jid = str(job.get("id") or "")
                result = results_by_id.get(jid)
                if result is None:
                    continue
                if result.verdict == "dead" and tombstone_closed_posting(job, result):
                    pruned += 1
                    reason = str(result.deleted_reason or "dead/404")
                    pruned_by_reason[reason] += 1
                    pruned_by_host[_url_host(result.url) or "?"] += 1
                    to_block.append(_block_snap_for_job(job))
                else:
                    _stamp_liveness_checked(job, result)
        for snap in to_block:
            _tombstone_url_block(snap)
    elif results_by_id:
        # Dry-run: count would-prune by reason/host without writing.
        for result in results_by_id.values():
            if result.verdict == "dead" and result.deleted_reason:
                pruned_by_reason[str(result.deleted_reason)] += 1
                pruned_by_host[_url_host(result.url) or "?"] += 1

    return {
        "considered": len(snaps),
        "checked": checked,
        "dead": dead,
        "alive": alive,
        "soft_fail": soft,
        "pruned": pruned if write else dead,
        "dry_run": not write,
        "reasons": sorted(pruned_by_reason.keys()),
        "pruned_by_reason": dict(pruned_by_reason),
        "pruned_by_host": dict(pruned_by_host),
        "checked_by_host": dict(checked_by_host),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--write", action="store_true", help="Persist prunes (default dry-run)")
    p.add_argument("--dry-run", action="store_true", help="Force dry-run (default)")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    p.add_argument(
        "--recheck",
        action="store_true",
        help="Ignore recent link_liveness_at stamps",
    )
    p.add_argument(
        "--host",
        default="",
        help="Only check URLs containing this host substring (e.g. lever.co)",
    )
    args = p.parse_args(argv)
    write = bool(args.write) and not bool(args.dry_run)
    summary = sweep_closed_postings(
        write=write,
        limit=args.limit,
        concurrency=args.concurrency,
        timeout_s=args.timeout,
        skip_recently_checked_s=None if args.recheck else 6 * 3600,
        host_substr=args.host or None,
    )
    print(
        f"link_liveness: considered={summary['considered']} "
        f"checked={summary['checked']} dead={summary['dead']} "
        f"alive={summary['alive']} soft_fail={summary['soft_fail']} "
        f"pruned={summary['pruned']} dry_run={summary['dry_run']}"
        + (
            f" reasons={summary['reasons']}"
            if summary.get("reasons")
            else ""
        )
    )
    if summary.get("pruned_by_reason"):
        print(f"  by_reason={summary['pruned_by_reason']}")
    if summary.get("pruned_by_host"):
        print(f"  by_host={summary['pruned_by_host']}")
    if summary.get("checked_by_host"):
        # Compact top hosts checked this batch (mixed ATS proof).
        top = sorted(
            summary["checked_by_host"].items(), key=lambda kv: (-kv[1], kv[0])
        )[:12]
        print(f"  checked_hosts={dict(top)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

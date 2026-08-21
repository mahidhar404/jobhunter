#!/usr/bin/env python3
"""Conservative jobs.json duplicate pass: merge URLs, soft-delete losers.

Merges only when confidence is high:
  - exact normalized URL or supported ATS posting-key match, OR
  - identical substantial full-JD fingerprints, with a same-ATS-org gate when
    normalized companies differ, OR
  - exact-title same-ATS-org reposts when one posting is demonstrably fresher.

Winner keeps the better (ATS) apply_url. Among equal-quality ATS URLs, prefer the
URL from the fresher posting (e.g. SmartRecruiters re-post). Winner's date_posted
(and related freshness fields) become the fresher of winner vs loser so Open's
stale filter does not hide a re-posted role. Loser's URLs are folded into winner
alternate_urls / source_url. Loser is soft-deleted (status=deleted,
deleted_reason=duplicate) with duplicate_of / merged_from pointing at the winner —
not left in a Skipped holding pen. Records are never hard-removed from jobs.json.

Usage:
  python3 dedup_jobs.py [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).parent))
from apply_urls import (  # noqa: E402
    collect_all_urls,
    enrich_listing_urls,
    is_aggregator_url,
    merge_source_names,
    merge_sources_entries,
    normalize_url,
    prefer_apply_url,
    url_preference_rank,
)
from jd_fingerprint import item_jd_fingerprint, same_jd_fingerprint  # noqa: E402
from jobs_lock import locked_jobs_for_write  # noqa: E402
from posting_identity import ats_org_key, posting_key, same_ats_org  # noqa: E402
from text_normalize import normalize_company, normalize_title  # noqa: E402

# Statuses that should not win a merge (or be active merge sources).
INACTIVE = frozenset({
    "deleted",
    "skipped_duplicate",  # legacy holding-pen
    "skipped_contract",
    "skipped_easy_apply",
    "skipped_manual",
    "applied",
    "cancelled",
})


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_urls(job: dict) -> list[str]:
    return collect_all_urls(job)


def exact_url_overlap(a: dict, b: dict) -> bool:
    a_keys = {normalize_url(u) for u in job_urls(a) if normalize_url(u)}
    b_keys = {normalize_url(u) for u in job_urls(b) if normalize_url(u)}
    return bool(a_keys & b_keys)


def duplicate_reason(a: dict, b: dict) -> str | None:
    """Return the first high-confidence identity signal in canonical order."""
    if a.get("id") == b.get("id"):
        return None
    if exact_url_overlap(a, b):
        return "url"
    a_posting, b_posting = posting_key(a), posting_key(b)
    if a_posting and a_posting == b_posting:
        return "posting_key"
    if same_jd_fingerprint(a, b):
        same_company = (
            normalize_company(a.get("company"))
            and normalize_company(a.get("company"))
            == normalize_company(b.get("company"))
        )
        if same_company or same_ats_org(a, b):
            return "jd_fingerprint"
    exact_title = normalize_title(a.get("title"))
    if (
        exact_title
        and exact_title == normalize_title(b.get("title"))
        and same_ats_org(a, b)
        and a_posting
        and b_posting
        and a_posting != b_posting
        and (is_fresher(a, b) or is_fresher(b, a))
    ):
        return "repost"
    return None


def should_merge(a: dict, b: dict) -> bool:
    return duplicate_reason(a, b) is not None


class _PostedSignal(NamedTuple):
    ts: float
    iso: str
    approx: bool


def _parse_posted_ts(value) -> float | None:
    """Parse date_posted / fallback into a comparable epoch seconds, or None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    # date-only → treat as UTC midnight
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return datetime(int(s[0:4]), int(s[5:7]), int(s[8:10]), tzinfo=timezone.utc).timestamp()
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        pass
    # Last resort: fromisoformat-ish prefixes already failed; skip junk.
    return None


def posted_signal(job: dict) -> _PostedSignal | None:
    """Effective posted signal mirroring dashboard jobPostedDisplay.

    Only ``date_posted`` (exact) or ``date_posted_fallback`` (~). Never
    ``created_at`` — discovery time is not a posted date.
    """
    exact = job.get("date_posted")
    ts = _parse_posted_ts(exact)
    if ts is not None:
        return _PostedSignal(ts, str(exact).strip(), False)
    fb = job.get("date_posted_fallback")
    ts = _parse_posted_ts(fb)
    if ts is not None:
        return _PostedSignal(ts, str(fb).strip(), True)
    return None


def is_fresher(a: dict, b: dict) -> bool:
    """True when a has a strictly newer effective posted date than b."""
    sa, sb = posted_signal(a), posted_signal(b)
    if sa is None:
        return False
    if sb is None:
        return True
    return sa.ts > sb.ts


def merge_freshness_into_winner(winner: dict, loser: dict) -> None:
    """Raise winner's posted freshness to at least the loser's (SanDisk stale-hide fix).

    Open hides discovered jobs with date_posted >30d via isHiddenUntouchedListing.
    Dedup often keeps the older record as winner (longer JD / earlier created_at) while
    soft-deleting the newer re-post — without this, the survivor stays stale-hidden.
    """
    w_sig = posted_signal(winner)
    l_sig = posted_signal(loser)
    if l_sig is None:
        return
    if w_sig is not None and l_sig.ts <= w_sig.ts:
        # Still fold fallbacks when useful, but don't regress exact date.
        w_fb = _parse_posted_ts(winner.get("date_posted_fallback"))
        l_fb = _parse_posted_ts(loser.get("date_posted_fallback"))
        if l_fb is not None and (w_fb is None or l_fb > w_fb):
            winner["date_posted_fallback"] = str(loser.get("date_posted_fallback")).strip()
        return

    # Loser is fresher (or winner had no posted date): promote onto winner.
    # Always write date_posted so jobPostedDisplay / stale filter see the fresh value
    # even when the fresher signal came from a fallback field.
    winner["date_posted"] = l_sig.iso
    if l_sig.approx:
        winner["date_posted_fallback"] = l_sig.iso
    else:
        # Prefer exact; keep the better of both fallbacks if present.
        w_fb = _parse_posted_ts(winner.get("date_posted_fallback"))
        l_fb = _parse_posted_ts(loser.get("date_posted_fallback"))
        if l_fb is not None and (w_fb is None or l_fb > w_fb):
            winner["date_posted_fallback"] = str(loser.get("date_posted_fallback")).strip()


def pick_winner(a: dict, b: dict) -> tuple[dict, dict]:
    ea = enrich_listing_urls(a)
    eb = enrich_listing_urls(b)
    ra = url_preference_rank(ea["apply_url"])
    rb = url_preference_rank(eb["apply_url"])
    if ra != rb:
        return (a, b) if ra < rb else (b, a)
    # Prefer active job as winner if one is already inactive/deleted
    if a.get("status") in INACTIVE and b.get("status") not in INACTIVE:
        return b, a
    if b.get("status") in INACTIVE and a.get("status") not in INACTIVE:
        return a, b
    # Longer description / earlier created
    if len(a.get("job_description") or "") != len(b.get("job_description") or ""):
        return (a, b) if len(a.get("job_description") or "") > len(b.get("job_description") or "") else (b, a)
    return (a, b) if str(a.get("created_at") or "") <= str(b.get("created_at") or "") else (b, a)


def _prefer_primary_apply(winner: dict, loser: dict, enriched_apply: str | None) -> str | None:
    """ATS-over-aggregator, then among equal rank prefer the fresher posting's URL."""
    w_apply = prefer_apply_url(winner.get("apply_url"), winner.get("job_url"))
    l_apply = prefer_apply_url(loser.get("apply_url"), loser.get("job_url"))
    # prefer_apply_url never demotes ATS→aggregator; seed with enrich result too.
    quality_best = prefer_apply_url(w_apply, l_apply, enriched_apply)
    if not quality_best:
        return w_apply or l_apply or enriched_apply

    rw = url_preference_rank(w_apply)
    rl = url_preference_rank(l_apply)
    # Different quality: prefer_apply_url already picked the better tier.
    if rw != rl:
        return quality_best
    # Equal quality (e.g. two SmartRecruiters postings): fresher posting wins primary.
    if l_apply and is_fresher(loser, winner) and url_preference_rank(l_apply) == rw:
        return l_apply
    if w_apply and is_fresher(winner, loser) and url_preference_rank(w_apply) == rl:
        return w_apply
    return quality_best


def fold_urls_into_winner(winner: dict, loser: dict) -> None:
    # Re-enrich from combined URL pools
    combined = {
        "job_url": winner.get("job_url"),
        "apply_url": winner.get("apply_url"),
        "job_url_direct": winner.get("job_url_direct") or loser.get("job_url_direct"),
        "source_url": winner.get("source_url") or loser.get("source_url"),
        "alternate_urls": list(winner.get("alternate_urls") or []) + list(loser.get("alternate_urls") or []),
        "description": (winner.get("job_description") or "") + "\n" + (loser.get("job_description") or ""),
    }
    for u in job_urls(loser):
        combined.setdefault("alternate_urls", []).append(u)
    out = enrich_listing_urls(combined)
    old_primary = winner.get("apply_url")
    manual_apply = bool(winner.get("apply_url_manual"))
    # ATS-over-aggregator first; among equal-quality ATS, fresher posting's URL.
    # Do not re-prefer via first-wins among equal rank (that would keep the stale URL).
    if not manual_apply:
        primary = _prefer_primary_apply(winner, loser, out.get("apply_url"))
        winner["apply_url"] = primary or old_primary
    # Never replace ATS apply with aggregator's job_url
    if is_aggregator_url(winner.get("job_url") or "") is False and is_aggregator_url(loser.get("job_url") or ""):
        winner["source_url"] = winner.get("source_url") or loser.get("job_url")
    elif out.get("source_url"):
        winner["source_url"] = winner.get("source_url") or out["source_url"]
    alts = list(winner.get("alternate_urls") or [])
    # Seed with existing alts so merges don't re-append near-dupes already on the winner
    seen = {
        normalize_url(u)
        for u in [
            winner.get("apply_url"),
            winner.get("job_url"),
            winner.get("source_url"),
            *alts,
        ]
        if u
    }
    # Keep demoted former primary in alternate_urls.
    for u in (old_primary, *(out.get("alternate_urls") or []), *job_urls(loser)):
        key = normalize_url(u)
        if key and key not in seen:
            seen.add(key)
            alts.append(u)
    winner["alternate_urls"] = alts
    names = merge_source_names(winner, loser)
    if names:
        winner["source_names"] = names
    sources = merge_sources_entries(winner, loser)
    if sources:
        winner["sources"] = sources
    # Freshness last so is_fresher() above still sees pre-merge dates.
    merge_freshness_into_winner(winner, loser)


def mark_loser_merged(
    loser: dict, winner: dict, *, why: str, deleted_reason: str = "duplicate"
) -> None:
    """Soft-delete loser after folding URLs onto winner (no Skipped pile)."""
    now = now_iso()
    loser["status"] = "deleted"
    loser["deleted_reason"] = deleted_reason
    loser["deleted_at"] = now
    wid = winner.get("id")
    loser["duplicate_of"] = wid
    loser["merged_from"] = wid
    loser["status_detail"] = (
        f"Duplicate of {wid} ({why}); "
        f"URLs merged onto winner apply_url / alternate_urls."
    )
    loser["updated_at"] = now


def _is_merged_away(job: dict) -> bool:
    st = job.get("status")
    return st == "deleted" or st == "skipped_duplicate"


def soft_link_exact_title_peers(jobs: list[dict]) -> None:
    """Relate same-company exact-title rows without treating title as identity."""
    groups: dict[tuple[str, str], list[dict]] = {}
    active: list[dict] = []
    for job in jobs:
        if _is_merged_away(job):
            continue
        active.append(job)
        key = (
            normalize_company(job.get("company")),
            normalize_title(job.get("title")),
        )
        if all(key):
            groups.setdefault(key, []).append(job)
    related_by_id: dict[str, list[str]] = {}
    for peers in groups.values():
        if len(peers) < 2:
            continue
        ids = {str(j.get("id")) for j in peers if j.get("id")}
        for job in peers:
            own = str(job.get("id") or "")
            related_by_id[own] = sorted(ids - {own})
    for job in active:
        related = related_by_id.get(str(job.get("id") or ""))
        if related:
            job["related_listing_ids"] = related
        else:
            job.pop("related_listing_ids", None)


def _backfill_freshness_from_deleted_dupes(
    jobs: list[dict], by_id: dict, dry_run: bool = False
) -> int:
    """Re-apply freshness + equal-rank fresher apply_url from already-deleted dupes.

    Fixes SanDisk-class winners that were merged before freshness promotion existed.
    """
    fixed = 0
    for job in jobs:
        if job.get("status") != "deleted":
            continue
        if job.get("deleted_reason") != "duplicate" and not job.get("duplicate_of"):
            continue
        wid = job.get("duplicate_of") or job.get("merged_from")
        if not wid:
            continue
        winner = by_id.get(wid)
        if not winner or _is_merged_away(winner):
            continue
        before_date = winner.get("date_posted")
        before_apply = winner.get("apply_url")
        if dry_run:
            if is_fresher(job, winner):
                print(
                    f"would backfill freshness {job.get('id')} -> {wid} "
                    f"(posted {job.get('date_posted')} onto winner {before_date})"
                )
                fixed += 1
            continue
        # fold_urls also promotes freshness + fresher equal-rank apply; safe to re-run
        # because loser is already deleted and URLs are idempotently folded.
        fold_urls_into_winner(winner, job)
        if not job.get("merged_from"):
            job["merged_from"] = wid
        if not job.get("duplicate_of"):
            job["duplicate_of"] = wid
        if (
            winner.get("date_posted") != before_date
            or winner.get("apply_url") != before_apply
        ):
            winner["updated_at"] = now_iso()
            fixed += 1
    return fixed


def _merge_active_jobs(jobs: list[dict], by_id: dict, dry_run: bool = False) -> int:
    """Merge duplicates among non-deleted jobs. Returns merge count."""
    active = [j for j in jobs if not _is_merged_away(j)]
    merged_pairs = 0

    def merge_phase(
        items: list[dict],
        reason_for_pair,
    ) -> list[dict]:
        nonlocal merged_pairs
        kept: list[dict] = []
        for item in items:
            match_idx = None
            reason = None
            for i, existing in enumerate(kept):
                reason = reason_for_pair(item, existing)
                if reason:
                    match_idx = i
                    break
            if match_idx is None:
                kept.append(item)
                continue
            existing = kept[match_idx]
            if reason == "repost":
                winner, loser = (
                    (item, existing) if is_fresher(item, existing) else (existing, item)
                )
            else:
                winner, loser = pick_winner(existing, item)
            if dry_run:
                print(
                    f"would merge {loser.get('id')} -> {winner.get('id')} "
                    f"({reason})"
                )
                kept[match_idx] = winner
                merged_pairs += 1
                continue
            if _is_merged_away(winner):
                kept[match_idx] = item
                continue
            fold_urls_into_winner(winner, loser)
            loser_rec = by_id.get(loser["id"], loser)
            if not _is_merged_away(loser_rec):
                mark_loser_merged(
                    loser_rec,
                    winner,
                    why=str(reason).replace("_", " "),
                    deleted_reason="repost" if reason == "repost" else "duplicate",
                )
            winner["updated_at"] = now_iso()
            kept[match_idx] = winner
            merged_pairs += 1
        return kept

    # Pass 1: URL/posting key, globally (company labels may disagree).
    def url_or_posting_reason(a: dict, b: dict) -> str | None:
        if exact_url_overlap(a, b):
            return "url"
        a_key, b_key = posting_key(a), posting_key(b)
        if a_key and a_key == b_key:
            return "posting_key"
        return None

    survivors = merge_phase(active, url_or_posting_reason)

    # Pass 2: full-JD fingerprint, cross-company only inside the same ATS org.
    fp_groups: dict[str, list[dict]] = {}
    no_fp: list[dict] = []
    for j in survivors:
        fp = item_jd_fingerprint(j)
        if fp:
            fp_groups.setdefault(fp, []).append(j)
        else:
            no_fp.append(j)

    survivors = list(no_fp)
    for _fp, items in fp_groups.items():
        def fingerprint_reason(a: dict, b: dict) -> str | None:
            same_company = (
                normalize_company(a.get("company"))
                and normalize_company(a.get("company"))
                == normalize_company(b.get("company"))
            )
            return "jd_fingerprint" if same_company or same_ats_org(a, b) else None

        survivors.extend(merge_phase(items, fingerprint_reason))

    # Pass 3: exact-title, same-ATS-org reposts. Distinct posting keys are
    # required and the fresher posting wins; ordinary title similarity never deletes.
    repost_groups: dict[tuple[str, str], list[dict]] = {}
    no_repost_key: list[dict] = []
    for j in survivors:
        key = (ats_org_key(j) or "", normalize_title(j.get("title")))
        if all(key):
            repost_groups.setdefault(key, []).append(j)
        else:
            no_repost_key.append(j)

    survivors = no_repost_key
    for items in repost_groups.values():
        def repost_reason(a: dict, b: dict) -> str | None:
            a_key, b_key = posting_key(a), posting_key(b)
            if (
                a_key
                and b_key
                and a_key != b_key
                and (is_fresher(a, b) or is_fresher(b, a))
            ):
                return "repost"
            return None

        survivors.extend(merge_phase(items, repost_reason))

    if not dry_run:
        soft_link_exact_title_peers(jobs)
    return merged_pairs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        import json
        from pathlib import Path as P
        data = json.loads((P(__file__).parent.parent / "jobs.json").read_text())
        jobs = data.get("jobs") or []
        by_id = {j["id"]: j for j in jobs if j.get("id")}
        active = [j for j in jobs if not _is_merged_away(j)]
        merged_pairs = _merge_active_jobs(jobs, by_id, dry_run=True)
        backfills = _backfill_freshness_from_deleted_dupes(jobs, by_id, dry_run=True)
        print(
            f"dry-run: {merged_pairs} merge(s) among {len(active)} active jobs; "
            f"{backfills} freshness backfill(s) from deleted dupes"
        )
        return

    with locked_jobs_for_write() as data:
        jobs = data.get("jobs") or []
        by_id = {j["id"]: j for j in jobs if j.get("id")}
        merged_pairs = _merge_active_jobs(jobs, by_id, dry_run=False)
        backfills = _backfill_freshness_from_deleted_dupes(jobs, by_id, dry_run=False)

    print(
        f"merged {merged_pairs} duplicate(s); losers soft-deleted (deleted_reason=duplicate); "
        f"freshness backfills={backfills}"
    )


if __name__ == "__main__":
    main()

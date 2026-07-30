"""Concurrent batch runner for hybrid_fill.run_hybrid.

The missing throughput multiplier from the "optimize for speed" pass: Skyvern's
org-level concurrency cap (verified earlier this session at 5 simultaneous
tasks) was never actually exploited - every hybrid_fill.py run to date has
been one job at a time, sequentially. This bounds a real job batch to that
same cap via an asyncio.Semaphore, so up to 5 real browser sessions/LLM tasks
run at once instead of queueing behind each other.

Each job still runs run_hybrid() unmodified - same safety rules, same
learning-loop extraction, same per-tenant alias persistence. Concurrency here
is purely about wall-clock throughput; it changes nothing about what any
single job does.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hybrid_fill import run_hybrid  # noqa: E402

CONCURRENCY = 5


async def _run_bounded(sem: asyncio.Semaphore, url: str, job_id: str) -> dict:
    async with sem:
        try:
            return await run_hybrid(url, job_id)
        except Exception as e:
            # A single job's exception (e.g. a truly broken URL) must not
            # take down the whole batch - every other slot keeps running.
            print(f"[{job_id}] BATCH-LEVEL EXCEPTION: {e}", flush=True)
            return {"id": job_id, "url": url, "status": "exception", "error": str(e)}


async def run_batch(jobs: list[dict], concurrency: int = CONCURRENCY) -> list[dict]:
    """jobs: list of {"id": ..., "url": ...}. Returns results in the same order."""
    sem = asyncio.Semaphore(concurrency)
    t0 = time.time()
    print(f"[batch] {len(jobs)} jobs, concurrency={concurrency}", flush=True)
    results = await asyncio.gather(
        *[_run_bounded(sem, j["url"], j["id"]) for j in jobs]
    )
    elapsed = time.time() - t0

    ok = [r for r in results if r.get("status") == "completed"]
    watchdog = [r for r in results if r.get("watchdog_triggered")]
    captcha = [r for r in results if r.get("captcha_blocked")]
    failed = [r for r in results if r not in ok and r not in watchdog and r not in captcha]

    print(f"\n[batch] DONE in {elapsed:.1f}s ({elapsed/60:.1f}min) "
          f"- {len(ok)}/{len(jobs)} completed, {len(watchdog)} watchdog, "
          f"{len(captcha)} captcha-blocked, {len(failed)} other", flush=True)
    for r in results:
        print(f"  {r.get('id'):40s} status={r.get('status')!s:12s} "
              f"elapsed={r.get('elapsed_seconds', 0):.0f}s "
              f"watchdog={bool(r.get('watchdog_triggered'))} "
              f"captcha={bool(r.get('captcha_blocked'))} "
              f"err={str(r.get('error'))[:60]}")

    out_path = Path(__file__).parent.parent / "real_job_results" / "batch_summary.json"
    out_path.write_text(json.dumps({
        "concurrency": concurrency, "n_jobs": len(jobs), "elapsed_seconds": elapsed,
        "n_completed": len(ok), "n_watchdog": len(watchdog), "n_captcha": len(captcha),
        "n_other": len(failed), "results": results,
    }, indent=2))
    print(f"[batch] summary -> {out_path}", flush=True)
    return results


if __name__ == "__main__":
    jobs_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not jobs_path:
        print("usage: batch_hybrid.py <jobs.json with [{id,url},...]> [concurrency]", file=sys.stderr)
        sys.exit(1)
    jobs = json.loads(Path(jobs_path).read_text())
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else CONCURRENCY
    asyncio.run(run_batch(jobs, concurrency=concurrency))

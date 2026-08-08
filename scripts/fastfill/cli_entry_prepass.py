#!/usr/bin/env python3
"""Thin diagnostic wrapper around fast_fill.entry_prepass.

Production fills use fast_fill.run_fast_fill (which already runs entry_prepass).
This script remains useful for multi-URL button_gate smoke checks without a
full form fill.

  skyvern_runtime/venv/bin/python scripts/fastfill/cli_entry_prepass.py
  skyvern_runtime/venv/bin/python scripts/fastfill/cli_entry_prepass.py --headless URL...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from button_map import FINAL  # noqa: E402
from fast_fill import (  # noqa: E402
    detect_platform,
    entry_prepass,
    form_fields_visible,
    snapshot_controls,
    classify_controls,
)

OUT_DEFAULT = ROOT / "skyvern_runtime" / "real_job_results" / "cli_entry_prepass.json"

DEFAULT_URLS = [
    "https://job-boards.greenhouse.io/biohub/jobs/7747517",
    "https://job-boards.greenhouse.io/grvty/jobs/4273921009",
    "https://jobs.lever.co/shieldai/a32a2559-8aa2-4d18-ae61-41cbfbfb644a",
]


async def run_one(page, url: str, *, max_clicks: int = 3) -> dict:
    t0 = time.time()
    row: dict = {
        "url": url,
        "platform": detect_platform(url),
        "dummy": True,
        "never_submit": True,
        "delegated_to": "fast_fill.entry_prepass",
    }
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1200)
    except Exception as e:
        row["error"] = f"goto: {e}"[:300]
        row["elapsed_seconds"] = round(time.time() - t0, 2)
        return row

    before = await snapshot_controls(page)
    row["buttons_before"] = classify_controls(before)
    pre = await entry_prepass(page, max_clicks=max_clicks)
    row["entry_prepass"] = pre
    row["clicked"] = pre.get("clicked") or []
    row["final_seen"] = pre.get("final_seen") or []
    row["final_clicks"] = pre.get("final_clicks", 0)
    assert row["final_clicks"] == 0, "FINAL click leaked"
    form = await form_fields_visible(page)
    row["form"] = form
    row["form_reached"] = bool(form.get("reached"))
    row["elapsed_seconds"] = round(time.time() - t0, 2)
    # Refuse any FINAL classified in the post snapshot too (diagnostic only)
    after = classify_controls(await snapshot_controls(page))
    row["buttons_after_kinds"] = sorted({c.get("kind") for c in after})
    row["final_present_after"] = any(c.get("kind") == FINAL for c in after)
    return row


async def run(urls: list[str], *, headed: bool = True, max_clicks: int = 3) -> dict:
    from playwright.async_api import async_playwright

    t0 = time.time()
    rows: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not headed,
            slow_mo=40 if headed else 0,
        )
        context = await browser.new_context()
        page = await context.new_page()
        for url in urls:
            print(f"\n=== entry prepass: {url} ===", flush=True)
            row = await run_one(page, url, max_clicks=max_clicks)
            rows.append(row)
            print(
                json.dumps(
                    {
                        "platform": row.get("platform"),
                        "form_reached": row.get("form_reached"),
                        "clicks": len(row.get("clicked") or []),
                        "final_clicks": row.get("final_clicks"),
                        "elapsed": row.get("elapsed_seconds"),
                    },
                    indent=2,
                )
            )
        await browser.close()

    totals = {
        "urls": len(rows),
        "form_reached": sum(1 for r in rows if r.get("form_reached")),
        "final_clicks_total": sum(int(r.get("final_clicks") or 0) for r in rows),
        "elapsed_seconds": round(time.time() - t0, 2),
    }
    return {
        "experiment": "cli_entry_prepass",
        "delegated_to": "fast_fill.entry_prepass",
        "dummy": True,
        "never_submit": True,
        "totals": totals,
        "rows": rows,
    }


def discover_urls_from_listings(n: int = 3) -> list[str]:
    """Best-effort GH/Lever URLs from listings/."""
    gh: list[str] = []
    lever: list[str] = []
    for path in sorted((ROOT / "listings").glob("*.json"), reverse=True)[:20]:
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("jobs") or []
        if not isinstance(items, list):
            continue
        for j in items:
            if not isinstance(j, dict):
                continue
            for k in ("apply_url", "url", "job_url", "job_url_direct"):
                u = j.get(k) or ""
                if not isinstance(u, str) or not u.startswith("http"):
                    continue
                low = u.lower()
                if "greenhouse.io" in low and "/jobs/" in low and u not in gh:
                    gh.append(u)
                elif "jobs.lever.co" in low and u not in lever:
                    lever.append(u)
        if len(gh) >= 2 and lever:
            break
    out = gh[:2]
    if lever:
        applyish = [u for u in lever if u.rstrip("/").endswith("/apply")]
        out.append((applyish or lever)[0])
    return out[:n] or list(DEFAULT_URLS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("urls", nargs="*", help="ATS apply URLs")
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--headed", action="store_true", default=True)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--max-clicks", type=int, default=3)
    args = ap.parse_args()
    headed = not bool(args.headless)

    if args.urls:
        urls = args.urls
    elif args.discover:
        urls = discover_urls_from_listings(3)
    else:
        urls = list(DEFAULT_URLS)

    print("URLs:", *urls, sep="\n  ")
    print(
        f"[cli_entry_prepass → fast_fill.entry_prepass] headed={headed}…",
        flush=True,
    )
    summary = asyncio.run(run(urls, headed=headed, max_clicks=args.max_clicks))
    assert summary.get("never_submit") is True, "refuse to write without never_submit"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2))
    print("\n=== SUMMARY ===")
    print(json.dumps(summary["totals"], indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

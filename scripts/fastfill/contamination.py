"""End-of-page contamination sweep — re-read committed values (ChamPro).

Catches autofill spill / SPA wipe. Does not auto-reopen custom dropdowns on
false drift — only flags demotions for the orchestrator to repair once.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable


def _default_soft_match(expected: str, got: str) -> bool:
    """Polarity-aware match — never Male⊂Female / IL⊂Idaho via raw ``in``."""
    try:
        from verified_select import soft_value_match

        return soft_value_match(expected, got)
    except Exception:
        e = (expected or "").strip().lower()
        g = (got or "").strip().lower()
        if not e:
            return True
        if not g:
            return False
        return e == g


async def contamination_sweep(
    page,
    committed_rows: list[dict],
    *,
    read_fn: Callable[[Any, dict], Awaitable[str]] | None = None,
    soft_match: Callable[[str, str], bool] | None = None,
) -> dict[str, Any]:
    """Re-read each verified row; return drifts (do not reopen prompts).

    ``read_fn(page, row)`` optional; default uses row selector / automation_id.
    """
    drifts: list[dict] = []
    ok_rows: list[dict] = []
    match_fn = soft_match or _default_soft_match

    for row in committed_rows or []:
        if not isinstance(row, dict):
            continue
        if row.get("verified") is False or row.get("ok") is False:
            continue
        expected = str(
            row.get("readback")
            or row.get("picked")
            or row.get("value")
            or ""
        ).strip()
        if not expected:
            continue
        ftype = str(row.get("type") or "").upper()
        # Skip widgets ChamPro warns not to reopen on false chip-chrome drift.
        # (soft_value_match still applies to gender/state/phone rows below.)
        if ftype in ("HOW_HEARD", "SOURCE", "SCHOOL", "LOCATION"):
            ok_rows.append(row)
            continue
        got = ""
        try:
            if read_fn:
                got = (await read_fn(page, row)) or ""
            else:
                sel = row.get("selector") or ""
                aid = row.get("automation_id") or ""
                loc = None
                if sel:
                    loc = page.locator(str(sel)).first
                elif aid:
                    loc = page.locator(f'[data-automation-id="{aid}"]').first
                if loc is not None and await loc.count():
                    try:
                        got = (await loc.input_value(timeout=800)) or ""
                    except Exception:
                        try:
                            got = ((await loc.inner_text(timeout=800)) or "")[:120]
                        except Exception:
                            got = ""
        except Exception as e:
            drifts.append(
                {
                    "type": ftype,
                    "expected": expected[:80],
                    "got": "",
                    "reason": f"read_error:{e}"[:80],
                }
            )
            continue
        if match_fn(expected, got):
            ok_rows.append(row)
        else:
            drifts.append(
                {
                    "type": ftype,
                    "label": str(row.get("label") or row.get("automation_id") or "")[:80],
                    "expected": expected[:80],
                    "got": (got or "")[:80],
                    "reason": "value_drift",
                }
            )
    return {
        "ok_count": len(ok_rows),
        "drift_count": len(drifts),
        "drifts": drifts[:30],
    }


def apply_contamination_to_report(report: dict, sweep: dict) -> None:
    """Attach sweep results; demote Ready path when drifts exist."""
    report["contamination_sweep"] = {
        "ok_count": sweep.get("ok_count"),
        "drift_count": sweep.get("drift_count"),
        "drifts": sweep.get("drifts") or [],
    }
    if sweep.get("drifts"):
        report["contamination_drifts"] = sweep["drifts"]

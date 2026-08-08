#!/usr/bin/env python3
"""Compare dummy vs real prepare key presence (bool only — no PII values).

Smoke::

    .venv/bin/python scripts/fastfill/parity_report.py --self-test
    # Optional real (gated): FASTFILL_ALLOW_REAL=1 TEST_MODE=0 FASTFILL_REAL_PROFILE=1 \\
    #   .venv/bin/python scripts/fastfill/parity_report.py --real

Never prints passwords, emails, phones, or other PII — only ``key: true|false``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from field_map import (  # noqa: E402
    ADDRESS_CITY,
    ADDRESS_LINE1,
    ADDRESS_STATE,
    APPLYING_FOR,
    CURRENT_COMPANY,
    CURRENT_TITLE,
    DISCIPLINE,
    EDUCATION_END_YEAR,
    EMAIL,
    PASSWORD,
    PHONE,
    YEARS_EXPERIENCE,
)
from run_identity import (  # noqa: E402
    compute_parity_gaps,
    prepare_dummy_run,
)
from web_keys import ensure_password_for_company  # noqa: E402

# Keys asserted after prepare (+ web_keys password for real).
HARD_KEYS = (
    ("EMAIL", EMAIL),
    ("PHONE", PHONE),
    ("PASSWORD", PASSWORD),
    ("YEARS_EXPERIENCE", YEARS_EXPERIENCE),
)
ADDR_KEYS = (
    ("ADDRESS_CITY", ADDRESS_CITY),
    ("ADDRESS_STATE", ADDRESS_STATE),
    ("ADDRESS_LINE1", ADDRESS_LINE1),
)
SOFT_KEYS = (
    ("CURRENT_COMPANY", CURRENT_COMPANY),
    ("CURRENT_TITLE", CURRENT_TITLE),
    ("DISCIPLINE", DISCIPLINE),
    ("EDUCATION_END_YEAR", EDUCATION_END_YEAR),
    ("APPLYING_FOR", APPLYING_FOR),
)


def _populated(values: dict, key: str) -> bool:
    return bool(str(values.get(key) or "").strip())


def presence_map(values: dict) -> dict[str, bool]:
    """Bool map of key presence — never includes actual values."""
    out: dict[str, bool] = {}
    for name, key in HARD_KEYS:
        out[name] = _populated(values, key)
    out["ADDRESS"] = any(_populated(values, k) for _, k in ADDR_KEYS)
    for name, key in SOFT_KEYS:
        out[name] = _populated(values, key)
    return out


def report_dummy(*, compile_pdf: bool = False) -> dict:
    ident = prepare_dummy_run(compile_pdf=compile_pdf)
    values = dict(ident.values)
    # Mirror fast_fill: ensure PASSWORD via web_keys formula even without host.
    ensure_password_for_company("ParitySelfTest", values, host=None, email=ident.email)
    return {
        "mode": "dummy",
        "presence": presence_map(values),
        "parity_gaps": compute_parity_gaps(values),
    }


def report_real(*, job_id: str | None = None) -> dict:
    from field_map import is_real_profile_mode
    from run_identity import prepare_real_run

    if not is_real_profile_mode():
        raise RuntimeError(
            "report_real refused: set FASTFILL_ALLOW_REAL=1 "
            "FASTFILL_REAL_PROFILE=1 TEST_MODE=0"
        )
    ident = prepare_real_run(job_id=job_id)
    values = dict(ident.values)
    ensure_password_for_company(
        "ParityReal",
        values,
        host=None,
        email=ident.email,
    )
    return {
        "mode": "real",
        "presence": presence_map(values),
        "parity_gaps": list(ident.parity_gaps or compute_parity_gaps(values)),
        # Soft-warn only — do not hard-fail on company/title/discipline.
        "soft_warn": [
            g for g in (ident.parity_gaps or []) if str(g).startswith("soft:")
        ],
    }


def self_test() -> dict:
    """Dummy-only smoke: EMAIL/PHONE/PASSWORD/YOE/address must be populated."""
    rep = report_dummy(compile_pdf=False)
    presence = rep["presence"]
    hard_fail = [
        k
        for k in ("EMAIL", "PHONE", "PASSWORD", "YEARS_EXPERIENCE", "ADDRESS")
        if not presence.get(k)
    ]
    # Dummy should also have soft fields from DUMMY_PROFILE
    soft_fail = [
        k
        for k in ("CURRENT_COMPANY", "CURRENT_TITLE", "DISCIPLINE", "APPLYING_FOR")
        if not presence.get(k)
    ]
    return {
        **rep,
        "hard_fail": hard_fail,
        "soft_fail": soft_fail,
        "ok": not hard_fail and not soft_fail,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true", help="Dummy-only presence smoke")
    ap.add_argument(
        "--real",
        action="store_true",
        help="Real prepare (requires FASTFILL_ALLOW_REAL=1 + real env); prints key:bool only",
    )
    ap.add_argument("--job-id", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        out = self_test()
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            for k, v in sorted(out["presence"].items()):
                print(f"{k}: {bool(v)}")
            print(f"ok: {out['ok']}")
            if out["hard_fail"]:
                print(f"hard_fail: {out['hard_fail']}")
            if out["soft_fail"]:
                print(f"soft_fail: {out['soft_fail']}")
        return 0 if out["ok"] else 1

    if args.real:
        os.environ.setdefault("FASTFILL_ALLOW_REAL", "1")
        os.environ.setdefault("FASTFILL_REAL_PROFILE", "1")
        os.environ.setdefault("TEST_MODE", "0")
        out = report_real(job_id=args.job_id)
        if args.json:
            # Still presence-only — no values.
            print(json.dumps(out, indent=2))
        else:
            for k, v in sorted(out["presence"].items()):
                print(f"{k}: {bool(v)}")
            soft = out.get("soft_warn") or []
            if soft:
                print(f"soft_warn: {soft}")
            hard = [
                g
                for g in (out.get("parity_gaps") or [])
                if not str(g).startswith("soft:")
            ]
            if hard:
                print(f"hard_gaps: {hard}")
                return 1
        return 0

    # Default: dummy presence map
    out = report_dummy(compile_pdf=False)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        for k, v in sorted(out["presence"].items()):
            print(f"{k}: {bool(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

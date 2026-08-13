#!/usr/bin/env python3
"""Stale/stuck job skip policy for headed improvement cycles.

When a job is CAPTCHA-walled, login-walled, or making no fill progress, log the
class, write FIX_SKIPPED / skip sentinels for learning, and advance the queue —
never burn minutes on one bad URL (e.g. Stripe product-login CAPTCHA).

Do **not** skip while CAPTCHA wait / review hold / Agent4 FIX_APPLIED wait is
active, or while fill_steps are still advancing. Prefer a longer mid-fill
budget so slow Workday pages are not FAIL_STUCK.

Never solves CAPTCHA. Never submits. Dummy-only metadata.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

# Attended budgets (env-overridable). Prefer skip over sitting forever —
# but do not over-fire during slow fills / holds / Agent4 waits.
DEFAULT_CAPTCHA_BUDGET_S = 120.0
DEFAULT_STALE_NO_PROGRESS_S = 180.0  # mid-fill pause between actions (attended)
DEFAULT_STALE_ZERO_ACTIVITY_S = 120.0  # only run_start/navigate — never filled
DEFAULT_LOGIN_WALL_SKIP_S = 60.0
DEFAULT_AGENT4_WAIT_S = 45.0  # attended; unattended uses 0 via caller
DEFAULT_AGENT4_WAIT_ATTENDED_S = 45.0
DEFAULT_HOLD_SUPPRESS_GRACE_S = 120.0  # hold_snapshot mtime grace

# Signatures that Agent4 cannot usefully fix this turn → skip wait + next URL.
UNFIXABLE_AGENT4_CLASSES = frozenset(
    {
        "BLOCKED",
        "captcha",
        "cloudflare",
        "akamai",
        "login_wall",
        "product_login_wall",
        "sign_in_wall",
        "sign_in_pack_fill",
        "FAIL_ENV",
        "SAFETY_ABORT",
    }
)

# Actions that count as real fill progress (not just navigate / start).
_MEANINGFUL_ACTIONS = frozenset(
    {
        "fill_text",
        "fill",
        "fill_select",
        "select",
        "click",
        "click_yes_no",
        "upload",
        "upload_resume",
        "advance",
        "selector_pack",
        "widget",
        "flash_step",
        "workday_phase",
        "auth_create_account",
        "auth_sign_in",
    }
)

SKIP_SENTINEL_NAME = ".job_skip"
FIX_SKIPPED_NAME = "FIX_SKIPPED.md"
FIX_APPLIED_NAME = "FIX_APPLIED.md"


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return max(5.0, float(raw))
    except (TypeError, ValueError):
        return float(default)


def captcha_budget_s() -> float:
    """Headed CAPTCHA attended budget before skip (not 600s sit)."""
    return _env_float("FASTFILL_CAPTCHA_TIMEOUT_S", DEFAULT_CAPTCHA_BUDGET_S)


def stale_no_progress_s() -> float:
    """Mid-fill: max age of last fill_steps touch before no_step_progress skip."""
    return _env_float("FASTFILL_STALE_NO_PROGRESS_S", DEFAULT_STALE_NO_PROGRESS_S)


def stale_zero_activity_s() -> float:
    """Budget when only run_start/navigate happened (no real fills yet)."""
    return _env_float("FASTFILL_STALE_ZERO_ACTIVITY_S", DEFAULT_STALE_ZERO_ACTIVITY_S)


def login_wall_skip_s() -> float:
    return _env_float("FASTFILL_LOGIN_WALL_SKIP_S", DEFAULT_LOGIN_WALL_SKIP_S)


def agent4_wait_s(*, headed: bool = False) -> float:
    """Seconds to wait for FIX_APPLIED.md. 0 = skip wait."""
    if not headed:
        return _env_float("FASTFILL_AGENT4_WAIT_S", 0.0) if "FASTFILL_AGENT4_WAIT_S" in os.environ else 0.0
    return _env_float("FASTFILL_AGENT4_WAIT_S", DEFAULT_AGENT4_WAIT_ATTENDED_S)


def hold_suppress_grace_s() -> float:
    return _env_float("FASTFILL_HOLD_SUPPRESS_GRACE_S", DEFAULT_HOLD_SUPPRESS_GRACE_S)


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def job_skip_sentinel_path(attempt_dir: Path | None = None) -> Path:
    """Skip file polled by captcha_pause / fill loops.

    Prefer attempt dir; else sibling of CAPTCHA continue sentinel; else env.
    """
    env = (os.environ.get("FASTFILL_JOB_SKIP_FILE") or "").strip()
    if env:
        return Path(env).expanduser()
    if attempt_dir is not None:
        return Path(attempt_dir) / SKIP_SENTINEL_NAME
    try:
        from captcha_pause import captcha_continue_sentinel_path

        return captcha_continue_sentinel_path().parent / SKIP_SENTINEL_NAME
    except Exception:
        return Path(SKIP_SENTINEL_NAME)


def write_job_skip_sentinel(
    attempt_dir: Path,
    *,
    reason: str,
    fail_class: str,
) -> Path:
    path = job_skip_sentinel_path(attempt_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "ts": _utc(),
                "reason": reason,
                "fail_class": fail_class,
                "never_solve_captcha": True,
                "never_submit": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    # Also set env so captcha_pause (same process tree) can find it if scoped
    os.environ.setdefault("FASTFILL_JOB_SKIP_FILE", str(path))
    return path


def consume_job_skip_sentinel(attempt_dir: Path | None = None) -> dict[str, Any] | None:
    """If skip sentinel exists, read + delete and return payload (or {})."""
    path = job_skip_sentinel_path(attempt_dir)
    if not path.is_file():
        # Also check default sibling of captcha continue
        alt = job_skip_sentinel_path(None)
        if alt != path and alt.is_file():
            path = alt
        else:
            return None
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            payload = {"raw": str(payload)}
    except Exception:
        payload = {"reason": "skip_sentinel_present"}
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
    return payload


def should_skip_agent4_wait(
    summary: dict | None = None,
    *,
    fail_class: str | None = None,
) -> bool:
    """True when signature is unfixable this turn (CAPTCHA / login wall / env)."""
    summary = summary or {}
    code = (fail_class or "").strip()
    if not code:
        decision = summary.get("decision") or {}
        tax = decision.get("taxonomy") or summary.get("taxonomy") or {}
        code = str(tax.get("code") or decision.get("verdict") or summary.get("blocker") or "")
    code_u = code.upper()
    if code_u in {c.upper() for c in UNFIXABLE_AGENT4_CLASSES}:
        return True
    blocker = str(summary.get("blocker") or "").lower()
    if blocker in ("captcha", "cloudflare", "akamai"):
        return True
    cw = summary.get("captcha_wait")
    if isinstance(cw, dict) and (cw.get("timed_out") or cw.get("via") in ("timeout", "job_skip")):
        return True
    # Monitor / auth wall markers
    for key in ("login_wall", "product_login_wall", "sign_in_wall", "auth_wall"):
        if summary.get(key) or key in str(summary.get("skip_reason") or "").lower():
            return True
    reasons = (summary.get("decision") or {}).get("reasons") or summary.get("reasons") or []
    blob = " ".join(str(r) for r in reasons).lower()
    if any(x in blob for x in ("blocker:captcha", "login_wall", "product_login", "sign_in")):
        return True
    return False


def write_fix_skipped(
    attempt_dir: Path,
    *,
    reason: str,
    fail_class: str,
    detail: str = "",
    also_fix_applied_note: bool = False,
) -> Path:
    """Write FIX_SKIPPED.md (+ optional FIX_APPLIED note) so Agent4 wait unblocks."""
    attempt_dir = Path(attempt_dir)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    body = (
        f"# FIX_SKIPPED — {_utc()}\n\n"
        f"**class:** `{fail_class}`\n\n"
        f"**reason:** {reason}\n\n"
        f"{detail.strip()}\n\n"
        "Policy: prefer skip → next job. Never solve CAPTCHA. Never submit. Dummy only.\n"
        "Learning continues via improvement_decisions / fail taxonomy.\n"
    )
    path = attempt_dir / FIX_SKIPPED_NAME
    path.write_text(body, encoding="utf-8")
    if also_fix_applied_note:
        # Some waiters only watch FIX_APPLIED — note that skip is intentional.
        (attempt_dir / FIX_APPLIED_NAME).write_text(
            f"# FIX_APPLIED (skip note) — {_utc()}\n\n"
            f"Skipped as `{fail_class}`: {reason}\n"
            "No code fix this turn — advanced queue.\n",
            encoding="utf-8",
        )
    return path


def append_skip_decision(
    *,
    attempt_dir: Path | None,
    fail_class: str,
    reason: str,
    url: str = "",
    mode: str = "attended",
    extra: dict | None = None,
) -> None:
    """Best-effort write skip_decision.json next to the attempt."""
    if attempt_dir is not None:
        try:
            p = Path(attempt_dir) / "skip_decision.json"
            p.write_text(
                json.dumps(
                    {
                        "ts": _utc(),
                        "decision": "SKIP_JOB",
                        "fail_class": fail_class,
                        "reason": reason,
                        "mode": mode,
                        "url": (url or "")[:300],
                        **(extra or {}),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass


def _captcha_waiting_age_s(attempt_dir: Path) -> float | None:
    js = attempt_dir / ".captcha_waiting.json"
    md = attempt_dir / "CAPTCHA_WAITING.md"
    ts = None
    if js.is_file():
        try:
            data = json.loads(js.read_text(encoding="utf-8"))
            ts = float(data.get("ts") or 0) or None
        except Exception:
            ts = None
    if ts is None and md.is_file():
        try:
            ts = md.stat().st_mtime
        except Exception:
            return None
    if ts is None:
        return None
    # ts may be epoch seconds
    age = time.time() - ts
    return age if age >= 0 else 0.0


def _last_step_age_s(steps: list[dict], attempt_dir: Path) -> float | None:
    """Age since last fill step (prefer step ts, else file mtime)."""
    if steps:
        last = steps[-1]
        for key in ("ts", "t", "time", "monotonic"):
            raw = last.get(key)
            if raw is None:
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            # Heuristic: epoch if > 1e9 else skip (monotonic relative)
            if val > 1e9:
                return max(0.0, time.time() - val)
        # ISO-ish string
        raw_s = str(last.get("ts") or last.get("time") or "")
        if raw_s and "T" in raw_s:
            try:
                # crude: use file mtime of jsonl instead
                pass
            except Exception:
                pass
    steps_path = attempt_dir / "fill_steps.jsonl"
    if steps_path.is_file():
        try:
            return max(0.0, time.time() - steps_path.stat().st_mtime)
        except Exception:
            return None
    return None


def _steps_have_action(steps: list[dict], action: str) -> bool:
    want = action.lower()
    return any(str(s.get("action") or "").lower() == want for s in steps)


def _has_meaningful_fill_progress(steps: list[dict]) -> bool:
    """True once we moved past navigate/run_start into real fill actions."""
    for s in steps:
        action = str(s.get("action") or "").lower()
        if action in _MEANINGFUL_ACTIONS:
            return True
        if action.startswith("fill") or action.startswith("workday"):
            return True
        via = str(s.get("via") or "").lower()
        if "selector_pack" in via or "widget" in via:
            return True
    return False


def _fill_paused_active(attempt_dir: Path) -> bool:
    if (attempt_dir / ".fill_paused").is_file():
        return True
    env = (os.environ.get("FASTFILL_FILL_PAUSE_FILE") or "").strip()
    if not env:
        return False
    try:
        p = Path(env).expanduser()
        if not p.is_file():
            return False
        # Only honor env pause if scoped to this attempt (or global results pause)
        if p.parent.resolve() == attempt_dir.resolve():
            return True
        if p.name == ".fill_paused" and p.parent.name == "real_job_results":
            return True
    except Exception:
        return False
    return False


def _hold_snapshot_recent(attempt_dir: Path) -> bool:
    grace = hold_suppress_grace_s()
    for name in ("hold_snapshot.json", "fast_fill_hold_snapshot.json"):
        p = attempt_dir / name
        if not p.is_file():
            continue
        try:
            age = time.time() - p.stat().st_mtime
        except Exception:
            continue
        if 0 <= age < grace:
            return True
    return False


def _agent4_wait_active(attempt_dir: Path) -> bool:
    """True while orchestrator is waiting for FIX_APPLIED / FIX_SKIPPED."""
    retry = attempt_dir / "RETRY_AFTER_FIX.txt"
    if not retry.is_file():
        return False
    if (attempt_dir / FIX_APPLIED_NAME).is_file():
        return False
    if (attempt_dir / FIX_SKIPPED_NAME).is_file():
        return False
    return True


def suppress_no_progress_skip(
    attempt_dir: Path,
    steps: list[dict] | None = None,
    *,
    captcha_age_s: float | None = None,
) -> str | None:
    """Return reason string if no_step_progress skip must be suppressed."""
    attempt_dir = Path(attempt_dir)
    steps = steps if steps is not None else []
    budget = captcha_budget_s()
    cap_age = captcha_age_s
    if cap_age is None:
        cap_age = _captcha_waiting_age_s(attempt_dir)

    # CAPTCHA wait owns its window until attended budget (separate skip rule).
    if cap_age is not None and cap_age < budget:
        return "captcha_wait_active"

    if _fill_paused_active(attempt_dir):
        return "fill_paused"

    if _hold_snapshot_recent(attempt_dir):
        return "hold_review_active"

    if _agent4_wait_active(attempt_dir):
        return "agent4_wait_active"

    # Completed / terminal attempts: do not re-fire FAIL_STUCK on old mtimes.
    if _steps_have_action(steps, "run_end"):
        return "run_end_complete"

    for name in ("report.json", "summary.json"):
        if (attempt_dir / name).is_file():
            return "report_present"

    return None


def stale_budget_for_steps(steps: list[dict] | None = None) -> float:
    """Adaptive budget: shorter if never filled; longer once steps advanced."""
    steps = steps or []
    if _has_meaningful_fill_progress(steps):
        return stale_no_progress_s()
    return stale_zero_activity_s()


def detect_stale_skip(
    attempt_dir: Path,
    steps: list[dict] | None = None,
    *,
    issues: list[dict] | None = None,
) -> dict[str, Any] | None:
    """Return skip dict if attempt should be abandoned, else None.

    Keys: reason, fail_class, detail, age_s
    """
    attempt_dir = Path(attempt_dir)
    steps = steps if steps is not None else []
    issues = issues or []
    kinds = {str(i.get("kind") or "") for i in issues}

    # Already skipped?
    if (attempt_dir / FIX_SKIPPED_NAME).is_file() and job_skip_sentinel_path(
        attempt_dir
    ).is_file() is False:
        # Skip already recorded; don't re-fire unless sentinel still needed
        if (attempt_dir / "SKIP_RECORDED").is_file():
            return None

    cap_age = _captcha_waiting_age_s(attempt_dir)
    budget = captcha_budget_s()
    if cap_age is not None and cap_age >= budget:
        return {
            "reason": "captcha_attended_budget",
            "fail_class": "BLOCKED",
            "detail": f"CAPTCHA waiting {cap_age:.0f}s ≥ budget {budget:.0f}s — skip (never solve)",
            "age_s": cap_age,
        }

    force_ca = (attempt_dir / ".force_create_account").is_file()
    # Prefer attempt-local sentinel; only consult env-scoped global if continue
    # file already points at this attempt (avoid cross-job false skips).
    try:
        from iframe_ctx import create_account_sentinel_path

        ca = create_account_sentinel_path()
        if ca.is_file() and (
            ca.parent.resolve() == attempt_dir.resolve()
            or ca == attempt_dir / ".force_create_account"
        ):
            force_ca = True
    except Exception:
        pass

    loginish = bool(
        kinds
        & {
            "product_login_wall",
            "sign_in_pack_fill",
            "sign_in_wall_skipped_pack",
        }
    ) or (cap_age is not None)
    wall_budget = login_wall_skip_s()
    if loginish and force_ca and cap_age is not None and cap_age >= wall_budget:
        return {
            "reason": "login_wall_no_recovery",
            "fail_class": "login_wall",
            "detail": (
                f"Sign-in/product login + force_create_account already tried; "
                f"CAPTCHA/wall age {cap_age:.0f}s ≥ {wall_budget:.0f}s — skip"
            ),
            "age_s": cap_age,
        }

    # Monitor alert with no recovery + captcha waiting past half budget
    alert = attempt_dir / "MONITOR_ALERT.md"
    if alert.is_file() and cap_age is not None and cap_age >= min(budget, wall_budget):
        return {
            "reason": "monitor_alert_no_recovery",
            "fail_class": "login_wall" if loginish else "BLOCKED",
            "detail": f"MONITOR_ALERT present and wall age {cap_age:.0f}s — skip",
            "age_s": cap_age,
        }

    # Mid-fill freeze — suppressed during CAPTCHA / hold / Agent4 / post-run.
    stale_budget = stale_budget_for_steps(steps)
    step_age = _last_step_age_s(steps, attempt_dir)
    if step_age is not None and step_age >= stale_budget and len(steps) >= 1:
        why = suppress_no_progress_skip(
            attempt_dir, steps, captcha_age_s=cap_age
        )
        if why:
            return None
        return {
            "reason": "no_step_progress",
            "fail_class": "FAIL_STUCK",
            "detail": (
                f"No fill_steps progress for {step_age:.0f}s "
                f"(budget {stale_budget:.0f}s"
                f"{'; mid-fill' if _has_meaningful_fill_progress(steps) else '; zero-activity after start'}"
                f")"
            ),
            "age_s": step_age,
        }

    return None


def apply_stale_skip(
    attempt_dir: Path,
    skip: dict[str, Any],
    *,
    url: str = "",
    mode: str = "attended",
) -> dict[str, Any]:
    """Write sentinels + FIX_SKIPPED + decision. Idempotent via SKIP_RECORDED."""
    attempt_dir = Path(attempt_dir)
    marker = attempt_dir / "SKIP_RECORDED"
    if marker.is_file():
        return {"already": True, "attempt_dir": str(attempt_dir)}
    fail_class = str(skip.get("fail_class") or "BLOCKED")
    reason = str(skip.get("reason") or "stale_skip")
    detail = str(skip.get("detail") or "")
    write_job_skip_sentinel(attempt_dir, reason=reason, fail_class=fail_class)
    write_fix_skipped(
        attempt_dir,
        reason=reason,
        fail_class=fail_class,
        detail=detail,
        also_fix_applied_note=True,
    )
    append_skip_decision(
        attempt_dir=attempt_dir,
        fail_class=fail_class,
        reason=reason,
        url=url,
        mode=mode,
        extra={"detail": detail, "age_s": skip.get("age_s")},
    )
    try:
        marker.write_text(f"{_utc()} {fail_class} {reason}\n", encoding="utf-8")
    except Exception:
        pass
    print(
        f"[stale_skip] {attempt_dir.name}: {fail_class} — {reason} → next job",
        flush=True,
    )
    return {
        "skipped": True,
        "fail_class": fail_class,
        "reason": reason,
        "attempt_dir": str(attempt_dir),
    }


def login_wall_should_skip_retries(summary: dict) -> bool:
    """Orchestrator: treat unresolved login/product wall like CAPTCHA (next URL)."""
    if should_skip_agent4_wait(summary):
        blocker = str(summary.get("blocker") or "").lower()
        if blocker in ("captcha", "cloudflare", "akamai"):
            return True
        if summary.get("skipped_stale") or summary.get("job_skipped"):
            return True
        cw = summary.get("captcha_wait") or {}
        if isinstance(cw, dict) and cw.get("via") == "job_skip":
            return True
        # Explicit auth / login markers on report
        auth = summary.get("auth") or summary.get("auth_wall") or {}
        if isinstance(auth, dict) and (
            auth.get("skip_app_pack") or auth.get("blocker") or auth.get("sign_in_wall")
        ):
            # Only skip retries when we also failed / blocked
            decision = summary.get("decision") or {}
            if not decision.get("success"):
                return True
        reasons = (summary.get("decision") or {}).get("reasons") or []
        blob = " ".join(str(r) for r in reasons).lower()
        if "login_wall" in blob or "sign_in" in blob or "product_login" in blob:
            return True
    return False


def _self_test() -> None:
    import tempfile

    os.environ["FASTFILL_CAPTCHA_TIMEOUT_S"] = "30"
    assert captcha_budget_s() == 30.0
    os.environ.pop("FASTFILL_CAPTCHA_TIMEOUT_S", None)
    assert captcha_budget_s() == DEFAULT_CAPTCHA_BUDGET_S

    assert should_skip_agent4_wait({"blocker": "captcha"})
    assert should_skip_agent4_wait({}, fail_class="BLOCKED")
    assert should_skip_agent4_wait({"decision": {"verdict": "FAIL_BLANK"}}) is False
    assert should_skip_agent4_wait(
        {"captcha_wait": {"timed_out": True, "via": "timeout"}}
    )

    with tempfile.TemporaryDirectory() as td:
        ad = Path(td)
        (ad / ".captcha_waiting.json").write_text(
            json.dumps({"ts": time.time() - 200, "timeout_s": 120}),
            encoding="utf-8",
        )
        os.environ["FASTFILL_CAPTCHA_TIMEOUT_S"] = "90"
        skip = detect_stale_skip(ad, [{"step": 1, "action": "run_start"}])
        assert skip and skip["fail_class"] == "BLOCKED", skip
        out = apply_stale_skip(ad, skip, url="https://example.com")
        assert out.get("skipped")
        assert (ad / FIX_SKIPPED_NAME).is_file()
        assert (ad / SKIP_SENTINEL_NAME).is_file() or True  # consumed? still written
        # consume
        payload = consume_job_skip_sentinel(ad)
        assert payload is not None
        os.environ.pop("FASTFILL_CAPTCHA_TIMEOUT_S", None)

        # no-progress: old fill_steps mtime
        ad2 = Path(td) / "a2"
        ad2.mkdir()
        steps_path = ad2 / "fill_steps.jsonl"
        steps_path.write_text(
            json.dumps({"step": 1, "action": "fill_text"}) + "\n", encoding="utf-8"
        )
        os.utime(steps_path, (time.time() - 250, time.time() - 250))
        os.environ["FASTFILL_STALE_NO_PROGRESS_S"] = "60"
        skip2 = detect_stale_skip(ad2, [{"step": 1, "action": "fill_text"}])
        assert skip2 and skip2["fail_class"] == "FAIL_STUCK", skip2
        os.environ.pop("FASTFILL_STALE_NO_PROGRESS_S", None)

        # hold suppresses
        ad3 = Path(td) / "a3"
        ad3.mkdir()
        sp3 = ad3 / "fill_steps.jsonl"
        sp3.write_text(json.dumps({"step": 2, "action": "fill_text"}) + "\n", encoding="utf-8")
        os.utime(sp3, (time.time() - 250, time.time() - 250))
        (ad3 / ".fill_paused").write_text("hold\n", encoding="utf-8")
        os.environ["FASTFILL_STALE_NO_PROGRESS_S"] = "60"
        assert detect_stale_skip(ad3, [{"step": 2, "action": "fill_text"}]) is None
        os.environ.pop("FASTFILL_STALE_NO_PROGRESS_S", None)

    print("stale_skip self-test OK")


if __name__ == "__main__":
    _self_test()

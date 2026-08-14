#!/usr/bin/env python3
"""Platform-agnostic blazing-fast fill orchestrator (0-LLM path).

Universal coverage (product requirement):
  - Known ATS → detect_platform + SELECTOR_PACKS (Greenhouse/Workday/Lever/…)
  - Unknown / non-ATS company career pages → GENERIC_SELECTOR_PACK + extract/classify
  - Never give up solely because platform==unknown; leftovers listed; Flash opt-in only

Single entry point other agents call:

    from fast_fill import run_fast_fill
    report = run_fast_fill(url)                      # sync (headless by default)
    report = run_fast_fill(url, headed=True)         # visible Chromium window
    report = await run_fast_fill_async(url, headed=True)

CLI (interactive demos default to a visible browser; use --headless for CI):

    skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL --headed
    skyvern_runtime/venv/bin/python scripts/fastfill/fast_fill.py URL --headless \
        --out results.json

Hard rules (enforced here):
  - DUMMY_PROFILE / DUMMY_PDF only — never real profile.json PII
  - NEVER submit (button_gate on every click; FINAL refused)
  - Flash/Skyvern only for leftovers returned in report["leftovers"]
  - CAPTCHA / bot-detection → never solve; headed → pause for human then continue;
    headless → blocker (cannot solve)
  - Dummy resume must attach+verify; missing resume = FAIL
  - Headed/cycle: hold browser open; in-session leftover refill passes before close

Flow:
  1. detect_platform(url)  # may be 'unknown' — still fills via generic DOM
  2. ENTRY pre-pass via button_gate (entry_prepass / cli_entry_prepass)
  3. Optional platform selector pack (ats_notes-derived) or GENERIC pack
  4. extract_form_fields.js → field_map.classify_field → fill (0 LLM)
  5. Learned allow-list (learning.lookup_learned) for policy leftovers
  6. Greenhouse: gh_select.py react-select widgets; else combobox heuristic
  7. Return leftovers for Flash/Skyvern handoff (does not call Flash unless
     --flash-leftovers is explicitly enabled by the caller)
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import logging
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
_log = logging.getLogger("fast_fill")

from button_gate import (  # noqa: E402
    NAV_KINDS,
    gate_click,
    gate_locator_click,
)
from button_map import FINAL, UNKNOWN, classify_button  # noqa: E402
from field_map import (  # noqa: E402
    ADDRESS_CITY,
    ADDRESS_COUNTY,
    ADDRESS_COUNTRY,
    ADDRESS_LINE1,
    ADDRESS_LINE2,
    ADDRESS_STATE,
    ADDRESS_ZIP,
    ACCOMMODATIONS,
    ACCOMMODATIONS_DETAILS,
    COVER_LETTER,
    CURRENT_COMPANY,
    DUMMY_PROFILE,
    DUMMY_PDF,
    EDUCATION_END_YEAR,
    EDUCATION_START_YEAR,
    EMAIL,
    EMPLOYEE_REFERRAL,
    GENDER,
    GITHUB,
    HISPANIC,
    HOW_HEARD,
    INTEREST,
    LINKEDIN,
    LOCATION,
    MARKETING_CONSENT,
    NAME_FIRST,
    NAME_FULL,
    NAME_LAST,
    NAME_MIDDLE,
    NOTICE_PERIOD,
    PASSWORD,
    PASSWORD_CONFIRM,
    PHONE,
    PHONE_EXTENSION,
    PORTFOLIO,
    RACE,
    REFERRAL_EMAIL,
    RELATIVE_NAME,
    RELOCATION,
    RESUME_UPLOAD,
    SALARY_CURRENT,
    SALARY_EXPECTED,
    SCHOOL,
    DEGREE,
    DISCIPLINE,
    MAJOR,
    FIELD_OF_STUDY,
    TALENT_HUB,
    TERMS_CONSENT,
    TWITTER,
    US_RESIDENCE,
    US_CITIZEN,
    CLEARANCE,
    CLEARANCE_TYPE,
    VISA_STATUS,
    VETERAN,
    DISABILITY,
    WORK_AUTH,
    SPONSORSHIP,
    WORKED_HERE_BEFORE,
    apply_resolved_address,
    classify_field,
    is_phone_extension_field,
    is_short_numeric_field,
    OPTIONAL_LEAVE_BLANK_TYPES,
    validate_filled,
    value_ok_for_field_shape,
)
from address_resolver import resolve_address_for_resume  # noqa: E402
from run_identity import prepare_dummy_run  # noqa: E402
from gh_select import (  # noqa: E402
    aliases_for,
    fill_gh_select,
    fill_other_specify,
    is_post_resume_reassert_via,
)
from learning import lookup_learned  # noqa: E402
from ashby_widgets import (  # noqa: E402
    click_ashby_choice_option,
    fill_ashby_location_then_zip,
    fill_ashby_url_by_label,
    fill_ashby_widgets,
    is_empty_ui_value,
    live_ashby_url_readback,
    reassert_ashby_contact_after_resume,
)
from lever_widgets import fill_lever_widgets  # noqa: E402
from captcha_pause import (  # noqa: E402
    CAPTCHA_BLOCKERS,
    DEFAULT_CAPTCHA_TIMEOUT_S,
    captcha_waiting_marker_paths,
    handle_captcha_blocker,
    page_shows_interactive_captcha,
    resolve_captcha_wait,
)
from fill_pause import (  # noqa: E402
    consume_fill_continue_sentinel,
    drain_pause_before_close,
    enter_hold_continue_mode,
    ensure_fill_pause_ready,
    install_fill_pause_on_context,
    note_fill_activity,
    push_fill_activity,
    read_fill_pause_state,
    resolve_fill_pause,
    set_fill_paused,
    should_keep_fill_browser_open,
    wait_while_paused,
)
from resume_upload import (  # noqa: E402
    apply_resume_success_gate,
    ensure_resume_uploaded,
    is_resume_attachment_row,
    report_has_verified_resume,
    resume_pdf_from_values,
    sync_resume_verified_from_phase_a,
)
from field_attempt_log import (  # noqa: E402
    FieldAttemptLog,
    attach_attempt_log,
    note_attempt,
)
from fill_step_log import (  # noqa: E402
    FillStepLog,
    attach_fill_step_log,
    emit_filled_rows_as_steps,
    emit_leftover_rows_as_steps,
    finalize_step_log,
    log_row_as_step,
    note_step,
)
from fill_verify import is_verified_fill_row  # noqa: E402


def resolve_playwright_chromium_executable() -> str | None:
    """Prefer an existing Chromium binary (arm64 on Apple Silicon).

    Playwright sometimes reports chrome-mac-x64 on arm64 hosts when the
    interpreter is under Rosetta / mis-detected; fall back to chrome-mac-arm64.
    Searches PLAYWRIGHT_BROWSERS_PATH **and** the host default cache so a
    sandbox-scoped path cannot hide a working arm64 install.
    """
    import platform as _platform

    env = (os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or "").strip()
    if env and Path(env).exists():
        return env

    machine = _platform.machine().lower()
    prefer = ["arm64", "x64"] if machine in ("arm64", "aarch64") else ["x64", "arm64"]

    search_roots: list[Path] = []
    env_browsers = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if env_browsers:
        search_roots.append(Path(env_browsers).expanduser())
    default_browsers = Path.home() / "Library/Caches/ms-playwright"
    if default_browsers not in search_roots:
        search_roots.append(default_browsers)
    # Linux / CI common path
    linux_default = Path.home() / ".cache/ms-playwright"
    if linux_default not in search_roots:
        search_roots.append(linux_default)

    candidates: list[tuple[str, Path]] = []
    for browsers in search_roots:
        if not browsers.is_dir():
            continue
        roots = sorted(browsers.glob("chromium-*"), reverse=True)
        for arch in prefer:
            for root in roots:
                cand = (
                    root
                    / f"chrome-mac-{arch}"
                    / "Google Chrome for Testing.app"
                    / "Contents"
                    / "MacOS"
                    / "Google Chrome for Testing"
                )
                if cand.is_file():
                    candidates.append((arch, cand))
                # Headless shell / chrome-linux layout
                for linux_name in (
                    f"chrome-linux-{arch}",
                    "chrome-linux",
                ):
                    linux_cand = root / linux_name / "chrome"
                    if linux_cand.is_file():
                        candidates.append((arch if "arm" in linux_name else "x64", linux_cand))

    for arch in prefer:
        for a, cand in candidates:
            if a == arch:
                return str(cand)

    # Last resort: whatever Playwright thinks (may be missing)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            exe = p.chromium.executable_path
            if exe and Path(exe).exists():
                # On arm64, refuse x64 path if we somehow missed arm64 above
                if machine in ("arm64", "aarch64") and "chrome-mac-x64" in str(exe):
                    return None
                return exe
    except Exception:
        pass
    return None


def _attempt_log_from_report(report: dict | None) -> FieldAttemptLog | None:
    log = (report or {}).get("_attempt_log")
    return log if isinstance(log, FieldAttemptLog) else None


def _resolve_attempt_cycle_dir(
    report: dict,
    *,
    out: Path | str | None = None,
    screenshot: bool | Path | None = None,
) -> Path:
    """Prefer --out parent or screenshot parent under real_job_results."""
    if out:
        return Path(out).expanduser().resolve().parent
    for key in ("report_path", "hold_snapshot", "screenshot"):
        rp = report.get(key)
        if rp:
            return Path(str(rp)).expanduser().resolve().parent
    if isinstance(screenshot, (str, Path)) and screenshot is not True:
        return Path(screenshot).expanduser().resolve().parent
    rid = str(
        report.get("alias_token")
        or report.get("email_alias")
        or f"run_{int(time.time())}"
    )
    return ROOT / "skyvern_runtime" / "real_job_results" / f"field_attempts_{rid}"


def _attach_field_attempt_log(
    report: dict,
    *,
    out: Path | str | None = None,
    screenshot: bool | Path | None = None,
) -> FieldAttemptLog | None:
    """Attach FieldAttemptLog for this run. Never raises into the fill path."""
    try:
        if out:
            report["report_path"] = str(Path(out).expanduser().resolve())
        cycle_dir = _resolve_attempt_cycle_dir(report, out=out, screenshot=screenshot)
        log = attach_attempt_log(report, cycle_dir=cycle_dir)
        report["field_attempt_log_path"] = str(log.jsonl_path)
        print(
            f"[attempt_log] attached → {log.jsonl_path} (cycle_dir={cycle_dir})",
            flush=True,
        )
        return log
    except Exception as e:
        report.setdefault("errors", []).append({"field_attempt_attach": str(e)[:160]})
        return None


def _attach_fill_step_log(
    report: dict,
    *,
    out: Path | str | None = None,
    screenshot: bool | Path | None = None,
) -> FillStepLog | None:
    """Attach ordered fill step log (JSONL + markdown). Never raises."""
    try:
        cycle_dir = _resolve_attempt_cycle_dir(report, out=out, screenshot=screenshot)
        log = attach_fill_step_log(report, out_dir=cycle_dir)
        try:
            from field_lock import attach_field_locks

            attach_field_locks(report)
        except Exception as e:
            report.setdefault("errors", []).append({"field_lock_attach": str(e)[:120]})
        try:
            from action_supervisor import attach_action_supervisor

            attach_action_supervisor(report)
        except Exception as e:
            report.setdefault("errors", []).append({"action_supervisor_attach": str(e)[:120]})
        try:
            from flight_recorder import attach_flight_recorder, note_flight

            flight = attach_flight_recorder(report, out_dir=cycle_dir)
            if flight is not None:
                print(
                    f"[flight] attached → {flight.jsonl_path} (+ {flight.log_path.name})",
                    flush=True,
                )
                note_flight(
                    report,
                    "run_start",
                    action="run_start",
                    layer="fast_fill",
                    gate_kind="flight",
                    gate_result="on",
                    gate_reason=f"headed={bool(report.get('headed'))}",
                )
        except Exception as e:
            report.setdefault("errors", []).append({"flight_attach": str(e)[:120]})
        print(
            f"[step_log] attached → {log.jsonl_path} (cycle_dir={cycle_dir})",
            flush=True,
        )
        return log
    except Exception as e:
        report.setdefault("errors", []).append({"fill_step_attach": str(e)[:160]})
        return None


def _log_fill_step_from_row(
    report: dict | None,
    row: dict,
    *,
    action: str | None = None,
    before: str | None = None,
    pass_i: int | None = None,
) -> None:
    """Mirror a filled/skip row into the ordered step log (streams to terminal)."""
    if not report or not isinstance(row, dict):
        return
    try:
        log_row_as_step(report, row, action=action, before=before, pass_i=pass_i)
    except Exception as e:
        _log.debug("fill step log emit failed: %s", e)


def _record_fill_attempt(
    report: dict | None,
    row: dict,
    *,
    success: bool | None = None,
    pass_i: int | None = None,
    via_override: str | None = None,
) -> None:
    """Mid-pass success telemetry only — fail counts owned by ingest_pass."""
    if not report:
        return
    # Log skips and successes to ordered step log
    if row.get("skipped_already_correct") or row.get("reason") in (
        "already_correct_skip",
        "already_correct_keep",
    ):
        _log_fill_step_from_row(report, row, pass_i=pass_i)
        return
    log = _attempt_log_from_report(report)
    if log is None:
        return
    # Infer success; skip failures here to avoid double-counting with ingest_pass
    if success is None:
        if row.get("ok") is False:
            return
        if row.get("verified") is False and row.get("ok") is not True:
            return
        success = bool(row.get("verified") or row.get("ok"))
    if not success:
        return
    _log_fill_step_from_row(report, row, pass_i=pass_i)
    try:
        log.record_from_row(
            row, success=True, pass_i=pass_i, via_override=via_override
        )
    except Exception:
        pass


def _record_playbook_success(
    report: dict | None,
    field_type: str | None,
    selector: str | None,
    meta: dict | None,
) -> None:
    """Best-effort playbook cache hit after a verified select succeeds."""
    if not report or not field_type:
        return
    try:
        from playbooks import detect_playbook
        from record_replay import record_playbook_hit

        m = dict(meta or {})
        m.setdefault("platform", report.get("platform") or "")
        pb = detect_playbook(m)
        record_playbook_hit(
            str(report.get("url") or ""),
            str(report.get("platform") or "unknown"),
            str(field_type),
            pb,
            selector=str(selector or ""),
            ok=True,
        )
    except Exception:
        pass


def _field_capped_unfillable(
    report: dict | None,
    *,
    field_type: str | None = None,
    label: str | None = None,
    selector: str | None = None,
) -> bool:
    """Stop rewrite after 2 fails (align with UNFILLABLE_AFTER_2)."""
    log = _attempt_log_from_report(report)
    if log is None:
        return False
    try:
        return bool(
            log.is_unfillable(
                field_type=field_type, label=label, selector=selector
            )
        )
    except Exception:
        return False


def _playwright_sel(sel: str) -> str:
    """Strip CSS pseudos Playwright rejects (e.g. ``:visible`` from extract)."""
    s = (sel or "").strip()
    if not s:
        return s
    # Playwright locator CSS does not support jQuery-like :visible
    s = re.sub(r":visible\b", "", s, flags=re.I)
    s = re.sub(r":hidden\b", "", s, flags=re.I)
    return s


async def _locator_already_correct(
    loc,
    intended: str,
    *,
    field_type: str = "",
    label: str = "",
) -> tuple[bool, str]:
    """If live value already matches intended dummy, do not clear/retype.

    For react-select / combobox filters, never treat ``input_value`` (typed
    but uncommitted filter text) as a committed match — read the display.

    For Places/Location comboboxes, accept committed ``City, State, Country``
    lines matching dummy aliases (Airwallex Springfield thrash fix).

    Select / date / salary types never soft-skip on empty or placeholder
    commit (commit-verify contract).
    """
    _COMMIT_STRICT = frozenset(
        {
            SALARY_EXPECTED,
            SALARY_CURRENT,
            SCHOOL,
            DEGREE,
            "HOW_HEARD",
            "WORK_AUTH",
            "SPONSORSHIP",
            "GENDER",
            "RACE",
            "VETERAN",
            "DISABILITY",
        }
    )
    try:
        from verified_select import (
            is_location_field,
            is_placeholder_select_value,
            location_display_matches,
            location_option_aliases,
            read_combobox_display,
            read_gh_select_display,
            read_location_autocomplete_value,
        )

        tag = ""
        role = ""
        cls = ""
        try:
            tag = (await loc.evaluate("el => (el.tagName || '').toLowerCase()"))
            role = ((await loc.get_attribute("role")) or "").lower()
            cls = ((await loc.get_attribute("class")) or "").lower()
        except Exception:
            pass
        locish = is_location_field(field_type, label) or (
            bool(intended)
            and not re.search(r"\bbased\s+in\b", (label or ""), re.I)
            and field_type not in ("LOCATION",)
            and re.search(r"\blocation\b|city\s*,\s*country", (label or ""), re.I)
        )
        is_combo = (
            tag == "input"
            and (
                role == "combobox"
                or "select__input" in cls
                or "select__" in cls
            )
        ) or role == "combobox"
        # Places Location combobox — committed City, State, Country line
        if is_combo or locish or field_type in ("ADDRESS_CITY", "LOCATION"):
            rb_loc = ""
            try:
                rb_loc = await read_location_autocomplete_value(loc)
            except Exception:
                rb_loc = ""
            if not rb_loc:
                rb_loc = await read_combobox_display(loc)
            if rb_loc and not is_placeholder_select_value(rb_loc):
                aliases = location_option_aliases(
                    intended or "Springfield",
                    state="IL",
                    state_full="Illinois",
                    country="United States",
                )
                if location_display_matches(
                    rb_loc, aliases, city=intended or "Springfield"
                ):
                    return True, rb_loc
                if _value_matches_readback(str(intended), rb_loc):
                    return True, rb_loc
            if field_type in _COMMIT_STRICT or locish:
                return False, rb_loc or ""
        # Combobox / react-select input: climb to committed display
        if is_combo:
            try:
                container = loc.locator(
                    "xpath=ancestor::div[contains(@class,'select__container') "
                    "or contains(@class,'select-shell')][1]"
                ).first
                if await container.count():
                    rb = await read_gh_select_display(container)
                    if rb and not is_placeholder_select_value(rb):
                        if _value_matches_readback(str(intended), rb):
                            return True, rb
                    return False, rb or ""
            except Exception:
                pass
            rb = await read_combobox_display(loc)
            if not rb or is_placeholder_select_value(rb):
                return False, rb or ""
            if _value_matches_readback(str(intended), rb):
                return True, rb
            return False, rb
        rb = await _read_locator_value(loc)
    except Exception:
        return False, ""
    # Blank salary / school / degree — never already_correct
    if field_type in _COMMIT_STRICT and (
        not rb or is_empty_ui_value(rb) or not str(rb).strip()
    ):
        return False, rb or ""
    if _value_matches_readback(str(intended), rb):
        return True, rb or ""
    return False, rb or ""


async def _location_committed_on_page(
    page,
    loc,
    intended: str,
    *,
    state: str = "IL",
    state_full: str = "Illinois",
    country: str = "United States",
) -> tuple[bool, str]:
    """Probe Places Location — keep verified row when commit signals hold.

    After Ashby resume parse/reassert the combobox filter input can read empty
    while the committed City/State/Country display still matches (Airwallex thrash).
    """
    try:
        from verified_select import (
            location_display_matches,
            location_option_aliases,
            probe_location_committed,
            read_location_autocomplete_value,
        )

        city = (intended or "Springfield").strip()
        st_full = state_full or ("Illinois" if str(state).upper() == "IL" else state)
        aliases = location_option_aliases(
            city, state=state, state_full=st_full, country=country
        )
        probe = await probe_location_committed(
            page,
            loc,
            aliases,
            city=city,
            state=state,
            state_full=st_full,
            country=country,
        )
        shown = (probe.get("shown") or "").strip()
        if not shown:
            shown = (await read_location_autocomplete_value(loc) or "").strip()
        if probe.get("committed"):
            return True, shown
        if location_display_matches(
            shown,
            aliases,
            city=city,
            state=state,
            state_full=st_full,
            country=country,
        ):
            return True, shown
    except Exception:
        pass
    return False, ""


def _ingest_attempt_pass(
    report: dict,
    *,
    pass_i: int | None = None,
    phase: str = "fill",
) -> None:
    """Snapshot filled successes + leftover failures for this pass."""
    log = _attempt_log_from_report(report)
    if log is None:
        return
    try:
        summary = log.ingest_pass(report, pass_i=pass_i, phase=phase)
        unfillable = summary.get("unfillable") or []
        if unfillable:
            report["unfillable_after_2"] = True
            report["unfillable_count"] = len(unfillable)
            report["fixer_trigger_path"] = str(log.fixer_trigger)
            print(
                f"[attempt_log] UNFILLABLE_AFTER_2 count={len(unfillable)} "
                f"→ {log.unfillable_md} | fixer={log.fixer_trigger}",
                flush=True,
            )
        else:
            report.setdefault("unfillable_after_2", False)
        print(
            f"[attempt_log] ingest phase={phase} pass_i={pass_i} "
            f"ok={summary.get('recorded_ok')} fail={summary.get('recorded_fail')}",
            flush=True,
        )
    except Exception as e:
        report.setdefault("errors", []).append({"field_attempt_ingest": str(e)[:120]})


def _merge_ashby_reassert_rows(
    report: dict, already: set, rows: list[dict] | None
) -> None:
    """Fold post-resume / widget URL reassert rows into filled or leftovers."""
    for f in rows or []:
        if not isinstance(f, dict):
            continue
        ftype = f.get("type")
        _record_fill_attempt(
            report,
            f,
            success=bool(f.get("ok") and f.get("verified")),
            via_override=f.get("via") or "ashby_reassert",
        )
        if f.get("ok") and f.get("verified") and ftype:
            # Replace prior claims for this type (resume parse wipe repair)
            report["filled"] = [
                r
                for r in (report.get("filled") or [])
                if not (isinstance(r, dict) and r.get("type") == ftype)
            ]
            report["filled"].append(f)
            already.add(ftype)
            report["leftovers"] = [
                u
                for u in (report.get("leftovers") or [])
                if not (isinstance(u, dict) and u.get("type") == ftype)
            ]
        elif f.get("flash_candidate") or f.get("ok") is False:
            if f.get("reason") in ("already_matches",):
                continue
            # Optional URL fields absent on this Ashby form are not blanks.
            if f.get("reason") == "url_field_not_found":
                continue
            if ftype and any(
                isinstance(u, dict) and u.get("type") == ftype
                for u in (report.get("leftovers") or [])
            ):
                continue
            report.setdefault("leftovers", []).append(
                {
                    "label": f.get("label") or ftype,
                    "type": ftype,
                    "selector": f.get("selector"),
                    "reason": f.get("reason") or "ashby_url_unverified",
                    "readback": f.get("readback") or "",
                    "verified_value": f.get("verified_value"),
                    "flash_candidate": True,
                    "via": f.get("via") or "ashby_reassert",
                }
            )
            # Drop prior claims — live fill failed / empty readback
            if ftype:
                report["filled"] = [
                    r
                    for r in (report.get("filled") or [])
                    if not (isinstance(r, dict) and r.get("type") == ftype)
                ]
                already.discard(ftype)


async def ensure_ashby_application_url(page) -> dict:
    """Ashby JD pages often stay on Overview after Apply; force /application.

    Broad fix for jobs.ashbyhq.com/{org}/{uuid} → …/application so packs and
    extract see the form instead of Vimeo/embedded-media iframes.
    """
    out: dict = {"navigated": False, "from": "", "to": ""}
    try:
        url = page.url or ""
    except Exception:
        return out
    out["from"] = url[:200]
    low = url.lower()
    if "ashbyhq.com" not in low:
        return out
    if "/application" in low:
        return out
    # Strip query/hash; append /application
    base = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    if not re.search(r"ashbyhq\.com/[^/]+/[0-9a-f-]{16,}", base, re.I):
        return out
    dest = base + "/application"
    try:
        await page.goto(dest, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1500)
        out["navigated"] = True
        out["to"] = (page.url or dest)[:200]
    except Exception as e:
        out["error"] = str(e)[:160]
    return out


async def ensure_workable_apply_url(page) -> dict:
    """Workable JD `…/j/{id}` has no fields; form lives at `…/j/{id}/apply/`.

    midtier_smoke: JD URL coverage 0.0; /apply/ → 1.0 with WORKABLE_PACK.
    """
    out: dict = {"navigated": False, "from": "", "to": ""}
    try:
        url = page.url or ""
    except Exception:
        return out
    out["from"] = url[:200]
    low = url.lower()
    if "workable.com" not in low:
        return out
    if re.search(r"/apply/?(\?|$|#)", low):
        return out
    base = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    # apply.workable.com/{slug}/j/{id} or jobs.workable.com/…
    if not re.search(r"/j/[A-Za-z0-9]+$", base):
        return out
    dest = base + "/apply/"
    try:
        await page.goto(dest, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1200)
        out["navigated"] = True
        out["to"] = (page.url or dest)[:200]
    except Exception as e:
        out["error"] = str(e)[:160]
    return out


async def ensure_bamboohr_job_apply_url(page) -> dict:
    """BambooHR careers listing (`/careers`) has 0 inputs — open a job apply URL.

    Prefer visible /careers/{id} or apply links; never Submit/FINAL.
    """
    out: dict = {"navigated": False, "from": "", "to": "", "via": ""}
    try:
        url = page.url or ""
    except Exception:
        return out
    out["from"] = url[:200]
    low = url.lower()
    if "bamboohr.com" not in low:
        return out
    # Already on a job detail / apply path with an id
    if re.search(r"/careers/\d+", low) or "/careers/job" in low:
        # Some tenants need …/careers/{id}/apply or Application
        if "/apply" in low or "application" in low:
            return out
        # Try appending /application if still no inputs (best-effort)
        try:
            n = int(
                await page.evaluate(
                    """() => Array.from(document.querySelectorAll(
                      'input:not([type=hidden]), textarea, select'
                    )).filter(el => {
                      const s = getComputedStyle(el);
                      if (s.display === 'none' || s.visibility === 'hidden') return false;
                      const r = el.getBoundingClientRect();
                      return r.width > 2 && r.height > 2;
                    }).length"""
                )
            )
        except Exception:
            n = -1
        if n and n > 0:
            return out
    # Listing page: pick first job apply href
    try:
        href = await page.evaluate(
            """() => {
              const as = Array.from(document.querySelectorAll('a[href]'));
              const scored = [];
              for (const a of as) {
                const h = a.href || '';
                if (!/bamboohr\\.com/i.test(h)) continue;
                if (/\\/careers\\/\\d+/i.test(h)) scored.push([2, h]);
                else if (/apply|application/i.test(h) && /careers/i.test(h))
                  scored.push([1, h]);
              }
              scored.sort((x, y) => y[0] - x[0]);
              return scored.length ? scored[0][1] : '';
            }"""
        )
    except Exception as e:
        out["error"] = str(e)[:120]
        href = ""
    if not href:
        return out
    dest = str(href).split("#")[0]
    if dest.rstrip("/") == url.split("#")[0].rstrip("/"):
        return out
    try:
        await page.goto(dest, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1500)
        out["navigated"] = True
        out["to"] = (page.url or dest)[:200]
        out["via"] = "careers_job_href"
    except Exception as e:
        out["error"] = str(e)[:160]
    return out


def resolve_refill_wait_enter(refill_wait_enter: bool | None) -> bool:
    """Refill Enter wait is OFF by default — only ON when explicitly requested.

    Humans must not babysit School/Degree/salary leftovers. Auto-loop refill
    passes (prefill + Flash/inpage) until zero blanks or max passes.
    CAPTCHA still uses its own Enter pause.
    """
    if refill_wait_enter is None:
        return False
    return bool(refill_wait_enter)


async def run_inpage_flash_leftovers(
    page,
    report: dict,
    values: dict,
    *,
    force_demoted: bool = False,
) -> dict:
    """Fill leftover flash_candidates on the LIVE page before hold (no new browser).

    Uses this run's ``values`` (shared policy + unique identity) + grounded
    JD/resume answers for essays. Dummy or real depending on report.dummy.
    Marks flash.invoked only when an LLM/essay path ran — deterministic catalog
    reclaim uses via=deterministic_reclaim* and does NOT set flash_called.
    FILL3-013: ``invoked`` = LLM/Skyvern only; ``inpage_ran`` = this path executed
    (deterministic and/or LLM). Also fills Greenhouse react-select leftovers
    (School, Degree, salary, Yes/No)
    via gh_select — classifiers miss must not leave Select... blanks for a human.
    """
    from flash_leftovers import (
        answer_leftover_field,
        build_run_profile_facts,
        build_leftovers_handoff,
        build_resume_excerpt,
        flash_candidate_count,
        is_deterministic_leftover,
        scrape_job_context,
    )
    from page_progress import is_essay_leftover
    from verified_select import is_select_field, normalize_select_answer, verified_select

    # Scrape JD once for grounded essay / novel answers
    try:
        job_ctx = await scrape_job_context(page)
    except Exception as e:
        job_ctx = {"title": "", "company": "", "description": "", "error": str(e)[:80]}
    report["job_context"] = job_ctx

    resume_pdf = report.get("resume_pdf") or report.get("dummy_resume_pdf")
    is_dummy_run = report.get("dummy", True) is not False
    resume_excerpt = build_resume_excerpt(
        resume_pdf, allow_dummy_fallback=is_dummy_run
    )
    profile_facts = build_run_profile_facts(values)

    payload = build_leftovers_handoff(
        report, grounded=True, job_context=job_ctx, resume_path=resume_pdf, values=values
    )
    enrich_report_gh_id_leftovers(report)
    # Rebuild handoff after id→type enrichment so catalog rows defer from Flash
    payload = build_leftovers_handoff(
        report, grounded=True, job_context=job_ctx, resume_path=resume_pdf, values=values
    )
    # Process deferred deterministic first (reclaim), then true Flash leftovers.
    deferred = list(payload.get("deferred_deterministic") or [])
    leftovers = list(deferred) + list(payload.get("leftovers") or [])
    payload["mode"] = "inpage_leftovers"
    print(
        f"[flash] starting in-page leftovers "
        f"(deferred={len(deferred)} leftovers={len(payload.get('leftovers') or [])})",
        flush=True,
    )
    note_step(
        report,
        action="flash_start",
        reason=f"targets={len(leftovers)} force_demoted={force_demoted}",
        via="inpage_flash",
    )
    payload["invoked"] = False  # FILL3-013: LLM/Skyvern only; see inpage_ran
    payload["inpage_ran"] = True
    payload["flash_engine"] = "inpage"  # vs skyvern when run_flash_leftovers runs
    payload["grounded"] = True
    flash_before = flash_candidate_count(report)
    payload["flash_candidates_before"] = flash_before
    if not leftovers:
        payload["skipped_reason"] = "no_leftovers"
        print("[flash] skipped — no leftovers", flush=True)
        note_step(
            report,
            action="flash_end",
            reason="skipped: no_leftovers",
            via="inpage_flash",
        )
        return payload

    attempted: list[dict] = []
    still: list[dict] = []
    llm_fills = 0
    llm_attempts = 0
    det_fills = 0

    def _record_ok(
        *,
        label: str,
        ftype: str | None,
        val: str,
        sel: str,
        readback: str,
        essayish: bool,
        via: str = "inpage_flash",
        extra: dict | None = None,
    ) -> None:
        nonlocal llm_fills, det_fills
        row_ok = {
            "label": label[:80],
            "type": ftype,
            "ok": True,
            "verified": True,
            "value": str(val)[:120],
            "readback": (readback or "")[:120],
            "via": via,
            "essay": essayish,
        }
        if extra:
            row_ok.update(extra)
        attempted.append(row_ok)
        if str(via).startswith("deterministic_reclaim"):
            det_fills += 1
        else:
            llm_fills += 1
        report.setdefault("filled", []).append(
            {
                "via": via,
                "type": ftype or ("COVER_LETTER" if essayish else None),
                "label": label[:80],
                "selector": sel,
                "ok": True,
                "verified": True,
                "value": str(val)[:200],
                "readback": (readback or "")[:120],
                "essay": essayish,
            }
        )
        try:
            from field_lock import lock_verified_field

            lock_verified_field(
                report,
                {
                    "type": ftype,
                    "label": label,
                    "selector": sel,
                    "ok": True,
                    "verified": True,
                    "readback": (readback or "")[:120],
                    "value": str(val)[:120],
                    "via": via,
                },
                field_type=ftype,
                label=label,
                selector=sel,
                readback=(readback or "")[:120],
                via=via,
            )
        except Exception:
            pass
        if "verified_select" in str(via):
            _record_playbook_success(
                report,
                ftype,
                sel,
                {"label": label, "type": ftype, "platform": report.get("platform")},
            )

    for row in leftovers:
        try:
            await wait_while_paused(page, report)
        except Exception:
            pass
        label = str(row.get("label") or "")
        try:
            note_fill_activity(
                layer="flash",
                action="flash leftover",
                label=label[:80],
                detail=str(row.get("reason") or row.get("type") or "")[:60],
            )
            await push_fill_activity(page)
        except Exception:
            pass
        # FILL3-020: skip rows already marked soft-match / recent-flash skip
        if row.get("flash_skip_reason"):
            still.append(row)
            attempted.append(
                {
                    **row,
                    "skipped": True,
                    "reason": str(row.get("flash_skip_reason")),
                }
            )
            continue
        reason = str(row.get("reason") or "")
        mode = str(row.get("mode") or "")
        if reason == "generic_dom_no_fields":
            still.append(row)
            continue
        if reason in ("resume_missing", "resume_upload_failed"):
            # Resume is handled by ensure_resume_uploaded — keep as leftover if still bad
            still.append(row)
            continue
        fake = {
            "label": label,
            "name": "",
            "id": "",
            "type": row.get("html_type") or row.get("type") or "text",
            "placeholder": "",
            "aria_label": "",
            "autocomplete": "",
            "selector": row.get("selector") or "",
        }
        ftype, _layer = classify_field(fake)
        # Prefer leftover's classified type when extract already knew it
        row_type = str(row.get("type") or "").upper()
        if row_type and row_type not in ("TEXT", "TEXTAREA", "SELECT", "SELECT-ONE", "COMBOBOX"):
            ftype = row_type
        sel = row.get("selector") or ""
        # Sandoz / Owens: prior-employer radios must classify + respect singleton lock.
        try:
            from field_map import is_worked_here_label

            if is_worked_here_label(
                label,
                name=str(row.get("name") or fake.get("name") or ""),
                automation_id=str(row.get("automation_id") or ""),
            ) or "candidateispreviousworker" in sel.lower():
                ftype = WORKED_HERE_BEFORE
        except Exception:
            pass
        try:
            from leftover_miss_scan import _verified_worked_here

            if ftype == WORKED_HERE_BEFORE and _verified_worked_here(report):
                attempted.append(
                    {
                        **row,
                        "ok": True,
                        "verified": True,
                        "skipped": True,
                        "skipped_already_correct": True,
                        "reason": "worked_here_already_verified",
                        "readback": "No",
                    }
                )
                try:
                    note_step(
                        report,
                        action="skip_already_correct",
                        field_type=WORKED_HERE_BEFORE,
                        label=label[:80],
                        reason="worked_here_already_verified",
                        via="inpage_flash",
                    )
                except Exception:
                    pass
                continue
        except Exception:
            pass
        # Workday prior-worker: use pack radio helper (never flash-native on value=true).
        if ftype == WORKED_HERE_BEFORE and str(report.get("platform") or "").lower() == "workday":
            try:
                from exp_workday_selectors import _fill_radio_yes_no

                wh_val = str(values.get(WORKED_HERE_BEFORE) or "No")
                rr = await _fill_radio_yes_no(page, "worked_here_before", wh_val)
                if rr.get("verified"):
                    _record_ok(
                        label=label or "worked_here_before",
                        ftype=WORKED_HERE_BEFORE,
                        val=wh_val,
                        sel=str(rr.get("selector") or sel or "worked_here_before"),
                        readback=str(rr.get("readback") or wh_val),
                        essayish=False,
                        via="inpage_flash_workday_radio",
                        extra={"mode": rr.get("mode")},
                    )
                    continue
            except Exception:
                pass
        # HOW_HEARD / source--source: singleton lock + chip probe — never Flash revisit.
        try:
            from exp_workday_selectors import _probe_how_heard_already_committed
            from fill_verify import how_heard_candidates

            is_hh = (
                ftype == HOW_HEARD
                or str(row.get("automation_id") or "").lower()
                in ("how_heard", "source--source", "source")
                or "source--source" in sel.lower()
                or re.search(r"how\s+did\s+you\s+hear|where\s+did\s+you\s+hear", label, re.I)
            )
            if is_hh:
                ftype = HOW_HEARD
                hh_cands = how_heard_candidates(values)
                keep_hh = await _probe_how_heard_already_committed(page, hh_cands)
                if keep_hh is not None:
                    attempted.append(
                        {
                            **row,
                            "ok": True,
                            "verified": True,
                            "skipped": True,
                            "skipped_already_correct": True,
                            "reason": "how_heard_already_committed",
                            "readback": keep_hh.get("readback"),
                        }
                    )
                    continue
        except Exception:
            pass
        # Field lock: never reopen a commit-verified select (Capco referral=No thrash)
        try:
            from field_lock import gate_field_action, get_field_locks, resolve_lock_report

            g = gate_field_action(
                report, field_type=ftype, label=label, selector=sel
            )
            if g and g.get("action") == "lock_skip":
                attempted.append(
                    {
                        **row,
                        "ok": True,
                        "verified": True,
                        "skipped": True,
                        "skipped_locked": True,
                        "skipped_already_correct": True,
                        "reason": "field_locked_skip",
                        "readback": g.get("readback"),
                    }
                )
                try:
                    note_step(
                        report,
                        action="lock_skip",
                        field_type=ftype or "",
                        label=label[:80],
                        after=str(g.get("readback") or "")[:120],
                        via="inpage_flash",
                        reason="field_locked_skip",
                        extra={"thrash_retouch": True},
                    )
                except Exception:
                    pass
                continue
            # Soft identity: same policy type already locked (referral=No revisit)
            sess = get_field_locks(resolve_lock_report(report))
            if (
                sess is not None
                and ftype
                and ftype
                in (
                    "EMPLOYEE_REFERRAL",
                    "WORKED_HERE_BEFORE",
                    "ACCOMMODATIONS",
                    "RACE",
                )
                and ftype in sess.locked_types()
            ):
                sess.note_retouch(
                    sess.identity_key(field_type=ftype, label=label, selector=sel)
                )
                attempted.append(
                    {
                        **row,
                        "ok": True,
                        "verified": True,
                        "skipped": True,
                        "skipped_locked": True,
                        "reason": "field_locked_skip",
                    }
                )
                continue
        except Exception:
            pass
        # Optional short blanks (Phone Extension / middle name): never essay reclaim.
        if (
            ftype in OPTIONAL_LEAVE_BLANK_TYPES
            or is_phone_extension_field(label, ftype)
            or is_short_numeric_field(label, ftype)
        ):
            # Clear contamination if essay/long text already landed here.
            try:
                if sel:
                    loc_clear = page.locator(sel).first
                    if await loc_clear.count() > 0:
                        existing = (await _read_locator_value(loc_clear) or "").strip()
                        if existing and not value_ok_for_field_shape(
                            existing, label=label, ftype=ftype or PHONE_EXTENSION
                        ):
                            await loc_clear.fill("")
                            note_step(
                                report,
                                action="clear_wrong_type",
                                label=label[:80],
                                selector=sel,
                                reason="phone_ext_or_short_numeric_contamination",
                                via="inpage_flash_guard",
                                before=existing[:80],
                            )
            except Exception:
                pass
            # Leave blank — do not synthesize INTEREST into extension.
            continue
        val = values.get(ftype) if ftype else None
        # Cap (_field_capped_unfillable) is for Layer 0/1 thrash only.
        # Flash is the recovery path — skipping here caused flash_zero_fill /
        # invoked=false with leftovers still blank (live grvty 20260802:
        # Discipline + Employment Eligibility hit fail#2 before LLM ran).
        # force_demoted still promotes SPA-wiped verified rows.
        force_row = force_demoted and reason == "live_empty_after_claimed_verified"
        _ = force_row  # reserved for future selective retries
        essayish = bool(row.get("essay")) or is_essay_leftover(row) or ftype in (
            COVER_LETTER,
            INTEREST,
        )
        selectish = is_select_field(
            ftype or str(row.get("type") or ""),
            label,
            {**row, "type": ftype or row.get("type"), "html_type": row.get("html_type")},
        )
        if selectish:
            essayish = False  # never paste essays into select filters
        used_llm = False
        det_row = is_deterministic_leftover(
            {**row, "type": ftype or row.get("type")}, values=values
        )

        # Prefer mapped dummy values; otherwise ground an answer (incl. essays).
        if ftype == INTEREST and val:
            pass
        elif ftype in (SALARY_EXPECTED, SALARY_CURRENT) and val:
            pass
        elif ftype and val and ftype not in (COVER_LETTER,) and not essayish:
            pass
        elif essayish or not val:
            # Catalog reclaim: synthesize from DUMMY_PROFILE — never burn LLM on
            # email/zip/school and never leave as blank when a dummy value exists.
            if det_row and not essayish:
                val = answer_leftover_field(
                    label,
                    ftype=ftype or row.get("type"),
                    job_context=job_ctx,
                    resume_excerpt=resume_excerpt,
                    profile_facts=profile_facts,
                    use_llm=False,
                    values=values,
                )
                if not val:
                    still.append({**row, "reason": "deterministic_missing_value"})
                    continue
            else:
                # Grounded generation — essays / novel leftovers (shared+unique)
                val = answer_leftover_field(
                    label,
                    ftype=ftype or row.get("type"),
                    job_context=job_ctx,
                    resume_excerpt=resume_excerpt,
                    profile_facts=profile_facts,
                    use_llm=True,
                    values=values,
                )
                used_llm = True
                llm_attempts += 1
                if not val:
                    still.append({**row, "reason": "grounded_answer_empty"})
                    continue
        else:
            still.append(row)
            continue

        # Reject essay / wrong-shape values for short numeric / extension fields
        if not value_ok_for_field_shape(str(val), label=label, ftype=ftype):
            still.append({**row, "reason": "wrong_type_value_rejected"})
            continue

        via_base = (
            "deterministic_reclaim"
            if det_row and not used_llm and not essayish
            else "inpage_flash"
        )

        # Ashby/Lever choice leftovers often have selector=None — click by label
        # before giving up (Plaid hybrid Yes/No + rating radios silent miss).
        _choice_reasons = (
            "unclassified_yesno_needs_flash",
            "unclassified_unanswered_choice",
            "unanswered_choice_group",
            "unanswered_radio_group",
            "l01_miss_scan:unanswered_ashby_yesno",
            "l01_miss_scan:unanswered_radio_group",
            "l01_miss_scan:unanswered_ashby_consent",
            "live_required_empty:empty_required_radio_group",
            "empty_required_radio_group",
            "unclassified",
        )
        _choice_modes = ("yesno", "yesno_segmented", "radio", "checkbox")
        _reason_l = reason.lower()
        _is_choice = (
            reason in _choice_reasons
            or mode in _choice_modes
            or "radio" in _reason_l
            or "yesno" in _reason_l
            or "consent" in _reason_l
            or "unanswered_choice" in _reason_l
            or bool(
                re.search(
                    r"\byes\b.+\bno\b|hybrid|rate|impression|preferred|"
                    r"which of the following|why are you interested|"
                    r"which best|which statement|proficiency|"
                    r"production environment|consent\s*\*?",
                    label,
                    re.I,
                )
            )
        )
        if (not sel or _is_choice) and not essayish and _is_choice:
            try:
                from ashby_widgets import ashby_screening_dummy_answer

                choice_val = str(val or "") or ashby_screening_dummy_answer(label, values)
                if re.search(r"hybrid|able to meet this requirement", label, re.I):
                    choice_val = "Yes"
                elif re.search(r"rate|impression|scale", label, re.I):
                    choice_val = choice_val if re.match(r"^[1-4]|n/?a$", choice_val, re.I) else "N/A"
                elif ftype == TERMS_CONSENT or re.match(r"consent\s*\*?$", label.strip(), re.I):
                    choice_val = str(values.get(TERMS_CONSENT) or "Yes")
                ch = await click_ashby_choice_option(
                    page, label, choice_val, report=report
                )
                if ch.get("ok"):
                    _record_ok(
                        label=label,
                        ftype=ftype,
                        val=str(ch.get("picked") or choice_val)[:80],
                        sel=sel or "ashby_choice",
                        readback=str(ch.get("picked") or choice_val)[:80],
                        essayish=False,
                        via=f"{via_base}_ashby_choice",
                        extra={"mode": ch.get("mode")},
                    )
                    continue
            except Exception:
                pass

        # Places / Ashby Location leftovers — never type-and-hope into filter
        if (
            not selectish
            and (
                ftype in (ADDRESS_CITY, LOCATION)
                or re.search(r"^location\b|city\s*,\s*country", label, re.I)
            )
            and not re.search(r"based\s+in\s+any\s+of\s+these\s+states", label, re.I)
        ):
            try:
                loc = None
                if sel:
                    loc = page.locator(sel).first
                if loc is None or await loc.count() == 0:
                    loc = page.get_by_label(re.compile(r"location", re.I)).first
                if loc is not None and await loc.count():
                    detail = await fill_custom_widget(
                        page,
                        loc,
                        str(values.get(ADDRESS_CITY) or val or "Springfield"),
                        field_type=ADDRESS_CITY,
                        label=label or "Location",
                    )
                    if detail.get("ok") and (
                        detail.get("option_clicked")
                        or detail.get("committed")
                        or detail.get("skipped_already_correct")
                    ):
                        _record_ok(
                            label=label,
                            ftype=ADDRESS_CITY,
                            val=str(values.get(ADDRESS_CITY) or val),
                            sel=sel or "location_autocomplete",
                            readback=str(
                                detail.get("readback") or detail.get("picked") or ""
                            ),
                            essayish=False,
                            via=f"{via_base}_location_autocomplete",
                            extra={
                                "picked": detail.get("picked"),
                                "option_clicked": detail.get("option_clicked"),
                                "committed": detail.get("committed"),
                            },
                        )
                        continue
            except Exception:
                pass

        # ALL select answers → verified_select (click option, never essay loc.fill)
        if selectish:
            select_val = normalize_select_answer(
                label, str(val), field_type=ftype or ""
            )
            if ftype and values.get(ftype) and ftype in (
                LOCATION,
                US_RESIDENCE,
                WORK_AUTH,
                SPONSORSHIP,
                RELOCATION,
            ):
                mapped = normalize_select_answer(
                    label, str(values.get(ftype)), field_type=ftype
                )
                if re.search(r"based\s+in\s+any", label, re.I):
                    select_val = "Yes"
                elif mapped in ("Yes", "No") or ftype == SPONSORSHIP:
                    select_val = mapped if mapped else select_val
            select_aliases = aliases_for(ftype or "", select_val)
            if select_val and select_val not in select_aliases:
                select_aliases = [select_val, *select_aliases]
            try:
                result = await verified_select(
                    page,
                    label=label,
                    value=select_val,
                    field_type=ftype or "",
                    selector=sel,
                    aliases=select_aliases,
                )
                if result.get("ok") and result.get("verified", result.get("ok")):
                    shown = str(
                        result.get("shown")
                        or result.get("readback")
                        or result.get("picked")
                        or ""
                    )
                    _record_ok(
                        label=label,
                        ftype=ftype,
                        val=select_val,
                        sel=sel or "verified_select",
                        readback=shown,
                        essayish=False,
                        via=f"{via_base}_{result.get('via') or 'verified_select'}",
                        extra={
                            "picked": result.get("picked"),
                            "skipped_already_correct": result.get(
                                "skipped_already_correct"
                            ),
                        },
                    )
                    continue
                still.append(
                    {
                        **row,
                        "select": True,
                        "reason": result.get("error") or "verified_select_unverified",
                        "flash_candidate": True,
                    }
                )
                continue
            except Exception as e:
                still.append(
                    {
                        **row,
                        "select": True,
                        "reason": f"verified_select_error:{str(e)[:80]}",
                        "flash_candidate": True,
                    }
                )
                continue

        if not sel:
            still.append({**row, "reason": "selector_missing"})
            continue
        try:
            loc = page.locator(_playwright_sel(sel)).first
            if await loc.count() == 0:
                still.append({**row, "reason": "selector_missing"})
                continue
            tag = await loc.evaluate("el => (el.tagName || '').toLowerCase()")
            itype = await loc.evaluate("el => (el.type || '').toLowerCase()")
            role = ((await loc.get_attribute("role")) or "").lower()
            cls = ((await loc.get_attribute("class")) or "").lower()
            # Never fill() a react-select filter and trust input_value
            is_combo_filter = (
                role == "combobox"
                or "select__input" in cls
                or "select__" in cls
                or await loc.evaluate(
                    """el => !!(el.closest('.select__container,.select-shell')
                      || el.getAttribute('role')==='combobox')"""
                )
            )
            if is_combo_filter or tag == "select":
                select_val = normalize_select_answer(
                    label, str(val), field_type=ftype or ""
                )
                try:
                    result = await verified_select(
                        page,
                        label=label,
                        value=select_val,
                        field_type=ftype or "",
                        selector=sel,
                        aliases=aliases_for(ftype or "", select_val),
                    )
                    if result.get("ok") and result.get("verified", result.get("ok")):
                        _record_ok(
                            label=label,
                            ftype=ftype,
                            val=select_val,
                            sel=sel,
                            readback=str(
                                result.get("readback")
                                or result.get("shown")
                                or result.get("picked")
                                or ""
                            ),
                            essayish=False,
                            via=f"{via_base}_{result.get('via') or 'verified_select'}",
                            extra={"picked": result.get("picked")},
                        )
                        continue
                except Exception:
                    pass
                still.append(
                    {
                        **row,
                        "select": True,
                        "reason": "combo_requires_verified_select",
                        "flash_candidate": True,
                    }
                )
                continue
            if tag in ("textarea", "input") and itype not in (
                "checkbox",
                "radio",
                "file",
            ):
                # SKIP thrash on Flash/inpage leftover refill
                try:
                    skip_ok, skip_rb = await _locator_already_correct(
                        loc, str(val), field_type=str(ftype or ""), label=label
                    )
                    if skip_ok:
                        _record_ok(
                            label=label,
                            ftype=ftype,
                            val=str(val),
                            sel=sel,
                            readback=skip_rb or "",
                            essayish=essayish,
                            via=f"{via_base}_already_correct_skip",
                            extra={
                                "reason": "already_correct_skip",
                                "skipped_already_correct": True,
                            },
                        )
                        continue
                except Exception:
                    pass
                await loc.fill(str(val)[:2000])
                readback = await _read_locator_value(loc)
                ok = bool(readback) and _value_matches_readback(str(val), readback)
                if ok:
                    _record_ok(
                        label=label,
                        ftype=ftype,
                        val=str(val),
                        sel=sel,
                        readback=readback or "",
                        essayish=essayish,
                        via=via_base,
                    )
                else:
                    still.append(row)
            elif itype in ("checkbox", "radio"):
                # radio/checkbox — click matching option; verify commit (aria-checked OK)
                try:
                    want = str(val).strip().lower()
                    bool_val = (
                        "false"
                        if want in ("no", "false", "n", "0")
                        else "true"
                    )
                    clicked = False
                    if "candidateispreviousworker" in sel.lower() or ftype == WORKED_HERE_BEFORE:
                        target = page.locator(
                            f'input[name="candidateIsPreviousWorker"][value="{bool_val}"]'
                        ).first
                        if await target.count() > 0:
                            try:
                                await target.scroll_into_view_if_needed()
                            except Exception:
                                pass
                            try:
                                await target.check(timeout=2500, force=True)
                            except Exception:
                                await target.click(timeout=2500, force=True)
                            clicked = True
                            sel = f'input[name="candidateIsPreviousWorker"][value="{bool_val}"]'
                    if not clicked:
                        parent = loc.locator(
                            "xpath=ancestor::fieldset[1]|ancestor::div[1]"
                        )
                        opt = parent.get_by_label(
                            re.compile(re.escape(str(val)[:40]), re.I)
                        )
                        if await opt.count() == 0:
                            opt = page.get_by_label(
                                re.compile(re.escape(str(val)[:40]), re.I)
                            )
                        await opt.first.click(timeout=2500)
                        clicked = True
                    verified = await page.evaluate(
                        """(args) => {
                          const wantNo = args.wantNo;
                          const name = args.name;
                          if (name) {
                            const radios = [...document.querySelectorAll(
                              'input[type="radio"][name="' + name + '"]')];
                            const picked = radios.find(r =>
                              r.checked || r.getAttribute('aria-checked') === 'true');
                            if (!picked) return false;
                            const pv = (picked.value || '').toLowerCase();
                            if (wantNo) return pv === 'false' || pv === 'no' || pv === '0';
                            return pv === 'true' || pv === 'yes' || pv === '1';
                          }
                          return !!document.querySelector(
                            'input[type="radio"]:checked, input[type="radio"][aria-checked="true"], '
                            + '[role="radio"][aria-checked="true"]');
                        }""",
                        {
                            "wantNo": want in ("no", "false", "n", "0"),
                            "name": "candidateIsPreviousWorker"
                            if (
                                "candidateispreviousworker" in sel.lower()
                                or ftype == WORKED_HERE_BEFORE
                            )
                            else "",
                        },
                    )
                    if verified:
                        _record_ok(
                            label=label,
                            ftype=ftype,
                            val=str(val),
                            sel=sel,
                            readback=str(val),
                            essayish=False,
                            via=f"{via_base}_native",
                        )
                    else:
                        still.append({**row, "reason": "radio_unverified_after_click"})
                except Exception as e:
                    still.append({**row, "error": str(e)[:100]})
            else:
                still.append(row)
        except Exception as e:
            still.append({**row, "error": str(e)[:100]})

    # Flash "invoked" when an LLM answer was requested for any leftover —
    # not only on successful fill (else zero-fill looked like Flash never ran).
    payload["invoked"] = llm_fills > 0 or llm_attempts > 0
    payload["deterministic_reclaims"] = det_fills
    payload["llm_fills"] = llm_fills
    payload["llm_attempts"] = llm_attempts
    payload["inpage_attempted"] = attempted
    payload["leftovers"] = still
    payload["leftover_count"] = len(still)
    # Refresh report leftovers to drop successfully filled ones. Match by label
    # AND by selector: essays/URL fields filled via replay/inpage often carry an
    # empty label but a concrete selector (Ashby "favorite AI paper" essay filled
    # via replay had label="" → the by-label match alone left a filled field
    # falsely listed as an unclassified leftover → false FAIL).
    filled_labels = {
        (a.get("label") or "").lower()
        for a in attempted
        if a.get("ok") and a.get("label")
    }
    filled_selectors = {
        (a.get("selector") or "").lower()
        for a in attempted
        if a.get("ok") and a.get("selector")
    }
    report["leftovers"] = [
        u
        for u in (report.get("leftovers") or [])
        if (u.get("label") or "").lower()[:80] not in filled_labels
        and (
            not (u.get("selector") or "")
            or (u.get("selector") or "").lower() not in filled_selectors
        )
    ]
    # Merge any still-unfilled rows that might not already be listed
    existing = {(u.get("label") or "")[:80].lower() for u in report["leftovers"]}
    for s in still:
        key = (s.get("label") or "")[:80].lower()
        if key and key not in existing:
            report["leftovers"].append(s)
            existing.add(key)
    # Dedupe leftovers
    seen = set()
    deduped = []
    for u in report["leftovers"]:
        key = (u.get("label") or u.get("reason") or "")[:80]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(u)
    report["leftovers"] = deduped
    report["leftover_count"] = len(deduped)
    print(
        f"[flash] in-page leftovers done "
        f"invoked={payload.get('invoked')} "
        f"llm_fills={payload.get('llm_fills')} "
        f"left={payload.get('leftover_count')}",
        flush=True,
    )
    note_step(
        report,
        action="flash_end",
        reason=(
            f"invoked={payload.get('invoked')} "
            f"filled={payload.get('llm_fills')} "
            f"left={payload.get('leftover_count')}"
        ),
        via="inpage_flash",
    )
    return payload

_GH_SELECT_FIELD_TYPES = frozenset({
    "ADDRESS_COUNTRY",
    "ADDRESS_CITY",
    "WORK_AUTH",
    "US_RESIDENCE",
    "US_CITIZEN",
    "SPONSORSHIP",
    "HOW_HEARD",
    "GENDER",
    "HISPANIC",
    "RACE",
    "VETERAN",
    "DISABILITY",
    # GH Tax Relief Advocates blanks (education + Yes/No + salary selects)
    "SCHOOL",
    "DEGREE",
    "DISCIPLINE",
    "MAJOR",
    "FIELD_OF_STUDY",
    "EDUCATION_START_YEAR",
    "EDUCATION_END_YEAR",
    "SALARY_EXPECTED",
    "SALARY_CURRENT",
    "CLEARANCE",
    "CLEARANCE_TYPE",
    "VISA_STATUS",
    "WORKED_HERE_BEFORE",
    "RELOCATION",
    "COMMUTE",
    "NOTICE_PERIOD",
    "AGE_18",
    "FELONY",
    "BACKGROUND_CHECK",
    "MARKETING_CONSENT",
    "TERMS_CONSENT",
    "ACCOMMODATIONS",
    "ACCOMMODATIONS_DETAILS",
    "EMPLOYEE_REFERRAL",
    "TALENT_HUB",
    "SERVICE_MEMBER",
    "AGE_RANGE",
    "LOCATION",
})
_GH_SELECT_TYPES = frozenset({"search-dropdown", "combobox", "select-one"})
# Often free-text OR react-select — only use gh_select when DOM has control.
_GH_SELECT_OPTIONAL_DOM_TYPES = frozenset(
    {
        "SALARY_EXPECTED",
        "SALARY_CURRENT",
        "CLEARANCE",
        "CLEARANCE_TYPE",
        "VISA_STATUS",
        "US_CITIZEN",
    }
)


def _gh_city_aliases(values: dict, val: str) -> tuple[str, list[str]]:
    """Prefer Springfield, Illinois, United States for GH Location (City) selects.

    Bare ``aliases_for(ADDRESS_CITY, "Springfield")`` lets Places pick Missouri
    first (live Dragos). Always include IL state aliases.
    """
    from verified_select import location_option_aliases

    city = (
        str(val or "").strip()
        or str(values.get(ADDRESS_CITY) or values.get(LOCATION) or "").strip()
        or "Springfield"
    )
    # If already a City, State blob, keep head as city
    city_head = city.split(",")[0].strip() or "Springfield"
    state = str(values.get(ADDRESS_STATE) or "IL").strip() or "IL"
    country = str(values.get(ADDRESS_COUNTRY) or "United States").strip() or "United States"
    aliases = location_option_aliases(
        city_head, state=state, state_full="Illinois" if state.upper() == "IL" else state,
        country=country,
    )
    primary = aliases[0] if aliases else f"{city_head}, Illinois, United States"
    return primary, aliases


# Live-required-empty often surfaces element id/name only (not the visible label).
_GH_ID_LEFTOVER_MAP: dict[str, tuple[str, str]] = {
    "candidate-location": (LOCATION, "Location (City)"),
    "candidate_location": (LOCATION, "Location (City)"),
    "country": (ADDRESS_COUNTRY, "Country"),
}


def enrich_gh_id_leftover(row: dict) -> dict:
    """Map GH id-only leftovers to catalog types for deterministic reclaim.

    Dragos smoke: live_required_empty labels were ``candidate-location``,
    ``country``, ``question_*`` — untyped → Flash wasted / reclaim skipped.
    """
    if not isinstance(row, dict):
        return row
    out = dict(row)
    lab = str(out.get("label") or "").strip()
    lab_l = lab.lower()
    ftype = str(out.get("type") or "").strip().upper()
    if ftype and ftype not in ("TEXT", "TEXTAREA", "SELECT", "SELECT-ONE", "COMBOBOX"):
        return out
    mapped = _GH_ID_LEFTOVER_MAP.get(lab_l)
    if mapped:
        out["type"] = mapped[0]
        out["label"] = mapped[1]
        out["id_hint"] = lab
        out["flash_candidate"] = False
        out["flash_skip_reason"] = "deterministic_catalog"
        out["ownership"] = "prefill_reclaim"
        out["select"] = True
        return out
    # question_<digits> custom GH selects — keep id as selector hint
    if re.match(r"^question_\d+$", lab_l):
        out["selector"] = out.get("selector") or f"#{lab}:visible"
        out["select"] = True
        # Leave type for classify via nearby label during reclaim fill
    return out


def enrich_report_gh_id_leftovers(report: dict) -> int:
    """In-place enrich leftovers; return count of newly typed rows."""
    n = 0
    kept: list[dict] = []
    for u in report.get("leftovers") or []:
        if not isinstance(u, dict):
            continue
        before_t = str(u.get("type") or "")
        enriched = enrich_gh_id_leftover(u)
        if str(enriched.get("type") or "") and not before_t:
            n += 1
        kept.append(enriched)
    report["leftovers"] = kept
    return n


async def _should_use_gh_select(
    page, field: dict, ftype: str | None, platform: str
) -> bool:
    """True when the field is a Greenhouse react-select (label.select__label + control).

    Ashby/Lever comboboxes are handled by fill_custom_widget / ashby_widgets —
    never route Airwallex Location through gh_select.
    Salary / clearance can be free-text — require visible select__control.
    """
    label = (field.get("label") or "").strip()

    async def _label_has_select_control() -> bool:
        if not label:
            return False
        try:
            needle = label.replace("*", "").strip()[:40]
            if not needle:
                return False
            n = await page.locator("label.select__label").filter(
                has_text=re.compile(re.escape(needle[:32]), re.I)
            ).count()
            return n > 0
        except Exception:
            return False

    if platform in ("ashby", "lever"):
        # Lever card react-selects (Lindblad how-heard) use GH-style controls.
        if platform == "lever" and ftype == HOW_HEARD and label:
            return await _label_has_select_control()
        return False
    if platform not in ("greenhouse", "unknown", ""):
        # Other ATS: only when GH react-select DOM is present for this label
        return await _label_has_select_control()
    if ftype and ftype in _GH_SELECT_OPTIONAL_DOM_TYPES:
        return await _label_has_select_control()
    if ftype and ftype in _GH_SELECT_FIELD_TYPES:
        # Prefer DOM when label available; otherwise assume select for known types
        if label:
            has = await _label_has_select_control()
            if has:
                return True
            # Known select types without matching label.select__label — still try
            # (some GH boards omit select__label class). Optional types already
            # returned above.
            return True
        return True
    ftype_html = (field.get("type") or field.get("input_type") or "").lower()
    if ftype_html in _GH_SELECT_TYPES:
        return True
    if label and await _label_has_select_control():
        return True
    return False


# State abbrev↔full lives in verified_select.expand_state_value / _US_STATE_NAMES

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

EXTRACT_JS_CANDIDATES = [
    ROOT
    / "skyvern_runtime"
    / "venv"
    / "lib"
    / "python3.12"
    / "site-packages"
    / "skyvern"
    / "core"
    / "script_generations"
    / "extract_form_fields.js",
    ROOT
    / "skyvern_runtime"
    / "venv"
    / "lib"
    / "python3.11"
    / "site-packages"
    / "skyvern"
    / "core"
    / "script_generations"
    / "extract_form_fields.js",
]

OUT_DEFAULT = ROOT / "skyvern_runtime" / "real_job_results" / "fast_fill.json"

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------
# Host patterns from listings/* + ats_notes + dashboard ATS_URL_PATTERNS.
# Order matters: more-specific hosts before broad tokens.
# "unknown" is NOT a dead end — it uses GENERIC_SELECTOR_PACK + extract/classify.

PLATFORM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("greenhouse", re.compile(
        r"(?:boards|job-boards)(?:\.[a-z]+)?\.greenhouse\.io|greenhouse\.io/"
        r"|grnh\.se/",
        re.I,
    )),
    ("workday", re.compile(r"myworkdayjobs\.com|myworkdaysite\.com", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co|lever\.co/", re.I)),
    ("ashby", re.compile(r"(?:jobs|app)\.ashbyhq\.com", re.I)),
    ("icims", re.compile(r"icims\.com", re.I)),
    ("smartrecruiters", re.compile(
        r"(?:jobs\.)?smartrecruiters\.com|smartrecruiters\.com/",
        re.I,
    )),
    ("workable", re.compile(
        r"(?:apply|jobs)\.workable\.com|workable\.com/(?:j|jobs|apply)/",
        re.I,
    )),
    ("bamboohr", re.compile(r"(?:[\w-]+\.)?bamboohr\.com", re.I)),
    ("recruitee", re.compile(r"recruitee\.com", re.I)),
    # company.jobs.personio.de / .com (not bare personio.com marketing)
    ("personio", re.compile(r"(?:[\w-]+\.)?jobs\.personio\.(?:com|de)", re.I)),
    ("jobvite", re.compile(
        r"(?:app|jobs|careers)\.jobvite\.com|jobvite\.com/(?:company|hire|jobs)",
        re.I,
    )),
    ("taleo", re.compile(
        r"(?:[\w-]+\.)?taleo\.(?:net|com)|tbe\.taleo\.net",
        re.I,
    )),
    ("successfactors", re.compile(
        r"successfactors\.(?:com|eu)|sapsf\.(?:com|eu)|"
        r"career[s]?\d*\.sapsf|hcm\d*\.sapsf|"
        r"sap\.com/.*successfactors",
        re.I,
    )),
    ("dayforce", re.compile(
        r"(?:jobs\.)?dayforcehcm\.com|dayforce\.com|"
        r"csg\.ceridian\.com",
        re.I,
    )),
    ("ukg", re.compile(
        r"recruiting\d*\.ultipro\.com|ultipro\.com|"
        r"ukg\.com/|ukgready\.com",
        re.I,
    )),
    ("oracle", re.compile(r"oraclecloud\.com", re.I)),
    ("rippling", re.compile(r"(?:ats|app)\.rippling\.com|rippling\.com/(?:ats|jobs)/", re.I)),
    ("applytojob", re.compile(r"applytojob\.com", re.I)),
    ("breezy", re.compile(r"breezy\.hr", re.I)),
    ("jobscore", re.compile(r"jobscore\.com", re.I)),
    ("gem", re.compile(r"jobs\.gem\.com", re.I)),
    ("dover", re.compile(r"app\.dover\.com", re.I)),
    # Hosted phenompeople + white-label careers (utm / apply jobSeqNo / EXTERNAL* req ids).
    ("phenom", re.compile(
        r"phenompeople\.com|phenom\.com/|phncdn\.com|phenomprod\.com|"
        r"utm_medium=phenom|phenom-feeds|phenomtrack|"
        r"jobSeqNo=|stepname=personalInformation|"
        r"EXTERNALEN(?:US|GLOBAL|CA|GB|AU|IN|DE|FR|ES|JP)?(?:/|\?|$)",
        re.I,
    )),
]

# Platforms with dedicated multipage / pack logic vs generic DOM path.
PACK_PLATFORMS = frozenset(name for name, _ in PLATFORM_PATTERNS)


def detect_platform(url: str) -> str:
    """Return ATS id from URL host/path, or 'unknown' for company career pages.

    unknown is first-class: entry_prepass → generic pack → extract → classify →
    custom widgets → learned → leftovers (Flash opt-in only).
    """
    raw = url or ""
    for name, pat in PLATFORM_PATTERNS:
        if pat.search(raw):
            return name
    host = urlparse(raw).netloc.lower()
    # Conservative host-token fallback (avoid short tokens like gem/ukg alone)
    host_fallbacks = (
        ("greenhouse", "greenhouse"),
        ("myworkdayjobs", "workday"),
        ("myworkdaysite", "workday"),
        ("lever.co", "lever"),
        ("ashbyhq", "ashby"),
        ("icims", "icims"),
        ("smartrecruiters", "smartrecruiters"),
        ("workable.com", "workable"),
        ("bamboohr.com", "bamboohr"),
        ("recruitee", "recruitee"),
        ("jobs.personio", "personio"),
        ("jobvite.com", "jobvite"),
        ("taleo.net", "taleo"),
        ("taleo.com", "taleo"),
        ("successfactors", "successfactors"),
        ("sapsf.", "successfactors"),
        ("dayforcehcm", "dayforce"),
        ("dayforce.com", "dayforce"),
        ("csg.ceridian", "dayforce"),
        ("ultipro.com", "ukg"),
        ("ukgready", "ukg"),
        ("oraclecloud", "oracle"),
        ("rippling.com", "rippling"),
        ("applytojob", "applytojob"),
        ("breezy.hr", "breezy"),
        ("jobscore", "jobscore"),
        ("jobs.gem.com", "gem"),
        ("app.dover.com", "dover"),
        ("phenompeople", "phenom"),
        ("phenomprod", "phenom"),
        ("phncdn", "phenom"),
    )
    for token, name in host_fallbacks:
        if token in host:
            return name
    return "unknown"


# ---------------------------------------------------------------------------
# Platform selector packs (from ats_notes/* + common host heuristics) — Layer 0.5
# Each entry: (css_or_name_selector, canonical field_map type, mode)
# mode: fill | combobox | file
# Empty / light packs still run: extract+classify is the primary path.
# ---------------------------------------------------------------------------

# Generic contact pack — used for unknown/non-ATS and as baseline for thin ATS.
GENERIC_SELECTOR_PACK: list[tuple[str, str, str]] = [
    ("input[autocomplete='given-name'], input[name*='first' i][name*='name' i], input[id*='first' i][id*='name' i], input[name*='firstName' i], input[id*='firstName' i]", NAME_FIRST, "fill"),
    ("input[autocomplete='family-name'], input[name*='last' i][name*='name' i], input[id*='last' i][id*='name' i], input[name*='lastName' i], input[id*='lastName' i]", NAME_LAST, "fill"),
    ("input[autocomplete='name'], input[name='name'], input[name='full_name'], input[name='fullName'], input[name*='candidateName' i]", NAME_FULL, "fill"),
    ("input[type=email], input[autocomplete='email'], input[name*='email' i], input[id*='email' i]:not([id*='alert' i]):not([id*='notif' i]), input[name='css_loginName']", EMAIL, "fill"),
    (
        "input[type=password][name*='confirm' i], input[type=password][id*='confirm' i], "
        "input[name*='confirmPassword' i]",
        PASSWORD_CONFIRM,
        "fill",
    ),
    (
        "input[type=password], input[name*='password' i], input[autocomplete='current-password'], "
        "input[autocomplete='new-password']",
        PASSWORD,
        "fill",
    ),
    ("input[type=tel], input[autocomplete='tel'], "
     "input[name*='phone' i]:not([name*='device' i]):not([name*='type' i])"
     ":not([name*='ext' i]):not([name*='extension' i]):not([name*='country' i]), "
     "input[id*='phone' i]:not([id*='device' i]):not([id*='type' i])"
     ":not([id*='ext' i]):not([id*='extension' i]):not([id*='country' i])",
     PHONE, "fill"),
    ("input[name*='linkedin' i], input[placeholder*='LinkedIn' i], input[id*='linkedin' i]", LINKEDIN, "fill"),
    ("input[name*='github' i], input[placeholder*='GitHub' i], input[id*='github' i]", GITHUB, "fill"),
    (
        "input[autocomplete='address-line2'], input[name*='address2' i], "
        "input[id*='address2' i], input[name*='apartment' i], "
        "input[id*='apartment' i], input[name*='unit' i], input[id*='unit' i]",
        ADDRESS_LINE2,
        "fill",
    ),
    (
        "input[autocomplete='address-line1'], input[name*='address1' i], "
        "input[id*='address1' i], input[name*='street' i], input[id*='street' i]",
        ADDRESS_LINE1,
        "fill",
    ),
    ("input[autocomplete='address-level2'], input[name*='city' i], input[id*='city' i]", ADDRESS_CITY, "fill"),
    (
        "input[name*='county' i], input[id*='county' i], "
        "input[name*='regionSubdivision' i], input[id*='regionSubdivision' i]",
        ADDRESS_COUNTY,
        "fill",
    ),
    # ATS2-008: state is usually a select/combobox — never bare text fill
    (
        "select[autocomplete='address-level1'], select[name*='state' i], select[id*='state' i], "
        "[role='combobox'][name*='state' i], [aria-label*='state' i][role='combobox'], "
        "input[autocomplete='address-level1'], input[name*='state' i], input[id*='state' i]",
        ADDRESS_STATE,
        "combobox",
    ),
    ("input[autocomplete='postal-code'], input[name*='zip' i], input[id*='zip' i], input[name*='postal' i]", ADDRESS_ZIP, "fill"),
    ("input[type=file]", RESUME_UPLOAD, "file"),
]

GH_SELECTOR_PACK: list[tuple[str, str, str]] = [
    ("#first_name", NAME_FIRST, "fill"),
    ("#last_name", NAME_LAST, "fill"),
    ("#email", EMAIL, "fill"),
    ("#phone", PHONE, "fill"),
    (
        "input[name*='linkedin' i], input[id*='linkedin' i], "
        "input[placeholder*='LinkedIn' i], input[aria-label*='LinkedIn' i], "
        "label:has-text('LinkedIn') >> xpath=following::input[1], "
        ".field:has(label:has-text('LinkedIn')) input[type=text], "
        ".field:has(label:has-text('LinkedIn')) input[type=url]",
        LINKEDIN,
        "fill",
    ),
    # ATS3-014: Country / Location / State react-select when extract misses.
    # Target .select__control (not the hidden filter input) — gh_select pattern.
    (
        ".select__container:has(label.select__label:text-matches('Country', 'i')) "
        ".select__control, "
        ".select__container:has(label:has-text('Country')) .select__control, "
        ".select-shell:has(label:has-text('Country')) .select__control, "
        ".field:has(label:has-text('Country')) .select__control",
        ADDRESS_COUNTRY,
        "combobox",
    ),
    (
        ".select__container:has(label.select__label:text-matches('Location', 'i')) "
        ".select__control, "
        ".select__container:has(label:has-text('Location')) .select__control, "
        ".select__container:has(label:has-text('City')) .select__control, "
        ".field:has(label:has-text('Location')) .select__control, "
        ".field:has(label:has-text('City')) .select__control",
        LOCATION,
        "combobox",
    ),
    (
        ".select__container:has(label.select__label:text-matches('State', 'i')) "
        ".select__control, "
        ".select__container:has(label:has-text('State')) .select__control, "
        ".select__container:has(label:has-text('Province')) .select__control, "
        ".field:has(label:has-text('State')) .select__control",
        ADDRESS_STATE,
        "combobox",
    ),
    # Prefer resume-ish file inputs before the catch-all (GH dropzones).
    (
        "input[type=file][id*='resume' i], input[type=file][name*='resume' i], "
        "input[type=file][accept*='pdf' i], input[type=file]",
        RESUME_UPLOAD,
        "file",
    ),
]

LEVER_SELECTOR_PACK: list[tuple[str, str, str]] = [
    ("[name='name']", NAME_FULL, "fill"),
    ("[name='email']", EMAIL, "fill"),
    ("[name='emails']", EMAIL, "fill"),
    ("[name='phone']", PHONE, "fill"),
    ("[name='org']", CURRENT_COMPANY, "fill"),
    ("[name='urls[LinkedIn]']", LINKEDIN, "fill"),
    ("[name='urls[Github]']", GITHUB, "fill"),
    ("[name='urls[GitHub]']", GITHUB, "fill"),
    ("[name='urls[Twitter]'], [name='urls[Twitter URL]'], [name='urls[X]']", TWITTER, "fill"),
    ("[name='urls[Portfolio]']", PORTFOLIO, "fill"),
    ("[name='urls[Other]']", PORTFOLIO, "fill"),
    ("input[type=file]", RESUME_UPLOAD, "file"),
    # EEO native <select> (Decline values from build_value_map) — radios/cards
    # still handled by lever_widgets when name attrs vary per company.
    (
        "select[name*='gender' i], select[id*='gender' i], "
        "select[name*='eeo'][name*='gender' i], select[name='eeo[gender]']",
        GENDER,
        "fill",
    ),
    (
        "select[name*='race' i], select[id*='race' i], "
        "select[name*='ethnicity' i], select[name='eeo[race]'], "
        "select[name='eeo[race_ethnicity]']",
        RACE,
        "fill",
    ),
    (
        "select[name*='veteran' i], select[id*='veteran' i], "
        "select[name='eeo[veteran]'], select[name='eeo[veteran_status]']",
        VETERAN,
        "fill",
    ),
    (
        "select[name*='hispanic' i], select[name*='latino' i], "
        "select[name='eeo[hispanic]'], select[name='eeo[hispanic_or_latino]']",
        HISPANIC,
        "fill",
    ),
    (
        "select[name*='disabilit' i], select[id*='disabilit' i], "
        "select[name='eeo[disability]'], select[name='eeo[disability_status]']",
        DISABILITY,
        "fill",
    ),
]

# Workday CSS pack: single source in workday_selectors (derived from WD_CONTACT_PACK).
from workday_selectors import WD_SELECTOR_PACK  # noqa: E402

# Ashby: light heuristics + common name attrs; extract+classify remains primary.
ASHBY_SELECTOR_PACK: list[tuple[str, str, str]] = [
    ("input[name='_systemfield_name'], input[name='name'], input[autocomplete='name']", NAME_FULL, "fill"),
    ("input[name='firstName'], input[name='first_name'], input[autocomplete='given-name']", NAME_FIRST, "fill"),
    ("input[name='lastName'], input[name='last_name'], input[autocomplete='family-name']", NAME_LAST, "fill"),
    ("input[type=email], input[name='email'], input[name='_systemfield_email'], input[autocomplete='email']", EMAIL, "fill"),
    ("input[type=tel], input[name='phone'], input[name='_systemfield_phone'], input[autocomplete='tel']", PHONE, "fill"),
    # LinkedIn/GitHub often use opaque UUID name= — prefer label-scoped selectors.
    (
        ".ashby-application-form-field-entry:has(label:has-text('LinkedIn')) "
        "input[type=text], "
        ".ashby-application-form-field-entry:has(label:has-text('LinkedIn')) "
        "input[type=url], "
        ".ashby-application-form-field-entry:has(label:has-text('LinkedIn')) "
        "input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio]), "
        "[class*=\"_fieldEntry_\"]:has(label:has-text('LinkedIn')) input, "
        "input[name*='linkedin' i], input[placeholder*='LinkedIn' i]",
        LINKEDIN,
        "fill",
    ),
    (
        ".ashby-application-form-field-entry:has(label:has-text('GitHub')) "
        "input[type=text], "
        ".ashby-application-form-field-entry:has(label:has-text('GitHub')) "
        "input:not([type=hidden]):not([type=file]), "
        "input[name*='github' i], input[placeholder*='GitHub' i]",
        GITHUB,
        "fill",
    ),
    (
        ".ashby-application-form-field-entry:has(label:has-text('Portfolio')) "
        "input[type=text], "
        ".ashby-application-form-field-entry:has(label:has-text('Website')) "
        "input:not([type=hidden]):not([type=file]), "
        "input[placeholder*='Portfolio' i], input[placeholder*='Website' i]",
        PORTFOLIO,
        "fill",
    ),
    # Ashby Location is a combobox (not separate city/state/zip)
    (
        "label.ashby-application-form-question-title:has-text('Location') "
        "~ div [role=combobox], "
        ".ashby-application-form-field-entry:has(label:has-text('Location')) [role=combobox], "
        "input[placeholder='Start typing...'][role=combobox]",
        ADDRESS_CITY,
        "combobox",
    ),
    ("input[type=file], input[id='_systemfield_resume']", RESUME_UPLOAD, "file"),
]

# iCIMS: iframe-heavy; login gate uses css_loginName + password before profile.
ICIMS_SELECTOR_PACK: list[tuple[str, str, str]] = [
    # Auth gate (login / create) — dummy email from prepare_dummy_run
    (
        "input#email, input[name='css_loginName'], input[name*='loginName' i], "
        "input[name='PersonProfileFields.Login'], input[type=email], "
        "input[name*='email' i], input[autocomplete='email']",
        EMAIL,
        "fill",
    ),
    (
        "input[type=password][name*='confirm' i], input[type=password][id*='confirm' i], "
        "input[name*='confirmPassword' i], input[id*='verifyPassword' i]",
        PASSWORD_CONFIRM,
        "fill",
    ),
    (
        "input[type=password], input[name*='password' i], input[id*='password' i], "
        "input[autocomplete='new-password'], input[autocomplete='current-password']",
        PASSWORD,
        "fill",
    ),
    # Application / profile fields (post-auth)
    (
        "input[name*='firstname' i], input[id*='firstname' i], "
        "input[name='PersonProfileFields.FirstName'], input[autocomplete='given-name']",
        NAME_FIRST,
        "fill",
    ),
    (
        "input[name*='lastname' i], input[id*='lastname' i], "
        "input[name='PersonProfileFields.LastName'], input[autocomplete='family-name']",
        NAME_LAST,
        "fill",
    ),
    ("input[type=tel], input[name*='phone' i], input[autocomplete='tel']", PHONE, "fill"),
    ("input[name*='city' i], input[autocomplete='address-level2']", ADDRESS_CITY, "fill"),
    ("input[name*='zip' i], input[name*='postal' i], input[autocomplete='postal-code']", ADDRESS_ZIP, "fill"),
    ("input[type=file]", RESUME_UPLOAD, "file"),
]

# Mid-tier ATS packs (corpus-backed where available) — extract still fills depth.
# SmartRecruiters oneclick-ui: stable #*-input ids (Veolia/AbbVie corpus).
SMARTRECRUITERS_PACK: list[tuple[str, str, str]] = [
    ("#first-name-input, input[id='first-name-input'], input[autocomplete='given-name']", NAME_FIRST, "fill"),
    ("#last-name-input, input[id='last-name-input'], input[autocomplete='family-name']", NAME_LAST, "fill"),
    ("#email-input, input[id='email-input'], input[autocomplete='email']", EMAIL, "fill"),
    ("#confirm-email-input, input[id='confirm-email-input']", EMAIL, "fill"),
    ("#linkedin-input, input[id='linkedin-input'], input[name*='linkedin' i]", LINKEDIN, "fill"),
    ("#website-input, input[id='website-input']", GITHUB, "fill"),
    ("#file-input, input[id='file-input'], input[type=file]", RESUME_UPLOAD, "file"),
]

# Workable apply.workable.com — #firstname / #lastname / #email / #phone.
WORKABLE_PACK: list[tuple[str, str, str]] = [
    ("#firstname, input[name='firstname'], input[id='firstname']", NAME_FIRST, "fill"),
    ("#lastname, input[name='lastname'], input[id='lastname']", NAME_LAST, "fill"),
    ("#email, input[name='email'], input[id='email'], input[type=email]", EMAIL, "fill"),
    ("#phone, input[name='phone'], input[type=tel]", PHONE, "fill"),
    ("#address, input[name='address'], input[id='address']", ADDRESS_LINE1, "fill"),
    ("input[name*='linkedin' i], input[placeholder*='LinkedIn' i]", LINKEDIN, "fill"),
    ("input[type=file]", RESUME_UPLOAD, "file"),
]

# BambooHR *.bamboohr.com/careers — Greenhouse-like #first_name ids.
BAMBOOHR_PACK: list[tuple[str, str, str]] = [
    ("#first_name, input[name='first_name'], input[id='first_name']", NAME_FIRST, "fill"),
    ("#last_name, input[name='last_name'], input[id='last_name']", NAME_LAST, "fill"),
    ("#email, input[name='email'], input[id='email'], input[type=email]", EMAIL, "fill"),
    ("#phone, input[name='phone'], input[id='phone'], input[type=tel]", PHONE, "fill"),
    ("input[name*='city' i], input[id*='city' i]", ADDRESS_CITY, "fill"),
    ("input[name*='zip' i], input[id*='zip' i], input[name*='postal' i]", ADDRESS_ZIP, "fill"),
    ("input[type=file]", RESUME_UPLOAD, "file"),
]

RECRUITEE_PACK = list(GENERIC_SELECTOR_PACK)

# Personio *.jobs.personio.{com,de} — #field-* + documents.cv (live: Ultralytics apply).
PERSONIO_PACK: list[tuple[str, str, str]] = [
    ("#field-first_name, input[name='first_name'], input[id='field-first_name']", NAME_FIRST, "fill"),
    ("#field-last_name, input[name='last_name'], input[id='field-last_name']", NAME_LAST, "fill"),
    ("#field-email, input[name='email'], input[id='field-email'], input[type=email]", EMAIL, "fill"),
    ("#field-phone, input[name='phone'], input[id='field-phone']", PHONE, "fill"),
    ("input[placeholder*='LinkedIn' i], input[name*='linkedin' i]", LINKEDIN, "fill"),
    ("input[placeholder*='GitHub' i], input[name*='github' i]", GITHUB, "fill"),
    ("#doc-input-cv, input[name='documents.cv'], input[type=file]", RESUME_UPLOAD, "file"),
]

# Jobvite — opaque jv-field-* ids; autocomplete attrs are stable (corpus).
JOBVITE_PACK: list[tuple[str, str, str]] = [
    ("input[autocomplete='given-name'], input[id^='jv-field-'][autocomplete='given-name']", NAME_FIRST, "fill"),
    ("input[autocomplete='family-name'], input[id^='jv-field-'][autocomplete='family-name']", NAME_LAST, "fill"),
    ("input[autocomplete='email'], input[type=email], input[id^='jv-field-'][autocomplete='email']", EMAIL, "fill"),
    ("input[autocomplete='tel'], input[type=tel], input[id^='jv-field-'][autocomplete='tel']", PHONE, "fill"),
    ("input[name*='linkedin' i], input[placeholder*='LinkedIn' i]", LINKEDIN, "fill"),
    ("input[type=file], input[id*='resume' i], input[name*='resume' i]", RESUME_UPLOAD, "file"),
]

# Taleo JSF — suffix-stable ids; account gate uses dialogTemplate-dialogForm-*.
# Corpus: uhg.taleo.net login + candidateacquisition/flow.jsf personal_info_*.
TALEO_PACK: list[tuple[str, str, str]] = [
    ("input[id$='dialogForm-email'], input[name$='dialogForm-email']", EMAIL, "fill"),
    ("input[id$='dialogForm-emailConfirm'], input[name$='dialogForm-emailConfirm']", EMAIL, "fill"),
    ("input[id$='dialogForm-userName'], input[name$='dialogForm-userName']", EMAIL, "fill"),
    ("input[id$='dialogForm-password'], input[name$='dialogForm-password'], input[type=password]:not([id*='Confirm']):not([name*='Confirm'])", PASSWORD, "fill"),
    ("input[id$='dialogForm-passwordConfirm'], input[name$='dialogForm-passwordConfirm'], input[id*='passwordConfirm' i]", PASSWORD_CONFIRM, "fill"),
    ("input[id*='personal_info_FirstName' i], input[name*='personal_info_FirstName' i], input[id*='FirstName' i], input[name*='FirstName' i]", NAME_FIRST, "fill"),
    ("input[id*='personal_info_LastName' i], input[name*='personal_info_LastName' i], input[id*='LastName' i], input[name*='LastName' i]", NAME_LAST, "fill"),
    ("input[id*='personal_info_Email' i], input[name*='personal_info_Email' i], input[type=email]", EMAIL, "fill"),
    ("input[id*='personal_info_Address']:not([id*='Address2']), input[name*='personal_info_Address']:not([name*='Address2'])", ADDRESS_LINE1, "fill"),
    ("input[id*='personal_info_City' i], input[name*='personal_info_City' i]", ADDRESS_CITY, "fill"),
    ("input[id*='personal_info_Zip' i], input[name*='personal_info_Zip' i], input[id*='Postal' i]", ADDRESS_ZIP, "fill"),
    ("input[id*='personal_info_HomePhone' i], input[id*='personal_info_Mobile' i], input[id*='Phone' i], input[name*='Phone' i], input[type=tel]", PHONE, "fill"),
    ("input[id*='ResumeUploadInputFile'], input[name*='ResumeUploadInputFile'], input[type=file]", RESUME_UPLOAD, "file"),
]

# SAP SuccessFactors / sapsf — fbclc_* account gate + CSB/RCM name tokens.
SUCCESSFACTORS_PACK: list[tuple[str, str, str]] = [
    ("#fbclc_firstName, input[id='fbclc_firstName'], input[name*='firstName' i], input[id*='firstName' i], input[autocomplete='given-name']", NAME_FIRST, "fill"),
    ("#fbclc_lastName, input[id='fbclc_lastName'], input[name*='lastName' i], input[id*='lastName' i], input[autocomplete='family-name']", NAME_LAST, "fill"),
    ("#fbclc_userName, input[id='fbclc_userName'], input[name='fbclc_userName'], input[type=email], input[name*='email' i], input[id*='email' i]:not([id*='Confirm' i]), input[autocomplete='email']", EMAIL, "fill"),
    ("#fbclc_emailConfirm, input[id='fbclc_emailConfirm'], input[id*='emailConfirm' i], input[name*='emailConfirm' i]", EMAIL, "fill"),
    ("#fbclc_pwd, input[id='fbclc_pwd'], input[name='fbclc_pwd'], input[type=password]:not([id*='Confirm']):not([name*='Confirm'])", PASSWORD, "fill"),
    ("#fbclc_pwdConfirm, input[id='fbclc_pwdConfirm'], input[name='fbclc_pwdConfirm']", PASSWORD_CONFIRM, "fill"),
    ("input[type=tel], input[name*='phone' i], input[id*='phone' i], input[name*='cellPhone' i], input[autocomplete='tel']", PHONE, "fill"),
    ("input[name*='city' i], input[id*='city' i], input[autocomplete='address-level2']", ADDRESS_CITY, "fill"),
    ("input[name*='zip' i], input[name*='postal' i], input[autocomplete='postal-code']", ADDRESS_ZIP, "fill"),
    ("input[name*='address' i], input[id*='address' i], input[autocomplete='address-line1']", ADDRESS_LINE1, "fill"),
    ("input[name*='linkedin' i], input[placeholder*='LinkedIn' i]", LINKEDIN, "fill"),
    ("input[type=file]", RESUME_UPLOAD, "file"),
]

# Dayforce / Ceridian candidate portal — formcontrolname + camelCase names.
DAYFORCE_PACK: list[tuple[str, str, str]] = [
    ("input[formcontrolname='firstName'], input[name='firstName'], input[id*='firstName' i], input[autocomplete='given-name']", NAME_FIRST, "fill"),
    ("input[formcontrolname='lastName'], input[name='lastName'], input[id*='lastName' i], input[autocomplete='family-name']", NAME_LAST, "fill"),
    ("input[formcontrolname='email'], input[name='email'], input[type=email], input[id*='email' i], input[autocomplete='email']", EMAIL, "fill"),
    ("input[formcontrolname='phone'], input[formcontrolname*='phone' i], input[name='phone'], input[name*='phone' i], input[type=tel], input[autocomplete='tel']", PHONE, "fill"),
    ("input[formcontrolname='address'], input[name*='address' i], input[id*='address' i], input[autocomplete='address-line1']", ADDRESS_LINE1, "fill"),
    ("input[formcontrolname='city'], input[name*='city' i], input[id*='city' i], input[autocomplete='address-level2']", ADDRESS_CITY, "fill"),
    ("input[formcontrolname='postalCode'], input[formcontrolname='zip'], input[name*='postal' i], input[name*='zip' i], input[autocomplete='postal-code']", ADDRESS_ZIP, "fill"),
    ("input[formcontrolname='country'], select[formcontrolname='country'], input[name*='country' i]", ADDRESS_COUNTRY, "combobox"),
    ("input[name*='linkedin' i], input[placeholder*='LinkedIn' i]", LINKEDIN, "fill"),
    ("input[type=file]", RESUME_UPLOAD, "file"),
]

# UKG / UltiPro JobBoard — PascalCase ids from Account/Register corpus.
UKG_PACK: list[tuple[str, str, str]] = [
    ("#FirstName, input[name='FirstName'], input[id='FirstName']", NAME_FIRST, "fill"),
    ("#FamilyName, input[name='FamilyName'], input[id='FamilyName'], #LastName, input[name='LastName'], input[id='LastName']", NAME_LAST, "fill"),
    ("#Email, input[name='Email'], input[id='Email'], input[type=email]", EMAIL, "fill"),
    ("#PhoneNumber, input[name='PhoneNumber'], input[id='PhoneNumber'], input[type=tel]", PHONE, "fill"),
    ("#Password, input[name='Password'], input[id='Password']", PASSWORD, "fill"),
    ("#ConfirmPassword, input[name='ConfirmPassword'], input[id='ConfirmPassword']", PASSWORD_CONFIRM, "fill"),
    ("#AddressLine1, input[name='AddressLine1'], input[id='AddressLine1'], input[name*='Address' i]", ADDRESS_LINE1, "fill"),
    ("#City, input[name='City'], input[id='City']", ADDRESS_CITY, "fill"),
    ("#ZipCode, input[name='ZipCode'], input[id='ZipCode'], input[name*='Postal' i]", ADDRESS_ZIP, "fill"),
    ("#Country, select[name='Country'], select[id='Country'], input[name='Country']", ADDRESS_COUNTRY, "combobox"),
    ("input[name*='LinkedIn' i], input[id*='LinkedIn' i]", LINKEDIN, "fill"),
    ("input[type=file]", RESUME_UPLOAD, "file"),
]
ORACLE_PACK = list(GENERIC_SELECTOR_PACK)

# Rippling ATS — opaque names; stable data-testid + placeholders (live + corpus).
RIPPLING_PACK: list[tuple[str, str, str]] = [
    ("[data-testid='input-first_name'], input[placeholder='First name'], input[placeholder*='First name' i]", NAME_FIRST, "fill"),
    ("[data-testid='input-last_name'], input[placeholder='Last name'], input[placeholder*='Last name' i]", NAME_LAST, "fill"),
    ("[data-testid='input-email'], input[placeholder='Email'], input[type=email], input[placeholder*='Email' i]", EMAIL, "fill"),
    ("[data-testid='input-phone_number'], input[placeholder='Phone number'], input[placeholder*='Phone' i]", PHONE, "fill"),
    ("[data-testid='input-linkedin_link'], input[placeholder*='LinkedIn' i]", LINKEDIN, "fill"),
    ("[data-testid='input-website_link'], input[placeholder*='Website' i]", PORTFOLIO, "fill"),
    ("[data-testid='input-resume'], input[type=file]", RESUME_UPLOAD, "file"),
]

# JazzHR / applytojob.com — stable resumator-*-value name/id (corpus + live).
APPLYTOJOB_PACK: list[tuple[str, str, str]] = [
    ("#resumator-firstname-value, input[name='resumator-firstname-value']", NAME_FIRST, "fill"),
    ("#resumator-lastname-value, input[name='resumator-lastname-value']", NAME_LAST, "fill"),
    ("#resumator-email-value, input[name='resumator-email-value'], input[type=email]", EMAIL, "fill"),
    ("#resumator-phone-value, input[name='resumator-phone-value'], input[type=tel]", PHONE, "fill"),
    ("#resumator-address-value, input[name='resumator-address-value']", ADDRESS_LINE1, "fill"),
    ("#resumator-city-value, input[name='resumator-city-value']", ADDRESS_CITY, "fill"),
    ("#resumator-state-value, input[name='resumator-state-value']", ADDRESS_STATE, "fill"),
    ("#resumator-postal-value, input[name='resumator-postal-value']", ADDRESS_ZIP, "fill"),
    ("#resumator-linkedin-value, input[name='resumator-linkedin-value']", LINKEDIN, "fill"),
    ("#resumator-resume-value, input[name='resumator-resume-value'], input[type=file]", RESUME_UPLOAD, "file"),
]

# Breezy HR — cName/cEmail/cPhoneNumber/cResume; skip hp_* honeypots (field_map).
BREEZY_PACK: list[tuple[str, str, str]] = [
    ("input[name='cName'], input[placeholder='Full Name'], input[placeholder*='Full Name' i]", NAME_FULL, "fill"),
    ("input[name='cEmail'], input[type=email], input[placeholder='Email Address']", EMAIL, "fill"),
    ("input[name='cPhoneNumber'], input[placeholder='Phone Number'], input[type=tel]", PHONE, "fill"),
    ("input[name='cAddress'], #fullAddress, input[placeholder='Address']", ADDRESS_LINE1, "fill"),
    ("input[name*='linkedin' i], input[placeholder*='LinkedIn' i]", LINKEDIN, "fill"),
    ("#main-attachment, input[name='cResume'], input[type=file]", RESUME_UPLOAD, "file"),
]

# JobScore careers.jobscore.com — candidate_card[*] + resume_document.
JOBSCORE_PACK: list[tuple[str, str, str]] = [
    ("#candidate_card_first_name, input[name='candidate_card[first_name]']", NAME_FIRST, "fill"),
    ("#candidate_card_last_name, input[name='candidate_card[last_name]']", NAME_LAST, "fill"),
    ("#candidate_card_home_email, input[name='candidate_card[home_email]']", EMAIL, "fill"),
    ("#candidate_card_home_phone, input[name='candidate_card[home_phone]']", PHONE, "fill"),
    ("#candidate_card_home_street1, input[name='candidate_card[home_street1]']", ADDRESS_LINE1, "fill"),
    ("#candidate_card_home_city, input[name='candidate_card[home_city]']", ADDRESS_CITY, "fill"),
    ("#candidate_card_home_state_us, select[id='candidate_card_home_state_us']", ADDRESS_STATE, "combobox"),
    ("#candidate_card_home_postal_code, input[name='candidate_card[home_postal_code]']", ADDRESS_ZIP, "fill"),
    ("#candidate_card_home_country, select[name='candidate_card[home_country]']", ADDRESS_COUNTRY, "combobox"),
    ("#contact_online_profile_links_linked_in_0, input[placeholder*='LinkedIn' i]", LINKEDIN, "fill"),
    ("#resume_document, input[name='file'][type=file], input[type=file]", RESUME_UPLOAD, "file"),
]

# Gem jobs.gem.com — nameless inputs; label-adjacent Playwright selectors (live).
GEM_PACK: list[tuple[str, str, str]] = [
    ("span:has-text('First name') + div input, div.flex-30:has(> span:has-text('First name')) input", NAME_FIRST, "fill"),
    ("span:has-text('Last name') + div input, div.flex-30:has(> span:has-text('Last name')) input", NAME_LAST, "fill"),
    ("span:has-text('Email') + div input, div.flex-30:has(> span:has-text('Email')) input", EMAIL, "fill"),
    ("span:has-text('Phone') + div input, div.flex-30:has(> span:has-text('Phone')) input", PHONE, "fill"),
    ("span:has-text('LinkedIn') + div input, div.flex-30:has(> span:has-text('LinkedIn')) input", LINKEDIN, "fill"),
    ("span:has-text('Location') + div input, div.flex-30:has(> span:has-text('Location')) input", ADDRESS_CITY, "fill"),
    ("input[type=file]", RESUME_UPLOAD, "file"),
]

# Dover app.dover.com — React Hook Form name attrs (firstName/email/linkedinUrl/…).
DOVER_PACK: list[tuple[str, str, str]] = [
    ("input[name='firstName'], input[autocomplete='given-name']", NAME_FIRST, "fill"),
    ("input[name='lastName'], input[autocomplete='family-name']", NAME_LAST, "fill"),
    ("input[name='email'], input[type=email], input[autocomplete='email']", EMAIL, "fill"),
    ("input[name='phoneNumber'], input[name='phone'], input[type=tel], input[autocomplete='tel']", PHONE, "fill"),
    ("input[name='linkedinUrl'], input[name*='linkedin' i], input[placeholder*='LinkedIn' i]", LINKEDIN, "fill"),
    ("input[type=file]", RESUME_UPLOAD, "file"),
]

# Phenom white-label (cntryFields.*) + hosted phenompeople contact ids.
PHENOM_PACK: list[tuple[str, str, str]] = [
    ("input[id='cntryFields.firstName'], #firstName, input[name='firstName'], input[autocomplete='given-name']", NAME_FIRST, "fill"),
    ("input[id='cntryFields.lastName'], #lastName, input[name='lastName'], input[autocomplete='family-name']", NAME_LAST, "fill"),
    ("#email, input[id='email'], input[name='email'], input[type=email], input[autocomplete='email']", EMAIL, "fill"),
    ("input[id='phoneWidget.phoneNumber'], #phone, input[name='phone'], input[autocomplete='tel-national'], input[autocomplete='tel']", PHONE, "fill"),
    ("input[id='cntryFields.addressLine1'], input[autocomplete='address-line1']", ADDRESS_LINE1, "fill"),
    ("input[id='cntryFields.city'], input[autocomplete='address-level2']", ADDRESS_CITY, "fill"),
    ("input[id='cntryFields.postalCode'], input[autocomplete='postal-code']", ADDRESS_ZIP, "fill"),
    ("#country, select[id='country'], input[id='cntryFields.country']", ADDRESS_COUNTRY, "combobox"),
    ("input[name*='linkedin' i], input[placeholder*='LinkedIn' i]", LINKEDIN, "fill"),
    ("input[type=file]", RESUME_UPLOAD, "file"),
]

SELECTOR_PACKS: dict[str, list[tuple[str, str, str]]] = {
    "greenhouse": GH_SELECTOR_PACK,
    "lever": LEVER_SELECTOR_PACK,
    "workday": WD_SELECTOR_PACK,
    "ashby": ASHBY_SELECTOR_PACK,
    "icims": ICIMS_SELECTOR_PACK,
    "smartrecruiters": SMARTRECRUITERS_PACK,
    "workable": WORKABLE_PACK,
    "bamboohr": BAMBOOHR_PACK,
    "recruitee": RECRUITEE_PACK,
    "personio": PERSONIO_PACK,
    "jobvite": JOBVITE_PACK,
    "taleo": TALEO_PACK,
    "successfactors": SUCCESSFACTORS_PACK,
    "dayforce": DAYFORCE_PACK,
    "ukg": UKG_PACK,
    "oracle": ORACLE_PACK,
    "rippling": RIPPLING_PACK,
    "applytojob": APPLYTOJOB_PACK,
    "breezy": BREEZY_PACK,
    "jobscore": JOBSCORE_PACK,
    "gem": GEM_PACK,
    "dover": DOVER_PACK,
    "phenom": PHENOM_PACK,
    # First-class non-ATS path — never skip fill because platform==unknown
    "unknown": GENERIC_SELECTOR_PACK,
}


def coverage_path_for(platform: str) -> str:
    """How this URL is filled: workday_multipage | selector_pack | generic_dom."""
    if platform == "workday":
        return "workday_multipage"
    if platform == "unknown":
        return "generic_dom"
    if platform in SELECTOR_PACKS:
        return "selector_pack+generic_dom"
    return "generic_dom"


# Entry pre-pass
CLICKABLE_KINDS = frozenset({"ENTRY", "RESUME_ENTRY", "ADVANCE"})
KIND_PRIORITY = {"RESUME_ENTRY": 0, "ENTRY": 1, "ADVANCE": 2}

FORM_FIELD_SELECTORS = [
    "input#first_name",
    "input#last_name",
    "input#email",
    "input#phone",
    "input[name='job_application[first_name]']",
    "input[name='job_application[email]']",
    "input[name='name']",
    "input[name='email']",
    "input[name='emails']",
    # Bare type=email is soft evidence only (job-alert widgets filtered in iframe_ctx)
    "input[type='email']",
    # iCIMS login / create-account gate (iframe Apply → /login)
    "input[name='css_loginName']",
    "input[type='password']",
    "input[name='PersonProfileFields.FirstName']",
    "input[name='resume']",
    "input[type='file']",
    "textarea[name='comments']",
    "#application",
    "form#application-form",
    "form[action*='apply']",
    ".application-form",
    "[data-automation-id='legalNameSection_firstName']",
    "[data-automation-id='contactInformationPage']",
    "[data-qa='btn-submit']",
    # Generic / non-ATS / Phenom white-label career pages
    "input[autocomplete='given-name']",
    "input[autocomplete='family-name']",
    "input[autocomplete='email']",
    "form[id*='apply' i]",
    "form[class*='apply' i]",
    "form[class*='application' i]",
    "form[action*='application' i]",
    "[data-testid*='application' i]",
    "[data-ph-at-id*='apply' i]",
    "input[name*='firstName' i]",
    "input[name*='first_name' i]",
    "input[name*='lastName' i]",
    "input[id*='firstName' i]",
    "input[id*='candidate' i]",
    # Mid-tier ATS form-reached evidence
    "#first-name-input",  # SmartRecruiters
    "#last-name-input",
    "#email-input",
    "#firstname",  # Workable
    "#lastname",
    "input[id^='jv-field-']",  # Jobvite
    "input[id*='ResumeUploadInputFile']",  # Taleo
    "input[id$='dialogForm-email']",
    "input[id*='personal_info_FirstName' i]",
    "#fbclc_userName",  # SuccessFactors account gate
    "#fbclc_firstName",
    "#FirstName",  # UKG / UltiPro Register
    "#FamilyName",
    "#PhoneNumber",
    "input[formcontrolname='firstName']",  # Dayforce
    "input[formcontrolname='email']",
    "input[placeholder='First name']",  # Rippling
    "input[placeholder='Phone number']",
    "form[class*='personio' i]",
    # Mid-small ATS (JazzHR / Breezy / JobScore / Dover)
    "input[name='resumator-firstname-value']",
    "input[name='resumator-email-value']",
    "input[name='cName']",
    "input[name='cEmail']",
    "#main-attachment",
    "input[name='candidate_card[first_name]']",
    "#candidate_card_first_name",
    "#resume_document",
    "input[name='linkedinUrl']",  # Dover
    "input[name='phoneNumber']",
]


def resolve_extract_js() -> Path:
    for p in EXTRACT_JS_CANDIDATES:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "extract_form_fields.js not found under skyvern_runtime/venv "
        "(install Skyvern in that venv)"
    )


def _clean_label(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()
    return t[:120]


def _street_line(address_text: str) -> str:
    m = re.match(r"^(.+?),\s*[A-Za-z .'\-]+,\s*[A-Z]{2}\s+\d{5}", address_text or "")
    return m.group(1).strip() if m else (address_text or "").split(",")[0].strip()


def _normalize_extracted(field: dict) -> dict:
    """Map Skyvern extract shape → field_map.classify_field shape."""
    ftype = field.get("type") or field.get("input_type") or "text"
    return {
        "label": field.get("label") or "",
        "aria_label": field.get("ariaLabel") or field.get("aria_label") or "",
        "name": field.get("name") or "",
        "id": field.get("id") or "",
        "placeholder": field.get("placeholder") or "",
        "autocomplete": field.get("autocomplete") or "",
        "type": ftype,
        "input_type": field.get("input_type") or ftype,
        "tag": field.get("tag") or "input",
        "selector": field.get("selector") or "",
        "required": field.get("required", False),
        "options": field.get("options") or [],
        "aria_hidden": field.get("ariaHidden") or field.get("aria_hidden") or "",
        "title": field.get("title") or "",
        "role": field.get("role") or "",
    }


def _detect_blocker(page_text: str, title: str, url: str) -> str | None:
    blob = f"{title}\n{url}\n{page_text}".lower()
    checks = [
        ("ashby_spam_flagged", (
            "flagged as possible spam",
            "couldn't submit your application",
            "could not submit your application",
            "we couldn't submit your application",
        )),
        ("captcha", ("captcha", "recaptcha", "hcaptcha", "cf-challenge", "challenge-platform",
                     "i'm not a robot", "i am not a robot", "captcha-delivery",
                     "geo.captcha-delivery")),
        ("akamai", ("akamai", "access denied", "reference #", "pardon our interruption",
                    "bot detection", "unusual traffic")),
        ("cloudflare", ("just a moment", "cf-browser-verification", "attention required")),
        ("email_verify", (
            "check your email", "verify your email", "verification email",
            "verification link", "confirm your email", "we've sent", "we have sent",
            "email has been sent", "activate your account", "complete your registration",
        )),
    ]
    for name, needles in checks:
        if any(n in blob for n in needles):
            return name
    return None


# ---------------------------------------------------------------------------
# ENTRY pre-pass (button_gate on every click)
# ---------------------------------------------------------------------------


async def snapshot_controls(page) -> list[dict]:
    """Visible button/link labels on a Page or Frame."""
    return await page.evaluate(
        """() => {
          const sel = [
            'button',
            'a[href]',
            'input[type="button"]',
            'input[type="submit"]',
            '[role="button"]',
          ].join(',');
          const nodes = Array.from(document.querySelectorAll(sel));
          const out = [];
          const seen = new Set();
          for (const el of nodes) {
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            const rect = el.getBoundingClientRect();
            if (rect.width < 2 || rect.height < 2) continue;
            let label = (el.innerText || el.value || el.getAttribute('aria-label')
                         || el.getAttribute('title') || '').trim();
            label = label.replace(/\\s+/g, ' ').slice(0, 120);
            if (!label) continue;
            const key = label.toLowerCase() + '|' + (el.tagName || '');
            if (seen.has(key)) continue;
            seen.add(key);
            out.push({
              tag: (el.tagName || '').toLowerCase(),
              text: label,
              type: (el.getAttribute('type') || ''),
              aria_label: (el.getAttribute('aria-label') || ''),
              href: (el.getAttribute('href') || '').slice(0, 200),
              role: (el.getAttribute('role') || ''),
            });
          }
          return out;
        }"""
    )


async def snapshot_controls_anywhere(page) -> list[tuple[Any, list[dict]]]:
    """Controls on top page + apply-looking child frames (iCIMS Apply lives in iframe)."""
    from iframe_ctx import _frame_looks_apply, list_fill_contexts

    out: list[tuple[Any, list[dict]]] = []
    try:
        out.append((page, await snapshot_controls(page)))
    except Exception:
        out.append((page, []))
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for fr in frames:
        if fr == page.main_frame:
            continue
        if not _frame_looks_apply(fr):
            continue
        try:
            ctrls = await snapshot_controls(fr)
        except Exception:
            continue
        if ctrls:
            out.append((fr, ctrls))
    # Also probe contexts ranked by form signal (may include about:blank hosts)
    try:
        for ctx in await list_fill_contexts(page, FORM_FIELD_SELECTORS):
            fr = ctx.get("frame")
            if fr is None or fr is page or fr == page.main_frame:
                continue
            if any(fr is t for t, _ in out):
                continue
            try:
                ctrls = await snapshot_controls(fr)
            except Exception:
                continue
            if ctrls:
                out.append((fr, ctrls))
    except Exception:
        pass
    return out


def classify_controls(raw: list[dict]) -> list[dict]:
    classified = []
    for c in raw:
        text = _clean_label(c.get("text") or "")
        aria = c.get("aria_label") or ""
        btype = c.get("type") or ""
        kind = classify_button(text, button_type=btype, aria_label=aria)
        gate = gate_click(text, button_type=btype, aria_label=aria)
        classified.append(
            {
                "text": text,
                "tag": c.get("tag"),
                "type": btype,
                "aria_label": aria,
                "href": c.get("href") or "",
                "kind": kind,
                "gate_ok": bool(gate.get("ok")),
                "gate_reason": gate.get("reason") or "",
            }
        )
    return classified


async def form_fields_visible(page) -> dict:
    """Top-document form signal (legacy). Prefer form_fields_visible_anywhere."""
    evidence = []
    for sel in FORM_FIELD_SELECTORS:
        try:
            loc = page.locator(sel)
            n = await loc.count()
            if n == 0:
                continue
            for i in range(min(n, 3)):
                item = loc.nth(i)
                try:
                    if await item.is_visible(timeout=400):
                        evidence.append(sel)
                        break
                except Exception:
                    pass
        except Exception:
            continue
    input_count = await page.evaluate(
        """() => {
          const inputs = Array.from(document.querySelectorAll(
            'input:not([type=hidden]):not([type=checkbox]):not([type=radio]), textarea, select'
          ));
          return inputs.filter(el => {
            const s = window.getComputedStyle(el);
            if (s.display === 'none' || s.visibility === 'hidden') return false;
            const r = el.getBoundingClientRect();
            return r.width > 2 && r.height > 2;
          }).length;
        }"""
    )
    return {
        "reached": bool(evidence) or input_count >= 3,
        "evidence_selectors": evidence[:8],
        "visible_input_count": input_count,
    }


async def form_fields_visible_anywhere(page) -> dict:
    """Form signal across top page + child iframes (iCIMS / SPA apply)."""
    from iframe_ctx import pick_fill_context

    ctx = await pick_fill_context(page, FORM_FIELD_SELECTORS)
    return {
        "reached": bool(ctx.get("reached")),
        "evidence_selectors": (ctx.get("evidence") or [])[:8],
        "visible_input_count": int(ctx.get("visible_input_count") or 0),
        "fill_kind": ctx.get("kind"),
        "fill_url": (ctx.get("url") or "")[:200],
        "candidates": ctx.get("candidates"),
        "fill_target": ctx.get("frame"),
    }


async def gated_click_control(page, ctrl: dict):
    """Click only after button_gate allows. Never FINAL.

    ``page`` may be a Playwright Page or Frame (iCIMS Apply in iframe).

    Substring locators (has-text / role prefix) can resolve to a wider FINAL
    control ("Apply" → "Apply and Submit"). Every candidate is re-gated via
    ``gate_locator_click`` on the *resolved* node's labels before click.

    Returns True on same-page click, a Playwright Page if Apply opened a new tab,
    or False on failure.
    """
    text = ctrl["text"]
    kind = ctrl["kind"]
    if kind == FINAL or not ctrl.get("gate_ok"):
        return False
    if kind not in CLICKABLE_KINDS:
        return False
    # Re-gate intent at click time
    gate = gate_click(
        text,
        button_type=ctrl.get("type") or "",
        aria_label=ctrl.get("aria_label") or "",
    )
    if not gate["ok"] or gate["kind"] == FINAL:
        return False

    # Frame → parent Page for tab tracking
    try:
        host_page = page.page if hasattr(page, "page") and page.page is not None else page
    except Exception:
        host_page = page
    before_pages = list(host_page.context.pages)
    href = (ctrl.get("href") or "").strip()
    # Prefix match: Phenom aria = "Apply Now <Job Title>" while innerText = "Apply Now"
    # — still safe because gate_locator_click refuses FINAL widenings.
    name_exact = re.compile(rf"^\s*{re.escape(text)}\s*$", re.I)
    name_prefix = re.compile(rf"^\s*{re.escape(text)}\b", re.I)
    candidates = []
    # Prefer direct apply href when snapshot captured one (Serco / Phenom)
    if href and href not in ("#", "javascript:void(0)", "javascript:;") and not href.lower().startswith("javascript:"):
        if href.startswith("http") or href.startswith("/"):
            candidates.append(page.locator(f'a[href={json.dumps(href)}]'))
        candidates.append(page.locator(f'a[href*="apply" i]').filter(has_text=re.compile(re.escape(text), re.I)))
    candidates.extend(
        [
            page.get_by_role("button", name=name_exact),
            page.get_by_role("link", name=name_exact),
            page.get_by_role("button", name=name_prefix),
            page.get_by_role("link", name=name_prefix),
            page.locator(f"button:has-text({json.dumps(text)})"),
            page.locator(f"a:has-text({json.dumps(text)})"),
            page.locator(f"input[type=submit][value={json.dumps(text)}]"),
            page.locator(f"input[type=button][value={json.dumps(text)}]"),
        ]
    )
    for loc in candidates:
        try:
            n = await loc.count()
        except Exception:
            continue
        # Walk matches — .first alone can be a FINAL sibling of the intent.
        for i in range(min(n, 8)):
            try:
                target = loc.nth(i)
                if not await target.is_visible(timeout=800):
                    continue
                resolved = await gate_locator_click(
                    target, intent_label=text, allow_kinds=NAV_KINDS
                )
                if not resolved.get("ok") or resolved.get("kind") == FINAL:
                    continue
                await target.click(timeout=5000)
                # SPA career pages often navigate / open apply modals slowly
                try:
                    await host_page.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:
                    pass
                await host_page.wait_for_timeout(2500)
                after_pages = list(host_page.context.pages)
                if len(after_pages) > len(before_pages):
                    newest = after_pages[-1]
                    try:
                        await newest.wait_for_load_state("domcontentloaded", timeout=8000)
                    except Exception:
                        pass
                    await newest.wait_for_timeout(800)
                    return newest
                return True
            except Exception:
                continue
    return False


def _entry_click_reason(target: dict) -> str:
    """Human-readable entry_prepass reason for step log."""
    kind = str(target.get("kind") or "")
    text_l = (target.get("text") or "").lower()
    if kind == "RESUME_ENTRY":
        return "apply_with_resume"
    if "manually" in text_l:
        return "apply_manually_fallback"
    return kind[:64]


def _is_manual_entry_candidate(c: dict) -> bool:
    return "manually" in (c.get("text") or "").lower()


def pick_click_candidates(classified: list[dict], *, allow_advance: bool) -> list[dict]:
    out = []
    for c in classified:
        kind = c["kind"]
        if kind == FINAL or kind not in CLICKABLE_KINDS:
            continue
        if kind == "ADVANCE" and not allow_advance:
            continue
        if not c["gate_ok"]:
            continue
        out.append(c)

    def _rank(c: dict) -> tuple:
        href = (c.get("href") or "").lower()
        text_l = (c.get("text") or "").lower()
        # Prefer real Apply links over chatbot "I'm interested"
        apply_href = 0 if ("apply" in href or "jobapplication" in href) else 1
        apply_text = 0 if text_l.startswith("apply") else 1
        # Deprioritize "Apply Manually" when resume/autofill entry is also visible
        manual_penalty = 1 if _is_manual_entry_candidate(c) else 0
        return (KIND_PRIORITY.get(c["kind"], 99), manual_penalty, apply_href, apply_text)

    out.sort(key=_rank)
    return out


# ---------------------------------------------------------------------------
# Page-complete gate + ADVANCE helpers (generic multipage)
# ---------------------------------------------------------------------------

GENERIC_REQUIRED_EMPTY_JS = """() => {
  const out = [];
  const isVisible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0
      && window.getComputedStyle(el).visibility !== 'hidden';
  };
  const isEmptyUi = (raw) => {
    const t = (raw || '').trim().toLowerCase();
    if (!t) return true;
    if (t === 'type here...' || t === 'type here' || t.startsWith('type here')) return true;
    if (t === 'start typing...' || t === 'start typing' || t.startsWith('start typing')) return true;
    if (t === 'select' || t === 'select one' || t.startsWith('select ')) return true;
    if (t === 'choose' || t === '—' || t === '-') return true;
    if (t.startsWith('enter ') && t.length < 40) return true;
    return false;
  };
  const resumeUploaded = (() => {
    for (const fu of document.querySelectorAll('.file-upload, [class*="file-upload"]')) {
      const labEl = fu.querySelector('[id*="upload-label"], .upload-label, label');
      const labT = (labEl && (labEl.innerText || labEl.textContent) || '').toLowerCase();
      if (!/resume|\\bcv\\b/.test(labT) || /cover\\s*letter/.test(labT)) continue;
      const body = (fu.innerText || '').replace(/\\s+/g, ' ').trim();
      if (/\\.(pdf|doc|docx|txt|rtf)\\b/i.test(body)) return true;
    }
    for (const el of document.querySelectorAll(
      '.ashby-application-form-field-entry, [class*="_fieldEntry_"]'
    )) {
      const labEl = el.querySelector('label');
      const labT = (labEl && (labEl.innerText || labEl.textContent) || '').toLowerCase();
      if (!/resume|\\bcv\\b|curriculum/.test(labT) || /cover\\s*letter/.test(labT)) continue;
      const body = (el.innerText || '').replace(/\\s+/g, ' ').trim();
      if (/\\.(pdf|doc|docx|txt|rtf)\\b/i.test(body)) return true;
    }
    for (const inp of document.querySelectorAll('input[type=file]')) {
      const id = ((inp.id || '') + ' ' + (inp.name || '') + ' '
        + (inp.getAttribute('aria-label') || '')).toLowerCase();
      const lab = inp.closest('label, .ashby-application-form-field-entry, [class*="upload"]');
      const labT = (lab && lab.innerText || '').toLowerCase();
      if (!/resume|\\bcv\\b|curriculum|_systemfield_resume/.test(id + ' ' + labT)) continue;
      if (inp.files && inp.files.length > 0) return true;
    }
    return false;
  })();
  const push = (el, reason) => {
    const id = el.getAttribute('name') || el.id || el.getAttribute('data-automation-id')
      || el.getAttribute('aria-label') || el.tagName;
    out.push({id: String(id).slice(0, 80), reason});
  };
  // Required radio groups (one empty probe per name) — previously skipped, so
  // Ashby/Lever screening radios never reached leftovers / Flash.
  const radioNames = new Set();
  document.querySelectorAll(
    'input[type=radio][required], input[type=radio][aria-required="true"], input[type=radio][name]'
  ).forEach((r) => {
    if (!isVisible(r) || r.disabled || !r.name) return;
    const req = r.required || r.getAttribute('aria-required') === 'true';
    const wrap = r.closest('fieldset, [role="group"], label, .ashby-application-form-field-entry, [class*="_fieldEntry_"]');
    const labT = ((wrap && (wrap.innerText || wrap.textContent)) || '').slice(0, 120);
    const star = /\\*/.test(labT);
    if (!req && !star) return;
    radioNames.add(r.name);
  });
  for (const name of radioNames) {
    const group = Array.from(
      document.querySelectorAll('input[type=radio][name="' + CSS.escape(name) + '"]')
    ).filter(isVisible);
    if (!group.length) continue;
    if (group.some((r) => r.checked)) continue;
    push(group[0], 'empty_required_radio_group');
  }
  document.querySelectorAll(
    'input[required], input[aria-required="true"], textarea[required], textarea[aria-required="true"]'
  ).forEach((el) => {
    if (!isVisible(el)) return;
    if (el.disabled || el.type === 'hidden' || el.type === 'checkbox' || el.type === 'radio') return;
    if (el.type === 'file') {
      if (resumeUploaded) return;
      const files = el.files;
      if (!files || files.length < 1) push(el, 'empty_required_file');
      return;
    }
    // Greenhouse react-select: filter input stays empty while .select__single-value
    // (or iti flag + dial display) holds the committed choice.
    // IMPORTANT: do NOT use [class*="select__"] — that matches the input itself
    // (select__input) and never finds .select__single-value.
    const selectRoot = el.closest('.select__container, .select-shell');
    if (selectRoot) {
      const sv = selectRoot.querySelector('.select__single-value');
      const shown = ((sv && (sv.textContent || sv.innerText)) || '').trim();
      const hasFlag = !!selectRoot.querySelector('.iti__flag[class*="iti__"]');
      // Committed when single-value text present OR phone-country flag shown
      if ((shown && !isEmptyUi(shown)) || hasFlag) return;
    }
    // Workday how-heard multiSelect: filter input stays empty while chip chrome
    // shows "1 item selected, LinkedIn" in the formField wrap (Sandoz thrash fix).
    const wdSrc = el.closest(
      '[data-automation-id="formField-source"], [data-automation-id="formField-how_heard"], '
      + '[data-automation-id="formField-howDidYouHear"], [data-automation-id="formField-candidateSource"]'
    );
    if (wdSrc) {
      const wrapText = (wdSrc.innerText || wdSrc.textContent || '').toLowerCase();
      if (/[1-9]\\d*\\s+items?\\s+selected/.test(wrapText)) return;
      if (!wrapText.includes('0 items selected') && (
        wrapText.includes('linkedin') || wrapText.includes('indeed')
        || wrapText.includes('builtin') || wrapText.includes('glassdoor')
        || wrapText.includes('company website') || wrapText.includes('job board')
      )) return;
    }
    const v = (el.value || '').trim();
    const ph = (el.placeholder || '').trim();
    // Placeholder-only UI (Ashby "Type here...") counts as empty even if value
    // somehow equals the placeholder string.
    if (isEmptyUi(v) || (ph && v.toLowerCase() === ph.toLowerCase())) {
      push(el, 'empty_required_input');
    }
  });
  // Resume/CV file inputs often lack required= but still block apply
  document.querySelectorAll('input[type=file]').forEach((el) => {
    if (resumeUploaded) return;
    const id = ((el.id || '') + ' ' + (el.name || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
    const lab = el.closest('label, .ashby-application-form-field-entry, [class*="upload"]');
    const labT = (lab && lab.innerText || '').toLowerCase();
    if (!/resume|\\bcv\\b|curriculum|file-upload|attachment/.test(id + ' ' + labT)) return;
    const files = el.files;
    if (!files || files.length < 1) push(el, 'empty_resume_file');
  });
  // Ashby zip / URL questions sometimes lack required= but still block submit
  // with "Almost complete" while showing Type here... / blank LinkedIn.
  document.querySelectorAll(
    '.ashby-application-form-field-entry input[type=text], '
    + '.ashby-application-form-field-entry input[type=url], '
    + '.ashby-application-form-field-entry textarea, '
    + '[class*="_fieldEntry_"] input[type=text], '
    + '[class*="_fieldEntry_"] input[type=url], '
    + '[class*="_fieldEntry_"] textarea'
  ).forEach((el) => {
    if (!isVisible(el)) return;
    if (el.disabled || el.type === 'hidden') return;
    const lab = (
      (el.closest('.ashby-application-form-field-entry, [class*="_fieldEntry_"]')
        || el).querySelector('label')
    );
    const labelText = (lab && lab.innerText || '').toLowerCase();
    const looksZip = /zip|postal/.test(labelText);
    const looksLinkedIn = /linked\\s*in/.test(labelText);
    const looksGithub = /git\\s*hub/.test(labelText);
    const looksUrlProfile = looksLinkedIn || looksGithub
      || /portfolio|website|personal\\s*site/.test(labelText);
    const starRequired = /\\*/.test(labelText);
    if (
      !looksZip && !looksUrlProfile && !starRequired
      && !el.required && el.getAttribute('aria-required') !== 'true'
    ) return;
    const v = (el.value || '').trim();
    const ph = (el.placeholder || '').trim();
    if (isEmptyUi(v) || (ph && v.toLowerCase() === ph.toLowerCase())
        || (isEmptyUi(ph) && !v)) {
      let reason = 'empty_required_input';
      if (looksZip) reason = 'empty_ashby_zip';
      else if (looksLinkedIn) reason = 'empty_ashby_linkedin';
      else if (looksGithub) reason = 'empty_ashby_github';
      else if (looksUrlProfile) reason = 'empty_ashby_url';
      push(el, reason);
    }
  });
  document.querySelectorAll(
    'select[required], select[aria-required="true"], [role="combobox"][aria-required="true"]'
  ).forEach((el) => {
    if (!isVisible(el)) return;
    // GH react-select combobox input stays empty when single-value/flag committed
    const selectRoot = el.closest('.select__container, .select-shell');
    if (selectRoot) {
      const sv = selectRoot.querySelector('.select__single-value');
      const shown = ((sv && (sv.textContent || sv.innerText)) || '').trim();
      const hasFlag = !!selectRoot.querySelector('.iti__flag[class*="iti__"]');
      if ((shown && !isEmptyUi(shown)) || hasFlag) return;
    }
    const t = (el.value || el.innerText || el.getAttribute('aria-label') || '').trim().toLowerCase();
    if (isEmptyUi(t) || t.startsWith('select ')) {
      push(el, 'empty_required_select');
    }
  });
  // Radio-group emptiness (group-level): required / aria-required / * label
  (() => {
    const byName = new Map();
    for (const r of document.querySelectorAll('input[type=radio]')) {
      if (!isVisible(r) || r.disabled) continue;
      const name = r.name || '';
      if (!name) continue;
      if (!byName.has(name)) byName.set(name, []);
      byName.get(name).push(r);
    }
    for (const [name, radios] of byName.entries()) {
      if (radios.some((r) => r.checked)) continue;
      const required = radios.some(
        (r) => r.required || r.getAttribute('aria-required') === 'true'
      );
      let label = '';
      const first = radios[0];
      if (first.id) {
        const l = document.querySelector(`label[for="${CSS.escape(first.id)}"]`);
        if (l) label = (l.innerText || l.textContent || '').trim();
      }
      if (!label) {
        const fs = first.closest('fieldset, [role="radiogroup"], [role="group"]');
        if (fs) {
          const lab = fs.querySelector('legend, label');
          label = ((lab && (lab.innerText || lab.textContent)) || '').trim();
        }
      }
      const star = /\\*/.test(label);
      if (!required && !star) continue;
      out.push({
        id: String(name || label || 'radio_group').slice(0, 80),
        reason: 'empty_required_radio_group',
        label: String(label || name).slice(0, 120),
      });
    }
  })();
  const seen = new Set();
  return out.filter((x) => {
    const k = x.id + '|' + x.reason;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  }).slice(0, 20);
}"""

ADVANCE_BUTTON_TEXTS = (
    "Save and Continue",
    "Save & Continue",
    "Next",
    "Continue",
)


# Cookie consent labels only — exact role match. Never bare "Decline" (EEO).
# Never substring has-text ("I agree" → "I Agree and Submit" FINAL).
# FILL2-007: omit bare Reject/Agree/OK — too ambiguous vs form controls.
_COOKIE_BANNER_EXACT_TEXTS = (
    "Reject all",
    "Reject All",
    "Reject cookies",
    "Reject Cookies",
    "Decline all",
    "Decline All",
    "Decline cookies",
    "Decline Cookies",
    "Necessary only",
    "Only necessary",
    "Essential only",
    "Accept all",
    "Accept All",
    "Accept cookies",
    "Accept Cookies",
    "I agree",
    "Got it",
    "Allow all",
    "Allow All",
    "Allow cookies",
)
_COOKIE_EXACT_ALLOWED = frozenset(t.casefold() for t in _COOKIE_BANNER_EXACT_TEXTS)
_COOKIE_EEO_DECLINE_RE = re.compile(
    r"decline\s+to\s+self|self[\s_-]*identif|prefer\s+not\s+to|"
    r"rather\s+not|do\s+not\s+wish|choose\s+not\s+to|"
    r"^decline$",  # bare Decline is EEO-ambiguous — refuse (use Decline cookies/all)
    re.I,
)
# Cookie clicks may be UNKNOWN ("Accept all") — still refuse FINAL / submit.
_COOKIE_ALLOW_KINDS = NAV_KINDS + (UNKNOWN,)


def cookie_control_safe_to_click(
    label: str,
    *,
    button_type: str = "",
    aria_label: str = "",
    value: str = "",
) -> dict[str, Any]:
    """Refuse FINAL / submit / EEO Decline widenings for cookie dismiss.

    Unit-testable without Playwright. Prefer exact cookie roles; never
    has-text that matches Submit.
    """
    display = (label or value or aria_label or "").strip()
    blob = " ".join(x for x in (label, value, aria_label) if x)
    if not display:
        return {"ok": False, "kind": FINAL, "reason": "empty cookie control label"}
    if _COOKIE_EEO_DECLINE_RE.search(blob):
        return {
            "ok": False,
            "kind": "EEO",
            "reason": f"refuse EEO Decline via cookie path: {display!r}",
        }
    # Prefer allow-list of exact cookie phrases.
    if display.casefold() not in _COOKIE_EXACT_ALLOWED:
        return {
            "ok": False,
            "kind": UNKNOWN,
            "reason": f"refuse non-exact cookie label: {display!r}",
        }
    # FILL2-007 defense: ultra-short global labels never cookie-dismiss.
    if display.casefold() in {"reject", "agree", "ok", "yes", "no", "continue"}:
        return {
            "ok": False,
            "kind": UNKNOWN,
            "reason": f"refuse ambiguous short cookie label: {display!r}",
        }
    gate = gate_click(
        label,
        button_type=button_type,
        aria_label=aria_label,
        value=value,
    )
    kind = gate.get("kind") or classify_button(
        label, button_type=button_type, aria_label=aria_label, value=value
    )
    if kind == FINAL or not gate.get("ok"):
        return {
            "ok": False,
            "kind": kind or FINAL,
            "reason": gate.get("reason")
            or f"refuse FINAL/submit-like cookie click: {display!r}",
        }
    if kind not in _COOKIE_ALLOW_KINDS:
        return {
            "ok": False,
            "kind": kind,
            "reason": f"cookie kind {kind!r} not allowed: {display!r}",
        }
    # Defense: any label containing submit is never a cookie dismiss.
    if re.search(r"\bsubmit\b", blob, re.I):
        return {
            "ok": False,
            "kind": FINAL,
            "reason": f"refuse submit-like has-text cookie match: {display!r}",
        }
    return {"ok": True, "kind": kind, "reason": "allowed"}


async def dismiss_cookie_banners(page) -> dict:
    """Dismiss cookie / consent overlays so blanks are scorable. Never CAPTCHA.

    Exact role/name match only (no has-text substring). Every click is gated —
    never FINAL, never EEO Decline. CAPTCHA probe failure → fail closed (skip).
    """
    out: dict[str, Any] = {
        "clicked": [],
        "skipped_captcha": False,
        "refused": [],
    }
    try:
        if await page_shows_interactive_captcha(page):
            out["skipped_captcha"] = True
            return out
    except Exception as e:
        # FILL-009: fail closed — do not click Agree/OK near an unknown challenge.
        out["skipped_captcha"] = True
        out["captcha_check_error"] = str(e)[:120]
        return out

    for t in _COOKIE_BANNER_EXACT_TEXTS:
        try:
            # Exact accessible name only — never button:has-text (FINAL widenings).
            loc = page.get_by_role(
                "button", name=re.compile(rf"^{re.escape(t)}$", re.I)
            )
            if await loc.count() == 0:
                loc = page.get_by_role(
                    "link", name=re.compile(rf"^{re.escape(t)}$", re.I)
                )
            if await loc.count() == 0:
                continue
            n = min(await loc.count(), 6)
            for i in range(n):
                btn = loc.nth(i)
                try:
                    if not await btn.is_visible(timeout=400):
                        continue
                except Exception:
                    continue
                resolved = await gate_locator_click(
                    btn,
                    intent_label=t,
                    allow_kinds=_COOKIE_ALLOW_KINDS,
                )
                actual = str(resolved.get("actual") or t)[:80]
                safe = cookie_control_safe_to_click(
                    actual,
                    button_type=str(
                        (await btn.get_attribute("type")) or ""
                    ),
                    aria_label=str(
                        (await btn.get_attribute("aria-label")) or ""
                    ),
                )
                if (
                    not resolved.get("ok")
                    or resolved.get("kind") == FINAL
                    or not safe.get("ok")
                ):
                    out["refused"].append(
                        {
                            "intent": t,
                            "actual": actual,
                            "reason": safe.get("reason")
                            or resolved.get("reason")
                            or "refused",
                        }
                    )
                    continue
                await btn.click(timeout=2000)
                out["clicked"].append(actual[:60])
                await asyncio.sleep(0.35)
                return out  # one dismiss is enough
        except Exception:
            continue
    return out


def skip_ashby_location_zip(
    platform: str | None = None, report: dict | None = None
) -> bool:
    """True when Ashby location→zip must not run (Workday postalCode owns zip).

    0842: ``ashby_location_zip`` claimed ADDRESS_ZIP on nxp.wd3 after contact
    pack already filled postalCode.
    """
    plat = str(platform or (report or {}).get("platform") or "").strip().lower()
    if plat == "workday":
        return True
    url = str(
        (report or {}).get("url")
        or (report or {}).get("final_url")
        or (report or {}).get("start_url")
        or ""
    )
    if re.search(r"myworkdayjobs\.com|myworkdaysite\.com", url, re.I):
        return True
    return False


async def required_empty_on_page(page, report: dict | None = None) -> list[dict]:
    """Page-complete probe for generic ATS / unknown multipage forms.

    Workday path prefers ``workday_selectors._required_empty_on_page`` (imported
    when available) so Present/date-spin rules stay consistent.
    """
    try:
        from workday_selectors import _required_empty_on_page as _wd_req

        # Workday-flavored DOM (automation-id date spins / Present checkbox)
        has_wd = await page.evaluate(
            """() => !!(
              document.querySelector('[data-automation-id="dateSectionMonth-display"]')
              || document.querySelector('[data-automation-id="bottom-navigation-next-button"]')
              || document.querySelector('input[name="currentlyWorkHere"]')
            )"""
        )
        if has_wd:
            return await _wd_req(page)
    except Exception:
        pass
    try:
        empties = await page.evaluate(GENERIC_REQUIRED_EMPTY_JS)
    except Exception as e:
        # FILL2-003: fail closed — empty list would allow ADVANCE on probe failure.
        return [
            {
                "label": f"required_probe_error: {str(e)[:120]}",
                "reason": "probe_error",
            }
        ]
    verified = report_has_verified_resume(report) if report else False
    if not verified:
        try:
            from resume_upload import resume_satisfied_on_page

            verified = await resume_satisfied_on_page(page)
        except Exception:
            verified = False
    if verified:
        try:
            from resume_upload import filter_resume_required_empties

            empties = filter_resume_required_empties(empties, resume_verified=True)
        except Exception:
            pass
    return empties


async def _demote_filled_against_required_empty(page, report: dict, values: dict) -> dict:
    """Demote verified rows whose live DOM still shows empty/placeholder.

    Also one last Ashby zip refill if zip remains empty after earlier fills.
    """
    empties = await required_empty_on_page(page, report)
    try:
        from field_done import filter_required_empty_false_incomplete

        empties = await filter_required_empty_false_incomplete(page, report, empties)
    except Exception:
        pass
    report["required_empty_after_fill"] = empties

    demoted: list[dict] = []
    zip_retry = None

    # Live probe: Ashby zip — prefer visible "home zip" label; ignore hidden twins.
    live_zip = ""
    try:
        live_zip = await page.evaluate(
            """() => {
              const entries = Array.from(document.querySelectorAll(
                '.ashby-application-form-field-entry, [class*="_fieldEntry_"]'
              ));
              let fallback = '';
              for (const el of entries) {
                const lab = el.querySelector(
                  'label.ashby-application-form-question-title, label[class*="_heading_"], label'
                );
                const t = (lab && lab.innerText || '').toLowerCase();
                if (!/zip|postal/.test(t)) continue;
                const inputs = Array.from(el.querySelectorAll(
                  'input[type=text], input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio]), textarea'
                ));
                for (const inp of inputs) {
                  const r = inp.getBoundingClientRect();
                  const style = window.getComputedStyle(inp);
                  const visible = r.width > 0 && r.height > 0
                    && style.visibility !== 'hidden' && style.display !== 'none';
                  const val = (inp.value || '').trim();
                  if (/home\\s*zip|postal\\s*code/.test(t) && visible) {
                    return val;
                  }
                  if (visible && !fallback) fallback = val;
                }
              }
              return fallback;
            }"""
        )
    except Exception:
        live_zip = ""

    zip_still_empty = is_empty_ui_value(live_zip)
    empty_ids = " ".join(str(e.get("id") or "") for e in empties).lower()
    empty_reasons = " ".join(str(e.get("reason") or "") for e in empties).lower()

    # Workday/Ashby how_heard multi-select: demote when chip not committed
    how_heard_still_empty = (
        "empty_required_multiselect" in empty_reasons
        or "source--source" in empty_ids
        or any("how did you hear" in str(e.get("label") or "").lower() for e in empties)
    )
    if how_heard_still_empty or any(
        isinstance(f, dict)
        and (
            f.get("type") == HOW_HEARD
            or f.get("automation_id") == "how_heard"
        )
        for f in (report.get("filled") or [])
    ):
        from verified_select import (
            how_heard_source_committed,
            is_multiselect_uncommitted,
            is_uncommitted_filter_text as _hh_filter_text,
            filter_phone_country_false_empties,
            phone_country_verified_snips_from_report,
            read_phone_country_field_snip,
        )

        live_pc_snip = await read_phone_country_field_snip(page)
        fallbacks = phone_country_verified_snips_from_report(report)
        empties = filter_phone_country_false_empties(
            empties,
            live_pc_snip,
            fallback_snips=fallbacks,
        )
        report["required_empty_after_fill"] = empties
        empty_ids = " ".join(str(e.get("id") or "") for e in empties).lower()
        empty_reasons = " ".join(str(e.get("reason") or "") for e in empties).lower()

        live_hh_snip = ""
        try:
            live_hh_snip = await page.evaluate(
                """() => {
                  const field = document.querySelector(
                    '[data-automation-id="formField-source"], '
                    + '[data-automation-id*="formField-source"], '
                    + '[data-automation-id="formField-how_heard"]'
                  );
                  return field
                    ? (field.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200)
                    : '';
                }"""
            )
        except Exception:
            live_hh_snip = ""
        # Live chip chrome wins over stale required_empty (source--source) noise
        if how_heard_source_committed(live_hh_snip):
            how_heard_still_empty = False
        if is_multiselect_uncommitted(live_hh_snip) or how_heard_still_empty or any(
            isinstance(f, dict)
            and (
                f.get("type") == HOW_HEARD
                or f.get("automation_id") == "how_heard"
            )
            and _hh_filter_text(
                str(f.get("readback") or ""),
                str(f.get("value") or f.get("picked") or ""),
                picked=f.get("picked"),
                from_input=True,
            )
            for f in (report.get("filled") or [])
        ):
            kept_hh: list[dict] = []
            for f in report.get("filled") or []:
                if not isinstance(f, dict):
                    kept_hh.append(f)
                    continue
                is_hh = (
                    f.get("type") == HOW_HEARD
                    or f.get("automation_id") == "how_heard"
                )
                rb_hh = str(f.get("readback") or "")
                intended_hh = str(f.get("value") or f.get("picked") or "")
                filter_only = bool(
                    intended_hh
                    and _hh_filter_text(
                        rb_hh, intended_hh, picked=f.get("picked"), from_input=True
                    )
                )
                # Never demote a row whose live/readback chip is committed
                if is_hh and how_heard_source_committed(live_hh_snip or rb_hh):
                    kept_hh.append(f)
                    continue
                if is_hh and (
                    how_heard_still_empty
                    or is_multiselect_uncommitted(live_hh_snip)
                    or is_multiselect_uncommitted(rb_hh)
                    or filter_only
                    or f.get("committed") is False
                ):
                    demoted.append(dict(f))
                    report.setdefault("leftovers", []).append(
                        {
                            "label": f.get("label") or "How Did You Hear About Us",
                            "type": HOW_HEARD,
                            "selector": f.get("selector"),
                            "reason": "multiselect_uncommitted",
                            "readback": live_hh_snip or rb_hh,
                            "flash_candidate": True,
                            "via": f.get("via") or "demote_how_heard",
                        }
                    )
                else:
                    kept_hh.append(f)
            report["filled"] = kept_hh

    if "empty_ashby_zip" in empty_reasons:
        zip_still_empty = True

    # If Ashby zip still empty, try one more location→zip settle before demoting.
    # Never on Workday — postalCode is a different widget (0842 second writer).
    if (
        zip_still_empty
        and values.get(ADDRESS_ZIP)
        and not skip_ashby_location_zip(str(report.get("platform") or ""), report)
    ):
        try:
            from ashby_widgets import _ashby_zip_field_present

            if not await _ashby_zip_field_present(page):
                report["ashby_zip_absent"] = True
                zip_still_empty = False
            else:
                zip_rows = await fill_ashby_location_then_zip(page, values)
                zip_retry = [
                    {
                        k: r.get(k)
                        for k in ("ok", "verified", "readback", "reason")
                        if k in r
                    }
                    for r in (zip_rows or [])
                ]
                report["ashby_location_zip_retry"] = zip_retry
                ok_zip = next(
                    (
                        r
                        for r in (zip_rows or [])
                        if r.get("type") == ADDRESS_ZIP and r.get("verified")
                    ),
                    None,
                )
                if ok_zip:
                    report["filled"] = [
                        f
                        for f in (report.get("filled") or [])
                        if f.get("type") != ADDRESS_ZIP
                    ]
                    report["filled"].append(ok_zip)
                    report["leftovers"] = [
                        u
                        for u in (report.get("leftovers") or [])
                        if u.get("type") != ADDRESS_ZIP
                    ]
                    live_zip = str(ok_zip.get("readback") or "")
                    zip_still_empty = is_empty_ui_value(live_zip)
                    empties = await required_empty_on_page(page, report)
                    report["required_empty_after_fill"] = empties
                    empty_ids = " ".join(str(e.get("id") or "") for e in empties).lower()
        except Exception as e:
            zip_retry = {"error": str(e)[:120]}

    if zip_still_empty and not report.get("ashby_zip_absent"):
        kept = []
        for f in report.get("filled") or []:
            if f.get("type") == ADDRESS_ZIP:
                demoted.append(dict(f))
                report.setdefault("leftovers", []).append(
                    {
                        "label": f.get("label") or "zip / postal code",
                        "type": ADDRESS_ZIP,
                        "selector": f.get("selector"),
                        "reason": "live_empty_after_claimed_verified",
                        "readback": live_zip or f.get("readback") or "",
                        "flash_candidate": True,
                        "via": f.get("via"),
                    }
                )
            else:
                kept.append(f)
        report["filled"] = kept

    # Live LinkedIn probe — Ashby DOM vs Greenhouse/generic.
    # BUG (Tax Relief UNFILLABLE): always calling live_ashby_url_readback on GH
    # returned "" and demoted every LinkedIn fill as live_empty.
    is_ashby_dom = False
    try:
        is_ashby_dom = bool(
            await page.evaluate(
                """() => !!document.querySelector(
                  '.ashby-application-form-field-entry, [class*="_fieldEntry_"]'
                )"""
            )
        )
    except Exception:
        is_ashby_dom = False

    live_li: str | None = ""
    if is_ashby_dom:
        try:
            live_li = await live_ashby_url_readback(page, LINKEDIN)
        except Exception:
            live_li = ""
    else:
        # Greenhouse / generic LinkedIn text/url input
        try:
            live_li = await page.evaluate(
                """() => {
                  const tryInp = (inp) => {
                    if (!inp) return null;
                    const r = inp.getBoundingClientRect();
                    const s = getComputedStyle(inp);
                    if (r.width < 2 || r.height < 2) return null;
                    if (s.visibility === 'hidden' || s.display === 'none') return null;
                    return (inp.value || '').trim();
                  };
                  for (const sel of [
                    'input[name*="linkedin" i]',
                    'input[id*="linkedin" i]',
                    'input[placeholder*="LinkedIn" i]',
                    'input[aria-label*="LinkedIn" i]',
                    'input[type=url]',
                  ]) {
                    for (const el of document.querySelectorAll(sel)) {
                      const lab = (el.getAttribute('aria-label') || el.placeholder
                        || el.name || el.id || '');
                      if (sel.includes('url') && !/linked\\s*in/i.test(lab)) {
                        // bare type=url — only if nearby label says LinkedIn
                        const wrap = el.closest('div, fieldset, li') || el.parentElement;
                        const t = (wrap && wrap.innerText || '').slice(0, 80);
                        if (!/linked\\s*in/i.test(t)) continue;
                      }
                      const v = tryInp(el);
                      if (v !== null) return v;
                    }
                  }
                  for (const lab of document.querySelectorAll('label')) {
                    if (!/linked\\s*in/i.test(lab.innerText || '')) continue;
                    let inp = lab.querySelector('input');
                    if (!inp) {
                      const forId = lab.getAttribute('for');
                      if (forId) inp = document.getElementById(forId);
                    }
                    if (!inp && lab.parentElement) {
                      inp = lab.parentElement.querySelector(
                        'input[type=text], input[type=url], input:not([type=hidden])'
                      );
                    }
                    const v = tryInp(inp);
                    if (v !== null) return v;
                  }
                  return null; // field not found — do not demote
                }"""
            )
        except Exception:
            live_li = None

    li_still_empty = False
    if live_li is None:
        li_still_empty = False  # no LinkedIn field on page
    else:
        li_still_empty = is_empty_ui_value(live_li)
    if (
        is_ashby_dom
        and "empty_ashby_linkedin" in empty_reasons
        and is_empty_ui_value(live_li)
    ):
        li_still_empty = True
    if li_still_empty and values.get(LINKEDIN):
        # One last label-based refill before demoting
        try:
            if is_ashby_dom:
                li_row = await fill_ashby_url_by_label(
                    page, LINKEDIN, str(values[LINKEDIN])
                )
            else:
                # Greenhouse reassert-style refill
                li_row = {
                    "ok": False,
                    "verified": False,
                    "type": LINKEDIN,
                    "via": "gh_linkedin_demote_retry",
                }
                for sel in (
                    "input[name*='linkedin' i]",
                    "input[id*='linkedin' i]",
                    "input[placeholder*='LinkedIn' i]",
                    "input[aria-label*='LinkedIn' i]",
                    "label:has-text('LinkedIn') input",
                ):
                    loc = page.locator(sel).first
                    if await loc.count() == 0:
                        continue
                    skip_ok, skip_rb = await _locator_already_correct(
                        loc, str(values[LINKEDIN])
                    )
                    if skip_ok:
                        li_row = {
                            "ok": True,
                            "verified": True,
                            "type": LINKEDIN,
                            "selector": sel,
                            "value": values[LINKEDIN],
                            "readback": (skip_rb or "")[:120],
                            "via": "gh_linkedin_demote_retry",
                            "mode": "fill",
                            "reason": "already_correct_skip",
                            "skipped_already_correct": True,
                        }
                        break
                    await loc.fill(str(values[LINKEDIN]), timeout=3000)
                    rb = await _read_locator_value(loc)
                    ok = bool(rb) and (
                        "linkedin" in rb.lower() or len(rb) > 8
                    )
                    li_row = {
                        "ok": ok,
                        "verified": ok,
                        "type": LINKEDIN,
                        "selector": sel,
                        "value": values[LINKEDIN],
                        "readback": (rb or "")[:120],
                        "via": "gh_linkedin_demote_retry",
                        "mode": "fill",
                    }
                    if ok:
                        break
            report.setdefault("linkedin_demote_retry", []).append(
                {
                    k: li_row.get(k)
                    for k in ("ok", "verified", "readback", "reason", "selector", "via")
                    if k in li_row
                }
            )
            if li_row.get("ok") and li_row.get("verified"):
                report["filled"] = [
                    f for f in (report.get("filled") or []) if f.get("type") != LINKEDIN
                ]
                report["filled"].append(li_row)
                report["leftovers"] = [
                    u
                    for u in (report.get("leftovers") or [])
                    if u.get("type") != LINKEDIN
                ]
                live_li = str(li_row.get("readback") or "")
                li_still_empty = is_empty_ui_value(live_li)
            else:
                li_still_empty = True
        except Exception as e:
            report.setdefault("linkedin_demote_retry", {"error": str(e)[:120]})

    if li_still_empty and live_li is not None and values.get(LINKEDIN):
        want_li = str(values[LINKEDIN])
        for f in report.get("filled") or []:
            if f.get("type") != LINKEDIN:
                continue
            claimed_li = str(f.get("readback") or f.get("verified_value") or "")
            sel_li = str(f.get("selector") or "")
            if sel_li:
                try:
                    loc_li = page.locator(_playwright_sel(sel_li)).first
                    if await loc_li.count() > 0:
                        rb_li = await _read_locator_value(loc_li)
                        if rb_li and _value_matches_readback(want_li, rb_li):
                            live_li = rb_li
                            li_still_empty = False
                            break
                except Exception:
                    pass
            if (
                claimed_li
                and _value_matches_readback(want_li, claimed_li)
                and not should_demote_claimed_text_fill(
                    sel_found=True,
                    live_rb=live_li or "",
                    intended=want_li,
                    claimed_rb=claimed_li,
                    field_type=LINKEDIN,
                    id_still_empty=False,
                )
            ):
                live_li = claimed_li
                li_still_empty = False
                break

    if li_still_empty and live_li is not None:
        kept = []
        for f in report.get("filled") or []:
            if f.get("type") == LINKEDIN:
                demoted.append(dict(f))
                report.setdefault("leftovers", []).append(
                    {
                        "label": f.get("label") or "LinkedIn Profile",
                        "type": LINKEDIN,
                        "selector": f.get("selector"),
                        "reason": "live_empty_after_claimed_verified",
                        "readback": live_li or f.get("readback") or "",
                        "verified_value": None,
                        "flash_candidate": True,
                        "via": f.get("via"),
                    }
                )
            else:
                kept.append(f)
        report["filled"] = kept
        # Ensure LinkedIn is a leftover even if never claimed filled (Ashby only)
        if is_ashby_dom and values.get(LINKEDIN) and not any(
            isinstance(u, dict) and u.get("type") == LINKEDIN
            for u in (report.get("leftovers") or [])
        ):
            if "empty_ashby_linkedin" in empty_reasons or live_li == "":
                try:
                    has_li = await page.evaluate(
                        """() => {
                          const entries = Array.from(document.querySelectorAll(
                            '.ashby-application-form-field-entry, [class*="_fieldEntry_"]'
                          ));
                          return entries.some((el) => {
                            const lab = el.querySelector('label');
                            return /linked\\s*in/i.test((lab && lab.innerText) || '');
                          });
                        }"""
                    )
                except Exception:
                    has_li = "empty_ashby_linkedin" in empty_reasons
                if has_li:
                    report.setdefault("leftovers", []).append(
                        {
                            "label": "LinkedIn URL",
                            "type": LINKEDIN,
                            "reason": "live_empty_linkedin",
                            "readback": live_li or "",
                            "verified_value": None,
                            "flash_candidate": True,
                            "via": "demote_live_probe",
                        }
                    )

    report["live_linkedin_readback"] = (live_li or "")[:80] if live_li is not None else ""

    # Resume: demote claimed verified when FileList is empty after SPA remount
    try:
        from resume_upload import probe_resume_field

        if _gh_resume_verified_for_reassert(report):
            ru_probe = await probe_resume_field(page)
            # GH/Ashby uploaded UI counts as verified — do not demote contact reassert rows
            if (
                ru_probe.get("uploaded_ui")
                or ru_probe.get("ashby_uploaded_ui")
                or not ru_probe.get("empty")
            ):
                report["resume_verified"] = True
            elif ru_probe.get("present") and ru_probe.get("empty"):
                kept_resume = []
                for f in report.get("filled") or []:
                    if not isinstance(f, dict):
                        kept_resume.append(f)
                        continue
                    if is_post_resume_reassert_via(str(f.get("via") or "")):
                        kept_resume.append(f)
                        continue
                    if is_resume_attachment_row(f):
                        demoted.append(dict(f))
                        report.setdefault("leftovers", []).append(
                            {
                                "label": f.get("label") or "Resume / CV",
                                "type": f.get("type") or RESUME_UPLOAD,
                                "selector": f.get("selector"),
                                "reason": "live_empty_after_claimed_verified",
                                "readback": "",
                                "flash_candidate": True,
                                "via": f.get("via") or "demote_resume_probe",
                            }
                        )
                    else:
                        kept_resume.append(f)
                report["filled"] = kept_resume
                report["resume_verified"] = False
    except Exception:
        pass

    # Broader: re-read claimed fills live. Stale readback (SPA wipe / resume
    # parse) must demote even when report still has a non-empty verified string.
    still = []
    for f in report.get("filled") or []:
        if is_post_resume_reassert_via(str(f.get("via") or "")):
            if not _gh_resume_verified_for_reassert(report):
                continue
            if f.get("reason") in ("already_correct_skip", "already_correct_keep"):
                # Combobox/salary claims can lie after SPA wipe — still live-verify.
                mode = str(f.get("mode") or "")
                ftype_chk = str(f.get("type") or "")
                if mode not in ("gh_select", "select", "combobox", "typable_dropdown") and ftype_chk not in (
                    SALARY_EXPECTED,
                    SALARY_CURRENT,
                    SCHOOL,
                    DEGREE,
                ):
                    still.append(f)
                    continue
                # Fall through to live re-read below for select-like fields.
        if f.get("mode") == "file" or f.get("type") == RESUME_UPLOAD:
            still.append(f)
            continue
        ftype_s = str(f.get("type") or "")
        intended = str(f.get("value") or f.get("verified_value") or f.get("picked") or f.get("shown") or "")
        claimed_rb = str(f.get("readback") or f.get("verified_value") or f.get("shown") or "")

        # Places Location autocomplete — verify committed City/State/Country (Airwallex)
        if f.get("mode") == "location_autocomplete" or ftype_s in (
            ADDRESS_CITY,
            LOCATION,
        ):
            if f.get("skipped_already_correct") or f.get("reason") == "already_correct_skip":
                still.append(f)
                continue
            sel = str(f.get("selector") or "")
            live_rb = ""
            sel_found = False
            label_txt = str(f.get("label") or "Location")
            loc = None
            if sel:
                try:
                    loc = page.locator(_playwright_sel(sel)).first
                    if await loc.count() > 0:
                        sel_found = True
                except Exception:
                    loc = None
            if not sel_found:
                try:
                    loc = page.locator(
                        "label.ashby-application-form-question-title:has-text('Location') "
                        "~ div [role=combobox], "
                        ".ashby-application-form-field-entry:has(label:has-text('Location')) "
                        "[role=combobox]"
                    ).first
                    if await loc.count() > 0:
                        sel_found = True
                except Exception:
                    loc = None
            if sel_found and loc is not None:
                try:
                    skip_ok, skip_rb = await _locator_already_correct(
                        loc,
                        intended,
                        field_type=ftype_s,
                        label=label_txt,
                    )
                    if skip_ok:
                        f = dict(f)
                        f["readback"] = (skip_rb or "")[:120]
                        f["reason"] = "already_correct_keep"
                        still.append(f)
                        continue
                    live_rb = skip_rb or await _read_locator_value(loc)
                except Exception:
                    live_rb = ""
            if sel_found and loc is not None:
                committed_ok, probe_shown = await _location_committed_on_page(
                    page, loc, intended
                )
                if committed_ok:
                    kept_row = dict(f)
                    kept_row["readback"] = (probe_shown or live_rb or claimed_rb)[:120]
                    kept_row["reason"] = "already_correct_keep"
                    still.append(kept_row)
                    continue
            if should_demote_claimed_text_fill(
                sel_found=sel_found,
                live_rb=live_rb or claimed_rb,
                intended=intended,
                claimed_rb=claimed_rb,
                field_type=ftype_s,
            ):
                demoted.append(dict(f))
                report.setdefault("leftovers", []).append(
                    {
                        "label": f.get("label") or ftype_s or "Location",
                        "type": ftype_s or ADDRESS_CITY,
                        "selector": sel,
                        "reason": "live_empty_after_claimed_verified",
                        "readback": live_rb or "",
                        "stale_readback": claimed_rb[:80],
                        "verified_value": None,
                        "flash_candidate": True,
                        "via": f.get("via"),
                    }
                )
            else:
                still.append(f)
            continue

        # gh_select / react-select: verify committed display (never filter input)
        # Includes gh_select_sweep / reclaim / inpage_*_gh_select vias.
        if _is_gh_select_fill_row(f):
            live_rb = ""
            sel_found = False
            label_txt = str(f.get("label") or "")
            try:
                from gh_select import _shown_matches_cands, aliases_for
                from verified_select import is_placeholder_select_value, read_gh_select_display

                cands = aliases_for(ftype_s, intended)
                if intended and intended not in cands:
                    cands = [intended, *cands]
                container = None
                if label_txt:
                    needle = re.sub(r"\s+", " ", label_txt.replace("*", "")).strip()[:48]
                    if needle:
                        lab = page.locator("label").filter(
                            has_text=re.compile(re.escape(needle[:32]), re.I)
                        ).first
                        if await lab.count():
                            container = page.locator(".select__container").filter(
                                has=lab
                            ).first
                            if await container.count() == 0:
                                container = lab.locator(
                                    "xpath=ancestor::div[contains(@class,'select__container') "
                                    "or contains(@class,'select-shell')][1]"
                                ).first
                if container is not None and await container.count():
                    sel_found = True
                    live_rb = await read_gh_select_display(container)
                if sel_found and live_rb and not is_placeholder_select_value(live_rb):
                    if _shown_matches_cands(live_rb, cands, field_type=ftype_s):
                        f = dict(f)
                        f["readback"] = live_rb[:120]
                        f["reason"] = "already_correct_keep"
                        still.append(f)
                        continue
                    # GH Country* often commits as dial-only "+1" — shown_matches
                    # rejects bare dial, but value_matches_readback accepts it.
                    if intended and _value_matches_readback(intended, live_rb):
                        f = dict(f)
                        f["readback"] = live_rb[:120]
                        f["reason"] = "already_correct_keep"
                        still.append(f)
                        continue
                    # Live shows a different committed value (e.g. Decline vs No) —
                    # demote. Do not trust claimed over a wrong live commit.
                    bad = dict(f)
                    bad["_demote_reason"] = "live_mismatch"
                    demoted.append(bad)
                    report.setdefault("leftovers", []).append(
                        {
                            "label": f.get("label") or ftype_s,
                            "type": ftype_s,
                            "selector": f.get("selector"),
                            "reason": "live_mismatch_after_claimed_verified",
                            "readback": live_rb or "",
                            "stale_readback": claimed_rb[:80],
                            "verified_value": None,
                            "flash_candidate": True,
                            "via": f.get("via"),
                        }
                    )
                    continue
                if sel_found and (
                    not live_rb
                    or is_placeholder_select_value(live_rb)
                    or is_empty_ui_value(live_rb)
                ):
                    # Remount: control found but display blank — trust prior verified
                    # Decline/commit (same rule as text fills). Do not false-demote RACE.
                    # Salary/school/degree MUST demote — Tax Relief SPA wipe left
                    # already_correct_skip claims while the select showed blank.
                    force_blank_demote = ftype_s in (
                        SALARY_EXPECTED,
                        SALARY_CURRENT,
                        SCHOOL,
                        DEGREE,
                    )
                    if (not force_blank_demote) and (
                        not should_demote_claimed_text_fill(
                            sel_found=True,
                            live_rb=live_rb or "",
                            intended=intended,
                            claimed_rb=claimed_rb,
                            field_type=ftype_s,
                        )
                    ):
                        kept = dict(f)
                        kept["reason"] = "already_correct_keep"
                        if claimed_rb and (
                            not kept.get("readback")
                            or is_empty_ui_value(str(kept.get("readback") or ""))
                        ):
                            kept["readback"] = claimed_rb[:120]
                        still.append(kept)
                        continue
                    demoted.append(dict(f))
                    report.setdefault("leftovers", []).append(
                        {
                            "label": f.get("label") or ftype_s,
                            "type": ftype_s,
                            "selector": f.get("selector"),
                            "reason": "live_empty_after_claimed_verified",
                            "readback": live_rb or "",
                            "stale_readback": claimed_rb[:80],
                            "verified_value": None,
                            "flash_candidate": True,
                            "via": f.get("via"),
                        }
                    )
                    continue
                if not sel_found:
                    # Fragile label/container miss after remount — keep verified claim
                    still.append(f)
                    continue
            except Exception:
                still.append(f)
                continue

        if f.get("mode") in ("yesno", "radio", "checkbox"):
            # Verify checked/selected state — do not blanket-skip
            sel = str(f.get("selector") or "")
            sel_found = False
            live_ok = False
            if sel:
                try:
                    loc = page.locator(_playwright_sel(sel)).first
                    if await loc.count() > 0:
                        sel_found = True
                        try:
                            live_ok = await loc.is_checked()
                        except Exception:
                            live_ok = bool(await _read_locator_value(loc))
                except Exception:
                    pass
            if sel_found and live_ok:
                still.append(f)
                continue
            if sel_found and not live_ok:
                demoted.append(dict(f))
                report.setdefault("leftovers", []).append(
                    {
                        "label": f.get("label") or ftype_s,
                        "type": ftype_s,
                        "selector": sel,
                        "reason": "live_empty_after_claimed_verified",
                        "readback": "",
                        "stale_readback": claimed_rb[:80],
                        "verified_value": None,
                        "flash_candidate": True,
                        "via": f.get("via"),
                    }
                )
                continue
            still.append(f)
            continue

        if f.get("via") == "ashby_widgets":
            sel = str(f.get("selector") or "")
            live_rb = ""
            sel_found = False
            if sel:
                try:
                    loc = page.locator(_playwright_sel(sel)).first
                    if await loc.count() > 0:
                        sel_found = True
                        skip_ok, skip_rb = await _locator_already_correct(
                            loc, intended, field_type=ftype_s, label=label_txt
                        )
                        if skip_ok:
                            f = dict(f)
                            f["readback"] = (skip_rb or "")[:120]
                            f["reason"] = "already_correct_keep"
                            still.append(f)
                            continue
                        live_rb = skip_rb or await _read_locator_value(loc)
                except Exception:
                    live_rb = ""
            if should_demote_claimed_text_fill(
                sel_found=sel_found,
                live_rb=live_rb,
                intended=intended,
                claimed_rb=claimed_rb,
                field_type=ftype_s,
            ):
                demoted.append(dict(f))
                report.setdefault("leftovers", []).append(
                    {
                        "label": f.get("label") or ftype_s,
                        "type": ftype_s,
                        "selector": sel,
                        "reason": "live_empty_after_claimed_verified",
                        "readback": live_rb or "",
                        "stale_readback": claimed_rb[:80],
                        "verified_value": None,
                        "flash_candidate": True,
                        "via": f.get("via"),
                    }
                )
                continue
            still.append(f)
            continue

        sel = str(f.get("selector") or "")
        live_rb = ""
        sel_found = False
        if sel:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    sel_found = True
                    live_rb = await _read_locator_value(loc)
            except Exception:
                live_rb = ""
        # Greenhouse: fragile label:has-text('First Name*') input:visible often
        # misses the real #first_name that the pack filled — confirm via id.
        _gh_id = {
            NAME_FIRST: "#first_name",
            NAME_LAST: "#last_name",
            EMAIL: "#email",
            PHONE: "#phone",
        }.get(str(f.get("type") or ""))
        # Preferred First Name is NOT #first_name — never remap that label.
        _lab_l = str(f.get("label") or "").lower()
        if _lab_l.startswith("preferred"):
            _gh_id = None
        if (not live_rb or is_empty_ui_value(live_rb)) and _gh_id:
            try:
                alt = page.locator(_gh_id).first
                if await alt.count() > 0:
                    alt_rb = await _read_locator_value(alt)
                    if alt_rb and not is_empty_ui_value(alt_rb):
                        live_rb = alt_rb
                        sel_found = True
                        # Prefer stable id going forward
                        f = dict(f)
                        f["selector"] = _gh_id
                        sel = _gh_id
            except Exception:
                pass
        # Greenhouse LinkedIn: confirm via any linkedin-labeled input before demote
        if str(f.get("type") or "") == LINKEDIN and (
            not live_rb or is_empty_ui_value(live_rb)
        ):
            try:
                live_li_gh = await page.evaluate(
                    """() => {
                      const hit = (el) => (el && (el.value || '').trim()) || '';
                      for (const inp of document.querySelectorAll(
                        'input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio])'
                      )) {
                        const blob = [
                          inp.getAttribute('aria-label') || '',
                          inp.name || '', inp.id || '', inp.placeholder || '',
                        ].join(' ');
                        if (/linked\\s*in/i.test(blob)) {
                          const v = hit(inp);
                          if (v) return v;
                        }
                      }
                      for (const lab of document.querySelectorAll('label')) {
                        if (!/linked\\s*in/i.test(lab.innerText || '')) continue;
                        const forId = lab.getAttribute('for');
                        if (forId) {
                          const v = hit(document.getElementById(forId));
                          if (v) return v;
                        }
                        const root = lab.closest(
                          '.field, .form-field, .application--field, [class*=\"field\"]'
                        ) || lab.parentElement;
                        if (root) {
                          const v = hit(root.querySelector(
                            'input:not([type=hidden]):not([type=file])'
                          ));
                          if (v) return v;
                        }
                      }
                      return '';
                    }"""
                )
                if live_li_gh and not is_empty_ui_value(str(live_li_gh)):
                    live_rb = str(live_li_gh)
                    sel_found = True
            except Exception:
                pass
        name_m = re.search(r'name=["\']([^"\']+)', sel)
        fid = (name_m.group(1) if name_m else "").lower()
        id_still_empty = bool(fid and fid in empty_ids and sel_found)
        if should_demote_claimed_text_fill(
            sel_found=sel_found,
            live_rb=live_rb,
            intended=intended,
            claimed_rb=claimed_rb,
            field_type=ftype_s,
            id_still_empty=id_still_empty,
        ) and ftype_s not in (ADDRESS_ZIP,):
            demoted.append(dict(f))
            report.setdefault("leftovers", []).append(
                {
                    "label": f.get("label") or f.get("type"),
                    "type": f.get("type"),
                    "selector": sel,
                    "reason": (
                        "live_empty_after_claimed_verified"
                        if (is_empty_ui_value(live_rb) or id_still_empty)
                        else "empty_readback_never_filled"
                    ),
                    "readback": live_rb or "",
                    "stale_readback": claimed_rb[:80],
                    "verified_value": None,
                    "flash_candidate": True,
                    "via": f.get("via"),
                }
            )
        else:
            if (
                intended
                and live_rb
                and not is_empty_ui_value(live_rb)
                and _value_matches_readback(intended, live_rb)
            ):
                f = dict(f)
                f["readback"] = (live_rb or "")[:120]
                f["reason"] = "already_correct_keep"
            still.append(f)
    report["filled"] = still
    report["demoted_false_verified"] = [
        {
            "type": d.get("type"),
            "reason": (
                "live_mismatch_after_claimed_verified"
                if str(d.get("_demote_reason") or "") == "live_mismatch"
                else "live_empty_after_claimed_verified"
            ),
            "stale_readback": (d.get("readback") or d.get("stale_readback") or "")[:80],
        }
        for d in demoted
    ]
    report["live_zip_readback"] = (live_zip or "")[:32]
    # Honest leftovers: sync live required empties not already tracked.
    # Skip empties field_is_done already covers (0842Z country phone dual-oracle).
    existing_keys = {
        str(u.get("selector") or u.get("label") or u.get("type") or "").lower()
        for u in (report.get("leftovers") or [])
        if isinstance(u, dict)
    }
    done_keys: set[str] = set()
    try:
        from field_done import field_is_done_from_row

        for f in report.get("filled") or []:
            if isinstance(f, dict) and field_is_done_from_row(f).ok:
                done_keys |= _field_identity_keys(f)
    except Exception:
        done_keys = set()
    for e in empties:
        eid = str(e.get("id") or "").lower()
        if not eid or eid in existing_keys:
            continue
        e_keys = _field_identity_keys({"label": eid, "automation_id": eid})
        if done_keys and _identity_keys_overlap(e_keys, done_keys):
            continue
        row = enrich_gh_id_leftover(
            {
                "label": eid[:80],
                "type": None,
                "selector": "",
                "reason": f"live_required_empty:{e.get('reason')}",
                "flash_candidate": True,
            }
        )
        report.setdefault("leftovers", []).append(row)
        existing_keys.add(eid)
        # Also key by enriched label so we don't double-add
        existing_keys.add(str(row.get("label") or "").lower())
    # leftover_miss_scan lives in the refill loop only — not a second voter here.
    # Drop ghost Ashby _systemfield_resume leftovers when resume path succeeded
    if report_has_verified_resume(report) or report.get("resume_verified"):
        try:
            from resume_upload import filter_resume_leftovers

            report["leftovers"] = filter_resume_leftovers(report.get("leftovers") or [])
        except Exception:
            pass
    result = {"demoted": demoted, "zip_retry": zip_retry}
    _apply_demote_result(report, result)
    return result


async def try_advance_if_page_complete(page, report: dict | None = None) -> dict:
    """ADVANCE once only when required visible fields look complete. Never FINAL.

    Returns a small result dict folded into ``report['page_advance']`` by callers.
    Prefer FAIL-before-ADVANCE: when required empties exist, do not click Next.
    Tracks page fingerprints so stuck-on-same-page is visible in the report.
    """
    from page_progress import capture_step_fingerprint, note_advance_result, record_page_seen

    result: dict[str, Any] = {
        "attempted": False,
        "advanced": False,
        "advance_blocked_reason": None,
        "required_empty_before_advance": [],
        "advanced_incomplete": False,
        "validation_after_advance": None,
        "clicks": [],
        "next_existed": False,
        "fingerprint_before": None,
        "fingerprint_after": None,
        "stuck_on_same_page": False,
    }
    before = await capture_step_fingerprint(page)
    result["fingerprint_before"] = before["fingerprint"]
    if report is not None:
        record_page_seen(
            report,
            before["fingerprint"],
            meta={"url": before["url"], "title": before["title"], "step_hint": before["step_hint"]},
        )

    required_empty = await required_empty_on_page(page, report)
    try:
        from field_done import filter_required_empty_false_incomplete

        required_empty = await filter_required_empty_false_incomplete(
            page, report, required_empty
        )
    except Exception:
        pass
    result["required_empty_before_advance"] = required_empty

    # Find an ADVANCE control (never FINAL) — needed for stuck detection even
    # when we refuse to click due to required empties.
    raw = await snapshot_controls(page)
    classified = classify_controls(raw)
    cands = pick_click_candidates(classified, allow_advance=True)
    advance_cands = [
        c for c in cands
        if c["kind"] == "ADVANCE"
        and any(
            needle.lower() in (c.get("text") or "").lower()
            for needle in ADVANCE_BUTTON_TEXTS
        )
    ]
    next_existed = bool(advance_cands)
    result["next_existed"] = next_existed

    if required_empty:
        result["advance_blocked_reason"] = "required_fields_empty"
        after = await capture_step_fingerprint(page)
        result["fingerprint_after"] = after["fingerprint"]
        if report is not None:
            # ATS3-003: FAIL-before-ADVANCE must not sticky-stuck (match WD gate).
            progress = note_advance_result(
                report,
                fingerprint_before=before["fingerprint"],
                fingerprint_after=after["fingerprint"],
                next_existed=False,
                advance_clicked=False,
            )
            result["stuck_on_same_page"] = progress["stuck_on_same_page"]
            report["advance_blocked_reason"] = "required_fields_empty"
            report["advanced_incomplete"] = False
            report["required_empty_before_advance"] = required_empty
            # FAIL-before-ADVANCE only when a Next/Continue control exists.
            # Single-page GH (no ADVANCE) still records empties; final honesty
            # uses required_empty_after_fill after Flash/refill (reconcile clears
            # a stale required_fields_empty once those blanks are filled).
            if next_existed:
                report.setdefault("blocker", "page_incomplete")
                if not report.get("verdict") or report.get("verdict") == "SUCCESS":
                    report["verdict"] = "FAIL"
        return result

    if not advance_cands:
        result["advance_blocked_reason"] = "no_advance_button"
        after = await capture_step_fingerprint(page)
        result["fingerprint_after"] = after["fingerprint"]
        if report is not None:
            note_advance_result(
                report,
                fingerprint_before=before["fingerprint"],
                fingerprint_after=after["fingerprint"],
                next_existed=False,
                advance_clicked=False,
            )
        return result

    # Never ADVANCE with an open listbox / mid-widget prompt (GH react-select,
    # portal listbox, Workday searchSelect). Match Workday settle gate.
    try:
        from verified_select import settle_before_advance

        settle = await settle_before_advance(page, report)
        if settle.get("still_open"):
            result["advance_blocked_reason"] = "listbox_still_open"
            result["attempted"] = False
            after = await capture_step_fingerprint(page)
            result["fingerprint_after"] = after["fingerprint"]
            if report is not None:
                report["listbox_open"] = True
                report["mid_widget_open"] = True
                report["advance_blocked_reason"] = "listbox_still_open"
                report["blocker"] = report.get("blocker") or "page_incomplete"
                if not report.get("verdict") or report.get("verdict") == "SUCCESS":
                    report["verdict"] = "FAIL"
                note_advance_result(
                    report,
                    fingerprint_before=before["fingerprint"],
                    fingerprint_after=after["fingerprint"],
                    next_existed=True,
                    advance_clicked=False,
                )
            return result
    except Exception as e:
        result.setdefault("settle_error", str(e)[:80])

    target = advance_cands[0]
    result["attempted"] = True
    ok = await gated_click_control(page, target)
    result["clicks"].append({
        "text": target.get("text"),
        "kind": "ADVANCE",
        "action": "clicked" if ok else "failed",
    })
    if not ok:
        result["advance_blocked_reason"] = "advance_click_failed"
        after = await capture_step_fingerprint(page)
        result["fingerprint_after"] = after["fingerprint"]
        if report is not None:
            progress = note_advance_result(
                report,
                fingerprint_before=before["fingerprint"],
                fingerprint_after=after["fingerprint"],
                next_existed=True,
                advance_clicked=False,
            )
            result["stuck_on_same_page"] = progress["stuck_on_same_page"]
        return result

    result["advanced"] = True
    if report is not None:
        report["advanced"] = True
        try:
            from field_lock import clear_locks_on_advance

            clear_locks_on_advance(report)
        except Exception:
            pass
    try:
        await page.wait_for_timeout(1200)
    except Exception:
        pass

    after = await capture_step_fingerprint(page)
    result["fingerprint_after"] = after["fingerprint"]
    if report is not None:
        progress = note_advance_result(
            report,
            fingerprint_before=before["fingerprint"],
            fingerprint_after=after["fingerprint"],
            next_existed=True,
            advance_clicked=True,
        )
        result["stuck_on_same_page"] = progress["stuck_on_same_page"]
        if progress["stuck_on_same_page"] and report.get("verdict") == "SUCCESS":
            report["verdict"] = "FAIL"

    # Lightweight validation banner probe (generic + Workday needles)
    try:
        body = await page.evaluate(
            "() => (document.body && document.body.innerText || '').slice(0, 4000)"
        )
        low = (body or "").lower()
        needles = [
            n for n in (
                "errors found",
                "is required",
                "this field is required",
                "please complete",
                "please fill",
            )
            if n in low
        ]
        if needles:
            result["validation_after_advance"] = {
                "present": True,
                "needles": needles,
                "snippet": (body or "")[:400],
            }
            result["advanced_incomplete"] = True
            if report is not None:
                report["validation_after_advance"] = result["validation_after_advance"]
                report["advanced_incomplete"] = True
                report["blocker"] = "validation_errors"
                report["verdict"] = "FAIL"
    except Exception as e:
        _log.debug("validation banner probe after advance failed: %s", e)
    return result


# Heavy client-rendered ATS SPAs where the apply form hydrates async even on a
# direct deep-link with no entry click — give the long SPA wait before declaring
# generic_dom_no_fields (SmartRecruiters IE11/unsupported + slow React hydrate).
_SPA_LONG_WAIT_PLATFORMS = frozenset(
    {"smartrecruiters", "phenom", "workable", "gem", "dover", "jobvite"}
)


async def entry_prepass(page, *, max_clicks: int = 3, report: dict | None = None) -> dict:
    """ENTRY/RESUME_ENTRY/ADVANCE via button_gate until form fields appear.

    May switch `page` to a newly opened tab after Apply; the active page is
    returned as report['page'] for the caller to continue filling.
    Form discovery is iframe-aware (iCIMS / SPA apply hosts).
    """
    report: dict[str, Any] = {
        "clicked": [],
        "refused": [],
        "final_seen": [],
        "final_clicks": 0,
        "form": None,
        "time_to_form_seconds": None,
        "buttons_seen_count": 0,
        "page": page,
        "switched_tab": False,
    }
    t0 = time.time()
    seen_labels: set[str] = set()
    clicks_done = 0
    active = page

    def _form_public(form: dict) -> dict:
        return {k: v for k, v in form.items() if k != "fill_target"}

    form = await form_fields_visible_anywhere(active)
    if form["reached"]:
        report["form"] = _form_public(form)
        report["time_to_form_seconds"] = round(time.time() - t0, 2)
        report["fill_target"] = form.get("fill_target")
        return report

    # Give apply iframes a moment to mount (classic iCIMS content iframe)
    try:
        await active.wait_for_timeout(1200)
        for fr in list(active.frames):
            if fr == active.main_frame:
                continue
            try:
                url = (fr.url or "").lower()
                name = (fr.name or "").lower()
            except Exception:
                continue
            if "icims" in url or "icims" in name or "apply" in name:
                try:
                    await fr.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
    except Exception:
        pass

    for round_i in range(max_clicks + 1):
        # Top page + apply iframes (classic iCIMS: Apply is inside icims_content_iframe)
        anywhere = await snapshot_controls_anywhere(active)
        classified_by_ctx: list[tuple[Any, list[dict]]] = []
        for ctx, raw in anywhere:
            classified = classify_controls(raw)
            classified_by_ctx.append((ctx, classified))
            for c in classified:
                key = f"{c['kind']}|{c['text'].lower()}"
                if key in seen_labels:
                    continue
                seen_labels.add(key)
                report["buttons_seen_count"] += 1
                if c["kind"] == FINAL:
                    report["final_seen"].append(c["text"])
                    report["refused"].append(
                        {"text": c["text"], "kind": FINAL, "reason": c["gate_reason"] or "FINAL"}
                    )

        form = await form_fields_visible_anywhere(active)
        if form["reached"]:
            report["form"] = _form_public(form)
            report["time_to_form_seconds"] = round(time.time() - t0, 2)
            report["fill_target"] = form.get("fill_target")
            break
        if clicks_done >= max_clicks:
            break

        # Prefer ENTRY on any context; iframe Apply often beats empty top page
        # NEVER allow ADVANCE while a form with required empties is already visible
        # on this context — that is incomplete multipage ADVANCE.
        pick_ctx = active
        candidates: list[dict] = []
        for ctx, classified in classified_by_ctx:
            has_entry = any(
                c["kind"] in ("ENTRY", "RESUME_ENTRY") and c["gate_ok"] for c in classified
            )
            allow_adv = not has_entry
            if allow_adv:
                try:
                    # ATS-009: check required empties in THIS context (incl. iframe),
                    # not only when ctx is the active top page.
                    form_here = await form_fields_visible(ctx)
                    if form_here.get("reached"):
                        empties = await required_empty_on_page(ctx)
                        if empties:
                            allow_adv = False
                except Exception:
                    pass
            cands = pick_click_candidates(classified, allow_advance=allow_adv)
            clicked_texts = {x["text"].lower() for x in report["clicked"]}
            cands = [c for c in cands if c["text"].lower() not in clicked_texts]
            if cands:
                # Prefer ENTRY/RESUME_ENTRY over ADVANCE; prefer iframe when ENTRY
                best = cands[0]
                if best["kind"] in ("ENTRY", "RESUME_ENTRY") or not candidates:
                    candidates = cands
                    pick_ctx = ctx
                if best["kind"] in ("ENTRY", "RESUME_ENTRY") and ctx is not active:
                    # iCIMS: commit to iframe Apply immediately
                    break

        if not candidates:
            break

        target = candidates[0]
        ok = await gated_click_control(pick_ctx, target)
        clicks_done += 1
        # New tab opened by Apply
        if ok is not True and ok is not False and ok is not None:
            active = ok
            report["switched_tab"] = True
            report["page"] = active
            ok = True
        report["clicked"].append(
            {
                "text": target["text"],
                "kind": target["kind"],
                "ok": bool(ok),
                "round": round_i,
                "in_iframe": pick_ctx is not active and pick_ctx is not page,
            }
        )
        note_step(
            report,
            action="click_entry",
            label=str(target.get("text") or "")[:80],
            via="entry_prepass",
            reason=_entry_click_reason(target),
            extra={
                "round": round_i,
                "in_iframe": pick_ctx is not active and pick_ctx is not page,
                "ok": bool(ok),
                "kind": target.get("kind"),
            },
        )
        if not ok:
            break
        form = await form_fields_visible_anywhere(active)
        if form["reached"]:
            report["form"] = _form_public(form)
            report["time_to_form_seconds"] = round(time.time() - t0, 2)
            report["fill_target"] = form.get("fill_target")
            break

    # SPA / delayed iframe: poll after Apply before declaring no form
    if not (report.get("form") or {}).get("reached"):
        from iframe_ctx import wait_for_form_spa

        clicked_apply = bool(report.get("clicked"))
        # Known heavy SPAs (SmartRecruiters/Phenom/Workday-hosted) hydrate the
        # apply form async even on a direct deep-link with no entry click, so
        # give them the long wait too — otherwise a robust form is mislabeled
        # generic_dom_no_fields. Honest residual only after this real wait.
        _spa_platform = str(report.get("platform") or "").lower()
        long_spa = clicked_apply or _spa_platform in _SPA_LONG_WAIT_PLATFORMS
        spa = await wait_for_form_spa(
            active,
            evidence_selectors=FORM_FIELD_SELECTORS,
            # Phenom / company career SPAs often need longer after Apply→/apply
            timeout_ms=20000 if long_spa else 3500,
            poll_ms=900,
            clicked_apply=clicked_apply,
        )
        report["spa_wait"] = {
            "reached": spa.get("reached"),
            "waited_ms": spa.get("waited_ms"),
            "polls": spa.get("polls"),
            "iframe_tried": spa.get("iframe_tried"),
            "context": spa.get("context"),
            "navigations": spa.get("navigations"),
        }
        if spa.get("reached"):
            report["form"] = {
                "reached": True,
                "visible_input_count": (spa.get("context") or {}).get(
                    "visible_input_count"
                ),
                "evidence_selectors": [],
                "fill_kind": (spa.get("context") or {}).get("kind"),
                "fill_url": (spa.get("context") or {}).get("url"),
            }
            report["time_to_form_seconds"] = round(time.time() - t0, 2)
            report["fill_target"] = spa.get("fill_target")

    if report["form"] is None:
        form = await form_fields_visible_anywhere(active)
        report["form"] = _form_public(form)
        report["fill_target"] = form.get("fill_target")
    report["final_clicks"] = 0  # gated_click_control never clicks FINAL
    report["page"] = active
    return report


# ---------------------------------------------------------------------------
# Custom widget helper (shared): combobox / listbox → type → click option
# ---------------------------------------------------------------------------


async def fill_custom_widget(page, locator, value: str, *, timeout: int = 4000,
                             field_type: str = "", label: str = "") -> dict:
    """Universal typable dropdown for ARIA combobox/listbox (non-Greenhouse).

    Uses ``fill_typable_dropdown`` word-by-word algorithm for ALL typable selects
    (Location, Yes/No, how-heard, sponsorship, …). Never Enter. Never type-and-hope.
    """
    from verified_select import (
        fill_location_autocomplete,
        fill_typable_dropdown,
        location_option_aliases,
        normalize_select_answer,
        read_combobox_display,
    )
    from gh_select import _score_option

    ftype_u = (field_type or "").upper()
    label_l = (label or "").lower()
    value_n = normalize_select_answer(label, str(value or ""), field_type=field_type)
    cands = aliases_for(field_type, str(value_n)) if field_type else [str(value_n)]
    if value_n and value_n not in cands:
        cands = [value_n, *cands]

    # Places Location (City, Country) — full City/State/Country word split
    if ftype_u in ("ADDRESS_CITY", "LOCATION") or re.search(
        r"\blocation\b|city\s*,\s*country|city\s+and\s+country", label_l
    ):
        aliases = location_option_aliases(
            value_n or "Springfield", state="IL", country="United States"
        )
        for a in cands:
            if a and a not in aliases:
                aliases.append(a)

        async def _zip_probe() -> bool:
            try:
                url = (page.url or "").lower()
            except Exception:
                url = ""
            if "ashbyhq.com" not in url:
                return False
            try:
                z = page.locator(
                    "input[autocomplete='postal-code'], "
                    "input[name*='zip' i], input[placeholder*='zip' i]"
                ).first
                return await z.count() > 0 and await z.is_visible(timeout=250)
            except Exception:
                return False

        detail = await fill_location_autocomplete(
            page,
            locator,
            city=value_n or "Springfield",
            state="IL",
            state_full="Illinois",
            country="United States",
            aliases=aliases,
            commit_probe=_zip_probe,
            timeout_ms=max(timeout, 5000),
        )
        detail.setdefault("mode", "location_autocomplete")
        return detail

    # Resolve nested filter input when control is a wrapper
    filter_input = locator
    try:
        handle = locator.locator("input").first
        if await handle.count() and await handle.is_visible(timeout=300):
            filter_input = handle
    except Exception:
        pass

    detail = await fill_typable_dropdown(
        page,
        control=locator,
        filter_input=filter_input,
        value=value_n,
        aliases=cands,
        read_committed=lambda: read_combobox_display(locator),
        score_fn=_score_option,
        timeout_ms=max(timeout, 4000),
        field_type=field_type,
        label=label,
        option_selectors=[
            "[role='listbox'] [role='option']",
            "[role='option']",
            ".select__option",
        ],
    )
    detail["mode"] = detail.get("mode") or "combobox"
    if detail.get("picked") and not detail.get("option_text"):
        detail["option_text"] = str(detail.get("picked") or "")[:80]
    return detail



def _is_custom_widget(field: dict) -> bool:
    role = (field.get("role") or "").lower()
    ftype = (field.get("type") or field.get("input_type") or "").lower()
    tag = (field.get("tag") or "").lower()
    if role in ("combobox", "listbox", "searchbox"):
        return True
    if ftype in ("search-dropdown", "combobox", "select-one", "listbox"):
        return True
    # Workday / Ashby often expose button-like comboboxes
    if tag == "button" and any(
        k in (field.get("label") or "").lower()
        for k in ("country", "state", "school", "degree", "location", "device")
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# Fill helpers
# ---------------------------------------------------------------------------


from verified_select import value_matches_readback as _value_matches_readback  # noqa: E402


def _is_gh_select_fill_row(f: dict) -> bool:
    """True for gh_select / sweep / reclaim / inpage gh_select fill rows."""
    if not isinstance(f, dict):
        return False
    if str(f.get("mode") or "") == "gh_select":
        return True
    via = str(f.get("via") or "")
    return via == "gh_select" or "gh_select" in via


def should_demote_claimed_text_fill(
    *,
    sel_found: bool,
    live_rb: str,
    intended: str,
    claimed_rb: str = "",
    field_type: str | None = None,
    id_still_empty: bool = False,
) -> bool:
    """True when live DOM proves a claimed verified text fill is empty/wrong.

    Never demote when selector is missing (fragile GH label selectors) or when
    live committed readback still matches the intended dummy value.
    """
    if not sel_found:
        return False
    intended_s = str(intended or "").strip()
    live_s = str(live_rb or "").strip()
    ftype = str(field_type or "").upper()
    if ftype in ("ADDRESS_CITY", "LOCATION") or (
        intended_s and "," in live_s and "springfield" in live_s.lower()
    ):
        try:
            from verified_select import location_display_matches, location_option_aliases

            aliases = location_option_aliases(
                intended_s or "Springfield",
                state="IL",
                state_full="Illinois",
                country="United States",
            )
            if location_display_matches(live_s, aliases, city=intended_s or "Springfield"):
                return False
        except Exception:
            pass
    if intended_s and live_s and not is_empty_ui_value(live_s):
        if _value_matches_readback(intended_s, live_s):
            return False
    claimed_s = str(claimed_rb or "").strip()

    def _claimed_still_valid() -> bool:
        if not claimed_s or is_empty_ui_value(claimed_s):
            return False
        if ftype in ("ADDRESS_CITY", "LOCATION"):
            try:
                from verified_select import (
                    location_display_matches,
                    location_option_aliases,
                )

                aliases = location_option_aliases(
                    intended_s or "Springfield",
                    state="IL",
                    state_full="Illinois",
                    country="United States",
                )
                return location_display_matches(
                    claimed_s,
                    aliases,
                    city=intended_s or "Springfield",
                )
            except Exception:
                return False
        return bool(intended_s and _value_matches_readback(intended_s, claimed_s))

    if is_empty_ui_value(live_s):
        # Live probe empty — demote only when claimed readback also fails to match
        # (SPA wipe). Stale required-empty ids must not override verified readback.
        if _claimed_still_valid():
            return False
        if id_still_empty:
            return True
        return True
    if _claimed_still_valid() or (
        intended_s
        and live_s
        and not is_empty_ui_value(live_s)
        and _value_matches_readback(intended_s, live_s)
    ):
        return False
    if id_still_empty:
        return True
    if field_type == LINKEDIN and is_empty_ui_value(claimed_s):
        return True
    return False


def _record_demotion_failures(
    report: dict | None,
    demoted: list[dict],
    *,
    pass_i: int | None = None,
) -> None:
    """Wire demoted false-verified rows into field_attempt_log (fail counts)."""
    if not report or not demoted:
        return
    log = _attempt_log_from_report(report)
    if log is None:
        return
    for d in demoted:
        if not isinstance(d, dict):
            continue
        rb = str(d.get("readback") or d.get("verified_value") or d.get("stale_readback") or "")
        intended = str(
            d.get("value")
            or d.get("verified_value")
            or d.get("picked")
            or d.get("shown")
            or ""
        )
        if rb and not should_demote_claimed_text_fill(
            sel_found=True,
            live_rb=rb,
            intended=intended,
            claimed_rb=rb,
            field_type=str(d.get("type") or ""),
            id_still_empty=False,
        ):
            continue
        try:
            log.record_from_row(
                {
                    "type": d.get("type"),
                    "label": d.get("label"),
                    "selector": d.get("selector"),
                    "reason": "live_empty_after_claimed_verified",
                    "readback": rb,
                    "via": d.get("via") or "demote_live_probe",
                },
                success=False,
                pass_i=pass_i,
                via_override="demote_live_probe",
                error_override="live_empty_after_claimed_verified",
            )
        except Exception:
            pass


def _demoted_refill_types(
    report: dict, demoted: list[dict] | None = None
) -> set[str]:
    """Types that must be re-filled after live-empty demotion (incl. stale twins)."""
    types: set[str] = set()
    for d in demoted or []:
        t = d.get("type")
        if t:
            types.add(str(t))
    for u in report.get("leftovers") or []:
        if not isinstance(u, dict):
            continue
        if str(u.get("reason") or "") == "live_empty_after_claimed_verified":
            t = u.get("type")
            if t:
                types.add(str(t))
    return types


def _purge_stale_filled_after_demote(report: dict, demoted_types: set[str]) -> int:
    """Drop all filled rows for demoted types (kills stale replay/pack twins)."""
    if not demoted_types:
        return 0
    before = len(report.get("filled") or [])
    report["filled"] = [
        f
        for f in (report.get("filled") or [])
        if isinstance(f, dict) and str(f.get("type") or "") not in demoted_types
    ]
    return before - len(report["filled"])


def _ensure_leftovers_for_demoted_types(
    report: dict, demoted_types: set[str], demoted: list[dict]
) -> None:
    """Guarantee flash_candidates exist for every demoted type after purge."""
    by_type: dict[str, dict] = {}
    for d in demoted:
        t = d.get("type")
        if t:
            by_type[str(t)] = d
    existing: set[str] = set()
    for u in report.get("leftovers") or []:
        if isinstance(u, dict) and u.get("type"):
            existing.add(str(u["type"]))
    for t in demoted_types:
        if t in existing:
            continue
        src = by_type.get(t) or {}
        report.setdefault("leftovers", []).append(
            {
                "label": src.get("label") or t,
                "type": t,
                "selector": src.get("selector"),
                "reason": "live_empty_after_claimed_verified",
                "readback": src.get("readback") or "",
                "stale_readback": (src.get("readback") or "")[:80],
                "verified_value": None,
                "flash_candidate": True,
                "via": src.get("via") or "demote_purge_stale",
            }
        )


def _invalidate_replay_for_demoted(report: dict, demoted: list[dict]) -> int:
    """Drop replay-cache selectors that falsely verified before SPA wipe."""
    url = str(report.get("url") or "")
    platform = str(report.get("platform") or "unknown")
    if not url or not demoted:
        return 0
    try:
        from record_replay import invalidate
    except ImportError:
        return 0
    n = 0
    seen: set[str] = set()
    for d in demoted:
        sel = str(d.get("selector") or "").strip()
        if sel and sel not in seen:
            seen.add(sel)
            try:
                invalidate(url, platform, sel)
                n += 1
            except Exception:
                pass
    return n


def _apply_demote_result(
    report: dict,
    result: dict,
    *,
    pass_i: int | None = None,
) -> None:
    """Purge stale filled twins, invalidate replay, wire attempt-log fails."""
    demoted = result.get("demoted") or []
    if not demoted:
        return
    demoted_types = _demoted_refill_types(report, demoted)
    purged = _purge_stale_filled_after_demote(report, demoted_types)
    _ensure_leftovers_for_demoted_types(report, demoted_types, demoted)
    invalidated = _invalidate_replay_for_demoted(report, demoted)
    _record_demotion_failures(report, demoted, pass_i=pass_i)
    report["demote_side_effects"] = {
        "purged_filled": purged,
        "invalidated_replay_selectors": invalidated,
        "demoted_types": sorted(demoted_types),
    }


def _already_types_skip_refill(report: dict) -> set[str]:
    """Types already filled — minus any demoted leftovers needing reclaim.

    Honors ``already_correct_keep`` / ``already_correct_skip`` so same-page
    refill / continue-before-hold does not re-touch committed widgets.
    Also honors page-session field locks.
    """
    already: set[str] = set()
    for f in report.get("filled") or []:
        if not isinstance(f, dict):
            continue
        ftype = f.get("type")
        if not ftype or f.get("ok") is False:
            continue
        if f.get("verified") is False and not f.get("skipped_already_correct"):
            continue
        already.add(str(ftype))
    try:
        from field_lock import get_field_locks, resolve_lock_report

        sess = get_field_locks(resolve_lock_report(report))
        if sess is not None:
            already |= sess.locked_types()
    except Exception:
        pass
    for t in _demoted_refill_types(report):
        already.discard(t)
    # Leftover types must be re-attempted even if a twin filled row still claims
    # already_correct_skip (Tax Relief salary ghost leftover).
    for u in report.get("leftovers") or []:
        if not isinstance(u, dict):
            continue
        t = u.get("type")
        if t:
            already.discard(str(t))
    return already


async def _read_locator_value(loc) -> str:
    """Best-effort visible value for input/select/combobox.

    Returns ``\"\"`` when the only visible text is a UI placeholder.

    For react-select / ARIA combobox filters, reads **committed** display
    (``.select__single-value`` / chip text) — never uncommitted ``input_value``.
    """
    try:
        from verified_select import is_placeholder_select_value, read_combobox_display

        tag = (await loc.evaluate("el => (el.tagName || '').toLowerCase()"))
        role = ((await loc.get_attribute("role")) or "").lower()
        cls = ((await loc.get_attribute("class")) or "").lower()
        # Committed display for combobox / react-select — filter input is NOT value
        if tag == "input" and (
            role == "combobox"
            or "select__input" in cls
            or "select__" in cls
        ):
            rb = await read_combobox_display(loc)
            if not rb or is_placeholder_select_value(rb) or is_empty_ui_value(rb):
                return ""
            return rb
        if role in ("combobox", "listbox") or tag == "button":
            rb = await read_combobox_display(loc)
            if rb and not is_placeholder_select_value(rb) and not is_empty_ui_value(rb):
                return rb
        raw = ""
        if tag in ("input", "textarea"):
            raw = (await loc.input_value()) or ""
            # If value empty but placeholder is "Type here...", still empty.
            if is_empty_ui_value(raw):
                return ""
            return raw
        if tag == "select":
            raw = await loc.evaluate(
                """el => {
                  const o = el.options && el.selectedIndex >= 0
                    ? el.options[el.selectedIndex] : null;
                  return (o && (o.label || o.text || o.value) || el.value || '').trim();
                }"""
            )
            return "" if is_empty_ui_value(raw) else (raw or "")
        nested = loc.locator("input:not([type='hidden']), textarea").first
        if await nested.count():
            try:
                ntag = (await nested.evaluate("el => (el.tagName || '').toLowerCase()"))
                nrole = ((await nested.get_attribute("role")) or "").lower()
                ncls = ((await nested.get_attribute("class")) or "").lower()
                if ntag == "input" and (
                    nrole == "combobox"
                    or "select__input" in ncls
                    or "select__" in ncls
                ):
                    rb = await read_combobox_display(nested)
                    if rb and not is_placeholder_select_value(rb):
                        return rb
                    return ""
                raw = (await nested.input_value()) or ""
                return "" if is_empty_ui_value(raw) else raw
            except Exception:
                pass
        raw = (await loc.inner_text()).strip()
        return "" if is_empty_ui_value(raw) else raw
    except Exception:
        return ""


# is_verified_fill_row imported from fill_verify (shared with Workday path)


async def _supervise_selector_result(
    page,
    report: dict | None,
    result: dict,
    *,
    before: str = "",
    intent: str = "",
    locator=None,
) -> dict:
    """Action-wise audit after one _fill_selector touch (never raises)."""
    if not report or report.get("_supervisor_skip"):
        return result
    try:
        from fill_contract import commit_fill

        meta = {
            "type": result.get("type") or "",
            "selector": result.get("selector") or "",
            "automation_id": result.get("automation_id") or "",
            "label": result.get("label") or "",
            "mode": result.get("mode") or "",
        }

        async def _noop_fill() -> dict:
            return dict(result)

        fr = await commit_fill(
            page,
            meta,
            intent or str(result.get("value") or ""),
            _noop_fill,
            via=str(result.get("via") or "fill_selector"),
            locator=locator,
            report=report,
            before=before,
        )
        return fr.row
    except Exception as e:
        report.setdefault("errors", []).append({"fill_contract": str(e)[:120]})
    return result


def _automation_id_from_selector(sel: str) -> str:
    """Extract data-automation-id from a CSS selector when present."""
    if not sel:
        return ""
    m = re.search(r"data-automation-id=['\"]([^'\"]+)['\"]", sel, re.I)
    return (m.group(1) if m else "").strip()


async def _fill_selector(
    page,
    sel: str,
    ftype: str,
    value: str,
    *,
    mode: str = "fill",
    report: dict | None = None,
) -> dict:
    """Fill one CSS selector. ok/verified only after read-back (file = action ok)."""
    aid = _automation_id_from_selector(sel)
    out: dict[str, Any] = {"selector": sel, "type": ftype, "mode": mode}
    if aid:
        out["automation_id"] = aid
    # Field lock: skip re-touch of commit-verified fields (no DOM probe)
    try:
        from field_lock import gate_field_action, get_field_locks, resolve_lock_report

        g = gate_field_action(
            report,
            field_type=ftype,
            selector=sel,
            automation_id=aid or None,
            label=str(ftype or "") or None,
        )
        if g and g.get("action") == "lock_skip":
            lock_rb = str(g.get("readback") or "")[:120]
            lock_ok = False
            try:
                from field_done import field_is_done_from_readback

                lock_meta = {"type": ftype or "", "selector": sel, "mode": mode}
                if aid:
                    lock_meta["automation_id"] = aid
                lock_ok = bool(
                    field_is_done_from_readback(lock_rb, lock_meta, str(value or "")).ok
                )
            except Exception:
                lock_ok = False
            if not lock_ok:
                try:
                    from field_lock import unlock_if_not_done

                    unlock_if_not_done(
                        report,
                        field_type=ftype or None,
                        selector=sel,
                        automation_id=aid or None,
                        intent=str(value or "") or None,
                        readback=lock_rb,
                    )
                except Exception:
                    pass
            else:
                sess = get_field_locks(resolve_lock_report(report))
                skip = (
                    sess.lock_skip_result(g, automation_id=aid or None, field_type=ftype)
                    if sess
                    else {
                        "reason": "field_locked_skip",
                        "skipped_locked": True,
                        "skipped_already_correct": True,
                        "ok": True,
                        "verified": True,
                        "readback": g.get("readback"),
                    }
                )
                out.update(skip)
                out["selector"] = sel
                out["type"] = ftype
                out["mode"] = mode
                if aid:
                    out["automation_id"] = aid
                try:
                    note_step(
                        report,
                        action="lock_skip",
                        field_type=ftype,
                        label=str(aid or ftype or sel)[:80],
                        after=str(g.get("readback") or "")[:120],
                        via="field_lock",
                        reason="field_locked_skip",
                        extra={"thrash_retouch": True, "selector": sel[:120]},
                    )
                except Exception:
                    pass
                try:
                    from flight_recorder import note_flight

                    note_flight(
                        report,
                        "gate",
                        action="skip",
                        layer="pack",
                        field_type=ftype,
                        automation_id=aid or None,
                        selector=sel,
                        intent=value,
                        gate_kind="lock_skip",
                        gate_result="skip",
                        gate_reason="field_locked_skip",
                        readback=str(g.get("readback") or "")[:120] or None,
                        extra={"thrash_retouch": True},
                    )
                except Exception:
                    pass
                return out
    except Exception:
        pass
    try:
        await wait_while_paused(page, report)
    except Exception:
        pass
    try:
        note_fill_activity(
            layer="0" if mode in ("pack", "deterministic", "file") else "1",
            action="upload" if (ftype == RESUME_UPLOAD or mode == "file") else (
                "select" if mode == "select" else "fill"
            ),
            label=str(ftype or sel or "")[:80],
            detail=str(mode or "")[:40],
        )
        await push_fill_activity(page)
    except Exception:
        pass
    if ftype == RESUME_UPLOAD or mode == "file":
        try:
            from resume_upload import upload_resume_to_page

            # Prefer pack selector when it resolves; upload_resume_to_page verifies
            # FileList / UI filename (never claim verified on set_input_files alone).
            _rt = os.environ.get("FASTFILL_PACK_TIMING") == "1"
            _rt0 = time.monotonic()
            def _mark(tag: str) -> None:
                if _rt:
                    print(f"[resume_timing] {tag} {time.monotonic() - _rt0:.2f}s", flush=True)
            pdf = resume_pdf_from_values({RESUME_UPLOAD: value} if value else None)
            handles = page.locator("input[type=file]")
            if sel:
                specific = page.locator(sel)
                if await specific.count() > 0:
                    handles = specific
            n = await handles.count()
            _mark(f"after_count n={n}")
            if n == 0:
                # Fall through to broader resume upload helper (chooser / alt sels)
                got = await upload_resume_to_page(
                    page, {RESUME_UPLOAD: str(pdf)}, via="fill_selector_file", report=report
                )
                return {
                    **out,
                    "ok": bool(got.get("ok")),
                    "verified": bool(got.get("verified")),
                    "value": pdf.name,
                    "readback": got.get("readback") or "",
                    "reason": got.get("reason") or got.get("error"),
                    "selector": got.get("selector") or sel,
                    "mode": got.get("mode") or "file",
                }
            # Bound the file-input attach: a hidden / non-actionable
            # input[type=file] (common on Greenhouse styled dropzones) would
            # otherwise burn Playwright's 30s default actionability timeout
            # before we fall through to the working upload_resume_to_page helper.
            try:
                await handles.first.set_input_files(str(pdf), timeout=6000)
                _mark("after_set_input_files")
            except Exception:
                _mark("set_input_files_timeout")
                # Non-actionable input → skip straight to the broader helper.
                got = await upload_resume_to_page(
                    page, {RESUME_UPLOAD: str(pdf)}, via="fill_selector_file", report=report
                )
                return {
                    **out,
                    "ok": bool(got.get("ok")),
                    "verified": bool(got.get("verified")),
                    "value": pdf.name,
                    "readback": got.get("readback") or "",
                    "reason": got.get("reason") or got.get("error") or "set_input_files_timeout",
                    "selector": got.get("selector") or sel,
                    "mode": got.get("mode") or "file",
                }
            try:
                await page.wait_for_timeout(350)
            except Exception:
                pass
            info = await handles.first.evaluate(
                """(el) => {
                  const files = el && el.files;
                  if (!files || files.length < 1)
                    return {ok: false, name: '', count: 0};
                  return {ok: true, name: files[0].name || '', count: files.length};
                }"""
            )
            name = str((info or {}).get("name") or "")
            verified = bool((info or {}).get("ok") and name)
            _mark(f"after_evaluate verified={verified}")
            if not verified:
                # Empty FileList after attach is EXPECTED on Greenhouse styled
                # dropzones (the file is accepted + moved server-side, clearing
                # the input). Re-attaching to the same churned/detached input
                # burned ~30s (Playwright default timeout on the re-resolve, not
                # honoring the per-call timeout). Go straight to the robust
                # helper, which verifies via the uploaded-UI chrome fast.
                got = await upload_resume_to_page(
                    page,
                    {RESUME_UPLOAD: str(pdf)},
                    via="fill_selector_retry",
                    report=report,
                )
                _mark("after_upload_resume_to_page")
                return {
                    **out,
                    "ok": bool(got.get("ok")),
                    "verified": bool(got.get("verified")),
                    "value": pdf.name,
                    "readback": got.get("readback") or name,
                    "reason": got.get("reason") or "resume_unverified",
                    "retried": True,
                }
            return {
                **out,
                "ok": True,
                "verified": True,
                "value": pdf.name,
                "readback": name[:120] or pdf.name,
                "reason": "files_on_input",
            }
        except Exception as e:
            return {**out, "ok": False, "verified": False, "error": str(e)[:200]}

    loc = page.locator(sel).first
    before_rb = ""
    try:
        if await loc.count() == 0:
            return await _supervise_selector_result(
                page,
                report,
                {**out, "ok": False, "verified": False, "reason": "not_in_dom"},
                intent=str(value),
            )
        # Nested input for Workday wrappers
        if mode != "combobox":
            inner = page.locator(f"{sel} input").first
            if await inner.count() and await inner.is_visible(timeout=400):
                loc = inner
        before_rb = await _read_locator_value(loc)

        touch_meta = {
            "type": ftype,
            "selector": sel,
            "mode": mode,
        }
        if aid:
            touch_meta["automation_id"] = aid
        try:
            from fill_contract import verify_before_touch

            touch = await verify_before_touch(
                page, touch_meta, str(value), report=report
            )
            if touch.action == "skip_lock" and touch.row:
                return {**out, **touch.row}
        except Exception:
            pass

        async def _done(result: dict) -> dict:
            return await _supervise_selector_result(
                page,
                report,
                {**out, **result},
                before=before_rb,
                intent=str(value),
                locator=loc,
            )

        if mode == "combobox" or _looks_like_combobox_locator(await _locator_meta(page, loc)):
            detail = await fill_custom_widget(
                page, loc, value, field_type=ftype, label=""
            )
            verified = bool(detail.get("verified"))
            readback = str(
                detail.get("readback")
                or detail.get("option_text")
                or ""
            )
            from verified_select import is_placeholder_select_value

            if verified and is_placeholder_select_value(readback):
                verified = False
            if not verified and not readback:
                # Do not fall back to filter input_value
                readback = ""
            return await _done(
                {
                    "ok": verified,
                    "verified": verified,
                    "value": value,
                    "readback": (str(readback)[:120] if readback else ""),
                    "widget": detail,
                    "reason": None if verified else (
                        detail.get("error") or "combobox_unverified"
                    ),
                }
            )

        tag = (await loc.evaluate("el => (el.tagName || '').toLowerCase()"))
        itype = ((await loc.get_attribute("type")) or "text").lower()
        fiber_meta: dict[str, Any] = {}
        if tag == "select":
            skip_ok, skip_rb = await _locator_already_correct(
                loc, str(value), field_type=str(ftype or "")
            )
            if skip_ok:
                skip_meta = {"type": ftype or "", "selector": sel, "mode": mode}
                if aid:
                    skip_meta["automation_id"] = aid
                try:
                    from field_done import field_is_done_from_readback

                    skip_ok = bool(
                        field_is_done_from_readback(
                            skip_rb, skip_meta, str(value)
                        ).ok
                    )
                except Exception:
                    skip_ok = False
            if skip_ok:
                return await _done(
                    {
                        "ok": True,
                        "verified": True,
                        "value": value,
                        "readback": (skip_rb or "")[:120],
                        "reason": "already_correct_skip",
                        "skipped_already_correct": True,
                    }
                )
            try:
                await loc.select_option(label=value, timeout=3000)
            except Exception:
                await loc.select_option(value=value, timeout=3000)
        elif itype in ("checkbox", "radio"):
            want_on = str(value).lower() in ("yes", "true", "1")
            try:
                already = await loc.is_checked()
            except Exception:
                already = False
            if want_on and already:
                return await _done(
                    {
                        "ok": True,
                        "verified": True,
                        "value": value,
                        "readback": "checked",
                        "reason": "already_correct_skip",
                        "skipped_already_correct": True,
                    }
                )
            if want_on:
                await loc.check(timeout=3000)
            checked = False
            try:
                checked = await loc.is_checked()
            except Exception:
                checked = True
            return await _done(
                {
                    "ok": checked,
                    "verified": checked,
                    "value": value,
                    "readback": "checked" if checked else "",
                }
            )
        else:
            # SKIP thrash: never clear()/fill() when field_is_done agrees
            skip_ok, skip_rb = await _locator_already_correct(
                loc, str(value), field_type=str(ftype or "")
            )
            if skip_ok:
                skip_meta = {"type": ftype or "", "selector": sel, "mode": mode}
                if aid:
                    skip_meta["automation_id"] = aid
                try:
                    from field_done import field_is_done_from_readback

                    skip_ok = bool(
                        field_is_done_from_readback(
                            skip_rb, skip_meta, str(value)
                        ).ok
                    )
                except Exception:
                    skip_ok = False
            if skip_ok:
                return await _done(
                    {
                        "ok": True,
                        "verified": True,
                        "value": value,
                        "readback": (skip_rb or "")[:120],
                        "reason": "already_correct_skip",
                        "skipped_already_correct": True,
                    }
                )
            if itype == "password":
                await loc.fill(str(value), timeout=4000)
            else:
                from verified_select import (
                    fill_text_fiber_then_read,
                    is_stubborn_text_field,
                )

                stubborn = is_stubborn_text_field(
                    automation_id=aid,
                    field_type=str(ftype or ""),
                    selector=sel,
                )
                fiber_meta = await fill_text_fiber_then_read(
                    loc, str(value), stubborn=stubborn, page=page
                )

        readback = await _read_locator_value(loc)
        verified = _value_matches_readback(str(value), readback)
        extra: dict[str, Any] = {}
        if fiber_meta.get("algorithm"):
            extra["algorithm"] = fiber_meta.get("algorithm")
        if fiber_meta.get("fiber_onChange"):
            extra["fiber_onChange"] = True
        if fiber_meta.get("empty_readback_fiber_retry"):
            extra["empty_readback_fiber_retry"] = True
        return await _done(
            {
                "ok": verified,
                "verified": verified,
                "value": value,
                "readback": (readback or "")[:120],
                "reason": None if verified else "readback_empty_or_mismatch",
                **extra,
            }
        )
    except Exception as e:
        return await _supervise_selector_result(
            page,
            report,
            {**out, "ok": False, "verified": False, "error": str(e)[:200]},
            before=before_rb,
            intent=str(value),
        )


async def _locator_meta(page, loc) -> dict:
    try:
        return await loc.evaluate(
            """el => ({
              tag: (el.tagName || '').toLowerCase(),
              role: el.getAttribute('role') || '',
              type: (el.getAttribute('type') || '').toLowerCase(),
              aria_haspopup: el.getAttribute('aria-haspopup') || '',
            })"""
        )
    except Exception:
        return {}


def _looks_like_combobox_locator(meta: dict) -> bool:
    role = (meta.get("role") or "").lower()
    if role in ("combobox", "listbox"):
        return True
    if meta.get("aria_haspopup") in ("listbox", "true", "menu"):
        return True
    return False


def _gh_resume_verified_for_reassert(report: dict | None) -> bool:
    """True only when dummy resume upload is verified (parse-wipe may follow)."""
    if not report:
        return False
    ru = report.get("resume_upload") if isinstance(report.get("resume_upload"), dict) else {}
    if ru.get("verified") is True:
        return True
    result = ru.get("result") if isinstance(ru.get("result"), dict) else {}
    if result.get("verified") is True:
        return True
    if report.get("resume_verified") is True:
        return True
    return report_has_verified_resume(report)


async def _should_run_gh_post_resume_reassert(report: dict) -> bool:
    """Run GH contact reassert only after a verified resume upload (parse wipe)."""
    return _gh_resume_verified_for_reassert(report)


async def reassert_greenhouse_contact_after_resume(page, values: dict) -> list[dict]:
    """Re-fill GH contact/LinkedIn/preferred-name after resume remounts wipe values."""
    rows: list[dict] = []
    first = str(values.get(NAME_FIRST) or "")
    last = str(values.get(NAME_LAST) or "")
    email = str(values.get(EMAIL) or "")
    phone = str(values.get(PHONE) or "")
    linkedin = str(values.get(LINKEDIN) or "")

    for sel, ftype, val in (
        ("#first_name", NAME_FIRST, first),
        ("#last_name", NAME_LAST, last),
        ("#email", EMAIL, email),
        ("#phone", PHONE, phone),
    ):
        if not val:
            continue
        try:
            result = await _fill_selector(page, sel, ftype, val, mode="fill")
            row = {
                "via": "greenhouse_post_resume_reassert",
                "layer": "0.5",
                "label": ftype,
                **result,
            }
            rows.append(row)
        except Exception as e:
            rows.append(
                {
                    "via": "greenhouse_post_resume_reassert",
                    "type": ftype,
                    "ok": False,
                    "verified": False,
                    "reason": str(e)[:120],
                    "flash_candidate": True,
                }
            )

    # Preferred First Name (second NAME_FIRST) — common GH board field
    if first:
        try:
            lab = page.locator("label").filter(
                has_text=re.compile(r"preferred\s+first\s+name", re.I)
            ).first
            if await lab.count() > 0:
                box = lab.locator(
                    "xpath=following::input[not(@type='hidden')][1]"
                ).first
                if await box.count() == 0:
                    box = page.locator(
                        "label:has-text('Preferred First Name') input"
                    ).first
                if await box.count() > 0:
                    skip_ok, skip_rb = await _locator_already_correct(box, first)
                    if skip_ok:
                        rows.append(
                            {
                                "via": "greenhouse_post_resume_reassert",
                                "layer": "0.5",
                                "label": "Preferred First Name",
                                "type": NAME_FIRST,
                                "selector": "Preferred First Name",
                                "ok": True,
                                "verified": True,
                                "value": first,
                                "readback": (skip_rb or "")[:80],
                                "mode": "fill",
                                "reason": "already_correct_skip",
                                "skipped_already_correct": True,
                            }
                        )
                    else:
                        await box.fill(first, timeout=3000)
                        rb = await _read_locator_value(box)
                        rows.append(
                            {
                                "via": "greenhouse_post_resume_reassert",
                                "layer": "0.5",
                                "label": "Preferred First Name",
                                "type": NAME_FIRST,
                                "selector": "Preferred First Name",
                                "ok": bool(rb) and not is_empty_ui_value(rb),
                                "verified": bool(rb) and not is_empty_ui_value(rb),
                                "value": first,
                                "readback": (rb or "")[:80],
                                "mode": "fill",
                            }
                        )
        except Exception as e:
            rows.append(
                {
                    "via": "greenhouse_post_resume_reassert",
                    "type": NAME_FIRST,
                    "label": "Preferred First Name",
                    "ok": False,
                    "verified": False,
                    "reason": str(e)[:120],
                    "flash_candidate": True,
                }
            )

    if linkedin:
        filled_ok = False
        try:
            js_rb = await page.evaluate(
                """(url) => {
                  const getVal = (el) => (el && (el.value || '').trim()) || '';
                  const setVal = (el, v) => {
                    if (!el) return false;
                    // SKIP thrash: already correct — do not rewrite
                    if (getVal(el) && (
                      getVal(el).toLowerCase() === String(v).toLowerCase()
                      || getVal(el).toLowerCase().includes('linkedin')
                         && String(v).toLowerCase().includes(
                              getVal(el).toLowerCase().slice(0, 24)
                            )
                      || String(v).toLowerCase().includes(getVal(el).toLowerCase())
                    )) {
                      return getVal(el);
                    }
                    el.focus();
                    // React controlled inputs ignore a plain `el.value = v`
                    // (state re-render reverts it to empty — Dragos GH LinkedIn
                    // custom question). Use the native value setter so React's
                    // onChange registers the value and it persists.
                    const proto = window.HTMLInputElement
                      && window.HTMLInputElement.prototype;
                    const desc = proto && Object.getOwnPropertyDescriptor(proto, 'value');
                    if (desc && desc.set) { desc.set.call(el, v); }
                    else { el.value = v; }
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return (el.value || '').trim().length > 0 ? el.value : '';
                  };
                  for (const lab of document.querySelectorAll('label')) {
                    if (!/linked\\s*in/i.test(lab.innerText || '')) continue;
                    const forId = lab.getAttribute('for');
                    if (forId) {
                      const el = document.getElementById(forId);
                      const got = setVal(el, url);
                      if (got) return got;
                    }
                    const root = lab.closest(
                      '.field, .form-field, [class*=\"field\"]'
                    ) || lab.parentElement;
                    const inp = root && root.querySelector(
                      'input:not([type=hidden]):not([type=file])'
                    );
                    const got = setVal(inp, url);
                    if (got) return got;
                  }
                  for (const inp of document.querySelectorAll('input')) {
                    const blob = [inp.name||'', inp.id||'', inp.placeholder||'',
                      inp.getAttribute('aria-label')||''].join(' ');
                    if (/linked\\s*in/i.test(blob)) {
                      const got = setVal(inp, url);
                      if (got) return got;
                    }
                  }
                  return '';
                }""",
                linkedin,
            )
            if js_rb and "linkedin" in str(js_rb).lower():
                rows.append(
                    {
                        "via": "greenhouse_post_resume_reassert",
                        "layer": "0.5",
                        "label": "LinkedIn Profile",
                        "type": LINKEDIN,
                        "selector": "js:linkedin_label_input",
                        "ok": True,
                        "verified": True,
                        "value": linkedin,
                        "readback": str(js_rb)[:120],
                        "mode": "fill",
                    }
                )
                filled_ok = True
        except Exception:
            filled_ok = False
        if not filled_ok:
            for sel in (
                "input[name*='linkedin' i]",
                "input[id*='linkedin' i]",
                "input[placeholder*='LinkedIn' i]",
                "input[aria-label*='LinkedIn' i]",
                "label:has-text('LinkedIn') input",
            ):
                try:
                    loc = page.locator(sel).first
                    if await loc.count() == 0:
                        continue
                    skip_ok, skip_rb = await _locator_already_correct(loc, linkedin)
                    if skip_ok and "linkedin" in (skip_rb or "").lower():
                        rows.append(
                            {
                                "via": "greenhouse_post_resume_reassert",
                                "layer": "0.5",
                                "label": "LinkedIn Profile",
                                "type": LINKEDIN,
                                "selector": sel,
                                "ok": True,
                                "verified": True,
                                "value": linkedin,
                                "readback": (skip_rb or "")[:120],
                                "mode": "fill",
                                "reason": "already_correct_skip",
                                "skipped_already_correct": True,
                            }
                        )
                        break
                    await loc.fill(linkedin, timeout=3000)
                    rb = await _read_locator_value(loc)
                    ok = bool(rb) and "linkedin" in rb.lower()
                    rows.append(
                        {
                            "via": "greenhouse_post_resume_reassert",
                            "layer": "0.5",
                            "label": "LinkedIn Profile",
                            "type": LINKEDIN,
                            "selector": sel,
                            "ok": ok,
                            "verified": ok,
                            "value": linkedin,
                            "readback": (rb or "")[:120],
                            "mode": "fill",
                        }
                    )
                    if ok:
                        break
                except Exception:
                    continue

    # Education years / location / notice — often wiped after resume remount
    from gh_select import aliases_for, fill_gh_select

    for label_pat, ftype, val, via_label in (
        (r"start\s+date\s+year", EDUCATION_START_YEAR, values.get(EDUCATION_START_YEAR), "Start date year*"),
        (r"end\s+date\s+year", EDUCATION_END_YEAR, values.get(EDUCATION_END_YEAR), "End date year*"),
        (r"where\s+do\s+you\s+currently\s+reside", LOCATION, values.get(LOCATION), "Where do you currently reside?"),
        (r"available\s+start\s+date", NOTICE_PERIOD, values.get(NOTICE_PERIOD), "What is your available start date?*"),
        # Classic "will you require sponsorship?" (opposite Yes/No from WORK_AUTH)
        # Extend: "require immigration sponsorship … to attain work authorization"
        (
            r"will\s+you\s+.*require\s+(immigration\s+)?sponsorship|"
            r"require\s+(immigration\s+)?sponsorship|"
            r"require\s+sponsorship\s+for\s+employment\s+visa|"
            r"sponsorship\s+for\s+employment\s+visa\s+status|"
            r"immigration\s+sponsorship",
            SPONSORSHIP,
            values.get(SPONSORSHIP) or "No",
            "Will you now or in the future require sponsorship",
        ),
        # ALL work-auth selects — legal auth AND "without need for visa
        # sponsorship, now or in the future" (multi-instance; .first is wrong).
        # Do NOT match "require immigration sponsorship … work authorization"
        # (that is SPONSORSHIP=No — Extend GH thrash).
        (
            r"without\s+(the\s+)?need\s+for\s+(visa\s+)?sponsorship|"
            r"legally\s+authorized\s+to\s+work|"
            r"authorized\s+to\s+work\s+in\s+the\s+(us|u\.s\.|united)|"
            r"(?:^|\b)are\s+you\s+(?:legally\s+)?authorized\s+to\s+work",
            WORK_AUTH,
            values.get(WORK_AUTH) or "Yes",
            "Are you legally authorized to work",
        ),
    ):
        if not val:
            continue
        try:
            labs = page.locator("label").filter(has_text=re.compile(label_pat, re.I))
            n_labs = await labs.count()
            if n_labs == 0:
                continue
            for i in range(n_labs):
                lab = labs.nth(i)
                lab_text = (await lab.inner_text()).replace("\n", " ").strip()[:160]
                # Never treat sponsorship questions as WORK_AUTH (Extend: ends with
                # "work authorization" but polarity is opposite).
                if ftype == WORK_AUTH and re.search(
                    r"require\s+(immigration\s+)?sponsorship|immigration\s+sponsorship",
                    lab_text,
                    re.I,
                ):
                    continue
                # Prefer react-select when present
                container = page.locator(".select__container").filter(has=lab).first
                if await container.count() == 0:
                    # Sibling shell (label outside container)
                    container = lab.locator(
                        "xpath=ancestor::div[contains(@class,'select__container') "
                        "or contains(@class,'select-shell') or contains(@class,'field')][1]"
                    ).first
                if await container.count() > 0 and await container.locator(
                    ".select__control"
                ).count() > 0:
                    # Wrong-polarity sponsorship: force refill even if non-blank
                    force = False
                    sv = container.locator(".select__single-value").first
                    if await sv.count() > 0:
                        shown0 = (await sv.inner_text()).strip()
                        if (
                            ftype == SPONSORSHIP
                            and shown0
                            and re.search(
                                r"\b(will|do|would)\s+require\b", shown0, re.I
                            )
                            and not re.search(
                                r"will\s+not|do\s+not|don'?t", shown0, re.I
                            )
                        ):
                            force = True
                    # fill_gh_select skips when already matching aliases (no thrash)
                    result = await fill_gh_select(
                        page,
                        lab_text or via_label,
                        str(val),
                        field_type=ftype,
                        aliases=aliases_for(ftype, str(val)),
                    )
                    if force and result.get("skipped_already_correct"):
                        # shown matched a cand but polarity check said wrong —
                        # rare; leave as leftover for sweep
                        result = {
                            **result,
                            "ok": False,
                            "skipped_already_correct": False,
                            "error": "sponsorship_wrong_polarity",
                        }
                    rows.append(
                        {
                            "via": "greenhouse_post_resume_reassert",
                            "layer": "0.5",
                            "label": (lab_text or via_label)[:80],
                            "type": ftype,
                            "ok": bool(result.get("ok")),
                            "verified": bool(result.get("ok")),
                            "value": str(val),
                            "readback": str(
                                result.get("shown") or result.get("picked") or ""
                            )[:80],
                            "mode": "gh_select",
                            "skipped_already_correct": bool(
                                result.get("skipped_already_correct")
                            ),
                            "reason": (
                                "already_correct_skip"
                                if result.get("skipped_already_correct")
                                else result.get("error")
                            ),
                        }
                    )
                    continue
                box = lab.locator(
                    "xpath=following::input[not(@type='hidden')][1]"
                ).first
                if await box.count() == 0:
                    continue
                # Never type into a react-select filter as if it were a text box
                try:
                    cls = ((await box.get_attribute("class")) or "").lower()
                    role = ((await box.get_attribute("role")) or "").lower()
                    near_select = await box.evaluate(
                        """el => !!(el.closest('.select__container,.select-shell')
                          || (el.className||'').includes('select__'))"""
                    )
                except Exception:
                    cls, role, near_select = "", "", False
                if (
                    "select__" in cls
                    or role == "combobox"
                    or near_select
                    or ftype in (SPONSORSHIP, WORK_AUTH, LOCATION)
                ):
                    result = await fill_gh_select(
                        page,
                        lab_text or via_label,
                        str(val),
                        field_type=ftype,
                        aliases=aliases_for(ftype, str(val)),
                    )
                    rows.append(
                        {
                            "via": "greenhouse_post_resume_reassert",
                            "layer": "0.5",
                            "label": (lab_text or via_label)[:80],
                            "type": ftype,
                            "ok": bool(result.get("ok")),
                            "verified": bool(
                                result.get("verified", result.get("ok"))
                            ),
                            "value": str(val),
                            "readback": str(
                                result.get("shown") or result.get("picked") or ""
                            )[:80],
                            "mode": "gh_select",
                            "reason": result.get("error"),
                        }
                    )
                    continue
                skip_ok, skip_rb = await _locator_already_correct(box, str(val))
                if skip_ok:
                    rows.append(
                        {
                            "via": "greenhouse_post_resume_reassert",
                            "layer": "0.5",
                            "label": (lab_text or via_label)[:80],
                            "type": ftype,
                            "ok": True,
                            "verified": True,
                            "value": str(val),
                            "readback": (skip_rb or "")[:80],
                            "mode": "fill",
                            "reason": "already_correct_skip",
                            "skipped_already_correct": True,
                        }
                    )
                    continue
                await box.fill(str(val), timeout=3000)
                rb = await _read_locator_value(box)
                ok = bool(rb) and not is_empty_ui_value(rb)
                rows.append(
                    {
                        "via": "greenhouse_post_resume_reassert",
                        "layer": "0.5",
                        "label": (lab_text or via_label)[:80],
                        "type": ftype,
                        "ok": ok,
                        "verified": ok,
                        "value": str(val),
                        "readback": (rb or "")[:80],
                        "mode": "fill",
                    }
                )
        except Exception as e:
            rows.append(
                {
                    "via": "greenhouse_post_resume_reassert",
                    "type": ftype,
                    "label": via_label,
                    "ok": False,
                    "verified": False,
                    "reason": str(e)[:120],
                    "flash_candidate": True,
                }
            )

    # SCHOOL + SALARY react-select — only when present on page (Extend has neither)
    for via_label, ftype, val in (
        ("School*", SCHOOL, values.get(SCHOOL)),
        ("What is your desired salary?*", SALARY_EXPECTED, values.get(SALARY_EXPECTED)),
    ):
        if not val and ftype != SALARY_EXPECTED:
            continue
        # Salary may be empty string in profile — still try aliases (negotiable → $ band)
        try_val = str(val or "Open / negotiable within the posted range")
        try:
            result = await fill_gh_select(
                page,
                via_label,
                try_val,
                field_type=ftype,
                aliases=aliases_for(ftype, try_val),
            )
            if result.get("field_absent") or (
                not result.get("ok")
                and "label not found" in str(result.get("error") or "")
            ):
                # Field not on this tenant — silent skip (not leftover / not filled)
                continue
            rows.append(
                {
                    "via": "greenhouse_post_resume_reassert",
                    "layer": "0.5",
                    "label": via_label,
                    "type": ftype,
                    "ok": bool(result.get("ok")),
                    "verified": bool(result.get("ok")),
                    "value": try_val,
                    "readback": str(result.get("shown") or result.get("picked") or "")[:80],
                    "mode": "gh_select",
                    "error": None if result.get("ok") else result.get("error"),
                    "flash_candidate": not bool(result.get("ok")),
                    "skipped_already_correct": bool(
                        result.get("skipped_already_correct")
                    ),
                    "reason": (
                        "already_correct_skip"
                        if result.get("skipped_already_correct")
                        else (None if result.get("ok") else result.get("error"))
                    ),
                }
            )
        except Exception as e:
            rows.append(
                {
                    "via": "greenhouse_post_resume_reassert",
                    "type": ftype,
                    "label": via_label,
                    "ok": False,
                    "verified": False,
                    "reason": str(e)[:120],
                    "flash_candidate": True,
                }
            )
    return rows


async def _reassert_hispanic_if_race_clobbered(
    page, values: dict, report: dict | None = None
) -> bool:
    """Heal the GH cascading-ethnicity clobber.

    On some Greenhouse tenants "Please identify your race" is a dependent
    sub-select revealed by answering "Are you Hispanic/Latino?"=No; committing
    a race value collapses it and resets Hispanic to a Decline value. When we
    detect Hispanic now showing a Decline while policy wants a concrete answer
    (e.g. "No"), re-assert Hispanic (the required answer) and return True. The
    dependent race select is intentionally left empty — the stable state.
    """
    try:
        from gh_select import (
            _resolve_gh_select_container,
            aliases_for,
            fill_gh_select,
            is_decline_like_alias,
        )
        from verified_select import read_gh_select_display
    except Exception:
        return False
    intended = str((values or {}).get(HISPANIC) or "").strip()
    # Only heal toward a concrete policy answer — never re-assert a Decline.
    if not intended or is_decline_like_alias(intended):
        return False
    hisp_label = ""
    try:
        labs = page.locator("label").filter(
            has_text=re.compile(r"hispanic|latino", re.I)
        )
        if await labs.count():
            hisp_label = (
                await labs.first.inner_text()
            ).replace("\n", " ").strip()[:160]
    except Exception:
        hisp_label = ""
    if not hisp_label:
        return False
    live = ""
    try:
        _l, cont, _c, err = await _resolve_gh_select_container(page, hisp_label)
        if not err and cont is not None and await cont.count():
            live = (await read_gh_select_display(cont) or "").strip()
    except Exception:
        live = ""
    # No clobber to heal when Hispanic already shows a concrete (non-Decline) answer.
    if live and not is_decline_like_alias(live):
        return False
    try:
        res = await fill_gh_select(
            page,
            hisp_label,
            intended,
            field_type=HISPANIC,
            aliases=aliases_for(HISPANIC, intended),
        )
    except Exception:
        return False
    healed = bool(res.get("ok"))
    if healed and report is not None:
        report.setdefault("coupled_ethnicity_heals", []).append(
            {
                "label": hisp_label[:80],
                "reasserted": intended,
                "was": live[:60],
                "shown": str(res.get("shown") or res.get("picked") or "")[:60],
            }
        )
    return healed


async def sweep_gh_unfilled_selects(page, values: dict, report: dict | None = None) -> list[dict]:
    """Fill Greenhouse react-selects still showing Select… for known policy types.

    Broad class fix (not URL one-off): after extract/reassert, any
    `.select__placeholder` / empty single-value for SPONSORSHIP, WORK_AUTH,
    BACKGROUND_CHECK, etc. gets one gh_select attempt. Tax Relief r1 vision:
    visa sponsorship still Select… while WORK_AUTH Yes was filled.
    """
    from field_map import BACKGROUND_CHECK

    rows: list[dict] = []
    try:
        blanks = await page.evaluate(
            """() => {
              const out = [];
              const containers = Array.from(
                document.querySelectorAll('.select__container, .select-shell')
              );
              for (const c of containers) {
                const ph = c.querySelector('.select__placeholder');
                const sv = c.querySelector('.select__single-value');
                const shown = ((sv && sv.textContent) || '').trim();
                const placeholder = ((ph && ph.textContent) || '').trim();
                const empty = !shown || /^select/i.test(shown)
                  || (!shown && /^select/i.test(placeholder))
                  || (ph && !sv);
                if (!empty && shown) continue;
                if (!empty) continue;
                let lab = '';
                const labEl = c.querySelector('label.select__label, label');
                if (labEl) lab = (labEl.innerText || '').replace(/\\s+/g, ' ').trim();
                if (!lab) {
                  const root = c.closest('.field, .form-field, [class*="field"]')
                    || c.parentElement;
                  const lab2 = root && root.querySelector('label');
                  if (lab2) lab = (lab2.innerText || '').replace(/\\s+/g, ' ').trim();
                }
                if (!lab) {
                  let n = c.previousElementSibling;
                  for (let i = 0; i < 4 && n; i++, n = n.previousElementSibling) {
                    if ((n.tagName || '').toLowerCase() === 'label') {
                      lab = (n.innerText || '').replace(/\\s+/g, ' ').trim();
                      break;
                    }
                  }
                }
                if (!lab) continue;
                out.push({label: lab.slice(0, 160), placeholder: placeholder.slice(0, 40)});
              }
              return out;
            }"""
        )
    except Exception as e:
        if report is not None:
            report.setdefault("errors", []).append({"gh_select_sweep": str(e)[:120]})
        return rows

    priority = (
        SPONSORSHIP,
        WORK_AUTH,
        BACKGROUND_CHECK,
        MARKETING_CONSENT,
        "AGE_18",
        "RELOCATION",
        "COMMUTE",
        "WORKED_HERE_BEFORE",
        "FELONY",
        "US_RESIDENCE",
    )
    already_labels = {
        re.sub(r"\s+", " ", str(f.get("label") or "").lower())[:50]
        for f in ((report or {}).get("filled") or [])
        if isinstance(f, dict) and f.get("verified")
    }
    already_types = {
        f.get("type")
        for f in ((report or {}).get("filled") or [])
        if isinstance(f, dict) and f.get("type") and f.get("verified")
    }

    for blank in blanks or []:
        label = str(blank.get("label") or "")
        if not label:
            continue
        lab_key = re.sub(r"\s+", " ", label.lower())[:50]
        if lab_key in already_labels:
            continue
        fake = {
            "label": label,
            "name": "",
            "id": "",
            "type": "select-one",
            "placeholder": "",
            "aria_label": "",
            "autocomplete": "",
        }
        ftype, _layer = classify_field(fake)
        if not ftype or ftype not in _GH_SELECT_FIELD_TYPES:
            low = label.lower()
            if re.search(r"sponsor|employment\s+visa|visa\s+status", low):
                ftype = SPONSORSHIP
            elif re.search(r"authorized\s+to\s+work|work\s+authorization", low):
                ftype = WORK_AUTH
            elif re.search(r"background\s+check", low):
                ftype = BACKGROUND_CHECK
            elif re.search(
                r"marketing|promotional|sms|text[\s_-]*message|newsletter|"
                r"talent[\s_-]*community|opt[\s_-]*in",
                low,
            ):
                ftype = MARKETING_CONSENT
            else:
                continue
        # Coupled cascading-ethnicity widget already healed once: never re-fill
        # the dependent race sub-select (it reappears empty after each heal and
        # re-filling it would just re-clobber Hispanic).
        if ftype == RACE and (report or {}).get("coupled_ethnicity_heals"):
            continue
        # Always retry SPONSORSHIP/WORK_AUTH/MARKETING/GENDER if still showing Select…
        # Capco: two Gender widgets — filling one must not skip the sibling blank.
        if ftype in already_types and ftype not in (
            SPONSORSHIP,
            WORK_AUTH,
            BACKGROUND_CHECK,
            MARKETING_CONSENT,
            GENDER,
            EMPLOYEE_REFERRAL,
            ACCOMMODATIONS,
        ):
            continue
        val = values.get(ftype) if ftype else None
        if not (val or "").strip() and ftype in (
            SPONSORSHIP,
            WORK_AUTH,
            BACKGROUND_CHECK,
            MARKETING_CONSENT,
            EMPLOYEE_REFERRAL,
            ACCOMMODATIONS,
        ):
            val = {
                SPONSORSHIP: "No",
                WORK_AUTH: "Yes",
                BACKGROUND_CHECK: "Yes",
                MARKETING_CONSENT: "No",
                EMPLOYEE_REFERRAL: "No",
                ACCOMMODATIONS: "No",
            }.get(ftype, "")
        if not (val or "").strip():
            continue
        select_val = str(val)
        select_aliases = aliases_for(ftype, select_val)
        if ftype in (ADDRESS_CITY, LOCATION):
            select_val, select_aliases = _gh_city_aliases(values, str(val))
        try:
            result = await fill_gh_select(
                page,
                label,
                select_val,
                field_type=ftype,
                aliases=select_aliases,
            )
        except Exception as e:
            rows.append(
                {
                    "via": "gh_select_sweep",
                    "type": ftype,
                    "label": label[:80],
                    "ok": False,
                    "verified": False,
                    "reason": str(e)[:120],
                    "flash_candidate": True,
                }
            )
            continue
        ok = bool(result.get("ok"))
        shown = str(result.get("shown") or result.get("picked") or "")
        row = {
            "via": "gh_select_sweep",
            "layer": "0.5",
            "label": label[:80],
            "type": ftype,
            "ok": ok,
            "verified": ok and not re.match(r"^select", shown, re.I),
            "value": str(val),
            "readback": shown[:120],
            "picked": str(result.get("picked") or "")[:80],
            "mode": "gh_select",
        }
        if not ok:
            row["reason"] = result.get("error") or "gh_select_failed"
            row["options"] = (result.get("options") or [])[:8]
            row["flash_candidate"] = True
        # Cascading-ethnicity guard: some GH tenants render "Please identify your
        # race" as a dependent sub-select revealed by answering "Are you
        # Hispanic/Latino?"=No. Committing a race value COLLAPSES it and resets
        # Hispanic to "Decline To Self Identify" — clobbering the required
        # answer. Detect that clobber, re-assert Hispanic=No (which the user
        # explicitly requires), and drop the coupled race fill instead of
        # looping (Hispanic demote → refill → race sweep → clobber → …).
        if ftype == RACE and row.get("verified"):
            healed = await _reassert_hispanic_if_race_clobbered(
                page, values, report
            )
            if healed:
                # Coupled widget: drop the race sub-fill entirely (voluntary,
                # left empty) — Hispanic=No has been re-asserted as the answer.
                continue
        rows.append(row)
        if row.get("verified"):
            already_labels.add(lab_key)
            already_types.add(ftype)
            try:
                from field_lock import lock_verified_field

                lock_verified_field(
                    report,
                    row,
                    field_type=ftype,
                    label=label,
                    readback=shown[:120],
                    via="gh_select_sweep",
                )
            except Exception:
                pass

    rows.sort(
        key=lambda r: (
            priority.index(r["type"]) if r.get("type") in priority else 99
        )
    )

    # Force-attempt by label even when placeholder scrape missed the container
    # (Tax Relief: "without need for visa sponsorship…" stayed Select… after a
    # different WORK_AUTH select was filled — multi-instance, not type-once).
    force_specs = (
        (
            r"will\s+you\s+.*require\s+(immigration\s+)?sponsorship|"
            r"require\s+sponsorship\s+for\s+employment\s+visa|"
            r"immigration\s+sponsorship|"
            r"sponsorship\s+for\s+employment\s+visa\s+status",
            SPONSORSHIP,
            values.get(SPONSORSHIP) or "No",
            "Will you now or in the future require sponsorship",
        ),
        (
            r"without\s+(the\s+)?need\s+for\s+(visa\s+)?sponsorship|"
            r"authorized\s+to\s+work\s+.*without\s+.*sponsorship",
            WORK_AUTH,
            values.get(WORK_AUTH) or "Yes",
            "authorized without need for visa sponsorship",
        ),
        (
            r"legally\s+authorized\s+to\s+work|"
            r"authorized\s+to\s+work\s+in\s+the\s+(us|u\.s\.|united)",
            WORK_AUTH,
            values.get(WORK_AUTH) or "Yes",
            "Are you legally authorized to work",
        ),
        (
            r"marketing|promotional|sms|text[\s_-]*message|newsletter|"
            r"talent[\s_-]*community|opt[\s_-]*in|receive[\s_-]*information",
            MARKETING_CONSENT,
            values.get(MARKETING_CONSENT) or "No",
            "marketing consent",
        ),
        (
            # Extend GH: Yes/No select (Illinois is in the listed states → Yes).
            # Never pass free-text / LLM essays — that types into the filter only.
            r"based\s+in\s+any\s+of\s+these\s+states|"
            r"currently\s+based\s+in\s+any",
            LOCATION,
            "Yes",
            "based in any of these states",
        ),
        (
            r"city\s+and\s+state\s+are\s+you\s+currently\s+living|"
            r"what\s+city\s+and\s+state",
            LOCATION,
            values.get(LOCATION) or "Springfield, IL",
            "What city and state are you currently living in",
        ),
    )
    verified_label_keys = {
        re.sub(r"\s+", " ", str(r.get("label") or "").lower())[:50]
        for r in rows
        if r.get("verified")
    }
    for lab_pat, ftype, val, via_label in force_specs:
        try:
            labs = page.locator("label").filter(has_text=re.compile(lab_pat, re.I))
            n_labs = await labs.count()
            if n_labs == 0:
                continue
            for i in range(n_labs):
                lab = labs.nth(i)
                lab_text = (await lab.inner_text()).replace("\n", " ").strip()[:160]
                lab_key = re.sub(r"\s+", " ", lab_text.lower())[:50]
                if lab_key in verified_label_keys:
                    continue
                # Skip if single-value already set (not Select…)
                container = page.locator(".select__container").filter(has=lab).first
                if await container.count() > 0:
                    sv = container.locator(".select__single-value").first
                    if await sv.count() > 0:
                        shown0 = (await sv.inner_text()).strip()
                        if shown0 and not re.match(r"^select", shown0, re.I):
                            if ftype == SPONSORSHIP and "require" in shown0.lower() and not re.search(
                                r"will\s+not|do\s+not|don'?t", shown0, re.I
                            ):
                                pass  # wrong polarity — refill
                            else:
                                verified_label_keys.add(lab_key)
                                continue
                result = await fill_gh_select(
                    page,
                    lab_text or via_label,
                    str(val),
                    field_type=ftype,
                    aliases=aliases_for(ftype, str(val)),
                )
                ok = bool(result.get("ok"))
                shown = str(result.get("shown") or result.get("picked") or "")
                row = {
                    "via": "gh_select_sweep_force",
                    "layer": "0.5",
                    "label": (lab_text or via_label)[:80],
                    "type": ftype,
                    "ok": ok,
                    "verified": ok and not re.match(r"^select", shown, re.I),
                    "value": str(val),
                    "readback": shown[:120],
                    "picked": str(result.get("picked") or "")[:80],
                    "mode": "gh_select",
                    "reason": None if ok else (result.get("error") or "gh_select_failed"),
                    "options": (result.get("options") or [])[:8] if not ok else None,
                    "flash_candidate": not ok,
                }
                rows.append(row)
                if row.get("verified"):
                    verified_label_keys.add(lab_key)
        except Exception as e:
            rows.append(
                {
                    "via": "gh_select_sweep_force",
                    "type": ftype,
                    "label": via_label,
                    "ok": False,
                    "verified": False,
                    "reason": str(e)[:120],
                    "flash_candidate": True,
                }
            )

    return rows


def _merge_greenhouse_reassert_rows(
    report: dict, already: set, rows: list[dict] | None
) -> None:
    """Fold GH post-resume reassert into filled; record attempts."""
    for f in rows or []:
        if not isinstance(f, dict):
            continue
        ftype = f.get("type")
        label = f.get("label") or ftype
        ok = bool(f.get("ok") and f.get("verified"))
        _record_fill_attempt(
            report,
            f,
            success=ok,
            via_override=f.get("via") or "greenhouse_reassert",
        )
        if ok and ftype:
            # Preferred First Name shares NAME_FIRST — keep both pack + preferred
            if str(label).lower().startswith("preferred"):
                report["filled"] = [
                    r
                    for r in (report.get("filled") or [])
                    if not (
                        isinstance(r, dict)
                        and r.get("type") == NAME_FIRST
                        and str(r.get("label") or "").lower().startswith("preferred")
                    )
                ]
                report["filled"].append(f)
            elif ftype in (
                WORK_AUTH,
                SPONSORSHIP,
                WORKED_HERE_BEFORE,
                "BACKGROUND_CHECK",
                "AGE_18",
                "COMMUTE",
                INTEREST,
                NOTICE_PERIOD,
                RELOCATION,
                HOW_HEARD,
                MARKETING_CONSENT,
                TERMS_CONSENT,
            ):
                # Multi-instance types: replace only same label, not whole type
                # (Tax Relief: legal auth + without-sponsorship are both WORK_AUTH).
                lab_key = re.sub(r"\s+", " ", str(label).lower())[:40]
                report["filled"] = [
                    r
                    for r in (report.get("filled") or [])
                    if not (
                        isinstance(r, dict)
                        and r.get("type") == ftype
                        and re.sub(r"\s+", " ", str(r.get("label") or "").lower())[:40]
                        == lab_key
                    )
                ]
                report["filled"].append(f)
            else:
                report["filled"] = [
                    r
                    for r in (report.get("filled") or [])
                    if not (
                        isinstance(r, dict)
                        and r.get("type") == ftype
                        and not str(r.get("label") or "").lower().startswith("preferred")
                    )
                ]
                report["filled"].append(f)
            already.add(ftype)
            report["leftovers"] = [
                u
                for u in (report.get("leftovers") or [])
                if not (
                    isinstance(u, dict)
                    and u.get("type") == ftype
                    and (
                        ftype
                        not in (
                            WORK_AUTH,
                            SPONSORSHIP,
                            WORKED_HERE_BEFORE,
                        )
                        or re.sub(r"\s+", " ", str(u.get("label") or "").lower())[:30]
                        in re.sub(r"\s+", " ", str(label).lower())
                        or re.sub(r"\s+", " ", str(label).lower())[:30]
                        in re.sub(r"\s+", " ", str(u.get("label") or "").lower())
                    )
                )
            ]
        elif f.get("flash_candidate") or f.get("ok") is False:
            report.setdefault("leftovers", []).append(
                {
                    "label": label,
                    "type": ftype,
                    "reason": f.get("reason") or "gh_reassert_failed",
                    "flash_candidate": True,
                }
            )


_MIDTIER_COMBO_PLATFORMS = frozenset(
    {
        "smartrecruiters",
        "workable",
        "bamboohr",
        "recruitee",
        "rippling",
        "dayforce",
        "applytojob",
        "oracle",
        "personio",
        "jobvite",
        "taleo",
        "successfactors",
        "ukg",
        "breezy",
        "jobscore",
        "gem",
        "dover",
        "phenom",
    }
)

_MIDTIER_POLICY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        MARKETING_CONSENT,
        re.compile(
            r"marketing|promotional|sms|text[\s_-]*message|newsletter|"
            r"talent[\s_-]*community|opt[\s_-]*in|receive[\s_-]*information|"
            r"recruiting[\s_-]*events?",
            re.I,
        ),
    ),
    (
        WORK_AUTH,
        re.compile(
            r"authorized\s+to\s+work|work\s+authorization|legally\s+authorized",
            re.I,
        ),
    ),
    (
        SPONSORSHIP,
        re.compile(
            r"require\s+sponsorship|visa\s+sponsor|immigration\s+sponsorship",
            re.I,
        ),
    ),
]


async def sweep_midtier_policy_comboboxes(
    page, values: dict, report: dict | None = None
) -> list[dict]:
    """Fill mid-tier / Rippling typable policy comboboxes left as Search/Select…

    REV Robotics W04: MARKETING_CONSENT Search combobox missed by RIPPLING_PACK.
    Uses verified_select word-by-word via fill_custom_widget (never Enter).
    """
    rows: list[dict] = []
    filled_labels = {
        re.sub(r"\s+", " ", str(f.get("label") or "").lower())[:50]
        for f in ((report or {}).get("filled") or [])
        if isinstance(f, dict) and f.get("verified")
    }
    defaults = {
        MARKETING_CONSENT: "No",
        WORK_AUTH: "Yes",
        SPONSORSHIP: "No",
    }
    try:
        candidates = await page.evaluate(
            """() => {
              const out = [];
              const isVis = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0
                  && window.getComputedStyle(el).visibility !== 'hidden';
              };
              const blocks = Array.from(document.querySelectorAll(
                '[data-testid*="input"], [class*="field"], [class*="Field"], label'
              ));
              for (const el of blocks) {
                let label = '';
                if ((el.tagName || '').toLowerCase() === 'label') {
                  label = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                } else {
                  const lab = el.querySelector('label') ||
                    el.closest('[class*="field"]')?.querySelector('label');
                  label = ((lab && lab.innerText) || el.getAttribute('aria-label') || '')
                    .replace(/\\s+/g, ' ').trim();
                }
                if (!label || label.length < 8) continue;
                const block = el.closest('[class*="field"], [data-testid], form, div') || el;
                const combo = block.querySelector(
                  '[role=combobox], button[aria-haspopup="listbox"], ' +
                  'input[placeholder*="Search" i], input[placeholder*="Select" i]'
                );
                if (!combo || !isVis(combo)) continue;
                const shown = (combo.innerText || combo.value || '').trim();
                if (shown && shown.length > 2 &&
                    !/^(select|search|choose|type|start typing)/i.test(shown)) {
                  continue;
                }
                out.push({label: label.slice(0, 160)});
              }
              const seen = new Set();
              return out.filter((x) => {
                const k = (x.label || '').toLowerCase().slice(0, 60);
                if (seen.has(k)) return false;
                seen.add(k);
                return true;
              }).slice(0, 24);
            }"""
        )
    except Exception as e:
        if report is not None:
            report.setdefault("errors", []).append(
                {"midtier_combo_sweep": str(e)[:120]}
            )
        return rows

    for item in candidates or []:
        label = str(item.get("label") or "")
        if not label:
            continue
        lab_key = re.sub(r"\s+", " ", label.lower())[:50]
        if lab_key in filled_labels:
            continue
        ftype = None
        fake = {
            "label": label,
            "name": "",
            "id": "",
            "type": "combobox",
            "placeholder": "",
            "aria_label": label,
            "autocomplete": "",
        }
        ftype, _layer = classify_field(fake)
        if not ftype:
            for ft, pat in _MIDTIER_POLICY_PATTERNS:
                if pat.search(label):
                    ftype = ft
                    break
        if ftype not in defaults:
            continue
        val = str(values.get(ftype) or defaults[ftype])
        loc = None
        try:
            by_lab = page.get_by_label(re.compile(re.escape(label[:36]), re.I)).first
            if await by_lab.count() and await by_lab.is_visible(timeout=300):
                loc = by_lab
        except Exception:
            loc = None
        if loc is None or await loc.count() == 0:
            frag = re.escape(label[:40].strip())
            loc = page.locator(
                f"[class*='field']:has(label:has-text(/{frag}/i)) [role=combobox], "
                f"[class*='Field']:has(label:has-text(/{frag}/i)) [role=combobox], "
                f"[class*='field']:has(label:has-text(/{frag}/i)) "
                f"button[aria-haspopup='listbox'], "
                f"[data-testid]:has(label:has-text(/{frag}/i)) input"
            ).first
        if await loc.count() == 0:
            continue
        try:
            result = await fill_custom_widget(
                page, loc, val, field_type=ftype, label=label
            )
        except Exception as e:
            rows.append(
                {
                    "via": "midtier_combo_sweep",
                    "type": ftype,
                    "label": label[:80],
                    "ok": False,
                    "verified": False,
                    "reason": str(e)[:120],
                    "flash_candidate": True,
                }
            )
            continue
        ok = bool(result.get("verified"))
        row = {
            "via": "midtier_combo_sweep",
            "layer": "0.5",
            "type": ftype,
            "label": label[:80],
            "ok": ok,
            "verified": ok,
            "value": val,
            "readback": str(result.get("readback") or result.get("picked") or "")[:120],
            "mode": "combobox",
            "widget": result,
            "flash_candidate": not ok,
        }
        if not ok:
            row["reason"] = result.get("error") or "combobox_unverified"
        rows.append(row)
        if ok:
            filled_labels.add(lab_key)
    return rows


def _pack_item_locked(
    report: dict | None, ftype: str, sel: str, aid: str | None
) -> bool:
    """True when field_lock already owns this pack identity (no DOM probe)."""
    try:
        from field_lock import get_field_locks, resolve_lock_report

        sess = get_field_locks(resolve_lock_report(report))
        if sess is None:
            return False
        return bool(
            sess.is_locked(
                field_type=ftype, selector=sel, automation_id=aid or None
            )
        )
    except Exception:
        return False


async def apply_selector_pack(
    page, platform: str, values: dict, report: dict | None = None
) -> list[dict]:
    """Layer 0.5: platform-specific stable selectors (page or Frame).

    Vanilla native text/select/checkbox rows are set in one ``page.evaluate``
    via ``batch_fill_simple``; widgets and batch misses stay on sequential
    ``_fill_selector`` (never asyncio.gather — focus steal).
    """
    from batch_fill import (
        batch_fill_simple,
        batch_result_verified,
        is_batchable_row,
        normalize_batch_mode,
    )

    filled: list[dict] = []
    pack = SELECTOR_PACKS.get(platform) or []
    seen_types: set[str] = set()
    resume_done = False

    # Workday address line is street-only
    local_values = dict(values)
    if platform == "workday" and local_values.get(ADDRESS_LINE1):
        # If value looks like full address, strip to street
        local_values[ADDRESS_LINE1] = _street_line(str(local_values[ADDRESS_LINE1]))

    def _usable(ftype: str, confirm_email: bool) -> bool:
        if ftype == RESUME_UPLOAD and resume_done:
            return False
        if ftype in seen_types and ftype != RESUME_UPLOAD and not confirm_email:
            return False
        return True

    async def _commit_pack_result(
        sel: str, ftype: str, mode: str, result: dict
    ) -> bool:
        nonlocal resume_done
        row = {
            "via": f"{platform}_selector_pack",
            "layer": "0.5",
            **result,
        }
        ok = is_verified_fill_row(result)
        _record_fill_attempt(
            report, {**row, "type": ftype}, success=ok, via_override=row["via"]
        )
        if not ok:
            return False
        filled.append({**row, "ok": True, "verified": True})
        try:
            from field_lock import lock_verified_field

            pack_aid = str(row.get("automation_id") or "") or _automation_id_from_selector(
                sel
            )
            lock_verified_field(
                report,
                {**row, "type": ftype, "ok": True, "verified": True},
                field_type=ftype,
                selector=sel,
                automation_id=pack_aid or None,
                via=row["via"],
            )
        except Exception:
            pass
        seen_types.add(ftype)
        if ftype == RESUME_UPLOAD:
            resume_done = True
        # Ashby: Location combobox remounts dependent zip — settle before next pack item.
        # Never on Workday (skip_ashby_location_zip) — postalCode is already filled.
        if (
            not skip_ashby_location_zip(platform, report)
            and platform == "ashby"
            and ftype == ADDRESS_CITY
            and mode == "combobox"
        ):
            try:
                await page.wait_for_timeout(700)
            except Exception:
                pass
        return True

    async def _fill_one(sel: str, ftype: str, mode: str, val: str) -> None:
        _pack_t0 = time.monotonic()
        result = await _fill_selector(
            page, sel, ftype, str(val), mode=mode, report=report
        )
        _pack_dt = time.monotonic() - _pack_t0
        if os.environ.get("FASTFILL_PACK_TIMING") == "1" and _pack_dt > 1.0:
            print(f"[pack_timing] {platform} {ftype} mode={mode} {_pack_dt:.2f}s", flush=True)
        await _commit_pack_result(sel, ftype, mode, result)

    candidates: list[dict[str, Any]] = []
    for sel, ftype, mode in pack:
        confirm_email = ftype == EMAIL and bool(
            re.search(
                r"confirm|re[-_]?enter|verify|userName|user[_-]?name|fbclc_userName",
                sel,
                re.I,
            )
        )
        pack_aid = _automation_id_from_selector(sel)
        if _pack_item_locked(report, ftype, sel, pack_aid or None):
            continue
        if ftype == RESUME_UPLOAD:
            val = str(local_values.get(RESUME_UPLOAD) or DUMMY_PDF)
        else:
            val = local_values.get(ftype)
            if not val or not validate_filled(ftype, str(val)):
                continue
        candidates.append(
            {
                "sel": sel,
                "ftype": ftype,
                "mode": mode,
                "val": str(val),
                "aid": pack_aid,
                "confirm_email": confirm_email,
            }
        )

    i = 0
    n = len(candidates)
    while i < n:
        run: list[tuple[dict[str, Any], dict[str, Any]]] = []
        while i < n:
            c = candidates[i]
            if not _usable(c["ftype"], c["confirm_email"]):
                i += 1
                continue
            plan_row = {
                "selector": c["sel"],
                "value": c["val"],
                "type": c["ftype"],
                "mode": normalize_batch_mode(c["mode"]),
            }
            if is_batchable_row(plan_row):
                run.append((c, plan_row))
                i += 1
            else:
                break
        if run:
            still: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for c, plan_row in run:
                try:
                    from fill_contract import verify_before_touch

                    touch_meta = {
                        "type": c["ftype"],
                        "selector": c["sel"],
                        "mode": c["mode"],
                    }
                    if c["aid"]:
                        touch_meta["automation_id"] = c["aid"]
                    touch = await verify_before_touch(
                        page, touch_meta, str(c["val"]), report=report
                    )
                    if touch.action == "skip_lock" and touch.row:
                        skip_row = {
                            "selector": c["sel"],
                            "type": c["ftype"],
                            "mode": c["mode"],
                            **touch.row,
                        }
                        if c["aid"]:
                            skip_row["automation_id"] = c["aid"]
                        await _commit_pack_result(
                            c["sel"], c["ftype"], c["mode"], skip_row
                        )
                        continue
                except Exception:
                    pass
                still.append((c, plan_row))
            if still:
                results = await batch_fill_simple(
                    page, [p for _, p in still]
                )
                by_sel: dict[str, dict] = {}
                batch_failed = (
                    len(results) == 1
                    and not results[0].get("selector")
                    and results[0].get("ok") is False
                )
                if not batch_failed:
                    for r in results:
                        if isinstance(r, dict) and r.get("selector"):
                            by_sel[str(r["selector"])] = r
                for c, plan_row in still:
                    br = by_sel.get(c["sel"])
                    if br and batch_result_verified(plan_row, br):
                        result = {
                            "selector": c["sel"],
                            "type": c["ftype"],
                            "mode": c["mode"],
                            "ok": True,
                            "verified": True,
                            "value": c["val"],
                            "readback": str(br.get("readback") or "")[:120],
                            "reason": br.get("reason") or "batch_fill",
                            "via": f"{platform}_selector_pack",
                        }
                        if br.get("reason") == "already_correct_skip":
                            result["skipped_already_correct"] = True
                        if c["aid"]:
                            result["automation_id"] = c["aid"]
                        result = await _supervise_selector_result(
                            page, report, result, intent=str(c["val"])
                        )
                        rb = str(result.get("readback") or "").strip()
                        if is_verified_fill_row(result) and rb:
                            await _commit_pack_result(
                                c["sel"], c["ftype"], c["mode"], result
                            )
                            continue
                    # Miss / empty readback → sequential Playwright / fiber path
                    await _fill_one(c["sel"], c["ftype"], c["mode"], c["val"])
        if i < n:
            c = candidates[i]
            if _usable(c["ftype"], c["confirm_email"]):
                await _fill_one(c["sel"], c["ftype"], c["mode"], c["val"])
            i += 1
    return filled


async def apply_selector_pack_anywhere(
    page, platform: str, values: dict, report: dict | None = None
) -> tuple[list[dict], Any]:
    """Try selector pack on best iframe/page context; fall back to main page."""
    from iframe_ctx import pick_fill_context

    ctx = await pick_fill_context(page, FORM_FIELD_SELECTORS)
    target = ctx.get("frame") or page
    filled = await apply_selector_pack(target, platform, values, report=report)
    # If iframe pack missed everything, also try top page (some hybrid hosts)
    if not filled and target is not page:
        more = await apply_selector_pack(page, platform, values, report=report)
        filled.extend(more)
        if more:
            target = page
    for f in filled:
        f["fill_ctx"] = ctx.get("kind")
        f["fill_ctx_url"] = (ctx.get("url") or "")[:160]
    return filled, target


def _unclassified_skip_quietly(label: str, field: dict) -> bool:
    """True when an unclassified field should be skipped without a leftover.

    Optional social URLs, conditional follow-ups, bare Yes/No radios, and
    "Type your response" prompts are never invented — skip quietly (not a
    leftover). Extracted verbatim from ``fill_from_extract`` (behavior-preserving).
    """
    # Optional social URLs — never invent; skip quietly (not a leftover)
    if re.search(r"twitter|\bx\b[\s_-]*url|instagram|tiktok|facebook", label, re.I):
        return True
    # Conditional follow-ups / extract noise — skip quietly
    if re.search(r"partnership[\s_-]*program", label, re.I):
        return True
    if label.strip().lower() in ("yes", "no") and str(
        field.get("type") or ""
    ).lower() in ("radio_group", "radio", "checkbox_group", "checkbox"):
        return True
    if re.search(r"^type\s+your\s+response$", label.strip(), re.I):
        return True
    try:
        from workday_date_readback import (
            is_date_spin_theater_label,
            is_optional_gpa_label,
        )

        if is_date_spin_theater_label(label) or is_optional_gpa_label(label):
            return True
    except Exception:
        pass
    return False


def _sponsorship_radio_candidates(
    options: list[dict], default_cands: list[str]
) -> list[str]:
    """Candidate values for a SPONSORSHIP radio group.

    Lever can encode "do you require sponsorship?" as a citizenship-status
    radio list. "No" has no matching option there; using the old generic Citizen
    alias silently invented a status. Only use the explicit fictional dummy
    status when those are the available choices. Extracted verbatim from
    ``fill_from_extract`` (behavior-preserving).
    """
    option_text = " ".join(
        str(opt.get("label") or opt.get("value") or "")
        for opt in options
    ).lower()
    has_status_options = bool(
        re.search(r"\bcitizen\b|permanent\s+resident|\bopt\b", option_text)
    )
    has_direct_no = bool(
        re.search(
            r"\bno\b|do\s+not\s+require|don'?t\s+require|"
            r"will\s+not\s+require|no\s+sponsorship",
            option_text,
        )
    )
    dummy_status = str(
        (DUMMY_PROFILE.get("work_authorization") or {}).get("status") or ""
    ).strip()
    if has_status_options and not has_direct_no and dummy_status:
        return [dummy_status]
    return default_cands


# Types allowed to fill more than once per page (a second control is legitimate:
# e.g. resume+cover, prior-employer + relatives, preferred first name, work-auth
# legal-yes + sponsorship-yes). Others skip once their type is already filled.
_ALLOW_MULTI_FILL_TYPES = (
    RESUME_UPLOAD,
    TERMS_CONSENT,
    HOW_HEARD,
    NOTICE_PERIOD,
    RELOCATION,
    PORTFOLIO,
    INTEREST,
    WORKED_HERE_BEFORE,  # prior employer + relatives (two GH selects)
    NAME_FIRST,  # Preferred First Name after legal first
    MARKETING_CONSENT,
    # Tax Relief / many GH boards: legal auth Yes + "without need for
    # visa sponsorship" Yes are both WORK_AUTH — skipping the second
    # left Select… (vision paraphrased as SPONSORSHIP FAIL_BLANK).
    WORK_AUTH,
    SPONSORSHIP,
    "BACKGROUND_CHECK",
    "AGE_18",
    "COMMUTE",
)


def _skip_already_filled_type(
    ftype: str, label: str, filled_types: set[str]
) -> bool:
    """True when this field's type is already filled and must be skipped.

    Allow-multi types (resume/cover, HOW_HEARD other-specify, preferred first
    name, etc.) may fill twice. Extracted verbatim from ``fill_from_extract``
    (behavior-preserving).
    """
    if ftype in filled_types and ftype not in _ALLOW_MULTI_FILL_TYPES:
        # Allow a second HOW_HEARD "If Other, specify" text box after dropdown
        if not (ftype == HOW_HEARD and re.search(r"other|specify", label, re.I)):
            # Preferred first name is a second NAME_FIRST — still fill
            if not (
                ftype == NAME_FIRST and re.search(r"preferred", label, re.I)
            ):
                return True
    return False


def _sponsorship_intent_satisfied(filled_types: set[str]) -> bool:
    """True when the no-sponsorship intent is already committed by a sibling field.

    Dummy needs no visa/sponsorship, so a verified SPONSORSHIP, VISA_STATUS, or
    WORK_AUTH answer already expresses that decision. A second SPONSORSHIP field
    with no real react-select control is a phantom/duplicate — suppress it
    instead of thrashing Flash on a control that does not exist.
    """
    return bool(filled_types & {SPONSORSHIP, VISA_STATUS, WORK_AUTH})


def _radio_group_name(field: dict, sel: str) -> str:
    """Resolve the shared radio ``name=`` for a choice group.

    Prefers the extracted field name; else parses ``[name="…"]`` out of the
    selector (e.g. ``input[name="cards[…][field0]"][value=…]``). Extracted
    verbatim from ``fill_from_extract`` (behavior-preserving).
    """
    group_name = str(field.get("name") or "").strip()
    if not group_name and sel:
        m_name = re.search(r"\[name=(?:\"([^\"]+)\"|'([^']+)')\]", sel)
        if m_name:
            group_name = m_name.group(1) or m_name.group(2) or ""
    return group_name


_EXTRACT_BATCH_HTML = frozenset(
    {
        "text",
        "email",
        "tel",
        "url",
        "password",
        "search",
        "textarea",
        "number",
        "",
        "select",
        "checkbox",
    }
)


async def _batch_fill_extract_vanilla(
    page,
    raw_fields: list,
    values: dict,
    filled_types: set[str],
    *,
    platform: str = "unknown",
    report: dict | None = None,
) -> tuple[list[dict], set[int]]:
    """One-evaluate Layer 0/1 vanilla fills; misses stay for sequential extract."""
    from batch_fill import (
        batch_fill_simple,
        batch_result_verified,
        is_batchable_row,
    )

    filled_out: list[dict] = []
    ok_idx: set[int] = set()
    plan: list[dict] = []
    metas: list[tuple] = []

    for idx, raw in enumerate(raw_fields):
        field = _normalize_extracted(raw if isinstance(raw, dict) else {})
        ftype, layer = classify_field(field)
        if not ftype or layer == "honeypot_skipped":
            continue
        if ftype in (COVER_LETTER, RESUME_UPLOAD):
            continue
        if platform == "ashby" and ftype in (ADDRESS_CITY, ADDRESS_ZIP):
            continue
        if platform == "greenhouse" and ftype in _GH_SELECT_FIELD_TYPES:
            continue
        label = str(field.get("label") or field.get("name") or field.get("id") or "?")
        if _skip_already_filled_type(ftype, label, filled_types):
            continue
        if ftype in OPTIONAL_LEAVE_BLANK_TYPES or is_phone_extension_field(label, ftype):
            if not (values.get(ftype) or "").strip():
                continue
        if _is_custom_widget(field):
            continue
        tag = (field.get("tag") or "input").lower()
        html = (field.get("type") or "text").lower()
        if html in (
            "radio",
            "radio_group",
            "checkbox_group",
            "file",
            "date",
            "datetime-local",
            "month",
            "week",
            "time",
        ):
            continue
        if tag == "select" or html == "select":
            mode = "select"
        elif html == "checkbox":
            mode = "checkbox"
        elif tag in ("input", "textarea") and html in _EXTRACT_BATCH_HTML:
            mode = "text"
        else:
            continue
        if ftype == TERMS_CONSENT:
            val = values.get(TERMS_CONSENT) or "Yes"
        else:
            val = values.get(ftype)
            if not val or not validate_filled(ftype, str(val)):
                continue
        sel = field.get("selector") or ""
        if not sel:
            if field.get("id"):
                sel = f"#{field['id']}"
            elif field.get("name"):
                sel = f"[name={json.dumps(field['name'])}]"
        plan_row = {
            "selector": sel,
            "value": str(val),
            "type": ftype,
            "mode": mode,
        }
        if not is_batchable_row(plan_row):
            continue
        aid = _automation_id_from_selector(sel) or str(field.get("id") or "")
        if _pack_item_locked(report, ftype, sel, aid or None):
            continue
        try:
            from fill_contract import verify_before_touch

            touch_meta = {
                "type": ftype,
                "selector": sel,
                "mode": mode,
                "label": label,
            }
            if aid:
                touch_meta["automation_id"] = aid
            touch = await verify_before_touch(
                page, touch_meta, str(val), report=report
            )
            if touch.action == "skip_lock" and touch.row:
                filled_out.append(
                    {
                        "via": "extract+classify",
                        "layer": layer,
                        "label": label[:60],
                        "selector": sel,
                        "type": ftype,
                        **touch.row,
                    }
                )
                ok_idx.add(idx)
                continue
        except Exception:
            pass
        plan.append(plan_row)
        metas.append((idx, ftype, layer, val, sel, label, aid))

    if not plan:
        return filled_out, ok_idx
    results = await batch_fill_simple(page, plan)
    by_sel: dict[str, dict] = {}
    batch_failed = (
        len(results) == 1
        and not results[0].get("selector")
        and results[0].get("ok") is False
    )
    if not batch_failed:
        for r in results:
            if isinstance(r, dict) and r.get("selector"):
                by_sel[str(r["selector"])] = r
    for (idx, ftype, layer, val, sel, label, aid), plan_row in zip(metas, plan):
        br = by_sel.get(sel)
        if not batch_result_verified(plan_row, br):
            continue
        row = {
            "via": "extract+classify",
            "layer": layer,
            "label": label[:60],
            "selector": sel,
            "type": ftype,
            "value": val,
            "readback": str(br.get("readback") or "")[:120],
            "ok": True,
            "verified": True,
            "reason": br.get("reason") or "batch_fill",
        }
        if br.get("reason") == "already_correct_skip":
            row["skipped_already_correct"] = True
        if aid:
            row["automation_id"] = aid
        row = await _supervise_selector_result(
            page, report, row, intent=str(val)
        )
        if not is_verified_fill_row(row) or not str(row.get("readback") or "").strip():
            continue
        try:
            from field_lock import lock_verified_field

            lock_verified_field(
                report,
                row,
                field_type=ftype,
                selector=sel,
                automation_id=aid or None,
                via=row.get("via"),
            )
        except Exception:
            pass
        filled_out.append(row)
        ok_idx.add(idx)
    return filled_out, ok_idx


async def fill_from_extract(
    page,
    values: dict,
    already_types: set[str],
    *,
    platform: str = "unknown",
    report: dict | None = None,
    pass_i: int | None = None,
) -> tuple[list[dict], list[dict], list[dict], int]:
    """extract_form_fields.js → classify_field → fill. Returns filled, leftovers, errors, extracted_count.

    ``page`` may be a Playwright Page or Frame (iframe-aware callers pass the
    frame that hosts the apply form).
    """
    extract_js = resolve_extract_js().read_text()
    filled: list[dict] = []
    leftovers: list[dict] = []
    errors: list[dict] = []
    how_heard_picked_other = False
    try:
        raw_fields = await page.evaluate(extract_js)
    except Exception as e:
        errors.append({"extract": str(e)[:300]})
        return filled, leftovers, errors, 0

    if not isinstance(raw_fields, list):
        errors.append({"extract": f"unexpected type {type(raw_fields)}"})
        return filled, leftovers, errors, 0

    resume_done = RESUME_UPLOAD in already_types
    filled_types = set(already_types)
    batch_ok_idx: set[int] = set()
    try:
        extra_filled, batch_ok_idx = await _batch_fill_extract_vanilla(
            page,
            raw_fields,
            values,
            filled_types,
            platform=platform,
            report=report,
        )
        filled.extend(extra_filled)
        for row in extra_filled:
            ft = row.get("type")
            if ft:
                filled_types.add(str(ft))
    except Exception:
        batch_ok_idx = set()

    for idx, raw in enumerate(raw_fields):
        if idx in batch_ok_idx:
            continue
        try:
            await wait_while_paused(page, report)
        except Exception:
            pass
        field = _normalize_extracted(raw if isinstance(raw, dict) else {})
        ftype, layer = classify_field(field)
        label = field.get("label") or field.get("name") or field.get("id") or "?"
        try:
            note_fill_activity(
                layer=str(layer or "1"),
                action="fill",
                label=str(label)[:80],
                detail=str(ftype or "")[:40],
            )
            await push_fill_activity(page)
        except Exception:
            pass
        if layer == "honeypot_skipped":
            continue

        learned_val = lookup_learned(label)
        if not ftype:
            # Optional social URLs / conditional follow-ups / bare Yes-No /
            # "Type your response" — never invent; skip quietly (not a leftover).
            if _unclassified_skip_quietly(label, field):
                continue
            # Learned allow-list: policy facts for labels Layer 0/1 missed.
            if not learned_val:
                leftovers.append(
                    {
                        "index": idx,
                        "label": label[:100],
                        "html_type": field.get("type"),
                        "selector": field.get("selector") or "",
                        "reason": "unclassified",
                        "flash_candidate": True,
                    }
                )
                continue
            ftype = f"LEARNED:{label[:40]}"
            layer = "learned"
            val = learned_val
        else:
            # COVER_LETTER → leftover for grounded Flash.
            # Salary: fill only when DUMMY supplies a policy string (never invent $).
            # MARKETING_CONSENT: fill "No" (never opt-in) — required GH SMS selects
            # were left as Select... when we skip-quietly.
            if ftype == COVER_LETTER:
                leftovers.append(
                    {
                        "index": idx,
                        "label": label[:100],
                        "type": ftype,
                        "selector": field.get("selector") or "",
                        "reason": "essay_needs_grounded_answer",
                        "flash_candidate": True,
                        "essay": True,
                    }
                )
                continue
            if ftype in (SALARY_EXPECTED, SALARY_CURRENT):
                if not (values.get(ftype) or "").strip():
                    continue
            # Optional blanks — never Flash-essay into middle name / relative name /
            # phone extension.
            if ftype in OPTIONAL_LEAVE_BLANK_TYPES or is_phone_extension_field(
                label, ftype
            ):
                if not (values.get(ftype) or "").strip():
                    continue
            # Ashby: zip often remounts after Location — defer to
            # fill_ashby_location_then_zip (avoids false verified on detached input).
            # Ashby: Location + zip handled by fill_ashby_location_then_zip (no thrash)
            if platform == "ashby" and ftype == ADDRESS_CITY:
                leftovers.append(
                    {
                        "index": idx,
                        "label": label[:100],
                        "type": ftype,
                        "selector": field.get("selector") or "",
                        "reason": "deferred_ashby_location",
                        "flash_candidate": False,
                    }
                )
                continue
            if platform == "ashby" and ftype == ADDRESS_ZIP:
                leftovers.append(
                    {
                        "index": idx,
                        "label": label[:100],
                        "type": ftype,
                        "selector": field.get("selector") or "",
                        "reason": "deferred_ashby_location_zip",
                        "flash_candidate": True,
                    }
                )
                continue
            if _skip_already_filled_type(ftype, label, filled_types):
                continue
            # (HOW_HEARD already in allow-multi set above; keep other-specify note)

            if ftype == RESUME_UPLOAD:
                if resume_done:
                    continue
                sel = field.get("selector") or "input[type=file]"
                resume_path = str(values.get(RESUME_UPLOAD) or DUMMY_PDF)
                result = await _fill_selector(
                    page, sel, ftype, resume_path, mode="file", report=report
                )
                if result.get("ok"):
                    filled.append(
                        {
                            "via": "extract+classify",
                            "layer": layer,
                            "label": label[:60],
                            **result,
                        }
                    )
                    filled_types.add(RESUME_UPLOAD)
                    resume_done = True
                else:
                    leftovers.append(
                        {
                            "index": idx,
                            "label": label[:100],
                            "type": ftype,
                            "reason": "resume_upload_failed",
                            "error": result.get("error") or result.get("reason"),
                            "flash_candidate": True,
                        }
                    )
                continue

            if ftype == TERMS_CONSENT:
                val = values.get(TERMS_CONSENT) or "Yes"
            else:
                val = values.get(ftype)
                if val and validate_filled(ftype, str(val)):
                    pass  # DUMMY_PROFILE / Layer 0–1 value wins
                elif learned_val:
                    val = learned_val
                    layer = "learned"
                else:
                    leftovers.append(
                        {
                            "index": idx,
                            "label": label[:100],
                            "type": ftype,
                            "reason": "no_value",
                            "flash_candidate": True,
                        }
                    )
                    continue

        sel = field.get("selector") or ""
        if not sel:
            if field.get("id"):
                sel = f"#{field['id']}"
            elif field.get("name"):
                sel = f"[name={json.dumps(field['name'])}]"

        # Cap: after 2 fails stop rewriting this field (UNFILLABLE_AFTER_2)
        if _field_capped_unfillable(
            report, field_type=ftype, label=label, selector=sel
        ):
            leftovers.append(
                {
                    "index": idx,
                    "label": label[:100],
                    "type": ftype,
                    "selector": sel,
                    "reason": "unfillable_after_2",
                    "flash_candidate": False,
                }
            )
            continue

        try:
            # Greenhouse react-select: label sibling of .select__control (not descendant)
            if await _should_use_gh_select(page, field, ftype, platform):
                select_val = str(val)
                select_aliases = aliases_for(ftype, select_val)
                if ftype == ADDRESS_CITY:
                    select_val, select_aliases = _gh_city_aliases(values, str(val))
                result = await fill_gh_select(
                    page,
                    label,
                    select_val,
                    field_type=ftype,
                    aliases=select_aliases,
                )
                if result.get("ok"):
                    if ftype == HOW_HEARD and "other" in (result.get("picked") or "").lower():
                        how_heard_picked_other = True
                    _record_playbook_success(
                        report,
                        ftype,
                        sel,
                        {
                            "label": label,
                            "tag": field.get("tag"),
                            "role": field.get("role"),
                            "type": field.get("type") or field.get("html_type"),
                            "class": field.get("class") or field.get("className"),
                            "name": field.get("name"),
                            "id": field.get("id"),
                            "platform": platform,
                        },
                    )
                    filled.append(
                        {
                            "via": "gh_select",
                            "layer": layer,
                            "label": label[:60],
                            "selector": sel or "label.select__label+select__control",
                            "type": ftype,
                            "value": val,
                            "picked": result.get("picked"),
                            "shown": result.get("shown"),
                            "readback": (result.get("shown") or result.get("picked") or "")[:120],
                            "ok": True,
                            "verified": True,
                            "skipped_already_correct": bool(
                                result.get("skipped_already_correct")
                            ),
                            "reason": (
                                "already_correct_skip"
                                if result.get("skipped_already_correct")
                                else None
                            ),
                        }
                    )
                    filled_types.add(ftype)
                    continue
                # Free-text salary / clearance often misrouted: no select__control →
                # fall through to text fill instead of leaving blank.
                err = str(result.get("error") or "")
                no_control = (
                    "no select__control" in err.lower()
                    or "select__control" in err.lower()
                )
                # SPONSORSHIP phantom: a second sponsorship-family field with no
                # react-select control, when sponsorship intent is already
                # satisfied (SPONSORSHIP / VISA_STATUS / WORK_AUTH filled). Suppress
                # rather than thrash Flash on a control that does not exist
                # (Capco cycle: duplicate SPONSORSHIP leftover, "no select__control").
                if (
                    no_control
                    and ftype == SPONSORSHIP
                    and _sponsorship_intent_satisfied(filled_types)
                ):
                    filled.append(
                        {
                            "via": "gh_select",
                            "layer": layer,
                            "label": label[:60],
                            "type": ftype,
                            "ok": True,
                            "verified": True,
                            "skipped_already_correct": True,
                            "reason": "sponsorship_phantom_suppressed",
                        }
                    )
                    filled_types.add(ftype)
                    continue
                if (ftype in _GH_SELECT_OPTIONAL_DOM_TYPES or no_control) and sel:
                    pass  # fall through to locator text path below
                else:
                    leftovers.append(
                        {
                            "index": idx,
                            "label": label[:100],
                            "type": ftype,
                            "reason": "gh_select_failed",
                            "error": result.get("error"),
                            "options": (result.get("options") or [])[:8],
                            "flash_candidate": True,
                        }
                    )
                    continue

            if not sel:
                leftovers.append(
                    {
                        "index": idx,
                        "label": label[:100],
                        "type": ftype,
                        "reason": "no_selector",
                        "flash_candidate": True,
                    }
                )
                continue

            loc = page.locator(sel).first
            if await loc.count() == 0 and field.get("id"):
                loc = page.locator(f"#{field['id']}").first
                sel = f"#{field['id']}"
            if await loc.count() == 0:
                label_txt = (field.get("label") or "").split("*")[0].strip()
                if label_txt:
                    loc = page.get_by_label(label_txt, exact=False).first
                if await loc.count() == 0:
                    leftovers.append(
                        {
                            "index": idx,
                            "label": label[:100],
                            "type": ftype,
                            "reason": "locator_miss",
                            "selector": sel,
                            "flash_candidate": True,
                        }
                    )
                    continue

            tag = (field.get("tag") or "input").lower()
            ftype_html = (field.get("type") or "text").lower()
            try:
                dom_input_type = ((await loc.get_attribute("type")) or "").lower()
            except Exception:
                dom_input_type = ""
            is_choice_widget = ftype_html in (
                "checkbox",
                "radio",
                "radio_group",
                "checkbox_group",
            ) or dom_input_type in ("checkbox", "radio")

            # Lever: sponsorship is Yes/No radio — skip textarea misextracts (Lindblad field7)
            if (
                platform == "lever"
                and ftype in (SPONSORSHIP, VISA_STATUS)
                and not is_choice_widget
                and tag in ("textarea", "input")
                and dom_input_type not in ("radio", "checkbox")
            ):
                continue

            if not value_ok_for_field_shape(str(val), label=label, ftype=ftype):
                leftovers.append(
                    {
                        "index": idx,
                        "label": label[:100],
                        "type": ftype,
                        "selector": sel,
                        "reason": "crossfill_shape_rejected",
                        "flash_candidate": True,
                    }
                )
                continue

            if (
                _is_custom_widget(field)
                or ftype_html in ("search-dropdown", "combobox")
                or (
                    ftype in (ADDRESS_CITY, LOCATION)
                    and (
                        "location" in label.lower()
                        or ((await loc.get_attribute("role")) or "").lower()
                        == "combobox"
                    )
                )
            ):
                detail = await fill_custom_widget(
                    page, loc, str(val), field_type=ftype, label=label
                )
                if detail.get("ok"):
                    verified = bool(detail.get("verified")) and bool(
                        detail.get("option_clicked")
                        or detail.get("skipped_already_correct")
                        or detail.get("committed")
                    )
                    # Require non-placeholder readback
                    rb = str(
                        detail.get("readback") or detail.get("option_text") or ""
                    )
                    from verified_select import is_placeholder_select_value

                    if verified and is_placeholder_select_value(rb):
                        verified = False
                    if verified:
                        filled.append(
                            {
                                "via": "extract+classify+widget",
                                "layer": layer,
                                "label": label[:60],
                                "selector": sel,
                                "type": ftype,
                                "value": val,
                                "widget": detail,
                                "readback": (detail.get("option_text") or "")[:120],
                                "ok": True,
                                "verified": True,
                            }
                        )
                        filled_types.add(ftype)
                    else:
                        leftovers.append(
                            {
                                "index": idx,
                                "label": label[:100],
                                "type": ftype,
                                "reason": "widget_unverified",
                                "error": detail.get("error"),
                                "flash_candidate": True,
                            }
                        )
                else:
                    leftovers.append(
                        {
                            "index": idx,
                            "label": label[:100],
                            "type": ftype,
                            "reason": "widget_failed",
                            "error": detail.get("error"),
                            "flash_candidate": True,
                        }
                    )
            elif tag == "select" or ftype_html == "select":
                select_ok = False
                last_err = ""
                picked_val = str(val)
                for cand in aliases_for(ftype, str(val)):
                    try:
                        await loc.select_option(label=cand, timeout=2000)
                        select_ok = True
                        picked_val = cand
                        break
                    except Exception as e:
                        last_err = str(e)[:120]
                        try:
                            await loc.select_option(value=cand, timeout=2000)
                            select_ok = True
                            picked_val = cand
                            break
                        except Exception as e2:
                            last_err = str(e2)[:120]
                if not select_ok:
                    # Soft-match option labels (JazzHR "Decline to answer",
                    # Lever "Decline to self-identify"). Force for opacity:0
                    # native <select> behind custom Lever/GH chrome.
                    try:
                        from gh_select import _score_option

                        options = await loc.evaluate(
                            """el => Array.from(el.options || []).map(o => ({
                              value: o.value, label: (o.label || o.text || '').trim()
                            }))"""
                        )
                        # FILL2-010: require meaningful soft-match (not any >0).
                        _SOFT_SELECT_MIN = 50
                        best_v, best_s = None, 0
                        for opt in options or []:
                            lab = (opt.get("label") or "").strip()
                            if not lab or lab.lower().startswith("select"):
                                continue
                            for alias in aliases_for(ftype, str(val)):
                                s = _score_option(lab, alias)
                                if s > best_s:
                                    best_s, best_v, picked_val = s, opt.get("value"), lab
                        if best_v is not None and best_s >= _SOFT_SELECT_MIN:
                            try:
                                await loc.select_option(value=str(best_v), timeout=3000)
                                select_ok = True
                            except Exception:
                                # Hidden native select (Lever EEO): set value + events
                                await loc.evaluate(
                                    """(el, v) => {
                                      el.value = v;
                                      el.dispatchEvent(new Event('input', {bubbles:true}));
                                      el.dispatchEvent(new Event('change', {bubbles:true}));
                                    }""",
                                    str(best_v),
                                )
                                select_ok = True
                    except Exception as e:
                        last_err = str(e)[:120]
                if select_ok:
                    readback = await _read_locator_value(loc)
                    verified = _value_matches_readback(str(picked_val), readback) or bool(
                        (readback or "").strip()
                    )
                    if verified:
                        filled.append(
                            {
                                "via": "extract+classify",
                                "layer": layer,
                                "label": label[:60],
                                "selector": sel,
                                "type": ftype,
                                "value": picked_val,
                                "readback": (readback or picked_val)[:120],
                                "ok": True,
                                "verified": True,
                            }
                        )
                        filled_types.add(ftype)
                    else:
                        leftovers.append(
                            {
                                "index": idx,
                                "label": label[:100],
                                "type": ftype,
                                "reason": "select_unverified",
                                "error": last_err,
                                "flash_candidate": True,
                            }
                        )
                else:
                    leftovers.append(
                        {
                            "index": idx,
                            "label": label[:100],
                            "type": ftype,
                            "reason": "select_failed",
                            "error": last_err,
                            "flash_candidate": True,
                        }
                    )
            elif is_choice_widget:
                # Extract emits radio_group/checkbox_group with options[];
                # never Locator.fill() on radio/checkbox (Playwright rejects).
                group_clicked = False
                picked_val = str(val)
                picked_sel = ""
                options = field.get("options") or []
                cands = aliases_for(ftype, str(val))
                if ftype == "SPONSORSHIP":
                    cands = _sponsorship_radio_candidates(options, cands)
                group_name = _radio_group_name(field, sel)

                async def _click_and_verify_radio(
                    opt_loc, display: str, osel: str = ""
                ) -> tuple[bool, str, str]:
                    """Click radio/checkbox and confirm it is actually checked."""
                    try:
                        await opt_loc.click(timeout=3000, force=True)
                    except Exception:
                        try:
                            await opt_loc.check(timeout=3000, force=True)
                        except Exception:
                            return False, display, osel
                    await page.wait_for_timeout(80)
                    try:
                        checked = await opt_loc.is_checked()
                    except Exception:
                        checked = False
                    if not checked and group_name:
                        # Group may use sibling radios — any checked in name wins
                        try:
                            grp = page.locator(
                                f'input[type="radio"][name={json.dumps(group_name)}]:checked'
                            )
                            if await grp.count():
                                display_v = await grp.first.get_attribute("value")
                                return True, (display_v or display), osel
                        except Exception:
                            pass
                    if checked:
                        return True, display, osel
                    return False, display, osel

                # Prefer explicit option selectors from extract (scoped to group)
                for cand in cands:
                    cand_l = cand.lower().strip()
                    for opt in options:
                        olab = str(opt.get("label") or opt.get("value") or "").strip()
                        oval = str(opt.get("value") or "").strip()
                        osel = opt.get("selector") or ""
                        if not osel and not olab:
                            continue
                        from gh_select import _score_option

                        _EEO_RADIO_TYPES = frozenset(
                            {
                                "GENDER",
                                "HISPANIC",
                                "RACE",
                                "VETERAN",
                                "DISABILITY",
                                "LGBTQIA",
                                "AGE_RANGE",
                            }
                        )
                        if ftype in _EEO_RADIO_TYPES or ftype == "SPONSORSHIP":
                            match = _score_option(olab or oval, cand) >= 55
                        else:
                            match = (
                                cand_l == olab.lower()
                                or cand_l == oval.lower()
                                or cand_l in olab.lower()
                                or olab.lower() in cand_l
                            )
                            if not match and ftype in (
                                "GENDER",
                                "HISPANIC",
                                "RACE",
                                "VETERAN",
                                "DISABILITY",
                                "SPONSORSHIP",
                                "LGBTQIA",
                            ):
                                match = _score_option(olab or oval, cand) >= 55
                        if not match:
                            continue
                        try:
                            opt_loc = page.locator(osel).first if osel else None
                            if opt_loc is None or await opt_loc.count() == 0:
                                # Fall back: radio/checkbox by accessible name,
                                # scoped to same name= group when known.
                                role = (
                                    "checkbox"
                                    if "checkbox" in ftype_html or dom_input_type == "checkbox"
                                    else "radio"
                                )
                                if group_name and role == "radio":
                                    opt_loc = page.locator(
                                        f'input[type="radio"][name={json.dumps(group_name)}]'
                                        f'[value={json.dumps(oval or olab)}]'
                                    ).first
                                    if await opt_loc.count() == 0:
                                        # label text near group
                                        opt_loc = page.get_by_role(
                                            role,
                                            name=re.compile(re.escape(olab or oval), re.I),
                                        ).first
                                else:
                                    opt_loc = page.get_by_role(
                                        role, name=re.compile(re.escape(olab or oval), re.I)
                                    ).first
                            if await opt_loc.count():
                                ok_click, display, used_sel = await _click_and_verify_radio(
                                    opt_loc, olab or oval or cand, osel
                                )
                                if ok_click:
                                    group_clicked = True
                                    picked_val = display
                                    picked_sel = used_sel or osel or sel
                                    break
                        except Exception:
                            continue
                    if group_clicked:
                        break
                if not group_clicked:
                    # Role-based click WITHOUT page-wide leakage: only radios
                    # sharing this field's name= (Lever false_success: cand "No"
                    # matched an unrelated Yes/No above sponsorship).
                    for cand in cands:
                        try:
                            role = (
                                "checkbox"
                                if "checkbox" in ftype_html or dom_input_type == "checkbox"
                                else "radio"
                            )
                            if group_name and role == "radio":
                                radios = page.locator(
                                    f'input[type="radio"][name={json.dumps(group_name)}]'
                                )
                                n_r = await radios.count()
                                for ri in range(n_r):
                                    rloc = radios.nth(ri)
                                    rval = ((await rloc.get_attribute("value")) or "").strip()
                                    # Prefer value match; also try sibling label text
                                    label_txt = ""
                                    try:
                                        label_txt = await rloc.evaluate(
                                            """el => {
                                              const id = el.id;
                                              if (id) {
                                                const l = document.querySelector('label[for="'+id+'"]');
                                                if (l) return (l.innerText||'').trim();
                                              }
                                              const p = el.closest('label');
                                              return p ? (p.innerText||'').trim() : '';
                                            }"""
                                        )
                                    except Exception:
                                        label_txt = ""
                                    from gh_select import _score_option

                                    score = max(
                                        _score_option(rval, cand),
                                        _score_option(label_txt or "", cand),
                                    )
                                    if score < 55:
                                        continue
                                    ok_click, display, used_sel = await _click_and_verify_radio(
                                        rloc, label_txt or rval or cand, ""
                                    )
                                    if ok_click:
                                        group_clicked = True
                                        picked_val = display
                                        picked_sel = (
                                            f'input[type="radio"][name={json.dumps(group_name)}]'
                                            f'[value={json.dumps(rval)}]'
                                        )
                                        break
                                if group_clicked:
                                    break
                                continue
                            # No group name: role lookup (legacy) but verify checked
                            opt = page.get_by_role(
                                role, name=re.compile(re.escape(cand), re.I)
                            ).first
                            if await opt.count() and await opt.is_visible(timeout=400):
                                ok_click, display, used_sel = await _click_and_verify_radio(
                                    opt, cand, ""
                                )
                                if ok_click:
                                    group_clicked = True
                                    picked_val = display
                                    break
                        except Exception:
                            continue
                if not group_clicked:
                    # Multi-select office/location checkboxes under RELOCATION:
                    # value is "Yes, willing…" — pick the first concrete option.
                    if (
                        ftype == "RELOCATION"
                        and "checkbox" in ftype_html
                        and options
                    ):
                        for opt in options:
                            olab = str(opt.get("label") or opt.get("value") or "").strip()
                            osel = opt.get("selector") or ""
                            if not olab or olab.lower() in ("yes", "no", "select"):
                                continue
                            if not osel:
                                continue
                            try:
                                opt_loc = page.locator(osel).first
                                if await opt_loc.count():
                                    ok_click, display, used_sel = await _click_and_verify_radio(
                                        opt_loc, olab, osel
                                    )
                                    if ok_click:
                                        group_clicked = True
                                        picked_val = display
                                        picked_sel = used_sel or osel
                                        break
                            except Exception:
                                continue
                if not group_clicked:
                    # Last resort: check the locator if Yes-like
                    low_val = str(val).lower().strip()
                    if low_val in ("yes", "true", "1", "on") or low_val.startswith("yes"):
                        try:
                            ok_click, display, used_sel = await _click_and_verify_radio(
                                loc, str(val), sel
                            )
                            if ok_click:
                                group_clicked = True
                                picked_val = display
                                picked_sel = used_sel or sel
                        except Exception:
                            pass
                if group_clicked:
                    # Honest read-back: must see a checked control in the group
                    readback = str(picked_val)
                    verified = False
                    try:
                        if group_name:
                            chk = page.locator(
                                f'input[type="radio"][name={json.dumps(group_name)}]:checked, '
                                f'input[type="checkbox"][name={json.dumps(group_name)}]:checked'
                            )
                            if await chk.count():
                                verified = True
                                rb_val = await chk.first.get_attribute("value")
                                if rb_val:
                                    readback = rb_val
                        if not verified and picked_sel:
                            pl = page.locator(picked_sel).first
                            if await pl.count() and await pl.is_checked():
                                verified = True
                                readback = (
                                    (await pl.get_attribute("value")) or picked_val
                                )
                    except Exception:
                        verified = False
                    if verified:
                        filled.append(
                            {
                                "via": "extract+classify",
                                "layer": layer,
                                "label": label[:60],
                                "selector": picked_sel or sel,
                                "type": ftype,
                                "value": picked_val,
                                "readback": str(readback)[:120],
                                "ok": True,
                                "verified": True,
                                "mode": "radio",
                            }
                        )
                        filled_types.add(ftype)
                    else:
                        leftovers.append(
                            {
                                "index": idx,
                                "label": label[:100],
                                "type": ftype,
                                "reason": "radio_click_unverified",
                                "value_attempted": picked_val,
                                "flash_candidate": True,
                            }
                        )
                else:
                    leftovers.append(
                        {
                            "index": idx,
                            "label": label[:100],
                            "type": ftype,
                            "reason": "radio_unchecked_left",
                            "flash_candidate": True,
                        }
                    )
            else:
                # SKIP thrash: never clear+retype when live value already matches
                skip_ok, skip_rb = await _locator_already_correct(
                    loc, str(val), field_type=str(ftype or ""), label=label
                )
                if skip_ok:
                    filled.append(
                        {
                            "via": "extract+classify",
                            "layer": layer,
                            "label": label[:60],
                            "selector": sel,
                            "type": ftype,
                            "value": val,
                            "readback": (skip_rb or "")[:120],
                            "ok": True,
                            "verified": True,
                            "reason": "already_correct_skip",
                            "skipped_already_correct": True,
                        }
                    )
                    filled_types.add(ftype)
                    continue
                await loc.fill(str(val), timeout=4000)
                readback = await _read_locator_value(loc)
                verified = _value_matches_readback(str(val), readback)
                # GH/Lever: label:has-text('…') input often misses the real box;
                # retry via accessible name when readback is empty.
                if not verified and label:
                    try:
                        needle = re.split(r"[*\n?]", label)[0].strip()[:80]
                        alt = page.get_by_label(needle, exact=False).first
                        if await alt.count():
                            alt_skip, alt_rb = await _locator_already_correct(
                                alt, str(val), field_type=str(ftype or ""), label=label
                            )
                            if alt_skip:
                                loc = alt
                                readback = alt_rb
                                verified = True
                            else:
                                await alt.fill(str(val), timeout=4000)
                                readback = await _read_locator_value(alt)
                                verified = _value_matches_readback(str(val), readback)
                                if verified:
                                    loc = alt
                    except Exception:
                        pass
                if verified:
                    filled.append(
                        {
                            "via": "extract+classify",
                            "layer": layer,
                            "label": label[:60],
                            "selector": sel,
                            "type": ftype,
                            "value": val,
                            "readback": (readback or "")[:120],
                            "ok": True,
                            "verified": True,
                        }
                    )
                    filled_types.add(ftype)
                else:
                    leftovers.append(
                        {
                            "index": idx,
                            "label": label[:100],
                            "type": ftype,
                            "reason": "readback_empty_or_mismatch",
                            "readback": (readback or "")[:120],
                            "flash_candidate": True,
                        }
                    )
        except Exception as e:
            errors.append({"label": label[:60], "selector": sel, "error": str(e)[:200]})
            leftovers.append(
                {
                    "index": idx,
                    "label": label[:100],
                    "type": ftype,
                    "reason": "fill_error",
                    "error": str(e)[:160],
                    "flash_candidate": True,
                }
            )

    if how_heard_picked_other:
        other_val = values.get(HOW_HEARD) or "Internet job board"
        try:
            if await fill_other_specify(page, str(other_val)):
                filled.append(
                    {
                        "via": "gh_select_other_text",
                        "layer": "post_other",
                        "label": "If Other, please specify",
                        "selector": "label:has-text('Other') input",
                        "type": HOW_HEARD,
                        "value": other_val,
                        "ok": True,
                        "verified": True,
                        "readback": str(other_val)[:120],
                    }
                )
        except Exception as e:
            errors.append({"label": "If Other specify", "error": str(e)[:160]})

    if report is not None:
        for row in filled:
            _record_fill_attempt(
                report, row, success=True, pass_i=pass_i, via_override=row.get("via")
            )
        for row in leftovers:
            _record_fill_attempt(
                report, row, success=False, pass_i=pass_i, via_override=row.get("via") or "extract"
            )

    return filled, leftovers, errors, len(raw_fields)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _resolve_headless(*, headed: bool | None = None, headless: bool | None = None) -> bool:
    """Resolve Playwright headless flag. ``headed=True`` wins over ``headless``.

    Programmatic default (both None): headless=True.
    CLI interactive demos pass headed=True unless --headless is set.
    """
    if headed is not None:
        return not bool(headed)
    if headless is not None:
        return bool(headless)
    return True


# Post-fill browser hold for human review (headed demos / --hold-open).
# Variety / multi-agent: NEVER stack indefinite holds — Chrome OOM on 8GB Macs.
# --hold-open = indefinite (until Ctrl+C / browser closed / Cancel).
# Headed default without --hold-open stays short (90s). Explicit --hold-seconds
# stays capped at VARIETY_MAX unless FASTFILL_ALLOW_LONG_HOLD=1.
HOLD_INDEFINITE = -1
DEFAULT_HEADED_HOLD_SECONDS = 90
HOLD_OPEN_SECONDS = HOLD_INDEFINITE  # --hold-open waits until interrupt
VARIETY_MAX_HOLD_SECONDS = 120
def _max_headed_chrome_mains() -> int:
    """Headed Chrome-for-Testing slots (concurrent dashboard fills).

    Default 3 — enough for parallel fills while one job is on Ready/hold.
    Override with ``FASTFILL_MAX_HEADED_CHROME_MAINS`` (minimum 1).
    """
    raw = (os.environ.get("FASTFILL_MAX_HEADED_CHROME_MAINS") or "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


MAX_HEADED_CHROME_MAINS = _max_headed_chrome_mains()
HEADED_CHROME_LOCK_PATH = ROOT / "logs" / "chrome_headed.lock"


def hold_is_active(seconds: int | None) -> bool:
    """True when a post-fill hold should run (timed or indefinite)."""
    try:
        s = int(seconds or 0)
    except (TypeError, ValueError):
        return False
    return s != 0


def count_chrome_for_testing_mains(*, headed_only: bool = False) -> list[int]:
    """PIDs of Chrome-for-Testing *main* processes (exclude Helper/renderer).

    Used to enforce MAX_HEADED_CHROME_MAINS before launching another headed fill.
    Excludes dashboard UI (``dashboard_ui_profile`` / ``--app=:8787``) and
    OpenClaw PartyRock CDP (``~/.openclaw/browser/openclaw/user-data`` /
    ``:18800``) — same binary, not a fill window (CHR3-003).
    """
    import subprocess
    from pathlib import Path as _Path

    exclude_markers = (
        f"--user-data-dir={ROOT / 'dashboard_ui_profile'}",
        "--app=http://127.0.0.1:8787",
        f"--user-data-dir={_Path.home() / '.openclaw' / 'browser' / 'openclaw' / 'user-data'}",
        "--remote-debugging-port=18800",
        "openclaw/user-data",
    )

    try:
        out = subprocess.check_output(
            ["pgrep", "-lf", "Google Chrome for Testing"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    pids: list[int] = []
    for line in out.splitlines():
        if "Helper" in line or "crashpad" in line:
            continue
        # Main binary path (macOS Playwright layout)
        if "MacOS/Google Chrome for Testing" not in line and "/chrome " not in line:
            # Linux chrome-linux binary often ends with /chrome
            if not re.search(r"/chrome(?:\s|$)", line):
                continue
        if headed_only and ("--headless" in line or "headless=new" in line):
            continue
        if any(marker in line for marker in exclude_markers):
            continue
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        try:
            pids.append(int(parts[0]))
        except ValueError:
            continue
    return pids


@contextmanager
def _headed_chrome_launch_lock() -> Iterator[None]:
    """Exclusive flock around headed busy-check + chromium.launch (CHR-008).

    Prevents two concurrent fast_fill processes from both seeing count=0 and
    both launching. Released after launch returns (success or fail) — not held
    for the whole fill/hold.
    """
    HEADED_CHROME_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HEADED_CHROME_LOCK_PATH, "a+", encoding="utf-8") as lockfile:
        fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN)


def fill_hold_or_captcha_active() -> bool:
    """True when a CAPTCHA wait or live fill session should protect CfT.

    Used so kill_orphan_chrome_mains never SIGTERMs a review/hold window
    (CHR-007 / CHR2-005). Signals: fresh ``.captcha_waiting.json`` marker
    (TTL — stale/dead-writer cleared), or any live ``fast_fill.py`` /
    ``run_fill_visible.sh`` / ``hybrid_fill.py`` / ``real_job_test.py``.
    Does **not** treat OpenClaw PartyRock CfT alone as a hold (CHR3-004).
    """
    try:
        from captcha_pause import captcha_waiting_marker_active

        if captcha_waiting_marker_active():
            return True
    except Exception:
        pass
    import subprocess

    for pattern in (
        "fast_fill.py",
        "run_fill_visible.sh",
        "hybrid_fill.py",
        "real_job_test.py",
    ):
        try:
            out = subprocess.check_output(
                ["pgrep", "-lf", pattern],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            continue
        for line in out.splitlines():
            # Ignore this process itself when checking (we are fast_fill).
            parts = line.strip().split(None, 1)
            if not parts:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            if pid == os.getpid():
                continue
            return True
    return False


def kill_excess_chrome_mains(*, keep: int = 0) -> list[int]:
    """SIGTERM Chrome-for-Testing mains beyond *keep* (default 0 = kill all).

    Prefer ``kill_orphan_chrome_mains`` before headed launch — this raw killer
    does not know about hold/CAPTCHA review windows.
    """
    import signal
    import time as _time

    mains = count_chrome_for_testing_mains()
    killed: list[int] = []
    for pid in mains[keep:]:
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except OSError:
            continue
    if killed:
        _time.sleep(0.8)
    return killed


def kill_orphan_chrome_mains() -> list[int]:
    """Kill only orphaned fill CfT mains — never hold/CAPTCHA review (CHR-007).

    Prefer refuse_headed_if_chrome_busy over kill-all. If a captcha wait marker
    or another fast_fill process is live, return [] (caller refuses new headed).
    When no hold/fill is active, leftover mains are treated as orphans and
    cleared so a fresh headed launch can proceed.
    """
    if fill_hold_or_captcha_active():
        return []
    mains = count_chrome_for_testing_mains()
    if not mains:
        return []
    # No live fill/hold — safe to clear orphans.
    return kill_excess_chrome_mains(keep=0)


def refuse_headed_if_chrome_busy() -> dict[str, Any] | None:
    """Return a blocker report fragment if another Chrome-for-Testing main is live.

    Set FASTFILL_FORCE_HEADED=1 to bypass (document why in ORCHESTRATION.md).
    """
    if (os.environ.get("FASTFILL_FORCE_HEADED") or "").strip() in ("1", "true", "yes"):
        return None
    mains = count_chrome_for_testing_mains()
    if len(mains) < MAX_HEADED_CHROME_MAINS:
        return None
    holding = fill_hold_or_captcha_active()
    return {
        "blocker": "headed_cap",
        "headed_cap": {
            "max": MAX_HEADED_CHROME_MAINS,
            "running_mains": mains,
            "hold_or_captcha": holding,
            "message": (
                f"Refuse headed launch: {len(mains)} Chrome-for-Testing main(s) "
                f"already running {mains}. Cap={MAX_HEADED_CHROME_MAINS}. "
                + (
                    "Another fill is in hold/CAPTCHA review — do not kill it. "
                    if holding
                    else ""
                )
                + "Wait for slot or set FASTFILL_FORCE_HEADED=1."
            ),
        },
    }


def _resolve_hold_seconds(*, hold_seconds: int | None, headed: bool) -> int:
    """Seconds to keep the browser open after fill before close.

    ``HOLD_INDEFINITE`` (-1) = wait until Ctrl+C / browser closed (no cap).
    Positive seconds capped at VARIETY_MAX_HOLD_SECONDS (120) unless
    FASTFILL_ALLOW_LONG_HOLD=1. Explicit hold_seconds wins (then capped).
    Else FASTFILL_HOLD_MS (ms) if set. Otherwise headed defaults to
    DEFAULT_HEADED_HOLD_SECONDS; headless to 0.
    """
    allow_long = (os.environ.get("FASTFILL_ALLOW_LONG_HOLD") or "").strip() in (
        "1",
        "true",
        "yes",
    )
    raw: int
    if hold_seconds is not None:
        raw = int(hold_seconds)
        if raw < 0:
            return HOLD_INDEFINITE
        raw = max(0, raw)
    else:
        env_ms = os.environ.get("FASTFILL_HOLD_MS")
        if env_ms is not None and str(env_ms).strip() != "":
            try:
                ms = max(0, int(env_ms))
                # Env ms never means indefinite; cap to default headed window.
                raw = min(ms // 1000, DEFAULT_HEADED_HOLD_SECONDS)
            except ValueError:
                raw = DEFAULT_HEADED_HOLD_SECONDS if headed else 0
        else:
            raw = DEFAULT_HEADED_HOLD_SECONDS if headed else 0
    if raw <= 0:
        return 0
    if allow_long:
        return raw
    return min(raw, VARIETY_MAX_HOLD_SECONDS)


async def _hold_for_review(
    *,
    seconds: int,
    report: dict | None = None,
    browser=None,
    page=None,
) -> dict:
    """Keep browser open for review until timeout, Ctrl+C, browser closed, or Continue.

    ``seconds < 0`` (HOLD_INDEFINITE): wait forever until interrupt / disconnect /
    overlay Continue / ``.fill_continue``.
    ``seconds == 0``: no hold. Positive: timed hold (interruptible).
    Never submits.

    When *page* is set and fill-pause is enabled, overlay switches to **Continue**.
    Clicking Continue (or touching ``.fill_continue``) returns
    ``{"continued": True, ...}`` so the caller can resume fill / advance Next
    before re-holding. CAPTCHA wait owns its own Continue path separately.
    """
    out: dict = {"continued": False, "via": None, "waited": False}
    if seconds == 0:
        out["via"] = "no_hold"
        return out
    indefinite = seconds < 0
    if indefinite:
        print(
            "[hold] keeping browser open for review until you Continue "
            "(top-right), close the window, or Ctrl+C / Cancel run; never submit…",
            flush=True,
        )
        reason = "indefinite (never submit)"
    else:
        print(
            f"[hold] keeping browser open {seconds}s for review "
            "(Continue / Ctrl+C to leave early; never submit)…",
            flush=True,
        )
        reason = f"{seconds}s (never submit)"
    incomplete_hold = False
    hold_action = "hold_incomplete"
    if report is not None:
        try:
            from page_progress import (
                can_claim_ready,
                finalize_ready_flag,
                may_enter_review_hold,
                workday_wizard_incomplete,
            )

            # Hold browser either way; Ready only when honesty gates pass.
            # Caller should have probed footer_primary (Next vs Submit) already.
            # Never frame as hold_review while footer ADVANCE / mid-wizard.
            review_ok = bool(may_enter_review_hold(report) and can_claim_ready(report))
            if review_ok and not workday_wizard_incomplete(report):
                report["ready_for_review"] = True
                hold_action = "hold_review"
            else:
                report["ready_for_review"] = False
                report["hold_incomplete"] = True
                incomplete_hold = True
                hold_action = "hold_incomplete"
                if not may_enter_review_hold(report):
                    report.setdefault(
                        "hold_incomplete_reason",
                        report.get("footer_primary_label")
                        or report.get("workday_current_step")
                        or "wizard_incomplete",
                    )
            report["hold_indefinite"] = bool(indefinite)
            finalize_ready_flag(report)
            note_step(
                report,
                action=hold_action,
                reason=reason,
                via="headed_hold",
            )
        except Exception:
            incomplete_hold = bool(report.get("hold_incomplete"))
            hold_action = (
                "hold_review"
                if report and not report.get("hold_incomplete")
                else "hold_incomplete"
            )

    # Overlay → Continue (review or incomplete). Skip when CAPTCHA gate owns UI.
    pause_on = True
    if report is not None and "fill_pause_enabled" in report:
        pause_on = bool(report.get("fill_pause_enabled"))
    hold_armed = False
    ashby_hold = False
    if page is not None:
        try:
            from browser_hygiene import is_ashby_url

            ashby_hold = is_ashby_url(page.url or "")
        except Exception:
            ashby_hold = False
    # Ashby bot checks can flag our overlay during manual submit — strip it for
    # Ashby holds (any completeness) and for Ready review on other ATS.
    if page is not None and (ashby_hold or hold_action == "hold_review"):
        try:
            from browser_hygiene import (
                ASHBY_SPAM_USER_GUIDANCE,
                note_ashby_spam_blocker,
                prepare_browser_for_human_submit,
            )

            prep = await prepare_browser_for_human_submit(page)
            if report is not None:
                report["human_submit_prep"] = prep
            if ashby_hold and await note_ashby_spam_blocker(page, report):
                print(f"[ashby] {ASHBY_SPAM_USER_GUIDANCE}", flush=True)
                out["via"] = "ashby_spam_flagged"
                out["ashby_spam"] = True
                return out
        except Exception as e:
            if report is not None:
                report.setdefault("errors", []).append(
                    {"human_submit_prep": str(e)[:120]}
                )
    if page is not None and pause_on and not (
        ashby_hold or hold_action == "hold_review"
    ):
        try:
            st0 = await read_fill_pause_state(page)
            if st0.get("captcha_gated"):
                out["captcha_gated"] = True
            else:
                await enter_hold_continue_mode(
                    page, report, incomplete=incomplete_hold
                )
                hold_armed = True
                out["hold_continue_mode"] = True
        except Exception as e:
            if report is not None:
                report.setdefault("errors", []).append(
                    {"hold_continue_mode": str(e)[:120]}
                )

    end = None if indefinite else (time.monotonic() + seconds)
    out["waited"] = True
    try:
        while True:
            if browser is not None:
                try:
                    if not browser.is_connected():
                        print("[hold] browser closed — ending hold", flush=True)
                        if report is not None:
                            try:
                                note_step(
                                    report,
                                    action=(
                                        "hold_incomplete"
                                        if incomplete_hold
                                        or report.get("hold_incomplete")
                                        else "hold_review"
                                    ),
                                    reason="browser_closed",
                                    via="headed_hold",
                                )
                            except Exception:
                                pass
                        out["via"] = "browser_closed"
                        break
                except Exception:
                    print("[hold] browser gone — ending hold", flush=True)
                    out["via"] = "browser_gone"
                    break

            # Continue from hold (overlay or .fill_continue) — resume fill loop.
            if hold_armed and page is not None:
                try:
                    if consume_fill_continue_sentinel():
                        await set_fill_paused(page, False)
                        print(
                            "[hold] Continue sentinel — resuming fill "
                            "(will attempt Next / leftovers; never submit)…",
                            flush=True,
                        )
                        out.update(continued=True, via="sentinel")
                        if report is not None:
                            report.setdefault("fill_pause", {})["resume_rescan"] = True
                            report["hold_continued"] = True
                            note_step(
                                report,
                                action="hold_continue",
                                reason="sentinel",
                                via="headed_hold",
                            )
                        break
                    st = await read_fill_pause_state(
                        page, assume_paused_on_error=True
                    )
                    if st.get("captcha_gated"):
                        # CAPTCHA appeared mid-hold — yield to captcha wait path
                        out["via"] = "captcha_gated"
                        break
                    if not st.get("paused"):
                        print(
                            "[hold] Continue — resuming fill "
                            "(will attempt Next / leftovers; never submit)…",
                            flush=True,
                        )
                        out.update(continued=True, via="overlay_continue")
                        if report is not None:
                            report.setdefault("fill_pause", {})["resume_rescan"] = True
                            report["hold_continued"] = True
                            note_step(
                                report,
                                action="hold_continue",
                                reason="overlay_continue",
                                via="headed_hold",
                            )
                        break
                except Exception:
                    pass

            if page is not None and ashby_hold:
                try:
                    from browser_hygiene import (
                        ASHBY_SPAM_USER_GUIDANCE,
                        note_ashby_spam_blocker,
                    )

                    if await note_ashby_spam_blocker(page, report):
                        print(
                            f"[ashby] spam flag during hold — {ASHBY_SPAM_USER_GUIDANCE}",
                            flush=True,
                        )
                        out["via"] = "ashby_spam_flagged"
                        out["ashby_spam"] = True
                        break
                except Exception:
                    pass

            if end is not None:
                left = end - time.monotonic()
                if left <= 0:
                    out["via"] = "timeout"
                    break
                await asyncio.sleep(min(left, 0.5 if hold_armed else 1.0))
            else:
                await asyncio.sleep(0.5 if hold_armed else 1.0)
    except KeyboardInterrupt:
        print("[hold] SIGINT — closing browser", flush=True)
        out["via"] = "interrupt"
        if report is not None:
            try:
                note_step(
                    report,
                    action=(
                        "hold_incomplete"
                        if incomplete_hold or report.get("hold_incomplete")
                        else "hold_review"
                    ),
                    reason="interrupted by SIGINT",
                    via="headed_hold",
                )
            except Exception:
                pass
    return out


async def _resume_fill_after_hold(
    page,
    report: dict,
    values: dict,
    *,
    platform: str,
    identity,
) -> dict:
    """After Continue-from-hold: attempt more fill / Workday Next before re-hold.

    Never submits. Clears incomplete-hold framing for the attempt, probes footer,
    and runs Workday multipage continue when still mid-wizard. Returns a small
    status dict.
    """
    out: dict = {"attempted": False, "platform": platform}
    if page is None:
        out["via"] = "no_page"
        return out
    report.pop("hold_incomplete", None)
    report["hold_continue_resume"] = True
    report.setdefault("hold_continue_count", 0)
    report["hold_continue_count"] = int(report["hold_continue_count"]) + 1
    out["attempted"] = True
    out["round"] = report["hold_continue_count"]
    note_fill_activity(
        layer="1",
        action="resume after hold",
        detail=f"round {out['round']}",
    )
    try:
        await push_fill_activity(page)
    except Exception:
        pass
    try:
        await set_fill_paused(page, False)
    except Exception:
        pass

    try:
        from page_progress import probe_footer_primary, workday_wizard_incomplete

        try:
            await probe_footer_primary(page, report)
        except Exception as e:
            report.setdefault("errors", []).append(
                {"footer_primary_after_hold_continue": str(e)[:120]}
            )

        if platform == "workday" or workday_wizard_incomplete(report):
            from workday_selectors import workday_two_phase_on_page

            print(
                "[hold] Continue — Workday/multipage resume from current step…",
                flush=True,
            )
            note_fill_activity(
                layer="workday",
                action="continue multipage",
                detail=str(report.get("workday_current_step") or "after_hold"),
            )
            try:
                await push_fill_activity(page)
            except Exception:
                pass
            wd_more = await workday_two_phase_on_page(
                page,
                values,
                click_create_account=True,
                do_apply_clicks=False,
                resume_pdf=getattr(identity, "resume_pdf", None),
                step_report=report,
            )
            _merge_workday_into_report(report, wd_more, values)
            report["errors"].extend(wd_more.get("errors") or [])
            report["workday_continue_after_hold"] = True
            out["workday_continue"] = True
            try:
                await probe_footer_primary(page, report)
            except Exception:
                pass
        else:
            # Non-Workday: leftover Flash must finish remaining radios + consent.
            # Hold-incomplete is not "done" — Continue resumes inpage leftovers.
            out["workday_continue"] = False
            note_fill_activity(
                layer="1",
                action="rescan after hold",
                detail="leftovers / radios / consent",
            )
            try:
                await push_fill_activity(page)
            except Exception:
                pass
            try:
                from unanswered_choices import scan_and_promote_unanswered

                await scan_and_promote_unanswered(
                    page, report, platform=platform
                )
            except Exception as e:
                report.setdefault("errors", []).append(
                    {"hold_resume_unanswered": str(e)[:120]}
                )
            try:
                from leftover_miss_scan import promote_l01_misses

                await promote_l01_misses(page, report)
            except Exception as e:
                report.setdefault("errors", []).append(
                    {"hold_resume_l01": str(e)[:120]}
                )
            if str(platform or "").lower() == "ashby":
                try:
                    already = {
                        f.get("type")
                        for f in (report.get("filled") or [])
                        if isinstance(f, dict) and f.get("type")
                    }
                    ashby_filled = await fill_ashby_widgets(
                        page, values, report=report
                    )
                    _merge_ashby_reassert_rows(report, already, ashby_filled)
                    out["ashby_widgets_resume"] = True
                except Exception as e:
                    report.setdefault("errors", []).append(
                        {"hold_resume_ashby": str(e)[:120]}
                    )
            if report.get("flash_leftovers_requested") or _flash_candidate_leftovers(
                report
            ):
                try:
                    inpage = await run_inpage_flash_leftovers(page, report, values)
                    report["flash"] = inpage
                    out["inpage_flash_resume"] = True
                except Exception as e:
                    report.setdefault("errors", []).append(
                        {"hold_resume_inpage": str(e)[:120]}
                    )
            try:
                _finalize(report)
            except Exception:
                pass
            out["leftover_resume"] = True
    except Exception as e:
        report.setdefault("errors", []).append(
            {"resume_fill_after_hold": str(e)[:160]}
        )
        out["error"] = str(e)[:160]

    try:
        from page_progress import apply_progress_verdict_gates

        apply_progress_verdict_gates(report)
    except Exception:
        pass
    return out


async def _wait_enter_message(message: str, *, timeout_s: float = 600) -> dict:
    """Wait for Enter (TTY) or timeout; never blocks forever without a bound."""
    out = {"continued": False, "via": None, "message": message}
    print(f"\n*** {message} ***\n", flush=True)
    try:
        if not (sys.stdin and sys.stdin.isatty()):
            out["via"] = "no_tty"
            await asyncio.sleep(min(2.0, timeout_s))
            return out
    except Exception:
        out["via"] = "no_tty"
        return out
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(None, sys.stdin.readline)
    end = time.monotonic() + max(5.0, float(timeout_s))
    while time.monotonic() < end:
        if fut.done():
            try:
                fut.result()
            except Exception:
                pass
            out["continued"] = True
            out["via"] = "enter"
            return out
        await asyncio.sleep(0.4)
    fut.cancel()
    out["via"] = "timeout"
    return out


def _flash_candidate_leftovers(report: dict) -> list[dict]:
    return [
        u
        for u in (report.get("leftovers") or [])
        if isinstance(u, dict) and u.get("flash_candidate") is not False
        and not str(u.get("reason") or "").startswith("blocker:")
    ]


def _leftover_row_fp_key(u: dict) -> str:
    """Stable identity for a leftover row (type|label|selector)."""
    return "|".join(
        [
            str(u.get("type") or ""),
            str(u.get("label") or "")[:80].lower().strip(),
            str(u.get("selector") or "")[:120],
        ]
    )


def _leftover_set_fingerprint(report: dict) -> str:
    """Fingerprint of current flash-candidate leftovers (FILL3-006)."""
    rows = []
    for u in _flash_candidate_leftovers(report):
        rows.append(
            f"{_leftover_row_fp_key(u)}|{str(u.get('reason') or '')[:60]}"
        )
    blob = "\n".join(sorted(rows))
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


def _already_correct_leftover_keys(report: dict) -> set[str]:
    """Keys for fields recently kept/skipped as already_correct (FILL3-020)."""
    keys: set[str] = set()
    for f in report.get("filled") or []:
        if not isinstance(f, dict):
            continue
        reason = str(f.get("reason") or "")
        via = str(f.get("via") or "")
        if (
            f.get("skipped_already_correct")
            or reason in ("already_correct_skip", "already_correct_keep")
            or "already_correct" in via
        ):
            keys.add(_leftover_row_fp_key(f))
    return keys


def _recent_flash_attempt_keys(report: dict) -> set[str]:
    """Keys Flash already tried this run (failed/still/skipped — not verified fills).

    Successful fills that later SPA-wipe into ``live_empty`` are intentionally
    *not* listed here so pass-2 force promote can retry once (FILL3-020).
    """
    keys: set[str] = set()
    flash = report.get("flash") if isinstance(report.get("flash"), dict) else {}
    for bucket in ("attempted", "inpage_attempted", "still", "leftovers"):
        for row in flash.get(bucket) or []:
            if not isinstance(row, dict):
                continue
            # Verified success rows — allow SPA-wipe force retry
            if row.get("ok") is True and row.get("verified") is True:
                continue
            # already_correct skips still count as "attempted" (no re-Flash thrash)
            keys.add(_leftover_row_fp_key(row))
    return keys


_DEMOTED_FLASH_REASONS = frozenset(
    {"live_empty_after_claimed_verified", "unfillable_after_2"}
)


def _demoted_flash_leftovers(report: dict) -> list[dict]:
    """Leftovers demoted from false-verified fills (SPA wipe / readback lie)."""
    return [
        u
        for u in (report.get("leftovers") or [])
        if isinstance(u, dict)
        and str(u.get("reason") or "") in _DEMOTED_FLASH_REASONS
    ]


def _promote_demoted_flash_leftovers(report: dict) -> int:
    """Re-enable flash on demoted leftovers for pass-2 force retry.

    FILL3-020: skip re-Flash when soft-match already_correct keep still holds
    for the same leftover fingerprint, or Flash already attempted that
    fingerprint this run (avoids clear/retype thrash). Still promote
    ``live_empty_after_claimed_verified`` once when not recently flashed —
    SPA wipe recovery needs one force retry.
    """
    already_keys = _already_correct_leftover_keys(report)
    recent_keys = _recent_flash_attempt_keys(report)
    n = 0
    skipped = 0
    for u in report.get("leftovers") or []:
        if not isinstance(u, dict):
            continue
        reason = str(u.get("reason") or "")
        if reason not in _DEMOTED_FLASH_REASONS:
            continue
        key = _leftover_row_fp_key(u)
        if key in already_keys:
            u["flash_candidate"] = False
            u["flash_skip_reason"] = "already_correct_soft_match"
            skipped += 1
            continue
        if key in recent_keys:
            u["flash_candidate"] = False
            u["flash_skip_reason"] = "recent_flash_same_fingerprint"
            skipped += 1
            continue
        u["flash_candidate"] = True
        u["flash_force_reason"] = "pass2_after_zero_fill"
        if reason == "unfillable_after_2":
            u["reason"] = "live_empty_after_claimed_verified"
        n += 1
    if skipped:
        report["flash_promote_skipped"] = int(
            report.get("flash_promote_skipped") or 0
        ) + skipped
    return n


def _flash_filled_count(inpage: dict) -> int:
    """Honest inpage flash fill count (payload omits filled[])."""
    if inpage.get("filled_count") is not None:
        return int(inpage.get("filled_count") or 0)
    n = len(inpage.get("filled") or [])
    if n:
        return n
    return int(inpage.get("llm_fills") or 0) + int(
        inpage.get("deterministic_reclaims") or 0
    )


# Types we must reclaim with packs/gh_select — never burn Flash first-pass on these.
_DETERMINISTIC_RECLAIM_TYPES = frozenset(
    {
        NAME_FIRST,
        NAME_LAST,
        EMAIL,
        PHONE,
        LINKEDIN,
        LOCATION,
        NOTICE_PERIOD,
        EDUCATION_START_YEAR,
        EDUCATION_END_YEAR,
        SCHOOL,
        DEGREE,
        DISCIPLINE,
        MAJOR,
        FIELD_OF_STUDY,
        SALARY_EXPECTED,
        CLEARANCE,
        CLEARANCE_TYPE,
        US_CITIZEN,
        VISA_STATUS,
        WORK_AUTH,
        SPONSORSHIP,
        GENDER,
        HISPANIC,
        VETERAN,
        DISABILITY,
        RESUME_UPLOAD,
        ADDRESS_ZIP,
        ADDRESS_CITY,
        ADDRESS_STATE,
        ADDRESS_LINE1,
        ADDRESS_COUNTRY,
        GITHUB,
        PORTFOLIO,
    }
)


async def _reclaim_deterministic_leftovers(
    page,
    report: dict,
    values: dict,
    *,
    platform: str,
    pass_i: int = 0,
) -> dict:
    """Re-fill demoted/blank deterministic fields via extract+gh_select (0-LLM).

    Flash must only see essays / unclassified leftovers after this reclaim.
    """
    enrich_report_gh_id_leftovers(report)
    before = [
        u
        for u in _flash_candidate_leftovers(report)
        if (u.get("type") in _DETERMINISTIC_RECLAIM_TYPES)
        or str(u.get("ownership") or "") == "prefill_reclaim"
        or str(u.get("reason") or "")
        in (
            "live_empty_after_claimed_verified",
            "live_mismatch_after_claimed_verified",
            "gh_select_failed",
            "no matching option",
            "resume_missing",
            "resume_upload_failed",
            "unverified_readback",
            # Skip claims that still appear as leftovers (SPA wipe / soft match lie)
            "already_correct_skip",
            "already_correct_keep",
        )
        or (
            # Catalog id leftovers even when flash_candidate was cleared
            u.get("type") in _DETERMINISTIC_RECLAIM_TYPES
            and "live_required_empty" in str(u.get("reason") or "")
        )
    ]
    # Also pull deferred-deterministic (flash_candidate=False) catalog rows
    for u in report.get("leftovers") or []:
        if not isinstance(u, dict):
            continue
        if u.get("type") in _DETERMINISTIC_RECLAIM_TYPES and u not in before:
            before.append(u)
    out: dict[str, Any] = {"before": len(before), "filled": 0, "still": 0}
    if not before:
        return out

    # Resume first — GH Tax Relief often remounts file input after SPA wipe
    if any(
        u.get("type") == RESUME_UPLOAD
        or "resume" in str(u.get("label") or "").lower()
        for u in before
    ):
        try:
            # force=True for SPA remount of empty file input — still skips when
            # commit-verified / locked / chrome shows attachment.
            await ensure_resume_uploaded(page, values, report, force=True)
        except Exception as e:
            out["resume_error"] = str(e)[:120]

    already = {
        f.get("type")
        for f in (report.get("filled") or [])
        if isinstance(f, dict) and f.get("ok") is not False and f.get("type")
    }
    # Allow reclaim of demoted types even if still listed in filled incorrectly
    for u in before:
        t = u.get("type")
        if t:
            already.discard(t)
    for t in _demoted_refill_types(report):
        already.discard(t)

    try:
        ext_filled, leftovers, errors, _n = await fill_from_extract(
            page,
            values,
            already,
            platform=platform,
            report=report,
            pass_i=pass_i,
        )
        report["filled"].extend(ext_filled)
        out["filled"] = len(ext_filled)
        filled_types = {
            f.get("type")
            for f in (report.get("filled") or [])
            if isinstance(f, dict) and f.get("verified")
        }
        # Drop reclaimed leftovers; keep essays / unclassified
        kept = []
        for u in report.get("leftovers") or []:
            if not isinstance(u, dict):
                continue
            if u.get("type") in filled_types and u.get("type") in _DETERMINISTIC_RECLAIM_TYPES:
                continue
            kept.append(u)
        # Merge fresh leftovers for types still blank
        for u in leftovers:
            if u.get("type") not in filled_types:
                kept.append(u)
        report["leftovers"] = kept
        report["errors"].extend(errors)
    except Exception as e:
        out["error"] = str(e)[:160]

    if platform == "greenhouse":
        try:
            if await _should_run_gh_post_resume_reassert(report):
                already2 = {
                    f.get("type")
                    for f in (report.get("filled") or [])
                    if isinstance(f, dict) and f.get("verified")
                }
                re_rows = await reassert_greenhouse_contact_after_resume(page, values)
                _merge_greenhouse_reassert_rows(report, already2, re_rows)
                out["greenhouse_reassert"] = len(re_rows or [])
            else:
                out["greenhouse_reassert_skipped"] = "resume_not_verified"
        except Exception as e:
            out["greenhouse_reassert_error"] = str(e)[:120]

    out["still"] = len(
        [
            u
            for u in _flash_candidate_leftovers(report)
            if u.get("type") in _DETERMINISTIC_RECLAIM_TYPES
        ]
    )
    report["deterministic_reclaim"] = out
    return out


async def _in_session_refill_pass(
    page,
    report: dict,
    values: dict,
    *,
    platform: str,
    flash_leftovers: bool,
    pass_i: int,
    force_flash_demoted: bool = False,
    skip_heavy_reassert: bool = False,
) -> dict:
    """One leftover refill on the SAME Playwright page (no navigation restart)."""
    detail: dict[str, Any] = {"pass": pass_i, "before_leftovers": 0, "after_leftovers": 0}
    note_step(
        report,
        action="refill_pass_start",
        pass_i=pass_i,
        reason=f"leftovers={len(_flash_candidate_leftovers(report))}",
        via="in_session_refill",
    )
    # FILL3-009: pause checkpoint at pass boundary (between actions)
    try:
        await wait_while_paused(page, report)
    except Exception:
        pass
    before = _flash_candidate_leftovers(report)
    detail["before_leftovers"] = len(before)
    detail["leftover_fp_before"] = _leftover_set_fingerprint(report)
    detail["labels"] = [
        str(u.get("label") or u.get("type") or "?")[:80] for u in before[:20]
    ]
    if before:
        print(
            f"[refill] pass {pass_i}: {len(before)} blank(s) — "
            + ", ".join(detail["labels"][:8]),
            flush=True,
        )

    # Resume must stay attached across remounts
    try:
        ru = await ensure_resume_uploaded(page, values, report, force=False)
        detail["resume"] = {
            "verified": ru.get("verified"),
            "attempted": ru.get("attempted"),
        }
    except Exception as e:
        detail["resume_error"] = str(e)[:120]

    # FILL3-006: when page+leftover fingerprint is stable, skip scroll-heavy
    # ashby/GH reclaim reassert (still run extract + demote + optional Flash).
    if skip_heavy_reassert:
        detail["skipped_heavy_reassert"] = True
    elif platform == "ashby":
        try:
            already_types = {
                f.get("type")
                for f in (report.get("filled") or [])
                if isinstance(f, dict) and f.get("type")
            }
            re_rows = await reassert_ashby_contact_after_resume(page, values)
            _merge_ashby_reassert_rows(report, already_types, re_rows)
            detail["ashby_reassert"] = len(re_rows or [])
        except Exception as e:
            detail["ashby_reassert_error"] = str(e)[:120]
    elif platform == "greenhouse":
        try:
            if await _should_run_gh_post_resume_reassert(report):
                already_types = {
                    f.get("type")
                    for f in (report.get("filled") or [])
                    if isinstance(f, dict) and f.get("type")
                }
                re_rows = await reassert_greenhouse_contact_after_resume(page, values)
                _merge_greenhouse_reassert_rows(report, already_types, re_rows)
                detail["greenhouse_reassert"] = len(re_rows or [])
            else:
                detail["greenhouse_reassert_skipped"] = "resume_not_verified"
        except Exception as e:
            detail["greenhouse_reassert_error"] = str(e)[:120]

    if report.get("blocker") in CAPTCHA_BLOCKERS:
        detail["skipped"] = f"blocker:{report.get('blocker')}"
        return detail

    already = _already_types_skip_refill(report)
    try:
        ext_filled, leftovers, errors, extracted_count = await fill_from_extract(
            page, values, already, platform=platform, report=report, pass_i=pass_i
        )
        report["filled"].extend(ext_filled)
        # Merge leftovers: prefer fresh list for types we just attempted
        filled_types = {f.get("type") for f in report["filled"] if f.get("type")}
        old_left = [
            u
            for u in (report.get("leftovers") or [])
            if isinstance(u, dict)
            and u.get("type") not in filled_types
            and str(u.get("reason") or "")
            not in ("resume_upload_failed", "resume_missing")
        ]
        report["leftovers"] = old_left + [
            u for u in leftovers if u.get("type") not in filled_types
        ]
        report["errors"].extend(errors)
        if extracted_count:
            report["extracted_count"] = max(
                int(report.get("extracted_count") or 0), extracted_count
            )
        detail["extract_filled"] = len(ext_filled)
    except Exception as e:
        detail["extract_error"] = str(e)[:160]

    # Demote BEFORE Flash so SPA-wiped "verified" rows become flash_candidates.
    # (Prior order flashed first → pass2 often flash_invoked=false with 11 blanks.)
    try:
        await _demote_filled_against_required_empty(page, report, values)
    except Exception:
        pass
    report = _finalize(report)

    # Deterministic reclaim on demoted leftovers (packs/gh_select) before Flash.
    try:
        reclaim = await _reclaim_deterministic_leftovers(
            page, report, values, platform=platform, pass_i=pass_i
        )
        detail["reclaim"] = reclaim
        report = _finalize(report)
    except Exception as e:
        detail["reclaim_error"] = str(e)[:160]

    if force_flash_demoted:
        promoted = _promote_demoted_flash_leftovers(report)
        if promoted:
            detail["flash_demoted_promoted"] = promoted
            report = _finalize(report)
        if report.get("flash_promote_skipped"):
            detail["flash_promote_skipped"] = report.get("flash_promote_skipped")

    left_for_flash = _flash_candidate_leftovers(report)
    demoted_for_flash = [
        u
        for u in (_demoted_flash_leftovers(report) if force_flash_demoted else [])
        if u.get("flash_candidate") is not False
        and not u.get("flash_skip_reason")
    ]
    should_flash = flash_leftovers and (left_for_flash or demoted_for_flash)
    if should_flash:
        try:
            await wait_while_paused(page, report)
        except Exception:
            pass
        try:
            inpage = await run_inpage_flash_leftovers(
                page, report, values, force_demoted=force_flash_demoted
            )
            report["flash"] = inpage
            report["flash_called"] = bool(inpage.get("invoked")) or bool(
                report.get("flash_called")
            )
            detail["flash_invoked"] = bool(inpage.get("invoked"))
            detail["flash_filled"] = _flash_filled_count(inpage)
            detail["flash_attempted"] = True
            if force_flash_demoted:
                detail["flash_force_demoted"] = True
            # 0-fill Flash is waste — mark so scorecard/Fixer sees it
            flash_targets = len(left_for_flash) or len(demoted_for_flash)
            if int(detail["flash_filled"] or 0) == 0 and flash_targets > 0:
                detail["flash_zero_fill"] = True
                report.setdefault("errors", []).append(
                    {
                        "flash_zero_fill": (
                            f"pass {pass_i}: filled 0 of "
                            f"{flash_targets} leftovers"
                            + (" (force_demoted)" if force_flash_demoted else "")
                        )
                    }
                )
        except Exception as e:
            detail["flash_error"] = str(e)[:160]

    try:
        await _demote_filled_against_required_empty(page, report, values)
    except Exception:
        pass

    report = _finalize(report)
    detail["after_leftovers"] = len(_flash_candidate_leftovers(report))
    detail["leftover_fp_after"] = _leftover_set_fingerprint(report)
    emit_filled_rows_as_steps(report, pass_i=pass_i, phase="refill_pass")
    emit_leftover_rows_as_steps(report, pass_i=pass_i)
    note_step(
        report,
        action="refill_pass_end",
        pass_i=pass_i,
        reason=f"leftovers={detail['after_leftovers']}",
        via="in_session_refill",
    )
    # One leftover-fail count per refill pass (fail #2 → UNFILLABLE_AFTER_2 + FIXER_TRIGGER)
    _ingest_attempt_pass(report, pass_i=pass_i, phase=f"refill_pass_{pass_i}")
    return detail


async def _run_in_session_refill_loop(
    page,
    report: dict,
    values: dict,
    *,
    platform: str,
    flash_leftovers: bool,
    refill_passes: int,
    wait_enter: bool,
    screenshot: bool | Path | None,
) -> dict:
    """Auto-loop leftover refill on same page (Enter wait only if explicitly requested).

    FILL3-007: re-probe CAPTCHA each pass and pause again if a challenge appears
    mid-refill (never solve).
    FILL3-006: stop when page + leftover fingerprint is stable (no progress) to
    avoid scroll/reclaim/Flash thrash on an unchanged form.
    """
    summary: dict[str, Any] = {
        "passes": [],
        "max_passes": int(refill_passes),
        "wait_enter": bool(wait_enter),
    }
    if refill_passes <= 0:
        report["in_session_refills"] = summary
        return summary

    pass1_flash_zero_fill = False
    prev_stability: tuple[str, str] | None = None
    for i in range(1, int(refill_passes) + 1):
        force_demoted = i >= 2
        # FILL3-007: mid-refill CAPTCHA re-pause (never solve)
        try:
            if report.get("blocker") in CAPTCHA_BLOCKERS or await page_shows_interactive_captcha(
                page
            ):
                await handle_captcha_blocker(
                    page,
                    report,
                    str(report.get("blocker") or "captcha"),
                    headed=bool(report.get("headed")),
                    captcha_wait=report.get("captcha_wait"),
                    timeout_s=float(
                        report.get("captcha_timeout_s") or DEFAULT_CAPTCHA_TIMEOUT_S
                    ),
                )
                if report.get("blocker") in CAPTCHA_BLOCKERS:
                    summary["stopped"] = f"blocker:{report.get('blocker')}"
                    summary["captcha_mid_refill"] = True
                    break
        except Exception as e:
            summary.setdefault("captcha_mid_refill_errors", []).append(str(e)[:120])
        # Re-demote live blanks before trusting leftovers=0 (SPA wipe / false verified)
        try:
            await dismiss_cookie_banners(page)
        except Exception:
            pass
        try:
            await _demote_filled_against_required_empty(page, report, values)
        except Exception:
            pass
        # Promote still-unanswered required fields (radios / selects / yes-no) into
        # flash-candidate leftovers so the loop retries them instead of stopping
        # while required blanks remain (shared "not done" definition with the gate).
        try:
            from leftover_miss_scan import promote_l01_misses

            await promote_l01_misses(page, report)
        except Exception:
            pass
        report = _finalize(report)
        left = _flash_candidate_leftovers(report)
        if report.get("blocker") and report.get("blocker") not in CAPTCHA_BLOCKERS:
            summary["stopped"] = f"blocker:{report.get('blocker')}"
            break
        if not left:
            # No flash candidates: only honest to call it done when the gate's
            # shared blank definition is also empty; otherwise stop honestly so
            # _finalize's gate FAILs rather than implying completion.
            try:
                from page_progress import outstanding_required_blanks

                required_remain = bool(outstanding_required_blanks(report))
            except Exception:
                required_remain = False
            summary["stopped"] = "required_blanks_remain" if required_remain else "zero_blanks"
            break

        # FILL3-006: page + leftover fingerprint gate
        page_fp = ""
        try:
            from page_progress import capture_step_fingerprint

            page_fp = str((await capture_step_fingerprint(page)).get("fingerprint") or "")
        except Exception:
            page_fp = ""
        left_fp = _leftover_set_fingerprint(report)
        stability = (page_fp, left_fp)
        skip_heavy = False
        if prev_stability is not None and stability == prev_stability:
            # Same page + same blanks as end of prior pass → stop (no reclaim thrash)
            summary["stopped"] = "stable_fingerprint_no_progress"
            summary["stable_fingerprint_skip"] = True
            summary["stable_fingerprint"] = left_fp
            print(
                f"[refill] stable fingerprint — skipping further passes "
                f"(leftover_fp={left_fp})",
                flush=True,
            )
            break
        if prev_stability is not None and prev_stability[0] == page_fp:
            # Same page as last pass end: skip ashby/GH scroll reassert this pass
            skip_heavy = True

        labels = [str(u.get("label") or u.get("type") or "?")[:60] for u in left[:15]]
        print(
            f"[refill] {len(left)} blank(s) before auto pass {i}"
            + (" (waiting Enter — explicit --refill-wait-enter)" if wait_enter else " (auto, no Enter)"),
            flush=True,
        )
        for lab in labels:
            print(f"  - {lab}", flush=True)
        if wait_enter:
            await _wait_enter_message(
                "OPTIONAL: press Enter to continue refill (explicit --refill-wait-enter). "
                "Default cycles auto-refill without this pause. "
                "CAPTCHA continue is separate (Enter / .captcha_continue while overlay is gated).",
                timeout_s=600,
            )

        detail = await _in_session_refill_pass(
            page,
            report,
            values,
            platform=platform,
            flash_leftovers=flash_leftovers,
            pass_i=i,
            force_flash_demoted=force_demoted,
            skip_heavy_reassert=skip_heavy,
        )
        # ingest_pass already ran inside _in_session_refill_pass (one fail/key/pass)
        summary["passes"].append(detail)
        if i == 1 and flash_leftovers and int(detail.get("before_leftovers") or 0) > 0:
            if int(detail.get("flash_filled") or 0) == 0 and int(
                detail.get("after_leftovers") or 0
            ) > 0:
                pass1_flash_zero_fill = True
                summary["pass1_flash_zero_fill"] = True
        if screenshot:
            try:
                await _maybe_shot(page, screenshot, report)
                detail["screenshot"] = report.get("screenshot")
            except Exception as e:
                detail["screenshot_error"] = str(e)[:120]

        # Capture post-pass stability for next iteration
        page_fp_after = page_fp
        try:
            from page_progress import capture_step_fingerprint

            page_fp_after = str(
                (await capture_step_fingerprint(page)).get("fingerprint") or page_fp
            )
        except Exception:
            pass
        left_fp_after = str(detail.get("leftover_fp_after") or _leftover_set_fingerprint(report))
        prev_stability = (page_fp_after, left_fp_after)
        detail["page_fp_before"] = page_fp
        detail["page_fp_after"] = page_fp_after

        # No progress on this pass + unchanged leftover fp → stop further passes
        no_progress = (
            int(detail.get("before_leftovers") or 0)
            == int(detail.get("after_leftovers") or 0)
            and str(detail.get("leftover_fp_before") or "") == left_fp_after
            and int(detail.get("extract_filled") or 0) == 0
            and int(detail.get("flash_filled") or 0) == 0
        )
        if detail.get("after_leftovers", 1) == 0:
            try:
                from page_progress import outstanding_required_blanks

                required_remain = bool(outstanding_required_blanks(report))
            except Exception:
                required_remain = False
            summary["stopped"] = "required_blanks_remain" if required_remain else "zero_blanks"
            break
        if no_progress:
            summary["stopped"] = "stable_fingerprint_no_progress"
            summary["stable_fingerprint_skip"] = True
            print(
                f"[refill] pass {i} no progress on stable leftovers — stopping",
                flush=True,
            )
            break
    else:
        summary["stopped"] = "max_passes"

    report["in_session_refills"] = summary
    return summary


def _merge_workday_into_report(report: dict, wd: dict, values: dict) -> None:
    """Fold Workday Phase A/B metrics into the fast_fill report shape."""
    report["workday"] = {
        "phase_a": wd.get("phase_a"),
        "phase_a_resume": wd.get("phase_a_resume"),
        "autofill_fallback": wd.get("autofill_fallback"),
        "phase_b": wd.get("phase_b"),
        "metrics": wd.get("metrics"),
        "blocker": wd.get("blocker"),
        "verdict": wd.get("verdict"),
        "identity_email": wd.get("identity_email") or values.get(EMAIL),
        "final_url": wd.get("final_url"),
        "page_title": wd.get("page_title"),
        "validation_after_advance": wd.get("validation_after_advance"),
        "advance_blocked_reason": wd.get("advance_blocked_reason"),
        "advanced_incomplete": wd.get("advanced_incomplete"),
        "ready_for_review": wd.get("ready_for_review"),
        "phase_c": wd.get("phase_c"),
        "phase_c2": wd.get("phase_c2"),
        "phase_d": wd.get("phase_d"),
        "phase_e": wd.get("phase_e"),
    }
    if wd.get("phase_a_resume") is not None:
        report["phase_a_resume"] = wd.get("phase_a_resume")
        try:
            from resume_upload import sync_resume_verified_from_phase_a

            sync_resume_verified_from_phase_a(report)
        except Exception:
            par = wd.get("phase_a_resume") or {}
            up = par.get("upload") if isinstance(par.get("upload"), dict) else {}
            if up.get("verified") is True:
                report["resume_verified"] = True
                report["resume_field_present"] = True
    if wd.get("autofill_fallback") is not None:
        report["autofill_fallback"] = wd.get("autofill_fallback")
    if wd.get("workday_entry_path"):
        report["workday_entry_path"] = wd.get("workday_entry_path")
        report["workday"]["workday_entry_path"] = wd.get("workday_entry_path")
    if wd.get("blocker"):
        report["blocker"] = wd["blocker"]
    if wd.get("verdict"):
        report["verdict"] = wd["verdict"]
    if wd.get("advanced_incomplete"):
        report["advanced_incomplete"] = True
    if wd.get("validation_after_advance"):
        report["validation_after_advance"] = wd["validation_after_advance"]
        # Never allow SUCCESS when a validation banner followed ADVANCE
        if report.get("verdict") == "SUCCESS":
            report["verdict"] = "FAIL"
        if (report.get("workday") or {}).get("verdict") == "SUCCESS":
            report["workday"]["verdict"] = "FAIL"
    if wd.get("advance_blocked_reason"):
        report["advance_blocked_reason"] = wd["advance_blocked_reason"]
    if wd.get("required_empty_before_advance") is not None:
        report["required_empty_before_advance"] = wd["required_empty_before_advance"]
    if wd.get("ready_for_review"):
        report["ready_for_review"] = True
    if isinstance(wd.get("vision_judge_live"), dict):
        report["vision_judge_live"] = wd["vision_judge_live"]
    if wd.get("vision_incomplete"):
        report["vision_incomplete"] = True
    if wd.get("workday_current_step"):
        report["workday_current_step"] = wd.get("workday_current_step")
        report["workday"]["current_step"] = wd.get("workday_current_step")
    if wd.get("workday_wizard_progress") is not None:
        report["workday_wizard_progress"] = wd.get("workday_wizard_progress")
        report["workday"]["wizard_progress"] = wd.get("workday_wizard_progress")
    report["identity_email"] = wd.get("identity_email") or report.get("identity_email")

    # Promote Workday required-empty leftovers (Select One blanks, etc.)
    wd_left = wd.get("leftovers") or []
    if wd_left:
        existing = report.setdefault("leftovers", [])
        if not isinstance(existing, list):
            existing = []
            report["leftovers"] = existing
        seen = {
            (str(u.get("label") or ""), str(u.get("automation_id") or ""))
            for u in existing
            if isinstance(u, dict)
        }
        for row in wd_left:
            if not isinstance(row, dict):
                continue
            key = (str(row.get("label") or ""), str(row.get("automation_id") or ""))
            if key in seen:
                continue
            existing.append(row)
            seen.add(key)
        report["leftover_count"] = len(existing)

    # Demote false Workday SUCCESS (contact-only / mid-wizard)
    if report.get("verdict") == "SUCCESS" and not report.get("ready_for_review"):
        report["verdict"] = "FAIL"
        report["verdict_reason"] = "multipage_incomplete_not_ready_for_review"
        if isinstance(report.get("workday"), dict):
            report["workday"]["verdict"] = "FAIL"
            report["workday"]["verdict_reason"] = report["verdict_reason"]

    # Multipage progress (stuck-on-same-page honesty)
    for key in (
        "pages_seen",
        "advanced_count",
        "stuck_on_same_page",
        "page_fingerprint_before",
        "page_fingerprint_after",
    ):
        if wd.get(key) is not None:
            if key == "pages_seen":
                existing = list(report.get("pages_seen") or [])
                for item in wd.get("pages_seen") or []:
                    if item not in existing:
                        existing.append(item)
                report["pages_seen"] = existing
            elif key == "advanced_count":
                report["advanced_count"] = max(
                    int(report.get("advanced_count") or 0),
                    int(wd.get("advanced_count") or 0),
                )
            elif key == "stuck_on_same_page":
                report["stuck_on_same_page"] = bool(
                    report.get("stuck_on_same_page") or wd.get("stuck_on_same_page")
                )
            else:
                report[key] = wd[key]

    # Prefer explicit filled list; never treat status=stuck as a successful fill.
    wd_rows = list(wd.get("filled") or [])
    if not wd_rows:
        # Legacy artifacts only: stuck was misused as filled alias — require verified.
        wd_rows = [
            r
            for r in (wd.get("stuck") or [])
            if isinstance(r, dict) and r.get("verified") is True and r.get("status") != "stuck"
        ]
    for row in wd_rows:
        if not is_verified_fill_row(row) and not (
            row.get("verified") is True
            and row.get("status") == "filled"
            and (row.get("readback") or "").strip()
        ):
            continue
        report["filled"].append(
            {
                "via": "workday_contact_pack",
                "layer": "0.5",
                "type": row.get("automation_id"),
                "selector": row.get("selector"),
                "value": row.get("value"),
                "readback": row.get("readback"),
                "mode": row.get("mode"),
                "ok": True,
                "verified": True,
                "automation_id": row.get("automation_id"),
            }
        )
    for row in (wd.get("create_account") or {}).get("filled") or []:
        if row.get("verified") is not True and row.get("status") != "filled":
            continue
        if row.get("status") == "stuck":
            continue
        report["filled"].append(
            {
                "via": "workday_auth_create",
                "layer": "0.5",
                "type": row.get("automation_id"),
                "selector": row.get("selector")
                or f'[data-automation-id="{row.get("automation_id")}"]',
                "value": row.get("value"),
                "readback": row.get("readback"),
                "ok": True,
                "verified": True,
                "automation_id": row.get("automation_id"),
            }
        )
    for row in (wd.get("sign_in") or {}).get("filled") or []:
        if row.get("verified") is not True and row.get("status") != "filled":
            continue
        if row.get("status") == "stuck":
            continue
        report["filled"].append(
            {
                "via": "workday_auth_signin",
                "layer": "0.5",
                "type": row.get("automation_id"),
                "selector": row.get("selector")
                or f'[data-automation-id="{row.get("automation_id")}"]',
                "value": row.get("value"),
                "readback": row.get("readback"),
                "ok": True,
                "verified": True,
                "automation_id": row.get("automation_id"),
            }
        )
    for row in wd.get("missed") or []:
        report["leftovers"].append(
            {
                "label": row.get("automation_id") or "workday_field",
                "reason": row.get("reason") or "workday_missed",
                "automation_id": row.get("automation_id"),
                "error": row.get("error"),
                "flash_candidate": row.get("reason") != "contact_page_absent",
            }
        )
    report["entry_prepass"] = {
        "clicked": [
            {
                "text": c.get("text"),
                "kind": c.get("kind"),
                "ok": c.get("action") == "clicked",
            }
            for c in (wd.get("clicks") or [])
            if c.get("action") in ("clicked", "refused", "blocked")
        ],
        "refused_final": [
            c.get("text")
            for c in (wd.get("clicks") or [])
            if c.get("action") == "refused"
        ],
        "final_clicks": 0,
        "form_reached": bool(
            wd.get("reached_contact") or wd.get("contact_page_present")
        ),
        "workday_metrics": wd.get("metrics"),
    }


async def run_fast_fill_async(
    url: str,
    *,
    test_mode: bool = True,
    job_id: str | None = None,
    resume_path: Path | str | None = None,
    job_title: str | None = None,
    headed: bool | None = None,
    headless: bool | None = None,
    screenshot: bool | Path | None = None,
    max_entry_clicks: int = 3,
    flash_leftovers: bool = False,
    hold_seconds: int | None = None,
    captcha_wait: bool | None = None,
    captcha_timeout_s: float = DEFAULT_CAPTCHA_TIMEOUT_S,
    refill_passes: int = 0,
    refill_wait_enter: bool | None = None,
    fill_pause: bool | None = None,
    out: Path | str | None = None,
) -> dict:
    """Async orchestrator. Default test_mode=True uses dummy data; never submits.

    test_mode=False (dashboard Test Mode OFF): real profile.json + tailored resume
    when ``job_id`` / ``resume_path`` resolve — still never submits.

    Pass headed=True for a visible browser (preferred for live demos).
    Default when neither headed/headless is passed: headless (batch-safe).

    hold_seconds: keep browser open after fill for human review (None =
    DEFAULT_HEADED_HOLD_SECONDS when headed, else 0). Use --hold-open for
    indefinite hold until Ctrl+C / browser closed, or --hold-seconds N.

    captcha_wait: headed default ON — pause for human CAPTCHA solve (Enter),
    then continue same session. Headless always BLOCKED (cannot solve).

    fill_pause: headed default ON — in-page Pause/Continue overlay. Disable with
    False / ``--no-fill-pause`` / ``FASTFILL_FILL_PAUSE=0``.

    refill_passes: after first fill+screenshot, re-run leftover/Flash fill on
    the SAME page up to N times (cycle headed default 2–3) without closing.
    refill_wait_enter: default False — auto-loop immediately. Pass True / CLI
    --refill-wait-enter only when a human explicitly wants a pause between passes.
    CAPTCHA still pauses for human Enter when headed.

    flash_leftovers=False (default): 0-LLM only; leftovers listed for handoff.
    flash_leftovers=True: after Layer 0/1, optionally call thin Flash/Skyvern
    for leftover fields only (never submit).
    """
    from playwright.async_api import async_playwright

    import os as _os

    from field_map import (  # noqa: PLC0415
        assert_dummy_resume_path,
        assert_not_real_profile_env,
        assert_real_resume_path,
        is_real_profile_mode,
    )
    from run_identity import (  # noqa: PLC0415
        apply_job_title_to_values,
        prepare_dummy_run,
        prepare_real_run,
    )

    use_real = (not test_mode) or is_real_profile_mode()
    # Loop-entry dummy-only guard (defense in depth): a gateway / free-pool LLM
    # base (OmniRoute / OpenRouter / any non-DeepSeek-direct) may be used only for
    # dummy runs. flash_leftovers._post_chat_completion also enforces this per
    # call; failing here stops a real-profile run before any field is touched.
    try:
        from llm_config import is_gateway_base, resolve_base_model  # noqa: PLC0415

        _gw_base, _ = resolve_base_model()
        if use_real and is_gateway_base(_gw_base):
            raise RuntimeError(
                "gateway/free-pool LLM base refused for a real-profile run: "
                "real PII must use DeepSeek direct (unset OPENAI_COMPATIBLE_API_BASE)"
            )
    except ImportError:
        pass
    if use_real:
        if test_mode:
            raise RuntimeError("conflict: test_mode=True but real-profile env set")
        identity = prepare_real_run(
            job_id=job_id,
            resume_path=resume_path,
            job_title=job_title,
        )
        assert_real_resume_path(identity.resume_pdf)
    else:
        _os.environ.pop("FASTFILL_REAL_PROFILE", None)
        _os.environ["FASTFILL_REAL_PROFILE"] = "0"
        assert_not_real_profile_env()
        identity = prepare_dummy_run(compile_pdf=True)
        # Test Mode: never attach job-scoped/tailored PDFs — dummy fixture only.
        # (Dashboard also omits --resume-path in test_mode.)
        assert_dummy_resume_path(identity.resume_pdf)
    use_headless = _resolve_headless(headed=headed, headless=headless)
    is_headed = not use_headless
    hold_sec = _resolve_hold_seconds(
        hold_seconds=hold_seconds, headed=is_headed
    )
    do_captcha_wait = resolve_captcha_wait(
        headed=is_headed, captcha_wait=captcha_wait
    )
    do_fill_pause = resolve_fill_pause(headed=is_headed, fill_pause=fill_pause)
    # Auto-loop by default — never block mid-pipeline on Enter for School/salary.
    # Only --refill-wait-enter opts into human babysitting between passes.
    do_refill_wait = resolve_refill_wait_enter(refill_wait_enter)

    is_dummy = use_real is not True
    if not is_dummy:
        _tag = ""
    else:
        _tag = str(identity.email).split("@", 1)[0].rsplit("+", 1)[-1]
        if "+" not in str(identity.email).split("@", 1)[0] or _tag.isdigit():
            raise RuntimeError(f"refuse sequential/reused email: {identity.email!r}")
        if not identity.compiled:
            raise RuntimeError(f"refuse uncompiled resume for {identity.email!r}")
    values = dict(identity.values)
    # Job title → APPLYING_FOR (CLI / env / dashboard); dummy keeps fallback if unset.
    if not job_title and job_id:
        try:
            _jobs = json.loads((ROOT / "jobs.json").read_text(encoding="utf-8"))
            for _j in _jobs.get("jobs") or []:
                if _j.get("id") == job_id:
                    job_title = (_j.get("title") or "").strip() or None
                    break
        except Exception:
            pass
    apply_job_title_to_values(values, job_title)
    resolved_apartment = resolve_address_for_resume(
        identity.resume_pdf,
        fallback_location=(_os.environ.get("FASTFILL_JOB_LOCATION") or "").strip(),
    )
    apply_resolved_address(values, resolved_apartment)
    if values.get(ADDRESS_LINE1):
        values[ADDRESS_LINE1] = _street_line(str(values[ADDRESS_LINE1]))
    # Per-site ATS password via web_keys (never empty PASSWORD; never log secrets).
    try:
        from urllib.parse import urlparse as _urlparse

        from web_keys import company_from_host, ensure_password_for_company

        _host = (_urlparse(url).hostname or "").strip().lower() or None
        _company = ""
        if job_id:
            try:
                _jobs = json.loads((ROOT / "jobs.json").read_text(encoding="utf-8"))
                for _j in _jobs.get("jobs") or []:
                    if _j.get("id") == job_id:
                        _company = str(_j.get("company") or "").strip()
                        break
            except Exception:
                pass
        if not _company:
            _company = company_from_host(_host)
        ensure_password_for_company(
            _company,
            values,
            host=_host,
            email=str(values.get(EMAIL) or identity.email or ""),
        )
    except Exception:
        # FILL-011: never inject dummy password into real-profile fills
        if is_dummy and not (values.get(PASSWORD) or "").strip():
            from field_map import DUMMY_PROFILE as _DP

            _fallback = (_DP.get("account") or {}).get("password") or "TestDummy!2026x"
            values[PASSWORD] = _fallback
            values[PASSWORD_CONFIRM] = _fallback
        elif not is_dummy and not (values.get(PASSWORD) or "").strip():
            values["_web_keys_password_error"] = (
                "real_mode_refuses_dummy_password_fallback"
            )
    print(
        f"[identity] test_mode={test_mode} dummy={is_dummy} email={identity.email} "
        f"compiled={identity.compiled} pdf={identity.resume_pdf}",
        flush=True,
    )
    platform = detect_platform(url)

    t0 = time.time()
    report: dict[str, Any] = {
        "url": url,
        "platform": platform,
        "coverage_path": coverage_path_for(platform),
        "universal_attempt": True,
        "test_mode": test_mode,
        "dummy": is_dummy,
        # Real mode uses SHARED_FILL_POLICY for EEO/work-auth/screening;
        # contact+education stay unique to the profile.
        "policy_overlay": (
            "shared_policy" if not is_dummy else "shared_policy_dummy_unique"
        ),
        "never_submit": True,
        "submit_clicked": False,
        "headed": not use_headless,
        "headless": use_headless,
        "hold_seconds": hold_sec,
        "captcha_wait": do_captcha_wait,
        "captcha_timeout_s": float(captcha_timeout_s),
        "fill_pause_enabled": do_fill_pause,
        "refill_passes": int(refill_passes),
        "refill_wait_enter": do_refill_wait,
        "identity_email": identity.email,
        "email": identity.email,
        "email_alias": identity.email_alias,
        "alias_token": identity.alias_token,
        "resume_pdf": str(identity.resume_pdf),
        "resume_compiled": identity.compiled,
        "address_source": "synthetic_bank_from_resume_city",
        "address_city": resolved_apartment["city"],
        "address_state": resolved_apartment["state"],
        # Composed type→value for Flash handoff / reclaim (shared + unique).
        "fill_values": dict(values),
        "parity_gaps": getattr(identity, "parity_gaps", None),
        "entry_prepass": None,
        "filled": [],
        "leftovers": [],
        "errors": [],
        "blocker": None,
        "extracted_count": 0,
        "flash_called": False,
        "flash_leftovers_requested": bool(flash_leftovers),
        "flash_note": (
            "leftovers only — pass flash_leftovers=True / --flash-leftovers to invoke "
            "thin DeepSeek-V4-Flash handoff (default OFF)"
        ),
        "pages_seen": [],
        "advanced_count": 0,
        "stuck_on_same_page": False,
        "page_fingerprint_before": None,
        "page_fingerprint_after": None,
    }

    # Per-field attempt tracking → cycle_dir/field_attempts.jsonl + UNFILLABLE_AFTER_2.md
    report["unfillable_after_2"] = False
    _attach_field_attempt_log(report, out=out, screenshot=screenshot)
    _attach_fill_step_log(report, out=out, screenshot=screenshot)
    note_step(
        report,
        action="run_start",
        reason=f"platform={platform} headless={use_headless} refill={refill_passes}",
        via="fast_fill",
    )

    # Hard cap (CHR-007/008): flock around busy-check + launch. Kill orphans
    # only — never kill-all of hold/CAPTCHA review windows; refuse instead.
    async with async_playwright() as p:
        launch_kwargs: dict[str, Any] = {
            "headless": use_headless,
            # Visible demos: slower actions so multipage Workday/GH flows are watchable
            "slow_mo": 200 if not use_headless else 0,
        }
        try:
            from browser_hygiene import chromium_launch_hygiene_kwargs

            launch_kwargs.update(chromium_launch_hygiene_kwargs())
        except Exception:
            pass
        exe = resolve_playwright_chromium_executable()
        if exe:
            launch_kwargs["executable_path"] = exe
            report["chromium_executable"] = exe

        def _headed_prelaunch_gate() -> dict[str, Any] | None:
            if (os.environ.get("FASTFILL_NO_KILL_CHROME") or "").strip() not in (
                "1",
                "true",
                "yes",
            ):
                killed = kill_orphan_chrome_mains()
                if killed:
                    print(
                        f"[chromium] killed orphan Chrome mains (not hold/CAPTCHA): {killed}",
                        flush=True,
                    )
            return refuse_headed_if_chrome_busy()

        try:
            if is_headed:
                with _headed_chrome_launch_lock():
                    cap_hit = _headed_prelaunch_gate()
                    if cap_hit:
                        report["blocker"] = cap_hit["blocker"]
                        report["headed_cap"] = cap_hit.get("headed_cap")
                        report["chromium_fail_fast"] = True
                        report["errors"].append(
                            {
                                "headed_cap": (cap_hit.get("headed_cap") or {}).get(
                                    "message"
                                )
                            }
                        )
                        report["elapsed_seconds"] = round(time.time() - t0, 2)
                        cap_msg = (cap_hit.get("headed_cap") or {}).get("message") or ""
                        print(
                            "\n"
                            "╔══════════════════════════════════════════════════════════════════╗\n"
                            "║  HEADED CAP — refused to launch another Chrome-for-Testing       ║\n"
                            "║  Existing fill/hold window kept (no kill-all). Wait for slot, OR:║\n"
                            "║    export FASTFILL_FORCE_HEADED=1     (bypass cap — sparingly)  ║\n"
                            "╚══════════════════════════════════════════════════════════════════╝\n"
                            f"[chromium] headed_cap REFUSED: {cap_msg}",
                            flush=True,
                        )
                        return _finalize(report, close_step_log=True)
                    browser = await p.chromium.launch(**launch_kwargs)
            else:
                browser = await p.chromium.launch(**launch_kwargs)
        except Exception as e:
            msg = str(e)
            report["errors"].append({"chromium_launch": msg[:400]})
            report["blocker"] = "chromium_missing"
            report["chromium_fail_fast"] = True
            report["elapsed_seconds"] = round(time.time() - t0, 2)
            print(f"[chromium] launch FAILED (fail-fast, do not retry×3): {msg[:200]}", flush=True)
            return _finalize(report, close_step_log=True)
        if is_headed:
            try:
                from captcha_pause import bring_chrome_testing_to_front

                bring_chrome_testing_to_front(loud=True)
            except Exception:
                pass
            print(
                "[browser] Headed Chromium launched — look for "
                "'Google Chrome for Testing' window (may be behind other apps).\n"
                "[browser] CHR3-005: fill CfT shares Dock icon with UI/PartyRock — "
                "focus fill via: dashboard/launch_dashboard.sh --focus-fill "
                "(never tell application … activate).",
                flush=True,
            )
        note_step(
            report,
            action="browser_launch",
            reason="headed" if is_headed else "headless",
            via="playwright",
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        if do_fill_pause:
            try:
                await install_fill_pause_on_context(context)
            except Exception as e:
                report.setdefault("errors", []).append(
                    {"fill_pause_init": str(e)[:120]}
                )
        page = await context.new_page()
        report["_page"] = page
        if do_fill_pause:
            try:
                await ensure_fill_pause_ready(page, report)
            except Exception as e:
                report.setdefault("errors", []).append(
                    {"fill_pause_inject": str(e)[:120]}
                )
        try:
            print(f"[browser] Opening job URL…", flush=True)
            note_step(
                report,
                action="navigate",
                reason=(url or "")[:160],
                via="page.goto",
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            await asyncio.sleep(2.0 if platform == "workday" else 1.5)
            if do_fill_pause:
                try:
                    await ensure_fill_pause_ready(page, report)
                except Exception:
                    pass
        except Exception as e:
            report["errors"].append({"goto": str(e)[:300]})
            note_step(
                report,
                action="navigate",
                reason=f"FAILED: {str(e)[:120]}",
                via="page.goto",
            )
            report["elapsed_seconds"] = round(time.time() - t0, 2)
            report.pop("_page", None)
            await browser.close()
            return _finalize(report, close_step_log=True)

        try:
            report["cookie_dismiss"] = await dismiss_cookie_banners(page)
            cd = report.get("cookie_dismiss") or {}
            note_step(
                report,
                action="cookie_dismiss",
                reason=(
                    f"clicked={cd.get('clicked') or []} "
                    f"skipped_captcha={cd.get('skipped_captcha')}"
                    if isinstance(cd, dict)
                    else str(cd)[:80]
                ),
                via="dismiss_cookie_banners",
            )
        except Exception as e:
            report["cookie_dismiss"] = {"error": str(e)[:120]}
            note_step(
                report,
                action="cookie_dismiss",
                reason=f"error: {str(e)[:80]}",
                via="dismiss_cookie_banners",
            )

        # Baseline step fingerprint (stuck detection compares against ADVANCE after)
        try:
            from page_progress import capture_step_fingerprint, record_page_seen

            start_fp = await capture_step_fingerprint(page)
            report["page_fingerprint_before"] = start_fp["fingerprint"]
            record_page_seen(
                report,
                start_fp["fingerprint"],
                meta={
                    "url": start_fp["url"],
                    "title": start_fp["title"],
                    "step_hint": start_fp["step_hint"],
                },
            )
        except Exception as e:
            report.setdefault("errors", []).append({"start_fingerprint": str(e)[:120]})

        try:
            title = await page.title()
            body_snip = await page.evaluate(
                "() => (document.body && document.body.innerText || '').slice(0, 2500)"
            )
            blocker = _detect_blocker(body_snip, title, page.url)
            # Prefer interactive widget over footer-only "Protected by reCAPTCHA"
            if await page_shows_interactive_captcha(page):
                blocker = "captcha"
            if blocker:
                if blocker in CAPTCHA_BLOCKERS:
                    outcome = await handle_captcha_blocker(
                        page,
                        report,
                        blocker,
                        headed=is_headed,
                        captcha_wait=do_captcha_wait,
                        timeout_s=captcha_timeout_s,
                    )
                    if outcome == "continued":
                        blocker = None
                    else:
                        # Timed out / wait disabled — keep blocker. Headed: hold
                        # browser open for review (never kill mid-CAPTCHA wait;
                        # wait already finished). Do not auto-solve.
                        report["blocker"] = blocker
                        report["elapsed_seconds"] = round(time.time() - t0, 2)
                        report["leftovers"].append(
                            {
                                "reason": f"blocker:{blocker}",
                                "flash_candidate": False,
                                "label": "page_blocked",
                            }
                        )
                        if screenshot:
                            await _maybe_shot(page, screenshot, report)
                        if hold_is_active(hold_sec) and is_headed:
                            try:
                                await drain_pause_before_close(page, report)
                            except Exception:
                                pass
                            cap_hold = (
                                HOLD_INDEFINITE
                                if hold_sec < 0
                                else min(hold_sec, 600)
                            )
                            report["headed_hold_ms"] = (
                                None if cap_hold < 0 else cap_hold * 1000
                            )
                            print(
                                "[captcha] blocker unresolved — holding browser "
                                "for review (never solved auto)…",
                                flush=True,
                            )
                            await _hold_for_review(
                                seconds=cap_hold, report=report, browser=browser
                            )
                        await browser.close()
                        return _finalize(report, close_step_log=True)
                else:
                    report["blocker"] = blocker
                    report["elapsed_seconds"] = round(time.time() - t0, 2)
                    report["leftovers"].append(
                        {
                            "reason": f"blocker:{blocker}",
                            "flash_candidate": False,
                            "label": "page_blocked",
                        }
                    )
                    if screenshot:
                        await _maybe_shot(page, screenshot, report)
                    await browser.close()
                    return _finalize(report, close_step_log=True)
        except Exception:
            pass

        if platform == "workday":
            from workday_selectors import workday_two_phase_on_page

            wd = await workday_two_phase_on_page(
                page,
                values,
                click_create_account=True,
                do_apply_clicks=True,
                resume_pdf=identity.resume_pdf,
                step_report=report,
            )
            if do_fill_pause:
                try:
                    await ensure_fill_pause_ready(page, report)
                except Exception:
                    pass
            _merge_workday_into_report(report, wd, values)
            report["errors"].extend(wd.get("errors") or [])

            if wd.get("reached_contact") and report.get("blocker") not in (
                "captcha",
                "akamai",
                "cloudflare",
            ):
                already = {
                    f.get("type")
                    for f in report["filled"]
                    if f.get("ok") is not False and f.get("type")
                }
                pack_filled = await apply_selector_pack(page, platform, values)
                for f in pack_filled:
                    if f.get("type") not in already:
                        report["filled"].append(f)
                        if f.get("type"):
                            already.add(f["type"])
                ext_filled, leftovers, errors, extracted_count = await fill_from_extract(
                    page, values, already, platform=platform
                )
                report["filled"].extend(ext_filled)
                report["leftovers"] = [
                    u
                    for u in report["leftovers"]
                    if u.get("reason") != "contact_page_absent"
                ]
                report["leftovers"].extend(leftovers)
                report["errors"].extend(errors)
                report["extracted_count"] = extracted_count

            # Workday CAPTCHA mid-flow: pause for human (headed) then CONTINUE
            # same session — re-run multiphase fill after challenge clears.
            if report.get("blocker") in CAPTCHA_BLOCKERS:
                outcome = await handle_captcha_blocker(
                    page,
                    report,
                    str(report["blocker"]),
                    headed=is_headed,
                    captcha_wait=do_captcha_wait,
                    timeout_s=captcha_timeout_s,
                )
                if outcome == "continued" and not report.get("blocker"):
                    report["captcha_resume_workday"] = True
                    try:
                        wd2 = await workday_two_phase_on_page(
                            page,
                            values,
                            click_create_account=True,
                            do_apply_clicks=True,
                            resume_pdf=identity.resume_pdf,
                            step_report=report,
                        )
                        if do_fill_pause:
                            try:
                                await ensure_fill_pause_ready(page, report)
                            except Exception:
                                pass
                        _merge_workday_into_report(report, wd2, values)
                        report["errors"].extend(wd2.get("errors") or [])
                    except Exception as e:
                        report.setdefault("errors", []).append(
                            {"resume_workday_after_captcha": str(e)[:160]}
                        )
                    try:
                        ru = await ensure_resume_uploaded(page, values, report)
                        report.setdefault("resume_upload_workday", ru)
                    except Exception as e:
                        report.setdefault("errors", []).append(
                            {"resume_after_captcha": str(e)[:120]}
                        )
        else:
            # Thin ATS + unknown: allow more entry clicks (Apply → Start → …)
            thin = platform in (
                "unknown",
                "smartrecruiters",
                "workable",
                "bamboohr",
                "recruitee",
                "personio",
                "jobvite",
                "taleo",
                "successfactors",
                "dayforce",
                "ukg",
                "oracle",
                "rippling",
                "applytojob",
                "breezy",
                "jobscore",
                "gem",
                "dover",
                "phenom",
            )
            entry_clicks = max(max_entry_clicks, 5) if thin else max_entry_clicks
            try:
                prepass = await entry_prepass(page, max_clicks=entry_clicks, report=report)
            except Exception as e:
                # Human closed Chrome mid-entry, or site crashed the page.
                if "closed" in str(e).lower() or "TargetClosed" in type(e).__name__:
                    report["blocker"] = "browser_closed_during_entry"
                    report["errors"].append({"entry_prepass": str(e)[:200]})
                    report["entry_prepass"] = {"form_reached": False, "error": "page_closed"}
                    if hold_sec > 0:
                        # Nothing to show — skip long hold
                        pass
                    await browser.close()
                    report["elapsed_seconds"] = round(time.time() - t0, 2)
                    return _finalize(report, close_step_log=True)
                raise
            # Follow Apply into a new tab when the career site opens one
            if prepass.get("page") is not None:
                page = prepass["page"]
            report["entry_prepass"] = {
                "clicked": prepass.get("clicked"),
                "refused_final": prepass.get("final_seen"),
                "final_clicks": prepass.get("final_clicks", 0),
                "form_reached": (prepass.get("form") or {}).get("reached"),
                "time_to_form_seconds": prepass.get("time_to_form_seconds"),
                "buttons_seen_count": prepass.get("buttons_seen_count"),
                "switched_tab": bool(prepass.get("switched_tab")),
                "spa_wait": prepass.get("spa_wait"),
                "fill_kind": (prepass.get("form") or {}).get("fill_kind"),
                "fill_url": (prepass.get("form") or {}).get("fill_url"),
            }
            assert prepass.get("final_clicks", 0) == 0, "FINAL click leaked in entry prepass"

            # Ashby: Apply may leave Overview active — force /application URL.
            if platform == "ashby" or "ashbyhq.com" in (page.url or "").lower():
                ashby_nav = await ensure_ashby_application_url(page)
                report["ashby_application_nav"] = ashby_nav
                if ashby_nav.get("navigated"):
                    platform = "ashby"
                    report["platform"] = "ashby"
                    report["coverage_path"] = coverage_path_for("ashby")
                    # Re-probe form on the application document
                    try:
                        form2 = await form_fields_visible_anywhere(page)
                        report["entry_prepass"]["form_reached"] = bool(form2.get("reached"))
                        report["entry_prepass"]["fill_kind"] = form2.get("fill_kind")
                        report["entry_prepass"]["fill_url"] = form2.get("fill_url") or page.url
                        if form2.get("fill_target") is not None:
                            prepass["fill_target"] = form2.get("fill_target")
                    except Exception as e:
                        report.setdefault("errors", []).append(
                            {"ashby_reprobe": str(e)[:120]}
                        )

            try:
                title = await page.title()
                body_snip = await page.evaluate(
                    "() => (document.body && document.body.innerText || '').slice(0, 2500)"
                )
                blocker = _detect_blocker(body_snip, title, page.url)
                if await page_shows_interactive_captcha(page):
                    blocker = "captcha"
                if blocker in CAPTCHA_BLOCKERS:
                    outcome = await handle_captcha_blocker(
                        page,
                        report,
                        blocker,
                        headed=is_headed,
                        captcha_wait=do_captcha_wait,
                        timeout_s=captcha_timeout_s,
                    )
                    if outcome != "continued":
                        report["blocker"] = blocker
                elif blocker:
                    report["blocker"] = blocker
            except Exception:
                pass

            # Prefer iframe/SPA fill target discovered during entry prepass
            fill_ctx = prepass.get("fill_target") or page
            # White-label hosts (RockCo) start as unknown but Apply lands on icims.com
            fill_url_hint = (
                report["entry_prepass"].get("fill_url")
                or getattr(fill_ctx, "url", None)
                or ""
            )
            detected_from_frame = detect_platform(str(fill_url_hint))
            if (
                platform in ("unknown",)
                and detected_from_frame not in ("unknown", "")
            ):
                platform = detected_from_frame
                report["platform"] = platform
                report["platform_from_fill_url"] = True
                report["coverage_path"] = coverage_path_for(platform)

            # Auth gate BEFORE selector pack when Sign-in / login wall is up
            # (Stripe dashboard.stripe.com/login, MyGreenhouse, iCIMS /login).
            # Dummy runs must click Create account — never fill GH #email on Sign in.
            skip_app_pack = False
            auth_gate_ran = False
            try:
                from iframe_ctx import (
                    consume_create_account_sentinel,
                    run_auth_gate_before_pack,
                )

                force_ca = consume_create_account_sentinel()
                if force_ca:
                    report["force_create_account"] = True
                auth_pre = await run_auth_gate_before_pack(
                    page,
                    values,
                    fill_target=fill_ctx,
                    max_rounds=2,
                    force=force_ca,
                )
                report["auth_gate"] = {
                    k: v
                    for k, v in auth_pre.items()
                    if k != "fill_target"
                }
                if auth_pre.get("ran"):
                    auth_gate_ran = True
                    note_step(
                        report,
                        action="auth_gate",
                        via="run_auth_gate_before_pack",
                        reason=(
                            f"wall={auth_pre.get('is_sign_in_wall')} "
                            f"skip_pack={auth_pre.get('skip_app_pack')} "
                            f"ca={((auth_pre.get('create_account') or {}).get('clicked') or {})}"
                        ),
                    )
                if auth_pre.get("fill_target") is not None:
                    fill_ctx = auth_pre["fill_target"]
                skip_app_pack = bool(auth_pre.get("skip_app_pack"))
                auth = auth_pre.get("iframe_login") or {}
                for f in auth.get("filled") or []:
                    if f.get("ok"):
                        report["filled"].append(f)
                report["iframe_login"] = auth if auth else report.get("iframe_login")
                if auth_pre.get("blocker") and not report.get("blocker"):
                    report["blocker"] = auth_pre["blocker"]
                if auth.get("blocker") and not report.get("blocker"):
                    report["blocker"] = auth["blocker"]
                for blk in (
                    auth_pre.get("blocker"),
                    auth.get("blocker"),
                ):
                    if blk in CAPTCHA_BLOCKERS:
                        outcome = await handle_captcha_blocker(
                            page,
                            report,
                            str(blk),
                            headed=is_headed,
                            captcha_wait=do_captcha_wait,
                            timeout_s=captcha_timeout_s,
                        )
                        if outcome == "continued":
                            if report.get("iframe_login"):
                                report["iframe_login"]["blocker"] = None
                                report["iframe_login"]["captcha_human_solved"] = True
                            if report.get("auth_gate"):
                                report["auth_gate"]["blocker"] = None
                        break
            except Exception as e:
                report.setdefault("errors", []).append(
                    {"auth_gate_before_pack": str(e)[:160]}
                )

            # Resume-first: upload/parse the resume BEFORE the deterministic fill
            # layers so a parsing ATS (Ashby, some Greenhouse/Workable) populates
            # fields for us, and so a later parse can't wipe values we already
            # typed (the reason Ashby/GH previously needed a post-upload
            # re-assert). Idempotent — skips when already verified or no file field
            # is present, and the existing post-extract ensure_resume_uploaded call
            # then no-ops or completes it (e.g. when the field lives in an apply
            # iframe that only resolves after the pack).
            # Skip on pure Sign-in walls — pack would thrash #email on Stripe login.
            pack_filled: list[dict] = []
            if (
                not skip_app_pack
                and report.get("blocker")
                not in (
                    "captcha",
                    "email_verify",
                    "akamai",
                    "cloudflare",
                    "login_wall",
                    "sign_in_only_no_create",
                )
            ):
                try:
                    await ensure_resume_uploaded(fill_ctx, values, report)
                except Exception as e:
                    report.setdefault("errors", []).append(
                        {"ensure_resume_first": str(e)[:160]}
                    )

                if fill_ctx is page:
                    pack_filled, fill_ctx = await apply_selector_pack_anywhere(
                        page, platform, values, report=report
                    )
                else:
                    pack_filled = await apply_selector_pack(
                        fill_ctx, platform, values, report=report
                    )
                    if not pack_filled:
                        # Hybrid hosts: also try top page
                        more = await apply_selector_pack(
                            page, platform, values, report=report
                        )
                        pack_filled.extend(more)
                report["filled"].extend(pack_filled)
                emit_filled_rows_as_steps(report, phase="selector_pack")
            elif skip_app_pack:
                report["selector_pack_skipped"] = "sign_in_wall"
                note_step(
                    report,
                    action="selector_pack_skipped",
                    via="auth_gate",
                    reason="pure_sign_in_wall",
                )
            # Incremental experience after pack (same-run Flash grounding);
            # selector_stats + demotion happen once at finalize via learn_from_report.
            try:
                from continuous_learn import append_experience
                from urllib.parse import urlparse as _urlparse

                _host = (_urlparse(url).netloc or "").lower()
                if _host.startswith("www."):
                    _host = _host[4:]
                _tm = bool(report.get("test_mode", report.get("dummy", True)))
                _pack_rows = [
                    {
                        "platform": platform,
                        "host": _host,
                        "selector": f.get("selector"),
                        "type": f.get("type"),
                        "label": f.get("label"),
                        "value": f.get("value") or f.get("readback"),
                        "verified": f.get("verified", f.get("ok")),
                        "ok": f.get("ok", f.get("verified")),
                        "via": f.get("via") or "selector_pack",
                    }
                    for f in pack_filled
                    if isinstance(f, dict) and (f.get("ok") or f.get("verified"))
                ]
                if _pack_rows:
                    append_experience(_pack_rows, test_mode=_tm)
                    report["pack_learn_appended"] = len(_pack_rows)
                    # Fingerprints so learn_from_report does not double-append
                    fps = report.setdefault("_cl_experience_fps", set())
                    for _r in _pack_rows:
                        fps.add(
                            (
                                str(_r.get("platform") or ""),
                                str(_r.get("host") or ""),
                                str(_r.get("selector") or ""),
                                str(_r.get("type") or ""),
                            )
                        )
            except Exception as e:
                report.setdefault("errors", []).append({"pack_learn": str(e)[:80]})
            if (
                not skip_app_pack
                and platform in _MIDTIER_COMBO_PLATFORMS
                and not report.get("blocker")
            ):
                try:
                    mt_rows = await sweep_midtier_policy_comboboxes(
                        fill_ctx, values, report=report
                    )
                    if not mt_rows and fill_ctx is not page:
                        mt_rows = await sweep_midtier_policy_comboboxes(
                            page, values, report=report
                        )
                    report["midtier_combo_sweep"] = [
                        {
                            k: r.get(k)
                            for k in ("type", "label", "ok", "verified", "readback", "reason")
                            if k in r
                        }
                        for r in (mt_rows or [])
                    ]
                    report["filled"].extend(
                        [r for r in (mt_rows or []) if r.get("verified")]
                    )
                except Exception as e:
                    report.setdefault("errors", []).append(
                        {"midtier_combo_sweep": str(e)[:160]}
                    )
            report["fill_context"] = {
                "kind": report["entry_prepass"].get("fill_kind") or (
                    "frame" if fill_ctx is not page else "page"
                ),
                "url": report["entry_prepass"].get("fill_url"),
            }
            already = {
                f.get("type")
                for f in pack_filled
                if f.get("ok") is not False and f.get("type")
            }
            for f in report.get("filled") or []:
                if f.get("ok") is not False and f.get("type"):
                    already.add(f["type"])

            # Late iframe auth (only if pre-pack gate did not already handle login)
            if not auth_gate_ran and not skip_app_pack:
                try:
                    from iframe_ctx import continue_iframe_login

                    auth = await continue_iframe_login(
                        page, values, fill_target=fill_ctx, max_rounds=2
                    )
                    report["iframe_login"] = {
                        k: v
                        for k, v in auth.items()
                        if k != "fill_target"
                    }
                    for f in auth.get("filled") or []:
                        if f.get("ok") and f.get("type") and f.get("type") not in already:
                            report["filled"].append(f)
                            already.add(f["type"])
                        elif f.get("ok") and f.get("type") in already:
                            # Prefer verified iframe_login row when pack already filled
                            report["filled"].append(f)
                    if auth.get("blocker") and not report.get("blocker"):
                        report["blocker"] = auth["blocker"]
                    if auth.get("blocker") in CAPTCHA_BLOCKERS:
                        outcome = await handle_captcha_blocker(
                            page,
                            report,
                            str(auth["blocker"]),
                            headed=is_headed,
                            captcha_wait=do_captcha_wait,
                            timeout_s=captcha_timeout_s,
                        )
                        if outcome == "continued":
                            auth["blocker"] = None
                            if report.get("iframe_login"):
                                report["iframe_login"]["blocker"] = None
                                report["iframe_login"]["captcha_human_solved"] = True
                    if auth.get("fill_target") is not None:
                        fill_ctx = auth["fill_target"]
                        report["fill_context"]["url"] = (auth.get("final_url") or "")[:200]
                        report["fill_context"]["post_login"] = True
                    # After auth ADVANCE, re-run pack on new form (profile fields)
                    if auth.get("reached_app_fields") or (
                        auth.get("ran") and not auth.get("blocker")
                    ):
                        more_pack = await apply_selector_pack(fill_ctx, platform, values)
                        for f in more_pack:
                            if f.get("type") and f.get("type") in already:
                                continue
                            if f.get("ok"):
                                report["filled"].append(f)
                                if f.get("type"):
                                    already.add(f["type"])
                except Exception as e:
                    report.setdefault("errors", []).append(
                        {"iframe_login": str(e)[:160]}
                    )
            elif auth_gate_ran and not skip_app_pack:
                # Pre-pack gate reached app fields — pack may still need a pass
                auth = report.get("iframe_login") or {}
                if auth.get("reached_app_fields") or (
                    auth.get("ran") and not auth.get("blocker") and not pack_filled
                ):
                    try:
                        more_pack = await apply_selector_pack(
                            fill_ctx, platform, values, report=report
                        )
                        for f in more_pack:
                            if f.get("type") and f.get("type") in already:
                                continue
                            if f.get("ok"):
                                report["filled"].append(f)
                                if f.get("type"):
                                    already.add(f["type"])
                    except Exception as e:
                        report.setdefault("errors", []).append(
                            {"post_auth_pack": str(e)[:160]}
                        )

            try:
                from record_replay import apply_replay_map

                # Overlay sanitized type→value hints from prior dummy runs
                # (policy / select leftovers). Contact types stay on DUMMY_PROFILE.
                try:
                    from continuous_learn import type_value_replay_map
                    from urllib.parse import urlparse as _up

                    _h = (_up(url).netloc or "").lower()
                    if _h.startswith("www."):
                        _h = _h[4:]
                    _tm = bool(report.get("test_mode", report.get("dummy", True)))
                    for _ft, _hint in type_value_replay_map(
                        platform, host=_h, test_mode=_tm
                    ).items():
                        if _ft and _hint and not values.get(_ft):
                            values[_ft] = _hint
                except Exception:
                    pass

                # Prefer fill_ctx (apply iframe when present) so replay hits the form.
                replay_filled = await apply_replay_map(fill_ctx, url, platform, values)
                for f in replay_filled:
                    if f.get("type") and f.get("type") in already:
                        continue
                    report["filled"].append(f)
                    if f.get("type"):
                        already.add(f["type"])
                report["replay_filled_count"] = len(
                    [f for f in replay_filled if f.get("ok")]
                )
            except Exception as e:
                report.setdefault("errors", []).append({"replay": str(e)[:160]})

            # Skip heavy extract when still blocked (after optional captcha wait)
            if report.get("blocker") in (
                "captcha",
                "email_verify",
                "akamai",
                "cloudflare",
                "login_wall",
                "sign_in_only_no_create",
            ):
                report["extracted_count"] = report.get("extracted_count") or 0
                report["leftovers"].append(
                    {
                        "reason": f"blocker:{report['blocker']}",
                        "label": "auth_gate_blocked",
                        "flash_candidate": False,
                        "iframe_login": report.get("iframe_login"),
                        "auth_gate": report.get("auth_gate"),
                    }
                )
            else:
                # Deterministic resume upload early (before/alongside extract)
                try:
                    await ensure_resume_uploaded(fill_ctx, values, report)
                except Exception as e:
                    report.setdefault("errors", []).append(
                        {"ensure_resume_early": str(e)[:160]}
                    )
                # Ashby resume parse can wipe contact/LinkedIn — re-assert after upload.
                if platform == "ashby":
                    try:
                        re_rows = await reassert_ashby_contact_after_resume(
                            fill_ctx, values
                        )
                        if not re_rows and fill_ctx is not page:
                            re_rows = await reassert_ashby_contact_after_resume(
                                page, values
                            )
                        report["ashby_post_resume_reassert"] = [
                            {
                                k: r.get(k)
                                for k in (
                                    "type",
                                    "ok",
                                    "verified",
                                    "readback",
                                    "reason",
                                    "via",
                                )
                                if k in r
                            }
                            for r in (re_rows or [])
                        ]
                        _merge_ashby_reassert_rows(report, already, re_rows)
                    except Exception as e:
                        report.setdefault("errors", []).append(
                            {"ashby_post_resume_reassert": str(e)[:160]}
                        )
                elif platform == "greenhouse":
                    try:
                        if await _should_run_gh_post_resume_reassert(report):
                            re_rows = await reassert_greenhouse_contact_after_resume(
                                fill_ctx, values
                            )
                            if not re_rows and fill_ctx is not page:
                                re_rows = await reassert_greenhouse_contact_after_resume(
                                    page, values
                                )
                            report["greenhouse_post_resume_reassert"] = [
                                {
                                    k: r.get(k)
                                    for k in (
                                        "type",
                                        "ok",
                                        "verified",
                                        "readback",
                                        "reason",
                                        "via",
                                        "label",
                                    )
                                    if k in r
                                }
                                for r in (re_rows or [])
                            ]
                            _merge_greenhouse_reassert_rows(report, already, re_rows)
                        else:
                            report["greenhouse_post_resume_reassert_skipped"] = (
                                "resume_not_verified"
                            )
                    except Exception as e:
                        report.setdefault("errors", []).append(
                            {"greenhouse_post_resume_reassert": str(e)[:160]}
                        )
                ext_filled, leftovers, errors, extracted_count = await fill_from_extract(
                    fill_ctx,
                    values,
                    already,
                    platform=platform,
                    report=report,
                    pass_i=0,
                )
                # If iframe extract empty, retry top page once
                if extracted_count == 0 and fill_ctx is not page:
                    ext2, left2, err2, n2 = await fill_from_extract(
                        page,
                        values,
                        already,
                        platform=platform,
                        report=report,
                        pass_i=0,
                    )
                    if n2 > extracted_count:
                        ext_filled, leftovers, errors, extracted_count = ext2, left2, err2, n2
                        report["fill_context"]["fallback"] = "top_page_extract"
                    else:
                        errors.extend(err2)
                report["filled"].extend(ext_filled)
                report["leftovers"].extend(leftovers)
                report["errors"].extend(errors)
                report["extracted_count"] = extracted_count

                # Greenhouse: sweep Select… leftovers (visa sponsorship class)
                if platform == "greenhouse" and not report.get("blocker"):
                    try:
                        sweep_rows = await sweep_gh_unfilled_selects(
                            fill_ctx, values, report
                        )
                        if not sweep_rows and fill_ctx is not page:
                            sweep_rows = await sweep_gh_unfilled_selects(
                                page, values, report
                            )
                        report["gh_select_sweep"] = [
                            {
                                k: r.get(k)
                                for k in (
                                    "type",
                                    "label",
                                    "ok",
                                    "verified",
                                    "readback",
                                    "reason",
                                    "picked",
                                )
                                if k in r
                            }
                            for r in (sweep_rows or [])
                        ]
                        _merge_greenhouse_reassert_rows(report, already, sweep_rows)
                        # Drop leftovers only when that label was swept OK —
                        # WORK_AUTH is multi-instance (legal + without-sponsorship).
                        swept_ok_labels = {
                            re.sub(
                                r"\s+", " ", str(r.get("label") or "").lower()
                            )[:40]
                            for r in (sweep_rows or [])
                            if r.get("verified")
                            and r.get("type")
                            in {SPONSORSHIP, WORK_AUTH, "BACKGROUND_CHECK"}
                        }
                        swept_ok_types_single = {
                            r.get("type")
                            for r in (sweep_rows or [])
                            if r.get("verified")
                            and r.get("type")
                            in {SPONSORSHIP, WORK_AUTH, "BACKGROUND_CHECK"}
                        }

                        def _leftover_swept(u: dict) -> bool:
                            ut = u.get("type")
                            if ut not in swept_ok_types_single:
                                return False
                            ul = re.sub(
                                r"\s+", " ", str(u.get("label") or "").lower()
                            )[:40]
                            if ul and any(
                                ul[:30] in sk or sk[:30] in ul
                                for sk in swept_ok_labels
                            ):
                                return True
                            # No label on leftover — only drop if a single
                            # instance of that type exists on the page.
                            return ut in swept_ok_types_single and len(
                                [
                                    x
                                    for x in (sweep_rows or [])
                                    if x.get("type") == ut and x.get("verified")
                                ]
                            ) >= 1 and ut not in (WORK_AUTH, SPONSORSHIP)

                        report["leftovers"] = [
                            u
                            for u in (report.get("leftovers") or [])
                            if not _leftover_swept(u)
                        ]
                    except Exception as e:
                        report.setdefault("errors", []).append(
                            {"gh_select_sweep": str(e)[:160]}
                        )
            # Ashby Yes/No buttons + EEO/consent radios (extract routinely misses these)
            if platform == "ashby" and not report.get("blocker"):
                try:
                    ashby_filled = await fill_ashby_widgets(
                        fill_ctx, values, report=report
                    )
                    if not ashby_filled and fill_ctx is not page:
                        ashby_filled = await fill_ashby_widgets(
                            page, values, report=report
                        )
                    for f in ashby_filled:
                        if f.get("ok") and f.get("verified"):
                            ftype = f.get("type")
                            if ftype:
                                report["filled"] = [
                                    r
                                    for r in report["filled"]
                                    if not (
                                        isinstance(r, dict) and r.get("type") == ftype
                                    )
                                ]
                            report["filled"].append(f)
                            if ftype:
                                already.add(ftype)
                        elif f.get("flash_candidate") or f.get("ok") is False:
                            _merge_ashby_reassert_rows(report, already, [f])
                            if f.get("ok") is False and not f.get("flash_candidate"):
                                report.setdefault("errors", []).append(
                                    {"ashby_widget": f}
                                )
                    report["ashby_widgets_filled"] = len(
                        [f for f in ashby_filled if f.get("ok") and f.get("verified")]
                    )
                    # Drop leftovers whose type we just filled via widgets
                    filled_types_now = {
                        f.get("type")
                        for f in report["filled"]
                        if f.get("type") and is_verified_fill_row(f)
                    }
                    report["leftovers"] = [
                        u
                        for u in report["leftovers"]
                        if u.get("type") not in filled_types_now
                        and not (
                            u.get("reason") == "unclassified"
                            and any(
                                f.get("label")
                                and (u.get("label") or "")[:40].lower()
                                in (f.get("label") or "").lower()
                                for f in ashby_filled
                                if f.get("ok") and f.get("verified")
                            )
                        )
                    ]
                except Exception as e:
                    report.setdefault("errors", []).append(
                        {"ashby_widgets": str(e)[:160]}
                    )
                emit_filled_rows_as_steps(report, phase="ashby_widgets")

                # Location → zip dependency: remount-safe fill AFTER location + widgets.
                # Skip when we never reached a form (JD-only / crashed page).
                formish = bool(report.get("filled")) or int(
                    report.get("extracted_count") or 0
                ) > 0
                if formish:
                    try:
                        zip_rows = await fill_ashby_location_then_zip(fill_ctx, values)
                        if not zip_rows and fill_ctx is not page:
                            zip_rows = await fill_ashby_location_then_zip(page, values)
                        report["ashby_location_zip"] = [
                            {
                                k: r.get(k)
                                for k in (
                                    "ok",
                                    "verified",
                                    "readback",
                                    "reason",
                                    "type",
                                    "via",
                                    "error",
                                )
                                if k in r
                            }
                            for r in (zip_rows or [])
                        ]
                        # Replace any prior ADDRESS_ZIP claims with the live settle result
                        if any(r.get("type") == ADDRESS_ZIP for r in (zip_rows or [])):
                            report["filled"] = [
                                f
                                for f in report["filled"]
                                if f.get("type") != ADDRESS_ZIP
                            ]
                            already.discard(ADDRESS_ZIP)
                        for f in zip_rows or []:
                            if f.get("ok") and f.get("verified"):
                                report["filled"].append(f)
                                already.add(ADDRESS_ZIP)
                                report["leftovers"] = [
                                    u
                                    for u in report["leftovers"]
                                    if u.get("type") != ADDRESS_ZIP
                                ]
                            elif f.get("reason") == "zip_field_absent_on_form":
                                report["ashby_zip_absent"] = True
                            else:
                                report.setdefault("leftovers", []).append(
                                    {
                                        "label": f.get("label")
                                        or "What is your home zip code?",
                                        "type": ADDRESS_ZIP,
                                        "selector": f.get("selector"),
                                        "reason": f.get("reason")
                                        or "zip_readback_empty_or_placeholder",
                                        "readback": f.get("readback") or "",
                                        "error": f.get("error"),
                                        "flash_candidate": True,
                                        "via": "ashby_location_zip",
                                    }
                                )
                    except Exception as e:
                        report.setdefault("errors", []).append(
                            {"ashby_location_zip": str(e)[:160]}
                        )

            # Lever WORK_AUTH / SPONSORSHIP radios + EEO Decline selects.
            # Run even when captcha blocks extract — Utility Global left these
            # unchecked while contact pack still filled (W03 pixel FAIL_BLANK).
            if platform == "lever":
                formish = bool(report.get("filled")) or int(
                    report.get("extracted_count") or 0
                ) > 0
                if formish:
                    try:
                        lever_filled = await fill_lever_widgets(
                            fill_ctx, values, report=report
                        )
                        if not lever_filled and fill_ctx is not page:
                            lever_filled = await fill_lever_widgets(
                                page, values, report=report
                            )
                        for f in lever_filled or []:
                            if f.get("ok") and f.get("verified"):
                                ftype = f.get("type")
                                if ftype:
                                    report["filled"] = [
                                        r
                                        for r in report["filled"]
                                        if not (
                                            isinstance(r, dict) and r.get("type") == ftype
                                        )
                                    ]
                                    already.add(ftype)
                                report["filled"].append(
                                    {**f, "ok": True, "verified": True}
                                )
                            elif f.get("flash_candidate") or f.get("ok") is False:
                                report.setdefault("leftovers", []).append(
                                    {
                                        "label": (f.get("label") or f.get("type") or "")[
                                            :100
                                        ],
                                        "type": f.get("type"),
                                        "reason": f.get("reason")
                                        or "lever_widget_failed",
                                        "flash_candidate": True,
                                    }
                                )
                        report["lever_widgets_filled"] = len(
                            [
                                f
                                for f in (lever_filled or [])
                                if f.get("ok") and f.get("verified")
                            ]
                        )
                        filled_types_now = {
                            f.get("type")
                            for f in report["filled"]
                            if f.get("type") and is_verified_fill_row(f)
                        }
                        report["leftovers"] = [
                            u
                            for u in report["leftovers"]
                            if u.get("type") not in filled_types_now
                        ]
                    except Exception as e:
                        report.setdefault("errors", []).append(
                            {"lever_widgets": str(e)[:160]}
                        )

            # Honest signal when generic path ran but no fields appeared (JD-only page,
            # iframe apply, or post-Apply redirect not yet form). Still not a dead end —
            # leftovers/blocker carry the next action for the human / Flash opt-in.
            if (
                report.get("extracted_count", 0) == 0
                and not report.get("filled")
                and not report.get("blocker")
            ):
                report["leftovers"].append(
                    {
                        "reason": "generic_dom_no_fields",
                        "label": "no_extractable_fields_after_entry",
                        "flash_candidate": True,
                        "hint": (
                            "Apply may open iframe/new tab/login; inspect "
                            "entry_prepass.spa_wait / fill_context / iframe_login"
                        ),
                        "platform": platform,
                        "coverage_path": report.get("coverage_path"),
                        "spa_wait": report["entry_prepass"].get("spa_wait"),
                        "fill_context": report.get("fill_context"),
                    }
                )

            # Honesty first: demote claimed fills that are live-empty BEFORE ADVANCE
            # (commit-verify contract — never advance on soft-skip lies).
            try:
                await _demote_filled_against_required_empty(fill_ctx, report, values)
            except Exception as e:
                report.setdefault("errors", []).append(
                    {"demote_required_empty": str(e)[:160]}
                )

            # Generic multipage: verify → ADVANCE → wait remount → repeat (bounded).
            # Skip when blocked / no fields / no filled progress — FAIL-before-ADVANCE.
            if (
                not report.get("blocker")
                and report.get("filled")
                and report.get("extracted_count", 0) > 0
            ):
                try:
                    import os as _os

                    from page_progress import capture_step_fingerprint, note_settle_cycle

                    max_advances = int(_os.environ.get("FASTFILL_MAX_ADVANCES", "4"))
                    advance_rounds: list[dict] = []
                    last_clicks: list = []
                    for _adv_i in range(max(1, max_advances)):
                        try:
                            await _demote_filled_against_required_empty(
                                fill_ctx, report, values
                            )
                        except Exception:
                            pass
                        before_fp = (
                            await capture_step_fingerprint(fill_ctx)
                        ).get("fingerprint")
                        adv = await try_advance_if_page_complete(fill_ctx, report)
                        last_clicks = list(adv.get("clicks") or [])
                        advance_rounds.append(
                            {k: v for k, v in adv.items() if k != "clicks"}
                        )
                        if not adv.get("advanced"):
                            # No advance → empty settle; budgeted STOP if cycling
                            note_settle_cycle(
                                report,
                                filled_this_cycle=0,
                                advanced_this_cycle=False,
                            )
                            break
                        changed = False
                        for _ in range(20):
                            await fill_ctx.wait_for_timeout(150)
                            after_fp = (
                                await capture_step_fingerprint(fill_ctx)
                            ).get("fingerprint")
                            if after_fp and after_fp != before_fp:
                                changed = True
                                break
                        if not changed:
                            report["stuck_on_same_page"] = True
                            note_settle_cycle(
                                report,
                                filled_this_cycle=0,
                                advanced_this_cycle=False,
                                stuck_on_same_page=True,
                            )
                            break
                        report["multipage_steps"] = int(
                            report.get("multipage_steps") or 0
                        ) + 1
                        note_settle_cycle(
                            report,
                            filled_this_cycle=1,
                            advanced_this_cycle=True,
                        )
                        if report.get("progress_stop"):
                            break
                    report["page_advance"] = (
                        dict(advance_rounds[-1]) if advance_rounds else {}
                    )
                    report["page_advance"]["clicks"] = last_clicks
                    report["page_advance"]["rounds"] = len(advance_rounds)
                    if report.get("progress_decision"):
                        report["page_advance"]["progress_decision"] = report[
                            "progress_decision"
                        ]
                    report["page_advance_rounds"] = advance_rounds
                except Exception as e:
                    report.setdefault("errors", []).append(
                        {"page_advance": str(e)[:160]}
                    )

            # Post-advance honesty pass (SPA wipe after Next)
            try:
                await _demote_filled_against_required_empty(fill_ctx, report, values)
            except Exception as e:
                report.setdefault("errors", []).append(
                    {"demote_required_empty_post_advance": str(e)[:160]}
                )

            # Late resume retry if field still empty after prefill
            try:
                await ensure_resume_uploaded(fill_ctx, values, report, force=False)
            except Exception as e:
                report.setdefault("errors", []).append(
                    {"ensure_resume_late": str(e)[:160]}
                )
            # Post-late-resume contact/LinkedIn reassert (Ashby parse race)
            if platform == "ashby" and not report.get("blocker"):
                try:
                    re_rows = await reassert_ashby_contact_after_resume(fill_ctx, values)
                    if not re_rows and fill_ctx is not page:
                        re_rows = await reassert_ashby_contact_after_resume(
                            page, values
                        )
                    report["ashby_post_resume_reassert_late"] = [
                        {
                            k: r.get(k)
                            for k in ("type", "ok", "verified", "readback", "reason")
                            if k in r
                        }
                        for r in (re_rows or [])
                    ]
                    _merge_ashby_reassert_rows(report, already, re_rows)
                except Exception as e:
                    report.setdefault("errors", []).append(
                        {"ashby_post_resume_reassert_late": str(e)[:160]}
                    )
            elif platform == "greenhouse" and not report.get("blocker"):
                try:
                    if await _should_run_gh_post_resume_reassert(report):
                        re_rows = await reassert_greenhouse_contact_after_resume(
                            fill_ctx, values
                        )
                        if not re_rows and fill_ctx is not page:
                            re_rows = await reassert_greenhouse_contact_after_resume(
                                page, values
                            )
                        report["greenhouse_post_resume_reassert_late"] = [
                            {
                                k: r.get(k)
                                for k in (
                                    "type",
                                    "ok",
                                    "verified",
                                    "readback",
                                    "reason",
                                    "label",
                                )
                                if k in r
                            }
                            for r in (re_rows or [])
                        ]
                        _merge_greenhouse_reassert_rows(report, already, re_rows)
                    else:
                        report["greenhouse_post_resume_reassert_late_skipped"] = (
                            "resume_not_verified"
                        )
                except Exception as e:
                    report.setdefault("errors", []).append(
                        {"greenhouse_post_resume_reassert_late": str(e)[:160]}
                    )

        report["submit_clicked"] = False
        report["never_submit"] = True

        # Flash leftovers MUST run before hold-open (same session when possible).
        # In-page first so headed review shows filled leftovers; Skyvern only if
        # still needed and not holding a headed browser for human review.
        from flash_leftovers import (
            build_leftovers_handoff,
            run_flash_leftovers,
        )
        from page_progress import apply_progress_verdict_gates

        report = _finalize(report)
        # Soft blockers (contact_incomplete, page_incomplete) still need
        # demote/enumerate/Flash. Only hard CAPTCHA/bot walls skip.
        _hard_block = report.get("blocker") in CAPTCHA_BLOCKERS or report.get(
            "blocker"
        ) in ("akamai", "chromium_missing", "browser_closed_during_entry")
        # Demote + deterministic reclaim BEFORE Flash so leftovers=0 lies and
        # SPA-wiped fields become flash_candidates (packs get first shot).
        if not _hard_block:
            try:
                await _demote_filled_against_required_empty(page, report, values)
                report = _finalize(report)
            except Exception as e:
                report.setdefault("errors", []).append(
                    {"demote_before_flash": str(e)[:160]}
                )
            try:
                reclaim = await _reclaim_deterministic_leftovers(
                    page, report, values, platform=platform, pass_i=0
                )
                report["deterministic_reclaim_pre_flash"] = reclaim
                report = _finalize(report)
            except Exception as e:
                report.setdefault("errors", []).append(
                    {"reclaim_before_flash": str(e)[:160]}
                )
            # Unanswered choice groups (Ashby yesno / Lever radios / generic)
            # → flash_candidates so Flash sees L0/1 misses widgets skipped.
            try:
                from unanswered_choices import scan_and_promote_unanswered

                scan_page = page
                try:
                    scan_page = fill_ctx  # generic/iframe path
                except NameError:
                    scan_page = page
                ua = await scan_and_promote_unanswered(
                    scan_page, report, platform=platform
                )
                if (
                    not ua.get("promoted")
                    and scan_page is not page
                ):
                    ua = await scan_and_promote_unanswered(
                        page, report, platform=platform
                    )
                report = _finalize(report)
            except Exception as e:
                report.setdefault("errors", []).append(
                    {"enumerate_unanswered": str(e)[:160]}
                )
            # Per-entry Ashby radios + consent (name-collision groups + checkboxes)
            # must reach Flash even when refill_passes=0 / hold-open is next.
            try:
                from leftover_miss_scan import promote_l01_misses

                await promote_l01_misses(page, report)
                report = _finalize(report)
            except Exception as e:
                report.setdefault("errors", []).append(
                    {"l01_miss_scan_pre_flash": str(e)[:160]}
                )
        if flash_leftovers and (
            report.get("leftover_count", 0) > 0
            or _flash_candidate_leftovers(report)
        ) and not _hard_block:
            try:
                await wait_while_paused(page, report)
            except Exception:
                pass
            try:
                inpage = await run_inpage_flash_leftovers(page, report, values)
                report["flash"] = inpage
                report["flash_called"] = bool(inpage.get("invoked"))
                report["flash_tokens_est"] = inpage.get("prompt_chars")
                filled_n = _flash_filled_count(inpage)
                left_n = int(report.get("leftover_count") or 0)
                cand_n = len(_flash_candidate_leftovers(report))
                if int(filled_n or 0) == 0 and (
                    inpage.get("invoked") or cand_n > 0 or left_n > 0
                ):
                    # FILL3-008: do not claim "invoked but filled 0" when
                    # invoked=false (deterministic-only / no LLM).
                    if inpage.get("invoked"):
                        zero_msg = (
                            f"initial: LLM invoked but filled 0 of "
                            f"{left_n} leftovers"
                        )
                    else:
                        zero_msg = (
                            f"initial: inpage leftovers filled 0 of {left_n} "
                            f"(invoked=false — deterministic-only / no LLM; "
                            f"not a Skyvern/Flash invoke failure)"
                        )
                    report.setdefault("errors", []).append(
                        {"flash_zero_fill": zero_msg}
                    )
                for fr in inpage.get("filled") or []:
                    if isinstance(fr, dict):
                        _record_fill_attempt(
                            report,
                            fr,
                            success=bool(fr.get("verified") or fr.get("ok")),
                            pass_i=0,
                            via_override="inpage_flash",
                        )
                report = _finalize(report)
            except Exception as e:
                report.setdefault("errors", []).append({"inpage_flash": str(e)[:160]})
            # After Flash, re-assert live zip / placeholder honesty (Flash can
            # claim verified against a remounted empty Ashby zip).
            try:
                await _demote_filled_against_required_empty(page, report, values)
                report = _finalize(report)
            except Exception as e:
                report.setdefault("errors", []).append(
                    {"demote_after_flash": str(e)[:160]}
                )
            # Skyvern Flash: only when leftovers remain AND we are not about to
            # hold a headed browser open (separate session would confuse review).
            still_left = int(report.get("leftover_count") or 0)
            if still_left > 0 and not hold_is_active(hold_sec) and int(refill_passes) <= 0:
                try:
                    await wait_while_paused(page, report)
                except Exception:
                    pass
                try:
                    flash = await run_flash_leftovers(
                        url, report, invoke=True, max_steps=5
                    )
                    report["flash"] = flash
                    report["flash_called"] = bool(flash.get("invoked"))
                    report["flash_tokens_est"] = flash.get("prompt_chars")
                except Exception as e:
                    report.setdefault("errors", []).append({"flash": str(e)[:160]})
            elif still_left > 0 and (hold_is_active(hold_sec) or int(refill_passes) > 0):
                report.setdefault("flash", report.get("flash") or build_leftovers_handoff(report))
                report["flash"]["skyvern_deferred"] = (
                    "headed_hold_open — inpage leftovers only; Skyvern skipped "
                    "so the held browser stays the review surface"
                )
                # FILL3-001 / FILL3-004 / FILL3-013: dashboard Flash+hold+refill
                # is inpage-only by design; invoked=false ≠ Flash failed.
                report["flash"].setdefault("flash_engine", "inpage")
                report["flash"].setdefault("inpage_ran", True)
        elif flash_leftovers:
            report["flash"] = build_leftovers_handoff(report)
            report["flash"]["skipped_reason"] = (
                "hard_blocker" if _hard_block else (
                    "blocker" if report.get("blocker") else "no_leftovers"
                )
            )
            report["flash"]["invoked"] = False
            report["flash_called"] = False
        else:
            report["flash"] = build_leftovers_handoff(report)
            report["flash"]["invoked"] = False
            report["flash_called"] = False

        report["never_submit"] = True
        report["submit_clicked"] = False
        apply_progress_verdict_gates(report)

        if screenshot:
            await _maybe_shot(page, screenshot, report)

        # Pass 0: record verified fills + leftover fails (attempt #1 per blank)
        _ingest_attempt_pass(report, pass_i=0, phase="initial_fill")

        # Same-session leftover refill loop (cycle headed: fill → judge blanks → refill)
        if int(refill_passes) > 0 and not (
            report.get("blocker") and report.get("blocker") not in CAPTCHA_BLOCKERS
        ):
            # If CAPTCHA reappears mid-refill, pause again then continue
            if report.get("blocker") in CAPTCHA_BLOCKERS or await page_shows_interactive_captcha(
                page
            ):
                await handle_captcha_blocker(
                    page,
                    report,
                    str(report.get("blocker") or "captcha"),
                    headed=is_headed,
                    captcha_wait=do_captcha_wait,
                    timeout_s=captcha_timeout_s,
                )
            await _run_in_session_refill_loop(
                page,
                report,
                values,
                platform=platform,
                flash_leftovers=flash_leftovers,
                refill_passes=int(refill_passes),
                wait_enter=do_refill_wait,
                screenshot=screenshot,
            )
            report = _finalize(report)
            apply_progress_verdict_gates(report)

        # Live DOM/vision judge before Ready (Workday Phase E may already have set it).
        if page is not None and not isinstance(report.get("vision_judge_live"), dict):
            try:
                from page_progress import apply_live_vision_gate

                await apply_live_vision_gate(page, report)
            except Exception as e:
                report["vision_incomplete"] = True
                report.setdefault("errors", []).append({"vision_gate": str(e)[:120]})
                if not report.get("blocker"):
                    report["blocker"] = "vision_incomplete"

        # Workday multipage: if still mid-wizard (Experience / Questions / …),
        # continue advancing BEFORE any review-hold. Never claim Ready early.
        # Probe footer primary (Next vs Submit) so incomplete detection is live.
        if (
            page is not None
            and platform == "workday"
            and report.get("blocker") not in CAPTCHA_BLOCKERS
        ):
            try:
                from page_progress import probe_footer_primary, workday_wizard_incomplete

                try:
                    await probe_footer_primary(page, report)
                except Exception as e:
                    report.setdefault("errors", []).append(
                        {"footer_primary_before_continue": str(e)[:120]}
                    )

                if workday_wizard_incomplete(report):
                    from workday_selectors import workday_two_phase_on_page

                    print(
                        "[workday] multipage incomplete — continuing from current "
                        "step (not holding for review yet)…",
                        flush=True,
                    )
                    note_fill_activity(
                        layer="workday",
                        action="continue multipage",
                        detail=str(report.get("workday_current_step") or "unknown"),
                    )
                    try:
                        await push_fill_activity(page)
                    except Exception:
                        pass
                    wd_more = await workday_two_phase_on_page(
                        page,
                        values,
                        click_create_account=True,
                        do_apply_clicks=False,
                        resume_pdf=identity.resume_pdf,
                        step_report=report,
                    )
                    _merge_workday_into_report(report, wd_more, values)
                    report["errors"].extend(wd_more.get("errors") or [])
                    report["workday_continue_before_hold"] = True
                    apply_progress_verdict_gates(report)
            except Exception as e:
                report.setdefault("errors", []).append(
                    {"workday_continue_before_hold": str(e)[:160]}
                )

        # SLO clock: fill work done (incl. refill); hold/CAPTCHA review excluded.
        report["fill_elapsed_seconds"] = round(time.time() - t0, 2)

        # Keep browser open for human review (--hold-open / --hold-seconds / headed default).
        # CRITICAL: drain Pause first — never browser.close() while Pause is engaged
        # or race-close when fill finished all fields during a pause.
        try:
            if page is not None:
                try:
                    report["pause_drain"] = await drain_pause_before_close(
                        page, report
                    )
                except Exception as e:
                    report.setdefault("errors", []).append(
                        {"pause_drain": str(e)[:120]}
                    )
            # Headed --hold-open (indefinite): always enter hold after terminal fill.
            # Timed hold / default headed 90s still hold when hold_is_active.
            force_indefinite = should_keep_fill_browser_open(
                paused=False, hold_seconds=hold_sec
            )
            do_hold = hold_is_active(hold_sec) or force_indefinite
            if do_hold:
                from page_progress import (
                    can_claim_ready,
                    finalize_ready_flag,
                    may_enter_review_hold,
                    probe_footer_primary,
                    workday_wizard_incomplete,
                )

                # Fresh footer primary (Next vs Submit) before Ready / hold label.
                if page is not None:
                    try:
                        await probe_footer_primary(page, report)
                    except Exception as e:
                        report.setdefault("errors", []).append(
                            {"footer_primary_before_hold": str(e)[:120]}
                        )

                hold_for = (
                    HOLD_INDEFINITE
                    if force_indefinite or hold_sec < 0
                    else int(hold_sec)
                )
                report["headed_hold_ms"] = None if hold_for < 0 else hold_for * 1000
                review_hold_ok = may_enter_review_hold(report)
                if review_hold_ok and can_claim_ready(report):
                    report["ready_for_review"] = True
                finalize_ready_flag(report)
                incomplete_wd = workday_wizard_incomplete(report)
                if review_hold_ok:
                    hold_action = "hold for review"
                    hold_detail = (
                        "indefinite — human closes browser"
                        if hold_for < 0
                        else f"{hold_for}s review"
                    )
                else:
                    # Mid-wizard / incomplete: keep browser open if requested, but
                    # never frame as Ready / "holding for review".
                    hold_action = "holding incomplete — not ready"
                    hold_detail = (
                        str(report.get("workday_current_step") or "")
                        or str(report.get("blocker") or "multipage_incomplete")
                    )[:120]
                    report["ready_for_review"] = False
                    report["hold_incomplete"] = True
                    if incomplete_wd:
                        report.setdefault(
                            "verdict_reason",
                            report.get("verdict_reason")
                            or "multipage_incomplete_not_ready_for_review",
                        )
                note_fill_activity(
                    layer="hold",
                    action=hold_action,
                    detail=hold_detail,
                )
                try:
                    if page is not None:
                        await push_fill_activity(page)
                except Exception:
                    pass
                # Snapshot BEFORE long hold so metrics are available while Chromium stays open.
                try:
                    snap = _finalize(dict(report))
                    snap.pop("_attempt_log", None)
                    snap_path = (
                        ROOT
                        / "skyvern_runtime"
                        / "real_job_results"
                        / "fast_fill_hold_snapshot.json"
                    )
                    if out:
                        snap_path = Path(out).parent / "hold_snapshot.json"
                    elif isinstance(screenshot, (str, Path)) and screenshot is not True:
                        shot_p = Path(screenshot)
                        if shot_p.parent.name.startswith("live_proof") or "cycle_" in str(
                            shot_p
                        ):
                            snap_path = shot_p.parent / "hold_snapshot.json"
                    snap_path.parent.mkdir(parents=True, exist_ok=True)
                    snap_path.write_text(json.dumps(snap, indent=2, default=str))
                    report["hold_snapshot"] = str(snap_path)
                    print(
                        f"[hold] snapshot {snap_path} "
                        f"filled={snap.get('filled_count')} "
                        f"leftovers={snap.get('leftover_count')}"
                        + (
                            " incomplete=1"
                            if snap.get("hold_incomplete")
                            or workday_wizard_incomplete(snap)
                            else ""
                        ),
                        flush=True,
                    )
                except Exception as e:
                    report.setdefault("errors", []).append(
                        {"hold_snapshot": str(e)[:160]}
                    )
                if sys.platform == "darwin":
                    try:
                        from captcha_pause import bring_chrome_testing_to_front

                        bring_chrome_testing_to_front()
                    except Exception:
                        pass
                # Hold loop: Continue → resume fill / Next → re-hold (cap rounds).
                # Never re-enter hold without attempting progress after Continue.
                MAX_HOLD_CONTINUE = 8
                hold_round = 0
                while True:
                    hold_round += 1
                    hold_result = await _hold_for_review(
                        seconds=hold_for,
                        report=report,
                        browser=browser,
                        page=page,
                    )
                    report["hold_result"] = hold_result
                    if not (hold_result or {}).get("continued"):
                        break
                    if hold_round > MAX_HOLD_CONTINUE:
                        print(
                            f"[hold] Continue cap ({MAX_HOLD_CONTINUE}) — "
                            "ending hold loop (never submit).",
                            flush=True,
                        )
                        break
                    try:
                        if browser is not None and not browser.is_connected():
                            break
                    except Exception:
                        break
                    resume = await _resume_fill_after_hold(
                        page,
                        report,
                        values,
                        platform=platform,
                        identity=identity,
                    )
                    report.setdefault("hold_resumes", []).append(resume)
                    # Re-label hold honesty before next wait (don't claim Ready early).
                    try:
                        await probe_footer_primary(page, report)
                    except Exception:
                        pass
                    review_hold_ok = may_enter_review_hold(report)
                    if review_hold_ok and can_claim_ready(report):
                        report["ready_for_review"] = True
                        report.pop("hold_incomplete", None)
                    else:
                        report["ready_for_review"] = False
                        report["hold_incomplete"] = True
                    finalize_ready_flag(report)
                    note_fill_activity(
                        layer="hold",
                        action=(
                            "hold for review"
                            if review_hold_ok
                            else "holding incomplete — not ready"
                        ),
                        detail=f"after continue round {hold_round}",
                    )
                    try:
                        await push_fill_activity(page)
                    except Exception:
                        pass
                    # Keep holding (indefinite / timed) so human can Continue again.
        finally:
            report.pop("_page", None)
            # Only reach here after pause drain + hold (or no hold requested).
            # Indefinite hold exits when human already closed the window.
            await browser.close()

    report["elapsed_seconds"] = round(time.time() - t0, 2)
    if report.get("fill_elapsed_seconds") is None:
        report["fill_elapsed_seconds"] = report["elapsed_seconds"]
    report["hold_seconds_applied"] = int(hold_sec) if hold_sec else 0
    report = _finalize(report, close_step_log=True)

    # Flash already ran before hold (when requested). Re-assert honesty gates.
    # Continuous learn runs inside _finalize(close_step_log=True) so early exits
    # also learn (guarded by report["_learned"]).
    report["never_submit"] = True
    report["submit_clicked"] = False
    if "flash" not in report:
        from flash_leftovers import build_leftovers_handoff

        report["flash"] = build_leftovers_handoff(report)
        report["flash"]["invoked"] = False
        report["flash_called"] = False
    from page_progress import apply_progress_verdict_gates

    apply_progress_verdict_gates(report)
    return report


def _run_continuous_learn(report: dict) -> None:
    """Experience + selector stats + replay. Idempotent via report['_learned']."""
    if report.get("_learned"):
        return
    report["_learned"] = True
    url = str(report.get("url") or "")
    platform = str(report.get("platform") or "unknown")
    try:
        from continuous_learn import learn_from_report
        from record_replay import page_fingerprint

        cl = learn_from_report(report)
        report["replay_recorded"] = int(cl.get("replay_recorded") or 0)
        report["replay_fingerprint"] = page_fingerprint(url, platform)
        if cl.get("ok") is False:
            errs = cl.get("errors") or []
            if errs:
                report.setdefault("errors", []).extend(
                    [{"continuous_learn": str(e)[:120]} for e in errs[:5]]
                )
    except Exception as e:
        report.setdefault("errors", []).append({"continuous_learn": str(e)[:120]})
        try:
            from record_replay import page_fingerprint, record_successful_fills

            n = record_successful_fills(url, platform, report.get("filled") or [])
            report["replay_recorded"] = n
            report["replay_fingerprint"] = page_fingerprint(url, platform)
        except Exception as e2:
            report.setdefault("errors", []).append({"replay_record": str(e2)[:120]})


def _run_vision_judge_finalize(report: dict) -> None:
    """Write vision_judge.json beside after_fill / final screenshot (headed path)."""
    if not report.get("headed"):
        return
    shot = report.get("screenshot")
    if not shot:
        step_log = report.get("_fill_step_log")
        if isinstance(step_log, FillStepLog):
            cand = step_log.out_dir / "after_fill.png"
            if cand.is_file():
                shot = str(cand)
    if not shot:
        return
    try:
        from vision_judge import judge_from_report, write_vision_judge

        vision = judge_from_report(report, screenshot=shot)
        out = Path(str(shot)).parent / "vision_judge.json"
        write_vision_judge(vision, out)
        report["vision_judge"] = str(out)
        report["vision_verdict"] = vision.get("verdict")
    except Exception as e:
        report.setdefault("errors", []).append({"vision_judge": str(e)[:120]})


def _norm_field_identity_key(raw: str) -> str:
    """Collapse Workday automation-id / label / selector name variants to one key."""
    s = (raw or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"section", "", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


def _field_identity_keys(row: dict) -> set[str]:
    """Normalized identity keys for leftover ↔ verified-fill reconciliation."""
    keys: set[str] = set()
    for attr in ("label", "automation_id", "type", "selector"):
        val = row.get(attr)
        if isinstance(val, str) and val.strip():
            nk = _norm_field_identity_key(val)
            if nk:
                keys.add(nk)
    sel = row.get("selector") or ""
    if isinstance(sel, str):
        for m in re.finditer(r'name=["\']?([^"\']+)["\']?', sel, re.I):
            nk = _norm_field_identity_key(m.group(1))
            if nk:
                keys.add(nk)
        for m in re.finditer(r"data-automation-id=['\"]([^'\"]+)['\"]", sel, re.I):
            nk = _norm_field_identity_key(m.group(1))
            if nk:
                keys.add(nk)
    return keys


def _identity_keys_overlap(a: set[str], b: set[str], *, min_len: int = 8) -> bool:
    """Exact or distinctive substring overlap (0842Z countryPhoneCode vs phonenumber--countryphonecode)."""
    if a & b:
        return True
    for x in a:
        if len(x) < min_len:
            continue
        for y in b:
            if len(y) < min_len:
                continue
            if x in y or y in x:
                return True
    return False


def _finalize(report: dict, *, close_step_log: bool = False) -> dict:
    # Safety invariants — every report JSON must carry these.
    report["never_submit"] = True
    report["submit_clicked"] = False
    report.setdefault("dummy", True)
    # Never leave non-JSON FieldAttemptLog in serializable snapshots
    if "_attempt_log" in report and not isinstance(
        report.get("field_attempt_log"), dict
    ):
        log = report.get("_attempt_log")
        if isinstance(log, FieldAttemptLog):
            report["field_attempt_log"] = {
                "jsonl": str(log.jsonl_path),
                "unfillable_md": str(log.unfillable_md),
                "fixer_trigger": str(getattr(log, "fixer_trigger", "")),
                "unfillable_count": len(log._unfillable_keys),
                "unfillable_keys": sorted(log._unfillable_keys),
                "run_id": log.run_id,
            }
    # Step log terminalization only on real exit — mid-run _finalize used to
    # emit run_end + freeze fill_steps.md too early (refill / pre-flash).
    step_log = report.get("_fill_step_log")
    if close_step_log and isinstance(step_log, FillStepLog):
        emit_filled_rows_as_steps(report, phase="finalize")
        emit_leftover_rows_as_steps(report)
        note_step(
            report,
            action="run_end",
            reason=f"verdict={report.get('verdict')} filled={len(report.get('filled') or [])} "
            f"leftovers={len(report.get('leftovers') or [])}",
            via="fast_fill",
        )
        finalize_step_log(report)
        report["fill_step_log"] = {
            "jsonl": str(step_log.jsonl_path),
            "md": str(step_log.md_path),
            "step_count": step_log._step,
            "run_id": step_log.run_id,
            "steps_index": str(step_log.out_dir / "steps" / "index.html"),
        }
        try:
            from flight_recorder import finalize_flight, get_flight, note_flight

            note_flight(
                report,
                "run_end",
                action="run_end",
                layer="fast_fill",
                gate_kind="flight",
                gate_result=str(report.get("verdict") or ""),
                gate_reason=(
                    f"filled={len(report.get('filled') or [])} "
                    f"leftovers={len(report.get('leftovers') or [])} "
                    f"advance_blocked={report.get('advance_blocked_reason')}"
                ),
                advance_decision=(
                    "STOP"
                    if report.get("progress_stop") or report.get("advance_blocked_reason")
                    else None
                ),
                advance_reason=str(
                    report.get("progress_stop_reason")
                    or report.get("advance_blocked_reason")
                    or ""
                )
                or None,
            )
            finalize_flight(report)
            flight = get_flight(report)
            if flight is not None:
                report["flight_recorder_summary"] = {
                    "jsonl": str(flight.jsonl_path),
                    "log": str(flight.log_path),
                    "event_count": flight._seq,
                    "run_id": flight.run_id,
                }
        except Exception as e:
            report.setdefault("errors", []).append({"flight_finalize": str(e)[:120]})
    # Promote nested widget/gh evidence onto verified before counting
    for f in report.get("filled") or []:
        if not isinstance(f, dict):
            continue
        if f.get("verified") is True:
            continue
        if is_verified_fill_row(f):
            f["verified"] = True
            f.setdefault("ok", True)
            if not f.get("readback"):
                f["readback"] = (f.get("shown") or f.get("picked") or "")[:120]
    raw_filled = [f for f in report.get("filled") or [] if isinstance(f, dict)]
    filled_ok = [f for f in raw_filled if is_verified_fill_row(f)]
    unverified = [f for f in raw_filled if f not in filled_ok and f.get("ok") is not False]
    # Demote unverified attempts out of filled[] so reports stay honest
    if unverified:
        report["filled"] = filled_ok
        for f in unverified:
            report.setdefault("leftovers", []).append(
                {
                    "label": f.get("label") or f.get("type") or f.get("automation_id"),
                    "type": f.get("type"),
                    "selector": f.get("selector"),
                    "reason": f.get("reason") or "unverified_readback",
                    "via": f.get("via"),
                    "flash_candidate": True,
                }
            )
    report["filled_count"] = len(filled_ok)
    # Reconcile leftovers against VERIFIED fills by label OR selector. A field can
    # be tracked under two identities — filled (e.g. COVER_LETTER essay via replay
    # with an empty label but a concrete textarea selector, or a URL field) while
    # an original extract-time leftover for the same DOM node lingers by label.
    # Only verified fills clear a leftover (unverified attempts were just demoted
    # into leftovers above), so this removes false FAILs without hiding real gaps;
    # a truly-reverted field is still caught by the independent live vision gate.
    if report.get("leftovers"):
        _verified_labels = {
            (f.get("label") or "").strip().lower()[:80]
            for f in filled_ok
            if f.get("label")
        }
        _verified_selectors = {
            (f.get("selector") or "").strip().lower()
            for f in filled_ok
            if f.get("selector")
        }
        _verified_identity_keys: set[str] = set()
        for f in filled_ok:
            _verified_identity_keys |= _field_identity_keys(f)

        def _leftover_is_verified_filled(u: dict) -> bool:
            lab = (u.get("label") or "").strip().lower()[:80]
            sel = (u.get("selector") or "").strip().lower()
            if lab and lab in _verified_labels:
                return True
            if sel and sel in _verified_selectors:
                return True
            u_keys = _field_identity_keys(u)
            if u_keys and _identity_keys_overlap(u_keys, _verified_identity_keys):
                return True
            return False

        report["leftovers"] = [
            u
            for u in report["leftovers"]
            if not (isinstance(u, dict) and _leftover_is_verified_filled(u))
        ]
    try:
        from leftover_miss_scan import demote_invented_leftovers

        demote_invented_leftovers(report)
    except Exception:
        pass
    report["leftover_count"] = len(report.get("leftovers") or [])
    # Persist scan/plan artifacts when an artifact dir is known
    try:
        art = report.get("artifact_dir") or report.get("out_dir") or report.get("shot_dir")
        if art and close_step_log:
            from scan_plan import build_plan_steps_from_filled, write_scan_plan

            write_scan_plan(
                art,
                fields=[
                    {
                        "type": f.get("type"),
                        "label": f.get("label") or f.get("automation_id"),
                        "verified": f.get("verified"),
                    }
                    for f in filled_ok
                ],
                plan=build_plan_steps_from_filled(filled_ok),
                meta={
                    "platform": report.get("platform"),
                    "url": (report.get("url") or "")[:200],
                    "verdict": report.get("verdict"),
                },
            )
    except Exception:
        pass
    # Contamination note (rows already verified — flag drifts without reopening prompts)
    if close_step_log and report.get("_contam_page") is None:
        report.setdefault("contamination_sweep", {"skipped": True, "reason": "no_live_page"})
    try:
        from flash_leftovers import flash_candidate_count

        report["flash_leftover_count"] = flash_candidate_count(report)
    except Exception:
        report.setdefault("flash_leftover_count", 0)
    req_after = report.get("required_empty_after_fill") or []
    fc = int(report.get("flash_leftover_count") or 0)
    lc = int(report.get("leftover_count") or 0)
    report["leftovers_zero_lie"] = bool(
        (lc == 0 and fc > 0) or (req_after and report.get("verdict") == "SUCCESS")
    )
    extracted = report.get("extracted_count") or 0
    # Coverage: verified filled / max(extracted, filled+leftovers classified attempts)
    denom = max(extracted, report["filled_count"] + report["leftover_count"], 1)
    report["coverage"] = round(report["filled_count"] / denom, 3)
    report["coverage_note"] = (
        "verified filled_count / max(extracted, filled+leftovers); "
        "unverified attempts are leftovers, never status=stuck"
    )
    # Honest ADVANCE: SUCCESS incompatible with validation / incomplete
    if report.get("validation_after_advance") or report.get("advanced_incomplete"):
        if report.get("verdict") == "SUCCESS":
            report["verdict"] = "FAIL"
    # Stuck-page / required-empty / Flash-leftover honesty gates
    from page_progress import (
        apply_progress_verdict_gates,
        finalize_ready_flag,
        is_essay_leftover,
    )

    for left in report.get("leftovers") or []:
        if isinstance(left, dict) and is_essay_leftover(left):
            left.setdefault("essay", True)
    apply_progress_verdict_gates(report)
    finalize_ready_flag(report)
    apply_resume_success_gate(report)
    try:
        from field_lock import fold_lock_metrics

        fold_lock_metrics(report)
    except Exception:
        pass
    assert report.get("never_submit") is True, "never_submit must be True"
    assert report.get("submit_clicked") is False, "submit_clicked must be False"
    ep = report.get("entry_prepass") or {}
    if isinstance(ep, dict):
        assert (ep.get("final_clicks") or 0) == 0, "FINAL click leaked in entry_prepass"
    # Continuous learn + vision after honesty demotion (same as former post-_finalize
    # block). Early exits with close_step_log=True also learn.
    if close_step_log:
        _run_continuous_learn(report)
        _run_vision_judge_finalize(report)
    return report


async def _maybe_shot(page, screenshot: bool | Path, report: dict) -> None:
    path = (
        Path(screenshot)
        if isinstance(screenshot, (str, Path)) and screenshot is not True
        else None
    )
    if path is None:
        # Prefer run artifact dir (same as fill_steps) → after_fill.png for vision_judge
        step_log = report.get("_fill_step_log")
        if isinstance(step_log, FillStepLog):
            path = step_log.out_dir / "after_fill.png"
        else:
            path = ROOT / "skyvern_runtime" / "real_job_results" / "fast_fill.png"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        await page.screenshot(path=str(path), full_page=True)
        report["screenshot"] = str(path)
    except Exception as e:
        report.setdefault("errors", []).append({"screenshot": str(e)[:160]})


_NON_JSON_TYPES = frozenset(
    {
        "Locator",
        "Page",
        "Browser",
        "BrowserContext",
        "ActionSupervisor",
        "FillStepLog",
        "FieldAttemptLog",
        "FieldLockSession",
    }
)


def report_for_json(report: dict | None) -> dict:
    """Return a JSON-serializable copy of a fill report.

    Strips live handles (``_page``, log objects, locators) and breaks circular
    references so ``json.dumps`` never raises on headed runs.
    """
    if not report:
        return {}

    def _sanitize(obj: Any, seen: set[int]) -> Any:
        if obj is None or isinstance(obj, (bool, int, float, str)):
            return obj
        oid = id(obj)
        if isinstance(obj, dict):
            if oid in seen:
                return "<circular>"
            seen.add(oid)
            out: dict[str, Any] = {}
            for k, v in obj.items():
                ks = str(k)
                if ks.startswith("_"):
                    continue
                out[ks] = _sanitize(v, seen)
            seen.discard(oid)
            return out
        if isinstance(obj, (list, tuple)):
            if oid in seen:
                return ["<circular>"]
            seen.add(oid)
            out_list = [_sanitize(x, seen) for x in obj]
            seen.discard(oid)
            return out_list
        if isinstance(obj, set):
            return sorted(str(x) for x in obj)
        cn = type(obj).__name__
        if cn in _NON_JSON_TYPES:
            return f"<{cn}>"
        if hasattr(obj, "jsonl_path") or hasattr(obj, "audit_path"):
            return f"<{cn}>"
        try:
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return f"<{cn}>"

    return _sanitize(dict(report), set())


def run_fast_fill(
    url: str,
    *,
    test_mode: bool = True,
    job_id: str | None = None,
    resume_path: Path | str | None = None,
    job_title: str | None = None,
    headed: bool | None = None,
    headless: bool | None = None,
    screenshot: bool | Path | None = None,
    max_entry_clicks: int = 3,
    flash_leftovers: bool = False,
    hold_seconds: int | None = None,
    captcha_wait: bool | None = None,
    captcha_timeout_s: float = DEFAULT_CAPTCHA_TIMEOUT_S,
    refill_passes: int = 0,
    refill_wait_enter: bool | None = None,
    fill_pause: bool | None = None,
    out: Path | str | None = None,
) -> dict:
    """Sync API for other agents. Returns the fill report dict.

    test_mode=True (default): dummy profile + per-run resume PDF.
    test_mode=False: real profile.json + job/tailored resume (dashboard opt-in).
    Never submits. Flash is OFF by default.

    headed=True opens a visible Chromium window (best for interactive demos).
    Default headed=None → headless (batch-safe).
    hold_seconds: post-fill review hold (None = headed default / headless 0).
    captcha_wait: default ON when headed — pause for human CAPTCHA (Enter).
    fill_pause: headed default ON — in-page Pause/Continue overlay.
    refill_passes: same-session leftover refill loops before close (0 = off).
    """
    report = asyncio.run(
        run_fast_fill_async(
            url,
            test_mode=test_mode,
            job_id=job_id,
            resume_path=resume_path,
            job_title=job_title,
            headed=headed,
            headless=headless,
            screenshot=screenshot,
            max_entry_clicks=max_entry_clicks,
            flash_leftovers=flash_leftovers,
            hold_seconds=hold_seconds,
            captcha_wait=captcha_wait,
            captcha_timeout_s=captcha_timeout_s,
            refill_passes=refill_passes,
            refill_wait_enter=refill_wait_enter,
            fill_pause=fill_pause,
            out=out,
        )
    )
    assert report.get("never_submit") is True, "refuse to write report without never_submit"
    assert report.get("submit_clicked") is False, "refuse to write report with submit_clicked"
    if out:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Drop non-JSON attempt logger object before serialize
        log_obj = report.pop("_attempt_log", None)
        step_obj = report.pop("_fill_step_log", None)
        flight_obj = report.pop("_flight_recorder", None)
        report.pop("_page", None)
        try:
            out_path.write_text(json.dumps(report_for_json(report), indent=2, default=str))
        finally:
            if log_obj is not None:
                report["_attempt_log"] = log_obj
            if step_obj is not None:
                report["_fill_step_log"] = step_obj
            if flight_obj is not None:
                report["_flight_recorder"] = flight_obj
        report["report_path"] = str(out_path)
    return report


def _url_quality(plat: str, url: str) -> int:
    """Higher = better demo URL (direct host, apply path, no trackers)."""
    low = (url or "").lower()
    score = 0
    host = urlparse(url).netloc.lower()
    if plat == "workday":
        if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
            score += 50
        if "recruitics" in low or "rx_url=" in low:
            score -= 40
    if plat == "lever" and low.rstrip("/").endswith("/apply"):
        score += 20
    if plat == "ashby" and "/application" in low:
        score += 20
    if plat == "greenhouse" and "/jobs/" in low:
        score += 10
    if "utm_" in low:
        score -= 2
    return score


def load_eval_urls_slo() -> tuple[dict, dict[str, str]]:
    """Return (slo dict, first URL per platform) from eval_urls.json."""
    path = HERE / "eval_urls.json"
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}, {}
    slo = data.get("slo") if isinstance(data.get("slo"), dict) else {}
    first: dict[str, str] = {}
    for row in data.get("urls") or []:
        if not isinstance(row, dict):
            continue
        plat = str(row.get("platform") or "").strip()
        url = row.get("url") or ""
        if plat and isinstance(url, str) and url.startswith("http") and plat not in first:
            first[plat] = url
    return slo, first


def discover_demo_urls(n_per: int = 1) -> dict[str, str]:
    """Pick one public apply URL per major platform from listings/."""
    want = ["greenhouse", "lever", "ashby", "workday"]
    found: dict[str, str] = {}
    listings = sorted((ROOT / "listings").glob("*.json"), reverse=True)
    for path in listings[:40]:
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("jobs") or data.get("listings") or []
        if not isinstance(items, list):
            continue
        for j in items:
            if not isinstance(j, dict):
                continue
            for k in ("job_url_direct", "apply_url", "url", "job_url", "application_url", "link"):
                u = j.get(k) or ""
                if not isinstance(u, str) or not u.startswith("http"):
                    continue
                plat = detect_platform(u)
                if plat not in want:
                    continue
                # Workday: only accept direct tenant hosts (not recruiter redirects)
                if plat == "workday":
                    host = urlparse(u).netloc.lower()
                    if "myworkdayjobs.com" not in host and "myworkdaysite.com" not in host:
                        continue
                if plat not in found or _url_quality(plat, u) > _url_quality(plat, found[plat]):
                    found[plat] = u
            if len(found) >= len(want) and all(
                _url_quality(p, found[p]) >= 10 for p in want if p in found
            ):
                # Keep scanning a bit for better URLs, but allow early exit when solid
                if all(p in found for p in want):
                    # Prefer finishing current file for quality upgrades
                    pass
        if len(found) >= len(want):
            # After one full file with all platforms, stop if workday is direct
            host = urlparse(found.get("workday", "")).netloc.lower()
            if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
                break
    # Fallbacks known-stable from experiments
    found.setdefault("greenhouse", "https://job-boards.greenhouse.io/biohub/jobs/7747517")
    found.setdefault(
        "lever",
        "https://jobs.lever.co/shieldai/a32a2559-8aa2-4d18-ae61-41cbfbfb644a/apply",
    )
    found.setdefault(
        "ashby",
        "https://jobs.ashbyhq.com/sentilink/9bc3de0b-1638-4310-8df1-2dd965f0bdf4/application",
    )
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url", nargs="?", help="Application URL")
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT, help="Write JSON report")
    # Interactive demos: visible browser by default. --headless for CI/batch.
    ap.add_argument(
        "--headed",
        action="store_true",
        default=True,
        help="Show a visible Chromium window (default for interactive demos)",
    )
    ap.add_argument(
        "--headless",
        action="store_true",
        help="Run without a visible browser window (CI/batch)",
    )
    ap.add_argument("--screenshot", action="store_true", help="Save full-page screenshot")
    ap.add_argument(
        "--flash-leftovers",
        action="store_true",
        default=False,
        help=(
            "After Layer 0/1, run leftovers recovery (default OFF). "
            "FILL3-004 matrix: with --hold-open or --refill-passes>0, recovery is "
            "inpage-only (Skyvern deferred; flash.invoked=LLM only). Without hold/"
            "refill, may invoke thin Skyvern (max_steps≤5). Dashboard headed Start "
            "pairs Flash+hold+refill → inpage. Never submits."
        ),
    )
    ap.add_argument(
        "--hold-open",
        action="store_true",
        help=(
            "After fill, keep browser open indefinitely for human review "
            "(until Ctrl+C / browser closed / Cancel). Never submit."
        ),
    )
    ap.add_argument(
        "--hold-seconds",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Seconds to keep browser open after fill before close "
            f"(default: {DEFAULT_HEADED_HOLD_SECONDS}s headed, 0 headless). "
            "Use -1 for indefinite. Overrides --hold-open when set."
        ),
    )
    ap.add_argument(
        "--captcha-wait",
        action="store_true",
        default=None,
        help=(
            "On CAPTCHA, pause for human solve then continue (default ON when headed). "
            "Prints: CAPTCHA detected — solve it in the browser, then press Enter here "
            "to continue. Never solves CAPTCHA automatically."
        ),
    )
    ap.add_argument(
        "--no-captcha-wait",
        action="store_true",
        help="Disable human CAPTCHA pause (headless-style BLOCKED even when headed)",
    )
    ap.add_argument(
        "--fill-pause",
        action="store_true",
        default=None,
        help=(
            "Show in-page Pause fill / Continue fill overlay (default ON when headed). "
            "Pause takes effect between fill actions (not mid-widget); "
            "Continue resumes and skips already-filled."
        ),
    )
    ap.add_argument(
        "--no-fill-pause",
        action="store_true",
        help="Disable in-page Pause/Continue overlay (also: FASTFILL_FILL_PAUSE=0)",
    )
    ap.add_argument(
        "--flight-recorder",
        action="store_true",
        default=None,
        help=(
            "Emit live flight.jsonl + flight.log decision trace (default ON when headed). "
            "Also: FASTFILL_FLIGHT=1. See scripts/fastfill/LIVE_VISIBILITY.md."
        ),
    )
    ap.add_argument(
        "--no-flight-recorder",
        action="store_true",
        help="Disable flight recorder even when headed (also: FASTFILL_FLIGHT=0)",
    )
    ap.add_argument(
        "--captcha-timeout",
        type=int,
        default=int(DEFAULT_CAPTCHA_TIMEOUT_S),
        metavar="SEC",
        help=f"Seconds to wait for human CAPTCHA solve (default {int(DEFAULT_CAPTCHA_TIMEOUT_S)})",
    )
    ap.add_argument(
        "--refill-passes",
        type=int,
        default=0,
        metavar="N",
        help=(
            "After first fill+screenshot, re-fill leftovers on the SAME page up to N "
            "times before close (0=off). Useful with --headed / --hold-open."
        ),
    )
    ap.add_argument(
        "--refill-wait-enter",
        action="store_true",
        default=None,
        help=(
            "OPTIONAL: between refill passes, wait for Enter after printing blanks. "
            "Default is auto-refill with NO Enter (humans do not babysit School/salary)."
        ),
    )
    ap.add_argument(
        "--no-refill-wait-enter",
        action="store_true",
        help="Auto-loop refill (default). Kept for backward compatibility.",
    )
    ap.add_argument(
        "--matrix",
        action="store_true",
        help=(
            "Smoke Greenhouse + Lever + Ashby + Workday; prefer eval_urls.json; "
            "write coverage matrix JSON (embeds eval SLO gates)"
        ),
    )
    ap.add_argument("--max-entry-clicks", type=int, default=3)
    ap.add_argument(
        "--test-mode",
        action="store_true",
        default=None,
        help="Use DUMMY_PROFILE + per-run dummy resume (default unless --real-profile)",
    )
    ap.add_argument(
        "--real-profile",
        action="store_true",
        help=(
            "Dashboard opt-in: real profile.json + job/tailored resume. "
            "Sets FASTFILL_ALLOW_REAL=1. Still never submits."
        ),
    )
    ap.add_argument(
        "--job-id",
        default=None,
        help="Job id for tailored resume path (resumes/<id>/resume.pdf) in --real-profile mode",
    )
    ap.add_argument(
        "--job-title",
        default=None,
        help="Job title for APPLYING_FOR (also reads FASTFILL_JOB_TITLE env)",
    )
    ap.add_argument(
        "--resume-path",
        default=None,
        help=(
            "Explicit resume PDF to attach (--real-profile only). "
            "Refused with --test-mode / dummy default — fixture PDF only. "
            "Still never submits."
        ),
    )
    args = ap.parse_args()
    if args.real_profile:
        test_mode = False
        os.environ["FASTFILL_ALLOW_REAL"] = "1"
        os.environ["FASTFILL_REAL_PROFILE"] = "1"
        os.environ["TEST_MODE"] = "0"
    elif args.test_mode:
        test_mode = True
        os.environ.pop("FASTFILL_ALLOW_REAL", None)
        os.environ["FASTFILL_REAL_PROFILE"] = "0"
        os.environ["TEST_MODE"] = "1"
    else:
        # Default = test/dummy — force env clear so stale FASTFILL_ALLOW_REAL
        # from a prior shell cannot leak into the run.
        test_mode = True
        os.environ.pop("FASTFILL_ALLOW_REAL", None)
        os.environ["FASTFILL_REAL_PROFILE"] = "0"
        os.environ["TEST_MODE"] = "1"
    # FILL-016: refuse --resume-path under test/dummy mode (dummy PDF only)
    if test_mode and args.resume_path:
        ap.error(
            "--resume-path is refused with --test-mode / dummy default "
            "(use --real-profile for job-scoped resumes; dummy uses fixture PDF only)"
        )
    headed = not bool(args.headless)
    if args.hold_seconds is not None:
        hold_seconds: int | None = int(args.hold_seconds)
        if hold_seconds >= 0:
            hold_seconds = max(0, hold_seconds)
        # negative → HOLD_INDEFINITE via _resolve_hold_seconds
    elif args.hold_open:
        hold_seconds = HOLD_INDEFINITE
    else:
        hold_seconds = None  # headed default / headless 0 inside run_fast_fill

    if args.no_captcha_wait:
        captcha_wait: bool | None = False
    elif args.captcha_wait:
        captcha_wait = True
    else:
        captcha_wait = None  # headed default ON

    if args.no_fill_pause:
        fill_pause: bool | None = False
    elif args.fill_pause:
        fill_pause = True
    else:
        fill_pause = None  # headed default ON

    if args.no_flight_recorder:
        os.environ["FASTFILL_FLIGHT"] = "0"
    elif args.flight_recorder:
        os.environ["FASTFILL_FLIGHT"] = "1"

    if args.no_refill_wait_enter:
        refill_wait_enter: bool | None = False
    elif args.refill_wait_enter:
        refill_wait_enter = True
    else:
        refill_wait_enter = None

    if args.matrix:
        slo, eval_first = load_eval_urls_slo()
        urls = discover_demo_urls()
        # Prefer curated eval_urls (align matrix smoke with suite) over listings.
        for plat in ("greenhouse", "lever", "ashby", "workday"):
            if plat in eval_first:
                urls[plat] = eval_first[plat]
        order = [p for p in ("greenhouse", "lever", "ashby", "workday") if p in urls]
        matrix = {
            "experiment": "fast_fill_coverage_matrix",
            "dummy": True,
            "never_submit": True,
            "flash_called": False,
            "slo_source": "scripts/fastfill/eval_urls.json",
            "slo": slo,
            "eval_platforms": sorted(eval_first.keys()),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "rows": [],
        }
        for plat in order[:4]:
            url = urls[plat]
            print(f"\n=== {plat}: {url} ===")
            row_out = args.out.parent / f"fast_fill_{plat}.json"
            print(f"Launching Chromium headed={headed} (dummy-only, never submit)…", flush=True)
            report = run_fast_fill(
                url,
                headed=headed,
                screenshot=args.screenshot,
                max_entry_clicks=args.max_entry_clicks,
                flash_leftovers=False,
                hold_seconds=hold_seconds,
                captcha_wait=captcha_wait,
                captcha_timeout_s=float(args.captcha_timeout),
                refill_passes=int(args.refill_passes),
                refill_wait_enter=refill_wait_enter,
                fill_pause=fill_pause,
                out=row_out,
            )
            row = {
                "platform": plat,
                "url": url,
                "url_source": "eval_urls" if eval_first.get(plat) == url else "listings",
                "coverage_path": report.get("coverage_path") or coverage_path_for(plat),
                "elapsed_seconds": report.get("elapsed_seconds"),
                "extracted": report.get("extracted_count"),
                "filled": report.get("filled_count"),
                "leftovers": report.get("leftover_count"),
                "coverage": report.get("coverage"),
                "blocker": report.get("blocker"),
                "form_reached": (report.get("entry_prepass") or {}).get("form_reached"),
                "entry_clicks": len((report.get("entry_prepass") or {}).get("clicked") or []),
                "final_clicks": (report.get("entry_prepass") or {}).get("final_clicks", 0),
                "submit_clicked": report.get("submit_clicked"),
                "never_submit": report.get("never_submit") is True,
                "flash_called": report.get("flash_called"),
                "errors": len(report.get("errors") or []),
            }
            matrix["rows"].append(row)
            print(json.dumps(row, indent=2))

        matrix_path = args.out.parent / "fast_fill_coverage_matrix.json"
        matrix_path.parent.mkdir(parents=True, exist_ok=True)
        assert matrix.get("never_submit") is True
        matrix_path.write_text(json.dumps(matrix, indent=2))
        print("\n=== COVERAGE MATRIX ===")
        print(f"{'platform':12} {'filled':>6} {'left':>6} {'cov':>6} {'sec':>6} blocker")
        for r in matrix["rows"]:
            print(
                f"{r['platform']:12} {r['filled']:>6} {r['leftovers']:>6} "
                f"{r['coverage']:>6} {r['elapsed_seconds']:>6} {r.get('blocker') or '-'}"
            )
        print(f"wrote {matrix_path} (slo from {matrix['slo_source']})")
        return 0

    if not args.url:
        ap.error("url required (or pass --matrix)")

    hold_disp = (
        hold_seconds
        if hold_seconds is not None
        else (DEFAULT_HEADED_HOLD_SECONDS if headed else 0)
    )
    if headed:
        print("═" * 62, flush=True)
        print(" LIVE FILL STEP LOG — [fill-step NNN] lines stream below", flush=True)
        print(" Keep this terminal open while the headed browser fills the form.", flush=True)
        print("═" * 62, flush=True)
    print(
        f"Launching Chromium headed={headed} flash_leftovers={args.flash_leftovers} "
        f"hold_seconds={hold_disp} captcha_wait={captcha_wait} "
        f"fill_pause={fill_pause} refill_passes={args.refill_passes} "
        f"test_mode={test_mode} (never submit)…",
        flush=True,
    )
    report = run_fast_fill(
        args.url,
        test_mode=test_mode,
        job_id=args.job_id,
        resume_path=args.resume_path,
        job_title=args.job_title or os.environ.get("FASTFILL_JOB_TITLE") or None,
        headed=headed,
        screenshot=args.screenshot,
        max_entry_clicks=args.max_entry_clicks,
        flash_leftovers=bool(args.flash_leftovers),
        hold_seconds=hold_seconds,
        captcha_wait=captcha_wait,
        captcha_timeout_s=float(args.captcha_timeout),
        refill_passes=int(args.refill_passes),
        refill_wait_enter=refill_wait_enter,
        fill_pause=fill_pause,
        out=args.out,
    )
    summary = {
        "platform": report["platform"],
        "test_mode": report.get("test_mode"),
        "dummy": report.get("dummy"),
        "elapsed_seconds": report.get("elapsed_seconds"),
        "extracted": report.get("extracted_count"),
        "filled": report.get("filled_count"),
        "leftovers": report.get("leftover_count"),
        "coverage": report.get("coverage"),
        "blocker": report.get("blocker"),
        "submit_clicked": report.get("submit_clicked"),
        "never_submit": report.get("never_submit"),
        "flash_called": report.get("flash_called"),
        "flash_leftovers_requested": report.get("flash_leftovers_requested"),
        "out": report.get("report_path"),
    }
    if report.get("workday"):
        summary["workday_metrics"] = (report["workday"] or {}).get("metrics")
    if report.get("flash"):
        summary["flash_api"] = {
            "mode": report["flash"].get("mode"),
            "invoked": report["flash"].get("invoked"),
            "max_steps": report["flash"].get("max_steps"),
            "leftover_count": report["flash"].get("leftover_count"),
            "already_filled_count": report["flash"].get("already_filled_count"),
            "prompt_chars": report["flash"].get("prompt_chars"),
            "never_submit": report["flash"].get("never_submit"),
            "dummy": report["flash"].get("dummy"),
            "skipped_reason": report["flash"].get("skipped_reason"),
        }
    print(json.dumps(summary, indent=2))
    print("\nFilled:")
    for f in report.get("filled") or []:
        print(
            f"  [{f.get('layer') or f.get('via')}] {f.get('type')}: "
            f"{f.get('label', f.get('selector'))!r} -> {f.get('value')!r}"
        )
    if report.get("leftovers"):
        print("\nLeftovers (Flash/Skyvern handoff):")
        for u in (report.get("leftovers") or [])[:20]:
            print(f"  {u}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

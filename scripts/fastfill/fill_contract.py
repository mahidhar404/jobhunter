"""Unified fill contract — verify-before-touch, commit, advance (Tier-1 v2).

Single gate for verified rows: ``field_is_done`` + ActionSupervisor (fail-closed).
Do not add a parallel oracle for filled / skip / advance — Ready consults the
same ``field_is_done`` + ``filled_rows_honest`` contract (vision_judge is an
input to that gate, not a second advance voter). Dummy-only; never submit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

FillFn = Callable[[], Awaitable[dict]]


@dataclass(frozen=True)
class TouchDecision:
    action: str  # "touch" | "skip_lock"
    reason: str
    readback: str = ""
    row: dict | None = None


@dataclass(frozen=True)
class FillResult:
    row: dict
    verified: bool
    reason: str
    supervisor_verdict: str | None = None


@dataclass(frozen=True)
class AdvanceDecision:
    ready: bool
    reason: str
    advance: dict | None = None


async def verify_before_touch(
    page,
    field_meta: dict,
    intent: str | None,
    *,
    report: dict | None = None,
) -> TouchDecision:
    """Pre-touch gate: SKIP+LOCK when ``field_is_done`` says complete.

    Yogesh rule: if a prior layer already locked/verified this field, later
    layers must skip — never reopen/retype. Settle may still close chrome.
    """
    from field_done import field_is_done

    want = (intent or "").strip()
    meta = dict(field_meta)
    ft = str(meta.get("type") or "").upper()
    if ft in ("FIELD_OF_STUDY", "DISCIPLINE", "MAJOR"):
        meta.setdefault("dom_chip", True)

    # Locked by prior layer → hard skip (no DOM reopen / alias walk).
    if report is not None:
        try:
            from field_lock import gate_field_action

            g = gate_field_action(
                report,
                field_type=ft or None,
                label=str(meta.get("label") or "") or None,
                selector=str(meta.get("selector") or "") or None,
                automation_id=str(meta.get("automation_id") or "") or None,
            )
            if g and g.get("action") == "lock_skip":
                rb = str(g.get("readback") or "")[:120]
                # Lock only if field_is_done still agrees; unlock dishonest locks.
                lock_ok = True
                try:
                    from field_done import field_is_done_from_readback

                    lock_v = field_is_done_from_readback(rb, meta, want or None)
                    lock_ok = bool(lock_v.ok)
                except Exception:
                    lock_ok = False
                if not lock_ok:
                    try:
                        from field_lock import unlock_if_not_done

                        unlock_if_not_done(
                            report,
                            field_type=ft or None,
                            label=str(meta.get("label") or "") or None,
                            selector=str(meta.get("selector") or "") or None,
                            automation_id=str(meta.get("automation_id") or "") or None,
                            intent=want or None,
                            readback=rb,
                        )
                    except Exception:
                        pass
                else:
                    row: dict[str, Any] = {
                        **meta,
                        "value": want,
                        "readback": rb,
                        "verified": True,
                        "ok": True,
                        "reason": "field_locked_skip",
                        "skipped_already_correct": True,
                        "skipped_locked": True,
                        "via": "fill_contract",
                    }
                    try:
                        from fill_step_log import note_step

                        note_step(
                            report,
                            action="lock_skip",
                            field_type=ft[:48],
                            label=str(
                                meta.get("automation_id") or meta.get("selector") or ""
                            )[:80],
                            after=rb,
                            via="fill_contract",
                            reason="field_locked_skip",
                        )
                    except Exception:
                        pass
                    try:
                        from flight_recorder import note_flight

                        note_flight(
                            report,
                            "gate",
                            action="skip",
                            layer="fill_contract",
                            field_meta=meta,
                            intent=want,
                            gate_kind="lock_skip",
                            gate_result="skip",
                            gate_reason="field_locked_skip",
                            readback=rb,
                        )
                    except Exception:
                        pass
                    return TouchDecision("skip_lock", "field_locked_skip", rb, row)
        except Exception:
            pass

    # FoS: close Expanded chrome before done-probe so promptOption labels
    # cannot fake a matching chip (Arts-Other + open Science-Computer list).
    if ft in ("FIELD_OF_STUDY", "DISCIPLINE", "MAJOR") and page is not None:
        try:
            from verified_select import settle_fos_widget_until_closed

            await settle_fos_widget_until_closed(page, intent=want or None)
        except Exception:
            pass

    try:
        verdict = await field_is_done(page, meta, want or None)
    except Exception as e:
        return TouchDecision("touch", f"field_done_probe_error:{e}")

    if not verdict.ok:
        # Touch path — log why we are about to rewrite/fill (live visibility).
        if report is not None:
            try:
                from flight_recorder import note_flight

                note_flight(
                    report,
                    "gate",
                    action="touch",
                    layer="fill_contract",
                    field_meta=meta,
                    intent=want,
                    gate_kind="verify_before_touch",
                    gate_result="touch",
                    gate_reason=verdict.reason or "not_done",
                    readback=str(verdict.readback or "")[:120] or None,
                )
            except Exception:
                pass
        return TouchDecision("touch", verdict.reason or "not_done")

    rb = str(verdict.readback or "")[:120]
    row = {
        **meta,
        "value": want,
        "readback": rb,
        "verified": True,
        "ok": True,
        "reason": "already_correct_skip",
        "skipped_already_correct": True,
        "via": "fill_contract",
    }

    if report is not None:
        try:
            from field_lock import lock_verified_field

            lock_verified_field(
                report,
                row,
                field_type=str(meta.get("type") or "") or None,
                selector=str(meta.get("selector") or "") or None,
                automation_id=str(meta.get("automation_id") or "") or None,
                readback=rb,
                via="fill_contract",
            )
        except Exception:
            pass
        try:
            from fill_step_log import note_step

            note_step(
                report,
                action="skip_already_correct",
                field_type=str(meta.get("type") or "")[:48],
                label=str(meta.get("automation_id") or meta.get("selector") or "")[:80],
                after=rb,
                via="fill_contract",
                reason="already_correct_skip",
            )
        except Exception:
            pass
        try:
            from flight_recorder import note_flight

            note_flight(
                report,
                "gate",
                action="skip",
                layer="fill_contract",
                field_meta=meta,
                intent=want,
                gate_kind="verify_before_touch",
                gate_result="skip_lock",
                gate_reason=verdict.reason or "already_correct_skip",
                readback=rb,
            )
        except Exception:
            pass

    return TouchDecision("skip_lock", verdict.reason or "field_is_done", rb, row)


async def _clear_locator(loc: Any) -> None:
    if loc is None:
        return
    try:
        await loc.fill("", timeout=3000)
    except Exception:
        try:
            await loc.clear(timeout=3000)
        except Exception:
            pass


def _apply_field_done_verdict(row: dict, verdict, *, intent: str) -> None:
    """Rule 1: no verified=True without field_is_done pass."""
    rb = row.get("readback") if row.get("readback") is not None else row.get("shown")
    if verdict.ok:
        row["verified"] = True
        row["ok"] = True
        row["reason"] = row.get("reason") or verdict.reason
        return
    row["verified"] = False
    row["ok"] = False
    row["skipped_already_correct"] = False
    if verdict.reason in (
        "fos_chip_wrong_value",
        "chip_wrong_value",
        "text_mismatch",
        "autofill_mismatch_intent",
    ):
        row["reason"] = "wrong_autofill"
    else:
        row["reason"] = verdict.reason or "field_not_done"


async def commit_fill(
    page,
    field_meta: dict,
    intent: str | None,
    fill_fn: FillFn,
    *,
    via: str,
    locator: Any = None,
    report: dict | None = None,
    before: str = "",
) -> FillResult:
    """Run fill_fn once, audit via supervisor, enforce contract rules."""
    from action_judge import is_committed_autofill_text
    from field_done import field_is_done, field_is_done_from_readback

    want = (intent or "").strip()
    meta = dict(field_meta)
    ft = str(meta.get("type") or "").upper()
    if ft in ("FIELD_OF_STUDY", "DISCIPLINE", "MAJOR"):
        meta.setdefault("dom_chip", True)

    b = (before or "").strip()
    if not b and locator is not None:
        try:
            from fast_fill import _read_locator_value

            b = (await _read_locator_value(locator)).strip()
        except Exception:
            pass

    # Rule 2/3: wrong autofill before touch → clear + reclaim, never lock.
    if b and is_committed_autofill_text(b) and want:
        pre = field_is_done_from_readback(b, meta, want)
        if not pre.ok and pre.reason in (
            "fos_chip_wrong_value",
            "chip_wrong_value",
            "text_mismatch",
        ):
            await _clear_locator(locator)

    row = await fill_fn()
    row.setdefault("via", via)
    row.setdefault("value", want)
    if b:
        row.setdefault("readback_before", b)

    audit: dict | None = None
    if report is not None and not report.get("_supervisor_skip"):
        try:
            from action_supervisor import audit_fill_row

            audit = await audit_fill_row(
                page,
                report,
                row,
                before=b,
                intent=want,
                locator=locator,
            )
        except Exception as e:
            # Rule 4: supervisor fail-closed.
            row["verified"] = False
            row["ok"] = False
            row["reason"] = f"supervisor_error:{str(e)[:80]}"
            return FillResult(row, False, row["reason"], "STUCK")

    sv = str((audit or {}).get("supervisor_verdict") or "")
    jv = str((audit or {}).get("judge_verdict") or "")

    # Rule 3 continued: WRONG autofill — clear + one reclaim (supervisor fix is max 1).
    if sv == "WRONG" and locator is not None and want and not audit.get("fix_attempted"):
        # How-Heard: fill("") focuses the filter and reopens the listbox
        # (battle gym + live Workday). Reclaim via fill_fn without clearing.
        if ft not in ("HOW_HEARD",):
            await _clear_locator(locator)
        try:
            row = await fill_fn()
            row.setdefault("via", via)
            row["value"] = want
            from action_supervisor import audit_fill_row

            audit = await audit_fill_row(
                page,
                report,
                row,
                before=b,
                intent=want,
                locator=locator,
            )
            sv = str((audit or {}).get("supervisor_verdict") or "")
        except Exception:
            pass

    if sv == "STUCK" and ft in ("FIELD_OF_STUDY", "DISCIPLINE", "MAJOR") and page is not None:
        try:
            from field_map import FIELD_OF_STUDY

            from exp_workday_selectors import _fos_candidates, _reclaim_fos_on_wrap

            aid = str(meta.get("automation_id") or "").replace("education/", "")
            cands = _fos_candidates({FIELD_OF_STUDY: want})
            reclaim = await _reclaim_fos_on_wrap(
                page, aid or "formField-fieldOfStudy", want, cands, report=report
            )
            if reclaim.get("verified"):
                row.update(reclaim)
                row["verified"] = True
                row["ok"] = True
                row["supervisor_stuck"] = False
                sv = "OK"
        except Exception:
            pass

    # Live readback for field_is_done.
    # Never treat intent/value as a DOM readback (NXP 2244Z: null readback +
    # value=Illinois → false verified → pack_incomplete / no_matching_option).
    from workday_date_readback import is_date_spin_context, normalize_spin_readback

    raw_rb = row.get("readback")
    if raw_rb is None:
        raw_rb = row.get("picked") or row.get("verified_value") or row.get("option_text")
    spin_ctx = is_date_spin_context(
        field_type=ft,
        action=str(row.get("mode") or ""),
        widget=str(row.get("widget") or ""),
        mode=str(meta.get("mode") or ""),
        readback=raw_rb,
    )
    if spin_ctx:
        meta.setdefault("widget", "date_spin")
        meta.setdefault("mode", "date_spin")
        rb = normalize_spin_readback(raw_rb)
        if rb:
            row["readback"] = rb[:120]
    else:
        rb = str(raw_rb or "").strip()
    if locator is not None:
        try:
            from fast_fill import _read_locator_value

            live = (await _read_locator_value(locator)).strip()
            if live:
                rb = live
                row["readback"] = live[:120]
        except Exception:
            pass

    # Completion truth from readback when available (post-fill).
    if rb:
        done_v = field_is_done_from_readback(rb, meta, want or None)
    else:
        try:
            done_v = await field_is_done(page, meta, want or None)
            if done_v.ok and done_v.readback:
                row["readback"] = str(done_v.readback)[:120]
                rb = str(done_v.readback).strip()
        except Exception:
            done_v = field_is_done_from_readback(rb, meta, want or None)

    _apply_field_done_verdict(row, done_v, intent=want)

    # Rule 2 again: never treat wrong autofill as committed skip.
    if not done_v.ok and (
        jv == "wrong_autofill"
        or done_v.reason
        in (
            "fos_chip_wrong_value",
            "chip_wrong_value",
            "text_mismatch",
        )
    ):
        row["verified"] = False
        row["ok"] = False
        row["skipped_already_correct"] = False
        row["reason"] = "wrong_autofill"
        sv = "WRONG"

    # Thrash lock only when field_is_done passes.
    if sv == "THRASH" and not done_v.ok:
        row["verified"] = False
        row["ok"] = False

    if row.get("supervisor_skip"):
        row["verified"] = bool(done_v.ok)
        row["ok"] = bool(done_v.ok)

    # Yogesh rule: honest commit → lock so later layers cannot rewrite.
    # Same identity keys as verify_before_touch (aid/label/selector, not sel-only).
    # Idempotent: lock_verified_field skips duplicate keys.
    if report is not None and done_v.ok and row.get("verified"):
        try:
            from field_lock import lock_verified_field

            lock_verified_field(
                report,
                row,
                field_type=str(meta.get("type") or row.get("type") or "") or None,
                label=str(meta.get("label") or row.get("label") or "") or None,
                selector=str(meta.get("selector") or row.get("selector") or "") or None,
                automation_id=str(
                    meta.get("automation_id")
                    or row.get("automation_id")
                    or row.get("field_id")
                    or ""
                )
                or None,
                readback=str(row.get("readback") or rb or "")[:120] or None,
                via=via or "commit_fill",
            )
        except Exception:
            pass

    if report is not None:
        try:
            from flight_recorder import note_flight

            note_flight(
                report,
                "fill",
                action=str(row.get("mode") or via or "fill")[:64],
                layer=str(row.get("layer") or via or "commit_fill")[:48],
                field_meta={**meta, **{k: row.get(k) for k in ("label", "selector", "automation_id", "type") if row.get(k)}},
                intent=want,
                gate_kind="commit_fill",
                gate_result=sv or ("ok" if row.get("verified") else "fail"),
                gate_reason=str(row.get("reason") or done_v.reason or "")[:240],
                readback=str(row.get("readback") or rb or "")[:120] or None,
                extra={
                    "verified": bool(row.get("verified")),
                    "supervisor": sv or None,
                    "field_done": done_v.reason if done_v else None,
                    "before": b[:80] if b else None,
                },
            )
        except Exception:
            pass

    return FillResult(
        row,
        bool(row.get("verified")),
        str(row.get("reason") or ""),
        sv or None,
    )


async def advance_page_if_ready(page, report: dict | None) -> AdvanceDecision:
    """Advance only when filled rows are honest and page has no blockers."""
    if report is None:
        return AdvanceDecision(False, "no_report")

    def _flight_adv(decision: AdvanceDecision) -> AdvanceDecision:
        try:
            from flight_recorder import note_flight

            note_flight(
                report,
                "advance",
                action="advance" if decision.ready else "advance_blocked",
                layer="fill_contract",
                advance_decision="READY" if decision.ready else "BLOCKED",
                advance_reason=decision.reason,
                gate_kind="advance_page_if_ready",
                gate_result="ok" if decision.ready else "blocked",
                gate_reason=decision.reason,
            )
        except Exception:
            pass
        return decision

    try:
        from verified_select import settle_before_advance

        # Shared settle path (FoS chip override + phone-country override).
        settle = await settle_before_advance(page, report)
        if settle.get("still_open") and not settle.get("fos_chip_override"):
            # Report-backed FoS commit: DOM chip probe can miss while listbox chrome
            # still reads open (2237Z advance_contract listbox_still_open).
            fos_filled = any(
                isinstance(f, dict)
                and str(f.get("type") or "").upper()
                in ("FIELD_OF_STUDY", "DISCIPLINE", "MAJOR")
                and (f.get("verified") or f.get("ok") or f.get("skipped_already_correct"))
                for f in (report.get("filled") or [])
            )
            if fos_filled:
                report["fos_chip_override"] = True
                report["listbox_open"] = False
                report["mid_widget_open"] = False
                settle["still_open"] = False
                settle["fos_chip_override"] = True
            else:
                from verified_select import fos_skip_allows_advance, fos_widget_expanded

                if fos_skip_allows_advance(report) and not await fos_widget_expanded(
                    page
                ):
                    report["fos_skip_override"] = True
                    report["listbox_open"] = False
                    report["mid_widget_open"] = False
                    settle["still_open"] = False
                    settle["fos_skip_override"] = True
        if settle.get("still_open"):
            report["advance_blocked_reason"] = "listbox_still_open"
            report["listbox_open"] = True
            report["mid_widget_open"] = True
            return _flight_adv(AdvanceDecision(False, "listbox_still_open"))
        report["listbox_open"] = False
        report["mid_widget_open"] = False
        if report.get("advance_blocked_reason") == "listbox_still_open":
            report.pop("advance_blocked_reason", None)
    except Exception:
        pass

    try:
        from field_done import (
            filled_rows_honest,
            filter_required_empty_false_incomplete,
        )

        if not filled_rows_honest(report):
            return _flight_adv(AdvanceDecision(False, "filled_rows_not_honest"))

        try:
            from exp_workday_selectors import _required_empty_on_page

            live_empties = await _required_empty_on_page(page)
            filtered = await filter_required_empty_false_incomplete(
                page, report, live_empties
            )
            report["required_empty_before_advance"] = filtered
            report["required_empty_after_fill"] = filtered
            if filtered:
                from exp_workday_selectors import _advance_block_reason

                report["advance_blocked_reason"] = _advance_block_reason(filtered)
            elif report.get("advance_blocked_reason") in (
                "required_fields_empty",
                "required_dates_empty",
                "experience_dates_incomplete",
                "wizard_incomplete",
            ):
                report.pop("advance_blocked_reason", None)
        except Exception:
            pass
    except Exception:
        return _flight_adv(AdvanceDecision(False, "filled_rows_audit_error"))

    try:
        from page_progress import may_enter_review_hold, probe_footer_primary

        await probe_footer_primary(page, report)
        if may_enter_review_hold(report):
            return _flight_adv(AdvanceDecision(True, "review_ready"))
        if report.get("advance_blocked_reason"):
            return _flight_adv(AdvanceDecision(False, str(report["advance_blocked_reason"])))
        # 1138Z: footer ADVANCE + can_claim_ready False is mid-wizard (Ready
        # refuse via workday_wizard_incomplete). Page empties already filtered —
        # click Next. Do not STOP with wizard_incomplete.
    except Exception as e:
        return _flight_adv(AdvanceDecision(False, f"progress_probe_error:{e}"))

    try:
        from fast_fill import try_advance_if_page_complete

        adv = await try_advance_if_page_complete(page, report)
        if adv.get("advanced"):
            return _flight_adv(AdvanceDecision(True, "advanced", adv))
        if adv.get("advance_blocked_reason"):
            return _flight_adv(
                AdvanceDecision(False, str(adv["advance_blocked_reason"]), adv)
            )
        return _flight_adv(AdvanceDecision(False, "advance_not_clicked", adv))
    except Exception as e:
        return _flight_adv(AdvanceDecision(False, f"advance_error:{e}"))


async def finalize_widget_rows(
    page,
    report: dict | None,
    rows: list[dict],
    *,
    via: str,
) -> list[dict]:
    """Re-audit widget fill rows through commit_fill (noop re-fill)."""
    if not rows or report is None or page is None:
        return rows
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            out.append(row)
            continue
        captured = dict(row)

        async def _noop(r: dict = captured) -> dict:
            return r

        try:
            fr = await commit_fill(
                page,
                {
                    "type": captured.get("type") or "",
                    "selector": captured.get("selector") or "",
                    "automation_id": captured.get("automation_id") or "",
                    "mode": captured.get("mode") or "",
                },
                str(captured.get("value") or captured.get("picked") or ""),
                _noop,
                via=via,
                report=report,
                before=str(captured.get("readback") or captured.get("shown") or ""),
            )
            out.append(fr.row)
        except Exception:
            out.append(captured)
    return out

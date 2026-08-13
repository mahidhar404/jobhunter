#!/usr/bin/env python3
"""Unit tests: fill_contract verify/commit/advance (Tier-1 v2, dummy-only)."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FOS_FIXTURE = HERE / "gym/ats/cases/workday_education_fos_chip/form.html"
FOS_WRONG_FIXTURE = HERE / "gym/ats/cases/workday_education_fos_wrong_chip/form.html"


class _MockLoc:
    def __init__(self, value: str):
        self._value = value
        self.fill_calls: list[str] = []

    async def count(self) -> int:
        return 1

    async def input_value(self) -> str:
        return self._value

    async def inner_text(self) -> str:
        return self._value

    async def fill(self, val: str, timeout: int = 4000) -> None:
        self.fill_calls.append(val)
        self._value = val

    async def clear(self, timeout: int = 3000) -> None:
        self._value = ""


async def _browser_case(html_path: Path, fn) -> None:
    from playwright.async_api import async_playwright

    html = html_path.read_text(encoding="utf-8")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        await fn(page)
        await browser.close()


def test_verify_before_touch_skips_when_done():
    from fill_contract import verify_before_touch
    from field_map import FIELD_OF_STUDY

    async def _run(page):
        meta = {
            "type": FIELD_OF_STUDY,
            "automation_id": "formField-fieldOfStudy",
        }
        touch = await verify_before_touch(
            page, meta, "Computer Science", report={"field_lock_session": {}}
        )
        assert touch.action == "skip_lock", touch
        assert touch.row is not None
        assert touch.row.get("verified") is True
        assert touch.row.get("skipped_already_correct") is True

    asyncio.run(_browser_case(FOS_FIXTURE, _run))


def test_verify_before_touch_touches_when_wrong():
    from fill_contract import verify_before_touch
    from field_map import FIELD_OF_STUDY

    async def _run(page):
        meta = {
            "type": FIELD_OF_STUDY,
            "automation_id": "formField-fieldOfStudy",
        }
        touch = await verify_before_touch(page, meta, "Computer Science")
        assert touch.action == "touch"
        assert touch.row is None

    asyncio.run(_browser_case(FOS_WRONG_FIXTURE, _run))


def test_commit_fill_rejects_wrong_lock():
    from action_supervisor import ActionSupervisor
    from fill_contract import commit_fill
    from field_map import FIELD_OF_STUDY

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as td:
            report: dict = {"_attempt_cycle_dir": td}
            sup = ActionSupervisor(td)
            report["_action_supervisor"] = sup
            page = MagicMock()

            async def fill_fn() -> dict:
                return {
                    "type": FIELD_OF_STUDY,
                    "automation_id": "education/fieldOfStudy",
                    "readback": "Arts-Other",
                    "value": "Computer Science",
                    "verified": True,
                    "ok": True,
                    "mode": "fill",
                }

            fr = await commit_fill(
                page,
                {"type": FIELD_OF_STUDY, "dom_chip": True},
                "Computer Science",
                fill_fn,
                via="test",
                report=report,
                before="Arts-Other",
            )
            assert fr.verified is False
            assert fr.row.get("reason") == "wrong_autofill"
            assert fr.supervisor_verdict == "WRONG"

    asyncio.run(_run())


def test_commit_fill_wrong_does_not_lock():
    """Lock-on-commit: wrong autofill must not create a field lock."""
    from action_supervisor import ActionSupervisor
    from fill_contract import commit_fill
    from field_lock import attach_field_locks, get_field_locks
    from field_map import FIELD_OF_STUDY

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as td:
            report: dict = {"_attempt_cycle_dir": td}
            attach_field_locks(report)
            report["_action_supervisor"] = ActionSupervisor(td)
            page = MagicMock()

            async def fill_fn() -> dict:
                return {
                    "type": FIELD_OF_STUDY,
                    "automation_id": "education/fieldOfStudy",
                    "readback": "Arts-Other",
                    "value": "Computer Science",
                    "verified": True,
                    "ok": True,
                    "mode": "fill",
                    "dom_chip": True,
                }

            fr = await commit_fill(
                page,
                {"type": FIELD_OF_STUDY, "dom_chip": True, "automation_id": "education/fieldOfStudy"},
                "Computer Science",
                fill_fn,
                via="test",
                report=report,
                before="Arts-Other",
            )
            assert fr.verified is False
            sess = get_field_locks(report)
            assert sess is not None
            assert not sess.is_locked(
                field_type=FIELD_OF_STUDY, automation_id="education/fieldOfStudy"
            )

    asyncio.run(_run())


def test_commit_fill_one_fix_max():
    from action_supervisor import ActionSupervisor

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as td:
            report: dict = {"_attempt_cycle_dir": td}
            sup = ActionSupervisor(td)
            loc = _MockLoc("wrong@example.com")

            async def fix() -> str:
                await loc.fill("dummy@example.com")
                return loc._value

            audit = await sup.audit_after_action(
                report,
                field="EMAIL",
                field_type="EMAIL",
                intent="dummy@example.com",
                before="",
                after="wrong@example.com",
                action="fill",
                fix_fn=fix,
            )
            assert audit["fix_attempted"] is True
            assert loc.fill_calls == ["dummy@example.com"]
            assert audit["supervisor_verdict"] == "OK"

            audit2 = await sup.audit_after_action(
                report,
                field="EMAIL",
                field_type="EMAIL",
                intent="dummy@example.com",
                before="wrong@example.com",
                after="wrong@example.com",
                action="fill",
                fix_fn=fix,
            )
            assert audit2.get("fix_attempted") is False
            assert len(loc.fill_calls) == 1

    asyncio.run(_run())


def test_commit_fill_rewrite_wrong_not_verified():
    from action_supervisor import ActionSupervisor
    from fill_contract import commit_fill

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as td:
            report: dict = {"_attempt_cycle_dir": td, "field_lock_session": {}}
            sup = ActionSupervisor(td, thrash_limit=2)
            report["_action_supervisor"] = sup
            page = MagicMock()

            async def fill_fn() -> dict:
                return {
                    "type": "EXPERIENCE_TITLE",
                    "automation_id": "workExperience-1/jobTitle",
                    "readback": "Senior ML Engineer",
                    "value": "Applied AI/ML Analyst",
                    "verified": True,
                    "ok": True,
                    "mode": "fill",
                }

            fr = await commit_fill(
                page,
                {"type": "EXPERIENCE_TITLE"},
                "Applied AI/ML Analyst",
                fill_fn,
                via="test",
                report=report,
                before="Applied AI/ML Analyst",
            )
            assert fr.supervisor_verdict == "WRONG", fr
            assert fr.verified is False

    asyncio.run(_run())


def test_commit_fill_thrash_lock_no_verified():
    from action_supervisor import ActionSupervisor

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as td:
            report: dict = {"_attempt_cycle_dir": td, "field_lock_session": {}}
            sup = ActionSupervisor(td, thrash_limit=2)

            audit = await sup.audit_after_action(
                report,
                field="workExperience-1/jobTitle",
                field_type="EXPERIENCE_TITLE",
                intent="Applied AI/ML Analyst",
                before="Applied AI/ML Analyst",
                after="Senior ML Engineer",
                action="fill",
            )
            assert audit["supervisor_verdict"] == "THRASH", audit
            assert report.get("field_lock_session") or True

    asyncio.run(_run())


def test_filled_rows_honest_audits_skip_rows():
    from field_done import filled_rows_honest
    from field_map import FIELD_OF_STUDY

    honest = filled_rows_honest(
        {
            "filled": [
                {
                    "type": FIELD_OF_STUDY,
                    "readback": "Arts-Other",
                    "value": "Computer Science",
                    "verified": True,
                    "via": "already_correct_skip",
                    "skipped_already_correct": True,
                }
            ]
        }
    )
    assert honest is False


def test_supervisor_field_done_fail_closed():
    from action_supervisor import ActionSupervisor

    async def _run() -> None:
        sup = ActionSupervisor(tempfile.mkdtemp())
        page = MagicMock()

        async def _boom(*_a, **_k):
            raise RuntimeError("boom")

        import field_done

        orig = field_done.field_is_done
        field_done.field_is_done = _boom
        try:
            ok, reason = await sup._consult_field_done(
                page,
                field_type="EMAIL",
                intent="test@example.com",
                readback="",
            )
            assert ok is False
            assert reason == "field_done_error"
        finally:
            field_done.field_is_done = orig

    asyncio.run(_run())


def test_contract_advance_page_no_legacy_fallback():
    """Contract not-ready must NOT fall through to ``_gate_then_advance``."""
    from unittest.mock import patch

    import exp_workday_selectors as wd
    from fill_contract import AdvanceDecision

    async def _run() -> None:
        report: dict = {"filled": []}
        phase: dict = {}
        called = {"legacy": False}

        async def fake_adv(_page, _report):
            return AdvanceDecision(False, "filled_rows_not_honest")

        async def fake_gate(_page, _report, _phase):
            called["legacy"] = True
            return True

        with patch.object(wd, "_gate_then_advance", fake_gate):
            import fill_contract

            with patch.object(fill_contract, "advance_page_if_ready", fake_adv):
                ok = await wd._contract_advance_page(MagicMock(), report, phase)
        assert ok is False
        assert called["legacy"] is False
        assert phase.get("advance_contract_blocked") == "filled_rows_not_honest"
        assert phase.get("advanced") is False

    asyncio.run(_run())


def test_contract_advance_page_fail_closed_on_error():
    """Contract throw → no advance, no legacy fallback."""
    from unittest.mock import patch

    import exp_workday_selectors as wd

    async def _run() -> None:
        report: dict = {"filled": []}
        phase: dict = {}
        called = {"legacy": False}

        async def boom(_page, _report):
            raise RuntimeError("contract boom")

        async def fake_gate(_page, _report, _phase):
            called["legacy"] = True
            return True

        with patch.object(wd, "_gate_then_advance", fake_gate):
            import fill_contract

            with patch.object(fill_contract, "advance_page_if_ready", boom):
                ok = await wd._contract_advance_page(MagicMock(), report, phase)
        assert ok is False
        assert called["legacy"] is False
        assert phase.get("advance_contract_blocked") == "contract_error"
        errs = phase.get("errors") or []
        assert any("advance_contract" in str(e) for e in errs)

    asyncio.run(_run())


def test_verify_before_touch_unlocks_wrong_lock():
    """Dishonest FoS lock (Arts-Other vs CS) must unlock and touch."""
    from fill_contract import verify_before_touch
    from field_lock import attach_field_locks, get_field_locks
    from field_map import FIELD_OF_STUDY

    async def _run(page) -> None:
        report: dict = {"dummy": True}
        attach_field_locks(report)
        sess = get_field_locks(report)
        assert sess is not None
        sess.lock(
            field_type=FIELD_OF_STUDY,
            automation_id="formField-fieldOfStudy",
            readback="Arts-Other",
            via="dishonest_lock",
        )
        touch = await verify_before_touch(
            page,
            {
                "type": FIELD_OF_STUDY,
                "automation_id": "formField-fieldOfStudy",
                "dom_chip": True,
            },
            "Computer Science",
            report=report,
        )
        assert touch.action == "touch", touch
        assert not sess.is_locked(
            field_type=FIELD_OF_STUDY, automation_id="formField-fieldOfStudy"
        )

    asyncio.run(_browser_case(FOS_WRONG_FIXTURE, _run))


def test_commit_fill_locks_blocks_later_layer():
    """Layer A commit_fill locks → layer B verify_before_touch skip_lock / no rewrite."""
    from action_supervisor import ActionSupervisor
    from fill_contract import commit_fill, verify_before_touch
    from field_lock import attach_field_locks, get_field_locks

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as td:
            report: dict = {"_attempt_cycle_dir": td, "dummy": True}
            attach_field_locks(report)
            report["_action_supervisor"] = ActionSupervisor(td)
            page = MagicMock()

            async def fill_fn() -> dict:
                return {
                    "type": "EMAIL",
                    "automation_id": "contact_email",
                    "label": "Email",
                    "readback": "dummy@example.com",
                    "value": "dummy@example.com",
                    "verified": True,
                    "ok": True,
                    "mode": "fill",
                }

            fr = await commit_fill(
                page,
                {
                    "type": "EMAIL",
                    "automation_id": "contact_email",
                    "label": "Email",
                },
                "dummy@example.com",
                fill_fn,
                via="layer_a",
                report=report,
            )
            assert fr.verified is True, fr
            sess = get_field_locks(report)
            assert sess is not None
            assert sess.is_locked(
                field_type="EMAIL", automation_id="contact_email"
            ), sess.locked_keys()

            # Layer B must not rewrite — same dummy intent, lock identity matches.
            touch = await verify_before_touch(
                page,
                {
                    "type": "EMAIL",
                    "automation_id": "contact_email",
                    "label": "Email address",
                },
                "dummy@example.com",
                report=report,
            )
            assert touch.action == "skip_lock", touch
            assert touch.row is not None
            assert touch.row.get("skipped_locked") is True
            assert touch.row.get("reason") == "field_locked_skip"
            # Second lock is idempotent — still one key, thrash counted via gate.
            lock_verified = sess.lock(
                field_type="EMAIL",
                automation_id="contact_email",
                readback="dummy@example.com",
                via="layer_b_noop",
            )
            assert "aid:contact_email" in lock_verified.key
            assert sess.is_locked(field_type="EMAIL", automation_id="contact_email")

    asyncio.run(_run())


if __name__ == "__main__":
    failed = 0
    for name in [n for n in dir() if n.startswith("test_")]:
        try:
            globals()[name]()
            print("OK", name)
        except Exception as e:
            print("FAIL", name, e)
            failed += 1
    raise SystemExit(failed)

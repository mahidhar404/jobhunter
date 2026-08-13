#!/usr/bin/env python3
"""Unit tests: ActionSupervisor per-action audit loop (dummy-only)."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


class _MockLoc:
    """Minimal Playwright locator stub."""

    def __init__(self, value: str):
        self._value = value
        self.fill_calls: list[str] = []

    async def count(self) -> int:
        return 1

    async def input_value(self) -> str:
        return self._value

    async def fill(self, val: str, timeout: int = 4000) -> None:
        self.fill_calls.append(val)
        self._value = val


def test_supervisor_ok_already_correct():
    from action_supervisor import ActionSupervisor

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as td:
            report: dict = {"_attempt_cycle_dir": td}
            sup = ActionSupervisor(td)
            loc = _MockLoc("dummy@example.com")

            audit = await sup.audit_after_action(
                report,
                field="EMAIL",
                field_type="EMAIL",
                intent="dummy@example.com",
                before="dummy@example.com",
                after="dummy@example.com",
                action="fill",
                locator=loc,
            )
            assert audit["supervisor_verdict"] == "OK"
            assert audit["fix_attempted"] is False
            assert loc.fill_calls == []

    asyncio.run(_run())


def test_supervisor_wrong_one_fix_attempt():
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
                locator=loc,
                fix_fn=fix,
            )
            assert audit["fix_attempted"] is True
            assert loc.fill_calls == ["dummy@example.com"]
            assert audit["supervisor_verdict"] == "OK"
            assert audit["after"] == "dummy@example.com"

    asyncio.run(_run())


def test_supervisor_thrash_locks_field():
    from action_supervisor import ActionSupervisor

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as td:
            report: dict = {"_attempt_cycle_dir": td, "field_lock_session": {}}
            sup = ActionSupervisor(td, thrash_limit=2)

            t1 = await sup.audit_after_action(
                report,
                field="workExperience-1/jobTitle",
                field_type="EXPERIENCE_TITLE",
                intent="Applied AI/ML Analyst",
                before="Applied AI/ML Analyst",
                after="Senior ML Engineer",
                action="fill",
            )
            assert t1["supervisor_verdict"] == "THRASH"

            lines = sup.audit_path.read_text().strip().splitlines()
            row = json.loads(lines[-1])
            assert row["supervisor_verdict"] == "THRASH"
            assert row["judge_verdict"] == "thrash_rewrite"

    asyncio.run(_run())


def test_audit_fill_row_skip_already_correct():
    from action_supervisor import audit_fill_row

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as td:
            report: dict = {"_attempt_cycle_dir": td}
            loc = _MockLoc("Example Corp")
            page = MagicMock()
            page.locator = MagicMock(return_value=loc)

            row = {
                "type": "EXPERIENCE_COMPANY",
                "automation_id": "workExperience-1/company",
                "value": "Example Corp",
                "readback": "Example Corp",
                "verified": True,
                "skipped_already_correct": True,
                "reason": "already_correct_skip",
            }
            audit = await audit_fill_row(
                page,
                report,
                row,
                before="Example Corp",
                intent="Example Corp",
                locator=loc,
            )
            assert audit is not None
            assert audit["supervisor_verdict"] == "OK"
            assert loc.fill_calls == []

    asyncio.run(_run())


def test_self_test_runs():
    from action_supervisor import self_test

    self_test()


def test_dual_verifier_demotes_wrong_autofill_false_success():
    """Approach 13: fill-only would SUCCESS; judge+supervisor+field_done → FAIL."""
    from action_judge import judge_field_action
    from action_supervisor import ActionSupervisor
    from field_done import field_is_done_from_readback, filled_rows_honest
    from field_map import FIELD_OF_STUDY
    from fill_verify import is_verified_fill_row

    async def _run() -> None:
        wrong, intent = "Arts-Other", "Computer Science"
        # Fill-only oracle trusts verified=True
        row = {
            "type": FIELD_OF_STUDY,
            "automation_id": "education/fieldOfStudy",
            "readback": wrong,
            "value": intent,
            "verified": True,
            "ok": True,
            "via": "already_correct_skip",
            "dom_chip": True,
        }
        fill_only_success = bool(row["verified"]) and is_verified_fill_row(row) is False
        # Dual: judge + supervisor + field_done
        j = judge_field_action(
            field="fos", before=wrong, after=wrong, intent=intent, action="fill"
        )
        assert j["verdict"] == "wrong_autofill", j
        with tempfile.TemporaryDirectory() as td:
            report: dict = {
                "_attempt_cycle_dir": td,
                "filled": [dict(row)],
                "leftovers": [],
                "verdict": "SUCCESS",
            }
            sup = ActionSupervisor(td)
            audit = await sup.audit_after_action(
                report,
                field="education/fieldOfStudy",
                field_type=FIELD_OF_STUDY,
                intent=intent,
                before=wrong,
                after=wrong,
                action="fill",
                automation_id="education/fieldOfStudy",
            )
            assert audit["supervisor_verdict"] == "WRONG", audit
            fd = field_is_done_from_readback(
                wrong, {"type": FIELD_OF_STUDY, "dom_chip": True}, intent
            )
            assert not fd.ok
            assert filled_rows_honest(report) is False or not is_verified_fill_row(row)
            # Dual reduces false SUCCESS
            assert fill_only_success or row["verified"]  # fill layer claimed success
            assert audit["supervisor_verdict"] != "OK"

    asyncio.run(_run())


def test_dual_verifier_empty_readback_not_ok():
    """Fill-only empty_readback must not stay supervisor OK (dual verifier)."""
    from action_supervisor import ActionSupervisor
    from field_done import field_is_done_from_readback

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as td:
            report: dict = {"_attempt_cycle_dir": td}
            sup = ActionSupervisor(td)
            audit = await sup.audit_after_action(
                report,
                field="addressSection_addressLine2",
                field_type="ADDRESS_LINE2",
                intent="Apt 4",
                before="",
                after="",
                action="fill",
                automation_id="addressSection_addressLine2",
            )
            fd = field_is_done_from_readback(
                "", {"type": "ADDRESS_LINE2"}, "Apt 4"
            )
            assert not fd.ok
            assert fd.reason == "empty_readback"
            # page is None → consult_field_done short-circuits True; judge
            # still maps empty after+intent to WRONG via needed_fill/empty.
            assert audit["supervisor_verdict"] == "WRONG", audit

    asyncio.run(_run())


if __name__ == "__main__":
    test_supervisor_ok_already_correct()
    test_supervisor_wrong_one_fix_attempt()
    test_supervisor_thrash_locks_field()
    test_audit_fill_row_skip_already_correct()
    test_self_test_runs()
    test_dual_verifier_demotes_wrong_autofill_false_success()
    test_dual_verifier_empty_readback_not_ok()
    print("test_action_supervisor: OK")

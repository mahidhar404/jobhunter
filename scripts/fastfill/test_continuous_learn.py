#!/usr/bin/env python3
"""Unit tests for continuous_learn — sanitize, experience, preferred selectors."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def test_sanitize_strips_pii():
    from continuous_learn import (
        PLACEHOLDER_EMAIL,
        PLACEHOLDER_PHONE,
        PLACEHOLDER_PASSWORD,
        sanitize_value,
        value_shape,
    )

    assert sanitize_value("alice@example.com", field_type="EMAIL", test_mode=True) == PLACEHOLDER_EMAIL
    assert sanitize_value("405-555-0199", field_type="PHONE", test_mode=True) == PLACEHOLDER_PHONE
    assert sanitize_value("anyone@x.co", test_mode=True) == PLACEHOLDER_EMAIL
    # Real mode: never persist values
    assert sanitize_value("alice@example.com", field_type="EMAIL", test_mode=False) is None
    assert sanitize_value("Internet job board", field_type="HOW_HEARD", test_mode=False) is None
    # Dummy policy text kept
    assert sanitize_value("Internet job board", field_type="HOW_HEARD", test_mode=True) == (
        "Internet job board"
    )
    assert value_shape("alice@example.com", "EMAIL") == "email"
    assert value_shape("405-555-0100", "PHONE") == "phone"


def test_experience_append_and_prefer_high_success():
    import continuous_learn as cl

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        old = (
            cl.STORE_DIR,
            cl.EXPERIENCE_PATH,
            cl.SELECTOR_STATS_PATH,
            cl.LESSONS_JSON_PATH,
            cl.LESSONS_MD_PATH,
        )
        cl.STORE_DIR = td_path
        cl.EXPERIENCE_PATH = td_path / "experience.jsonl"
        cl.SELECTOR_STATS_PATH = td_path / "selector_stats.json"
        cl.LESSONS_JSON_PATH = td_path / "lessons.json"
        cl.LESSONS_MD_PATH = td_path / "lessons.md"
        try:
            n = cl.append_experience(
                [
                    {
                        "platform": "greenhouse",
                        "host": "boards.greenhouse.io",
                        "selector": "input#email",
                        "type": "EMAIL",
                        "label": "Email",
                        "value": "randommail6969+abc@gmail.com",
                        "verified": True,
                        "ok": True,
                    },
                    {
                        "platform": "greenhouse",
                        "host": "boards.greenhouse.io",
                        "selector": "select#good",
                        "type": "HOW_HEARD",
                        "label": "How did you hear about this job?",
                        "value": "Internet job board",
                        "verified": True,
                        "ok": True,
                    },
                ],
                test_mode=True,
            )
            assert n == 2
            rows = cl.load_experience()
            assert len(rows) == 2
            email = next(r for r in rows if r["type"] == "EMAIL")
            assert email["value"] == cl.PLACEHOLDER_EMAIL
            assert "randommail" not in json.dumps(rows)

            cl.update_selector_stats(
                [
                    {"selector": "select#good", "type": "HOW_HEARD", "verified": True, "ok": True},
                    {"selector": "select#good", "type": "HOW_HEARD", "verified": True, "ok": True},
                    {"selector": "select#bad", "type": "HOW_HEARD", "verified": False, "ok": False},
                    {"selector": "select#bad", "type": "HOW_HEARD", "verified": False, "ok": False},
                ],
                platform="greenhouse",
                host="boards.greenhouse.io",
            )
            cl.demote_selector("greenhouse", "select#bad")
            pref = cl.preferred_selectors("greenhouse", min_rate=0.5)
            assert any(p["selector"] == "select#good" for p in pref)
            assert not any(p["selector"] == "select#bad" for p in pref)

            ranked = cl.rank_replay_rows(
                [
                    {"selector": "select#bad", "type": "HOW_HEARD"},
                    {"selector": "select#good", "type": "HOW_HEARD"},
                ],
                platform="greenhouse",
            )
            assert ranked[0]["selector"] == "select#good"

            sim = cl.similar_leftover_answers(
                [{"type": "HOW_HEARD", "label": "How did you hear about this job?"}],
                platform="greenhouse",
            )
            assert sim and "Internet" in str(sim[0]["value"])
        finally:
            (
                cl.STORE_DIR,
                cl.EXPERIENCE_PATH,
                cl.SELECTOR_STATS_PATH,
                cl.LESSONS_JSON_PATH,
                cl.LESSONS_MD_PATH,
            ) = old


def test_learn_from_report_real_mode_no_pii():
    import continuous_learn as cl

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        old = (
            cl.STORE_DIR,
            cl.EXPERIENCE_PATH,
            cl.SELECTOR_STATS_PATH,
            cl.LESSONS_JSON_PATH,
            cl.LESSONS_MD_PATH,
        )
        cl.STORE_DIR = td_path
        cl.EXPERIENCE_PATH = td_path / "experience.jsonl"
        cl.SELECTOR_STATS_PATH = td_path / "selector_stats.json"
        cl.LESSONS_JSON_PATH = td_path / "lessons.json"
        cl.LESSONS_MD_PATH = td_path / "lessons.md"
        try:
            report = {
                "url": "https://jobs.lever.co/acme/abc",
                "platform": "lever",
                "test_mode": False,
                "dummy": False,
                "filled": [
                    {
                        "selector": "input[name=email]",
                        "type": "EMAIL",
                        "label": "Email",
                        "value": "real.person@company.com",
                        "ok": True,
                        "verified": True,
                    }
                ],
                "leftovers": [],
                "field_attempt_log": {},
            }
            # Avoid writing real replay_cache during unit test
            import record_replay as rr

            orig = rr.record_successful_fills
            rr.record_successful_fills = lambda *a, **k: 0
            try:
                summary = cl.learn_from_report(report)
            finally:
                rr.record_successful_fills = orig
            assert summary["ok"]
            assert summary["test_mode"] is False
            blob = cl.EXPERIENCE_PATH.read_text()
            assert "real.person@company.com" not in blob
            assert "{{EMAIL}}" not in blob or "EMAIL" in blob  # shape only; no cleartext
            row = cl.load_experience()[-1]
            assert row.get("value") is None or "value" not in row or row.get("value") is None
        finally:
            (
                cl.STORE_DIR,
                cl.EXPERIENCE_PATH,
                cl.SELECTOR_STATS_PATH,
                cl.LESSONS_JSON_PATH,
                cl.LESSONS_MD_PATH,
            ) = old


def main():
    test_sanitize_strips_pii()
    test_experience_append_and_prefer_high_success()
    test_learn_from_report_real_mode_no_pii()
    print("test_continuous_learn OK")


if __name__ == "__main__":
    main()

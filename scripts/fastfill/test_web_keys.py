#!/usr/bin/env python3
"""Unit tests for web_keys (dummy-only; never assert real profile PII)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import web_keys as wk  # noqa: E402
from field_map import PASSWORD, PASSWORD_CONFIRM  # noqa: E402


def test_sanitize_company():
    assert wk.sanitize_company("Quanti Phi Inc.") == "QuantiPhiInc"
    assert wk.sanitize_company("  ACME-AI  ") == "ACMEAI"
    assert wk.sanitize_company("") == ""


def test_make_password_format():
    pw = wk.make_password("Quantiphi")
    assert pw.startswith("Pswdpswd@912*")
    assert "Quantiphi" in pw
    assert " " not in pw
    assert len(pw) <= 64
    # Long company is truncated to keep total <= 64
    long_pw = wk.make_password("A" * 80)
    assert len(long_pw) <= 64
    assert long_pw.startswith("Pswdpswd@912*")


def test_upsert_lookup_roundtrip(tmp_path):
    path = tmp_path / "web_keys.json"
    with mock.patch.object(wk, "WEB_KEYS_PATH", path):
        wk.upsert(
            "quantiphi.wd1.myworkdayjobs.com",
            company="Quantiphi",
            email="dummy+abc@example.com",
            password="Pswdpswd@912*Quantiphi",
            job_id="job-1",
            source="fastfill",
        )
        got = wk.lookup("quantiphi.wd1.myworkdayjobs.com")
        assert got is not None
        assert got["company"] == "Quantiphi"
        assert got["email"] == "dummy+abc@example.com"
        assert got["password"] == "Pswdpswd@912*Quantiphi"
        assert got["job_id"] == "job-1"
        # Reload from disk
        data = wk.load_web_keys()
        assert "quantiphi.wd1.myworkdayjobs.com" in data["sites"]


def test_ensure_password_sets_confirm(tmp_path):
    path = tmp_path / "web_keys.json"
    with mock.patch.object(wk, "WEB_KEYS_PATH", path):
        values = {PASSWORD: "", PASSWORD_CONFIRM: ""}
        pw = wk.ensure_password_for_company(
            "Acme Corp",
            values,
            host="acme.wd1.myworkdayjobs.com",
        )
        assert pw
        assert "***" not in pw  # real password returned to caller, not masked
        assert values[PASSWORD] == pw
        assert values[PASSWORD_CONFIRM] == pw
        assert pw.startswith("Pswdpswd@912*")
        assert "AcmeCorp" in pw or "Acme" in pw


def test_ensure_reuses_lookup(tmp_path):
    path = tmp_path / "web_keys.json"
    with mock.patch.object(wk, "WEB_KEYS_PATH", path):
        wk.upsert(
            "acme.example.com",
            company="Acme",
            email="run@example.com",
            password="StoredPswd@1",
        )
        values = {"EMAIL": "run@example.com", PASSWORD: "", PASSWORD_CONFIRM: ""}
        pw = wk.ensure_password_for_company(
            "Acme",
            values,
            host="acme.example.com",
            email="run@example.com",
        )
        assert pw == "StoredPswd@1"
        assert values[PASSWORD] == "StoredPswd@1"


def test_load_creates_empty_sites(tmp_path):
    path = tmp_path / "web_keys.json"
    with mock.patch.object(wk, "WEB_KEYS_PATH", path):
        data = wk.load_web_keys()
        assert data == {"sites": {}}
        assert path.is_file()


def main() -> int:
    import tempfile

    test_sanitize_company()
    test_make_password_format()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        test_upsert_lookup_roundtrip(p)
        test_ensure_password_sets_confirm(p)
        test_ensure_reuses_lookup(p)
        test_load_creates_empty_sites(p / "empty_sub")
    print("test_web_keys: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

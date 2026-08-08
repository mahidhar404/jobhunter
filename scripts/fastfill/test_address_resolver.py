"""Synthetic apartment selection anchored to the resume header city."""

from __future__ import annotations

import json
from pathlib import Path

from address_resolver import (
    extract_resume_city_state,
    resolve_apartment_address,
    resolve_address_from_text,
)
from field_map import (
    ADDRESS_CITY,
    ADDRESS_LINE1,
    ADDRESS_LINE2,
    ADDRESS_STATE,
    ADDRESS_ZIP,
    apply_resolved_address,
)


def _bank(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "remote_default": {"city": "Chicago", "state": "IL"},
                "addresses": [
                    {
                        "city": "Austin",
                        "state": "TX",
                        "zip": "78701",
                        "street": "742 Maple Avenue",
                        "unit": "Apt 3B",
                    }
                ],
            }
        )
    )


def test_known_resume_city_uses_bank_entry(tmp_path: Path) -> None:
    bank = tmp_path / "addresses.json"
    _bank(bank)
    resume = r"""
    \begin{center}
    {\LARGE Test Dummy}\\
    405-555-0100 | Austin, TX | dummy@example.test
    \end{center}
    """
    assert extract_resume_city_state(resume) == ("Austin", "TX")
    address = resolve_address_from_text(resume, bank_path=bank)
    assert address["city"] == "Austin"
    assert address["state"] == "TX"
    assert address["zip"] == "78701"
    assert address["unit"] == "Apt 3B"


def test_tex_preamble_does_not_hide_header_city() -> None:
    fixture = Path(__file__).parent / "fixtures" / "dummy_resume_de.tex"
    text = fixture.read_text()
    assert len(text.split(r"\begin{document}", 1)[0]) > 2000
    assert extract_resume_city_state(text) == ("Springfield", "IL")


def test_unknown_resume_city_is_generated_and_persisted(tmp_path: Path) -> None:
    bank = tmp_path / "addresses.json"
    _bank(bank)
    address = resolve_apartment_address("Madison", "WI", bank_path=bank)
    assert address["city"] == "Madison"
    assert address["state"] == "WI"
    assert len(address["zip"]) == 5 and address["zip"].isdigit()
    assert address["unit"].startswith(("Apt ", "Unit "))

    saved = json.loads(bank.read_text())
    matches = [
        row
        for row in saved["addresses"]
        if row["city"] == "Madison" and row["state"] == "WI"
    ]
    assert matches == [address]
    assert resolve_apartment_address("Madison", "WI", bank_path=bank) == address


def test_remote_uses_documented_default_metro(tmp_path: Path) -> None:
    bank = tmp_path / "addresses.json"
    _bank(bank)
    address = resolve_address_from_text(
        "Test Dummy\n405-555-0100 | Remote, US | dummy@example.test",
        bank_path=bank,
    )
    assert (address["city"], address["state"]) == ("Chicago", "IL")


def test_explicit_header_city_wins_over_remote_word(tmp_path: Path) -> None:
    """The City, ST printed below the name is authoritative.

    Resumes commonly say ``Austin, TX | Open to Remote``.  The generic
    ``Remote`` marker must not discard the explicit header city and fall back
    to Chicago.
    """
    bank = tmp_path / "addresses.json"
    _bank(bank)
    resume = (
        "Test Dummy\n"
        "405-555-0100 | Austin, TX | Open to Remote | dummy@example.test"
    )
    assert extract_resume_city_state(resume) == ("Austin", "TX")
    address = resolve_address_from_text(resume, bank_path=bank)
    assert (address["city"], address["state"]) == ("Austin", "TX")


def test_field_map_uses_resolved_apartment_parts() -> None:
    values = {"EMAIL": "dummy@example.test"}
    apply_resolved_address(
        values,
        {
            "city": "Austin",
            "state": "TX",
            "zip": "78701",
            "street": "742 Maple Avenue",
            "unit": "Apt 3B",
        },
    )
    assert values[ADDRESS_LINE1] == "742 Maple Avenue"
    assert values[ADDRESS_LINE2] == "Apt 3B"
    assert values[ADDRESS_CITY] == "Austin"
    assert values[ADDRESS_STATE] == "TX"
    assert values[ADDRESS_ZIP] == "78701"
    assert values["EMAIL"] == "dummy@example.test"

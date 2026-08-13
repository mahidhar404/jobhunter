#!/usr/bin/env python3
"""Unit tests: Workday automation-id ontology + FoS family lock expand.

Dummy-only; never submit. Proves locking FoS blocks Major/Discipline retouch.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def test_ontology_fos_family_members():
    from workday_aid_ontology import expand_lock_aids, expand_lock_types, family_for

    fam = family_for(field_type="FIELD_OF_STUDY")
    assert fam is not None and fam.name == "fos"
    aids = expand_lock_aids(field_type="FIELD_OF_STUDY")
    assert "education/Major" in aids
    assert "formField-discipline" in aids or "education/Discipline" in aids
    types = expand_lock_types(automation_id="education/fieldOfStudy")
    assert {"FIELD_OF_STUDY", "MAJOR", "DISCIPLINE"} <= types
    # 0842 alias storm: select_one / edu_prompt lock with the same FoS family
    assert family_for(automation_id="select_one:Field of Study").name == "fos"
    assert family_for(label="edu_prompt:Major").name == "fos" or family_for(
        automation_id="edu_prompt:Major"
    ).name == "fos"


def test_ontology_school_and_degree_families():
    from workday_aid_ontology import family_for, lock_expands_family

    assert family_for(field_type="SCHOOL").name == "school"
    assert family_for(automation_id="formField-school").name == "school"
    assert family_for(automation_id="schoolName").name == "school"
    assert family_for(field_type="DEGREE").name == "degree"
    assert family_for(automation_id="formField-degree").name == "degree"
    assert family_for(automation_id="select_one:Degree").name == "degree"
    assert lock_expands_family("SCHOOL", probe_aid="educationSection_school")
    assert lock_expands_family("DEGREE", probe_aid="select_one:Degree")


def test_ontology_address_state_not_phone():
    from workday_aid_ontology import family_for

    assert family_for(automation_id="addressSection_countryRegion").name == "address_state"
    # Phone country must not collapse into address state
    phone = family_for(automation_id="countryPhoneCode", label="Country Phone Code")
    assert phone is None or phone.name != "address_state"


def test_ontology_how_heard():
    from workday_aid_ontology import family_for, related_aids

    assert family_for(automation_id="how_heard").name == "how_heard"
    assert "source--source" in related_aids("how_heard")


def test_lock_fos_blocks_major_discipline_retouch():
    """Breakthrough proof: lock FoS → Major/Discipline gate lock_skip."""
    from field_lock import attach_field_locks, gate_field_action
    from workday_aid_ontology import lock_expands_family

    assert lock_expands_family(
        "FIELD_OF_STUDY", probe_type="MAJOR", probe_aid="education/Major"
    )
    assert lock_expands_family(
        "FIELD_OF_STUDY", probe_type="DISCIPLINE", probe_label="Discipline"
    )

    report: dict = {}
    sess = attach_field_locks(report)
    sess.lock(
        field_type="FIELD_OF_STUDY",
        automation_id="education/fieldOfStudy",
        readback="Science-Computer",
        via="layer_a",
    )
    # Ontology expand should have sibling type keys
    locked_types = sess.locked_types()
    assert "FIELD_OF_STUDY" in locked_types
    assert "MAJOR" in locked_types or sess.is_locked(
        field_type="MAJOR", automation_id="education/Major"
    )

    for ft, aid, lab in (
        ("MAJOR", "education/Major", "Major"),
        ("DISCIPLINE", "formField-discipline", "Discipline"),
        ("FIELD_OF_STUDY", "educationSection_fieldOfStudy", "Field of Study"),
    ):
        g = gate_field_action(
            report, field_type=ft, automation_id=aid, label=lab
        )
        assert g and g.get("action") == "lock_skip", (ft, aid, g)


def test_wrong_lock_unlocks_on_intent_mismatch():
    from field_lock import (
        attach_field_locks,
        unlock_fos_if_intent_mismatch,
        gate_field_action,
    )

    report: dict = {}
    sess = attach_field_locks(report)
    sess.lock(
        field_type="FIELD_OF_STUDY",
        automation_id="education/fieldOfStudy",
        readback="Arts-Other",
        via="wrong_autofill",
    )
    info = unlock_fos_if_intent_mismatch(
        report,
        intent="Computer Science",
        candidates=["Computer Science", "Science-Computer"],
    )
    assert info and info.get("unlocked_fos_mismatch")
    assert not sess.is_locked(
        field_type="FIELD_OF_STUDY", automation_id="education/fieldOfStudy"
    )
    g = gate_field_action(
        report,
        field_type="FIELD_OF_STUDY",
        automation_id="education/fieldOfStudy",
    )
    assert g and g.get("action") == "proceed"


def test_fos_chip_label_strips_listbox_soup():
    from verified_select import fos_committed_chip_label, _fos_intent_matches_candidate

    polluted = (
        "Field of Study* Arts-Other × Arts-Other Science-Computer "
        "Computer Science Engineering-Other"
    )
    chip = fos_committed_chip_label(polluted)
    assert chip.lower().startswith("arts-other"), chip
    assert not _fos_intent_matches_candidate("Computer Science", polluted)
    assert not _fos_intent_matches_candidate("Science-Computer", polluted)
    assert _fos_intent_matches_candidate(
        "Computer Science", "Field of Study* Science-Computer ×"
    )


def test_layer_a_lock_layer_b_skip_adversarial():
    """Layer A commit → Layer B (leftover/Flash) must skip FoS family."""
    from field_lock import attach_field_locks, filter_locked_leftovers

    report: dict = {
        "leftovers": [
            {"type": "MAJOR", "automation_id": "education/Major", "label": "Major"},
            {"type": "DISCIPLINE", "automation_id": "formField-discipline"},
            {"type": "WORKED_HERE_BEFORE", "automation_id": "worked_here_before"},
        ]
    }
    sess = attach_field_locks(report)
    sess.lock(
        field_type="FIELD_OF_STUDY",
        automation_id="education/fieldOfStudy",
        readback="Science-Computer",
        via="layer_a",
    )
    kept = filter_locked_leftovers(report)
    aids = {r.get("automation_id") for r in kept}
    assert "education/Major" not in aids
    assert "formField-discipline" not in aids
    assert "worked_here_before" in aids


def test_address_state_family_blocks_sibling_aid():
    from field_lock import attach_field_locks, gate_field_action

    report: dict = {}
    sess = attach_field_locks(report)
    sess.lock(
        field_type="ADDRESS_STATE",
        automation_id="addressSection_countryRegion",
        readback="Illinois",
        via="layer_a",
    )
    g = gate_field_action(
        report,
        field_type="ADDRESS_STATE",
        automation_id="formField-countryRegion",
        label="State / Province",
    )
    assert g and g.get("action") == "lock_skip", g
    g_phone = gate_field_action(
        report,
        field_type="PHONE_COUNTRY_CODE",
        automation_id="countryPhoneCode",
    )
    assert g_phone and g_phone.get("action") == "proceed"


if __name__ == "__main__":
    test_ontology_fos_family_members()
    test_ontology_school_and_degree_families()
    test_ontology_address_state_not_phone()
    test_ontology_how_heard()
    test_lock_fos_blocks_major_discipline_retouch()
    test_wrong_lock_unlocks_on_intent_mismatch()
    test_fos_chip_label_strips_listbox_soup()
    test_layer_a_lock_layer_b_skip_adversarial()
    test_address_state_family_blocks_sibling_aid()
    print("test_workday_aid_ontology: OK")

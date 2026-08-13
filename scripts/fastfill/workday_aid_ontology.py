"""Workday automation-id ontology graph — related aids expand as one lock family.

Dummy-only; never submit. Pure structure — no PII.

Families
--------
- ``fos``: Field of Study ↔ Major ↔ Discipline (edu aliases share one chip)
- ``address_state``: countryRegion ↔ State/Province (NOT country phone)
- ``how_heard``: hierarchical source / how_heard chip

When any member locks after an honest verified fill, the whole family is treated
as locked (see ``field_lock.FOS_LOCK_TYPES`` + ``expand_lock_aids``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class AidFamily:
    name: str
    field_types: frozenset[str]
    automation_ids: frozenset[str]
    labels: frozenset[str] = frozenset()


# Canonical Workday aid graph (wd5+ apply flow + edu aliases).
AID_FAMILIES: tuple[AidFamily, ...] = (
    AidFamily(
        name="fos",
        field_types=frozenset({"FIELD_OF_STUDY", "DISCIPLINE", "MAJOR"}),
        automation_ids=frozenset(
            {
                "formField-fieldOfStudy",
                "fieldOfStudy",
                "educationSection_fieldOfStudy",
                "education/fieldOfStudy",
                "education/Major",
                "education/Discipline",
                "formField-discipline",
                "formField-major",
                "discipline",
                "major",
                "select_one:Field of Study",
                "select_one:Discipline",
                "select_one:Major",
                "edu_prompt:Field of Study",
                "edu_prompt:Discipline",
                "edu_prompt:Major",
            }
        ),
        labels=frozenset(
            {
                "field of study",
                "discipline",
                "major",
                "area of study",
            }
        ),
    ),
    AidFamily(
        name="school",
        field_types=frozenset({"SCHOOL"}),
        automation_ids=frozenset(
            {
                "formField-school",
                "schoolName",
                "school",
                "educationSection_school",
                "education/school",
                "select_one:School",
            }
        ),
        labels=frozenset({"school", "university", "college"}),
    ),
    AidFamily(
        name="degree",
        field_types=frozenset({"DEGREE"}),
        automation_ids=frozenset(
            {
                "formField-degree",
                "degree",
                "educationSection_degree",
                "education/degree",
                "select_one:Degree",
            }
        ),
        labels=frozenset({"degree"}),
    ),
    AidFamily(
        name="address_state",
        field_types=frozenset({"ADDRESS_STATE"}),
        automation_ids=frozenset(
            {
                "addressSection_countryRegion",
                "formField-countryRegion",
                "countryRegion",
            }
        ),
        labels=frozenset({"state", "province", "state / province", "country region"}),
    ),
    AidFamily(
        name="how_heard",
        field_types=frozenset({"HOW_HEARD"}),
        automation_ids=frozenset(
            {
                "how_heard",
                "source--source",
                "source",
                "formField-source",
                "howHeard",
            }
        ),
        labels=frozenset(
            {
                "how did you hear",
                "where did you hear",
                "referral source",
            }
        ),
    ),
)


def family_for(
    *,
    field_type: str | None = None,
    automation_id: str | None = None,
    label: str | None = None,
) -> AidFamily | None:
    """Resolve which ontology family a target belongs to (or None)."""
    ft = (field_type or "").strip().upper()
    aid = (automation_id or "").strip()
    aid_l = aid.lower()
    lab = (label or "").strip().lower()
    for fam in AID_FAMILIES:
        if ft and ft in fam.field_types:
            return fam
        if aid and (aid in fam.automation_ids or aid_l in {a.lower() for a in fam.automation_ids}):
            return fam
        if aid_l and any(a.lower() in aid_l or aid_l in a.lower() for a in fam.automation_ids):
            # Avoid country phone code false-hit on address_state
            if fam.name == "address_state" and (
                "phone" in aid_l or "countryphonecode" in aid_l
            ):
                continue
            if fam.name == "fos" and any(
                k in aid_l.replace(" ", "")
                for k in ("fieldofstudy", "discipline", "major")
            ):
                return fam
            if fam.name == "school" and any(
                k in aid_l for k in ("school", "university", "college")
            ):
                return fam
            if fam.name == "degree" and "degree" in aid_l.replace(" ", ""):
                return fam
            if fam.name == "address_state" and "countryregion" in aid_l:
                return fam
            if fam.name == "how_heard" and (
                "howheard" in aid_l or aid_l in ("source", "source--source", "how_heard")
            ):
                return fam
        if lab and any(x in lab for x in fam.labels):
            if fam.name == "address_state" and ("phone" in lab or "dial" in lab):
                continue
            return fam
    return None


def expand_lock_aids(
    *,
    field_type: str | None = None,
    automation_id: str | None = None,
    label: str | None = None,
) -> frozenset[str]:
    """Return all automation-ids that should lock together with this target."""
    fam = family_for(
        field_type=field_type, automation_id=automation_id, label=label
    )
    if fam is None:
        aid = (automation_id or "").strip()
        return frozenset({aid} if aid else ())
    return fam.automation_ids


def expand_lock_types(
    *,
    field_type: str | None = None,
    automation_id: str | None = None,
    label: str | None = None,
) -> frozenset[str]:
    """Return all field types in the same ontology family."""
    fam = family_for(
        field_type=field_type, automation_id=automation_id, label=label
    )
    if fam is None:
        ft = (field_type or "").strip().upper()
        return frozenset({ft} if ft else ())
    return fam.field_types


def related_aids(automation_id: str) -> frozenset[str]:
    """Sibling aids for one automation-id (empty if unknown)."""
    fam = family_for(automation_id=automation_id)
    return fam.automation_ids if fam else frozenset()


def lock_expands_family(
    locked_type: str | None,
    *,
    probe_type: str | None = None,
    probe_aid: str | None = None,
    probe_label: str | None = None,
) -> bool:
    """True when locking ``locked_type`` should block the probe target."""
    locked_fam = family_for(field_type=locked_type)
    if locked_fam is None:
        return False
    probe_fam = family_for(
        field_type=probe_type, automation_id=probe_aid, label=probe_label
    )
    return probe_fam is not None and probe_fam.name == locked_fam.name


def iter_family_edges() -> Iterable[tuple[str, str, str]]:
    """Yield (family, member_a, member_b) undirected edges for tests / viz."""
    for fam in AID_FAMILIES:
        aids = sorted(fam.automation_ids)
        for i, a in enumerate(aids):
            for b in aids[i + 1 :]:
                yield fam.name, a, b


def self_test() -> None:
    assert family_for(field_type="FIELD_OF_STUDY") is not None
    assert family_for(field_type="FIELD_OF_STUDY").name == "fos"  # type: ignore[union-attr]
    assert "education/Major" in expand_lock_aids(field_type="FIELD_OF_STUDY")
    assert lock_expands_family(
        "FIELD_OF_STUDY", probe_type="MAJOR", probe_aid="education/Major"
    )
    assert lock_expands_family(
        "FIELD_OF_STUDY", probe_type="DISCIPLINE", probe_label="Discipline"
    )
    assert not lock_expands_family(
        "FIELD_OF_STUDY", probe_type="EMAIL", probe_aid="email"
    )
    assert family_for(automation_id="addressSection_countryRegion").name == "address_state"  # type: ignore[union-attr]
    assert family_for(automation_id="countryPhoneCode") is None or family_for(
        automation_id="countryPhoneCode"
    ).name != "address_state"
    assert family_for(automation_id="how_heard").name == "how_heard"  # type: ignore[union-attr]
    assert family_for(field_type="SCHOOL").name == "school"  # type: ignore[union-attr]
    assert family_for(automation_id="select_one:Degree").name == "degree"  # type: ignore[union-attr]
    print("workday_aid_ontology.self_test: OK")


if __name__ == "__main__":
    self_test()

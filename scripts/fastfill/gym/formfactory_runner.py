#!/usr/bin/env python3
"""Offline FormFactory gym — discover templates, fill with dummy data, score via DOM."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

GYM_DIR = Path(__file__).resolve().parent / "formfactory"
VENDOR_DIR = GYM_DIR / "vendor"
TEMPLATES_DIR = VENDOR_DIR / "templates"
GOLD_DIR = VENDOR_DIR / "data" / "data1"

SKIP_TEMPLATES = {"base.html", "home.html", "form.html", "form2.html"}

# Template filename -> gold JSON stem (data/data1/{stem}.json)
TEMPLATE_GOLD_MAP: dict[str, str] = {
    "A11.html": "job_applications",
    "A12.html": "grant_applications",
    "A13.html": "scholarship_applications",
    "A14.html": "paper_submissions",
    "A15.html": "student_courses",
    "B11.html": "startup_funding_applications",
    "B12.html": "real_estate_rental_applications",
    "B13.html": "workshop_registrations",
    "B14.html": "membership_application",
    "C11.html": "Art_Exhibition_Submission_Form",
    "C12.html": "Literary_Magazine_Submission",
    "C13.html": "Conference_Speaker_Application",
    "D11.html": "Bug_report",
    "D12.html": "IT_support",
    "E11.html": "person_loan_applications",
    "E12.html": "bank_account_applications",
    "E13.html": "financial_planning",
    "F11.html": "Patient_Consent",
    "F12.html": "Medical_study_Form",
    "F13.html": "Health_Insurance",
    "G11.html": "NDA",
    "G12.html": "Background_check",
    "G13.html": "Contrator_onboard",
    "H11.html": "Project_Bid",
    "H12.html": "Manufacturing_Order",
    # Stub templates
    "job_app.html": "job_app",
    "registration.html": "registration",
    "contact.html": "contact",
}

DUMMY: dict[str, str] = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "middle_name": "Augusta",
    "full_name": "Ada Lovelace",
    "email": "ada.lovelace+gym@example.com",
    "phone": "+1-555-0100",
    "position": "Software Engineer",
    "department": "Engineering",
    "cover_letter": (
        "I am passionate about computing and eager to contribute my analytical "
        "skills to your team."
    ),
    "student_id": "GYM-1843",
    "gpa": "3.85",
    "major": "Mathematics",
    "academic_year": "senior",
    "financial_aid": "grants",
    "family_income": "30001-60000",
    "achievements": "Dean's List; First computer program author",
    "extracurricular": "Analytical Engine study group",
    "paper_title": "Notes on the Analytical Engine",
    "abstract": "A method for mechanical computation using punched cards.",
    "keywords": "computing, algorithms, mathematics",
    "paper_category": "Computer Science",
    "semester": "Fall 2025",
    "program": "Software Engineering",
    "comments": "No special requirements.",
    "default": "Gym test value",
}

GRANT_FORM_CONFIG = {
    "title": "Grant Application",
    "label_position": "top",
    "allowManualInput": True,
    "fields": [
        {"name": "first_name", "label": "First Name", "type": "string", "required": True, "row": 1, "col": 3},
        {"name": "last_name", "label": "Last Name", "type": "string", "required": True, "row": 1, "col": 3},
        {"name": "email", "label": "Email", "type": "email", "required": True, "row": 2, "col": 6},
        {"name": "dob", "label": "Date of Birth", "type": "date", "required": True, "row": 2, "col": 6},
        {"name": "gender", "label": "Gender", "type": "radio", "options": ["Male", "Female"], "required": True, "row": 3},
        {"name": "subscribe", "label": "Subscribe to Newsletter", "type": "checkbox", "required": False, "row": 4},
    ],
}


@dataclass
class FormField:
    name: str
    label: str
    tag: str
    input_type: str = "text"
    options: list[str] = field(default_factory=list)


@dataclass
class GymCase:
    template: str
    gold_stem: str
    gold_path: Path
    html_path: Path


@dataclass
class CaseResult:
    template: str
    gold_stem: str
    passed: bool
    field_accuracy: float
    matched_fields: int
    correct_fields: int
    total_expected: int
    errors: list[str] = field(default_factory=list)


def is_stub_mode() -> bool:
    """True when vendor is the minimal offline stub (3 simple forms)."""
    if not TEMPLATES_DIR.is_dir():
        return True
    stub_markers = {"job_app.html", "registration.html", "contact.html"}
    names = {p.name for p in TEMPLATES_DIR.glob("*.html")}
    return stub_markers.issubset(names) and "A11.html" not in names


def discover_cases() -> list[GymCase]:
    if not TEMPLATES_DIR.is_dir():
        return []

    cases: list[GymCase] = []
    for html_path in sorted(TEMPLATES_DIR.glob("*.html")):
        name = html_path.name
        if name in SKIP_TEMPLATES:
            continue
        gold_stem = TEMPLATE_GOLD_MAP.get(name)
        if not gold_stem:
            continue
        gold_path = GOLD_DIR / f"{gold_stem}.json"
        if not gold_path.is_file():
            continue
        cases.append(GymCase(name, gold_stem, gold_path, html_path))
    return cases


def load_gold_schema(gold_path: Path) -> list[str]:
    with open(gold_path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list) and data:
        record = data[0]
    elif isinstance(data, dict):
        record = data
    else:
        return []
    return list(record.keys())


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def dummy_for_gold_key(key: str) -> str | bool:
    kl = _normalize(key)

    if kl == "first name":
        return DUMMY["first_name"]
    if kl == "middle name":
        return DUMMY["middle_name"]
    if kl == "last name":
        return DUMMY["last_name"]
    if kl in {"full name", "applicant name", "author name", "name", "your name"}:
        return DUMMY["full_name"]
    if "email" in kl:
        return DUMMY["email"]
    if "phone" in kl:
        return DUMMY["phone"]
    if "position" in kl or "job title" in kl:
        return DUMMY["position"]
    if "department" in kl:
        return DUMMY["department"]
    if any(x in kl for x in ("cover letter", "statement of purpose", "personal statement", "essay")):
        return DUMMY["cover_letter"]
    if "abstract" in kl:
        return DUMMY["abstract"]
    if "keyword" in kl:
        return DUMMY["keywords"]
    if "paper title" in kl or kl == "title":
        return DUMMY["paper_title"]
    if "category" in kl:
        return DUMMY["paper_category"]
    if "student id" in kl:
        return DUMMY["student_id"]
    if "gpa" in kl:
        return DUMMY["gpa"]
    if "major" in kl or "field of study" in kl:
        return DUMMY["major"]
    if "academic year" in kl:
        return DUMMY["academic_year"]
    if "financial aid" in kl:
        return DUMMY["financial_aid"]
    if "family income" in kl or "annual family income" in kl:
        return DUMMY["family_income"]
    if "achievement" in kl:
        return DUMMY["achievements"]
    if "extracurricular" in kl or "activit" in kl:
        return DUMMY["extracurricular"]
    if "reference email" in kl:
        return DUMMY["email"]
    if "reference" in kl:
        return "Prof. Charles Babbage"
    if "semester" in kl:
        return DUMMY["semester"]
    if "program" in kl:
        return DUMMY["program"]
    if "comment" in kl or "requirement" in kl:
        return DUMMY["comments"]
    if kl == "gender":
        return "Female"
    if "date of birth" in kl or kl == "dob":
        return "1815/12/10"
    if "subscribe" in kl:
        return False
    if "message" in kl or "feedback" in kl or "description" in kl:
        return "Gym contact message from Ada Lovelace."
    if "subject" in kl:
        return "FormFactory gym inquiry"
    return DUMMY["default"]


def parse_form_fields_from_page(page) -> list[FormField]:
    """Extract fields from a live DOM (handles JS-rendered forms like A12)."""
    raw = page.evaluate(
        """() => {
        const out = [];
        const seen = new Set();
        const form = document.querySelector('form') || document.body;
        form.querySelectorAll('input, textarea, select').forEach(el => {
            const type = (el.type || el.tagName.toLowerCase()).toLowerCase();
            if (!el.name || ['hidden','submit','button','file'].includes(type)) return;
            if (seen.has(el.name)) return;
            seen.add(el.name);
            let label = el.name.replace(/_/g, ' ');
            const lbl = form.querySelector(`label[for="${el.name}"]`);
            if (lbl) label = lbl.textContent.trim();
            else {
                const wrap = el.closest('.form-group, .form-check, .mb-3');
                const l2 = wrap && wrap.querySelector('label');
                if (l2 && l2.textContent) label = l2.textContent.trim();
            }
            const options = [];
            if (el.tagName === 'SELECT') {
                el.querySelectorAll('option').forEach(o => {
                    if (o.value) options.push(o.value);
                });
            }
            out.push({name: el.name, label, tag: el.tagName.toLowerCase(), input_type: type, options});
        });
        return out;
    }"""
    )
    return [
        FormField(
            name=item["name"],
            label=item["label"],
            tag=item["tag"],
            input_type=item["input_type"],
            options=item.get("options") or [],
        )
        for item in raw
    ]


def parse_form_fields(html: str) -> list[FormField]:
    fields: list[FormField] = []
    labels: dict[str, str] = {}
    for m in re.finditer(
        r'<label[^>]*\sfor=["\']([^"\']+)["\'][^>]*>(.*?)</label>',
        html,
        re.I | re.S,
    ):
        fid = m.group(1)
        label = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        labels[fid] = label

    patterns = [
        (r"<(input|textarea|select)\b([^>]*)>", "tag"),
    ]
    for m in re.finditer(r"<(input|textarea|select)\b([^>]*)>", html, re.I):
        tag = m.group(1).lower()
        attrs = m.group(2)
        name_m = re.search(r'\bname=["\']([^"\']+)["\']', attrs, re.I)
        if not name_m:
            continue
        name = name_m.group(1)
        type_m = re.search(r'\btype=["\']([^"\']+)["\']', attrs, re.I)
        input_type = type_m.group(1).lower() if type_m else ("textarea" if tag == "textarea" else tag)
        if input_type in {"file", "submit", "button", "hidden"}:
            continue
        id_m = re.search(r'\bid=["\']([^"\']+)["\']', attrs, re.I)
        fid = id_m.group(1) if id_m else name
        label = labels.get(fid, name.replace("_", " ").title())
        options: list[str] = []
        if tag == "select":
            block = html[m.end() : m.end() + 800]
            options = re.findall(r'<option[^>]*\svalue=["\']([^"\']*)["\']', block, re.I)
            options = [o for o in options if o]
        fields.append(FormField(name=name, label=label, tag=tag, input_type=input_type, options=options))
    return fields


def match_gold_to_fields(gold_keys: list[str], form_fields: list[FormField]) -> dict[str, FormField]:
    mapping: dict[str, FormField] = {}
    used: set[str] = set()
    for key in gold_keys:
        best: FormField | None = None
        best_score = 0.0
        for ff in form_fields:
            if ff.name in used:
                continue
            score = max(
                _similarity(key, ff.label),
                _similarity(key, ff.name.replace("_", " ")),
            )
            if score > best_score:
                best_score = score
                best = ff
        if best and best_score >= 0.45:
            mapping[key] = best
            used.add(best.name)
    return mapping


def build_expected_values(gold_keys: list[str], form_fields: list[FormField]) -> dict[str, Any]:
    matched = match_gold_to_fields(gold_keys, form_fields)
    expected: dict[str, Any] = {}
    for key, ff in matched.items():
        val = dummy_for_gold_key(key)
        if ff.tag == "select" and ff.options:
            if isinstance(val, str) and val not in ff.options:
                val = ff.options[0]
        expected[ff.name] = val
    return expected


def prepare_html(case: GymCase) -> str:
    raw = case.html_path.read_text(encoding="utf-8")

    if case.template == "A12.html":
        config_json = json.dumps(GRANT_FORM_CONFIG)
        raw = re.sub(
            r"fetch\(['\"]/form-config['\"]\)\s*\n\s*\.then\(response => response\.json\(\)\)",
            f"Promise.resolve({config_json})",
            raw,
        )

    if "{% extends" in raw:
        content_m = re.search(r"{% block content %}(.*?){% endblock %}", raw, re.S)
        content = content_m.group(1) if content_m else raw
        title_m = re.search(r"{% block title %}(.*?){% endblock %}", raw)
        title = title_m.group(1).strip() if title_m else case.template
        raw = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
</head>
<body><div class="container mt-5">{content}</div></body>
</html>"""

    # Never POST externally — strip method/action and block submit.
    raw = re.sub(r'\smethod=["\']POST["\']', "", raw, flags=re.I)
    raw = re.sub(r'\saction=["\'][^"\']*["\']', "", raw, flags=re.I)
    raw = raw.replace('type="submit"', 'type="button"')
    raw = raw.replace("type='submit'", "type='button'")
    return raw


def _fill_field(page, ff: FormField, value: Any) -> None:
    sel = f'[name="{ff.name}"]'
    if ff.input_type == "checkbox":
        checked = page.locator(sel).is_checked()
        want = bool(value)
        if checked != want:
            page.locator(sel).click()
        return
    if ff.input_type == "radio":
        page.locator(f'{sel}[value="{value}"]').first.check()
        return
    if ff.tag == "select":
        page.locator(sel).select_option(str(value))
        return
    page.locator(sel).fill(str(value))


def _read_field(page, ff: FormField) -> Any:
    sel = f'[name="{ff.name}"]'
    if ff.input_type == "checkbox":
        return page.locator(sel).is_checked()
    if ff.input_type == "radio":
        checked = page.locator(f"{sel}:checked")
        if checked.count():
            return checked.first.get_attribute("value") or checked.first.input_value()
        return ""
    if ff.tag == "select":
        return page.locator(sel).input_value()
    return page.locator(sel).input_value()


def _values_match(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool):
        return bool(actual) == expected
    exp = _normalize(str(expected))
    act = _normalize(str(actual))
    if not exp and not act:
        return True
    if exp == act:
        return True
    return _similarity(exp, act) >= 0.85


def run_single_case(case: GymCase, *, headless: bool = True) -> CaseResult:
    from playwright.sync_api import sync_playwright

    html = prepare_html(case)
    gold_keys = load_gold_schema(case.gold_path)
    dynamic = case.template == "A12.html"
    form_fields = parse_form_fields(html)
    expected_by_name = build_expected_values(gold_keys, form_fields)
    field_by_name = {ff.name: ff for ff in form_fields}

    if not expected_by_name and not dynamic:
        return CaseResult(
            template=case.template,
            gold_stem=case.gold_stem,
            passed=False,
            field_accuracy=0.0,
            matched_fields=0,
            correct_fields=0,
            total_expected=len(gold_keys),
            errors=["no fillable fields matched gold schema"],
        )

    errors: list[str] = []
    correct = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            if dynamic:
                page.set_content(html, wait_until="load")
                page.wait_for_selector(
                    '#dynamicForm input[name="first_name"]',
                    state="attached",
                    timeout=15000,
                )
                form_fields = parse_form_fields_from_page(page)
                expected_by_name = build_expected_values(gold_keys, form_fields)
                field_by_name = {ff.name: ff for ff in form_fields}
            else:
                page.set_content(html, wait_until="domcontentloaded")

            for name, val in expected_by_name.items():
                ff = field_by_name.get(name)
                if not ff:
                    continue
                try:
                    _fill_field(page, ff, val)
                except Exception as exc:
                    errors.append(f"fill {name}: {exc}")

            for name, val in expected_by_name.items():
                ff = field_by_name.get(name)
                if not ff:
                    continue
                try:
                    actual = _read_field(page, ff)
                    if _values_match(val, actual):
                        correct += 1
                    else:
                        errors.append(f"{name}: expected {val!r}, got {actual!r}")
                except Exception as exc:
                    errors.append(f"read {name}: {exc}")

            if not expected_by_name:
                errors.append("no fillable fields matched gold schema")

        finally:
            browser.close()

    matched = len(expected_by_name)
    accuracy = correct / matched if matched else 0.0
    passed = matched > 0 and correct == matched
    return CaseResult(
        template=case.template,
        gold_stem=case.gold_stem,
        passed=passed,
        field_accuracy=accuracy,
        matched_fields=matched,
        correct_fields=correct,
        total_expected=len(gold_keys),
        errors=errors,
    )


def run_formfactory_gym(*, smoke: bool = True, headless: bool = True) -> dict[str, Any]:
    cases = discover_cases()
    if smoke:
        cases = cases[:3]

    if not cases:
        return {
            "ok": False,
            "n": 0,
            "passed": 0,
            "field_accuracy": 0.0,
            "cases": [],
            "stub_mode": is_stub_mode(),
            "error": "no form cases discovered",
        }

    results = [run_single_case(c, headless=headless) for c in cases]
    passed_count = sum(1 for r in results if r.passed)
    total_matched = sum(r.matched_fields for r in results)
    total_correct = sum(r.correct_fields for r in results)
    field_accuracy = total_correct / total_matched if total_matched else 0.0

    return {
        "ok": passed_count == len(results),
        "n": len(results),
        "passed": passed_count,
        "field_accuracy": round(field_accuracy, 4),
        "stub_mode": is_stub_mode(),
        "cases": [
            {
                "template": r.template,
                "gold": r.gold_stem,
                "passed": r.passed,
                "field_accuracy": round(r.field_accuracy, 4),
                "matched_fields": r.matched_fields,
                "correct_fields": r.correct_fields,
                "errors": r.errors[:5],
            }
            for r in results
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FormFactory offline gym runner")
    parser.add_argument("--self-test", action="store_true", help="Smoke test (first 3 forms)")
    parser.add_argument("--full", action="store_true", help="Run all discovered forms")
    parser.add_argument("--list", action="store_true", help="List discovered cases")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    args = parser.parse_args(argv)

    if args.list:
        cases = discover_cases()
        mode = "stub" if is_stub_mode() else "full"
        print(f"FormFactory gym ({mode}): {len(cases)} cases")
        for c in cases:
            print(f"  {c.template} -> {c.gold_stem}.json")
        return 0

    smoke = not args.full
    if args.self_test:
        smoke = True

    if not (args.self_test or args.full or args.list):
        parser.print_help()
        return 2

    summary = run_formfactory_gym(smoke=smoke, headless=not args.headed)
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

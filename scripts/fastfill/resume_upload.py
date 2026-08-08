"""Deterministic dummy resume upload + verification for fastfill.

Hard rules:
  - Dummy PDF only (prepare_dummy_run / DUMMY_PDF) — never tailored resumes
  - Verify files on the input (filename / FileList) before claiming success
  - Missing resume when a resume field exists = failure, not success
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from field_map import (
    DUMMY_PDF,
    RESUME_UPLOAD,
    assert_dummy_resume_path,
    assert_real_resume_path,
    is_real_profile_mode,
)

try:
    from fill_step_log import note_step
except ImportError:  # pragma: no cover
    def note_step(report, **kwargs):  # type: ignore
        return None

# Ashby binds the required gate to ``_systemfield_resume`` — prefer it over
# sibling dropzone inputs that may accept files without clearing the gate.
_ASHBY_RESUME_SELECTORS = (
    "input[id='_systemfield_resume']",
    "input[name='_systemfield_resume']",
)

# Prefer resume/CV-ish inputs over generic attachment slots when multiple exist.
_RESUME_FILE_SELECTORS = (
    # Workday autofill / experience resume gate
    "[data-automation-id='file-upload-input-ref']",
    "input[data-automation-id='file-upload-input-ref']",
    # Ashby system resume field (required gate) — before generic id*=resume
    *_ASHBY_RESUME_SELECTORS,
    # Personio *.jobs.personio.{com,de} — CV input name/id (not optional cover-letter slot)
    "#doc-input-cv",
    "input[name='documents.cv']",
    "input[type=file][id*='resume' i]",
    "input[type=file][name*='resume' i]",
    "input[type=file][id*='cv' i]",
    "input[type=file][name*='cv' i]",
    "[data-testid='input-resume']",
    "[data-automation-id='file-upload-input-ref']",
    "#resume_document",
    "#resumator-resume-value",
    # Greenhouse job-boards (Savant/Scout/Extend): #resume input remounts after upload
    "#resume",
    "input#resume",
    "label[for='resume'] >> xpath=following::input[@type='file'][1]",
    ".file-upload:has(#upload-label-resume) input[type=file]",
    ".file-upload:has([id*='upload-label-resume']) input[type=file]",
    # Greenhouse job-boards (Tax Relief / modern GH): often hidden until dropzone click
    "input[type=file][data-testid*='resume' i]",
    "input[type=file][accept*='pdf' i]",
    "div[data-testid*='resume' i] input[type=file]",
    "label:has-text('Resume') input[type=file]",
    "label:has-text('CV') input[type=file]",
    "[class*='resume' i] input[type=file]",
    "[class*='dropzone' i] input[type=file]",
    "input[type=file]",
)

_VERIFY_JS = """(el) => {
  if (!el) return {ok: false, reason: 'no_el'};
  const files = el.files;
  if (!files || files.length < 1) return {ok: false, reason: 'no_files', count: 0};
  const name = (files[0] && files[0].name) || '';
  return {ok: true, reason: 'files_on_input', count: files.length, name};
}"""

_PAGE_FILENAME_HINT_JS = """(needle) => {
  const n = (needle || '').toLowerCase();
  if (!n) return false;
  const body = (document.body && document.body.innerText || '').toLowerCase();
  if (body.includes(n)) return true;
  // Common ATS confirmation nodes
  const nodes = document.querySelectorAll(
    '[data-testid*="resume" i], [class*="resume" i], [class*="upload" i], '
    + '[data-automation-id*="file" i], [aria-label*="resume" i]'
  );
  for (const el of nodes) {
    const t = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase();
    if (t.includes(n) || /\\.pdf\\b/.test(t)) return true;
  }
  return false;
}"""

_ASHBY_RESUME_UPLOADED_JS = """() => {
  const entries = Array.from(document.querySelectorAll(
    '.ashby-application-form-field-entry, [class*="_fieldEntry_"]'
  ));
  for (const el of entries) {
    const labEl = el.querySelector(
      'label.ashby-application-form-question-title, label[class*="_heading_"], label'
    );
    const labT = (labEl && (labEl.innerText || labEl.textContent) || '').toLowerCase();
    if (!/resume|\\bcv\\b|curriculum/.test(labT) || /cover\\s*letter/.test(labT)) continue;
    const body = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    if (/\\.(pdf|doc|docx|txt|rtf)\\b/i.test(body)) {
      const m = body.match(/([^\\s]+\\.(pdf|doc|docx|txt|rtf))/i);
      return {uploaded: true, name: m ? m[1] : body.slice(0, 120), via: 'ashby_upload_ui'};
    }
    const inp = el.querySelector('input[type=file]');
    if (inp && inp.files && inp.files.length > 0) {
      const n = (inp.files[0] && inp.files[0].name) || '';
      if (n) return {uploaded: true, name: n, via: 'files_on_input'};
    }
  }
  for (const inp of document.querySelectorAll(
    'input[id="_systemfield_resume"], input[name="_systemfield_resume"]'
  )) {
    if (inp.files && inp.files.length > 0) {
      const n = (inp.files[0] && inp.files[0].name) || '';
      if (n) return {uploaded: true, name: n, via: 'systemfield_files'};
    }
  }
  return {uploaded: false, name: ''};
}"""

_GH_RESUME_UPLOADED_JS = """() => {
  for (const fu of document.querySelectorAll('.file-upload, [class*="file-upload"]')) {
    const labEl = fu.querySelector('[id*="upload-label"], .upload-label, label');
    const labT = (labEl && (labEl.innerText || labEl.textContent) || '').toLowerCase();
    if (!/resume|\\bcv\\b/.test(labT) || /cover\\s*letter/.test(labT)) continue;
    const body = (fu.innerText || '').replace(/\\s+/g, ' ').trim();
    if (/\\.(pdf|doc|docx|txt|rtf)\\b/i.test(body)) {
      const m = body.match(/([^\\s]+\\.(pdf|doc|docx|txt|rtf))/i);
      return {uploaded: true, name: m ? m[1] : body.slice(0, 120)};
    }
  }
  return {uploaded: false, name: ''};
}"""

_PROBE_RESUME_FIELD_JS = """() => {
  const out = {present: false, empty: false, selectors: []};
  const isResumeCvInput = (el) => {
    const id = ((el.id || '') + ' ' + (el.name || '') + ' '
      + (el.getAttribute('aria-label') || '')).toLowerCase();
    const lab = el.closest('label, [class*="upload" i], fieldset, .form-group');
    const labT = (lab && lab.innerText || '').toLowerCase();
    const blob = id + ' ' + labT;
    if (/other|cover\\s*letter|attachment(?!.*cv)/.test(blob) && !/\\bcv\\b|resume|documents\\.cv/.test(blob)) {
      return false;
    }
    return /resume|\\bcv\\b|curriculum|documents\\.cv|doc-input-cv/.test(blob);
  };
  const candidates = Array.from(document.querySelectorAll('input[type=file]'));
  const resumeInputs = candidates.filter(isResumeCvInput);
  const check = resumeInputs.length > 0 ? resumeInputs : candidates;
  // Also styled drop-zones that reference resume in label
  const labels = Array.from(document.querySelectorAll('label, [class*="upload" i]'));
  let resumeish = false;
  for (const lab of labels) {
    const t = (lab.innerText || lab.getAttribute('aria-label') || '').toLowerCase();
    if (/resume|\\bcv\\b|curriculum/.test(t) && !/other|cover\\s*letter/.test(t)) {
      resumeish = true;
      break;
    }
  }
  // Ashby: filename in field-entry or files on _systemfield_resume
  for (const el of document.querySelectorAll(
    '.ashby-application-form-field-entry, [class*="_fieldEntry_"]'
  )) {
    const labEl = el.querySelector('label');
    const labT = (labEl && (labEl.innerText || labEl.textContent) || '').toLowerCase();
    if (/resume|\\bcv\\b|curriculum/.test(labT) && !/cover\\s*letter/.test(labT)) {
      const body = (el.innerText || '').replace(/\\s+/g, ' ').trim();
      if (/\\.(pdf|doc|docx|txt|rtf)\\b/i.test(body)) {
        out.present = true;
        out.empty = false;
        out.uploaded_ui = true;
        out.ashby_uploaded_ui = true;
        out.selectors.push('ashby_upload_ui');
        return out;
      }
    }
  }
  for (const inp of document.querySelectorAll(
    'input[id="_systemfield_resume"], input[name="_systemfield_resume"]'
  )) {
    if (inp.files && inp.files.length > 0) {
      out.present = true;
      out.empty = false;
      out.selectors.push('_systemfield_resume');
      return out;
    }
  }
  // GH job-boards: after upload the #resume input is removed; filename stays in .file-upload
  for (const fu of document.querySelectorAll('.file-upload, [class*="file-upload"]')) {
    const labEl = fu.querySelector('[id*="upload-label"], .upload-label, label');
    const labT = (labEl && (labEl.innerText || labEl.textContent) || '').toLowerCase();
    if (!/resume|\\bcv\\b/.test(labT) || /cover\\s*letter/.test(labT)) continue;
    const body = (fu.innerText || '').replace(/\\s+/g, ' ').trim();
    if (/\\.(pdf|doc|docx|txt|rtf)\\b/i.test(body)) {
      out.present = true;
      out.empty = false;
      out.uploaded_ui = true;
      out.selectors.push('gh_file_upload_ui');
      return out;
    }
  }
  // Workday: file input remounts empty after upload — trust attachment chrome
  for (const wrap of document.querySelectorAll(
    '[data-automation-id="file-upload-input-ref"], '
    + '[data-automation-id*="fileUpload"], '
    + '[data-automation-id*="FileUpload"], '
    + '[data-automation-id="attachmentsList"], '
    + '[data-automation-id*="attachment"]'
  )) {
    const root = wrap.closest('[data-automation-id]') || wrap.parentElement || wrap;
    const body = ((root && (root.innerText || root.textContent)) || '').replace(/\\s+/g, ' ').trim();
    if (/\\.(pdf|doc|docx)\\b/i.test(body)) {
      out.present = true;
      out.empty = false;
      out.uploaded_ui = true;
      out.workday_uploaded_ui = true;
      out.selectors.push('workday_upload_ui');
      return out;
    }
    const delBtn = (root || document).querySelector(
      '[data-automation-id*="delete"], [data-automation-id*="Delete"], '
      + 'button[aria-label*="Delete" i], button[aria-label*="Remove" i]'
    );
    if (delBtn && /resume|cv|\\.pdf/i.test(body + ' ' + (delBtn.getAttribute('aria-label') || ''))) {
      out.present = true;
      out.empty = false;
      out.uploaded_ui = true;
      out.workday_uploaded_ui = true;
      out.selectors.push('workday_delete_file_ui');
      return out;
    }
  }
  // Workday page text: dummy PDF basename often visible after attach
  {
    const body = (document.body && document.body.innerText || '').toLowerCase();
    if (/dummy.*\\.pdf|test.?dummy.*\\.pdf|resume_dummy|dummy_resume/.test(body)
        || /uploaded\\s+(file|document)|file\\s+uploaded/i.test(body)) {
      // Only claim if a file-upload control exists on page
      if (document.querySelector('[data-automation-id="file-upload-input-ref"], input[type=file]')) {
        out.present = true;
        out.empty = false;
        out.uploaded_ui = true;
        out.workday_uploaded_ui = true;
        out.selectors.push('workday_body_pdf_hint');
        return out;
      }
    }
  }
  if (candidates.length === 0 && !resumeish) {
    return out;
  }
  out.present = check.length > 0 || resumeish;
  for (const el of check) {
    const id = el.id || el.name || 'file';
    out.selectors.push(String(id).slice(0, 80));
  }
  if (check.length === 0 && resumeish) {
    out.empty = true; // drop zone without wired files yet
    return out;
  }
  out.empty = check.some((el) => !el.files || el.files.length < 1);
  return out;
}"""


def accept_resume_after_empty_filelist(
    post_probe: dict | None, *, page_hint: bool = False
) -> bool:
    """True when remounted FileList is empty but UI still shows an attached resume."""
    p = post_probe or {}
    if not p.get("present") or not p.get("empty"):
        return False
    return bool(
        p.get("uploaded_ui")
        or p.get("ashby_uploaded_ui")
        or p.get("workday_uploaded_ui")
        or page_hint
    )


def is_resume_empty_required(entry: dict | None) -> bool:
    """True when a required-empty row is the resume/CV gate (incl. Ashby systemfield)."""
    if not isinstance(entry, dict):
        return False
    reason = str(entry.get("reason") or "")
    if reason not in ("empty_required_file", "empty_resume_file"):
        return False
    eid = str(entry.get("id") or "").lower()
    return bool(
        "_systemfield_resume" in eid
        or eid in ("resume", "cv", "file-upload-input-ref")
        or "resume" in eid
        or re.search(r"\bcv\b", eid)
    )


def filter_resume_required_empties(
    empties: list[dict] | None, *, resume_verified: bool = False
) -> list[dict]:
    """Drop ghost resume required-empty rows when upload is already verified."""
    if not resume_verified or not empties:
        return list(empties or [])
    return [e for e in empties if not is_resume_empty_required(e)]


def filter_resume_leftovers(leftovers: list[dict] | None) -> list[dict]:
    """Remove ``_systemfield_resume`` leftovers that duplicate a verified upload."""
    kept: list[dict] = []
    for u in leftovers or []:
        if not isinstance(u, dict):
            kept.append(u)
            continue
        lab = str(u.get("label") or "").lower()
        reason = str(u.get("reason") or "")
        if lab == "_systemfield_resume" or (
            u.get("type") == RESUME_UPLOAD
            and "live_required_empty" in reason
        ):
            continue
        kept.append(u)
    return kept


def gh_upload_filename_visible(upload_text: str) -> bool:
    """Pure check: GH `.file-upload` body shows an uploaded document filename."""
    body = re.sub(r"\s+", " ", (upload_text or "").strip())
    if not body:
        return False
    return bool(re.search(r"\.(pdf|doc|docx|txt|rtf)\b", body, re.I))


def is_resume_attachment_row(row: dict | None) -> bool:
    """True only for resume/CV file rows — not GH post-resume contact reassert."""
    if not isinstance(row, dict):
        return False
    ftype = str(row.get("type") or "")
    mode = str(row.get("mode") or "")
    return ftype == RESUME_UPLOAD or mode == "file"


async def _verify_ashby_resume_ui(page, expected_name: str) -> dict[str, Any]:
    try:
        info = await page.evaluate(_ASHBY_RESUME_UPLOADED_JS)
    except Exception as e:
        return {"ok": False, "verified": False, "reason": f"ashby_ui_error:{e}"[:120]}
    if not isinstance(info, dict) or not info.get("uploaded"):
        return {"ok": False, "verified": False, "reason": "ashby_ui_not_uploaded"}
    name = str(info.get("name") or "")
    if not name:
        return {"ok": False, "verified": False, "reason": "ashby_ui_no_name"}
    matched = True
    if expected_name:
        matched = (
            expected_name.lower() in name.lower()
            or name.lower() in expected_name.lower()
            or name.lower().endswith(".pdf")
        )
    via = str(info.get("via") or "ashby_upload_ui")
    return {
        "ok": bool(matched),
        "verified": bool(matched),
        "reason": via if matched else "ashby_ui_name_mismatch",
        "readback": name[:120],
        "mode": "ashby_upload_ui",
    }


async def ashby_resume_uploaded_on_page(page) -> bool:
    try:
        info = await page.evaluate(_ASHBY_RESUME_UPLOADED_JS)
        return bool(isinstance(info, dict) and info.get("uploaded"))
    except Exception:
        return False


async def resume_satisfied_on_page(page) -> bool:
    """True when any resume/CV path shows files or uploaded UI (GH or Ashby)."""
    probe = await probe_resume_field(page)
    if probe.get("uploaded_ui") or probe.get("ashby_uploaded_ui"):
        return True
    if probe.get("present") and not probe.get("empty"):
        return True
    if await gh_resume_uploaded_on_page(page):
        return True
    if await ashby_resume_uploaded_on_page(page):
        return True
    return False


async def _verify_gh_resume_ui(page, expected_name: str) -> dict[str, Any]:
    try:
        info = await page.evaluate(_GH_RESUME_UPLOADED_JS)
    except Exception as e:
        return {"ok": False, "verified": False, "reason": f"gh_ui_error:{e}"[:120]}
    if not isinstance(info, dict) or not info.get("uploaded"):
        return {"ok": False, "verified": False, "reason": "gh_ui_not_uploaded"}
    name = str(info.get("name") or "")
    if not name:
        return {"ok": False, "verified": False, "reason": "gh_ui_no_name"}
    matched = True
    if expected_name:
        matched = (
            expected_name.lower() in name.lower()
            or name.lower() in expected_name.lower()
            or name.lower().endswith(".pdf")
        )
    return {
        "ok": bool(matched),
        "verified": bool(matched),
        "reason": "gh_upload_ui" if matched else "gh_ui_name_mismatch",
        "readback": name[:120],
    }


async def gh_resume_uploaded_on_page(page) -> bool:
    """True when GH job-boards shows a resume filename in the upload widget."""
    try:
        info = await page.evaluate(_GH_RESUME_UPLOADED_JS)
        return bool(isinstance(info, dict) and info.get("uploaded"))
    except Exception:
        return False


def _is_job_scoped_resume(path: Path) -> bool:
    """True for resumes/<job_id>/*.pdf (or .doc) — uploaded/tailored job files."""
    s = str(path).replace("\\", "/").lower()
    name = path.name.lower()
    if name in ("credentials.json", "profile.json"):
        return False
    if path.suffix.lower() not in (".pdf", ".doc", ".docx"):
        return False
    return "/resumes/" in s and "dummy_resume" not in s


def resume_pdf_from_values(values: dict | None) -> Path:
    """Resolve upload PDF: real mode → job/tailored; test/dummy → fixture only.

    In test/dummy mode NEVER accept job-scoped ``resumes/<id>/*.pdf`` — only
    ``assert_dummy_resume_path`` (FILL-007).
    """
    raw = None
    if values:
        raw = values.get(RESUME_UPLOAD) or values.get("_resume_pdf")
    path = Path(str(raw)) if raw else DUMMY_PDF
    if is_real_profile_mode():
        return assert_real_resume_path(path)
    # Dummy / test mode: refuse job-scoped and any non-dummy path.
    return assert_dummy_resume_path(path)


def autofill_filename_verify_ok(
    *,
    filename: str | None,
    input_present: bool | None = None,
    files_on_input: bool | None = None,
) -> bool:
    """True when visible filename chrome may count as a verified resume.

    FILL3-011: if a file ``input`` exists and FileList is empty, do **not**
    trust filename-only UI (parse chrome can show a name that later wipes).
    When ``input_present`` / ``files_on_input`` are unknown (None), keep
    legacy behavior (filename alone OK) for GH/Ashby paths that do not probe.
    """
    name = str(filename or "").strip()
    if not name:
        return False
    if input_present is True and files_on_input is False:
        return False
    return True


def report_has_verified_resume(report: dict | None) -> bool:
    if not report:
        return False
    for f in report.get("filled") or []:
        if not isinstance(f, dict):
            continue
        ftype = str(f.get("type") or "")
        mode = str(f.get("mode") or "")
        if not is_resume_attachment_row(f):
            if f.get("automation_id") not in (
                "file-upload-input-ref",
                "file-upload-select-files",
            ):
                continue
        if f.get("ok") is False or f.get("verified") is False:
            continue
        rb = str(f.get("readback") or "").strip()
        reason = str(f.get("reason") or "")
        # FILL3-011: explicit empty FileList under a live input → not verified
        if reason == "filename_visible_filelist_empty":
            continue
        if mode == "filename_visible" and not autofill_filename_verify_ok(
            filename=rb or str(f.get("value") or ""),
            input_present=f.get("input_present"),
            files_on_input=f.get("files_on_input"),
        ):
            continue
        if f.get("verified") is True:
            # Must have filename readback or files_on_input reason — never blank claim
            if rb or reason in (
                "files_on_input",
                "filename_visible_ui",
                "gh_upload_ui",
            ):
                return True
            continue
        if f.get("ok") is True and rb:
            return True
    ru = report.get("resume_upload") or {}
    if isinstance(ru, dict) and ru.get("verified") is True:
        probe = ru.get("probe") if isinstance(ru.get("probe"), dict) else {}
        if not probe.get("present") or not probe.get("empty"):
            return True
    # Workday Autofill-with-Resume: phase_a_resume.upload.verified must agree
    # with top-level resume_verified (Quantiphi/BBH mismatch).
    for par in (
        report.get("phase_a_resume"),
        (report.get("workday") or {}).get("phase_a_resume")
        if isinstance(report.get("workday"), dict)
        else None,
    ):
        if not isinstance(par, dict):
            continue
        up = par.get("upload") if isinstance(par.get("upload"), dict) else {}
        if up.get("verified") is True:
            return True
        ready = (
            par.get("autofill_ready")
            if isinstance(par.get("autofill_ready"), dict)
            else {}
        )
        fname = str(ready.get("filename") or "").strip()
        if ready.get("ready") and fname:
            # FILL3-011: honor FileList probe when present on autofill_ready
            if autofill_filename_verify_ok(
                filename=fname,
                input_present=ready.get("input_present"),
                files_on_input=ready.get("files_on_input"),
            ):
                return True
    return False


def sync_resume_verified_from_phase_a(report: dict | None) -> bool:
    """Set top-level resume_verified from phase_a_resume when Autofill succeeded.

    Returns True when top-level was (or already is) verified.
    """
    if not isinstance(report, dict):
        return False
    if report_has_verified_resume(report):
        report["resume_verified"] = True
        report["resume_field_present"] = True
        return True
    return bool(report.get("resume_verified"))


def apply_resume_success_gate(report: dict) -> dict:
    """If a resume field was present but not verified → FAIL (not SUCCESS)."""
    ru = report.get("resume_upload") if isinstance(report.get("resume_upload"), dict) else {}
    present = bool(ru.get("field_present")) or bool(ru.get("present"))
    # Also infer from leftovers / extract
    if not present:
        for u in report.get("leftovers") or []:
            if not isinstance(u, dict):
                continue
            if u.get("type") == RESUME_UPLOAD or str(u.get("reason") or "") in (
                "resume_upload_failed",
                "resume_missing",
                "resume_unverified",
            ):
                present = True
                break
    verified = report_has_verified_resume(report)
    report["resume_field_present"] = bool(present or verified or ru.get("attempted"))
    report["resume_verified"] = bool(verified)
    sync_resume_verified_from_phase_a(report)
    verified = bool(report.get("resume_verified"))
    if (present or ru.get("attempted")) and not verified:
        report.setdefault("leftovers", []).append(
            {
                "label": "Resume / CV",
                "type": RESUME_UPLOAD,
                "reason": "resume_missing",
                "flash_candidate": True,
            }
        )
        # Dedupe resume_missing leftovers
        seen = False
        kept = []
        for u in report["leftovers"]:
            if (
                isinstance(u, dict)
                and u.get("type") == RESUME_UPLOAD
                and u.get("reason") == "resume_missing"
            ):
                if seen:
                    continue
                seen = True
            kept.append(u)
        report["leftovers"] = kept
        if report.get("verdict") == "SUCCESS":
            report["verdict"] = "FAIL"
        report["resume_gate"] = "missing_or_unverified"
    elif verified:
        report["resume_gate"] = "verified"
    else:
        report["resume_gate"] = "not_applicable"
    return report


async def _verify_input_files(locator, expected_name: str) -> dict[str, Any]:
    try:
        info = await locator.evaluate(_VERIFY_JS, timeout=2500)
    except Exception as e:
        return {"ok": False, "verified": False, "reason": f"verify_error:{e}"[:120]}
    if not isinstance(info, dict):
        return {"ok": False, "verified": False, "reason": "verify_bad_shape"}
    if not info.get("ok"):
        return {
            "ok": False,
            "verified": False,
            "reason": info.get("reason") or "no_files",
            "readback": "",
        }
    name = str(info.get("name") or "")
    # Accept any non-empty filename; prefer matching our PDF stem/name
    matched = True
    if expected_name and name:
        matched = (
            expected_name.lower() in name.lower()
            or name.lower() in expected_name.lower()
            or name.lower().endswith(".pdf")
        )
    return {
        "ok": bool(matched and name),
        "verified": bool(matched and name),
        "reason": "files_on_input" if matched and name else "filename_mismatch",
        "readback": name[:120],
        "file_count": info.get("count"),
    }


async def _page_shows_filename(page, name: str) -> bool:
    try:
        return bool(await page.evaluate(_PAGE_FILENAME_HINT_JS, name))
    except Exception:
        return False


async def probe_resume_field(page) -> dict[str, Any]:
    try:
        return await page.evaluate(_PROBE_RESUME_FIELD_JS)
    except Exception as e:
        return {"present": False, "empty": False, "error": str(e)[:120]}


async def _pick_resume_file_locator(page):
    """Return (locator, selector) for the resume/CV input — never cover letter."""
    # Ashby: always target _systemfield_resume when present (required gate).
    try:
        is_ashby = bool(
            await page.evaluate(
                """() => !!document.querySelector(
                  '.ashby-application-form-field-entry, [class*="_fieldEntry_"]'
                )"""
            )
        )
    except Exception:
        is_ashby = False
    if is_ashby:
        for sel in _ASHBY_RESUME_SELECTORS:
            loc = page.locator(sel).first
            try:
                if await loc.count() > 0:
                    return loc, sel
            except Exception:
                continue
    for sel in _RESUME_FILE_SELECTORS:
        loc = page.locator(sel)
        try:
            n = await loc.count()
        except Exception:
            continue
        if n <= 0:
            continue
        for i in range(n):
            item = loc.nth(i)
            try:
                blob = " ".join(
                    filter(
                        None,
                        [
                            await item.get_attribute("id"),
                            await item.get_attribute("name"),
                            await item.get_attribute("aria-label"),
                        ],
                    )
                ).lower()
            except Exception:
                blob = ""
            if re.search(r"cover\s*letter|cover_letter", blob):
                continue
            if re.search(r"resume|\\bcv\\b", blob) or sel in ("#resume", "input#resume"):
                return item, sel
            # Generic catch-all: first non-cover file input
            if sel == "input[type=file]" and not re.search(
                r"cover\s*letter|cover_letter", blob
            ):
                return item, sel
        # Resume-specific selector with a single non-cover match
        if n == 1:
            try:
                blob = (await loc.first.get_attribute("id") or "").lower()
                if not re.search(r"cover\s*letter|cover_letter", blob):
                    return loc.first, sel
            except Exception:
                return loc.first, sel
    return None, ""


async def upload_resume_to_page(
    page,
    values: dict,
    *,
    via: str = "ensure_resume",
    report: dict | None = None,
) -> dict[str, Any]:
    """Upload dummy resume to the best file input; verify FileList / UI."""
    pdf = resume_pdf_from_values(values)
    out: dict[str, Any] = {
        "type": RESUME_UPLOAD,
        "mode": "file",
        "via": via,
        "value": pdf.name,
        "path": str(pdf),
        "ok": False,
        "verified": False,
    }
    if not pdf.is_file():
        out["reason"] = "pdf_missing"
        note_step(
            report,
            action="upload_resume",
            field_type=RESUME_UPLOAD,
            label="Resume",
            reason="pdf_missing",
            via=via,
            extra={"ok": False},
        )
        return out

    note_step(
        report,
        action="upload_resume_start",
        field_type=RESUME_UPLOAD,
        label="Resume",
        after=pdf.name,
        via=via,
    )

    ashby_ui = await _verify_ashby_resume_ui(page, pdf.name)
    if ashby_ui.get("verified"):
        out.update(
            {
                "ok": True,
                "verified": True,
                "reason": ashby_ui.get("reason") or "ashby_upload_ui",
                "readback": ashby_ui.get("readback") or pdf.name,
                "selector": "ashby_upload_ui",
                "mode": "ashby_upload_ui",
            }
        )
        note_step(
            report,
            action="upload_resume",
            field_type=RESUME_UPLOAD,
            label="Resume",
            after=str(out.get("readback") or ""),
            reason=str(out.get("reason") or ""),
            via=via,
            layer="0.5",
            extra={"ok": True, "mode": "ashby_upload_ui"},
        )
        return out

    gh_ui = await _verify_gh_resume_ui(page, pdf.name)
    if gh_ui.get("verified"):
        out.update(
            {
                "ok": True,
                "verified": True,
                "reason": gh_ui.get("reason") or "gh_upload_ui",
                "readback": gh_ui.get("readback") or pdf.name,
                "selector": "gh_file_upload_ui",
                "mode": "gh_upload_ui",
            }
        )
        note_step(
            report,
            action="upload_resume",
            field_type=RESUME_UPLOAD,
            label="Resume",
            after=str(out.get("readback") or ""),
            reason=str(out.get("reason") or ""),
            via=via,
            layer="0.5",
            extra={"ok": True, "mode": "gh_upload_ui"},
        )
        return out

    target, used_sel = await _pick_resume_file_locator(page)
    handles = page.locator(used_sel) if used_sel else None
    if target is not None and used_sel:
        handles = target

    # Hidden / styled drop-zones: label present but no discoverable file input yet
    if target is None and not used_sel:
        try:
            fc_timeout = 8000 if "myworkdayjobs.com" in (page.url or "").lower() else 5000
            async with page.expect_file_chooser(timeout=fc_timeout) as fc_info:
                clicked = False
                for sel_txt in (
                    '[data-automation-id="file-upload-select-button"]',
                    'button:has-text("Select files")',
                    'button:has-text("Select Files")',
                    'text=Select files',
                    '.file-upload:has(#upload-label-resume) button:has-text("Attach")',
                    '.file-upload:has([id*="upload-label-resume"]) button:has-text("Attach")',
                    'label[for="resume"]',
                    '#upload-label-resume',
                    'label:has-text("Resume / CV")',
                    'label:has-text("Resume/CV")',
                    'label:has-text("Resume")',
                    'label:has-text("CV")',
                    'button:has-text("Upload resume")',
                    'button:has-text("Upload CV")',
                    'button:has-text("Attach")',
                    'button:has-text("Select files")',
                    'button:has-text("Choose file")',
                    'button:has-text("Browse")',
                    'button:has-text("Upload")',
                    'a:has-text("Upload")',
                    '[data-testid*="resume" i]',
                    '[class*="resume" i] button',
                    'div[role="button"]:has-text("Resume")',
                    '[class*="dropzone" i]',
                    '[class*="file-drop" i]',
                    '[class*="filepond" i]',
                    'div:has-text("Drop files here")',
                    'div:has-text("or select a file")',
                    'div:has-text("Attach a file")',
                    # Greenhouse job-boards styled drop zone (often no input until click)
                    '.resume-upload',
                    '[data-field="resume"]',
                    '#resume',
                ):
                    btn = page.locator(sel_txt).first
                    try:
                        if await btn.count() and await btn.is_visible(timeout=400):
                            await btn.click(timeout=3000)
                            clicked = True
                            used_sel = sel_txt
                            break
                    except Exception:
                        continue
                if not clicked:
                    raise RuntimeError("no_resume_click_target")
            chooser = await fc_info.value
            await chooser.set_files(str(pdf))
            out["mode"] = "file_chooser"
            out["selector"] = used_sel or "file_chooser"
            try:
                await page.wait_for_timeout(500)
            except Exception:
                pass
            if await _page_shows_filename(page, pdf.name):
                out["ok"] = True
                out["verified"] = True
                out["reason"] = "filename_visible_ui"
                out["readback"] = pdf.name
                return out
            # Re-probe file inputs after chooser (GH often mounts input post-click)
            for sel in _RESUME_FILE_SELECTORS:
                loc = page.locator(sel)
                try:
                    if await loc.count() > 0:
                        verify = await _verify_input_files(loc.first, pdf.name)
                        if verify.get("verified"):
                            out["ok"] = True
                            out["verified"] = True
                            out["reason"] = verify.get("reason")
                            out["readback"] = verify.get("readback")
                            out["selector"] = sel
                            return out
                except Exception:
                    continue
            out["reason"] = "chooser_unverified"
            return out
        except Exception as e:
            out["reason"] = "no_file_input"
            out["error"] = str(e)[:160]
            return out

    if target is None:
        out["reason"] = "no_file_input"
        out["error"] = "no_resume_input"
        return out

    out["selector"] = used_sel
    # Personio: hidden CV input may need its label/dropzone clicked first
    if "doc-input-cv" in used_sel or "documents.cv" in used_sel:
        for click_sel in (
            "label[for='doc-input-cv']",
            "#doc-input-cv",
            "input[name='documents.cv']",
        ):
            try:
                btn = page.locator(click_sel).first
                if await btn.count() and await btn.is_visible(timeout=400):
                    await btn.click(timeout=2000)
                    await page.wait_for_timeout(200)
                    break
            except Exception:
                continue
    try:
        await target.set_input_files(str(pdf))
    except Exception as e:
        # File-chooser path for hidden inputs
        try:
            host = page
            async with host.expect_file_chooser(timeout=4000) as fc_info:
                # Click a nearby upload control if present
                for sel_txt in (
                    '.file-upload:has(#upload-label-resume) button:has-text("Attach")',
                    'label[for="resume"]',
                    'button:has-text("Upload")',
                    'button:has-text("Select files")',
                    'button:has-text("Attach")',
                    'label:has-text("Resume")',
                    'label:has-text("CV")',
                ):
                    btn = page.locator(sel_txt).first
                    if await btn.count() and await btn.is_visible(timeout=400):
                        await btn.click(timeout=3000)
                        break
                else:
                    await target.click(timeout=3000, force=True)
            chooser = await fc_info.value
            await chooser.set_files(str(pdf))
            out["mode"] = "file_chooser"
        except Exception as e2:
            out["reason"] = "upload_error"
            out["error"] = f"{e}; {e2}"[:200]
            return out

    try:
        await page.wait_for_timeout(500)
    except Exception:
        pass

    verify = await _verify_input_files(target, pdf.name)
    if not verify.get("verified"):
        # GH remounts #resume after upload — verify via widget UI / page text
        gh_ui = await _verify_gh_resume_ui(page, pdf.name)
        if gh_ui.get("verified"):
            verify = gh_ui
            out["mode"] = out.get("mode") or "gh_upload_ui"
        elif await _page_shows_filename(page, pdf.name):
            verify = {
                "ok": True,
                "verified": True,
                "reason": "filename_visible_ui",
                "readback": pdf.name,
            }
    if not verify.get("verified"):
        # Retry once (SPA remount)
        try:
            retry_loc = page.locator("#resume").first
            if await retry_loc.count() > 0:
                await retry_loc.set_input_files(str(pdf))
            else:
                attach = page.locator(
                    '.file-upload:has(#upload-label-resume) button:has-text("Attach")'
                ).first
                if await attach.count():
                    async with page.expect_file_chooser(timeout=4000) as fc_info:
                        await attach.click(timeout=3000)
                    chooser = await fc_info.value
                    await chooser.set_files(str(pdf))
                    out["mode"] = "file_chooser"
            await page.wait_for_timeout(500)
            verify = await _verify_gh_resume_ui(page, pdf.name)
            if not verify.get("verified"):
                verify = await _verify_input_files(
                    page.locator("#resume").first
                    if await page.locator("#resume").count()
                    else target,
                    pdf.name,
                )
            out["retried"] = True
        except Exception as e:
            out["retry_error"] = str(e)[:120]

    if not verify.get("verified"):
        gh_ui = await _verify_gh_resume_ui(page, pdf.name)
        if gh_ui.get("verified"):
            verify = gh_ui
        elif out.get("mode") == "file_chooser" and await _page_shows_filename(
            page, pdf.name
        ):
            verify = {
                "ok": True,
                "verified": True,
                "reason": "filename_visible_ui",
                "readback": pdf.name,
            }

    out.update(
        {
            "ok": bool(verify.get("ok")),
            "verified": bool(verify.get("verified")),
            "reason": verify.get("reason"),
            "readback": verify.get("readback") or "",
            "file_count": verify.get("file_count"),
        }
    )
    # Re-probe Ashby systemfield after upload (SPA may mount sibling inputs)
    if out.get("verified"):
        post = await probe_resume_field(page)
        out["post_probe"] = {
            k: post.get(k)
            for k in ("present", "empty", "selectors", "uploaded_ui", "ashby_uploaded_ui")
        }
        if post.get("present") and post.get("empty"):
            ashby_ui = await _verify_ashby_resume_ui(page, pdf.name)
            if ashby_ui.get("verified"):
                out["verified"] = True
                out["ok"] = True
                out["reason"] = ashby_ui.get("reason") or "ashby_upload_ui"
                out["readback"] = ashby_ui.get("readback") or out.get("readback")
            elif await _page_shows_filename(page, pdf.name):
                out["reason"] = out.get("reason") or "filename_visible_ui"
    note_step(
        report,
        action="upload_resume",
        field_type=RESUME_UPLOAD,
        label="Resume",
        after=str(out.get("readback") or ""),
        reason=str(out.get("reason") or ""),
        via=via,
        layer="0.5",
        extra={
            "ok": bool(out.get("verified")),
            "mode": out.get("mode"),
            "selector": out.get("selector"),
            "post_probe": out.get("post_probe"),
        },
    )
    return out


async def ensure_resume_uploaded(
    page,
    values: dict,
    report: dict,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Early + late resume gate: upload dummy PDF when a file field exists.

    Sets ``report['resume_upload']`` and appends a verified filled row on success.
    Retries once if still empty after the first attempt.
    """
    probe = await probe_resume_field(page)
    already = report_has_verified_resume(report)
    summary: dict[str, Any] = {
        "attempted": False,
        "field_present": bool(probe.get("present")),
        "field_empty": bool(probe.get("empty")),
        "already_verified": already,
        "verified": already,
        "probe": {k: probe.get(k) for k in ("present", "empty", "selectors", "error")},
    }

    if already and not force and not probe.get("empty"):
        summary["skipped"] = "already_verified"
        report["resume_upload"] = summary
        return summary

    if already and not force and not probe.get("empty"):
        summary["skipped"] = "already_verified"
        report["resume_upload"] = summary
        return summary

    if not force and probe.get("present") and not probe.get("empty"):
        if probe.get("uploaded_ui") or await gh_resume_uploaded_on_page(page):
            gh_ui = await _verify_gh_resume_ui(
                page, resume_pdf_from_values(values).name
            )
            if gh_ui.get("verified"):
                summary["attempted"] = False
                summary["verified"] = True
                summary["skipped"] = "already_uploaded_ui"
                summary["result"] = {
                    "ok": True,
                    "verified": True,
                    "reason": gh_ui.get("reason") or "gh_upload_ui",
                    "readback": gh_ui.get("readback"),
                    "selector": "gh_file_upload_ui",
                    "mode": "gh_upload_ui",
                }
                if not report_has_verified_resume(report):
                    report.setdefault("filled", []).append(
                        {
                            "via": "ensure_resume",
                            "layer": "0.5",
                            "label": "Resume",
                            "type": RESUME_UPLOAD,
                            "mode": "gh_upload_ui",
                            "selector": "gh_file_upload_ui",
                            "value": gh_ui.get("readback"),
                            "readback": gh_ui.get("readback"),
                            "ok": True,
                            "verified": True,
                            "reason": gh_ui.get("reason") or "gh_upload_ui",
                        }
                    )
                report["resume_upload"] = summary
                return summary

    if not probe.get("present") and not force:
        summary["skipped"] = "no_resume_field"
        report["resume_upload"] = summary
        return summary

    summary["attempted"] = True
    result = await upload_resume_to_page(page, values, via="ensure_resume", report=report)
    if not result.get("verified"):
        # Second full attempt before declaring missing
        result2 = await upload_resume_to_page(
            page, values, via="ensure_resume_retry", report=report
        )
        result2["retried"] = True
        result = result2

    summary["result"] = {
        k: result.get(k)
        for k in (
            "ok",
            "verified",
            "reason",
            "readback",
            "selector",
            "mode",
            "value",
            "error",
            "retried",
        )
        if k in result
    }
    summary["verified"] = bool(result.get("verified"))

    # Post-upload probe: FileList must be non-empty when field present
    if result.get("verified"):
        post_probe = await probe_resume_field(page)
        summary["post_probe"] = {
            k: post_probe.get(k) for k in ("present", "empty", "selectors", "error")
        }
        if post_probe.get("present") and post_probe.get("empty"):
            # GH/Ashby/Workday remount — trust UI filename / sibling widget probe
            page_hint = False
            try:
                pdf_name = str(
                    (result.get("value") or "")
                    or (values.get("RESUME_PATH") or "")
                )
                base = Path(pdf_name).name if pdf_name else ""
                if base:
                    page_hint = bool(
                        await page.evaluate(_PAGE_FILENAME_HINT_JS, base)
                    )
            except Exception:
                page_hint = False
            if accept_resume_after_empty_filelist(
                post_probe,
                page_hint=page_hint
                or await gh_resume_uploaded_on_page(page)
                or await ashby_resume_uploaded_on_page(page),
            ):
                result["verified"] = True
                result["ok"] = True
                result["reason"] = (
                    result.get("reason")
                    or (
                        "workday_upload_ui"
                        if post_probe.get("workday_uploaded_ui") or page_hint
                        else (
                            "ashby_upload_ui"
                            if post_probe.get("ashby_uploaded_ui")
                            else "gh_upload_ui"
                        )
                    )
                )
                summary["verified"] = True
            else:
                result["verified"] = False
                result["ok"] = False
                result["reason"] = "probe_empty_after_upload"
                summary["verified"] = False

    if result.get("verified"):
        # Replace prior failed resume rows
        report["filled"] = [
            f
            for f in (report.get("filled") or [])
            if not (
                isinstance(f, dict)
                and (
                    f.get("type") == RESUME_UPLOAD
                    or f.get("mode") == "file"
                )
                and f.get("verified") is not True
            )
        ]
        # Avoid duplicate verified resume rows
        if not report_has_verified_resume(report):
            report.setdefault("filled", []).append(
                {
                    "via": result.get("via") or "ensure_resume",
                    "layer": "0.5",
                    "label": "Resume",
                    "type": RESUME_UPLOAD,
                    "mode": result.get("mode") or "file",
                    "selector": result.get("selector") or "input[type=file]",
                    "value": result.get("value"),
                    "readback": result.get("readback") or result.get("value"),
                    "ok": True,
                    "verified": True,
                    "reason": result.get("reason"),
                }
            )
        report["leftovers"] = filter_resume_leftovers(
            [
                u
                for u in (report.get("leftovers") or [])
                if not (
                    isinstance(u, dict)
                    and (
                        u.get("type") == RESUME_UPLOAD
                        or str(u.get("reason") or "").startswith("resume_")
                    )
                )
            ]
        )
        note_step(
            report,
            action="upload_resume_verified",
            field_type=RESUME_UPLOAD,
            label="Resume",
            after=str(result.get("readback") or ""),
            reason=str(result.get("reason") or ""),
            via=str(result.get("via") or "ensure_resume"),
            layer="0.5",
            extra={"ok": True},
        )
    else:
        report.setdefault("leftovers", []).append(
            {
                "label": "Resume / CV",
                "type": RESUME_UPLOAD,
                "reason": "resume_upload_failed",
                "error": result.get("error") or result.get("reason"),
                "flash_candidate": True,
            }
        )

    report["resume_upload"] = summary
    return summary

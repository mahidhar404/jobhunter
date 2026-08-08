"""Per-run dummy identity: random Gmail +alias + matching resume PDF.

Every fast_fill / hybrid_fill / exp / dashboard dummy run must:
  1. Mint a fresh random email: randommail6969+{random12}@gmail.com
  2. Put that SAME email in the fill value map AND the uploaded resume PDF

Interrupt-safe: allocate_random_run_email persists used addresses in
alias_state.json (used_emails) under exclusive flock on alias_state.json.lock;
re-running after Ctrl-C gets a NEW random email (never reuses, never leaves
resume on base while form uses an alias). Concurrent allocates cannot
last-writer-wins-drop an issued address.

Dummy-only — never reads profile.json contact email.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from field_map import (
    CURRENT_COMPANY,
    CURRENT_TITLE,
    DISCIPLINE,
    DUMMY_ADDRESS,
    DUMMY_PDF,
    DUMMY_PROFILE,
    EDUCATION_END_YEAR,
    EDUCATION_START_YEAR,
    EMAIL,
    FIELD_OF_STUDY,
    MAJOR,
    RESUME_UPLOAD,
    allocate_random_run_email,
    assert_dummy_resume_path,
    assert_not_real_profile_env,
    assert_real_profile_allowed,
    assert_real_resume_path,
    build_value_map,
    is_real_profile_mode,
    load_profile,
    resolve_real_address_text,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DUMMY_TEX = HERE / "fixtures" / "dummy_resume_de.tex"
BASE_EMAIL = DUMMY_PROFILE["contact"]["email"]
TECTONIC_BIN = os.environ.get("TECTONIC_BIN", "/opt/homebrew/bin/tectonic")
TRUSTED_UPLOADS = HERE.parents[1] / "skyvern_runtime" / "trusted_uploads"
_MIN_TOKEN_LEN = 12
_EMAIL_IN_TEXT = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
)


@dataclass
class RunIdentity:
    """One dummy fill run's consistent contact identity."""

    email: str
    email_alias: str
    alias_token: str
    base_email: str
    resume_pdf: Path
    compiled: bool
    values: dict
    pdf_email: str | None = None
    parity_gaps: list[str] | None = None

    def report_fields(self) -> dict:
        out = {
            "email": self.email,
            "email_alias": self.email_alias,
            "alias_token": self.alias_token,
            "identity_email": self.email,
            "resume_pdf": str(self.resume_pdf),
            "resume_compiled": self.compiled,
            "pdf_email": self.pdf_email,
            "form_email_matches_pdf": (
                self.pdf_email is not None
                and self.pdf_email.lower() == self.email.lower()
            ),
        }
        if self.parity_gaps:
            out["parity_gaps"] = list(self.parity_gaps)
        return out


def _edu_years_from_text(text: str) -> tuple[str, str]:
    """Pull end/start years from a resume education line or graduation string."""
    years = re.findall(r"(?:19|20)\d{2}", text or "")
    if not years:
        return "", ""
    end = years[-1]
    start = years[0] if len(years) >= 2 else (
        str(int(end) - 2) if end.isdigit() else ""
    )
    return end, start


def _discipline_from_edu_lines(lines: list) -> str:
    """Best-effort major/discipline from parsed education line strings."""
    # Common "B.S. in Computer Science" / "Master of Science, Data Science"
    pat = re.compile(
        r"(?:bachelor|master|b\.?s\.?|m\.?s\.?|b\.?a\.?|m\.?a\.?|ph\.?d\.?)"
        r"[^,\n]{0,40}?\b(?:in|of)\s+([A-Za-z][A-Za-z &'/-]{2,60})",
        re.I,
    )
    for ln in lines or []:
        if not isinstance(ln, str):
            continue
        m = pat.search(ln)
        if m:
            return m.group(1).strip(" .")[:80]
        # "Computer Science, University of …"
        m2 = re.match(
            r"^([A-Z][A-Za-z &'/-]{2,50}),\s+[A-Z]",
            ln.strip(),
        )
        if m2 and not re.search(r"\buniversity\b|\bcollege\b", m2.group(1), re.I):
            return m2.group(1).strip()[:80]
    return ""


def apply_job_title_to_values(values: dict, job_title: str | None) -> None:
    """Set APPLYING_FOR from CLI/dashboard job title when provided."""
    from field_map import APPLYING_FOR

    title = (job_title or "").strip()
    if not title:
        title = (os.environ.get("FASTFILL_JOB_TITLE") or "").strip()
    if title:
        values[APPLYING_FOR] = title


def compute_parity_gaps(values: dict) -> list[str]:
    """Keys that are blank after prepare (no PII values — names only)."""
    from field_map import (
        ADDRESS_CITY,
        ADDRESS_LINE1,
        ADDRESS_STATE,
        APPLYING_FOR,
        EMAIL,
        PASSWORD,
        PHONE,
        YEARS_EXPERIENCE,
    )

    gaps: list[str] = []
    hard = {
        "EMAIL": EMAIL,
        "PHONE": PHONE,
        "PASSWORD": PASSWORD,
        "YEARS_EXPERIENCE": YEARS_EXPERIENCE,
    }
    for name, key in hard.items():
        if not str(values.get(key) or "").strip():
            gaps.append(name)
    addr_ok = bool(
        str(values.get(ADDRESS_CITY) or "").strip()
        or str(values.get(ADDRESS_STATE) or "").strip()
        or str(values.get(ADDRESS_LINE1) or "").strip()
    )
    if not addr_ok:
        gaps.append("ADDRESS")
    soft = {
        "CURRENT_COMPANY": CURRENT_COMPANY,
        "CURRENT_TITLE": CURRENT_TITLE,
        "DISCIPLINE": DISCIPLINE,
        "EDUCATION_END_YEAR": EDUCATION_END_YEAR,
        "APPLYING_FOR": APPLYING_FOR,
    }
    for name, key in soft.items():
        if not str(values.get(key) or "").strip():
            gaps.append(f"soft:{name}")
    return gaps


def _backfill_from_resume(values: dict, resume_pdf: Path) -> list[str]:
    """Fill blank company/title/discipline/years from resume parse. No Example Corp."""
    notes: list[str] = []
    try:
        from resume_parser import parse_resume, resume_value_map

        parsed = parse_resume(resume_pdf)
        rvm = resume_value_map(parsed)
        for key in (CURRENT_COMPANY, CURRENT_TITLE):
            if rvm.get(key) and not (values.get(key) or "").strip():
                values[key] = rvm[key]
                notes.append(f"resume:{key}")
        # Education discipline / years when profile degrees lack them
        edu_lines = list(parsed.get("education") or [])
        disc = _discipline_from_edu_lines(edu_lines)
        if disc:
            for key in (DISCIPLINE, MAJOR, FIELD_OF_STUDY):
                if not (values.get(key) or "").strip():
                    values[key] = disc
                    notes.append(f"resume:{key}")
        # Years from first education line that has a year
        if not (values.get(EDUCATION_END_YEAR) or "").strip():
            for ln in edu_lines:
                end, start = _edu_years_from_text(str(ln))
                if end:
                    values[EDUCATION_END_YEAR] = end
                    if start and not (values.get(EDUCATION_START_YEAR) or "").strip():
                        values[EDUCATION_START_YEAR] = start
                    notes.append("resume:EDUCATION_YEARS")
                    break
        # Also map any years resume_value_map may expose later
        for key in (DISCIPLINE, MAJOR, FIELD_OF_STUDY, EDUCATION_END_YEAR, EDUCATION_START_YEAR):
            if rvm.get(key) and not (values.get(key) or "").strip():
                values[key] = rvm[key]
                notes.append(f"resume:{key}")
    except Exception as e:
        notes.append(f"resume_parse_error:{type(e).__name__}")
    return notes


def _alias_token_from_email(email: str) -> str:
    local = email.split("@", 1)[0]
    if "+" not in local:
        return ""
    return local.rsplit("+", 1)[-1]


def assert_non_sequential_run_email(email: str, alias_token: str | None = None) -> None:
    """Refuse base / sequential +1,+2,… / short decimal tokens."""
    local = email.split("@", 1)[0]
    tag = alias_token if alias_token is not None else _alias_token_from_email(email)
    if "+" not in local or not tag:
        raise RuntimeError(f"refuse sequential/reused email: {email!r}")
    if tag.isdigit():
        raise RuntimeError(f"refuse sequential/reused email: {email!r}")
    if len(tag) < _MIN_TOKEN_LEN:
        raise RuntimeError(
            f"refuse short/sequential alias token {tag!r} (need {_MIN_TOKEN_LEN}+ hex)"
        )


def _resolve_tectonic() -> Path | None:
    tectonic = Path(TECTONIC_BIN)
    if tectonic.is_file():
        return tectonic
    which = shutil.which("tectonic")
    return Path(which) if which else None


def _run_tectonic(tectonic: Path, tex_path: Path, dest_root: Path) -> tuple[bool, str]:
    """Run tectonic once; return (ok, stderr_or_error)."""
    cmd = [
        str(tectonic),
        "--keep-logs",
        "--outdir",
        str(dest_root),
        str(tex_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            cwd=str(dest_root),
        )
    except subprocess.TimeoutExpired as exc:
        return False, f"tectonic timeout: {exc}"
    except OSError as exc:
        return False, f"tectonic OSError: {exc}"

    pdf_path = dest_root / (tex_path.stem + ".pdf")
    ok = proc.returncode == 0 and pdf_path.is_file() and pdf_path.stat().st_size > 0
    err = (proc.stderr or proc.stdout or "").strip()
    if not ok and not err:
        err = f"tectonic exit {proc.returncode}, pdf exists={pdf_path.is_file()}"
    return ok, err


def compile_run_resume_pdf(
    email: str,
    *,
    out_dir: Path | None = None,
    alias_token: str = "",
    allow_base_fallback: bool = True,
) -> tuple[Path, bool]:
    """Compile dummy_resume_de.tex with ``email`` substituted into the header.

    Returns (pdf_path, compiled_ok). Retries tectonic once on failure. On final
    failure, optionally copies the base fixture PDF (compiled=False) so callers
    can inspect a path — prepare_dummy_run still refuses mismatched base PDFs.
    """
    if not DUMMY_TEX.is_file():
        raise FileNotFoundError(f"missing dummy tex: {DUMMY_TEX}")

    dest_root = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="fastfill_run_"))
    dest_root.mkdir(parents=True, exist_ok=True)
    stem = f"dummy_resume_run_{alias_token or 'x'}"
    tex_path = dest_root / f"{stem}.tex"
    pdf_path = dest_root / f"{stem}.pdf"

    tex = DUMMY_TEX.read_text(encoding="utf-8")
    if BASE_EMAIL not in tex:
        raise ValueError(
            f"dummy tex missing base email {BASE_EMAIL!r} — cannot patch"
        )
    patched = tex.replace(BASE_EMAIL, email)
    if email not in patched:
        raise ValueError(f"failed to patch run email into tex for {email!r}")
    tex_path.write_text(patched, encoding="utf-8")

    tectonic = _resolve_tectonic()
    compiled = False
    last_err = "tectonic binary not found"
    if tectonic is not None:
        for attempt in range(2):
            if pdf_path.is_file():
                try:
                    pdf_path.unlink()
                except OSError:
                    pass
            compiled, last_err = _run_tectonic(tectonic, tex_path, dest_root)
            if compiled:
                break

    if not compiled:
        if not allow_base_fallback:
            raise RuntimeError(
                f"resume PDF compile failed for {email!r}: {last_err[:800]}"
            )
        # Fallback keeps a path for inspection; email in PDF may lag.
        shutil.copy2(DUMMY_PDF, pdf_path)
    return pdf_path, compiled


def _pdf_email_matches(resume_pdf: Path, email: str) -> str:
    """Return the email found in the PDF; raise if missing or mismatched."""
    try:
        from resume_parser import parse_resume

        parsed = parse_resume(resume_pdf)
        found = (parsed.get("email") or "").strip()
    except Exception:
        found = ""

    if not found:
        # Lightweight fallback if pdfplumber unavailable in this interpreter
        try:
            raw = resume_pdf.read_bytes()
            # PDF may store ASCII email in streams; best-effort
            text = raw.decode("latin-1", errors="ignore")
            matches = _EMAIL_IN_TEXT.findall(text)
            for m in matches:
                if m.lower() == email.lower():
                    found = m
                    break
            if not found and matches:
                found = matches[0]
        except Exception:
            found = ""

    if not found:
        raise RuntimeError(
            f"resume PDF has no extractable email (expected {email!r}): {resume_pdf}"
        )
    if found.lower() != email.lower():
        raise RuntimeError(
            f"form email != resume PDF email: form={email!r} pdf={found!r}"
        )
    return found


def prepare_dummy_run(
    *,
    compile_pdf: bool = True,
    out_dir: Path | None = None,
    copy_to_trusted: bool = False,
) -> RunIdentity:
    """Allocate random run email, build value map, optionally compile matching PDF.

    Always dummy profile. Sets values[EMAIL] and values[RESUME_UPLOAD] to the
    run-specific identity so fill/upload stay consistent.
    """
    # Autofill hard gate: never inherit FASTFILL_REAL_PROFILE=1, never real PII.
    assert_not_real_profile_env()
    os.environ["FASTFILL_REAL_PROFILE"] = "0"
    profile, address, is_dummy = load_profile()
    assert is_dummy, "prepare_dummy_run refuses real profile.json"
    assert profile is DUMMY_PROFILE, "prepare_dummy_run must use DUMMY_PROFILE object"
    assert address == DUMMY_ADDRESS, "prepare_dummy_run must use DUMMY_ADDRESS"
    assert_email = profile.get("contact", {}).get("email", "")
    assert assert_email == BASE_EMAIL, "profile contact email must be DUMMY base"
    assert profile.get("personal", {}).get("full_name") == "Test Dummy", (
        "prepare_dummy_run refuses non-dummy full_name"
    )
    assert profile.get("contact", {}).get("phone") == "405-555-0100", (
        "prepare_dummy_run refuses non-dummy phone"
    )

    alloc = allocate_random_run_email(BASE_EMAIL)
    email = alloc["email"]
    token = alloc["alias_token"]
    assert_non_sequential_run_email(email, token)

    values = build_value_map(profile, address)
    values = dict(values)
    values[EMAIL] = email

    compiled = False
    pdf_email: str | None = None
    if compile_pdf:
        resume_pdf, compiled = compile_run_resume_pdf(
            email,
            out_dir=out_dir,
            alias_token=token,
            allow_base_fallback=False,
        )
        if not compiled:
            raise RuntimeError(
                f"resume PDF compile failed — refuse mismatched base PDF for {email!r}"
            )
        pdf_email = _pdf_email_matches(resume_pdf, email)
    else:
        # Logical-only path (dry tests): still point RESUME_UPLOAD at base PDF
        # but email in values is the run alias — call with compile_pdf=True for
        # application-consistent uploads.
        resume_pdf = DUMMY_PDF

    if copy_to_trusted and compile_pdf:
        TRUSTED_UPLOADS.mkdir(parents=True, exist_ok=True)
        trusted = TRUSTED_UPLOADS / f"dummy_resume_run_{token}.pdf"
        shutil.copy2(resume_pdf, trusted)
        resume_pdf = trusted
        # Re-verify after copy (same bytes, but keep the hard rule local).
        pdf_email = _pdf_email_matches(Path(resume_pdf), email)

    assert_dummy_resume_path(resume_pdf)
    values[RESUME_UPLOAD] = str(resume_pdf)

    return RunIdentity(
        email=email,
        email_alias=email,
        alias_token=token,
        base_email=BASE_EMAIL,
        resume_pdf=Path(resume_pdf),
        compiled=compiled,
        values=values,
        pdf_email=pdf_email,
    )


def resolve_real_resume_path(
    job_id: str | None = None,
    resume_path: Path | str | None = None,
) -> Path:
    """Tailored job resume, else trusted_uploads/resume.pdf."""
    candidates: list[Path] = []
    if resume_path:
        candidates.append(Path(resume_path))
    if job_id:
        candidates.append(ROOT / "resumes" / job_id / "resume.pdf")
    candidates.append(TRUSTED_UPLOADS / "resume.pdf")
    for cand in candidates:
        if cand.is_file():
            return assert_real_resume_path(cand)
    raise FileNotFoundError(
        "real-profile fill needs a resume PDF "
        f"(job_id={job_id!r}, tried {[str(c) for c in candidates]})"
    )


def prepare_real_run(
    *,
    job_id: str | None = None,
    resume_path: Path | str | None = None,
    address_text: str | None = None,
    address_pick: dict | None = None,
    job_title: str | None = None,
) -> RunIdentity:
    """Production apply prep: real profile.json + tailored/trusted resume.

    Requires explicit env opt-in (``is_real_profile_mode()``). Never mints
    random dummy emails. Still never submits — caller must keep never_submit.
    """
    assert_real_profile_allowed(force_real=True)
    if not is_real_profile_mode():
        raise RuntimeError("prepare_real_run refused: not in real-profile mode")

    profile, resolved_address, is_dummy = load_profile(force_real=True)
    if is_dummy:
        raise RuntimeError("prepare_real_run refused dummy profile")
    addr = (address_text or "").strip() or resolved_address
    if not addr:
        addr = resolve_real_address_text(
            job_id=job_id,
            address_pick=address_pick,
        )

    resume_pdf = resolve_real_resume_path(job_id=job_id, resume_path=resume_path)
    email = profile.get("contact", {}).get("email", "")
    if not email:
        raise RuntimeError("profile.json contact.email missing")

    # Shared policy + unique identity via build_value_map (compose_fill_values).
    # Do NOT call overlay_dummy_policy_on_real with empty address — that injects
    # dummy Springfield into real fills when pick_address fails.
    values = dict(build_value_map(profile, addr or ""))
    values[EMAIL] = email
    values[RESUME_UPLOAD] = str(resume_pdf)

    # Always attempt resume parse backfill when company/title/edu blank.
    # Never injects dummy "Example Corp". Leaves blank + parity_gaps if still empty.
    _backfill_from_resume(values, resume_pdf)

    # Job title from CLI/dashboard → APPLYING_FOR (both modes call this helper).
    if not job_title and job_id:
        try:
            jobs = json.loads((ROOT / "jobs.json").read_text(encoding="utf-8"))
            for j in jobs.get("jobs") or []:
                if j.get("id") == job_id:
                    job_title = (j.get("title") or "").strip() or None
                    break
        except Exception:
            pass
    apply_job_title_to_values(values, job_title)

    # PASSWORD stays empty here — fast_fill / Phase A call ensure_password_for_company
    # from web_keys before auth (never profile.account).

    parity_gaps = compute_parity_gaps(values)

    pdf_email: str | None = None
    try:
        pdf_email = _pdf_email_matches(resume_pdf, email)
    except RuntimeError:
        # Tailored resumes may omit email in PDF — form still uses profile email.
        pdf_email = None

    return RunIdentity(
        email=email,
        email_alias=email,
        alias_token="",
        base_email=email,
        resume_pdf=resume_pdf,
        compiled=False,
        values=values,
        pdf_email=pdf_email,
        parity_gaps=parity_gaps,
    )


def main() -> int:
    """Smoke: two prepares must yield different random emails; optional PDF match."""
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--compile", action="store_true", help="Also tectonic-compile PDFs")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    a = prepare_dummy_run(compile_pdf=args.compile)
    b = prepare_dummy_run(compile_pdf=args.compile)

    def _checks(ident: RunIdentity) -> dict:
        try:
            assert_non_sequential_run_email(ident.email, ident.alias_token)
            not_sequential = True
            seq_err = None
        except RuntimeError as exc:
            not_sequential = False
            seq_err = str(exc)
        form_match = ident.values.get(EMAIL) == ident.email
        pdf_match = (
            ident.pdf_email is not None
            and ident.pdf_email.lower() == ident.email.lower()
        )
        return {
            "not_sequential": not_sequential,
            "sequential_error": seq_err,
            "values_match_email": form_match,
            "form_email_matches_pdf": pdf_match if args.compile else None,
            "pdf_email": ident.pdf_email,
        }

    ca, cb = _checks(a), _checks(b)
    out = {
        "run_a": {**a.report_fields(), "values_email": a.values.get(EMAIL), **ca},
        "run_b": {**b.report_fields(), "values_email": b.values.get(EMAIL), **cb},
        "emails_differ": a.email != b.email,
        "values_match_email_a": ca["values_match_email"],
        "values_match_email_b": cb["values_match_email"],
        "not_sequential_a": ca["not_sequential"],
        "not_sequential_b": cb["not_sequential"],
        "form_email_matches_pdf_a": ca["form_email_matches_pdf"],
        "form_email_matches_pdf_b": cb["form_email_matches_pdf"],
    }
    ok = (
        out["emails_differ"]
        and out["values_match_email_a"]
        and out["values_match_email_b"]
        and out["not_sequential_a"]
        and out["not_sequential_b"]
    )
    if args.compile:
        ok = ok and bool(out["form_email_matches_pdf_a"]) and bool(
            out["form_email_matches_pdf_b"]
        )

    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"run_a: {a.email}")
        print(f"run_b: {b.email}")
        print(f"emails_differ: {out['emails_differ']}")
        print(f"not_sequential: {out['not_sequential_a'] and out['not_sequential_b']}")
        if args.compile:
            print(f"compiled_a: {a.compiled} pdf={a.resume_pdf} pdf_email={a.pdf_email}")
            print(f"compiled_b: {b.compiled} pdf={b.resume_pdf} pdf_email={b.pdf_email}")
            print(
                "form_email_matches_pdf: "
                f"{out['form_email_matches_pdf_a'] and out['form_email_matches_pdf_b']}"
            )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

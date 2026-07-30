"""SOTA layered architecture: deterministic classification driving an LLM agent,
instead of either alone.

Neither pure approach is right in isolation:
  * The from-scratch Playwright walker (scripts/fastfill/fill_form.py) classifies
    92.3% of fields correctly with zero LLM calls, but hand-rolled page-settling
    heuristics kept discovering a new race every time Workday did something
    slightly different (a re-render swapping a marked button, an async account-
    creation POST, a "Back" button misclassified as forward progress). Each fix
    revealed the next one - that pattern is what re-implementing a real browser
    agent's perception loop from scratch looks like.
  * A pure LLM agent (the ORIGINAL Skyvern harness, real_job_test.py) re-perceives
    the page after every single action, which is exactly why it doesn't fall into
    those same races - but it pays a full reasoning call for every field,
    including the ~92% a lookup table already answers for free.

So: Layers 0-1 (scripts/fastfill/field_map.py) resolve what they can and hand
Skyvern a CHEAT SHEET instead of a from-scratch profile description - Skyvern
still drives every click and still re-perceives the page each step (so it
inherits Skyvern's robustness to exactly the races the walker kept hitting), but
it only has to REASON about the ~8% the cheat sheet doesn't cover, and about
navigation (Workday's account gate, multi-step wizards) which the walker was
reinventing badly. Layer 3 safety (never submit, terms-vs-marketing, honeypots,
resume-preferred entry) is written into the prompt AND re-uses the exact
code-level backstops already proven in real_job_test.py (_check_no_submit_clicked
et al.) - a prompt-only rule is a suggestion; a DB-query-based check on the raw
DOM is a guarantee that holds even if the model ignores the suggestion.
"""

import re
import sys
import json
import asyncio
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "fastfill"))

import real_job_test as rjt  # noqa: E402 - reuses proven task/safety/notify machinery
from field_map import (  # noqa: E402
    build_value_map, load_profile, PATTERNS, AUTOCOMPLETE_MAP, make_alias_email,
    validate_filled, DUMMY_PDF as FASTFILL_DUMMY_PDF,
)
from resume_parser import parse_resume, resume_value_map  # noqa: E402

# Skyvern's upload sandboxing only allowlists specific directories (see
# forge/sdk/api/files.py's validate_local_file_path(), fixed earlier this
# session for exactly this reason) - skyvern_runtime/trusted_uploads/ is one of
# them, scripts/fastfill/fixtures/ is not. Passing the fastfill fixture path
# directly failed live: "PermissionError - file access denied... outside the
# allowed directory." Content is parsed from the fixtures copy (single source
# of truth for what the resume SAYS); the upload path Skyvern actually touches
# is this pre-copied one inside the allowlisted directory.
SKYVERN_UPLOAD_PDF = Path(__file__).resolve().parents[1] / "trusted_uploads" / "dummy_resume_de.pdf"


def build_cheat_sheet(values: dict, include_learned: bool = True) -> tuple[str, set]:
    """Turn the Layer 0/1 resolved values into a compact lookup table for the
    prompt, rather than a paragraph Skyvern has to re-derive meaning from.

    Table form (not prose) is deliberate for two reasons: it is what the
    classifier itself actually is (label pattern -> value), so translating it
    into a paragraph would just make the model re-infer structure that already
    exists; and a short, STABLE block maximises DeepSeek's prompt-cache hit rate
    (the real, measured 50x cache-hit discount from earlier this session) since
    the exact same table is byte-identical across every job on the same
    platform, unlike a full page dump.

    include_learned=True folds in learning.learned_cheat_sheet_rows() - every
    field a PRIOR run's Layer 2 had to reason about, on ANY platform, that this
    run now gets for free. This is what makes the system actually improve with
    use instead of paying the same LLM cost on the same question forever.
    """
    def gist_of(pattern: str) -> str:
        """First alternative of a Layer-1 regex, stripped to plain words.

        A naive string .replace() on the character class left the opening
        bracket behind ("first[ name" instead of "first name") - caught by
        actually reading the printed cheat sheet rather than trusting it would
        work. A real regex substitution removes the whole `[\\s_-]*` token
        (and any other regex syntax) atomically, not piecemeal.
        """
        g = pattern.split("|")[0]
        # Character-class runs like [\s_-]* or [\s_-]+, and their (\s_-)*
        # sibling - covers every quantifier, not just `*`, which the first
        # version of this missed (caught live: 'years of \w+[\s_-]+{0,2}
        # experience' still had raw regex syntax in it).
        g = re.sub(r"[\[(]\\s_-[\])][*+]?", " ", g)
        g = re.sub(r"\\w\+?", " ", g)                       # \w / \w+ tokens
        g = re.sub(r"\{\d*,?\d*\}", "", g)                  # {0,2} quantifiers
        g = re.sub(r"\\[bB]", "", g)                        # \b word boundaries
        g = re.sub(r"[\^\$]", "", g)                        # anchors
        g = re.sub(r"[()?]", "", g)                         # leftover groups
        return re.sub(r"\s+", " ", g).strip()

    from learning import normalize_label

    rows = []
    static_gists = set()  # normalized labels the STATIC layers already cover -
    # returned to the caller so the post-run learning extraction knows not to
    # "learn" something field_map.py already knew.
    for ftype, pattern in PATTERNS.items():
        val = values.get(ftype)
        # validate_filled catches a malformed static value (e.g. an email
        # missing '@') before it's asserted to the model as a confident
        # answer - without this, a bad DUMMY_PROFILE entry would be handed
        # to Skyvern as fact instead of falling through to Layer 2 reasoning.
        if val and validate_filled(ftype, val):
            # Show the human-readable gist of the pattern, not the raw regex -
            # Skyvern's model doesn't need to parse regex syntax, only to
            # recognise "a field about this topic".
            gist = gist_of(pattern)
            rows.append(f"  - fields about {gist!r} (label/name/placeholder) -> {val!r}")
            static_gists.add(normalize_label(gist))
    for token, ftype in AUTOCOMPLETE_MAP.items():
        val = values.get(ftype) if ftype else None
        if val and validate_filled(ftype, val):
            rows.append(f"  - autocomplete=\"{token}\" -> {val!r}")

    if include_learned:
        from learning import learned_cheat_sheet_rows
        learned = learned_cheat_sheet_rows()
        if learned:
            rows.append("  --- learned from prior runs on other forms ---")
            rows.extend(learned)
            # Deliberately NOT added to static_gists: if this run re-confirms a
            # learned fact, extract_and_save_learnings() SHOULD see it again and
            # increment its confidence count. Only the built-in regex/
            # autocomplete entries (which need no confidence signal - they're
            # already exact) are excluded from re-learning.

    text = ("KNOWN FIELD MAPPING (resolved in advance - use these values directly, do not "
            "re-derive or guess):\n" + "\n".join(rows))
    return text, static_gists


LAYER3_RULES = """
LAYERED EXECUTION - read this before acting. Speed matters: act decisively,
don't second-guess known values, don't retry failed actions hoping for a
different result.
1. Any field matching the KNOWN FIELD MAPPING: use that value, don't
   second-guess it.
2. Any field NOT in the mapping: reason about it yourself.
3. Resume upload: file at {resume_path}. Prefer "Autofill with Resume" over
   manual entry when both are offered. EXCEPTION: if autofill redirects to an
   account sign-in requiring an email/SMS verification code, don't retry it -
   switch permanently to "Apply Manually."
4. Honeypot fields (name/id like "hp_"/"honeypot", or label saying "leave
   blank"/"for robots only"): skip entirely, never fill.
5. Consent checkboxes: CHECK Terms/Privacy/"I agree" boxes (required, pre-
   authorized). Leave UNCHECKED marketing/SMS/newsletter opt-ins (optional).
6. Account creation gates: filling email/password and clicking Create
   Account/Register is allowed (throwaway test account). If redirected to
   Sign In instead (already registered), sign in with the same known
   credentials rather than treating it as a dead end.
7. ABSOLUTE HARD RULE, no exception: never click the final Submit/Apply/
   Send/Finish/Confirm button. Reaching the final review step and stopping
   is success, not failure.
8. Any button labeled "Submit" is high risk no matter where in the flow -
   many ATS platforms treat a mid-flow "Submit" as the real final action
   even when it looks like a section gate. Do not rationalize it as safe -
   terminate reporting uncertainty instead of clicking it.
9. If a click produces no visible change within a few seconds, do not click
   it again - it may have already succeeded via a delayed background
   request. Terminate reporting uncertainty instead of re-clicking.
10. Autocomplete/typeahead fields (Company, Job Title, "Current location"):
    typing text is NOT enough - click the matching suggestion to confirm.
    Never press Enter to confirm - Enter can invisibly submit the ENTIRE
    form even with no submit button clicked (directly observed; absolute
    rule, no exception).
11. No human will respond to WAIT - this run is fully autonomous. If a
    required field has no mapped value and can't be reasonably inferred,
    terminate immediately and report which field blocked you; do not WAIT.
12. If a field rejects the mapped value or stays empty: try once more via
    (a) clicking a dropdown suggestion if one appeared, or (b) pressing Tab
    to blur/commit (never click a distant field to blur - it wastes time
    and risks disturbing that field's content; Tab is safe, unlike Enter).
    If still rejected, clear-and-move-on if optional, or terminate if
    required. Never retype identical text a 3rd time.
13. SPEED: you are allowed to propose MULTIPLE actions in a single response,
    not just one - the platform executes them in the order given. Use this
    for pairs that always go together: typing into a custom dropdown/
    typeahead field and then the Tab keypress that confirms it (rule 10/12)
    belong in the SAME response as two actions, not as two separate turns
    each requiring you to re-observe the page first. This is purely a speed
    optimization - it changes nothing about which fields you fill or how
    you decide their values, it just avoids paying a full page-observation
    round-trip for a confirmation step whose outcome is already known the
    moment you finish typing. Do NOT batch actions whose outcome depends on
    what happened in a PRIOR action within the same page - only batch a
    type immediately followed by its own confirming Tab.
"""


def _tenant_key(url: str) -> str:
    """Stable identity key for 'which company is this' - the sign-in-fallback
    rule only works if repeated runs against the SAME company reuse the SAME
    email/password an earlier run may have already registered. Netloc alone is
    enough: two different job postings on the same Workday tenant share one
    account space."""
    from urllib.parse import urlparse
    return urlparse(url).netloc or url


async def run_hybrid(url: str, job_id: str, alias_n: int | None = None, max_steps: int = 90,
                     timeout: float = 1800) -> dict:
    # max_steps/timeout raised together with WATCHDOG_MAX_TOTAL_ACTIONS
    # (50->75, real_job_test.py): both must stay comfortably above the
    # watchdog ceiling, or Skyvern's own generic step-limit message or the
    # client-side timeout becomes the new binding constraint instead of our
    # own more diagnostic watchdog - defeating the point of raising it.
    # Pfizer's real run needed >50 actions at ~20s/action pace; 75 actions
    # at that pace is ~1500s, so 1200s alone would have cut it off anyway.
    profile, address, is_dummy = load_profile()
    assert is_dummy, "hybrid_fill must never run against the real profile"
    values = build_value_map(profile, address)
    parsed = parse_resume(FASTFILL_DUMMY_PDF)
    # Fill GAPS only - never override an already-populated value. Found live
    # (2026-07-30): the dummy resume PDF's own text says "Hoboken, NJ" for
    # the candidate's location, while DUMMY_ADDRESS says "Springfield, IL
    # 62701" - two independently-authored sources for the same real-world
    # fact that will never agree. A blind .update() let the resume's value
    # win every time (it's applied second), silently overriding the address-
    # derived ADDRESS_CITY/ADDRESS_STATE fix and reintroducing exactly the
    # city/state/zip mismatch that fix was meant to close (confirmed as the
    # actual cause of 3 separate real failures this session: Teladoc
    # terminating on a rejected NJ/62701 combo, Wand AI's watchdog-cancelled
    # repeat-input loop, Markel silently self-correcting NJ->Illinois mid-run).
    # DUMMY_ADDRESS is the more complete source (it's the only one with a
    # zip), so it must win whenever both are present. This still preserves
    # the resume as a genuine fallback for fields build_value_map() has no
    # other source for at all (CURRENT_COMPANY/CURRENT_TITLE), and for real
    # (non-dummy) profile mode, where address_text is empty and the resume's
    # own city/state is the only source available.
    values.update({k: v for k, v in resume_value_map(parsed).items() if v and not values.get(k)})

    # Auto-derive a STABLE alias per company rather than requiring the caller
    # to track one manually. Persisted (field_map.load_next_alias_index) so a
    # second run against the same tenant reuses the same email/password that
    # may have already created a real account there - which is exactly what
    # the sign-in-fallback rule (below) needs to be able to act on.
    from field_map import EMAIL, load_next_alias_index, save_next_alias_index
    tenant_key = _tenant_key(url)
    if alias_n is None:
        alias_n = load_next_alias_index(tenant_key) or 1
        save_next_alias_index(tenant_key, alias_n)
    if alias_n > 0:
        values[EMAIL] = make_alias_email(values[EMAIL], alias_n)

    cheat_sheet, static_rows = build_cheat_sheet(values)
    rules = LAYER3_RULES.format(resume_path=str(SKYVERN_UPLOAD_PDF))
    prompt = f"Fill out this real job application form.\n\n{cheat_sheet}\n\n{rules}\n\n{rjt.NEVER_SUBMIT}"

    job = {"id": job_id, "url": url, "platform": "hybrid"}
    print(f"[{job_id}] prompt size: {len(prompt)} chars (cheat sheet: {len(cheat_sheet)} chars)")
    print(f"[{job_id}] using email alias #{alias_n}: {values.get('EMAIL')}")

    skyvern = rjt.Skyvern(base_url=rjt.BASE_URL, api_key=rjt.API_KEY)
    t0 = time.time()
    print(f"[{job_id}] START {time.strftime('%Y-%m-%d %H:%M:%S %Z')}", flush=True)
    result = None
    err = None
    run_id = None
    watchdog_triggered = submit_alarm = enter_alarm = None
    browser_session_id = None
    captcha_blocked = False
    try:
        session = await skyvern.create_browser_session(timeout=60)
        browser_session_id = session.browser_session_id
        # NOTE: a CDP-based deterministic pre-script (entry click + direct
        # field fills before the LLM task starts) was built and removed on
        # 2026-07-30: local Skyvern launches Chromium with
        # --remote-debugging-pipe (CDP over parent-process pipe, not a TCP
        # port), so browser_address is never populated in ENV=local and NO
        # external process can attach - verified by direct isolated test
        # (10s of polling, always None) and by reading browser_factory.py
        # (the internal cdp_port kwarg is unreachable from the public API).
        # Patching Skyvern's own source to force port-mode was rejected: it
        # touches the same launch path every safety check depends on, breaks
        # on every upgrade, and needs per-session port plumbing to be
        # concurrency-safe. If pre-scripting is ever revisited, that source
        # patch is the only known route - budget for it as real work.
        run_id = await rjt._create_task_v1(
            url=url,
            navigation_goal=prompt,
            max_steps_per_run=max_steps,
            include_action_history_in_verification=True,
            complete_criterion=rjt.COMPLETE_CRITERION,
            terminate_criterion=rjt.TERMINATE_CRITERION,
            browser_session_id=browser_session_id,
        )
        watchdog_triggered, submit_alarm, enter_alarm, result, err = await rjt._poll_and_finalize(
            skyvern, job, run_id, t0, timeout
        )
        if result and rjt._looks_like_captcha_block(getattr(result, "failure_reason", None)):
            captcha_blocked = True
            print(f"[{job_id}] CAPTCHA_BLOCKED - browser_session_id={browser_session_id}", flush=True)
    except Exception as e:
        err = err or str(e)
    finally:
        if browser_session_id and not captcha_blocked:
            try:
                await skyvern.close_browser_session(browser_session_id)
            except Exception:
                pass

    elapsed = time.time() - t0
    status = getattr(result, "status", None)
    failure_reason = getattr(result, "failure_reason", None) if result else None
    print(
        f"[{job_id}] END elapsed={elapsed:.1f}s status={status} error={err} "
        f"watchdog={bool(watchdog_triggered)} SUBMIT_ALARM={bool(submit_alarm)} "
        f"ENTER_ALARM={bool(enter_alarm)} CAPTCHA_BLOCKED={captcha_blocked}",
        flush=True,
    )
    if failure_reason:
        print(f"[{job_id}] reason: {failure_reason[:300]}", flush=True)

    # Close the learning loop: mine what Layer 2 actually resolved for fields
    # the static cheat sheet did NOT already cover, and persist it. This is
    # the one step that makes the system improve with use instead of paying
    # the same LLM cost on the same question forever - see learning.py.
    learned_count = 0
    if run_id:
        try:
            from learning import extract_and_save_learnings
            learned_count = extract_and_save_learnings(
                rjt.DB_CONN_KWARGS, run_id, platform=tenant_key,
                already_known_labels=static_rows,
            )
            if learned_count and job_id:
                print(f"[{job_id}] learned {learned_count} new field(s) for future runs", flush=True)
        except Exception as e:
            print(f"[{job_id}] learning extraction failed (non-fatal): {e}", flush=True)

    out = {
        "id": job_id, "url": url, "alias_n": alias_n, "elapsed_seconds": elapsed,
        "status": status, "error": err, "failure_reason": failure_reason,
        "watchdog_triggered": watchdog_triggered, "submit_alarm": submit_alarm,
        "enter_alarm": enter_alarm, "captcha_blocked": captcha_blocked,
        "prompt_chars": len(prompt), "cheat_sheet_chars": len(cheat_sheet),
    }
    out_path = Path(__file__).parent.parent / "real_job_results" / f"hybrid-{job_id}.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"[{job_id}] saved -> {out_path}")
    return out


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://tsys.wd1.myworkdayjobs.com/TSYS/job/ALPHARETTA-GEORGIA/AI-Engineer_R0071834"
    job_id = sys.argv[2] if len(sys.argv) > 2 else "global-payments-hybrid"
    # None (the default) means "auto": derive and persist a stable alias per
    # company, so a retry against the same tenant can sign in with an account
    # an earlier attempt may have already created, instead of always minting a
    # fresh one. Pass an explicit number only to force a specific identity.
    alias_n = int(sys.argv[3]) if len(sys.argv) > 3 else None
    asyncio.run(run_hybrid(url, job_id, alias_n=alias_n))

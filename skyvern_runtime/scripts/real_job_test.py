import os
import sys
import time
import json
import asyncio
import subprocess
import threading
import httpx
import psycopg
from pathlib import Path
from skyvern import Skyvern

BASE_URL = "http://localhost:8000"
API_KEY = os.environ["SKYVERN_API_KEY"]
# Connect via keyword args, not a DSN string - the generated DB password contains
# literal "/" characters, which breaks naive URI parsing (a real latent bug from
# how the password was generated without URL-encoding). Keyword args sidestep it.
DB_CONN_KWARGS = dict(
    host="127.0.0.1", port=5432, dbname="skyvern_production",
    user="skyvern_app", password=os.environ["SKYVERN_DB_PASSWORD"],
)
RESULTS_DIR = Path(__file__).parent.parent / "real_job_results"
RESULTS_DIR.mkdir(exist_ok=True)

# Stuck-loop watchdog: real runs today repeated the same (action_type, response)
# pair 5-6+ times in a row (re-answering an already-correct field, or retrying an
# unresolvable required field) before finally terminating - burning 20+ extra
# actions and hundreds of thousands of tokens for zero additional progress. This
# polls the actions table directly (ground truth, not a self-report) and cancels
# the run the moment a real repeat pattern shows up, rather than waiting for
# max_steps or a timeout to do it the expensive way.
WATCHDOG_POLL_S = 3          # was 12, then 6 - each cycle is a cheap local DB query, and every poll
# tick adds up to a full interval of pure dead time at the tail of every run (waiting to notice a
# run already finished) plus the same lag before a real safety/watchdog signal gets acted on -
# halving it again roughly halves both, at negligible added query cost on a local Postgres instance
WATCHDOG_WINDOW = 6          # look at the last N actions for tight back-to-back repeats
WATCHDOG_REPEAT_THRESHOLD = 4  # cancel if any single (type, response) appears this many times in the window
# Separate, whole-run check: "complete" carries no meaningful response field (it's
# always empty/null), so repeats there can't rely on response content at all - and
# a real run showed the failure-to-wrap-up pattern spread out (complete, click,
# complete, click...) rather than back-to-back, which the sliding window alone
# caught only very late (673s into a 676s run). Counting rejected completes across
# the whole run, independent of the window, catches this far earlier.
WATCHDOG_MAX_FAILED_COMPLETES = 4  # tried 2 (Gridware false positive), then 3 (Cogent false positive:
# the final screenshot showed the form fully correct - resume, visa toggle, all fields - by the
# time of the 3rd rejected complete, meaning Skyvern's own completion-verification is slower to
# recognize a correctly-set custom toggle/segmented-button widget than 3 attempts allows for
# (it had just identified and was fixing an unexpected pre-filled "IBM" value in a
# Current Company field) - 2 doesn't leave room for "reject -> genuinely fix -> retry"
# to finish. 3 still catches true non-convergence (e.g. Cogent's case: clicking the
# same visa-sponsorship toggle with the same value twice, no new information between
# attempts) while giving one real correction cycle room to succeed.
# Third signal: the same DOM element_id acted on repeatedly within one run. Only
# caught SOME real stuck loops (Cogent: yes, element repeated 3x) and missed
# others (Zoox's Yes/No flip-flop: Skyvern re-scrapes and reassigns fresh element
# ids each step even for the same physical button, so this alone isn't reliable -
# it's a genuine extra signal, not a replacement for the other two.
# Threshold is 4, not 3: verified live that some legitimate 3-attempt sequences on
# one element are real progressive problem-solving, not stuck-ness - a Man Group
# run adjusting a rejected salary value (140000 -> cleared -> 190000) converged to
# a genuine "completed" on its 3rd attempt, and cancelling at exactly 3 would have
# thrown away a run that was about to succeed. Give one more attempt before concluding.
WATCHDOG_MAX_SAME_ELEMENT = 4
# Absolute backstop, independent of any pattern match, for novel failure modes the
# other three signals weren't designed around. NOT meant to bound normal run length -
# raised twice already because genuinely long, repeat-free forms (SmartRecruiters,
# then UnitedHealth's COI questionnaire) kept exceeding it. Keep it generous; the
# other three signals are what actually catch stuck loops.
WATCHDOG_MAX_TOTAL_ACTIONS = 75  # raised 20->35 (SmartRecruiters/Veolia needed 25+, zero repeats)
# then 35->50 - UnitedHealth Group's own conflict-of-interest questionnaire (COI2B-COI6B, board
# memberships, government contracts, security clearance) hit exactly 35 with ZERO element repeats,
# all genuine forward progress, clearly more form left. The other signals (same-element,
# failed-completes, sliding-window-repeat) are the real defense against actual stuck loops; this
# backstop is a last-resort sanity ceiling, not the primary catch, so it should stay generous.
# Raised a third time, 50->75 (2026-07-30): Pfizer's own Workday flow (Voluntary
# Disclosures -> Self-Identify -> EEO sections) hit exactly 50 with zero element
# repeats and every action a genuine select_option/click on a DIFFERENT required
# field, correctly progressing the whole way - cut off mid-form, not stuck.


def _recent_actions(task_id: str, limit: int = WATCHDOG_WINDOW) -> list[tuple[str, str, str, dict]]:
    with psycopg.connect(**DB_CONN_KWARGS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT action_type, COALESCE(response, ''), COALESCE(element_id, ''), "
                "COALESCE(action_json, '{}'::json) FROM actions "
                "WHERE task_id = %s ORDER BY created_at DESC LIMIT %s",
                (task_id, limit),
            )
            return cur.fetchall()


def _failed_complete_count(task_id: str) -> int:
    with psycopg.connect(**DB_CONN_KWARGS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM actions WHERE task_id = %s AND action_type = 'complete' AND status = 'failed'",
                (task_id,),
            )
            return cur.fetchone()[0]


def _max_same_element_count(task_id: str, lookback: int = 40) -> tuple[str, int] | None:
    """Flags an element repeated >= WATCHDOG_MAX_SAME_ELEMENT times ONLY if at
    least one pair of consecutive occurrences has NO genuine work between them.

    Superseded a windowed-count version (lookback=12) after a THIRD real
    Workday run (hybrid5, global-payments) broke it: that run's Education
    section had enough real sub-parts that 4 of its 6 "Save and Continue"
    clicks landed inside even a 12-action window, still a false positive by
    the same mechanism - shrinking the window further only works by luck for
    one page's shape and would start missing genuinely tight repeats on a
    shorter form. Bounding by recent ACTION COUNT was the wrong dimension.

    The right signal, visible in all three false positives (hybrid2/AACy,
    hybrid3/AACo, hybrid5/AACx): a persistent nav button reused across a long
    wizard has REAL, DISTINCT work (a typed name, a selected dropdown, an
    address) between every single occurrence. A genuine stuck loop does not -
    Cogent's original case was the same toggle re-clicked with literally
    nothing else happening in between. So: count total occurrences within a
    generous lookback (40, wide enough to span a whole long form) as before,
    but only flag it if some CONSECUTIVE pair of those occurrences has zero
    real intervening action (an input_text/select_option with a non-empty
    response, or a click on a DIFFERENT element).
    """
    with psycopg.connect(**DB_CONN_KWARGS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT element_id, action_type, COALESCE(response, '') FROM actions "
                "WHERE task_id = %s ORDER BY created_at DESC LIMIT %s",
                (task_id, lookback),
            )
            rows = cur.fetchall()  # newest first
    if not rows:
        return None
    # Real false positive found live (2026-07-30, Pivotal Solutions/Zoho
    # Recruit): action types like solve_captcha and complete are not tied to
    # any specific DOM element, so Skyvern records element_id as NULL for all
    # of them. A run needing 3 legitimate captcha-solve attempts (normal -
    # captchas often take more than one try) plus a final complete all shared
    # element_id=None, and got counted as "the same element acted on 4x" even
    # though they were four different action types making real progress,
    # ending in an actual successful complete. None is not a real element -
    # exclude it, the same way it would make no sense to flag "field X
    # revisited" for a field that was never a field.
    rows = [(eid, atype, resp) for eid, atype, resp in rows if eid is not None]
    if not rows:
        return None
    counts: dict[str, int] = {}
    for eid, _, _ in rows:
        counts[eid] = counts.get(eid, 0) + 1
    top_eid, top_count = max(counts.items(), key=lambda kv: kv[1])

    # Oldest-first for a natural "did real work happen between repeats" read.
    chrono = list(reversed(rows))

    def is_real_work(action_type: str, response: str) -> bool:
        return bool(response.strip()) or action_type not in ("click", "complete")

    occurrence_idx = [i for i, (eid, _, _) in enumerate(chrono) if eid == top_eid]
    has_empty_gap = False
    for a, b in zip(occurrence_idx, occurrence_idx[1:]):
        gap_has_work = any(is_real_work(at, resp) for _, at, resp in chrono[a + 1:b])
        if not gap_has_work:
            has_empty_gap = True
            break

    return (top_eid, top_count) if has_empty_gap else None


def _total_action_count(task_id: str) -> int:
    with psycopg.connect(**DB_CONN_KWARGS) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM actions WHERE task_id = %s", (task_id,))
            return cur.fetchone()[0]


def _task_status(task_id: str) -> str | None:
    with psycopg.connect(**DB_CONN_KWARGS) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM tasks WHERE task_id = %s", (task_id,))
            row = cur.fetchone()
            return row[0] if row else None


# Hard safety check, distinct from the stuck-loop watchdog above - this is the
# one rule that must never be violated, so it's checked every poll cycle (not
# just at the end) and treated as a loud, unmissable alarm, not a quiet log line.
# "apply now" is split out as a WEAK/ambiguous signal (see below) - unlike the
# others, it's the standard label for the ENTRY button on a job description page
# (before any form exists at all), so it needs corroborating evidence to alarm on.
SUBMIT_SIGNAL_WORDS = ("submit", "send application", "send my application", "finish application")
AMBIGUOUS_SIGNAL_WORDS = ("apply now",)


def _check_no_submit_clicked(task_id: str) -> dict | None:
    """Inspects every click action's real DOM element data (not just the
    agent's own reasoning text, which could describe a click inaccurately) for
    anything that looks like a submit-type button. Returns details if found."""
    with psycopg.connect(**DB_CONN_KWARGS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT action_id, element_id, skyvern_element_data, created_at, action_type, reasoning "
                "FROM actions WHERE task_id = %s ORDER BY created_at ASC",
                (task_id,),
            )
            all_rows = cur.fetchall()
    # Real false positive found live on Rippling: the "Apply now" entry button on
    # a bare job-description page (the FIRST action of the whole run, before any
    # field was ever filled) - its own text was empty (label lives in a nested
    # child <span>, not this element), and "apply now" matched via the blob dump
    # of that nested text. "Apply now" genuinely can ALSO be a final-submission
    # label on some forms, so it can't be blanket-excluded like search/next - the
    # reliable signal is whether any real field-filling action happened first;
    # nothing can be "submitted" before a single field has ever been touched.
    form_filling_seen_before: dict[str, bool] = {}
    seen_any_fill = False
    for action_id, _element_id, _element_data, _created_at, action_type, _reasoning in all_rows:
        form_filling_seen_before[action_id] = seen_any_fill
        if action_type in ("input_text", "upload_file", "select_option"):
            seen_any_fill = True

    # Structural signal for the class of failure text/type checks below cannot
    # see at all: the Afficiency/SmartRecruiters incident used a Shadow DOM
    # button with EMPTY text everywhere our extraction looks (see
    # _check_post_hoc_submission_confirmed's docstring) - button_type wasn't
    # "submit", no signal word appeared anywhere. Can't fix that by reading
    # harder; the label genuinely isn't in the data. What IS derivable: on
    # THIS SAME page (page_url), had any real field actually been filled
    # right before this click? A "Next"/"Save and Continue" click always
    # follows real fills on the section it's leaving (confirmed live on
    # AbbVie and Markel: several input_text/select_option actions on the same
    # page immediately before each legitimate advance click). A pure review/
    # recap page - all the real work already done on EARLIER pages, nothing
    # left to fill on this one - has no such immediately-preceding fills.
    # That gap is what distinguishes "just advanced past a section" from
    # "landed on a page with nothing to fill and one button to press."
    #
    # BACKTESTED AND FOUND INSUFFICIENT ALONE: the real Afficiency incident's
    # submit click does NOT match this pattern - its risky Submit button was
    # on the SAME page where real fields (city, privacy checkbox) had JUST
    # been filled seconds earlier, structurally identical to a legitimate
    # "Save and Continue" section-advance. There was no separate review page.
    # This signal is kept as defense-in-depth for a DIFFERENT site shape (a
    # genuinely separate, no-fill review page with an unreadable button) but
    # is NOT what closes the Afficiency-class gap - see visual_submit_mention
    # below for that.
    no_recent_fill_same_page: dict[str, bool] = {}
    last_fill_page: str | None = None
    for action_id, _element_id, element_data, _created_at, action_type, _reasoning in all_rows:
        page_url = (element_data or {}).get("page_url", "")
        no_recent_fill_same_page[action_id] = (page_url != last_fill_page)
        if action_type in ("input_text", "select_option"):
            last_fill_page = page_url

    rows = [(a, e, d, c, r) for a, e, d, c, t, r in all_rows if t == "click"]
    prior_advance_count = 0  # count of prior Next/Continue-style clicks - "clicked
    # through continue multiple times" is itself a suspicion signal for an
    # unreadable button encountered afterward, distinct from a single-page
    # form's one and only button.
    for action_id, element_id, element_data, created_at, reasoning in rows:
        if not element_data:
            continue
        blob = json.dumps(element_data).lower()
        attrs = (element_data or {}).get("attributes", {}) or {}
        button_type = str(attrs.get("type", "")).lower()
        # Real false positive found live on Allspring's iCIMS posting: a native
        # `<input type="submit" value="Next">` element. For <input> tags the visible
        # label lives in the `value` ATTRIBUTE, not child text content the way
        # <button>Next</button> would have it - element_data.text was correctly empty,
        # but that meant the real label ("Next") was invisible to this check entirely.
        # Fall back to the value attribute so text-based checks (both the safe-list
        # below and SUBMIT_SIGNAL_WORDS) see what a human actually sees on the button.
        # Real false positive found live on an Oracle Cloud HCM posting: a native
        # `<button type="submit">` whose own text node is empty - its real label
        # ("Next") lived only in `aria-label`/`title` and a nested child <span>, none
        # of which `element_data.get("text")` captures (that only reflects the
        # element's OWN direct text, not descendants). Added aria-label/title as
        # further fallbacks - the same "real label lives somewhere text-extraction
        # doesn't look" root cause as the iCIMS value-attribute case above, just a
        # third distinct place it can hide.
        visible_text = str(
            element_data.get("text") or attrs.get("value") or attrs.get("aria-label") or attrs.get("title") or ""
        ).lower()
        # Real false positive found live on Intel's and Unity's Workday postings: the job
        # URL had 404'd, so the agent used Workday's own site search to find it, and
        # clicked its "Search" button - type="submit" purely because it's a standard HTML
        # search-form pattern, nothing to do with a job application. The FIRST fix attempt
        # only excluded this from the button_type=="submit" check, but missed that the word
        # "submit" is ALSO a SUBMIT_SIGNAL_WORD matched against `blob` (the full JSON dump
        # of element_data) - and blob always contains the literal text `"type": "submit"`
        # for ANY submit-type button, regardless of visible text, since that's just the
        # attribute's own JSON serialization. That made the exclusion below a no-op: the
        # button_type clause was correctly suppressed, but the blob-substring clause still
        # matched on "submit" every time. Fixed by dropping the bare "submit" word from the
        # blob-substring check entirely - button_type=="submit" already covers that signal
        # precisely, so checking for the substring "submit" in a JSON dump was redundant AND
        # is exactly what caused this to false-trigger on unrelated attribute noise.
        # "next"/"continue" cover multi-step registration/login wizards (iCIMS: an
        # account-creation step gate before the real application form) - advancing a
        # step is not a final submission, even though the button is type="submit".
        # "save and continue"/"save & continue" false-positived live on a real Deloitte/
        # Avature posting: a standard multi-step-form "save this section, advance to the
        # next one" button (confirmed via the agent's own reasoning: "proceed to the next
        # step (Job specific questions)" - clearly not final). It contains "continue" but
        # didn't match the exact-equality check above since it's a longer compound phrase -
        # added as its own exact entries rather than switching to a substring/contains
        # check, since a broader match risks swallowing a genuinely different phrase like
        # "continue to submit application".
        # "create account"/"register"/"sign up" (and spacing/hyphen variants) added once
        # NEVER_SUBMIT/COMPLETE_CRITERION were changed to actually allow completing an
        # account-creation gate (a throwaway login, not the employer-facing action) - the
        # prompt change alone left this independent, code-level safety net unaware of the
        # new rule, so it kept firing CRITICAL and cancelling the run the moment the agent
        # did exactly what it was now told to do. Confirmed live on a Labcorp/Phenom
        # posting: reasoning was literally "Create Account button should be clicked to
        # register the account." Still does NOT cover the real final application submit
        # button (Submit/Send/Finish/Confirm), which must keep alarming.
        # "sign in"/"log in" (and spacing/hyphen variants) - the EXACT same gap, missed
        # when create-account/register/sign-up were added even though the sign-in-
        # fallback rule (LAYER3_RULES rule 6: an already-registered email should sign in
        # rather than treating the redirect as a dead end) was added around the same
        # time. Confirmed live on a Fiserv/Workday posting: the agent correctly followed
        # that exact rule ("we already have an account... Sign In is the correct way"),
        # filled the known credentials, and clicked Sign In to AUTHENTICATE - not to
        # submit the job application - and this safety net cancelled it anyway because
        # it had never been told sign-in submission is a permitted action, same root
        # cause as the create-account gap, just never ported over to this button too.
        is_known_safe_non_submit = visible_text in (
            "search", "filter", "find jobs", "go", "next", "continue",
            "save and continue", "save & continue",
            "create account", "create an account", "register", "sign up", "sign-up",
            "sign in", "sign-in", "log in", "log-in", "login",
        )
        blob_signal_words = tuple(w for w in SUBMIT_SIGNAL_WORDS if w != "submit")
        ambiguous_match = any(word in blob or word in visible_text for word in AMBIGUOUS_SIGNAL_WORDS)
        # Only count an ambiguous "apply now" match if a real field was already filled -
        # nothing can be a final submission before the form has ever been touched.
        ambiguous_match_corroborated = ambiguous_match and form_filling_seen_before.get(action_id, False)

        # Structural signal for the Shadow DOM class of failure, where text/
        # type checks above have NOTHING to match (visible_text is genuinely
        # empty, not just unhelpful). class/style attributes calling a button
        # "primary"/"emphasized"/a CTA are how design systems mark THE main
        # action on a page - both a real "Next" and a real final "Submit" get
        # this styling, so it alone can't discriminate (confirmed live:
        # Afficiency's legitimate Next click had the identical
        # "c-spl-button--primary"/"Emphasized" signature as its risky Submit
        # click). What DOES discriminate: whether real fields were filled on
        # THIS page right before the click (a genuine section-advance always
        # follows real work; a bare review/recap page's one button doesn't),
        # combined with having already clicked through at least one prior
        # Next/Continue this run (a single-page form's only button, clicked
        # first and only, does not meet this - matching the "single page
        # asking for everything" case being a separate, already-covered
        # readable-text scenario, not this one).
        style_blob = (str(attrs.get("class", "")) + str(attrs.get("data-spl-button-type-description", ""))).lower()
        is_primary_styled = any(w in style_blob for w in ("primary", "emphasized", "cta"))
        structurally_suspicious_unreadable = (
            not visible_text
            and is_primary_styled
            and form_filling_seen_before.get(action_id, False)
            and no_recent_fill_same_page.get(action_id, False)
            and prior_advance_count >= 1
        )

        # What actually closes the Afficiency gap: the agent's OWN reasoning
        # text for this click, when its DOM-visible text is empty. The model's
        # PERCEPTION was correct - Afficiency's own reasoning literally said
        # "Click the Submit button..." - it could read the button just fine
        # via the screenshot; what went wrong was its JUDGMENT about whether
        # THIS Submit was final ("this is not the final application submit;
        # it is a gate" - wrong). This check deliberately extracts only the
        # FACT the model stated (it's describing a button labeled Submit),
        # while discarding the model's own risk conclusion about it entirely
        # - exactly like every other check in this function: the code decides
        # safety, not the model's self-report. Only fires when DOM text is
        # empty (button_type/blob checks above already own the readable-text
        # case) and a real field was already filled (rules out the Rippling
        # "Apply now" first-click case).
        reasoning_lower = str(reasoning or "").lower()
        visual_submit_mention = (
            not visible_text
            and form_filling_seen_before.get(action_id, False)
            and ("submit button" in reasoning_lower or "click submit" in reasoning_lower
                 or "clicking submit" in reasoning_lower or "the submit" in reasoning_lower)
        )

        if (
            (button_type == "submit" and not is_known_safe_non_submit)
            or (not is_known_safe_non_submit and any(word in blob or word in visible_text for word in blob_signal_words))
            or visual_submit_mention
            or ambiguous_match_corroborated
            or structurally_suspicious_unreadable
        ):
            return {
                "action_id": action_id,
                "element_id": element_id,
                "created_at": str(created_at),
                "button_type": button_type,
                "visible_text": visible_text,
                "structural": structurally_suspicious_unreadable,
                "visual_submit_mention": visual_submit_mention,
            }
        if is_known_safe_non_submit:
            prior_advance_count += 1
    return None


def _check_enter_keypress(task_id: str) -> dict | None:
    """Real incident found live: pressing Enter inside a form field (e.g. to
    confirm a combobox/autocomplete selection) can trigger the form's own
    implicit submit behavior even though no submit button was ever clicked -
    a risk the click-only _check_no_submit_clicked above cannot see at all.
    The task prompt now instructs the agent to click the dropdown option
    instead of pressing Enter, but this is a hard rule, so it's also enforced
    here independent of whether the model actually follows that instruction."""
    with psycopg.connect(**DB_CONN_KWARGS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT action_id, action_json, created_at FROM actions "
                "WHERE task_id = %s AND action_type = 'keypress'",
                (task_id,),
            )
            rows = cur.fetchall()
    for action_id, action_json, created_at in rows:
        keys = [str(k).lower() for k in (action_json or {}).get("keys", [])]
        if "enter" in keys:
            return {
                "action_id": action_id,
                "created_at": str(created_at),
                "reasoning": (action_json or {}).get("reasoning", ""),
            }
    return None


POST_HOC_SUBMIT_PHRASES = (
    "application submitted", "successfully submitted", "thank you for applying",
    "we've received your application", "we have received your application",
    "your application has been submitted", "application received",
    "submission was successful", "application has been received",
)


def _check_post_hoc_submission_confirmed(task_id: str, failure_reason: str | None) -> dict | None:
    """Reactive backstop for the class of failure _check_no_submit_clicked
    cannot see at all: a click-level check inspects DOM element_data, but a
    real incident (Afficiency/SmartRecruiters) used a Shadow DOM button
    component whose visible "Submit" label lives inside a shadow root - not
    in `text`, not in any attribute this project's DOM scraper captures.
    button_type was "button", not "submit", and no signal word appeared
    anywhere in the captured data, so the click-level check had structurally
    nothing to match against. The model itself COULD see the label visually
    and even said so in its own reasoning ("Click the Submit button...") -
    it misjudged an actual final submission as a safe intermediate gate.

    This can't prevent that click (nothing here runs before it), but it
    ensures the aftermath is never just a quiet 'terminated' status buried in
    a batch summary: if the task's own final reasoning or the model's own
    completion text confirms a real submission occurred, this is elevated to
    the exact same loud CRITICAL alarm as a live-caught submit click, because
    that's what it just as seriously represents.
    """
    def _matches(text: str | None) -> bool:
        t = (text or "").lower()
        return any(p in t for p in POST_HOC_SUBMIT_PHRASES)

    if _matches(failure_reason):
        return {"action_id": None, "element_id": None, "button_type": "post_hoc",
                "visible_text": (failure_reason or "")[:200], "detection": "failure_reason"}

    with psycopg.connect(**DB_CONN_KWARGS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT action_id, action_type, response, reasoning FROM actions "
                "WHERE task_id = %s AND action_type IN ('complete', 'terminate') "
                "ORDER BY created_at DESC LIMIT 5",
                (task_id,),
            )
            rows = cur.fetchall()
    for action_id, _atype, response, reasoning in rows:
        if _matches(response) or _matches(reasoning):
            return {"action_id": action_id, "element_id": None, "button_type": "post_hoc",
                     "visible_text": str(response or reasoning)[:200], "detection": "action_text"}
    return None


def _detect_stuck_loop(task_id: str) -> str | None:
    """Returns a human-readable description of the detected pattern, or None."""
    actions = _recent_actions(task_id)
    if len(actions) >= WATCHDOG_REPEAT_THRESHOLD:
        counts: dict[tuple[str, str, str], int] = {}
        for sig in actions:
            # "complete" has no meaningful response field - never match on it here,
            # the dedicated failed-complete counter below handles that case instead.
            if sig[0] == "complete":
                continue
            # Real false positive found live on Zoox: 6 different co-op survey
            # questions ("currently enrolled?", "school schedule?", "require
            # sponsorship?", etc.) all legitimately answered "No" on 6 different
            # elements got misdetected as one stuck repeated action, because the
            # signature used to only key on (action_type, response text) with no
            # element_id - any form with several unrelated yes/no questions that
            # happen to share an answer would trip this. element_id must be part
            # of the key so this only fires for genuine same-field repeats.
            #
            # Real false positive found live on PhysicsX: keypress actions have
            # NEITHER a meaningful response NOR an element_id (both come back
            # empty via COALESCE), so EVERY keypress in a run - Tab confirming a
            # phone country picker, ArrowDown navigating a completely different
            # "how did you hear about us" dropdown - collapsed into the exact
            # same signature ('keypress', '', ''). 4 legitimately different
            # keypresses on different widgets were counted as one stuck repeat.
            # The actual key(s) pressed live in action_json, never read before -
            # using that instead of the always-empty response field is what
            # actually distinguishes them.
            if sig[0] == "keypress":
                pressed = ",".join(str(k) for k in (sig[3] or {}).get("keys", []))
                key = (sig[0], sig[2], pressed)
            else:
                key = (sig[0], sig[2], sig[1][:40])
            counts[key] = counts.get(key, 0) + 1
        for key, count in counts.items():
            if count >= WATCHDOG_REPEAT_THRESHOLD:
                return f"repeated action {key} {count}x in last {WATCHDOG_WINDOW} actions"

    failed_completes = _failed_complete_count(task_id)
    if failed_completes >= WATCHDOG_MAX_FAILED_COMPLETES:
        return f"{failed_completes} rejected 'complete' attempts - not converging"

    same_element = _max_same_element_count(task_id)
    if same_element and same_element[1] >= WATCHDOG_MAX_SAME_ELEMENT:
        return f"element {same_element[0]} acted on {same_element[1]}x - stuck on one field"

    total_actions = _total_action_count(task_id)
    if total_actions >= WATCHDOG_MAX_TOTAL_ACTIONS:
        return f"{total_actions} total actions with no terminal state - exceeds normal-run backstop"

    return None

PROFILE = None  # set only when not TEST_MODE — never load for dummy/autofill imports
# TEST_MODE=1 swaps every identity field for a clearly-fake placeholder, not just email -
# for pure generalization/reliability testing on new, non-priority companies where the goal
# is finding bugs, not producing a real application, this keeps the user's actual name/phone/
# LinkedIn out of random new ATS systems entirely. 555-0100 is the NANP-reserved fictional
# number block (same idea as example.com for email - guaranteed non-routable/non-real).
#
# CRITICAL: when TEST_MODE=1 we must NOT open profile.json at all (hybrid_fill / dashboard
# dummy paths import this module). Real PII stays on disk; autofill never reads it.
TEST_MODE = os.environ.get("TEST_MODE") == "1"
_PROFILE_PATH = Path(__file__).resolve().parents[2] / "profile.json"

if TEST_MODE:
    FULL_NAME = "Test Dummy"
    FIRST, LAST = "Test", "Dummy"
    EMAIL = os.environ.get("TEST_EMAIL_OVERRIDE") or "test-dummy@example.com"
    PHONE = "405-555-0100"
    GITHUB = "https://github.com/test-dummy-account"
    LINKEDIN = "https://www.linkedin.com/in/test-dummy-000000000"
    DEGREES = [
        {"degree": "M.S., Example Studies", "school": "University of Alabama, Tuscaloosa"},
        {"degree": "B.S., Example Studies", "school": "GITAM, Visakhapatnam, India"},
    ]
    YEARS_EXPERIENCE = 3.0
    EEO = {
        "gender": "Decline to self identify",
        "hispanic_or_latino": "Decline to self identify",
        "race_ethnicity": "Decline to self identify",
        "veteran_status": "Decline to self identify",
        "disability_status": "Decline to self identify",
    }
    SALARY_EXPECTED_RULE = (
        "Leave blank / Decline — TEST_MODE has no salary rule from profile.json"
    )
    SALARY_CURRENT_RULE = (
        "Leave blank / Decline — TEST_MODE has no salary rule from profile.json"
    )
    RESUME_PATH = (
        "/Users/job/.openclaw/workspace/job-hunter/skyvern_runtime/trusted_uploads/dummy_resume.pdf"
    )
else:
    PROFILE = json.load(open(_PROFILE_PATH))
    p = PROFILE
    FULL_NAME = p["personal"]["full_name"]
    FIRST, LAST = FULL_NAME.split(" ", 1)
    EMAIL = os.environ.get("TEST_EMAIL_OVERRIDE") or p["contact"]["email"]
    PHONE = p["contact"]["phone"]
    GITHUB = p["links"]["github"]
    LINKEDIN = p["links"]["linkedin"]
    DEGREES = p["education"]["degrees"]
    YEARS_EXPERIENCE = p["experience"]["total_years_of_experience"]
    EEO = p["eeo_demographic"]
    SALARY_EXPECTED_RULE = p["salary_expectation"]["rule"]
    SALARY_CURRENT_RULE = p["current_salary"]["rule"]
    RESUME_PATH = (
        "/Users/job/.openclaw/workspace/job-hunter/skyvern_runtime/trusted_uploads/resume.pdf"
    )

ADDRESS_TEXT = "2500 N Lincoln Blvd, Apt 4B, Oklahoma City, OK 73105"  # placeholder-pool style address, not the job's own city

_PROFILE_INTRO = (
    "Use this TEST DUMMY applicant profile data for every field the application asks for."
    if TEST_MODE
    else "Use this REAL applicant profile data for every field the application asks for."
)

PROFILE_BLOCK = f"""
{_PROFILE_INTRO} Never invent
or substitute different values. If a field asks for something not listed here (e.g. a specific
past employer/dates, since employment history isn't recorded yet), leave it blank or skip it
and note that in your final report rather than making something up.

- Full name: {FULL_NAME} (First: {FIRST}, Last: {LAST})
- Email: {EMAIL}
- Phone: {PHONE}
- GitHub: {GITHUB}
- LinkedIn: {LINKEDIN}
- Social/portfolio links policy: do NOT fill GitHub, LinkedIn, Facebook, X/Twitter, or any other
  social link field unless that specific field is marked required. If one of these fields IS
  required, fill in LinkedIn only - never GitHub, Facebook, or X/Twitter even if required, and
  never fill more than the one required field. Leave every other social link field blank,
  including ones offered as optional convenience fields.
- Mailing address (use if a form needs one): {ADDRESS_TEXT}
- Education: {DEGREES[0]['degree']}, {DEGREES[0]['school']}; also {DEGREES[1]['degree']}, {DEGREES[1]['school']}
- Total years of experience: {YEARS_EXPERIENCE}
- US work authorization: authorized to work in the US without sponsorship
- Willing to relocate: yes
- Location preference if asked: prefer Remote, then Hybrid, then Onsite (pick the highest-priority option the form actually offers)
- Notice period: immediately available (effectively 0-1 week). If "Immediately available" (or an
  exact equivalent) is not one of the selectable options, rank every offered option from shortest
  to longest and pick whichever is CLOSEST to one week (e.g. "1 Week" or "2 Weeks" beat "1 Month",
  which beats "1-2 Months", which beats "2+ Months"). If an option itself is a range (e.g. "2
  Weeks - 1 Month"), rank it by its shorter/nearer end. If none of the offered options are anywhere
  near a week - e.g. the shortest available bucket is itself "1 Month" or "< 1 Month" - that
  shortest available bucket IS the correct choice; do not leave it unset and do not pick anything
  longer than the shortest offered option. State this substitution explicitly in your final report
  (e.g. "Notice period: no exact match, selected '< 1 Month' as the shortest available option").
- Gender: {EEO['gender']}
- Hispanic or Latino: {EEO['hispanic_or_latino']}
- Race/ethnicity: {EEO['race_ethnicity']}
- Veteran status: {EEO['veteran_status']}
- Disability status: {EEO['disability_status']}
- Age 18 or older: yes
- Worked here before or has a relative employed here: No
- Felony conviction: No
- How did you hear about this job: LinkedIn
- Resume/CV file upload field: a resume file is available at exactly this path -
  {RESUME_PATH}
  - use this exact path for any resume/CV upload field. It may not be perfectly tailored
  to this specific posting's wording, that's fine, just use it for the upload.
- If a required open-ended text field (e.g. "What excites you about this role?", a cover letter,
  or any other free-form question) has no corresponding data in this profile, do NOT invent or
  fabricate a substantive answer - never guess. If the form allows the field to stay blank, leave
  it blank. But if it's REQUIRED and a "complete" attempt gets rejected because of it, do NOT keep
  retrying "complete" over and over hoping something changes - a real incident showed this wastes
  many actions and can cause collateral damage elsewhere (re-clicking an already-correct toggle
  button out of uncertainty, accidentally un-toggling it) while never actually resolving, since no
  amount of other changes fixes a field with no data behind it. Instead, type a short, clearly-
  flagged placeholder into that ONE field, such as "N/A - no data provided in profile, please fill
  in before submitting" - this is not fabrication, it is an explicit, honest flag of a real gap,
  and the user reviews and edits everything before any real submission happens anyway. Then attempt
  "complete" once more. Always name this substitution explicitly in your final report.
- Desired/expected salary field: {SALARY_EXPECTED_RULE} IMPORTANT: there is no human
  available to ask in this environment - "ask the user" is not an action you can take here. If no
  range is stated, try leaving the field blank ONCE. If the field is required and rejects being
  blank, do NOT keep retrying it (typing, clicking, or waiting repeatedly on the same field is a
  known failure pattern from earlier runs) - after one attempt, stop, move on to every other field,
  and note in your final report that this field is genuinely blocked because no salary range was
  given and no one was available to ask. A field left honestly unresolved after one try is far
  better than looping on it.
- Current salary field: {SALARY_CURRENT_RULE}

EFFICIENCY RULES, based on real problems observed in earlier runs on this exact kind of form:
- Before answering any field, check whether it already shows the correct value from a previous
  step. Do not re-answer a field you have already correctly filled - if you're unsure whether a
  field is already correct, look at its current displayed value first, don't just re-click/re-type.
- If you attempt to mark the task complete and it is rejected, identify the ONE specific remaining
  required field that's still unaddressed before taking any other action. Do not re-do fields that
  were already filled correctly - only act on the actual blocker.
- For custom dropdown/combobox widgets (not a plain native HTML <select>) - including
  location/city/address fields even when they LOOK like a plain text input with no visible widget
  styling (a real incident showed one silently reverting a typed value later, to a wrong city,
  because the site only treats a value as "confirmed" once a suggestion is clicked, not just typed):
  prefer the SELECT_OPTION action with your best-guess full label (e.g. "Oklahoma City, OK") -
  its matching tolerates minor formatting differences (extra ", USA", capitalization), so it's
  faster than manually re-scraping and clicking a suggestion yourself. If SELECT_OPTION doesn't
  visibly select anything after one try, fall back to: type the value, then manually CLICK the
  matching option in the dropdown/listbox that appears - never press Enter/Return to confirm it,
  since that can trigger an invisible, unintended submission of the ENTIRE form even with no submit
  button ever clicked (directly observed; absolute rule, no exception). If no clickable option is
  visible, use ArrowDown to highlight it then CLICK (or click elsewhere to close the dropdown).
  After confirming, check the widget's displayed text actually matches what you intended - if it
  still doesn't after one retry (two attempts total), stop, move on, and explicitly name the
  unresolved field in your final "complete" reasoning.
- School/University, Company, Skills, and similar "search-as-you-type" fields: ALWAYS type a
  short relevant substring into the field FIRST (e.g. the school/company name), as your very first
  action on it, before trying SELECT_OPTION, clicking, or scrolling at all. Do not open the dropdown
  and scroll/hunt through whatever list appears by default - these fields commonly start with ZERO
  real options loaded (an unfiltered default/alphabetical list may still render, but it is not the
  real, matching option list) until you type something, because the actual suggestion list is
  fetched fresh based on your input. Directly observed twice this session (a University field on one
  company, a School field on another): the agent scrolled through an alphabetically-unrelated
  default list hunting for the real school name instead of typing first - on one, SELECT_OPTION
  even failed outright first because there was nothing yet to select. After typing, wait briefly for
  the real suggestion list to appear, then SELECT_OPTION or CLICK the matching suggestion.
- Toggle/segmented Yes-No style buttons (a pair or row of button-like options, not native
  radio/checkbox inputs) need care for two directly-observed reasons: (1) they can be slow to
  visually update after a click, so wait a moment before judging whether it registered rather than
  concluding it failed within the same reasoning step; (2) many are literal ON/OFF switches, not a
  one-way "select and stay selected" control - clicking an ALREADY-selected option turns it back OFF
  instead of harmlessly re-affirming it (observed live: a correctly-selected "Yes" got un-toggled by
  an unnecessary defensive re-click). Because of this, re-clicking "just to be sure" is actively
  harmful here, unlike a normal button where a repeat click is a harmless no-op. Before re-clicking
  any such button, look for its OWN visual selected-state signal (filled/dark background, border
  highlight, checkmark - whatever this widget uses) and if it already shows that signal, trust it
  and do NOT click again, even if not fully certain. Only click one that clearly does NOT show its
  selected-state signal.
- Immediately before attempting "complete", re-check every previously-filled field's CURRENTLY
  displayed value against what you intended, not just the fields you touched most recently - some
  sites' own scripts (geolocation autofill, address autocomplete) can silently revert an earlier
  field after the fact. If any field has reverted, redo it once using the rule above, then proceed
  to complete regardless of outcome - do not loop on this indefinitely.

HARD ACCURACY RULE, no exception: never silently accept or leave in place a substituted value
without saying so. The one authorized substitution is the notice-period "choose the least/shortest
offered option" rule above - that one is fine to use without asking, but must still be named
explicitly in your final report. For every OTHER field, if the real profile value can't be made to
stick in a combobox after two tries, do NOT quietly settle for whatever else happened to be
selectable and call the task complete as if it were correct - that misrepresents the applicant.
Prefer an honestly-incomplete field with a clear note over an incorrect one reported as fine. Your
final "complete" reasoning must explicitly list every field where the displayed value does not
match the real profile value, including the notice-period substitution if you made it - not just
the fields you skipped entirely.
"""

NEVER_SUBMIT = """
ABSOLUTE HARD RULE, NO EXCEPTIONS: Fill out every field on this real job application as
accurately and completely as you can using the profile data above, reach the final
"ready to review/submit" state of the application - but DO NOT click Submit, Apply, Send
Application, Send, Finish, Confirm, or any equivalent final-action button, under any
circumstances, no matter how confident you are that everything is correct. This is a real,
live application to a real employer and it must NOT actually be sent. Once every field is
filled and you are on the final review step (or you've filled everything possible and the
only remaining action would be a final submission), STOP immediately and report exactly
what state the page is in, without clicking anything further.

Some platforms (seen on iCIMS and Workday) require creating a login account as a gate before
reaching the real application form (email, password, confirm password, accept terms). Unlike
the final application submission, this IS allowed to go through: fill the email/password/
checkbox fields (a short invented placeholder password is fine, since a password is a
meaningless technical credential, not a misrepresentation to the employer the way a
fabricated free-text answer would be) and click Create Account/Register/Sign Up to actually
create it, using the email address given above (a dummy address in TEST_MODE - a throwaway
account on a random ATS carries no real consequence). This is a login gate, not the employer-
facing action - it is NOT equivalent to submitting the application. After the account is
created, continue on into the real application form behind it and apply every rule above to
that form as normal, including the absolute rule never to click its own final Submit/Apply/
Send/Confirm/Finish button.
"""

# Skyvern's separate complete_verify LLM call re-scrapes the page fresh on every "complete"
# attempt and judges done-ness against the WHOLE prompt above - several runs this session needed
# 2-4 rejected-complete cycles before converging (each one costs 2 extra round-trips: the agent's
# own retry + the verifier's own re-check). complete_criterion/terminate_criterion give that
# verifier a short, crisp condition to check instead of inferring from the full prompt - a real,
# exposed lever (confirmed working via the Workflow API pilot's NavigationBlock) that the ad-hoc
# Task API's SDK wrapper (skyvern.run_task()) doesn't expose, but the underlying legacy
# POST /api/v1/tasks endpoint does (TaskRequest schema, forge/sdk/schemas/tasks.py) - see
# _create_task_v1() below. Untested at scale until validated on a couple of fresh jobs.
COMPLETE_CRITERION = (
    "Every field this profile has data for is filled in with the correct value, and the page is "
    "now on its final review/submit step (or there is nothing left that can be filled) - as long "
    "as the actual job application's Submit/Apply/Send/Confirm/Finish button itself has NOT been "
    "clicked. A prior account-creation gate (Create Account/Register/Sign Up) may have already "
    "been completed - that is expected and does not count against this criterion."
)
TERMINATE_CRITERION = (
    "A required field has no corresponding profile data and cannot be resolved with the "
    "placeholder/substitution rules given, or the page shows a hard error, closed/expired "
    "posting, or dead end (e.g. a bot-detection wall) that blocks any further progress."
)

JOBS = [
    {
        "id": "st-labs-ai-engineer",
        "url": "https://jobs.ashbyhq.com/st-labs/37b019c5-8233-42f8-bde9-fba2d322b5a1/application",
        "platform": "ashby",
    },
    {
        "id": "jobgether-senior-data-engineer-data-quality-observability",
        "url": "https://jobs.lever.co/jobgether/f9ee7f33-7512-4364-a86f-5da3014eb4ef/apply",
        "platform": "lever",
    },
    {
        "id": "rzr-machine-learning-engineer",
        "url": "https://job-boards.greenhouse.io/rzr/jobs/4217715009",
        "platform": "greenhouse",
    },
    {
        "id": "mangroup-quantitative-researcher-ai-ml",
        "url": "https://job-boards.eu.greenhouse.io/mangroup/jobs/4882441101",
        "platform": "greenhouse",
    },
    # Fresh, never-before-tested companies - checking the tuning generalizes
    # rather than being overfit to the 4 companies above.
    {
        "id": "cogent-security-applied-ai-engineer",
        "url": "https://jobs.ashbyhq.com/cogent-security/8762ddb1-fad6-40e0-90a9-c239d1cbdb17/application",
        "platform": "ashby",
    },
    {
        "id": "gridware-senior-data-engineer",
        "url": "https://jobs.lever.co/gridware/adda173c-a93e-416b-8a0c-77d7e066106e/apply",
        "platform": "lever",
    },
    {
        "id": "kikoff-senior-machine-learning-engineer",
        "url": "https://job-boards.greenhouse.io/kikoff/jobs/4157394009",
        "platform": "greenhouse",
    },
    {
        "id": "zoox-part-time-student-worker-data-analyst",
        "url": "https://jobs.lever.co/zoox/5e03b357-0cc1-4194-9488-14f85044f4f9/apply",
        "platform": "lever",
    },
    # New platforms never tested before (Workday, SmartRecruiters) - checking
    # generalization beyond Ashby/Lever/Greenhouse.
    {
        "id": "intel-machine-learning-engineer",
        "url": "https://intel.wd1.myworkdayjobs.com/en-US/External/job/Machine-Learning-Engineer_JR0284870",
        "platform": "workday",
    },
    {
        "id": "unity-machine-learning-engineer",
        "url": "https://unitytech.wd1.myworkdayjobs.com/en-US/Unity/job/Machine-Learning-Engineer_JOBREQ-2616004",
        "platform": "workday",
    },
    {
        "id": "veolia-data-engineer",
        "url": "https://jobs.smartrecruiters.com/VeoliaEnvironnementSA/744000138692959-data-engineer",
        "platform": "smartrecruiters",
    },
    {
        "id": "quora-machine-learning-engineer-new-grad",
        "url": "https://jobs.ashbyhq.com/quora/cb455a21-e66f-4e21-afdf-4511c6c442d1/application",
        "platform": "ashby",
    },
    # Another new platform never tested before (iCIMS), plus fresh companies on
    # already-seen platforms for continued generalization coverage.
    {
        "id": "atlassian-machine-learning-engineer",
        "url": "https://careers-americas.icims.com/jobs/13271/machine-learning-engineer/job",
        "platform": "icims",
    },
    {
        "id": "allspring-data-engineer",
        "url": "https://careers-allspringglobal.icims.com/jobs/1231/data-engineer/job",
        "platform": "icims",
    },
    {
        "id": "jobgether-staff-data-engineer",
        "url": "https://jobs.lever.co/jobgether/29dcfc25-078b-4c44-a20f-1306d786ac40",
        "platform": "lever",
    },
    {
        "id": "bamboohr-sr-ai-ml-engineer",
        "url": "https://job-boards.greenhouse.io/bamboohr17/jobs/5719425004",
        "platform": "greenhouse",
    },
    {
        "id": "qbe-ml-engineering-intern",
        "url": "https://qbe.wd3.myworkdayjobs.com/en-US/QBE-Careers/job/Sun-Prairie-WI-USA/Data-Science-and-Machine-Learning-Engineering-Intern--Summer-2026-_344248/apply",
        "platform": "workday",
    },
    {
        "id": "raymondjames-lead-data-engineer",
        "url": "https://raymondjames.wd1.myworkdayjobs.com/en-US/RaymondJamesCareers/job/Saint-Petersburg-Florida---United-States/Lead-Data-Engineer_R-0012161/apply",
        "platform": "workday",
    },
    # Workable - a genuinely new platform never tested before, plus a fresh Ashby posting.
    {
        "id": "datatonic-machine-learning-engineer",
        "url": "https://apply.workable.com/datatonic/j/292029A9AE/",
        "platform": "workable",
    },
    {
        "id": "explosion-machine-learning-data-engineer",
        "url": "https://apply.workable.com/explosion/j/D6BD689E53/",
        "platform": "workable",
    },
    {
        "id": "quora-swe-new-grad-data-infra",
        "url": "https://jobs.ashbyhq.com/quora/6d5ce948-148e-4b0b-8623-4dbc4517a743",
        "platform": "ashby",
    },
    # Sourced directly from the real job-hunter discovery backlog (jobs.json's
    # apply_url field) instead of web search - much fresher, since these came from
    # an actual scraper run 2 days ago, not a stale search index. New platforms:
    # Rippling, Paylocity, Oracle Cloud HCM never tested before.
    {
        "id": "rippling-data-engineer-ii",
        "url": "https://ats.rippling.com/invita-healthcare-technologies/jobs/306d24d3-2002-49ec-ad4c-36bfcf695873",
        "platform": "rippling",
    },
    {
        "id": "inadev-corporation-data-engineer",
        "url": "https://recruiting.paylocity.com/Recruiting/Jobs/Details/4358480/Inadev/Data-Engineer",
        "platform": "paylocity",
    },
    {
        "id": "versa-networks-sr-data-scientist",
        "url": "https://apply.workable.com/j/5FDA6B7FB2",
        "platform": "workable",
    },
    {
        "id": "ford-motor-company-ml-ai-developer",
        "url": "https://efds.fa.em5.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs/preview/62235",
        "platform": "oracle-cloud-hcm",
    },
    {
        "id": "bv-teck-ml-platform-engineer",
        "url": "https://brightvisiontechnologies.applytojob.com/apply/hZ76Q8OIWs/ML-Platform-Engineer",
        "platform": "applytojob",
    },
    {
        "id": "lyft-staff-applied-scientist",
        "url": "https://app.careerpuck.com/job-board/lyft/job/8649343002",
        "platform": "careerpuck",
    },
    {
        "id": "microsoft-applied-scientist-ii-2",
        "url": "https://apply.careers.microsoft.com/careers/job/1970393556943885",
        "platform": "microsoft-careers",
    },
    {
        "id": "optum-associate-ai-ml-engineer-rag-llm",
        "url": "https://careers.unitedhealthgroup.com/job/-/-/34088/97202374400",
        "platform": "unitedhealth-careers",
    },
    {
        "id": "amazon-applied-scientist-automated-reasoning",
        "url": "https://www.amazon.jobs/jobs/10484024/-applied-scientist-aws-automated-reasoning",
        "platform": "amazon-jobs",
    },
    {
        "id": "gm-principal-ai-ml-engineer",
        "url": "https://search-careers.gm.com/en/jobs/jr-202604516/principal-ai-ml-engineer",
        "platform": "gm-careers",
    },
    {
        "id": "alpha-omega-data-scientist",
        "url": "https://recruiting.ultipro.com/alp1013apao/JobBoard/4db1723d-a288-4922-9a21-754e6e1cf5c4/OpportunityDetail?opportunityId=128adaaa-f555-458c-8ba8-b1c6644f9bbf",
        "platform": "ultipro",
    },
    {
        "id": "child-mind-institute-data-engineer",
        "url": "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=1b50a554-ed4a-4219-a358-aa3cbeea1e70&selectedMenuKey=CurrentOpenings&jobId=580230",
        "platform": "adp-workforce-now",
    },
    {
        "id": "narmi-applied-ai-engineer",
        "url": "https://jobs.gem.com/narmi/am9icG9zdDrV19TcOAgLx34ko3ng7_f-",
        "platform": "gem",
    },
    {
        "id": "matrixspace-machine-learning-engineer-2",
        "url": "https://careers.jobscore.com/apply_flow/applications/go?job_id=c73VzFtJffA5YyDegI_-R-",
        "platform": "jobscore",
    },
    {
        "id": "kele-inc-sr-data-engineer",
        "url": "https://www.paycomonline.net/v4/ats/web.php/portal/0CD03D349D6A29BE4D1C4745E87E83CC/jobs/83535",
        "platform": "paycom",
    },
    {
        "id": "pinterest-data-scientist",
        "url": "https://www.pinterestcareers.com/jobs/?gh_jid=4912949",
        "platform": "pinterest-careers",
    },
    {
        "id": "cash-app-staff-applied-machine-learning-engineer-fraud-abuse",
        "url": "https://block.xyz/careers/jobs/4969342008",
        "platform": "block-careers",
    },
    {
        "id": "google-senior-software-engineer-ai-ml-ads-and-commerce",
        "url": "https://careers.google.com/jobs/results/101582018550080198-senior-software-engineer/",
        "platform": "google-careers",
    },
    {
        "id": "usajobs-railroad-retirement-board-data-scientist",
        "url": "https://www.usajobs.gov/job/877873700",
        "platform": "usajobs-gov",
    },
    {
        "id": "caterpillar-senior-data-scientist",
        "url": "https://careers.caterpillar.com/en/jobs/r0000382924/senior-data-scientist/",
        "platform": "caterpillar-careers",
    },
    {
        "id": "pwc-agentic-ai-ml-developer",
        "url": "https://jobs-us.pwc.com/us/en/job/PUVPUIUS731539WDEXTERNALENUS/Acceleration-Center-Agentic-AI-and-Machine-Learning-Developer-Experienced-Associate",
        "platform": "pwc-careers",
    },
    # Fresh companies on already-well-characterized platforms (Ashby, Lever) - used
    # specifically to validate the complete_criterion/terminate_criterion change above
    # in isolation, since these platforms' quirks are already understood from many
    # earlier runs this session.
    {
        "id": "front-staff-data-engineer",
        "url": "https://jobs.ashbyhq.com/frontcareers/9af2a994-c917-4da2-9d5d-8deb2548ff2f?utm_source=NyZvmVj08g",
        "platform": "ashby",
    },
    {
        "id": "shield-ai-senior-engineer-ai-engineering-r5459",
        "url": "https://jobs.lever.co/shieldai/a32a2559-8aa2-4d18-ae61-41cbfbfb644a",
        "platform": "lever",
    },
    # Broader complete_criterion validation batch - fresh companies across platforms
    # not yet tested with the criterion fix, spanning ones with historically tricky
    # complete-verification behavior (Workday, iCIMS, SmartRecruiters) as well as
    # already-clean ones (Greenhouse, Workable), to check the fix holds broadly.
    {
        "id": "philips-data-scientist-medical-imaging-plymouth-mn",
        "url": "https://philips.wd3.myworkdayjobs.com/en-US/jobs-and-careers/job/Plymouth-Minnesota-United-States/Data-Scientist---Medical-Imaging--Plymouth--MN-_587832",
        "platform": "workday",
    },
    {
        "id": "abbvie-senior-research-statistician-hybrid",
        "url": "https://jobs.smartrecruiters.com/AbbVie/3743990014302945-senior-research-statistician-hybrid-",
        "platform": "smartrecruiters",
    },
    {
        "id": "cpi-card-group-director-data-engineering",
        "url": "https://careers-cpicardgroup.icims.com/jobs/11299/job?utm_source=indeed_integration&iis=Job%20Board&iisn=Indeed&indeed-apply-token=73a2d2b2a8d6d5c0a62696875eaebd669103652d3f0c2cd5445d3e66b1592b0f",
        "platform": "icims",
    },
    {
        "id": "versa-networks-sr-data-scientist-2",
        "url": "https://apply.workable.com/j/DBE1DB22C1",
        "platform": "workable",
    },
    {
        "id": "rocketlab-senior-analytics-engineer-i",
        "url": "https://job-boards.greenhouse.io/rocketlab/jobs/7777678003",
        "platform": "greenhouse",
    },
    {
        "id": "scribdinc-data-scientist-ii",
        "url": "https://jobs.ashbyhq.com/ScribdInc/5c1fd186-f4e7-40d0-ae0a-d55bc4a1a47d/application",
        "platform": "ashby",
    },
    {
        "id": "anavationllc-data-engineer",
        "url": "https://jobs.lever.co/anavationllc/a3b73ff5-1556-4963-aef6-39881a5cda0e/apply",
        "platform": "lever",
    },
    # Fresh breadth batch - new companies, spanning platforms with less coverage
    # than Ashby/Lever/Greenhouse, to validate the full rewritten harness
    # (persistent browser session, complete_criterion, strengthened combobox rule).
    {
        "id": "preferred-travel-group-data-scientist-i",
        "url": "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=3e5659aa-96ec-4248-b32b-238111027b07&ccId=19000101_000001&jobId=642768&source=IN&lang=en_US",
        "platform": "adp-workforce-now",
    },
    {
        "id": "liquidity-services-inc-data-engineer",
        "url": "https://ebwi.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/requisitions/preview/351",
        "platform": "oracle-cloud-hcm",
    },
    {
        "id": "narmi-applied-ai-engineer-2",
        "url": "https://jobs.gem.com/narmi/am9icG9zdDrV19TcOAgLx34ko3ng7_f-?utm_source=GemJobBoardLink&utm_medium=",
        "platform": "gem",
    },
    {
        "id": "rippling-data-ai-engineer-hybrid",
        "url": "https://ats.rippling.com/cruiseplanners/jobs/e6ae477d-6a3c-45aa-ade1-21414fe119f9?jobSite=Indeed",
        "platform": "rippling",
    },
    {
        "id": "diversified-services-network-data-engineer",
        "url": "https://apply.workable.com/j/3D91222A27",
        "platform": "workable",
    },
    # Direct OpenClaw-vs-Skyvern comparison set: the exact same real postings
    # OpenClaw has real historical timing.log/jobs.json data for (see
    # project_job_hunter memory). Running these through Skyvern (TEST_MODE)
    # gives a true same-job comparison without triggering any new real
    # OpenClaw applications (which would need the user's own dashboard
    # Start click per PLAYBOOK.md's standing rule).
    {
        "id": "cmp-teladoc-health-staff-ai-engineer-genai",
        "url": "https://teladoc.wd503.myworkdayjobs.com/en-US/teladochealth_is_hiring/job/USA---Any-Location-Remote/Staff-AI-Engineer--GenAI_JR20872",
        "platform": "workday",
    },
    {
        "id": "cmp-echostar-agentic-ai-engineer-ii",
        "url": "https://attract-careers1-echostar.icims.com/jobs/99822/job?utm_source=indeed_integration&iis=Job%20Board&iisn=Indeed&indeed-apply-token=73a2d2b2a8d6d5c0a62696875eaebd669103652d3f0c2cd5445d3e66b1592b0f",
        "platform": "icims",
    },
    {
        "id": "cmp-wand-synthesis-staff-machine-learning-engineer",
        "url": "https://jobs.ashbyhq.com/wand-ai/f5b44e9b-1be5-43e0-b03e-b94bc29360b6?utm_source=lk7EXNDGQ0",
        "platform": "ashby",
    },
    {
        "id": "cmp-chima-ai-engineer",
        "url": "https://www.linkedin.com/jobs/view/3958982160",
        "platform": "linkedin",
    },
    {
        "id": "cmp-capital-one-sr-lead-machine-learning-engineer",
        "url": "https://www.capitalonecareers.com/job/-/-/1732/98233422256",
        "platform": "capital-one-careers",
    },
    {
        "id": "cmp-jpmorganchase-data-scientist",
        "url": "https://www.linkedin.com/jobs/view/4444453667",
        "platform": "linkedin",
    },
    {
        "id": "cmp-capital-one-senior-associate-data-scientist",
        "url": "https://www.capitalonecareers.com/job/-/-/1732/98233422368",
        "platform": "capital-one-careers",
    },
    {
        "id": "cmp-deloitte-data-engineer-ii",
        "url": "https://deloitteus.avature.net/careers/InviteToApply?jobId=360414",
        "platform": "avature",
    },
    # 20-job race, batch 2: fresh real postings, sourced live from jobs.json,
    # run sequentially (one at a time) alongside the comparison set above.
    {
        "id": "race-markel-sr-people-analytics-data-analyst",
        "url": "https://markelcorp.wd5.myworkdayjobs.com/GlobalCareers/job/Richmond-VA/Sr-People-Analytics-Data-Analyst_R0023530",
        "platform": "workday",
    },
    {
        "id": "race-ameriprise-senior-data-scientist",
        "url": "https://ameriprise.wd5.myworkdayjobs.com/Ameriprise/job/Boston-Massachusetts/Senior-Data-Scientist_R26_1914-1",
        "platform": "workday",
    },
    {
        "id": "race-theradex-oncology-data-engineer",
        "url": "https://uscareers-theradex.icims.com/jobs/1443/job?utm_source=indeed_integration&iis=Job%20Board&iisn=Indeed&indeed-apply-token=73a2d2b2a8d6d5c0a62696875eaebd669103652d3f0c2cd5445d3e66b1592b0f",
        "platform": "icims",
    },
    {
        "id": "race-physicians-mutual-data-engineer",
        "url": "https://careers-physiciansmutual.icims.com/jobs/2095/data-engineer---operational-data/job",
        "platform": "icims",
    },
    {
        "id": "race-global-payments-ai-engineer",
        "url": "https://tsys.wd1.myworkdayjobs.com/TSYS/job/ALPHARETTA-GEORGIA/AI-Engineer_R0071834",
        "platform": "workday",
    },
    {
        "id": "race-rivian-vw-staff-geospatial-data-science-engineer",
        "url": "https://jobs.ashbyhq.com/rivianvw.tech/204c3ab6-fe60-4697-bf60-de1cafd649d3",
        "platform": "ashby",
    },
    {
        "id": "race-dc-pcsb-data-engineer-analytics",
        "url": "https://dcpcsb.applytojob.com/apply/8XiSTMIZUR/Data-Engineer-Analytics-Products?source=INDE&~",
        "platform": "applytojob",
    },
    {
        "id": "race-honeywell-aerospace-data-engineer-ii",
        "url": "https://icfcjb.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/requisitions/preview/118039",
        "platform": "oracle-cloud-hcm",
    },
    {
        "id": "race-oracle-principal-applied-scientist",
        "url": "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/340896",
        "platform": "oracle-cloud-hcm",
    },
    {
        "id": "race-3pillar-global-ai-data-architect",
        "url": "https://jobs.lever.co/3pillarglobal/d2ded0cc-eb2c-4185-9347-62d5c9f402bd?lever-source=Indeed",
        "platform": "lever",
    },
    {
        "id": "race-mercor-machine-learning-engineer-marketplace",
        "url": "https://jobs.ashbyhq.com/mercor/7cee578f-799c-46ad-8951-cb0b724d619a/application",
        "platform": "ashby",
    },
    {
        "id": "race-reyes-beverage-senior-financial-data-analyst",
        "url": "https://careers-reyesbeveragegroup.icims.com/jobs/34724/job",
        "platform": "icims",
    },
    # Fresh batch, sourced from the real job-hunter discovery backlog's own
    # "discovered" (never-touched) postings, filtered to genuinely new ATS
    # platforms never exercised by this harness before - each confirmed live
    # via a direct fetch (HTTP 200, no expired/closed marker) before inclusion.
    # Real desktop notifications (captcha + needs-info + safety-catch outcomes)
    # are wired into run_one() as of this batch - see _notify_desktop().
    {
        "id": "new-altamira-technologies-corp-data-engineer",
        "url": "https://app.jobvite.com/CompanyJobs/Job.aspx?j=oCZyAfwt&s=Indeed",
        "platform": "jobvite",
    },
    {
        "id": "new-labcorp-senior-rcm-data-analyst",
        "url": "https://jsv3.recruitics.com/redirect?rx_cid=3600&rx_jobId=2623469&rx_url=https%3A%2F%2Fcareers.labcorp.com%2Fglobal%2Fen%2Fjob%2F2623469%3Frx_a%3D0%26rx_c%3D%26rx_ch%3Djobp4p%26rx_group%3D442132%26rx_id%3D1d9a5f94-879e-11f1-9bfb-1d6ff3143586%26rx_job%3D2623469%26rx_medium%3Dcpc%26rx_r%3Dnone%26rx_source%3Dindeed%26rx_ts%3D20260725T100421Z%26rx_vp%3Dcpc%26source%3Dindeed%26utm_medium%3Dorganic%26utm_source%3Dindeed",
        "platform": "phenom",  # resolves through to careers.labcorp.com - a Phenom-skinned
        # career site over a Workday backend; confirmed distinct from PNC's contacthr
        # redirect, which lands on the exact same Phenom+Workday combo - only one kept.
    },
    {
        "id": "new-optum-principal-data-scientist-remote",
        "url": "https://uhg.taleo.net/careersection/10000/jobdetail.ftl?job=2379663&lang=en",
        "platform": "taleo",
    },
    {
        "id": "new-spokeo-senior-data-engineer",
        "url": "https://spokeo.na.teamtailor.com/jobs/609343-senior-data-engineer",
        "platform": "teamtailor",
    },
    {
        "id": "new-allcloud-machine-learning-engineer",
        "url": "https://www.comeet.com/jobs/allcloud/71.008/machine-learning-engineer/3C.D65",
        "platform": "comeet",
    },
    {
        "id": "new-numentica-llc-senior-databricks-data-engineer",
        "url": "https://numentica.zohorecruit.com/jobs/careers/253104000021405365/Senior-Databricks-Data-Engineer?$apply=true&source=CareerSite",
        "platform": "zoho-recruit",
    },
    {
        "id": "new-cruisebound-data-scientist",
        "url": "https://cruisebound.breezy.hr/p/40a2b0b63666-data-scientist/apply?source=BuiltInNationwide",
        "platform": "breezyhr",
    },
    {
        "id": "new-elder-research-machine-learning-engineer",
        "url": "https://elderresearch.clearcompany.com/careers/jobs/6480247b-566e-afe4-d465-ab8496b13199/apply",
        "platform": "clearcompany",
    },
    {
        "id": "new-green-cabbage-inc-data-scientist-i",
        "url": "https://greencabbage.bamboohr.com/careers/51?source=BuiltInNationwide",
        "platform": "bamboohr",
    },
    {
        "id": "new-ultralytics-llm-engineer",
        "url": "https://ultralytics.jobs.personio.de/?language=en#1893660",
        "platform": "personio",
    },
    {
        "id": "new-superior-plus-propane-customer-marketing-data-science-specialist",
        "url": "https://jobs.dayforcehcm.com/en-US/superiorplus/CANDIDATEPORTAL/jobs/41069",
        "platform": "dayforcehcm",
    },
    {
        "id": "new-kalibrate-senior-analytics-engineer",
        "url": "https://kalibrate.recruitee.com/o/senior-analytics-engineer?source=Indeed",
        "platform": "recruitee",
    },
    {
        "id": "new-eab-data-engineer",
        "url": "http://recruit.hirebridge.com/v3/Jobs/JobDetails.aspx?jid=611179&cid=7856&locvalue=1032",
        "platform": "hirebridge",
    },
    {
        "id": "new-evolver-inc-power-platform-and-ai-engineer",
        "url": "https://www.applicantpro.com/openings/evolver/jobs/4158576-886424",
        "platform": "applicantpro",
    },
    {
        "id": "new-elder-research-inc-machine-learning-engineer",
        "url": "https://elderresearch.hrmdirect.com/employment/view.php?req=3770334&jbsrc=1014&location=96ed04a2-5a1b-c0ca-ac6e-ee1722501f27",
        "platform": "hrmdirect",
    },
    {
        "id": "new-cohere-commerce-data-scientist-applied-ai",
        "url": "https://app.dover.com/apply/Cohere%20Commerce/1a648ded-8940-41b5-a225-6950d29fb27b",
        "platform": "dover",
    },
    {
        "id": "new-teema-computer-vision-engineer",
        "url": "https://app.workwolf.com/pipelineLink/HZPHB6CC?utm_source=indeed",
        "platform": "workwolf",
    },
]


async def _create_task_v1(
    url: str,
    navigation_goal: str,
    max_steps_per_run: int,
    include_action_history_in_verification: bool,
    complete_criterion: str,
    terminate_criterion: str,
    browser_session_id: str | None = None,
) -> str:
    """Bypasses skyvern.run_task() - the generated SDK wrapper doesn't expose
    complete_criterion/terminate_criterion at all, even though the underlying legacy
    POST /api/v1/tasks endpoint (TaskRequest schema, forge/sdk/schemas/tasks.py)
    accepts both as flat fields. Same task creation path either way - the returned
    task_id works identically with get_run()/cancel_run() and every watchdog query
    already in this file, since those all key on task_id regardless of how the task
    was created. Caveat: this route is tagged legacy_base_router server-side and
    isn't in the generated Python client - could be removed without notice."""
    body = {
        "url": url,
        "navigation_goal": navigation_goal,
        "max_steps_per_run": max_steps_per_run,
        "include_action_history_in_verification": include_action_history_in_verification,
        "complete_criterion": complete_criterion,
        "terminate_criterion": terminate_criterion,
    }
    if browser_session_id:
        body["browser_session_id"] = browser_session_id
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/api/v1/tasks",
            headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        return resp.json()["task_id"]


# Real ATS-side text a task's own failure_reason/terminate reasoning uses when it hits
# a CAPTCHA/human-verification wall it correctly refuses to solve (per PLAYBOOK.md's
# hard rule) - seen live this session on Kikoff (Greenhouse verification gate) and
# cpi-card-group (iCIMS hCaptcha). Case-insensitive substring match, not exhaustive.
CAPTCHA_MARKERS = ("captcha", "hcaptcha", "recaptcha", "human verification", "are you human")


def _looks_like_captcha_block(text: str | None) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(marker in low for marker in CAPTCHA_MARKERS)


def _notify_desktop(title: str, body: str, url: str | None = None) -> None:
    """Real macOS modal (`display dialog`, not `display notification`) - same
    pattern as dashboard/server.py's _send_desktop_notification and for the same
    reason: a notification banner auto-dismisses after a few seconds with no
    per-call override, `display dialog` is the only osascript primitive that
    stays on screen until dismissed. Reimplemented standalone here (not imported
    from the production server) so this R&D harness stays decoupled from
    production internals. Runs in a daemon thread - the dialog can sit
    unanswered for minutes, and this must never block the batch's sequential
    job loop from moving on to the next job."""
    body = body.strip().replace("\n", " ")[:300]

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    offer_open = bool(url)
    if offer_open:
        script = (
            f'display dialog "{esc(body)}" with title "{esc(title)}" '
            'buttons {"Dismiss", "Open Application"} default button "Open Application" '
            'with icon caution'
        )
    else:
        script = (
            f'display dialog "{esc(body)}" with title "{esc(title)}" '
            'buttons {"OK"} default button "OK" with icon caution'
        )

    def _run():
        try:
            result = subprocess.run(
                ["osascript", "-e", "beep", "-e", script],
                capture_output=True, text=True,
            )
            if offer_open and "Open Application" in result.stdout:
                subprocess.run(["open", url], capture_output=True)
        except Exception as e:
            print(f"warn: desktop notification failed: {e}", flush=True)

    threading.Thread(target=_run, daemon=True).start()


async def _poll_and_finalize(skyvern, job, run_id: str, t0: float, timeout: float):
    """Shared polling/watchdog loop, factored out of run_one() so a captcha-continuation
    run (continue_after_captcha, below) gets the exact same safety checks as a normal
    run - never a reduced-safety code path just because it's a resume."""
    watchdog_triggered = None
    submit_alarm = None
    enter_alarm = None
    result = None
    err = None
    terminal_statuses = {"completed", "failed", "terminated", "canceled", "timed_out"}
    cancel_requested_at = None
    while True:
        await asyncio.sleep(WATCHDOG_POLL_S)
        if time.time() - t0 > timeout:
            err = "client-side timeout waiting for run to finish"
            try:
                await skyvern.cancel_run(run_id)
            except Exception as cancel_exc:
                print(f"[{job['id']}] failed to cancel run {run_id} after client-side timeout: {cancel_exc}", flush=True)
            break
        status = _task_status(run_id)
        if status in terminal_statuses:
            if cancel_requested_at is not None and status == "completed":
                print(
                    f"[{job['id']}] WATCHDOG cancel was requested at {cancel_requested_at:.1f}s but the "
                    f"task converged to 'completed' on its own first - false positive, not counting it "
                    f"as a real stuck-loop catch.",
                    flush=True,
                )
                watchdog_triggered = None
            break

        submit_hit = _check_no_submit_clicked(run_id)
        if submit_hit and cancel_requested_at is None:
            print(
                f"\n{'!' * 70}\n[{job['id']}] CRITICAL: SUBMIT-TYPE CLICK DETECTED on run {run_id}\n"
                f"  action_id={submit_hit['action_id']} element_id={submit_hit['element_id']}\n"
                f"  button_type={submit_hit['button_type']!r} visible_text={submit_hit['visible_text']!r}\n"
                f"  Cancelling immediately.\n{'!' * 70}\n",
                flush=True,
            )
            await skyvern.cancel_run(run_id)
            cancel_requested_at = time.time() - t0
            submit_alarm = submit_hit
            continue

        enter_hit = _check_enter_keypress(run_id)
        if enter_hit and cancel_requested_at is None:
            print(
                f"\n{'!' * 70}\n[{job['id']}] CRITICAL: ENTER KEYPRESS DETECTED on run {run_id}\n"
                f"  action_id={enter_hit['action_id']} reasoning={enter_hit['reasoning']!r}\n"
                f"  Enter can implicitly submit a form even with no submit click - cancelling immediately.\n{'!' * 70}\n",
                flush=True,
            )
            await skyvern.cancel_run(run_id)
            cancel_requested_at = time.time() - t0
            enter_alarm = enter_hit
            continue

        loop_sig = _detect_stuck_loop(run_id)
        if loop_sig and cancel_requested_at is None:
            print(f"[{job['id']}] WATCHDOG: repeat pattern {loop_sig} - cancelling run {run_id}", flush=True)
            await skyvern.cancel_run(run_id)
            cancel_requested_at = time.time() - t0
            watchdog_triggered = {"reason": loop_sig, "detected_at": cancel_requested_at}

    result = await skyvern.get_run(run_id)
    final_submit_hit = _check_no_submit_clicked(run_id)
    if final_submit_hit and submit_alarm is None:
        print(
            f"\n{'!' * 70}\n[{job['id']}] CRITICAL: SUBMIT-TYPE CLICK DETECTED (final check) on run {run_id}\n"
            f"  action_id={final_submit_hit['action_id']} element_id={final_submit_hit['element_id']}\n"
            f"  button_type={final_submit_hit['button_type']!r} visible_text={final_submit_hit['visible_text']!r}\n"
            f"{'!' * 70}\n",
            flush=True,
        )
        submit_alarm = final_submit_hit
    final_enter_hit = _check_enter_keypress(run_id)
    if final_enter_hit and enter_alarm is None:
        print(
            f"\n{'!' * 70}\n[{job['id']}] CRITICAL: ENTER KEYPRESS DETECTED (final check) on run {run_id}\n"
            f"  action_id={final_enter_hit['action_id']} reasoning={final_enter_hit['reasoning']!r}\n"
            f"{'!' * 70}\n",
            flush=True,
        )
        enter_alarm = final_enter_hit
    if submit_alarm is None:
        post_hoc_hit = _check_post_hoc_submission_confirmed(run_id, getattr(result, "failure_reason", None))
        if post_hoc_hit:
            print(
                f"\n{'!' * 70}\n[{job['id']}] CRITICAL: SUBMISSION CONFIRMED POST-HOC on run {run_id}\n"
                f"  detection={post_hoc_hit['detection']} text={post_hoc_hit['visible_text']!r}\n"
                f"  No click-level signal caught this in advance (likely a Shadow DOM button -\n"
                f"  see _check_post_hoc_submission_confirmed docstring). A REAL submission may\n"
                f"  have occurred - this is not a prevention, only detection after the fact.\n"
                f"{'!' * 70}\n",
                flush=True,
            )
            submit_alarm = post_hoc_hit
    return watchdog_triggered, submit_alarm, enter_alarm, result, err


async def run_one(skyvern, job, max_steps=50, timeout=900):
    # max_steps raised in step with WATCHDOG_MAX_TOTAL_ACTIONS (both 35->50) - actions-
    # per-step isn't always > 1 (some complex forms run close to 1:1), so leaving this
    # lower than the action ceiling above risked Skyvern's own native step-limit silently
    # capping a legitimately long form before the watchdog got a chance to see it clearly.
    prompt = f"Fill out this real job application form.\n{PROFILE_BLOCK}\n{NEVER_SUBMIT}"
    t0 = time.time()
    print(f"[{job['id']}] START {time.strftime('%Y-%m-%d %H:%M:%S %Z')}", flush=True)
    result = None
    err = None
    watchdog_triggered = submit_alarm = enter_alarm = None
    browser_session_id = None
    session_app_url = None
    captcha_blocked = False
    try:
        # HYB2-001: refuse when Playwright Chrome-for-Testing fill/hold is live.
        if (os.environ.get("FASTFILL_FORCE_HEADED") or "").strip().lower() not in (
            "1",
            "true",
            "yes",
        ):
            # CHR3-003 / HYB3-001: UI + OpenClaw PartyRock are not fill CfT.
            exclude_markers = (
                "dashboard_ui_profile",
                "--app=http://127.0.0.1:8787",
                "openclaw/user-data",
                "--remote-debugging-port=18800",
            )
            try:
                cft_out = subprocess.check_output(
                    ["pgrep", "-lf", "Google Chrome for Testing"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
            except (subprocess.CalledProcessError, FileNotFoundError, OSError):
                cft_out = ""
            for line in cft_out.splitlines():
                if "Helper" in line or "crashpad" in line:
                    continue
                if "MacOS/Google Chrome for Testing" not in line and "/chrome " not in line:
                    continue
                if any(m in line for m in exclude_markers):
                    continue
                raise RuntimeError(
                    "HYB2-001: headed Chrome-for-Testing fill/hold already running — "
                    "refusing Skyvern browser session (cap=1)"
                )
        # Persistent session (skyvern.create_browser_session(), NOT the SDK's default
        # ephemeral per-task browser) so a captcha-caused termination leaves the browser
        # window open for the user to actually solve it in, instead of it closing the
        # moment the task gives up - user asked for this after watching a real hCaptcha
        # block (cpi-card-group) terminate cleanly but with no way to get past it.
        session = await skyvern.create_browser_session(timeout=60)
        browser_session_id = session.browser_session_id
        session_app_url = session.app_url
        # Round-trip reduction: Skyvern's separate completion-verifier call (re-scrapes
        # the page fresh on every "complete" attempt, success or failure - a full extra
        # round-trip each time) used to judge done-ness from ONLY the current page
        # snapshot plus the ENTIRE prompt above, with no crisp success condition and no
        # memory of what actions were actually taken. include_action_history_in_verification
        # (validated earlier this session) gives it the action history; complete_criterion/
        # terminate_criterion (validated at scale across 6+ platforms) gives it a short,
        # explicit condition instead of inferring from the whole prompt.
        # skyvern.run_task()'s SDK wrapper doesn't expose either criterion field, so this
        # goes through _create_task_v1() (the legacy endpoint) instead - see that
        # function's docstring for why the returned task_id is a drop-in replacement for
        # run_task()'s run_id everywhere below.
        run_id = await _create_task_v1(
            url=job["url"],
            navigation_goal=prompt,
            max_steps_per_run=max_steps,
            include_action_history_in_verification=True,
            complete_criterion=COMPLETE_CRITERION,
            terminate_criterion=TERMINATE_CRITERION,
            browser_session_id=browser_session_id,
        )
        watchdog_triggered, submit_alarm, enter_alarm, result, err = await _poll_and_finalize(
            skyvern, job, run_id, t0, timeout
        )
        if result and _looks_like_captcha_block(getattr(result, "failure_reason", None)):
            captcha_blocked = True
            print(
                f"\n{'#' * 70}\nCAPTCHA_BLOCKED [{job['id']}]\n"
                f"  browser_session_id={browser_session_id}\n"
                f"  app_url={session_app_url}\n"
                f"  Browser window kept OPEN (not closed) - solve the captcha there, then\n"
                f"  call continue_after_captcha() with this browser_session_id to resume.\n{'#' * 70}\n",
                flush=True,
            )
            _notify_desktop(
                f"Job Hunter (Skyvern): {job['id']}",
                f"CAPTCHA is blocking this application ({job['platform']}). Browser stayed "
                f"open - click Open Application to solve it, then resume the run.",
                url=session_app_url,
            )
        else:
            # Every other non-clean outcome also needs a live ping, not just a log line
            # someone has to go looking for - that was the exact gap found live: the
            # captcha-detect mechanism worked, but only ever wrote to a file no one was
            # watching in real time. Priority order below picks the single most specific
            # reason when more than one signal fired on the same run.
            final_status = _task_status(run_id)
            failure_reason = getattr(result, "failure_reason", None) if result else None
            if final_status == "terminated":
                _notify_desktop(
                    f"Job Hunter (Skyvern): {job['id']}",
                    f"Needs your input - agent stopped: {failure_reason or 'required info missing, see log'}",
                )
            elif watchdog_triggered:
                _notify_desktop(
                    f"Job Hunter (Skyvern): {job['id']}",
                    f"Stuck-loop safety catch, run cancelled: {watchdog_triggered.get('reason', '')}",
                )
            elif submit_alarm:
                _notify_desktop(
                    f"Job Hunter (Skyvern): {job['id']}",
                    f"Safety catch: submit-type button clicked ({submit_alarm.get('visible_text', '')!r}) - run cancelled.",
                )
            elif enter_alarm:
                _notify_desktop(
                    f"Job Hunter (Skyvern): {job['id']}",
                    "Safety catch: Enter keypress detected mid-form - run cancelled.",
                )
            elif err or final_status in ("failed", "timed_out"):
                _notify_desktop(
                    f"Job Hunter (Skyvern): {job['id']}",
                    f"Error: {err or failure_reason or final_status}",
                )
    except Exception as e:
        err = err or str(e)
    finally:
        # Only close the persistent session on a normal end - a captcha-blocked run
        # needs the browser to stay open for the human to actually interact with it.
        if browser_session_id and not captcha_blocked:
            try:
                await skyvern.close_browser_session(browser_session_id)
            except Exception as close_exc:
                print(f"[{job['id']}] failed to close browser session {browser_session_id}: {close_exc}", flush=True)

    elapsed = time.time() - t0
    print(
        f"[{job['id']}] END elapsed={elapsed:.1f}s error={err} watchdog={bool(watchdog_triggered)} "
        f"SUBMIT_ALARM={bool(submit_alarm)} ENTER_ALARM={bool(enter_alarm)} CAPTCHA_BLOCKED={captcha_blocked}",
        flush=True,
    )

    out = {
        "id": job["id"],
        "url": job["url"],
        "platform": job["platform"],
        "elapsed_seconds": elapsed,
        "error": err,
        "watchdog_triggered": watchdog_triggered,
        "submit_alarm": submit_alarm,
        "enter_alarm": enter_alarm,
        "captcha_blocked": captcha_blocked,
        "browser_session_id": browser_session_id if captcha_blocked else None,
        "result": json.loads(result.model_dump_json()) if result else None,
    }
    (RESULTS_DIR / f"{job['id']}.json").write_text(json.dumps(out, indent=2))
    print(f"[{job['id']}] saved -> real_job_results/{job['id']}.json", flush=True)
    return out


async def continue_after_captcha(skyvern, job, browser_session_id: str, max_steps=50, timeout=900):
    """Resumes a job after the user has manually solved a captcha in the still-open
    browser window from a prior captcha_blocked run_one() call. Reuses the same
    browser_session_id (same page/cookies/state - does NOT re-navigate to job['url']
    via a fresh browser), a continuation-worded prompt, and the exact same
    _poll_and_finalize() safety checks as a normal run - never a reduced-safety resume
    path just because it's picking up mid-form."""
    prompt = (
        "Continue filling out this real job application form from its CURRENT state - "
        "do not navigate away or restart from scratch. A CAPTCHA/human-verification step "
        "that was blocking progress has just been solved by a human; the page should now "
        "be past it. Pick up exactly where the form currently stands.\n"
        f"{PROFILE_BLOCK}\n{NEVER_SUBMIT}"
    )
    t0 = time.time()
    print(f"[{job['id']}] RESUME after captcha {time.strftime('%Y-%m-%d %H:%M:%S %Z')}", flush=True)
    run_id = await _create_task_v1(
        url=job["url"],
        navigation_goal=prompt,
        max_steps_per_run=max_steps,
        include_action_history_in_verification=True,
        complete_criterion=COMPLETE_CRITERION,
        terminate_criterion=TERMINATE_CRITERION,
        browser_session_id=browser_session_id,
    )
    watchdog_triggered, submit_alarm, enter_alarm, result, err = await _poll_and_finalize(
        skyvern, job, run_id, t0, timeout
    )
    try:
        await skyvern.close_browser_session(browser_session_id)
    except Exception as close_exc:
        print(f"[{job['id']}] failed to close browser session {browser_session_id}: {close_exc}", flush=True)

    elapsed = time.time() - t0
    print(
        f"[{job['id']}] RESUME END elapsed={elapsed:.1f}s error={err} watchdog={bool(watchdog_triggered)} "
        f"SUBMIT_ALARM={bool(submit_alarm)} ENTER_ALARM={bool(enter_alarm)}",
        flush=True,
    )
    out = {
        "id": job["id"] + "-resumed",
        "url": job["url"],
        "platform": job["platform"],
        "elapsed_seconds": elapsed,
        "error": err,
        "watchdog_triggered": watchdog_triggered,
        "submit_alarm": submit_alarm,
        "enter_alarm": enter_alarm,
        "result": json.loads(result.model_dump_json()) if result else None,
    }
    (RESULTS_DIR / f"{job['id']}-resumed.json").write_text(json.dumps(out, indent=2))
    print(f"[{job['id']}] saved -> real_job_results/{job['id']}-resumed.json", flush=True)
    return out


async def main():
    # A base_url override (SKYVERN_BASE_URL_OVERRIDE) lets a second lane point
    # at a second Skyvern server instance (its own port, its own DeepSeek key)
    # so two lanes can race in parallel while each lane stays strictly
    # sequential internally - one job at a time per racer, matching the
    # requested "race" methodology exactly (no intra-lane concurrency to
    # contaminate any single job's timing).
    base_url = os.environ.get("SKYVERN_BASE_URL_OVERRIDE", BASE_URL)
    skyvern = Skyvern(base_url=base_url, api_key=API_KEY)
    # Comma-separated list of job ids runs them in that exact order, one at a
    # time, within this single process - the natural way to run a sequential
    # "lane" without needing a separate invocation (and a fresh wait) per job.
    only = sys.argv[1] if len(sys.argv) > 1 else None
    if only:
        ids = [i.strip() for i in only.split(",") if i.strip()]
        by_id = {j["id"]: j for j in JOBS}
        jobs = [by_id[i] for i in ids if i in by_id]
    else:
        jobs = JOBS
    for job in jobs:
        await run_one(skyvern, job)


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""Drive PartyRock's resume tailoring app directly - no agent/LLM involved.

Generating a tailored resume is pure "paste text, wait for a web app to
finish streaming, copy the result" - no judgment calls in it. Doing this
via the agent's browser tool means the LLM has to snapshot-and-decide its
way through a multi-minute wait, and every one of those snapshots is a full
accessibility-tree dump that gets re-processed by every later call in that
turn - real token cost for zero real decisions.

This connects to OpenClaw's managed Chrome-for-Testing over CDP
(``127.0.0.1:18800``, profile ``~/.openclaw/browser/openclaw/user-data``) —
the same session as ``./open_partyrock.sh`` / dashboard Start tailor. That is
**not** Cursor's IDE browser tool and not daily Google Chrome; if PartyRock
shows a sign-in wall, re-auth with ``./open_partyrock.sh`` then retry. Drives
the page with Playwright and polls the DOM in a plain loop.

Each run claims one PartyRock CDP tab (reuses an idle leftover page when
possible, otherwise ``/json/new``) so parallel jobs never share/overwrite one
PartyRock page. The dashboard lets the tab close as soon as the generated
resume is collected; ``--keep-open`` is reserved for an explicit manual/debug
hold.

Usage:
  python3 tailor_resume.py --jd-file PATH --title ROLE --company COMPANY \
      --location "City, ST" --out PATH
  python3 tailor_resume.py --jd-file PATH --title ROLE --company COMPANY \
      --location "City, ST" --out PATH \
      --job-id ID --keep-open

Exit code 0 + writes --out on success. Nonzero + prints an error on
failure/timeout - the caller should fall back to ``./open_partyrock.sh``
(manual paste) rather than guessing at a fix or using a generic browser tool.
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from partyrock_config import (
    build_partyrock_input,
    partyrock_mode_label,
    partyrock_url,
    test_mode_from_env,
)
from partyrock_tabs import (
    clear_tab_meta,
    close_job_partyrock_tab,
    close_tab,
    open_job_partyrock_tab,
    write_tab_meta,
)

JD_PLACEHOLDER = "Paste the complete job description here"
CDP_URL = "http://127.0.0.1:18800"
POLL_INTERVAL_S = 4
STABLE_POLLS_REQUIRED = 3
EARLY_CHECK_DELAY_S = 15


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def strip_line_numbers(text: str) -> str:
    """PartyRock's code viewer renders a line-number gutter as part of the
    element's own text content (e.g. "1\\documentclass..."), not as
    separate markup. A greedy "strip leading digits" regex is wrong here:
    when real content starts with a digit too (a phone number, a year),
    it gets eaten along with the line-number prefix (observed: line 26's
    "555-123-4567" became "-123-4567"). Line numbers are strictly
    sequential from 1, so strip exactly len(str(line_index)) leading
    characters instead - that consumes precisely the number gutter no
    matter what the actual content starts with."""
    lines = text.split("\n")
    cleaned = []
    for i, line in enumerate(lines, start=1):
        prefix_len = len(str(i))
        cleaned.append(line[prefix_len:] if line[:prefix_len] == str(i) else line)
    return "\n".join(cleaned)


STRUCTURAL_LINE_PREFIXES = ("\\begin{document}", "\\end{document}", "\\documentclass", "\\usepackage",
                             "\\title", "\\setlength", "\\pagenumbering", "\\setstretch", "\\titleformat",
                             "\\titlespacing", "\\needspace", "\\setlist")


def fix_known_latex_bugs(text: str) -> str:
    """PartyRock's own LaTeX generation has a recurring bug (seen across
    multiple real runs, not an extraction artifact): it randomly emits one
    extra stray closing brace after an \\mbox{...} - sometimes bare
    (\\mbox{MySQL}}), sometimes nested (\\textbf{\\mbox{X}}}) - there's no
    single textual pattern that covers every case, so this scans each
    content line's brace depth and drops any '}' that would take it
    negative (an unmatched close), which is exactly what a stray extra
    brace looks like. Preamble/structural lines are left untouched since
    they're already correctly balanced and some legitimately reference
    braces in ways this per-line scan isn't meant for."""
    lines = text.split("\n")
    fixed = []
    for line in lines:
        if not line.strip() or line.strip().startswith(STRUCTURAL_LINE_PREFIXES):
            fixed.append(line)
            continue
        depth = 0
        out_chars = []
        for ch in line:
            if ch == "{":
                depth += 1
                out_chars.append(ch)
            elif ch == "}":
                if depth == 0:
                    continue  # drop the stray unmatched closing brace
                depth -= 1
                out_chars.append(ch)
            else:
                out_chars.append(ch)
        fixed.append("".join(out_chars))
    result = "\n".join(fixed)
    # The Latin Modern text font used here can't render a raw Unicode
    # em-dash (observed: "Missing character" warnings in tectonic) - the
    # LaTeX em-dash ligature (---) renders correctly and looks identical.
    return result.replace("—", "---")


def find_latex_code(page) -> str | None:
    for code_el in page.locator("code").all():
        try:
            text = code_el.inner_text()
        except Exception:
            continue
        if "documentclass" in text:
            return text
    return None


def _page_target_id(page) -> str | None:
    try:
        sess = page.context.new_cdp_session(page)
        info = sess.send("Target.getTargetInfo")
        return (info.get("targetInfo") or {}).get("targetId")
    except Exception:
        return None


def _find_page_by_target_id(browser, target_id: str, timeout_s: float = 10.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for ctx in browser.contexts:
            for page in ctx.pages:
                if _page_target_id(page) == target_id:
                    return page
        time.sleep(0.1)
    return None


def main() -> None:
    run_start = time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument("--jd-file", default=None)
    parser.add_argument(
        "--title",
        default="",
        help="Role title included with the description sent to PartyRock",
    )
    parser.add_argument(
        "--company",
        default="",
        help="Company name included with the description sent to PartyRock",
    )
    parser.add_argument(
        "--location",
        default="",
        help="Job location included with the description sent to PartyRock",
    )
    parser.add_argument("--out", default=None)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--cdp-url", default=CDP_URL)
    parser.add_argument(
        "--job-id",
        default=None,
        help="Job id for per-job PartyRock tab registry (resumes/<id>/partyrock_tab.json)",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Leave the PartyRock tab open after success (dashboard closes on Mark as applied)",
    )
    parser.add_argument(
        "--test-mode",
        action="store_true",
        default=None,
        help="Use PartyRock Testing app (Ultron-Resume-v3-Testing)",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Use PartyRock Real app (Ultron-Resume-v3)",
    )
    parser.add_argument(
        "--print-url",
        action="store_true",
        help="Print resolved PartyRock URL and exit (no browser)",
    )
    args = parser.parse_args()

    if args.real and args.test_mode:
        log("error: pass only one of --test-mode / --real")
        sys.exit(2)
    if args.real:
        test_mode = False
    elif args.test_mode:
        test_mode = True
    else:
        test_mode = test_mode_from_env(default=True)

    url = partyrock_url(test_mode=test_mode)
    mode = partyrock_mode_label(test_mode=test_mode)
    if args.print_url:
        print(url)
        return

    if not args.jd_file or not args.out:
        log("error: --jd-file and --out are required (unless --print-url)")
        sys.exit(2)

    job_description = Path(args.jd_file).read_text()
    partyrock_input = build_partyrock_input(
        job_description,
        args.location,
        company=args.company,
        title=args.title,
    )
    job_id = (args.job_id or "").strip()
    keep_open = bool(args.keep_open)
    out_path = Path(args.out)
    job_dir = out_path.parent
    cdp_http = args.cdp_url.rstrip("/")

    log(f"PartyRock mode={mode} url={url} keep_open={keep_open} job_id={job_id or '-'}")

    target_id: str | None = None
    success = False
    try:
        # One tab per job: reuse live registry entry when present (dedupe retries).
        if job_id:
            tab_info = open_job_partyrock_tab(
                job_dir,
                job_id,
                url,
                cdp_http=cdp_http,
            )
        else:
            from partyrock_tabs import create_tab

            tab_info = create_tab(url, cdp_http=cdp_http)
        target_id = str(tab_info["id"])
        reused = bool(tab_info.get("reused"))
        log(
            f"{'reusing' if reused else 'opened'} PartyRock tab "
            f"target_id={target_id}"
        )

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(args.cdp_url)
            page = _find_page_by_target_id(browser, target_id)
            if page is None:
                log(f"error: could not attach to CDP target {target_id}")
                sys.exit(1)
            try:
                if tab_info.get("needs_navigate"):
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                else:
                    # /json/new may already be navigating; wait for app UI.
                    page.wait_for_load_state("domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)

                try:
                    page.locator("button:has-text('Dismiss')").first.click(timeout=3000)
                except Exception:
                    pass  # cookie banner not present or already dismissed

                jd_box = page.get_by_placeholder(JD_PLACEHOLDER)
                if jd_box.count() == 0:
                    log("error: could not find the job description textbox on the page")
                    sys.exit(1)
                jd_box.fill(partyrock_input)

                play_button = page.locator("button:has-text('Play App')").first
                if play_button.count() == 0:
                    log("error: could not find the 'Play App' button")
                    sys.exit(1)
                play_button.click()
                log(
                    "submitted role title + company + location + JD, waiting "
                    "for PartyRock to generate the resume..."
                )
                poll_start = time.monotonic()

                # Fail fast instead of silently polling the full timeout: a real
                # run flips the button to "Pause" and the JD box's placeholder
                # disappears within a few seconds. If neither happens shortly
                # after clicking, the click didn't actually start anything -
                # observed live: this state is indistinguishable from a normal
                # in-progress run by find_latex_code() alone, since both show
                # zero LaTeX output early on, so it has to be checked separately.
                page.wait_for_timeout(EARLY_CHECK_DELAY_S * 1000)
                signin_wall = page.locator("text=Sign in to join the party").count() > 0
                if signin_wall:
                    log(
                        "error: PartyRock showed a sign-in wall — OpenClaw CfT "
                        "session expired/logged out. Re-auth with "
                        "./open_partyrock.sh (Chrome-for-Testing + "
                        "~/.openclaw/browser/openclaw/user-data :18800), then retry. "
                        "Do not use a generic IDE/browser tool — cookies won't match."
                    )
                    page.screenshot(path=str(out_path.with_suffix(".signin_wall.png")))
                    sys.exit(1)
                started = page.locator("button:has-text('Pause')").count() > 0
                if not started:
                    log(f"error: clicked 'Play App' but it never started running (still shows 'Play App' after {EARLY_CHECK_DELAY_S}s) - "
                        "not a normal wait, something rejected the run silently")
                    page.screenshot(path=str(out_path.with_suffix(".not_started.png")))
                    sys.exit(1)

                deadline = time.time() + args.timeout
                prev_len = -1
                stable_count = 0
                latex_text = None
                poll_num = 0
                while time.time() < deadline:
                    time.sleep(POLL_INTERVAL_S)
                    poll_num += 1
                    latex_text = find_latex_code(page)
                    length = len(latex_text) if latex_text else 0
                    if length and length == prev_len:
                        stable_count += 1
                    else:
                        stable_count = 0
                    prev_len = length
                    if poll_num % 3 == 0 or stable_count:
                        state = "not started yet" if not length else f"{length} chars, stable_count={stable_count}"
                        log(f"poll {poll_num} ({time.monotonic() - poll_start:.0f}s elapsed): {state}")
                    if stable_count >= STABLE_POLLS_REQUIRED and latex_text and "\\end{document}" in latex_text:
                        break
                else:
                    log(f"error: timed out after {args.timeout}s waiting for PartyRock to finish")
                    page.screenshot(path=str(out_path.with_suffix(".timeout.png")))
                    sys.exit(1)

                if not latex_text or "\\end{document}" not in latex_text:
                    log("error: PartyRock finished but the output doesn't look like complete LaTeX")
                    sys.exit(1)

                cleaned = fix_known_latex_bugs(strip_line_numbers(latex_text))
                out_path.write_text(cleaned)
                log(f"wrote tailored resume -> {args.out} (total {time.monotonic() - run_start:.0f}s)")
                success = True

                if keep_open and target_id:
                    title = ""
                    try:
                        title = page.title() or ""
                    except Exception:
                        pass
                    meta_job = job_id or out_path.parent.name
                    write_tab_meta(
                        job_dir,
                        job_id=meta_job,
                        target_id=target_id,
                        url=url,
                        title=title,
                        in_use=False,
                    )
                    log(
                        f"keeping PartyRock tab open (target_id={target_id}); "
                        "dashboard closes it on Mark as applied"
                    )
            finally:
                # PR2-001: do NOT call browser.close() on a CDP-attached browser.
                # connect_over_cdp + close() clears contexts and can drop / blank
                # PartyRock tabs (including keep_open / other jobs' tabs). Leaving
                # the sync_playwright() context tears down the driver connection
                # without issuing Browser.close / context teardown on Chrome.
                pass
    finally:
        if target_id and not (success and keep_open):
            try:
                if job_id:
                    closed = close_job_partyrock_tab(job_id, job_dir, cdp_http=cdp_http)
                    log(
                        f"PartyRock tab cleanup target_id={target_id} "
                        f"reason={closed.get('reason')}"
                    )
                else:
                    close_tab(target_id, cdp_http=cdp_http)
                    log(f"closed PartyRock tab target_id={target_id} (run not kept open)")
            except Exception as e:
                log(f"warn: failed to close PartyRock tab {target_id}: {e}")
            if not job_id:
                clear_tab_meta(job_dir)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Drive PartyRock's resume tailoring app directly - no agent/LLM involved.

Generating a tailored resume is pure "paste text, wait for a web app to
finish streaming, copy the result" - no judgment calls in it. Doing this
via the agent's browser tool means the LLM has to snapshot-and-decide its
way through a multi-minute wait, and every one of those snapshots is a full
accessibility-tree dump that gets re-processed by every later call in that
turn - real token cost for zero real decisions.

This connects to OpenClaw's own managed browser over the Chrome DevTools
Protocol (verified live: it's already reachable at 127.0.0.1:18800 and
shares the same authenticated session/cookies as the agent's browser tool -
no separate login needed), drives the page directly with Playwright, and
polls the DOM in a plain loop.

Usage:
  python3 tailor_resume.py --jd-file PATH --out PATH [--timeout 600]

Exit code 0 + writes --out on success. Nonzero + prints an error on
failure/timeout - the caller should fall back to the agent driving the
browser tool manually rather than guessing at a fix.
"""
import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

PARTYROCK_URL = "https://partyrock.aws/u/yo68749/mICSZlMtv/Ultron-Resume-v1"
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
    "405-667-0068" became "-667-0068"). Line numbers are strictly
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


def main() -> None:
    run_start = time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument("--jd-file", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--cdp-url", default=CDP_URL)
    args = parser.parse_args()

    job_description = Path(args.jd_file).read_text()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp_url)
        ctx = browser.contexts[0]
        page = ctx.new_page()
        try:
            page.goto(PARTYROCK_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            try:
                page.locator("button:has-text('Dismiss')").first.click(timeout=3000)
            except Exception:
                pass  # cookie banner not present or already dismissed

            jd_box = page.get_by_placeholder(JD_PLACEHOLDER)
            if jd_box.count() == 0:
                log("error: could not find the job description textbox on the page")
                sys.exit(1)
            jd_box.fill(job_description)

            play_button = page.locator("button:has-text('Play App')").first
            if play_button.count() == 0:
                log("error: could not find the 'Play App' button")
                sys.exit(1)
            play_button.click()
            log("submitted JD, waiting for PartyRock to generate the resume...")
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
                log("error: PartyRock showed a sign-in wall - the managed browser's session has expired/logged out")
                page.screenshot(path=str(Path(args.out).with_suffix(".signin_wall.png")))
                sys.exit(1)
            started = page.locator("button:has-text('Pause')").count() > 0
            if not started:
                log(f"error: clicked 'Play App' but it never started running (still shows 'Play App' after {EARLY_CHECK_DELAY_S}s) - "
                    "not a normal wait, something rejected the run silently")
                page.screenshot(path=str(Path(args.out).with_suffix(".not_started.png")))
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
                page.screenshot(path=str(Path(args.out).with_suffix(".timeout.png")))
                sys.exit(1)

            if not latex_text or "\\end{document}" not in latex_text:
                log("error: PartyRock finished but the output doesn't look like complete LaTeX")
                sys.exit(1)

            cleaned = fix_known_latex_bugs(strip_line_numbers(latex_text))
            Path(args.out).write_text(cleaned)
            log(f"wrote tailored resume -> {args.out} (total {time.monotonic() - run_start:.0f}s)")
        finally:
            page.close()


if __name__ == "__main__":
    main()

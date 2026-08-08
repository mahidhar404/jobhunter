#!/usr/bin/env python3
"""Shrink a resume's LAYOUT (never its content) until it fits 2 pages AND
has no line running off the right edge of the page.

PartyRock resumes occasionally run a couple of lines past 2 pages - not a
word of text changes here, only two purely cosmetic LaTeX knobs every
resume this project compiles already has (see tailor_resume.py's
template): geometry's margin and setspace's line-stretch factor. This is
a plain regex substitution + recompile loop, not a rewrite - the same
"deterministic script, not judgment" pattern as everything else here.

Also checks for horizontal overflow (a real one observed live: a dense
"Cloud & DevOps" skills line, packed with several \\mbox{}-wrapped
multi-word tools, ran 55pt past the right margin - the PDF isn't a
straightforward page-count problem, it was already exactly 2 pages, so
the old page-count-only check here would have shipped it as fine). This
is a "too many unbreakable tokens on one line" issue, not a content bug -
the fix is the same two layout knobs, and reducing margin here also
*widens* the usable line, which helps both page count and horizontal
overflow at once, so one loop below now checks both.

Usage:
  python3 fit_resume_pages.py RESUME_TEX_PATH

Requires RESUME_TEX_PATH to already be compiled once (its .pdf sibling
must exist). Recompiles once up front regardless of that existing PDF's
page count, since the pdf alone can't reveal overfull-hbox warnings - only
a fresh tectonic run's log can. Does nothing further if that compile is
already <= 2 pages with no overfull hbox beyond OVERFULL_THRESHOLD_PT.
Otherwise steps margin/stretch down through a fixed, already-tested
sequence, recompiling with tectonic each time, until both conditions
are satisfied. If the tightest step still fails either one, leaves that
tightest attempt in place (better than the original) and exits nonzero
so the caller knows it didn't fully succeed - never silently ships
something claiming to fit when it doesn't.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

# Resolve tectonic: explicit env override → PATH → macOS Homebrew default.
# Keeps the original macOS path as the final fallback.
TECTONIC_BIN = (
    (os.environ.get("JOBHUNTER_TECTONIC_BIN") or "").strip()
    or shutil.which("tectonic")
    or "/opt/homebrew/bin/tectonic"
)

# A few points of overfull hbox is common in ordinary LaTeX documents and
# invisible in practice (font metrics rounding, not a real overflow).
# Observed real failure was 55pt (roughly 0.76in) - visibly off the page.
# This threshold is deliberately well below that: worth reacting to
# anything a reader could plausibly notice, not chasing sub-pixel slack.
OVERFULL_THRESHOLD_PT = 8.0
_OVERFULL_RE = re.compile(r"Overfull \\hbox \(([0-9.]+)pt too wide\)")

# Each step is (margin_inches, line_stretch), ordered from the original
# down to the tightest combination still considered readable. Never goes
# past the last (tightest) entry - a resume that still overflows there
# needs a human look, not further automatic squeezing. Shrinking margin
# also widens the usable line width, so tighter steps help horizontal
# overflow too, not just page count.
STEPS = [
    (0.75, 1.10),  # PartyRock's own default
    (0.70, 1.08),
    (0.65, 1.05),
    (0.60, 1.02),
    (0.55, 1.00),
    (0.50, 0.98),
]


def max_overfull_pt(compile_output: str) -> float:
    matches = _OVERFULL_RE.findall(compile_output)
    return max((float(m) for m in matches), default=0.0)


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def page_count(pdf_path: Path) -> int:
    return len(PdfReader(str(pdf_path)).pages)


def apply_step(tex_text: str, margin: float, stretch: float) -> str:
    tex_text = re.sub(
        r"\\usepackage\[margin=[0-9.]+in\]\{geometry\}",
        f"\\\\usepackage[margin={margin}in]{{geometry}}",
        tex_text,
    )
    tex_text = re.sub(
        r"\\setstretch\{[0-9.]+\}",
        f"\\\\setstretch{{{stretch}}}",
        tex_text,
    )
    return tex_text


def compile_tex(tex_path: Path, timeout_s: int = 60) -> tuple[int, str]:
    proc = subprocess.run(
        [TECTONIC_BIN, str(tex_path.name)],
        capture_output=True, text=True, timeout=timeout_s, cwd=str(tex_path.parent),
    )
    return proc.returncode, proc.stdout + proc.stderr


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tex_path")
    args = parser.parse_args()

    tex_path = Path(args.tex_path)
    pdf_path = tex_path.with_suffix(".pdf")
    if not pdf_path.exists():
        log(f"error: {pdf_path} doesn't exist - compile once with tectonic before running this")
        sys.exit(1)

    original_text = tex_path.read_text()

    # Recompile once up front at the original settings (STEPS[0]) even
    # though a .pdf already exists - only a fresh log reveals overfull
    # hbox, and the existing PDF's page count alone isn't enough to know
    # whether this resume is actually fine.
    tex_path.write_text(apply_step(original_text, *STEPS[0]))
    exit_code, output = compile_tex(tex_path)
    if exit_code != 0:
        log(f"error: tectonic failed recompiling at the original settings (exit {exit_code}) - leaving as-is")
        sys.exit(1)
    pages = page_count(pdf_path)
    overfull = max_overfull_pt(output)
    log(f"original settings -> {pages} page(s), max overfull hbox {overfull:.1f}pt")
    if pages <= 2 and overfull <= OVERFULL_THRESHOLD_PT:
        log("already fits with no significant horizontal overflow, nothing to do")
        return

    log("trying tighter layout (margin/line-spacing only) to fix page count and/or horizontal overflow")
    for margin, stretch in STEPS[1:]:  # STEPS[0] just got tried above
        tex_path.write_text(apply_step(original_text, margin, stretch))
        exit_code, output = compile_tex(tex_path)
        if exit_code != 0:
            log(f"warn: tectonic failed at margin={margin}in stretch={stretch} (exit {exit_code}), trying next step")
            continue
        pages = page_count(pdf_path)
        overfull = max_overfull_pt(output)
        log(f"margin={margin}in stretch={stretch} -> {pages} page(s), max overfull hbox {overfull:.1f}pt")
        if pages <= 2 and overfull <= OVERFULL_THRESHOLD_PT:
            log(f"fits in 2 pages with no significant horizontal overflow at margin={margin}in stretch={stretch}")
            return

    log(f"warn: still {pages} page(s) / {overfull:.1f}pt overfull at the tightest tested layout "
        f"(margin={STEPS[-1][0]}in, stretch={STEPS[-1][1]}) - left in place, but this one needs a manual look")
    sys.exit(1)


if __name__ == "__main__":
    main()

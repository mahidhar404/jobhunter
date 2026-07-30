#!/usr/bin/env python3
"""Convert a plain-text file (a job description) into a simple, readable
PDF - pure text layout, no judgment call, so it runs as a script rather
than costing agent tokens.

fpdf2's built-in core fonts only render latin-1, but scraped job
descriptions routinely carry smart quotes/dashes/bullets from the source
site - rather than fail on those or require bundling a full Unicode font,
common lookalikes are normalized to their plain-ASCII equivalents first,
and anything still outside latin-1 after that is dropped rather than
raising, so a weird character never blocks a real job's tracker row from
getting its JD PDF.

Usage:
  python3 text_to_pdf.py INPUT_TXT OUTPUT_PDF --title "Company - Role"
"""
import argparse
import unicodedata
from pathlib import Path

from fpdf import FPDF

_REPLACEMENTS = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "--", "…": "...",
    "•": "-", "●": "-", "▪": "-", " ": " ",
}


def normalize_for_latin1(text: str) -> str:
    for src, dst in _REPLACEMENTS.items():
        text = text.replace(src, dst)
    text = unicodedata.normalize("NFKD", text)
    return text.encode("latin-1", errors="ignore").decode("latin-1")


def convert(text: str, output_path: Path, title: str | None = None) -> None:
    pdf = FPDF(format="letter")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    if title:
        pdf.set_font("Helvetica", "B", 14)
        pdf.multi_cell(0, 8, normalize_for_latin1(title))
        pdf.ln(4)
        pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 6, normalize_for_latin1(text))
    pdf.output(str(output_path))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_txt")
    parser.add_argument("output_pdf")
    parser.add_argument("--title", default=None)
    args = parser.parse_args()

    text = Path(args.input_txt).read_text()
    convert(text, Path(args.output_pdf), title=args.title)
    print(f"wrote {args.output_pdf}")


if __name__ == "__main__":
    main()

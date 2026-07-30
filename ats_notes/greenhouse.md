# Greenhouse - known form structure

Greenhouse's own embedded form is much simpler, more standard HTML than
Workday's - mostly plain input `id`s and readable label text, consistent
across every company using Greenhouse. Each company can still add its own
custom free-text screening questions on top of this shared part - those
still need reading and answering fresh per job, this note only covers the
fixed shared structure.

## Core fields (plain ids)
`#first_name`, `#last_name`, `#email`, `#phone`. Resume upload:
`input[type="file"]` (usually the first file input on the page).

## Fields found by matching visible label text, not a fixed id
LinkedIn URL, personal website, and a separate "Legal Name" field (if
distinct from first/last name) are matched by their visible label text
(e.g. an element containing "LinkedIn") rather than a stable id - a
straightforward lookup by label text works reliably here.

## Notes
- Greenhouse offers "autofill from resume," which pre-populates downstream
  fields (education, work history). It's a convenience, but the parser is
  imperfect - job titles/dates can come out wrong or truncated - so verify
  anything it fills rather than trusting it as ground truth.
- Every company's own custom screening questions still need reading and
  answering fresh each time; there's no shared pattern for those since
  each employer writes their own.

## Source
Selector/structure knowledge verified against real, working automation
code: https://github.com/ChadLei/Job-Auto-Apply - only the field-mapping
knowledge was taken from this; the same project also used bot-detection
evasion techniques (undetected browser drivers, spoofed user agents) which
were deliberately NOT carried over here - never bypass bot detection.

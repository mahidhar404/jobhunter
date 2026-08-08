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

## Custom selects (react-select)
Company screening / EEO / Country / City often use Greenhouse's
`.select__container` + `.select__control` + `input.select__input`
(role=combobox) widgets, not native `<select>`. Skyvern's extract selector
`label:has-text('…') input:visible` usually fails: the input is a sibling of
the label inside `.select-shell`, not a descendant.

Deterministic fill (`scripts/fastfill/gh_select.py`): click `.select__control`
→ type a filter fragment → click matching `.select__option` (never Enter;
Enter can submit the whole form). Scope options to `.select__option` so
intl-tel-input dial-code lists are ignored.

## Notes
- Greenhouse offers "autofill from resume," which pre-populates downstream
  fields (education, work history). It's a convenience, but the parser is
  imperfect - job titles/dates can come out wrong or truncated - so verify
  anything it fills rather than trusting it as ground truth.
- Every company's own custom screening questions still need reading and
  answering fresh each time; there's no shared pattern for those since
  each employer writes their own.
- HOW_HEARD profile value "Internet job board" often has no exact option —
  fall back to "Other" and fill the specify text box.
- EEO decline phrases vary ("Decline to Self Identify" vs "I don’t wish to
  answer" for veteran); match with soft aliases from DUMMY_PROFILE.

## Source
Selector/structure knowledge verified against real, working automation
code: https://github.com/ChadLei/Job-Auto-Apply - only the field-mapping
knowledge was taken from this; the same project also used bot-detection
evasion techniques (undetected browser drivers, spoofed user agents) which
were deliberately NOT carried over here - never bypass bot detection.

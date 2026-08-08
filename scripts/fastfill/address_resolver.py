"""Resolve a synthetic apartment address from the city in a resume header.

This module never reads profile.json.  The resume city is authoritative for
address fields in both dummy and explicitly enabled real-profile fills; identity
fields remain owned by the existing mode gates.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import random
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_BANK_PATH = HERE / "fixtures" / "us_apartment_addresses.json"
REMOTE_MARKERS = re.compile(
    r"\b(remote|remote\s*[-/]\s*(?:us|usa)|united states remote|anywhere in the us)\b",
    re.I,
)
CITY_STATE_RE = re.compile(
    r"\b([A-Z][A-Za-z.' -]{1,45}?),\s*([A-Z]{2})\b"
)

# ZIPs are generated inside a normal five-digit prefix for the state.  They are
# synthetic privacy placeholders, not claims that a particular building exists.
STATE_ZIP_BASE = {
    "AL": 35000, "AK": 99500, "AZ": 85000, "AR": 71600, "CA": 90000,
    "CO": 80000, "CT": 6000, "DE": 19700, "DC": 20000, "FL": 32000,
    "GA": 30000, "HI": 96700, "ID": 83200, "IL": 60000, "IN": 46000,
    "IA": 50000, "KS": 66000, "KY": 40000, "LA": 70000, "ME": 3900,
    "MD": 20600, "MA": 1000, "MI": 48000, "MN": 55000, "MS": 38600,
    "MO": 63000, "MT": 59000, "NE": 68000, "NV": 88900, "NH": 3000,
    "NJ": 7000, "NM": 87000, "NY": 10000, "NC": 27000, "ND": 58000,
    "OH": 43000, "OK": 73000, "OR": 97000, "PA": 15000, "RI": 2800,
    "SC": 29000, "SD": 57000, "TN": 37000, "TX": 75000, "UT": 84000,
    "VT": 5000, "VA": 22000, "WA": 98000, "WV": 24700, "WI": 53000,
    "WY": 82000,
}
STREET_NAMES = (
    "Maple Avenue",
    "Cedar Street",
    "Parkview Drive",
    "Riverside Avenue",
    "Market Street",
    "Oak Lane",
)


def _header_text(text: str) -> str:
    """Keep only the header so employer/school locations cannot win."""
    if r"\begin{document}" in text:
        text = text.split(r"\begin{document}", 1)[1]
    parts = re.split(
        r"\\section\s*\{|(?:^|\n)\s*(?:SUMMARY|EXPERIENCE|EDUCATION|SKILLS)\s*(?:\n|$)",
        text,
        maxsplit=1,
        flags=re.I,
    )
    return parts[0][:2000]


def extract_resume_city_state(text: str) -> tuple[str, str] | None:
    """Extract ``City, ST`` under the resume name; Remote uses bank policy."""
    header = _header_text(text)
    matches = list(CITY_STATE_RE.finditer(header))
    if not matches:
        return None
    city, state = matches[0].group(1).strip(), matches[0].group(2).upper()
    if state == "US":
        return None
    return city, state


def _load_bank(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("addresses"), list):
        raise ValueError(f"address bank missing addresses list: {path}")
    return data


@contextmanager
def _locked_bank(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with lock_path.open("r+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _save_bank(path: Path, data: dict) -> None:
    payload = json.dumps(data, indent=2, sort_keys=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=".us_apartments_",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _generated_address(city: str, state: str) -> dict:
    digest = hashlib.sha256(f"{city.lower()}|{state}".encode()).digest()
    number = 100 + int.from_bytes(digest[:2], "big") % 8900
    street = STREET_NAMES[digest[2] % len(STREET_NAMES)]
    unit_prefix = "Apt" if digest[3] % 2 == 0 else "Unit"
    unit = f"{unit_prefix} {1 + digest[4] % 18}{chr(65 + digest[5] % 6)}"
    base = STATE_ZIP_BASE.get(state, 10000)
    zip_code = f"{base + int.from_bytes(digest[6:8], 'big') % 900:05d}"
    return {
        "city": city,
        "state": state,
        "zip": zip_code,
        "street": f"{number} {street}",
        "unit": unit,
        "generated": True,
    }


def resolve_apartment_address(
    city: str,
    state: str,
    *,
    bank_path: Path | str = DEFAULT_BANK_PATH,
    rng: random.Random | None = None,
) -> dict:
    """Pick an exact-city bank entry or persist a deterministic new one."""
    city = re.sub(r"\s+", " ", str(city or "")).strip()
    state = str(state or "").strip().upper()
    if not city or not re.fullmatch(r"[A-Z]{2}", state):
        raise ValueError(f"need a US city and two-letter state, got {city!r}, {state!r}")
    path = Path(bank_path)
    with _locked_bank(path):
        data = _load_bank(path)
        matches = [
            row for row in data["addresses"]
            if str(row.get("city", "")).strip().casefold() == city.casefold()
            and str(row.get("state", "")).strip().upper() == state
        ]
        if matches:
            chooser = rng or random.SystemRandom()
            return dict(chooser.choice(matches))
        generated = _generated_address(city, state)
        data["addresses"].append(generated)
        _save_bank(path, data)
        return dict(generated)


def resolve_address_from_text(
    text: str,
    *,
    fallback_location: str = "",
    bank_path: Path | str = DEFAULT_BANK_PATH,
    rng: random.Random | None = None,
) -> dict:
    """Resolve from resume text, then job location, then Remote/US policy."""
    path = Path(bank_path)
    data = _load_bank(path)
    found = extract_resume_city_state(text)
    if found is None and fallback_location:
        found = extract_resume_city_state(fallback_location)
    remote = bool(REMOTE_MARKERS.search(_header_text(text))) or bool(
        fallback_location and REMOTE_MARKERS.search(fallback_location)
    )
    if found is None:
        default = data.get("remote_default") or {"city": "Chicago", "state": "IL"}
        if not remote and fallback_location and not REMOTE_MARKERS.search(fallback_location):
            raise ValueError(f"could not parse US city/state from location: {fallback_location!r}")
        found = (default["city"], default["state"])
    return resolve_apartment_address(
        found[0],
        found[1],
        bank_path=path,
        rng=rng,
    )


def resolve_address_for_resume(
    resume_path: Path | str,
    *,
    fallback_location: str = "",
    bank_path: Path | str = DEFAULT_BANK_PATH,
    rng: random.Random | None = None,
) -> dict:
    """Read PDF/text/TeX and resolve its synthetic exact-city apartment."""
    path = Path(resume_path)
    if path.suffix.lower() == ".pdf":
        from resume_parser import extract_text

        text = extract_text(path)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return resolve_address_from_text(
        text,
        fallback_location=fallback_location,
        bank_path=bank_path,
        rng=rng,
    )


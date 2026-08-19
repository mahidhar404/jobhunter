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

# ZIPs for generated rows pick from real USPS samples per state so city/state
# pairs validate on ATS forms.  Unit numbers and street numbers stay synthetic.
STATE_ZIP_SAMPLES: dict[str, tuple[str, ...]] = {
    "AL": ("35203", "36104", "35801", "35401", "36801"),
    "AK": ("99501", "99701", "99801", "99645", "99577"),
    "AZ": ("85004", "85281", "85701", "85224", "86301"),
    "AR": ("72201", "72701", "72756", "72901", "71601"),
    "CA": ("94103", "95110", "94612", "91911", "95814"),
    "CO": ("80202", "80014", "80302", "80903", "80521"),
    "CT": ("06103", "06510", "06810", "06901", "06457"),
    "DE": ("19801", "19901", "19711", "19720", "19958"),
    "DC": ("20003", "20001", "20009", "20016", "20032"),
    "FL": ("32801", "33301", "33130", "33602", "32202"),
    "GA": ("30303", "30060", "30004", "30030", "31401"),
    "HI": ("96815", "96813", "96720", "96732", "96753"),
    "ID": ("83702", "83301", "83401", "83814", "83642"),
    "IL": ("60601", "60661", "62701", "60540", "60201"),
    "IN": ("46204", "47401", "46802", "47904", "46601"),
    "IA": ("50309", "52240", "52801", "51101", "50010"),
    "KS": ("66210", "66101", "67202", "66603", "67401"),
    "KY": ("40202", "40507", "42101", "41011", "40701"),
    "LA": ("70112", "70801", "71101", "70501", "70433"),
    "ME": ("04101", "04401", "04240", "04901", "04092"),
    "MD": ("21202", "20814", "20852", "21401", "21740"),
    "MA": ("02108", "02110", "02139", "02143", "02169"),
    "MI": ("48226", "48104", "48067", "49503", "48933"),
    "MN": ("55401", "55102", "55425", "55802", "55901"),
    "MS": ("39201", "39530", "38655", "39759", "39401"),
    "MO": ("63101", "63105", "64105", "65201", "65806"),
    "MT": ("59101", "59801", "59715", "59601", "59901"),
    "NE": ("68102", "68508", "69101", "68701", "68046"),
    "NV": ("89101", "89011", "89501", "89801", "89030"),
    "NH": ("03101", "03801", "03301", "03431", "03755"),
    "NJ": ("07302", "07102", "08608", "08401", "07030"),
    "NM": ("87102", "87501", "88001", "88201", "87401"),
    "NY": ("10001", "11201", "11101", "10701", "14202"),
    "NC": ("27601", "27701", "27514", "27511", "28202"),
    "ND": ("58102", "58501", "58201", "58801", "58401"),
    "OH": ("43215", "44113", "45202", "43604", "43016"),
    "OK": ("73102", "74103", "74012", "73501", "74820"),
    "OR": ("97201", "97005", "97124", "97401", "97701"),
    "PA": ("19106", "15222", "17101", "16501", "18101"),
    "RI": ("02903", "02840", "02886", "02904", "02809"),
    "SC": ("29201", "29401", "29601", "29902", "29301"),
    "SD": ("57104", "57701", "57006", "57401", "57301"),
    "TN": ("37201", "37064", "38103", "37604", "37402"),
    "TX": ("78701", "75201", "75024", "77002", "78205"),
    "UT": ("84101", "84070", "84601", "84321", "84770"),
    "VT": ("05401", "05602", "05701", "05301", "05101"),
    "VA": ("22314", "22204", "22201", "23219", "23510"),
    "WA": ("98101", "98004", "98033", "98052", "98402"),
    "WV": ("25301", "25701", "26505", "24701", "26003"),
    "WI": ("53703", "53202", "54301", "54601", "54911"),
    "WY": ("82001", "82601", "83001", "82414", "82070"),
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
    samples = STATE_ZIP_SAMPLES.get(state, ("10001",))
    zip_code = samples[int.from_bytes(digest[6:8], "big") % len(samples)]
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
            # Stable order so repeats (and rng=None) are deterministic.
            matches = sorted(
                matches,
                key=lambda row: (
                    str(row.get("street") or ""),
                    str(row.get("unit") or ""),
                    str(row.get("zip") or ""),
                ),
            )
            if rng is None:
                return dict(matches[0])
            return dict(rng.choice(matches))
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


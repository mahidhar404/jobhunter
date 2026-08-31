"""Shared helpers for worldwide board scrapers (JSON / RSS / HTML).

Polite HTTP only — no CAPTCHA solve, no login walls. Normalizes to the
standard listing schema used by scout / ATS / India scrapers.
"""
from __future__ import annotations

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Broad tech title net (mirrors Discover intent).
RELEVANT_KEYWORDS = (
    "machine learning", "ml engineer", "mlops", "deep learning",
    "artificial intelligence", " ai ", "ai engineer", "llm", "nlp",
    "computer vision", "data scientist", "data science", "data engineer",
    "data analyst", "analytics engineer", "software engineer",
    "software developer", "backend", "full stack", "fullstack",
    "python", "platform engineer", "research engineer", "applied scientist",
)


def log(msg: str, *, err: bool = False) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr if err else sys.stdout, flush=True)


def polite_sleep(seconds: float = 0.4) -> None:
    if seconds > 0:
        time.sleep(seconds)


def is_relevant_title(title: str) -> bool:
    t = f" {(title or '').lower()} "
    return any(kw in t for kw in RELEVANT_KEYWORDS)


def is_within_days(date_str: str | None, max_days: int = 10) -> bool:
    """Return True if date_str is within max_days of today, or if date is unknown."""
    if not date_str:
        return True
    try:
        dt = datetime.fromisoformat(str(date_str).strip()[:10]).date()
        today = datetime.now().date()
        return (today - dt).days <= max_days
    except (ValueError, TypeError):
        return True


def fetch_bytes(url: str, *, headers: dict | None = None, timeout: int = 25) -> bytes | None:
    hdrs = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        log(f"warn: fetch failed {url}: {exc}", err=True)
        return None


def fetch_json(url: str, *, headers: dict | None = None, timeout: int = 25):
    hdrs = {"Accept": "application/json, text/plain, */*"}
    if headers:
        hdrs.update(headers)
    raw = fetch_bytes(url, headers=hdrs, timeout=timeout)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        log(f"warn: bad json {url}: {exc}", err=True)
        return None


def fetch_text(url: str, *, headers: dict | None = None, timeout: int = 25) -> str | None:
    raw = fetch_bytes(url, headers=headers, timeout=timeout)
    if raw is None:
        return None
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def clean_html(text: str | None) -> str:
    if not text:
        return ""
    t = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    t = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = re.sub(r"&nbsp;", " ", t)
    t = re.sub(r"&amp;", "&", t)
    t = re.sub(r"&lt;", "<", t)
    t = re.sub(r"&gt;", ">", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def parse_rss_items(xml_text: str) -> list[dict]:
    """Robust RSS/Atom item extractor with BS4 fallback → {title, link, description, pubDate}."""
    if not xml_text:
        return []
    items: list[dict] = []
    # 1. Try standard XML parser
    try:
        root = ET.fromstring(xml_text)
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = item.findtext("description") or item.findtext("content:encoded") or ""
            pub = item.findtext("pubDate") or item.findtext("published") or ""
            items.append({"title": title, "link": link, "description": desc, "pubDate": pub})
        for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
            title = (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            link = link_el.get("href") if link_el is not None else ""
            desc = entry.findtext("{http://www.w3.org/2005/Atom}summary") or ""
            pub = entry.findtext("{http://www.w3.org/2005/Atom}updated") or entry.findtext("{http://www.w3.org/2005/Atom}published") or ""
            items.append({"title": title, "link": link or "", "description": desc, "pubDate": pub})
        if items:
            return items
    except Exception:
        pass

    # 2. BeautifulSoup fallback for lenient parsing
    try:
        soup = BeautifulSoup(xml_text, "html.parser")
        for item in soup.find_all(["item", "entry"]):
            title_node = item.find("title")
            title = title_node.get_text(strip=True) if title_node else ""
            link = ""
            link_node = item.find("link")
            if link_node:
                link = link_node.get("href") or link_node.get_text(strip=True) or ""
                if not link and link_node.next_sibling and isinstance(link_node.next_sibling, str):
                    link = link_node.next_sibling.strip()
            if not link:
                m = re.search(r"<link[^>]*>(.*?)</link>", str(item), re.I | re.S)
                if m:
                    link = m.group(1).strip()
            desc_node = item.find(["description", "summary", "content"])
            desc = desc_node.get_text(strip=True) if desc_node else ""
            pub_node = item.find(["pubdate", "published", "updated", "dc:date"])
            pub = pub_node.get_text(strip=True) if pub_node else ""
            if title or link:
                items.append({"title": title, "link": link, "description": desc, "pubDate": pub})
    except Exception as exc:
        log(f"warn: rss fallback parse failed: {exc}", err=True)

    return items


def normalize_posted(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = str(raw).strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if m:
        return m.group(1)
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def listing(
    *,
    title: str,
    company: str,
    site: str,
    job_url: str,
    description: str = "",
    date_posted: str | None = None,
    location: str | None = "Remote",
    job_type: str = "fulltime",
    job_url_direct: str | None = None,
    salary_hint: str | None = None,
    max_days: int = 10,
) -> dict | None:
    if not title or not job_url:
        return None
    if not is_relevant_title(title):
        return None
    if date_posted and not is_within_days(date_posted, max_days=max_days):
        return None
    desc = clean_html(description or "")
    if salary_hint and salary_hint not in desc:
        desc = f"{desc}\nSalary: {salary_hint}".strip()
    return {
        "title": title.strip(),
        "company": (company or "").strip() or "Unknown",
        "site": site,
        "job_url": job_url,
        "job_url_direct": job_url_direct or job_url,
        "description": desc,
        "date_posted": date_posted,
        "job_type": job_type,
        "location": location or "Remote",
        "search_term": f"ww:{site}",
    }


def dedup_by_url(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for row in rows:
        url = (row.get("job_url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(row)
    return out


def write_listings(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"wrote {len(rows)} listings → {path}")

"""Website branding evidence for human review; never rename a school from HTML."""

import re
import unicodedata

from bs4 import BeautifulSoup

from src.name_cleaner import clean_school_name


def extract_website_names(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates = [
        meta.get("content", "") for meta in soup.find_all("meta")
        if str(meta.get("property", "")).lower() == "og:site_name"
    ]
    if soup.title:
        candidates.append(soup.title.get_text(" ", strip=True))
    for img in soup.find_all("img", alt=True):
        if re.search(r"logo|title|brand", str(img.get("src", "")), re.I):
            candidates.append(img["alt"])
    names = []
    for candidate in candidates:
        # A title such as "Home | School Name" should retain the brand half.
        parts = re.split(r"\s+[|\u2013\u2014-]\s+", candidate)
        for part in parts:
            name = clean_school_name(part)
            if not name or name.casefold() in {
                "home", "homepage", "welcome", "contact", "about", "untitled", "wix.com",
            }:
                continue
            if not 4 <= len(name) <= 120 or "@" in name:
                continue
            if name.casefold() not in {value.casefold() for value in names}:
                names.append(name)
    return names[:4]


def _tokens(name: str) -> set[str]:
    words = re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", name).casefold())
    return set(words) - {
        "the", "of", "and", "at", "in", "home", "welcome", "to", "school",
        "academy", "foundation", "dojo", "center", "inc", "llc", "www", "com", "org",
    }


def conflicting_website_names(name: str, observed: list[str]) -> str:
    current = _tokens(name)
    if not current or not observed:
        return ""
    for candidate in observed:
        other = _tokens(candidate)
        if other and (current <= other or other <= current):
            return ""
    return " / ".join(observed)[:400]

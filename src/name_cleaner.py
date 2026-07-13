"""
Utilities for turning Google Places business names into email-safe school names.

Google Places often returns legal suffixes, alternate-language names, unit
numbers, or SEO stuffing. Those are fine for maps, but they make outreach look
careless when the template says "I was on {{school_name}}'s site...".
"""

from __future__ import annotations

import re
import unicodedata


LEGAL_SUFFIX_RE = re.compile(
    r"\s*,?\s+\b("
    r"inc\.?|incorporated|llc|l\.l\.c\.|ltd\.?|limited|corp\.?|corporation"
    r")\b\.?$",
    re.IGNORECASE,
)

ACRONYMS = {
    "ACT",
    "AP",
    "BJJ",
    "IB",
    "LA",
    "MMA",
    "SAT",
    "STEM",
    "UCLA",
    "USC",
    "USA",
    "YMCA",
}

LOWERCASE_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "but",
    "by",
    "for",
    "from",
    "in",
    "nor",
    "of",
    "on",
    "or",
    "per",
    "the",
    "to",
    "vs",
    "with",
}

LOCATION_SUFFIXES = {
    "downtown la",
    "downtown los angeles",
    "east la",
    "east los angeles",
    "north la",
    "north los angeles",
    "south la",
    "south los angeles",
    "west la",
    "west los angeles",
}

PUNCT_TRANSLATION = str.maketrans({
    "’": "'",
    "‘": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    "→": " ",
})


def _strip_non_latin_scripts(text: str) -> str:
    """Keep Latin-script names, including accents; remove other scripts/noisy symbols."""
    chars: list[str] = []
    for char in text:
        if ord(char) < 128:
            chars.append(char)
            continue

        if char.isalpha():
            try:
                char_name = unicodedata.name(char)
            except ValueError:
                chars.append(" ")
                continue
            chars.append(char if "LATIN" in char_name else " ")
            continue

        chars.append(" ")
    return "".join(chars)


def _strip_wrapped_noise(name: str) -> str:
    """Remove parenthetical/bracketed SEO or alternate-language chunks."""
    previous = None
    current = name
    while previous != current:
        previous = current
        current = re.sub(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]\s*", " ", current)
    return current


def _smart_title_token(token: str) -> str:
    upper = token.upper()
    if upper in ACRONYMS:
        return upper

    if "-" in token:
        return "-".join(_smart_title_token(part) for part in token.split("-"))

    if "'" in token:
        parts = token.lower().split("'")
        if len(parts) == 2 and parts[1] in {"s", "t", "re", "ve", "ll", "d", "m"}:
            return parts[0].capitalize() + "'" + parts[1]
        return "'".join(part.capitalize() for part in parts)

    return token.lower().capitalize()


def _smart_title(name: str) -> str:
    tokens = name.split()
    titled = []
    for idx, token in enumerate(tokens):
        low = token.lower()
        if idx > 0 and low in LOWERCASE_WORDS:
            titled.append(low)
        else:
            titled.append(_smart_title_token(token))
    return " ".join(titled)


def _strip_location_suffix(name: str) -> str:
    """Remove location-only suffixes that make outreach sound scraped."""
    parts = re.split(r"\s+[-–—]\s+", name)
    if len(parts) <= 1:
        return name
    suffix = parts[-1].strip().lower().strip(".,;: ")
    if suffix in LOCATION_SUFFIXES:
        return " - ".join(parts[:-1]).strip()
    return name


def clean_school_name(raw_name: str) -> str:
    """
    Return a concise, English-first display name suitable for outreach.

    Examples:
      Jung Im Lee Korean Dance Academy | ... -> Jung Im Lee Korean Dance Academy
      Olive Tree Learning Academy Inc -> Olive Tree Learning Academy
      Tree House Kids #2 Daycare -> Tree House Kids Daycare
      PEPE'S SPORTS -> Pepe's Sports
      Edupro Academy(.../ Tutoring/ SAT/ ACT/ ...) -> Edupro Academy
    """
    original = str(raw_name or "").strip()
    if not original:
        return ""

    name = unicodedata.normalize("NFKC", original)
    name = name.translate(PUNCT_TRANSLATION)
    name = re.sub(r"[\r\n\t]+", " ", name)

    # Keep the English/primary name before alternate-name separators.
    name = re.split(r"\s+\|\s+|\s+//\s+", name, maxsplit=1)[0]
    name = _strip_wrapped_noise(name)

    # Drop non-Latin alternate scripts. This is deliberate for cold outreach:
    # the email copy is English and should not include parallel Korean/Chinese/
    # Japanese/etc. names from Google Places. Preserve accented Latin names.
    name = _strip_non_latin_scripts(name)

    # Remove unit/location markers and legal suffixes that make the email feel
    # like a scraped database row.
    name = re.sub(r"\s+#\s*\d+\b", " ", name)
    name = re.sub(r"\s+\b(no\.?|number)\s*\d+\b", " ", name, flags=re.IGNORECASE)
    name = _strip_location_suffix(name)
    while True:
        cleaned = LEGAL_SUFFIX_RE.sub("", name).strip()
        if cleaned == name.strip():
            break
        name = cleaned

    name = name.strip(" \"',;:-")
    name = re.sub(r"\s*/\s*", " ", name)
    name = re.sub(r"\s+", " ", name).strip()

    if not name:
        return original

    letters = [c for c in name if c.isalpha()]
    if letters:
        uppercase_ratio = sum(c.isupper() for c in letters) / len(letters)
        if uppercase_ratio >= 0.8 and len(letters) >= 4:
            name = _smart_title(name)

    return name

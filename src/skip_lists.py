"""
Pre-filter skip lists for Phase 1.
Rationale: many Places API results are known franchises, large institutions,
or hosted on enrollment vendor platforms. Cheap to skip without LLM calls.
"""

import re

# Big-box / known franchises that definitely have online enrollment already.
# Match is case-insensitive substring on the place name.
KNOWN_CHAIN_NAMES = {
    "kumon",
    "mathnasium",
    "sylvan learning",
    "huntington learning",
    "bricks 4 kidz",
    "code ninjas",
    "idtech",
    "the little gym",
    "the tutoring center",
    "my gym",
    "kidstrong",
    "gymboree",
    "goldfish swim",
    "british swim school",
    "aqua-tots",
    "kindercare",
    "primrose school",
    "bright horizons",
    "la petite academy",
    "la petite",
    "challenger school",
    "the learning experience",
    "goddard school",
    "stretch-n-grow",
    "challenger sports",
    "soccer shots",
    "i9 sports",
    "kaplan",
    "ec english",
    "ec los angeles",
    "eye level",
    "geos languages plus",
    "sprachcaffe",
    "dance 101",  # generic franchise-y
}

# Large/non-target organizations that commonly leak into broad Places searches.
# Keep this intentionally conservative: these are not independent schools and
# should never receive the small-school outreach.
NON_TARGET_EXACT_NAMES = {
    "gateway towne center",
    "los angeles",
    "la county",
    "city of la",
    "lakeshore learning",
    "shiekh",
}

NON_TARGET_NAME_PATTERNS = {
    "equinox",
    "national basketball association",
    "los angeles lakers",
    "la lakers",
    "los angeles clippers",
    "la clippers",
    "lakeshore learning",
    "major league soccer",
    "la fitness",
    "24 hour fitness",
    "planet fitness",
    "city of los angeles",
    "county of los angeles",
    "los angeles county",
    "department of recreation",
    "parks and recreation",
    "recreation center",
    "community center",
    "family center",
    "senior center",
    "senior services",
    "aquatic center",
    "swimming pool",
    "public pool",
    "swim stadium",
    "sports massage",
    "sporting goods",
    "personal trainer",
    "personal training",
    "shopping center",
    "shopping mall",
    "shoe store",
    "public library",
    "public school",
    "elementary school",
    "middle school",
    "high school",
    "adult school",
    "community adult school",
    "museum",
    "early head start",
    "head start",
    "family services",
    "software development services",
    "technology staffing",
    "tech staffing",
    "it staffing",
}

NON_TARGET_WORD_PATTERNS = {
    "nba",
    "mls",
    "ymca",
}

# If the school's "website" field points to any of these domains, they're already
# using an online enrollment vendor — skip. (Some schools list their vendor page
# as their only web presence, rather than having a real site.)
ENROLLMENT_VENDOR_DOMAINS = {
    "jackrabbitclass.com",
    "dancestudio-pro.com",
    "akadaclass.com",
    "iclasspro.com",
    "studiodirector.com",
    "mindbodyonline.com",
    "brightwheel.com",
    "procareconnect.com",
    "kindertales.com",
    "classdojo.com",
    "sawyer.com",
    "hi-sawyer.com",
    "regpacks.com",
    "campsite.com",
    "activenetwork.com",
    "amilia.com",
    "perfectmind.com",
    "tumblebee.com",
    "swimschoolsoftware.com",
    "corelms.com",
    "lapetite.com",
    "gostudiopro.com",
    "app.gostudiopro.com",
    "opus1.io",
    "childplus.net",
    "pushpress.com",
    "wellnessliving.com",
    "zenplanner.com",
    "wodify.com",
    "kicksite.com",
    "sparkmembership.com",
    "gymdesk.com",
    "clubready.com",
    "teamup.com",
}

# Places API type values that are clearly retail/public-facility results, not
# independent schools or activity programs. Keep this list conservative; Phase 3
# still handles ambiguous sites with content-based checks.
NON_TARGET_PLACE_TYPES = {
    "book_store",
    "clothing_store",
    "department_store",
    "home_goods_store",
    "shoe_store",
    "sporting_goods_store",
    "store",
    "shopping_mall",
    "toy_store",
}

TARGET_PLACE_TYPES = {
    "child_care_agency",
    "dance_school",
    "education",
    "educational_institution",
    "martial_arts_school",
    "music_school",
    "preschool",
    "school",
    "sports_activity_location",
    "swimming_school",
    "tutoring_service",
}

# Domains that indicate the school is too large / already digital
EXCLUDED_TLDS = (".edu", ".gov")

EXCLUDED_DOMAINS = {
    "big5sportinggoods.com",
    "comptoncity.org",
    "dickssportinggoods.com",
    "drewcdc.org",
    "ecenglish.com",
    "lacountypools.com",
    "lakeshorelearning.com",
    "lausd.org",
    "myeyelevel.com",
    "pacela.org",
    "schoolloop.com",
    "shiekh.com",
    "shopgatewaytownecenter.com",
    "sprachcaffe.com",
    "revize.com",
    "ymca.org",
}


def is_skipped_by_name(name: str) -> tuple[bool, str]:
    """Returns (skip, reason). True if name matches a known chain."""
    if not name:
        return False, ""
    name_lower = name.lower()
    normalized_name = " ".join(name_lower.split()).strip(".,;:! ")
    if normalized_name in NON_TARGET_EXACT_NAMES:
        return True, f"non_target_org:{normalized_name}"
    for pattern in NON_TARGET_WORD_PATTERNS:
        if re.search(rf"\b{re.escape(pattern)}\b", name_lower):
            return True, f"non_target_org:{pattern}"
    for pattern in NON_TARGET_NAME_PATTERNS:
        if pattern in name_lower:
            return True, f"non_target_org:{pattern}"
    for chain in KNOWN_CHAIN_NAMES:
        if chain in name_lower:
            return True, f"known_chain:{chain}"
    return False, ""


def is_skipped_by_domain(website: str) -> tuple[bool, str]:
    """Returns (skip, reason). True if website is on an excluded domain."""
    if not website:
        return False, ""
    website_lower = website.lower()

    for tld in EXCLUDED_TLDS:
        # match as full-word TLD (e.g. ".edu" but not ".education")
        if website_lower.endswith(tld) or tld + "/" in website_lower:
            return True, f"excluded_tld:{tld}"

    for vendor in ENROLLMENT_VENDOR_DOMAINS:
        if vendor in website_lower:
            return True, f"enrollment_vendor:{vendor}"

    for domain in EXCLUDED_DOMAINS:
        if domain in website_lower:
            return True, f"excluded_domain:{domain}"

    return False, ""


def is_skipped_by_place_types(place_types: list[str] | tuple[str, ...] | None) -> tuple[bool, str]:
    """Returns (skip, reason). True if Places classified this as clear retail/non-target."""
    normalized = {str(t or "").strip().lower() for t in (place_types or [])}
    if normalized & TARGET_PLACE_TYPES:
        return False, ""
    for place_type in sorted(NON_TARGET_PLACE_TYPES):
        if place_type in normalized:
            return True, f"non_target_place_type:{place_type}"
    return False, ""

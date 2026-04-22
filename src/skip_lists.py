"""
Pre-filter skip lists for Phase 1.
Rationale: many Places API results are known franchises, large institutions,
or hosted on enrollment vendor platforms. Cheap to skip without LLM calls.
"""

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
    "the learning experience",
    "goddard school",
    "stretch-n-grow",
    "challenger sports",
    "soccer shots",
    "i9 sports",
    "dance 101",  # generic franchise-y
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
    "gostudiopro.com",
    "app.gostudiopro.com",
    "opus1.io",
}

# Domains that indicate the school is too large / already digital
EXCLUDED_TLDS = (".edu", ".gov")


def is_skipped_by_name(name: str) -> tuple[bool, str]:
    """Returns (skip, reason). True if name matches a known chain."""
    if not name:
        return False, ""
    name_lower = name.lower()
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

    return False, ""

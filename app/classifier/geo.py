"""
L3 geo + property tagger.

Strategy (cheap-first, no LLM unless needed):
  1. Regex for MLS-####### -> direct property lookup.
  2. Regex for known county names -> county.
  3. Regex for street + city -> property fuzzy match.
  4. Fall through with county=None.

Returns (county, property_external_id).  Caller resolves property_id by
joining on contact_property + property.external_id.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.logging_setup import get_logger

log = get_logger("L3_geo")

_KNOWN_COUNTIES = ["Fulton", "DeKalb", "Cobb", "Gwinnett", "Forsyth", "Cherokee"]

# Known cities mapped back to their county (cheap heuristic - the real
# implementation should hit a gazetteer or the property table).
_CITY_TO_COUNTY = {
    "atlanta": "Fulton", "sandy springs": "Fulton", "roswell": "Fulton",
    "decatur": "DeKalb", "brookhaven": "DeKalb", "dunwoody": "DeKalb",
    "marietta": "Cobb", "smyrna": "Cobb", "kennesaw": "Cobb",
    "lawrenceville": "Gwinnett", "duluth": "Gwinnett", "sugar hill": "Gwinnett",
    "cumming": "Forsyth",
    "canton": "Cherokee", "woodstock": "Cherokee",
}

_MLS_RE = re.compile(r"\bMLS[-_ ]?(\d{6,8})\b", re.IGNORECASE)
_COUNTY_RE = re.compile(r"\b(" + "|".join(_KNOWN_COUNTIES) + r")\b", re.IGNORECASE)


@dataclass
class GeoResult:
    county: str | None
    listing_external_id: str | None
    matched_via: str        # 'mls' | 'county_name' | 'city' | 'none'


def tag_geo(body: str, subject: str | None = None) -> GeoResult:
    haystack = ((subject or "") + "\n" + (body or "")).strip()
    if not haystack:
        return GeoResult(county=None, listing_external_id=None, matched_via="none")

    # MLS
    m = _MLS_RE.search(haystack)
    if m:
        ext = f"MLS-{m.group(1)}"
        log.info("geo_match", via="mls", listing=ext)
        return GeoResult(county=None, listing_external_id=ext, matched_via="mls")

    # County name
    m = _COUNTY_RE.search(haystack)
    if m:
        # Normalise case to canonical spelling
        canon = next((c for c in _KNOWN_COUNTIES if c.lower() == m.group(1).lower()), m.group(1))
        log.info("geo_match", via="county_name", county=canon)
        return GeoResult(county=canon, listing_external_id=None, matched_via="county_name")

    # City
    lower = haystack.lower()
    for city, county in _CITY_TO_COUNTY.items():
        if city in lower:
            log.info("geo_match", via="city", city=city, county=county)
            return GeoResult(county=county, listing_external_id=None, matched_via="city")

    return GeoResult(county=None, listing_external_id=None, matched_via="none")

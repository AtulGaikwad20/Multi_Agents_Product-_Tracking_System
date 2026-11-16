"""Optional, consent-based location context.

Principles implemented here:
  * Location is never read without an explicit user action in the browser.
  * It is one-shot (no tracking): the browser asks once, we use the result once.
  * Raw coordinates are NOT stored. We keep only a place label / city / postal
    code, with an expiry, in the user's session. The user can delete it any time.
  * Coordinates are sent to a reverse-geocoding service only if the user ticked
    the separate consent for that; otherwise they are discarded and the user is
    asked to type a city or postal code instead.
  * Manual entry (city / postal code / address area) needs no device permission.
  * Everything derived from location is an *estimate*. This module never marks
    anything as retailer-confirmed.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import requests

LOCATION_TTL = timedelta(hours=24)
POSTAL = re.compile(r"\b(\d{6}|\d{5}(?:-\d{4})?|[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2})\b", re.I)


class LocationError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.user_message = message


def _clean(text, limit=120):
    text = re.sub(r"[\x00-\x1f<>\"'`]", " ", str(text or ""))
    return " ".join(text.split())[:limit]


def _now():
    return datetime.now(timezone.utc)


def from_manual(text):
    label = _clean(text)
    if len(label) < 2:
        raise LocationError("Please enter a city, area or postal code.")
    match = POSTAL.search(label)
    return _wrap(label, "manual", "manual entry", postal=match.group(1) if match else None)


def reverse_geocode(lat, lng, precise, url, user_agent, timeout=8, session=None):
    if not url:
        raise LocationError("Reverse geocoding is not configured. Please type a city or postal code instead.")
    session = session or requests.Session()
    try:
        response = session.get(url, params={"format": "jsonv2", "lat": f"{lat:.4f}", "lon": f"{lng:.4f}",
                                            "zoom": 18 if precise else 10, "addressdetails": 1},
                               headers={"User-Agent": user_agent}, timeout=timeout)
        data = response.json() if response.status_code == 200 else {}
    except (requests.RequestException, ValueError):
        raise LocationError("The place-name service could not be reached. Please type a city or postal code instead.")
    address = data.get("address") or {}
    parts = [address.get(k) for k in (("suburb", "neighbourhood", "city_district") if precise else ()) if address.get(k)]
    city = address.get("city") or address.get("town") or address.get("village") or address.get("county")
    if city:
        parts.append(city)
    if address.get("state"):
        parts.append(address["state"])
    if address.get("country"):
        parts.append(address["country"])
    if not parts:
        raise LocationError("Your position could not be turned into a place name. Please type a city or postal code.")
    return {"label": ", ".join(parts), "city": city, "postal_code": address.get("postcode") if precise else None,
            "country": address.get("country")}


def from_device(lat, lng, precision, share_with_geocoder, settings, session=None):
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        raise LocationError("Invalid coordinates.")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise LocationError("Coordinates are out of range.")
    precise = precision == "precise"
    if not share_with_geocoder:
        raise LocationError("To turn your position into a place name I need your OK to send it to the place-name "
                            "service. Otherwise please type a city or postal code.")
    found = reverse_geocode(lat, lng, precise, settings.get("GEOCODER_URL"),
                            settings.get("USER_AGENT", "ResquoraResearchBot/1.0"), session=session)
    # coordinates are intentionally dropped here
    return _wrap(found["label"], "device", "precise" if precise else "approximate",
                 city=found.get("city"), postal=found.get("postal_code"), country=found.get("country"))


def _wrap(label, source, precision, city=None, postal=None, country=None):
    now = _now()
    return {"label": label, "city": city, "postal_code": postal, "country": country, "source": source,
            "precision": precision, "granted_at": now.isoformat(timespec="seconds"),
            "expires_at": (now + LOCATION_TTL).isoformat(timespec="seconds"),
            "disclaimer": "Estimate only. Not confirmed by any retailer."}


def current(session):
    """Return the unexpired location context from the session, else None."""
    loc = session.get("pg_location")
    if not loc:
        return None
    try:
        if datetime.fromisoformat(loc["expires_at"]) < _now():
            session.pop("pg_location", None)
            return None
    except (KeyError, ValueError):
        session.pop("pg_location", None)
        return None
    return loc

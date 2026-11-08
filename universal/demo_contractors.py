"""Deterministic, explicitly fictional contractor fixtures for private testing.

Coordinates are approximate metropolitan centers (NOT the fictional street
addresses). These are not real people or verified/deliverable addresses.
Never contact them, geocode the addresses, or treat them as client data.
"""
from __future__ import annotations

# Each tuple represents one state. Two clearly labeled demo workers are
# generated per state. Latitude/longitude represent approximate city areas.
CITY_ANCHORS = (
    ("AL", "Birmingham", 33.5186, -86.8104),
    ("AK", "Anchorage", 61.2181, -149.9003),
    ("AZ", "Phoenix", 33.4484, -112.0740),
    ("AR", "Little Rock", 34.7465, -92.2896),
    ("CA", "Los Angeles", 34.0522, -118.2437),
    ("CO", "Denver", 39.7392, -104.9903),
    ("CT", "Hartford", 41.7658, -72.6734),
    ("DE", "Wilmington", 39.7447, -75.5484),
    ("FL", "Orlando", 28.5383, -81.3792),
    ("GA", "Atlanta", 33.7490, -84.3880),
    ("HI", "Honolulu", 21.3099, -157.8581),
    ("ID", "Boise", 43.6150, -116.2023),
    ("IL", "Chicago", 41.8781, -87.6298),
    ("IN", "Indianapolis", 39.7684, -86.1581),
    ("IA", "Des Moines", 41.5868, -93.6250),
    ("KS", "Wichita", 37.6872, -97.3301),
    ("KY", "Louisville", 38.2527, -85.7585),
    ("LA", "New Orleans", 29.9511, -90.0715),
    ("ME", "Portland", 43.6591, -70.2568),
    ("MD", "Baltimore", 39.2904, -76.6122),
    ("MA", "Boston", 42.3601, -71.0589),
    ("MI", "Detroit", 42.3314, -83.0458),
    ("MN", "Minneapolis", 44.9778, -93.2650),
    ("MS", "Jackson", 32.2988, -90.1848),
    ("MO", "St. Louis", 38.6270, -90.1994),
    ("MT", "Billings", 45.7833, -108.5007),
    ("NE", "Omaha", 41.2565, -95.9345),
    ("NV", "Las Vegas", 36.1699, -115.1398),
    ("NH", "Manchester", 42.9956, -71.4548),
    ("NJ", "Newark", 40.7357, -74.1724),
    ("NM", "Albuquerque", 35.0844, -106.6504),
    ("NY", "Buffalo", 42.8864, -78.8784),
    ("NC", "Charlotte", 35.2271, -80.8431),
    ("ND", "Fargo", 46.8772, -96.7898),
    ("OH", "Columbus", 39.9612, -82.9988),
    ("OK", "Oklahoma City", 35.4676, -97.5164),
    ("OR", "Portland", 45.5152, -122.6784),
    ("PA", "Pittsburgh", 40.4406, -79.9959),
    ("RI", "Providence", 41.8240, -71.4128),
    ("SC", "Columbia", 34.0007, -81.0348),
    ("SD", "Sioux Falls", 43.5473, -96.7283),
    ("TN", "Nashville", 36.1627, -86.7816),
    ("TX", "Dallas", 32.7767, -96.7970),
    ("UT", "Salt Lake City", 40.7608, -111.8910),
    ("VT", "Burlington", 44.4759, -73.2121),
    ("VA", "Richmond", 37.5407, -77.4360),
    ("WA", "Seattle", 47.6062, -122.3321),
    ("WV", "Charleston", 38.3498, -81.6326),
    ("WI", "Milwaukee", 43.0389, -87.9065),
    ("WY", "Cheyenne", 41.1400, -104.8202),
)

def demo_rows():
    """Return exactly 100 repeatable, non-deliverable test-only records."""
    result = []
    for state, city, lat, lon in CITY_ANCHORS:
        for number in (1, 2):
            suffix = f"{state}-{number:02d}"
            # Distinct approximate positions; unrelated to invented addresses.
            delta = -0.018 if number == 1 else 0.018
            index = len(result) + 1
            status = ("inactive" if index % 17 == 0
                      else "in_training" if index % 8 == 0 else "active")
            result.append({
                "external_id": f"DEMO-{suffix}",
                "name": f"Demo Installer {suffix}",
                "email": None,
                "status": status,
                "home": {"lat": round(lat + delta, 5),
                         "lon": round(lon - delta, 5)},
                "service_radius_miles": 100.0 + (index % 3) * 25,
                "home_address": f"{100 + number} Example Way, {city}, {state} — FICTIONAL / DO NOT SHIP",
            })
    assert len(result) == 100 and len({row["external_id"] for row in result}) == 100
    return result

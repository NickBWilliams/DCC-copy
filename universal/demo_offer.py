"""Fictional, read-only route invitation preview for the private SSH tunnel.

This module cannot read company databases, issue offers or respond to real
contractors. The only access is via development + localhost Host and base URL.
"""
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import HTTPException, Request

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def require_private_demo(request: Request):
    public_base = urlparse(os.getenv("DCC_PUBLIC_URL", "http://localhost:8000"))
    if (os.getenv("DCC_ENV") != "development"
            or public_base.hostname not in LOCAL_HOSTS
            or request.url.hostname not in LOCAL_HOSTS):
        raise HTTPException(404, "Not found")


def demo_data(scenario="single"):
    now = datetime.now(timezone.utc)
    expiry = (now + timedelta(hours=48)).isoformat()
    due = (now + timedelta(days=12)).date().isoformat()
    home = {"lat": 39.1702, "lon": -121.5918}
    coords = [(39.1416, -121.6090), (39.1530, -121.6012),
              (39.1679, -121.6045), (39.1591, -121.6178),
              (39.1457, -121.6222), (39.1744, -121.5965)]
    count = 18 if scenario == "days" else (6 if scenario == "multi" else 1)
    if scenario == "days":
        coords = [(lat + block*.02, lon + block*.01) for block in range(3) for lat, lon in coords]
    stops = [
        {
            "position": n + 1,
            "venue": f"Example Store {n+1} · DEMO",
            "address": f"{100+n*100} Fictional Avenue, Yuba City, CA — NOT A REAL ADDRESS",
            "lat": coords[n][0], "lon": coords[n][1],
            "tasks": [{"task_type": "Pull Down" if n % 2 == 0 else "Install",
                       "external_task_id": f"DEMO-ONLY-TASK-{n+1:02d}"}],
        }
        for n in range(count)
    ]
    from routing import geographic_order
    if scenario != "days":
        order = geographic_order((home["lon"],home["lat"]), [(s["lon"],s["lat"]) for s in stops])
        stops = [{**stops[i], "position":pos+1} for pos,i in enumerate(order)]
    days = []
    if scenario == "days":
        for n in range(3):
            group = stops[n*6:(n+1)*6]
            order = geographic_order((home["lon"],home["lat"]), [(s["lon"],s["lat"]) for s in group])
            group = [{**group[i], "position":n*6+pos+1, "day":n+1, "day_position":pos+1} for pos,i in enumerate(order)]
            days.append({"day":n+1,"stops":6,"itinerary":group,"road_miles":47.2,
                         "driving_hours":2.1,"service_hours":4.5,"break_hours":.5,
                         "waiting_hours":0,"total_hours":7.1,"price":"180.00","return_to_start":True})
        stops = [s for d in days for s in d["itinerary"]]
    return {
        "days":days, "day_count":len(days) if days else None,
        "total_hours":21.3 if days else None,
        "demo": True,
        "scenario": scenario,
        "title": ("Demo · Northern California Service Route" if count == 1
                  else "Demo · Northern California Multi-stop Route"),
        "company_name": "Example Field Services",
        "contractor": "Demo Contractor",
        "stops": count,
        "tasks": count,
        "price": "540.00" if days else ("20.00" if count == 1 else "130.00"),
        # Purely hypothetical demo numbers, not measured road mileage.
        "road_miles": 141.6 if days else (12.4 if count == 1 else 47.2),
        "driving_hours": 6.3 if days else (0.8 if count == 1 else 2.1),
        "due_date": due,
        "expires_at": expiry,
        "home": home,
        "itinerary": stops,
        "acknowledgment_text": (
            "DEMO ONLY: I have reviewed the route and understand that "
            "this sample does not create an actual agreement or assignment."
        ),
        "acknowledgment_version": 1,
        "work_order_label": None,
        "map_tiles": {
            "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            "attribution": "© OpenStreetMap contributors",
        },
        "notice": (
            "This is a fictional example. No contractor records, account data, "
            "road-provider requests, real offers or outbound messages are used."
        ),
    }

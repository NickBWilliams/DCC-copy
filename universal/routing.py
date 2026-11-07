"""Provider-independent routing for DCC Universal.

Designed independently for the personal DCC Universal repository.
No Mapbox, Railway, or employer credentials. Public OSRM is never a
commercial-production fallback. Coordinates throughout are [longitude, latitude].
"""
import hashlib
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
HEIGIT_DIRECTIONS = "https://api.heigit.org/openrouteservice/v2/directions/driving-car"
HEIGIT_OPTIMIZATION = "https://api.heigit.org/vroom/v0"
PUBLIC_OSRM = "https://router.project-osrm.org"
MILES_PER_METER = 0.000621371
CACHE_VERSION = "dcc-universal-route-v1"

class RoutingUnavailable(Exception):
    pass

class Point(BaseModel):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)

class RoundTripInput(BaseModel):
    home: Point
    stops: list[Point] = Field(min_length=1, max_length=90)

class GeocodeInput(BaseModel):
    address: str = Field(min_length=8, max_length=300)

def utcnow():
    return datetime.now(timezone.utc)

def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

def _get_json(url, *, params=None):
    response = httpx.get(url, params=params, timeout=30, follow_redirects=False)
    response.raise_for_status()
    return response.json()

def _post_json(url, *, payload, headers=None):
    response = httpx.post(url, json=payload, headers=headers, timeout=40, follow_redirects=False)
    response.raise_for_status()
    return response.json()

def active_provider():
    # A company-controlled OSRM instance is the lowest variable-cost option.
    osrm = (os.getenv("DCC_OSRM_BASE_URL") or "").strip().rstrip("/")
    if osrm:
        if not (osrm.startswith("https://") or osrm.startswith("http://")):
            raise RoutingUnavailable("DCC_OSRM_BASE_URL must be an HTTP(S) URL")
        from urllib.parse import urlparse
        hostname = (urlparse(osrm).hostname or "").lower()
        if hostname in ("router.project-osrm.org", "routing.openstreetmap.de"):
            if os.getenv("DCC_ENV") != "development":
                raise RoutingUnavailable("Public OSRM demo is not permitted in commercial production")
        return "osrm", osrm
    key = (os.getenv("HEIGIT_API_KEY") or "").strip()
    if key:
        return "heigit", key
    if os.getenv("DCC_ENV") == "development" and os.getenv("DCC_ALLOW_PUBLIC_OSRM") == "true":
        return "osrm-demo", PUBLIC_OSRM
    raise RoutingUnavailable("Configure personal HEIGIT_API_KEY or DCC_OSRM_BASE_URL")

def _validate_location(point):
    lon, lat = point
    if not all(math.isfinite(v) for v in (lon, lat)) or (lon == 0 and lat == 0):
        raise RoutingUnavailable("Invalid route coordinates")
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise RoutingUnavailable("Coordinates outside valid bounds")

def _haversine(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 2 * math.asin(math.sqrt(min(1.0, h)))

def geographic_order(home, stops):
    """Multi-start nearest neighbor with continuous depot-to-depot 2-opt."""
    if len(stops) < 2:
        return list(range(len(stops)))
    points = [home, *stops]
    n = len(stops)
    costs = [[_haversine(a, b) for b in points] for a in points]
    def path_cost(indices):
        path = [0, *[i+1 for i in indices], 0]
        return sum(costs[a][b] for a,b in zip(path,path[1:]))
    def improve(order):
        order = list(order)
        for _ in range(30):
            best_gain, span = 0.0, None
            path = [0, *[i+1 for i in order], 0]
            for i in range(n-1):
                for j in range(i+1,n):
                    a,b,c,d = path[i],path[i+1],path[j+1],path[j+2]
                    gain = costs[a][c] + costs[b][d] - costs[a][b] - costs[c][d]
                    if gain < best_gain - 1e-12:
                        best_gain,span = gain,(i,j)
            if span is None:
                break
            a,b = span
            order[a:b+1] = reversed(order[a:b+1])
        return order
    starts = sorted(range(n),key=lambda i:(costs[0][i+1],i))
    sample = list(dict.fromkeys([starts[i*(n-1)//min(7,n-1)] for i in range(min(8,n))]))
    candidates=[]
    for first in sample:
        remaining=set(range(n))-{first}
        route=[first]
        while remaining:
            nxt=min(remaining,key=lambda i:(costs[route[-1]+1][i+1],i))
            route.append(nxt)
            remaining.remove(nxt)
        candidates.append(improve(route))
    return min(candidates,key=lambda order:(path_cost(order),order))

def _osrm_roundtrip(home, stops, base_url):
    if len(stops)+1 > 91:
        raise RoutingUnavailable("OSRM stop count exceeded")
    coords=";".join(f"{lon:.7f},{lat:.7f}" for lon,lat in [home,*stops])
    body=_get_json(f"{base_url}/trip/v1/driving/{coords}",
                    params={"source":"first","roundtrip":"true","overview":"false"})
    trips=body.get("trips") or []
    wps=body.get("waypoints") or []
    if body.get("code")!="Ok" or len(trips)!=1 or len(wps)!=len(stops)+1:
        raise RoutingUnavailable("OSRM failed to return a full round trip")
    indices=[wp.get("waypoint_index") for wp in wps]
    if any(type(i) is not int for i in indices) or sorted(indices)!=list(range(len(indices))):
        raise RoutingUnavailable("OSRM omitted or duplicated a stop")
    if indices[0] != 0:
        raise RoutingUnavailable("OSRM did not keep the depot first")
    order=sorted(range(len(stops)),key=lambda i:indices[i+1])
    trip=trips[0]
    return _complete_result(trip["distance"],trip["duration"],order,"osrm")

def _heigit_optimize(home,stops,key):
    data={
        "jobs":[{"id":i+1,"location":list(stop)} for i,stop in enumerate(stops)],
        "vehicles":[{"id":1,"profile":"driving-car","start":list(home),"end":list(home)}],
    }
    body=_post_json(HEIGIT_OPTIMIZATION,payload=data,
                    headers={"Authorization":key,"Content-Type":"application/json"})
    routes=body.get("routes") or []
    if body.get("code") not in (None,0) or body.get("unassigned") or len(routes)!=1:
        raise RoutingUnavailable("HEIGIT optimization did not assign every stop")
    order=[step["job"]-1 for step in routes[0].get("steps",[]) if step.get("type")=="job"]
    if sorted(order)!=list(range(len(stops))):
        raise RoutingUnavailable("HEIGIT optimization omitted or duplicated a stop")
    return order

def _heigit_roundtrip(home,stops,key):
    if len(stops)<=48:
        order=_heigit_optimize(home,stops,key)
    else:
        # Over provider job limit: deterministic round-trip heuristic, then
        # actual road network measurement for every leg. No fake road miles.
        order=geographic_order(home,stops)
    points=[home,*[stops[i] for i in order],home]
    distance=duration=0.0
    # Inclusive 50-coordinate chunks; each next chunk shares the prior endpoint.
    for i in range(0,len(points)-1,49):
        chunk=points[i:i+50]
        body=_post_json(HEIGIT_DIRECTIONS,
                        payload={"coordinates":[list(p) for p in chunk],"instructions":False},
                        headers={"Authorization":key,"Content-Type":"application/json"})
        try:
            summary=body["routes"][0]["summary"]
            distance+=float(summary["distance"])
            duration+=float(summary["duration"])
        except (KeyError,IndexError,TypeError,ValueError) as exc:
            raise RoutingUnavailable("HEIGIT directions were incomplete") from exc
    return _complete_result(distance,duration,order,"heigit")

def _complete_result(distance_m,duration_s,order,provider):
    try:
        distance_m=float(distance_m)
        duration_s=float(duration_s)
    except (TypeError,ValueError) as exc:
        raise RoutingUnavailable("Routing returned invalid distance") from exc
    if not (math.isfinite(distance_m) and math.isfinite(duration_s) and
            distance_m>=0 and duration_s>=0):
        raise RoutingUnavailable("Routing returned nonfinite road values")
    return {"road_miles":round(distance_m*MILES_PER_METER,2),
            "driving_hours":round(duration_s/3600,2),"stop_order":order,
            "provider":provider}

def roundtrip(home,stops):
    _validate_location(home)
    for stop in stops:
        _validate_location(stop)
    provider,credential=active_provider()
    try:
        if provider in ("osrm","osrm-demo"):
            return _osrm_roundtrip(home,stops,credential)
        return _heigit_roundtrip(home,stops,credential)
    except RoutingUnavailable:
        raise
    except (httpx.HTTPError,KeyError,TypeError,ValueError,IndexError) as exc:
        raise RoutingUnavailable(f"{provider} routing request failed") from exc

def census_geocode(address):
    """Strict Census street-address lookup; never substitute a ZIP centroid."""
    body=_get_json(CENSUS_URL,params={
        "address":address,"benchmark":"Public_AR_Current","format":"json"})
    matches=((body.get("result") or {}).get("addressMatches") or [])
    requested_state=re.search(r"\b([A-Z]{2})\s+\d{5}(?:-\d{4})?\b",address.upper())
    requested_zip=re.search(r"\b(\d{5})(?:-\d{4})?\b",address)
    for match in matches:
        try:
            coords=match["coordinates"]
            point=[float(coords["x"]),float(coords["y"])]
            comps=match.get("addressComponents") or {}
            state=str(comps.get("state") or "").upper()
            zipcode=str(comps.get("zip") or "")[:5]
            if requested_state and state and state!=requested_state.group(1):
                continue
            if requested_zip and zipcode and zipcode!=requested_zip.group(1):
                continue
            _validate_location(point)
            return {"longitude":point[0],"latitude":point[1],
                    "source":"us-census-address"}
        except (KeyError,ValueError,TypeError,RoutingUnavailable):
            continue
    raise RoutingUnavailable("US Census could not resolve the street address")

def install(app,Base,engine,require_staff):
    class CachedRouting(Base):
        __tablename__ = "dcc_routing_cache"
        id: Mapped[int] = mapped_column(primary_key=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        fingerprint: Mapped[str] = mapped_column(String(64),unique=True,index=True)
        value: Mapped[str] = mapped_column(Text)
        expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def cached_result(org,fingerprint):
        with Session(engine) as db:
            row=db.scalar(select(CachedRouting).where(
                CachedRouting.organization==org,
                CachedRouting.fingerprint==fingerprint))
            if row is not None and aware(row.expires_at)>utcnow():
                return json.loads(row.value)
        return None

    def save_result(org,fingerprint,data,days=1):
        try:
            with Session(engine) as db:
                row=db.scalar(select(CachedRouting).where(
                    CachedRouting.organization==org,CachedRouting.fingerprint==fingerprint))
                if row is None:
                    row=CachedRouting(organization=org,fingerprint=fingerprint,
                                      value="",expires_at=utcnow())
                    db.add(row)
                row.value=json.dumps(data,separators=(",",":"))
                row.expires_at=utcnow()+timedelta(days=days)
                db.commit()
        except Exception:
            # Cache failure must not make a valid route calculation fail.
            pass

    def fingerprint(*args):
        return hashlib.sha256(json.dumps(args,sort_keys=True,default=str).encode()).hexdigest()

    @app.get("/api/routing/status")
    def status(staff=Depends(require_staff)):
        try:
            provider,_=active_provider()
            return {"ready":True,"provider":provider}
        except RoutingUnavailable:
            return {"ready":False,"provider":None}

    @app.post("/api/routing/roundtrip")
    def route(data:RoundTripInput,staff=Depends(require_staff)):
        home=(data.home.lon,data.home.lat)
        stops=[(p.lon,p.lat) for p in data.stops]
        try:
            provider,_=active_provider()
        except RoutingUnavailable as exc:
            raise HTTPException(503,str(exc))
        cache_key=fingerprint(CACHE_VERSION,staff.organization,provider,os.getenv("DCC_OSRM_BASE_URL",""),home,stops)
        result=cached_result(staff.organization,cache_key)
        if result is not None:
            return {**result,"cached":True}
        try:
            result=roundtrip(home,stops)
        except RoutingUnavailable as exc:
            raise HTTPException(502,str(exc))
        save_result(staff.organization,cache_key,result)
        return {**result,"cached":False}

    @app.post("/api/routing/geocode")
    def geocode(data:GeocodeInput,staff=Depends(require_staff)):
        address=" ".join(data.address.strip().split())
        cache_key=fingerprint("census-geocode-v1",staff.organization,address.casefold())
        result=cached_result(staff.organization,cache_key)
        if result is not None:
            return {**result,"cached":True}
        try:
            result=census_geocode(address)
        except RoutingUnavailable as exc:
            raise HTTPException(422,str(exc))
        except (httpx.HTTPError,KeyError,TypeError,ValueError) as exc:
            raise HTTPException(502,"US Census geocoder unavailable") from exc
        save_result(staff.organization,cache_key,result,days=30)
        return {**result,"cached":False}

"""Fast, read-only nationwide bulk dispatch preflight.

Previews geographically group physical locations, match only contractors
belonging to the signed-in company, and identify already-reserved work.

IMPORTANT: aerial mileage in this module is ONLY a geographic screening
heuristic. Road mileage, time and pay must be computed by the existing
authoritative /api/dispatch/routes endpoint before any route is reserved.
No offers, emails, messages or assignments are sent from this module.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import math
import os
from typing import Callable

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from demo_contractors import CITY_ANCHORS
from dispatch import TaskInput, task_ref
from contractors import miles_between

MAX_BULK_TASKS = 2000
MAX_BULK_ROUTES = 500


class BulkPreviewRequest(BaseModel):
    tasks: list[TaskInput] = Field(min_length=1, max_length=MAX_BULK_TASKS)
    max_stops_per_route: int = Field(default=6, ge=1, le=30)
    include_training: bool = True
    include_demo: bool = True


def safe_distance(a, b):
    return miles_between(a["lon"], a["lat"], b["lon"], b["lat"])


def sample_tasks():
    """300 independent fictional tasks covering every state; no real clients."""
    tasks = []
    offsets = ((.012, -.017), (.022, .011), (-.013, .019),
               (-.018, -.015), (.034, -.003), (.002, .031))
    for state, city, latitude, longitude in CITY_ANCHORS:
        for i, (dy, dx) in enumerate(offsets, 1):
            tasks.append({
                "external_task_id": f"DEMO-BULK-{state}-{i:02d}",
                "venue": f"DEMO {city} Stop {i}",
                "address": f"Fictional Sample Site {i}, {city}, {state} (DO NOT DISPATCH)",
                "lat": round(latitude + dy, 5),
                "lon": round(longitude + dx, 5),
                "task_type": "Demo service",
            })
    assert len(tasks) == 300
    return tasks


def generate_preview(
    tasks: list[TaskInput],
    candidates: list[dict],
    reserved: set[str],
    max_stops_per_route: int,
):
    """Pure, deterministic clustering; no SQL writes or routing API calls."""
    refs = [task_ref(t.external_task_id) for t in tasks]
    if len(set(refs)) != len(refs):
        raise HTTPException(422, "Duplicate task IDs in the CSV; each must appear once")
    for t in tasks:
        if not (math.isfinite(t.lat) and math.isfinite(t.lon)):
            raise HTTPException(422, "Task coordinates must be finite")
        if t.lat == 0 and t.lon == 0:
            raise HTTPException(422, "Zero/zero is not a usable route location")
        if not t.venue.strip() or not t.address.strip():
            raise HTTPException(422, "Venue and address must not be blank")
    # Never mix already-claimed physical tasks into a new route. Each excluded
    # task has an explicit reason in the response.
    blocked = []
    grouped: dict[tuple, dict] = {}
    for task in tasks:
        if task_ref(task.external_task_id) in reserved:
            blocked.append({
                "external_task_id": task.external_task_id,
                "venue": task.venue,
                "reason": "Already reserved by a route in this company",
            })
            continue
        key = (task.address.strip().casefold(), round(task.lat, 6), round(task.lon, 6))
        if key not in grouped:
            grouped[key] = {"home": {"lat": task.lat, "lon": task.lon},
                            "venue": task.venue, "tasks": []}
        grouped[key]["tasks"].append(task)
    # A physical stop must stay intact even if it has several task line items.
    for stop in grouped.values():
        if len(stop["tasks"]) > 90:
            raise HTTPException(422, "More than 90 tasks exist at one physical stop")
    filtered = []
    for c in candidates:
        home = c.get("home") or {}
        lat, lon = home.get("lat"), home.get("lon")
        if not isinstance(lat, (float,int)) or not isinstance(lon, (float,int)):
            continue
        if not (math.isfinite(lat) and math.isfinite(lon)) or (lat == 0 and lon == 0):
            continue
        if c.get("status") not in ("active", "in_training"):
            continue
        filtered.append(c)
    filtered.sort(key=lambda c: c["external_id"].casefold())
    by_contractor: dict[str, list] = defaultdict(list)
    selected = {}
    unmatched = []
    for stop in sorted(grouped.values(), key=lambda s: (s["home"]["lat"], s["home"]["lon"], s["venue"])):
        possibilities = []
        for worker in filtered:
            distance = safe_distance(worker["home"], stop["home"])
            if distance <= worker["service_radius_miles"]:
                # Small occupancy penalty spreads work without sending jobs
                # hundreds of miles farther than a close contractor.
                current = len(by_contractor[worker["external_id"]])
                score = distance + min(current, 30) * 0.65
                possibilities.append((score, distance, worker["external_id"], worker))
        if not possibilities:
            for t in stop["tasks"]:
                unmatched.append({
                    "external_task_id": t.external_task_id,
                    "venue": t.venue,
                    "reason": "No active contractor within their declared aerial service radius",
                })
            continue
        _, _, contractor_ref, contractor = min(possibilities, key=lambda p: p[:3])
        by_contractor[contractor_ref].append(stop)
        selected[contractor_ref] = contractor
    drafts = []
    for ref in sorted(by_contractor):
        contractor = selected[ref]
        unplanned = list(by_contractor[ref])
        home = contractor["home"]
        while unplanned:
            location = home
            route_stops = []
            route_task_count = 0
            while unplanned and len(route_stops) < max_stops_per_route:
                possibilities = [
                    (safe_distance(location, stop["home"]), index)
                    for index, stop in enumerate(unplanned)
                    if route_task_count + len(stop["tasks"]) <= 90
                ]
                if not possibilities:
                    break
                _, index = min(possibilities)
                stop = unplanned.pop(index)
                route_stops.append(stop)
                location = stop["home"]
                route_task_count += len(stop["tasks"])
            if not route_stops:
                raise HTTPException(422, "A route would exceed the 90-task safety limit")
            all_tasks = [task.model_dump(mode="json")
                         for stop in route_stops for task in stop["tasks"]]
            center = {
                "lat": sum(stop["home"]["lat"] for stop in route_stops) / len(route_stops),
                "lon": sum(stop["home"]["lon"] for stop in route_stops) / len(route_stops),
            }
            distance = round(safe_distance(home, center), 2)
            digest = sha256((",".join(sorted(t["external_task_id"].casefold()
                        for t in all_tasks))).encode()).hexdigest()[:12]
            index = len(drafts) + 1
            drafts.append({
                "draft_id": f"BULK-{digest}",
                "title": f"Batch {index:03d} · {contractor['name']}"[:200],
                "contractor_external_id": contractor["external_id"],
                "contractor": contractor["name"],
                "contractor_source": contractor.get("source", "manual"),
                "is_demo": bool(contractor.get("is_demo", False)),
                "home": home,
                "stop_count": len(route_stops),
                "task_count": len(all_tasks),
                "aerial_to_cluster_miles": distance,
                "tasks": all_tasks,
                "road_miles": None,
                "driving_hours": None,
                "price": None,
                "status": "preview_only",
            })
            if len(drafts) > MAX_BULK_ROUTES:
                raise HTTPException(422, "Preview would create over 500 routes; split the workload")
    # Never invent a price or present geographic distance as road mileage.
    drafts.sort(key=lambda r: (r["contractor"].casefold(), r["draft_id"]))
    total_matched = sum(r["task_count"] for r in drafts)
    return {
        "mode": "read_only_aerial_preflight",
        "input_tasks": len(tasks),
        "physical_stops": len(grouped),
        "eligible_contractors": len(filtered),
        "proposed_routes": len(drafts),
        "matched_tasks": total_matched,
        "unmatched_tasks": len(unmatched),
        "reserved_tasks": len(blocked),
        "unmatched": unmatched,
        "reserved": blocked,
        "routes": drafts,
        "road_distance_available": False,
        "pricing_calculated": False,
        "offers_sent": 0,
        "notice": (
            "This is geographic grouping only. Road miles, driving time, "
            "pay and task reservations are NOT computed or saved. "
            "Each route must be calculated by the configured road provider "
            "and explicitly approved before dispatch."
        ),
    }


def install(app, require_staff, roster_candidates: Callable, reserved_refs: Callable):
    @app.get("/api/dispatch/bulk/demo")
    def demo_work(staff=Depends(require_staff)):
        if os.getenv("DCC_ENV") != "development":
            raise HTTPException(403, "Fictional nationwide demo is available only in private development")
        return {
            "tasks": sample_tasks(),
            "notice": "300 FICTIONAL locations nationwide; not verified addresses or real client work",
        }

    @app.post("/api/dispatch/bulk/preview")
    def bulk_preview(data: BulkPreviewRequest, staff=Depends(require_staff)):
        if data.include_demo and os.getenv("DCC_ENV") != "development":
            raise HTTPException(403, "Demo contractors cannot be used for production planning")
        candidates = roster_candidates(
            staff.organization,
            include_training=data.include_training,
            include_demo=data.include_demo and os.getenv("DCC_ENV") == "development",
        )
        already_claimed = reserved_refs(staff.organization, [task.external_task_id for task in data.tasks])
        return generate_preview(data.tasks, candidates, already_claimed, data.max_stops_per_route)

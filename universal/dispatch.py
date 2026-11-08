"""Independent dispatch planning: physical stops, task claims, and pricing.

Does not import employer systems. All company data is scoped to the signed-in
staff organization. A route is planned using actual road-network measurements.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from routing import Point, RoutingUnavailable, roundtrip

def now():
    return datetime.now(timezone.utc)

class TaskInput(BaseModel):
    external_task_id: str = Field(min_length=1, max_length=128)
    venue: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=5, max_length=400)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    task_type: str = Field(default="Service", min_length=1, max_length=80)

class PricingPolicy(BaseModel):
    base_fee: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)
    per_stop: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)
    per_mile: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)
    per_hour: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)
    minimum: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)

class PlanInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    contractor: str = Field(min_length=1, max_length=200)
    home: Point
    tasks: list[TaskInput] = Field(min_length=1, max_length=90)
    pricing: PricingPolicy = Field(default_factory=PricingPolicy)

def task_ref(value):
    result = str(value).strip().casefold()
    if not result:
        raise HTTPException(422, "A nonblank task ID is required")
    return result

def plan(data):
    """Calculate the route; do not reserve or persist work in this function."""
    refs = [task_ref(task.external_task_id) for task in data.tasks]
    if len(set(refs)) != len(refs):
        raise HTTPException(422, "Each task ID may appear only once in a route")
    if not data.title.strip() or not data.contractor.strip():
        raise HTTPException(422, "Route and contractor names cannot be blank")
    physical = []
    by_location = {}
    for t in data.tasks:
        if not t.venue.strip() or not t.address.strip() or not t.task_type.strip():
            raise HTTPException(422, "Venue, address and task type cannot be blank")
        # Merge multiple campaign tasks at a single physical address.
        key = (t.address.strip().casefold(), round(t.lon, 6), round(t.lat, 6))
        if key not in by_location:
            by_location[key] = len(physical)
            physical.append({"venue":t.venue.strip(), "address":t.address.strip(),
                             "lat":t.lat, "lon":t.lon, "tasks":[]})
        physical[by_location[key]]["tasks"].append({
            "external_task_id":t.external_task_id.strip(),
            "task_type":t.task_type.strip(),
            "venue":t.venue.strip(),
        })
    try:
        result = roundtrip((data.home.lon, data.home.lat),
                           [(p["lon"],p["lat"]) for p in physical])
    except RoutingUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
    indices = result.get("stop_order")
    if not isinstance(indices, list) or sorted(indices) != list(range(len(physical))):
        raise HTTPException(502, "Routing provider omitted or repeated a physical stop")
    distance = Decimal(str(result["road_miles"]))
    hours = Decimal(str(result["driving_hours"]))
    if distance < 0 or hours < 0:
        raise HTTPException(502, "Routing provider returned invalid totals")
    policy = data.pricing
    raw_price = (policy.base_fee
                 + Decimal(len(physical)) * policy.per_stop
                 + distance * policy.per_mile
                 + hours * policy.per_hour)
    price = max(policy.minimum, raw_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if price > Decimal("9999999999.99"):
        raise HTTPException(422, "Calculated amount exceeds supported limit")
    itinerary = [{**physical[i], "position":position+1}
                 for position,i in enumerate(indices)]
    return {
        "title":data.title.strip(),"contractor":data.contractor.strip(),
        "home":{"lon":data.home.lon,"lat":data.home.lat},
        "stops":len(physical),"tasks":len(data.tasks),
        "road_miles":result["road_miles"],
        "driving_hours":result["driving_hours"],
        "provider":result["provider"],
        "optimization":result.get("optimization","provider"),
        "price":str(price),
        "price_breakdown":{
            "base_fee":str(policy.base_fee),
            "per_stop":str(policy.per_stop),
            "per_mile":str(policy.per_mile),
            "per_hour":str(policy.per_hour),
            "minimum":str(policy.minimum),
            "raw_total":str(raw_price.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)),
        },
        "itinerary":itinerary,
    }

def install(app, Base, Route, engine, require_staff, record, serialize, database_url):
    class RoutePlan(Base):
        __tablename__ = "dcc_dispatch_route_plans"
        id: Mapped[int] = mapped_column(primary_key=True)
        route_id: Mapped[int] = mapped_column(ForeignKey("universal_routes.id"), unique=True, index=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        plan_json: Mapped[str] = mapped_column(Text)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    class RouteTask(Base):
        __tablename__ = "dcc_dispatch_route_tasks"
        __table_args__ = (UniqueConstraint("route_id","task_ref"),)
        id: Mapped[int] = mapped_column(primary_key=True)
        route_id: Mapped[int] = mapped_column(ForeignKey("universal_routes.id"), index=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        task_ref: Mapped[str] = mapped_column(String(128))
        external_task_id: Mapped[str] = mapped_column(String(128))
        stop_position: Mapped[int] = mapped_column(Integer)
        venue: Mapped[str] = mapped_column(String(200))
        address: Mapped[str] = mapped_column(String(400))
        task_type: Mapped[str] = mapped_column(String(80))
        completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    class TaskClaim(Base):
        __tablename__ = "dcc_dispatch_task_claims"
        __table_args__ = (UniqueConstraint("organization","task_ref"),)
        id: Mapped[int] = mapped_column(primary_key=True)
        organization: Mapped[str] = mapped_column(String(80), index=True)
        task_ref: Mapped[str] = mapped_column(String(128))
        route_id: Mapped[int] = mapped_column(ForeignKey("universal_routes.id"), index=True)

    class CancelRequest(BaseModel):
        expected_version: int = Field(ge=1)

    def details(db, route, public=False):
        snapshot = db.scalar(select(RoutePlan).where(
            RoutePlan.route_id == route.id,
            RoutePlan.organization == route.organization))
        if snapshot is None:
            return None
        value = json.loads(snapshot.plan_json)
        value["route_id"] = route.id
        value["status"] = route.status
        value["version"] = route.version
        value["price"] = str(route.price)
        value["proposed_price"] = str(route.proposed_price) if route.proposed_price is not None else None
        if public:
            # Contractor pages show only the accepted route details; no
            # organization identity, internal task claim state or pricing rules.
            return {
                "itinerary":value["itinerary"],
                "road_miles":value["road_miles"],
                "driving_hours":value["driving_hours"],
            }
        return value

    @app.post("/api/dispatch/quote")
    def quote(data: PlanInput, staff=Depends(require_staff)):
        return plan(data)

    @app.post("/api/dispatch/routes", status_code=201)
    def create_planned_route(data: PlanInput, staff=Depends(require_staff)):
        calculated = plan(data)
        with Session(engine) as db:
            existing = list(db.scalars(select(TaskClaim).where(
                TaskClaim.organization == staff.organization,
                TaskClaim.task_ref.in_([task_ref(t.external_task_id) for t in data.tasks]))))
            if existing:
                raise HTTPException(409, "One or more tasks are already reserved by another route")
            route = Route(
                organization=staff.organization,title=calculated["title"],
                contractor=calculated["contractor"],
                stops=calculated["stops"],tasks=calculated["tasks"],
                price=Decimal(calculated["price"]),status="ready")
            db.add(route)
            db.flush()
            db.add(RoutePlan(route_id=route.id,organization=staff.organization,
                             plan_json=json.dumps(calculated,separators=(",",":"))))
            for stop in calculated["itinerary"]:
                for task in stop["tasks"]:
                    ref=task_ref(task["external_task_id"])
                    db.add(RouteTask(
                        route_id=route.id, organization=staff.organization,
                        task_ref=ref, external_task_id=task["external_task_id"],
                        stop_position=stop["position"],venue=task["venue"],
                        address=stop["address"],task_type=task["task_type"]))
                    db.add(TaskClaim(
                        organization=staff.organization,task_ref=ref,route_id=route.id))
            record(db,route,"route_planned",
                   f'stops={route.stops} tasks={route.tasks} miles={calculated["road_miles"]}')
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise HTTPException(409, "A task was reserved by a concurrent request") from exc
            db.refresh(route)
            return {"route":serialize(route),"plan":details(db,route)}

    @app.get("/api/dispatch/routes/{route_id}")
    def get_planned_route(route_id:int,staff=Depends(require_staff)):
        with Session(engine) as db:
            route = db.scalar(select(Route).where(
                Route.id == route_id, Route.organization == staff.organization))
            if route is None:
                raise HTTPException(404, "Route not found")
            value=details(db,route)
            if value is None:
                raise HTTPException(404, "No itinerary recorded for this route")
            return value

    @app.post("/api/dispatch/routes/{route_id}/cancel")
    def cancel_planned_route(route_id:int,data:CancelRequest,staff=Depends(require_staff)):
        with Session(engine) as db:
            q=select(Route).where(Route.id==route_id,Route.organization==staff.organization)
            if not database_url.startswith("sqlite"):
                q=q.with_for_update()
            route=db.scalar(q)
            if route is None:
                raise HTTPException(404, "Route not found")
            if route.version != data.expected_version:
                raise HTTPException(409, "Stale route version; refresh before cancelling")
            if route.status not in ("unassigned","ready","flagged","declined"):
                raise HTTPException(409, "Revoke an open offer first; accepted or completed routes cannot be cancelled")
            db.query(TaskClaim).filter(
                TaskClaim.route_id==route.id,
                TaskClaim.organization==staff.organization).delete(synchronize_session=False)
            route.status="cancelled"
            route.version+=1
            record(db,route,"route_cancelled","Active task reservations released")
            db.commit()
            db.refresh(route)
            return serialize(route)

    def reserved_task_refs(organization, refs):
        """Find already-claimed tasks in this tenant without exposing route IDs."""
        result = set()
        if not refs:
            return result
        normalized = [task_ref(ref) for ref in refs]
        with Session(engine) as db:
            for start in range(0, len(normalized), 400):
                chunk = normalized[start:start + 400]
                query = select(TaskClaim.task_ref).where(
                    TaskClaim.organization == organization, TaskClaim.task_ref.in_(chunk))
                result.update(db.scalars(query).all())
        return result

    return details, reserved_task_refs

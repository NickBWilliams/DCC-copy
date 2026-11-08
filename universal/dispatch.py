"""Independent dispatch planning: physical stops, task claims, and pricing.

Does not import employer systems. All company data is scoped to the signed-in
staff organization. A route is planned using actual road-network measurements.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from routing import Point, RoutingUnavailable, road_matrix
from day_planning import WorkdayPolicy, build_days

def now():
    return datetime.now(timezone.utc)

class TaskInput(BaseModel):
    external_task_id: str = Field(min_length=1, max_length=128)
    venue: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=5, max_length=400)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    task_type: str = Field(default="Service", min_length=1, max_length=80)

    service_minutes: float | None = Field(default=None, gt=0, le=600)
    window_start_minute: int | None = Field(default=None, ge=0, le=1439)
    window_end_minute: int | None = Field(default=None, ge=1, le=1440)

    @model_validator(mode="after")
    def valid_window(self):
        if (self.window_start_minute is not None and self.window_end_minute is not None
                and self.window_start_minute >= self.window_end_minute):
            raise ValueError("Venue opening must precede closing")
        return self

class PricingPolicy(BaseModel):
    hourly_basis: Literal["driving", "total_work"] = "driving"
    base_fee: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)
    per_stop: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)
    per_mile: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)
    per_hour: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)
    per_service_hour: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)
    minimum: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)

class PlanInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    contractor: str = Field(min_length=1, max_length=200)
    home: Point
    tasks: list[TaskInput] = Field(min_length=1, max_length=90)
    pricing: PricingPolicy | None = None
    workday: WorkdayPolicy | None = None
    offer_total: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)

class CompanyPlanningInput(BaseModel):
    pricing: PricingPolicy = Field(default_factory=PricingPolicy)
    workday: WorkdayPolicy = Field(default_factory=WorkdayPolicy)
    version: int = Field(default=0, ge=0)

def task_ref(value):
    result = str(value).strip().casefold()
    if not result:
        raise HTTPException(422, "A nonblank task ID is required")
    return result

def plan(data):
    """Calculate the route; do not reserve or persist work in this function."""
    data = data.model_copy(update={"pricing":data.pricing or PricingPolicy(), "workday":data.workday or WorkdayPolicy()})
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
                             "lat":t.lat, "lon":t.lon, "tasks":[], "service_minutes":0,
                             "window_start_minute":None, "window_end_minute":None})
        stop = physical[by_location[key]]
        stop["service_minutes"] += t.service_minutes if t.service_minutes is not None else data.workday.default_service_minutes
        if t.window_start_minute is not None:
            stop["window_start_minute"] = max(stop["window_start_minute"] or 0, t.window_start_minute)
        if t.window_end_minute is not None:
            stop["window_end_minute"] = min(stop["window_end_minute"] or 1440, t.window_end_minute)
        physical[by_location[key]]["tasks"].append({
            "external_task_id":t.external_task_id.strip(),
            "task_type":t.task_type.strip(),
            "venue":t.venue.strip(),
        })
    try:
        matrix = road_matrix((data.home.lon, data.home.lat), [(p["lon"], p["lat"]) for p in physical])
        days = build_days(physical, matrix, data.workday)
    except RoutingUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
    policy = data.pricing
    itinerary = []
    for day in days:
        day_stops = []
        for local_position, index in enumerate(day.pop("stop_order"), 1):
            stop = {**physical[index], "position":len(itinerary) + 1,
                    "day":day["day"], "day_position":local_position}
            itinerary.append(stop)
            day_stops.append(stop)
        raw = (policy.base_fee + Decimal(len(day_stops)) * policy.per_stop
               + Decimal(str(day["road_miles"])) * policy.per_mile
               + Decimal(str(day["total_hours"] if policy.hourly_basis == "total_work" else day["driving_hours"])) * policy.per_hour
               + Decimal(str(day["service_hours"])) * policy.per_service_hour)
        day["price"] = str(max(policy.minimum, raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        day["itinerary"] = day_stops
        day["stops"] = len(day_stops)
        day["return_to_start"] = True
    price = sum(Decimal(day["price"]) for day in days)
    if price > Decimal("9999999999.99"):
        raise HTTPException(422, "Calculated amount exceeds supported limit")
    calculated_price = price
    if data.offer_total is not None:
        price = data.offer_total
        # Explicit route override apportioned across days; last-day remainder
        # guarantees the displayed daily amounts sum to the agreed offer.
        remaining = price
        for index, day in enumerate(days):
            share = (price * Decimal(day["price"]) / calculated_price if calculated_price
                     else price / Decimal(len(days)))
            allocated = min(remaining, share.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if index < len(days)-1 else remaining
            day["price"] = str(allocated)
            remaining -= allocated
    warnings = []
    if any(t.service_minutes is None for t in data.tasks):
        warnings.append("Some onsite times use the configured default per task; verify before offering.")
    if any(d["above_target"] for d in days):
        warnings.append("A daily route exceeds the target but remains within the configured maximum.")
    warnings.append("Daily estimates are not dated appointments. Confirm venue hours and contractor availability before sending.")
    return {
        "title":data.title.strip(), "contractor":data.contractor.strip(),
        "home":{"lon":data.home.lon,"lat":data.home.lat},
        "stops":len(physical), "tasks":len(data.tasks),
        **{key:round(sum(day[key] for day in days), 4) for key in
           ("road_miles", "driving_hours", "service_hours", "break_hours", "waiting_hours", "total_hours")},
        "provider":matrix["provider"], "optimization":"directed_road_daily_insertion_2opt",
        "price":str(price), "days":days, "day_count":len(days),
        "workday":data.workday.model_dump(), "warnings":warnings,
        "price_breakdown":{**{k:str(v) for k,v in policy.model_dump().items()},
                           "base_and_minimum_scope":"per_day", "calculated_total":str(calculated_price),
                           "offer_total_override":str(data.offer_total) if data.offer_total is not None else None, "raw_total":str(price)},
        "itinerary":itinerary,
    }

def install(app, Base, Route, engine, require_staff, record, serialize, database_url):
    class CompanyPlanning(Base):
        __tablename__ = "dcc_company_planning_settings"
        organization: Mapped[str] = mapped_column(String(80), primary_key=True)
        settings_json: Mapped[str] = mapped_column(Text)
        version: Mapped[int] = mapped_column(Integer, default=1)

    def company_defaults(organization):
        with Session(engine) as db:
            row = db.get(CompanyPlanning, organization)
            return ({**json.loads(row.settings_json), "version":row.version} if row
                    else CompanyPlanningInput().model_dump(mode="json"))

    def with_defaults(data, organization):
        settings = CompanyPlanningInput(**company_defaults(organization))
        return data.model_copy(update={"pricing":data.pricing or settings.pricing,
                                       "workday":data.workday or settings.workday})

    @app.get("/api/planning-settings")
    def get_planning_settings(staff=Depends(require_staff)):
        return company_defaults(staff.organization)

    @app.put("/api/planning-settings")
    def put_planning_settings(data: CompanyPlanningInput, staff=Depends(require_staff)):
        if staff.role not in ("owner", "admin"):
            raise HTTPException(403, "Company owner/admin access required")
        with Session(engine) as db:
            query = select(CompanyPlanning).where(CompanyPlanning.organization == staff.organization)
            if not database_url.startswith("sqlite"):
                query = query.with_for_update()
            row = db.scalar(query)
            if (row.version if row else 0) != data.version:
                raise HTTPException(409, "Company planning settings changed; reload defaults")
            if row is None:
                row = CompanyPlanning(organization=staff.organization, settings_json="{}", version=0)
                db.add(row)
            row.settings_json = data.model_dump_json(exclude={"version"})
            row.version += 1
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise HTTPException(409, "Company planning settings changed; reload defaults") from exc
        return company_defaults(staff.organization)

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
            # A negotiated total replaces the old offer; don't present stale
            # daily amounts as the newly agreed allocation.
            days = value.get("days", [])
            if days and sum(Decimal(d["price"]) for d in days) != route.price:
                days = [{k:v for k,v in d.items() if k != "price"} for d in days]
            # Contractor pages show only the accepted route details; no
            # organization identity, internal task claim state or pricing rules.
            return {
                "itinerary":value["itinerary"],
                "home": value.get("home"),
                "road_miles":value["road_miles"],
                "driving_hours":value["driving_hours"],
                "days":days,
                "day_count":value.get("day_count"),
                **{key:value.get(key) for key in ("service_hours", "break_hours", "waiting_hours", "total_hours")},
            }
        return value

    @app.post("/api/dispatch/quote")
    def quote(data: PlanInput, staff=Depends(require_staff)):
        return plan(with_defaults(data, staff.organization))

    @app.post("/api/dispatch/routes", status_code=201)
    def create_planned_route(data: PlanInput, staff=Depends(require_staff)):
        calculated = plan(with_defaults(data, staff.organization))
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

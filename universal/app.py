"""Independent DCC Universal MVP. No employer credentials or integrations."""
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

DATABASE_URL = os.getenv("DCC_DATABASE_URL", "sqlite:///./dcc_universal.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}, pool_pre_ping=True)

class Base(DeclarativeBase):
    pass

class Route(Base):
    __tablename__ = "universal_routes"
    id: Mapped[int] = mapped_column(primary_key=True)
    organization: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(200))
    contractor: Mapped[str] = mapped_column(String(200), default="Unassigned")
    stops: Mapped[int] = mapped_column(Integer, default=0)
    tasks: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    proposed_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="unassigned")
    version: Mapped[int] = mapped_column(Integer, default=1)

class Event(Base):
    __tablename__ = "universal_route_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("universal_routes.id"))
    organization: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str] = mapped_column(String(500), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

app = FastAPI(title="DCC Universal", version="0.2.0")
app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "assets"), name="assets")
from staff_auth import install as install_staff_auth
bootstrap_staff, require_staff = install_staff_auth(app, Base, engine)

@app.middleware("http")
async def security_headers(request: Request, call_next):
    # JSON-only mutating endpoints plus same-origin protection for browser sessions.
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlparse
            if urlparse(origin).netloc.lower() != request.headers.get("host", "").lower():
                return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
    response = await call_next(request)
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith(("/api/", "/offer/")):
        response.headers["Cache-Control"] = "no-store"
    return response


class NewRoute(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    contractor: str = Field(default="Unassigned", max_length=200)
    stops: int = Field(ge=0, le=10000)
    tasks: int = Field(ge=0, le=100000)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)

class Transition(BaseModel):
    action: Literal["prepare", "flag", "approve_counter", "reject_counter", "finalize", "complete", "requeue"]
    amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    expected_version: int = Field(ge=1)

TRANSITIONS = {
    "prepare": ({"unassigned", "declined"}, "ready"),
    "flag": ({"unassigned", "ready"}, "flagged"),
    # Contractor responses require a unique offer link, never staff impersonation.
    # Issuing an offer is handled separately so a real revocable link exists.
    "approve_counter": ({"countered"}, "ready"),
    "reject_counter": ({"countered"}, "declined"),
    "finalize": ({"accepted"}, "finalized"),
    "complete": ({"finalized"}, "completed"),
    "requeue": ({"flagged", "declined"}, "unassigned"),
}

def serialize(route: Route):
    return dict(id=route.id, organization=route.organization, title=route.title, contractor=route.contractor, stops=route.stops, tasks=route.tasks, done=route.done, price=str(route.price), proposed_price=str(route.proposed_price) if route.proposed_price is not None else None, status=route.status, version=route.version)

def record(db: Session, route: Route, action: str, detail: str = ""):
    db.add(Event(route_id=route.id, organization=route.organization, action=action, detail=detail))

@app.on_event("startup")
def initialize():
    Base.metadata.create_all(engine)
    bootstrap_staff()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/api/routes")
def routes(staff=Depends(require_staff)):
    with Session(engine) as db:
        return [serialize(r) for r in db.scalars(select(Route).where(Route.organization == staff.organization).order_by(Route.id.desc()))]

@app.post("/api/routes", status_code=201)
def create_route(data: NewRoute, staff=Depends(require_staff)):
    with Session(engine) as db:
        route = Route(organization=staff.organization, **data.model_dump())
        db.add(route)
        db.flush()
        record(db, route, "created")
        db.commit()
        db.refresh(route)
        return serialize(route)

@app.post("/api/routes/{route_id}/transition")
def transition(route_id: int, data: Transition, staff=Depends(require_staff)):
    with Session(engine) as db:
        query = select(Route).where(Route.id == route_id, Route.organization == staff.organization)
        if not DATABASE_URL.startswith("sqlite"):
            query = query.with_for_update()
        route = db.scalar(query)
        if route is None:
            raise HTTPException(404, "Route not found")
        if route.version != data.expected_version:
            raise HTTPException(409, "Stale route version; refresh before retrying")
        valid, target = TRANSITIONS[data.action]
        if route.status not in valid:
            raise HTTPException(409, f"{data.action} not permitted from {route.status}")
        if data.action == "approve_counter":
            if route.proposed_price is None:
                raise HTTPException(409, "No counteroffer recorded")
            route.price = route.proposed_price
            route.proposed_price = None
        if data.action == "reject_counter":
            route.proposed_price = None
        if data.action == "complete":
            route.done = route.tasks
        route.status = target
        route.version += 1
        record(db, route, data.action, f"amount={data.amount}" if data.amount is not None else "")
        db.commit()
        db.refresh(route)
        return serialize(route)

@app.get("/api/routes/{route_id}/events")
def events(route_id: int, staff=Depends(require_staff)):
    with Session(engine) as db:
        route = db.scalar(select(Route).where(Route.id == route_id, Route.organization == staff.organization))
        if route is None:
            raise HTTPException(404, "Route not found")
        return [dict(action=e.action, detail=e.detail, at=e.at.isoformat()) for e in db.scalars(select(Event).where(Event.route_id == route_id).order_by(Event.id))]

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return Path(__file__).with_name("index.html").read_text(encoding="utf-8")

# Independent contractor roster and low-cost approximate proximity matching.
from contractors import install as install_contractors
import_action, import_batch_type, roster_candidates = install_contractors(app, Base, engine, require_staff)
from integrations import install as install_integrations
install_integrations(app, Base, engine, require_staff, import_action, import_batch_type)

# Independent dispatch planning and durable physical task reservations.
from dispatch import install as install_dispatch
dispatch_details, reserved_task_refs = install_dispatch(app, Base, Route, engine, require_staff, record, serialize, DATABASE_URL)

# Tenant-specific high-volume geographic preflight. Preview does not quote,
# create routes, reserve tasks or notify contractors.
from bulk_dispatch import install as install_bulk_dispatch
install_bulk_dispatch(app, require_staff, roster_candidates, reserved_task_refs)

# Contractor responses remain external and never require app accounts.
from offers import install as install_offers
install_offers(app, Base, Route, Event, engine, require_staff, serialize, record, DATABASE_URL, dispatch_details)

# Provider-independent routing API; road distances never use straight-line estimates.
from routing import install as install_routing
install_routing(app, Base, engine, require_staff)
